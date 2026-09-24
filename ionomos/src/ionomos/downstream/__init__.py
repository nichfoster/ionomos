"""
Downstream analysis: FragPipe output -> tables, statistics, volcano plots, an HTML report.

    outcome = analyze(experiment_dir)                       # method + samples detected from the folder
    outcome = analyze(dest, method="DIA", analysis_cfg=cfg.analysis, overrides=exp_yaml_analysis, record=ionomos_json)

Layout it reads and writes (inside the experiment folder):

    fragpipe/                       FragPipe's output (read only)
    results/
      report.html                   the page to open (self-contained)
      <prefix>_sites.tsv            isoDTB: sites, identical to the lab's R script
      experimental_annotation.tsv   TMT: identical to the lab's R script
      <level>_matrix_log2.tsv       the quantities that went into the statistics
      <comparison>_differential.tsv every feature: log2FC, p, q, significance
      volcano_<comparison>.svg      standalone plot (opens in any browser, pastes into slides)
      analysis.json                 what was done, with which settings (reproducibility)

Pipeline stages, each a module:
    method prep   isodtb.py / tmt.py           (ports of the lab R scripts)
    quantities    quant.py   -> QuantMatrix    (one shape for every method)
    statistics    analysis.py + stats.py       (comparisons, Welch / one-sample t, BH)
    presentation  charts.py + report.py        (SVG + HTML)

Adding a method or an output means adding one loader or one renderer; the
stages don't know about each other's internals. Nothing here deletes or
rewrites FragPipe's files, and a failure returns warnings instead of raising.
"""
from __future__ import annotations

import json
import logging
import math
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ionomos.downstream import analysis, charts, isodtb, quant, report, tmt
from ionomos.downstream.tables import read_header, write_tsv

log = logging.getLogger("ionomos.downstream")

RESULTS = "results"


@dataclass
class Outcome:
    method: str | None
    results_dir: Path
    report: Path | None = None
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _find(root: Path, *patterns: str) -> Path | None:
    """First match, shallowest path first, for each pattern in order."""
    for pat in patterns:
        hits = sorted(root.rglob(pat), key=lambda p: (len(p.parts), str(p)))
        hits = [h for h in hits if RESULTS not in h.relative_to(root).parts[:1] and "_previous_" not in str(h)]
        if hits:
            return hits[0]
    return None


def detect_method(workdir: Path) -> str | None:
    if _find(workdir, isodtb.LABEL_FILE):
        return "isoDTB"
    if _find(workdir, "abundance_*_MD.tsv", "abundance_*.tsv"):
        return "TMT"
    if _find(workdir, "*pg_matrix.tsv"):
        return "DIA"
    if _find(workdir, "combined_protein.tsv"):
        return "LFQ"
    return None


def _sample_map(record: dict | None) -> dict[str, tuple[str, int]]:
    out = {}
    for line in ((record or {}).get("plan") or {}).get("manifest") or []:
        stem = quant.run_stem(line["file"])
        out[stem] = (str(line["experiment"]), int(line["bioreplicate"]))
    return out


def _merge_ratio(mats: list[quant.QuantMatrix]) -> quant.QuantMatrix:
    """Several isoDTB samples in one folder -> one site matrix (union of sites)."""
    if len(mats) == 1:
        return mats[0]
    ids, feats = {}, []
    for m in mats:
        for f in m.features:
            if f.id not in ids:
                ids[f.id] = len(feats)
                feats.append(f)
    samples = [s for m in mats for s in m.samples]
    values = [[None] * len(samples) for _ in feats]
    off = 0
    for m in mats:
        for f, row in zip(m.features, m.values, strict=True):
            for j, v in enumerate(row):
                values[ids[f.id]][off + j] = v
        off += len(m.samples)
    cond = {s: c for m in mats for s, c in m.condition.items()}
    return quant.QuantMatrix("ratio", "site", feats, samples, values, cond, mats[0].source)


def load_quantities(method: str | None, workdir: Path, results: Path, record: dict | None,
                    mod_mass: str = "561.3387") -> tuple[quant.QuantMatrix | None, list[Path], list[str]]:
    """Method prep + loading. Returns (matrix or None, files written, notes)."""
    files: list[Path] = []
    notes: list[str] = []
    results.mkdir(parents=True, exist_ok=True)
    if method == "isoDTB":
        lq = _find(workdir, isodtb.LABEL_FILE)
        if lq is None:
            return None, files, [f"no {isodtb.LABEL_FILE} in {workdir.name}/ (is label quantification on in the workflow?)"]
        site_files = isodtb.write_site_tables(lq, results, mod_mass)
        files += site_files
        return _merge_ratio([quant.from_isodtb_sites(p) for p in site_files]), files, notes
    if method == "TMT":
        ab = _find(workdir, "abundance_gene_MD.tsv", "abundance_protein_MD.tsv", "abundance_*_MD.tsv")
        if ab is None:
            return None, files, ["no tmt-report/abundance_*_MD.tsv found (did TMT-Integrator run?)"]
        ann_path = tmt.write_annotation(ab, results / "experimental_annotation.tsv")
        files.append(ann_path)
        ann = tmt.annotation_rows(read_header(ab))
        if not ann:
            notes.append("TMT sample names don't follow condition_1_channel (e.g. DMSO_1_126), so the lab's "
                         "annotation file is empty; conditions were taken from the text before the first '_'")
        return quant.from_tmt_abundance(ab, ann), files, notes
    if method == "DIA":
        pg = _find(workdir, "report.pg_matrix.tsv", "*pg_matrix.tsv")
        if pg is None:
            return None, files, ["no DIA-NN *pg_matrix.tsv found (did the DIA-NN step run?)"]
        return quant.from_pg_matrix(pg, _sample_map(record)), files, notes
    cp = _find(workdir, "combined_protein.tsv")
    if cp is None:
        return None, files, [f"don't know how to analyse method {method!r} (no known result table found)"]
    return quant.from_combined_protein(cp), files, notes


def _guard(p, comps, settings) -> tuple[list[tuple[int, str]], list[str]]:
    """Comparisons whose groups are too small to test: [(index, reason)], notes."""
    bad, notes = [], []
    m = p.m
    for k, (t, c) in enumerate(comps):
        groups = [(t, len(m.samples_of(t)))]
        if c == "others":
            groups.append(("the other conditions", len(m.samples) - groups[0][1]))
        elif c is not None:
            groups.append((c, len(m.samples_of(c))))
        small = [(g, n) for g, n in groups if n < settings.min_valid]
        if small:
            bad.append((k, "small"))
            notes.append(f"Cannot test {t} vs {c or '0'}: " + ", ".join(f"{g} has {n} sample(s)" for g, n in small) +
                         f"; at least {settings.min_valid} per group are required. Check missing runs and sample labels.")
    return bad, notes


def _blank(r) -> None:
    n = len(r.diff)
    r.t, r.p, r.q, r.ci_low, r.ci_high = ([math.nan] * n for _ in range(5))


def _enrichment(diffs, p, settings, notes) -> list[dict]:
    from ionomos.downstream import enrich

    if not settings.enrichment or not diffs:
        return []
    libs, lnotes = enrich.load_libraries(settings.enrichment_libraries, settings.enrichment_gmt or None)
    notes += lnotes
    if not libs:
        return []
    out = []
    for d in diffs:
        tested = [r for r in d.rows if r["pvalue"] is not None]
        bg = [enrich.gene_symbol(r["label"]) for r in tested if r["label"]]
        for direction in ("up", "down"):
            hits = [enrich.gene_symbol(r["label"]) for r in tested if r["significant"] == direction and r["label"]]
            for name, lib in libs.items():
                out.append({"comparison": d.name, "direction": direction, "library": name, "hits": len(set(hits)),
                            "background": len(set(bg)), "terms": enrich.ora(hits, bg, lib) if hits else []})
    return out


def analyze(dest: Path, method: str | None = None, analysis_cfg: dict | None = None, overrides: dict | None = None,
            record: dict | None = None, context: dict | None = None, mod_mass: str = "561.3387",
            progress=None) -> Outcome:
    """Run every downstream stage for one experiment folder. Never raises; problems become warnings.
    progress(text) is called between stages (the app shows it)."""
    from ionomos import __version__
    from ionomos.downstream import export, fpa

    def say(msg: str) -> None:
        log.info("%s: %s", dest.name, msg)
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    dest = Path(dest)
    workdir = dest / "fragpipe" if (dest / "fragpipe").is_dir() else dest
    results = dest / RESULTS
    out = Outcome(method=method, results_dir=results)
    try:
        results.mkdir(parents=True, exist_ok=True)
        out.method = method = method if method and method != "auto" else detect_method(workdir)
        try:
            settings = analysis.settings_from(analysis_cfg, overrides)
        except analysis.AnalysisError as exc:
            out.warnings.append(f"analysis settings ignored ({exc}); using defaults")
            settings = analysis.settings_from(analysis_cfg) if analysis_cfg else analysis.Settings()
        say(f"reading {method or 'FragPipe'} results")
        m, files, notes = load_quantities(method, workdir, results, record, mod_mass)
        out.files += files
        if m is not None:
            notes += m.notes
        diffs: list[analysis.DiffResult] = []
        processed = None
        qcd: dict = {}
        enrichment: list[dict] = []
        enr_notes: list[str] = []
        comps: list = []
        if m is not None and m.features:
            files_mx = results / f"{m.level}_matrix_log2.tsv"
            write_tsv(files_mx, ["id", "label", "description", *m.samples],
                      [[f.id, f.label, f.description, *vals] for f, vals in zip(m.features, m.values, strict=True)])
            out.files.append(files_mx)
            say("filtering, normalising, imputing")
            processed, pnotes = fpa.process(
                m, exclude=settings.exclude_samples, conditions=settings.sample_conditions,
                contaminants=settings.remove_contaminants, global_pct=settings.filter_global_pct,
                condition_pct=settings.filter_condition_pct, normalization=settings.normalize,
                imputation=settings.imputation, shift=settings.impute_shift, scale=settings.impute_scale,
                seed=settings.seed)
            notes += pnotes
            pm = processed.m
            try:
                comps, cnotes = analysis.choose_comparisons(pm, settings)
            except analysis.AnalysisError as exc:
                comps, cnotes = [], [str(exc)]
            notes += cnotes
            bad, gnotes = _guard(processed, comps, settings)
            notes += gnotes
            if comps and pm.features:
                say("statistics (" + ", ".join(analysis.comparison_name(t, c) for t, c in comps) + ")")
                results_ = analysis.run_contrasts(processed, comps, settings)
                for k, _ in bad:
                    _blank(results_[k])
                for (t, c), r in zip(comps, results_, strict=True):
                    d = analysis.to_diff(processed, r, c, settings)
                    if d.tested == 0 and not any(k == comps.index((t, c)) for k, _ in bad):
                        notes.append(f"{d.name}: zero features could be tested. No valid volcano can be drawn; "
                                     "check replicate grouping and missing quantities.")
                    elif d.tested == 0:
                        notes.append(f"{d.name}: zero features could be tested (see above).")
                    diffs.append(d)
                    out.files.append(write_tsv(results / f"{d.slug()}_differential.tsv", analysis.DIFF_COLUMNS, d.rows))
                    svg = results / f"volcano_{d.slug()}.svg"
                    svg.write_text(charts.volcano(d, standalone=True), encoding="utf-8")
                    out.files.append(svg)
            if pm.features:
                out.files.append(export.processed_matrix(results / f"{m.level}_matrix_processed.tsv", processed))
                if diffs:
                    out.files.append(export.results_table(results / f"{m.level}_results.tsv", processed, diffs))
                say("quality control (PCA, correlation, missing values)")
                qcd = _qc(processed, diffs, settings)
            if diffs and settings.enrichment:
                say("enrichment")
                enrichment = _enrichment(diffs, processed, settings, enr_notes)
                if enrichment:
                    out.files.append(export.enrichment_table(results / "enrichment.tsv", enrichment))
            try:
                out.files += export.fragpipe_analyst(results / "fragpipe-analyst", m, processed, diffs, settings, comps)
            except Exception as exc:  # noqa: BLE001 - an optional export must not stop the report
                notes.append(f"FragPipe-Analyst export skipped ({exc})")
        elif m is not None:
            notes.append("the result table had no rows")
        out.warnings += notes
        ctx = {"version": __version__, **(context or {})}
        if not ctx.get("method") or ctx["method"] == "?":
            ctx["method"] = method or "unknown method"
        ctx.setdefault("experiment", dest.name)
        ctx["enrichment_notes"] = enr_notes
        say("writing the report")
        rel = [str(p.relative_to(results)).replace("\\", "/") if p.is_relative_to(results) else p.name
               for p in out.files]
        html = report.render(ctx, m, processed, diffs, out.warnings, rel, settings, qcd, enrichment)
        out.report = results / "report.html"
        out.report.write_text(html, encoding="utf-8")
        pm = processed.m if processed else m
        out.summary = {
            "report": f"{RESULTS}/report.html",
            "method": method,
            "features": len(pm.features) if pm else 0,
            "features_loaded": len(m.features) if m else 0,
            "level": m.level if m else None,
            "samples": {x: pm.condition[x] for x in pm.samples} if pm else {},
            "comparisons": [{"name": d.name, "up": d.up, "down": d.down, "tested": d.tested,
                             "table": f"{RESULTS}/{d.slug()}_differential.tsv"} for d in diffs],
            "processing": processed.steps if processed else [],
            "imputation": processed.imputation if processed else None,
            "settings": analysis.as_dict(settings),
            "enrichment": [{k: v for k, v in b.items() if k != "terms"} | {"top": [t["term"] for t in b["terms"][:5]
                            if t["q"] <= 0.05]} for b in enrichment],
            "enrichment_notes": enr_notes,
            "source": m.source if m else None,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "ionomos_version": __version__,
            "notes": out.warnings,
        }
        (results / "analysis.json").write_text(json.dumps(out.summary, indent=2, default=str), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - analysis must never take the job (or the watcher) down
        log.exception("downstream analysis failed for %s", dest)
        out.warnings.append(f"analysis failed: {type(exc).__name__}: {exc}")
        try:
            (results / "analysis_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
    return out


def _qc(p, diffs, settings) -> dict:
    from ionomos.downstream import qc

    pm = p.m
    before = p.before_filter or pm
    out = {
        "pca": qc.pca(pm.values, settings.pca_features),
        "correlation": qc.correlation(pm.values),
        "cv": qc.cv_by_condition(pm.values, pm.samples, pm.condition),
        "features_per_sample": qc.feature_numbers(before.values),
        "features_per_sample_after": qc.feature_numbers(p.measured),
        "missing": qc.missing_pattern(p.measured, pm.samples, pm.condition),
        "box_before": qc.box_stats(p.normalized_from or p.measured),
        "box_after": qc.box_stats(p.measured),
    }
    # heatmap of hits: centred values of features significant in any comparison, clustered
    sig = {}
    for d in diffs:
        for r in d.rows:
            if r["significant"]:
                q = r["qvalue"] if r["qvalue"] is not None else 1.0
                sig[r["index"]] = min(sig.get(r["index"], 1.0), q)
    rows = sorted(sig, key=lambda i: sig[i])[: settings.heatmap_max]
    if len(rows) >= 2 and len(pm.samples) >= 2:
        centred = []
        for i in rows:
            vals = pm.values[i]
            obs = [v for v in vals if v is not None]
            mu = sum(obs) / len(obs) if obs else 0.0
            centred.append([None if v is None else v - mu for v in vals])
        ro = qc.cluster_order(centred)
        cols = [[r[j] for r in centred] for j in range(len(pm.samples))]
        co = qc.cluster_order(cols)
        out["heatmap"] = {"rows": [rows[k] for k in ro], "cols": co,
                          "values": [[centred[k][j] for j in co] for k in ro], "total_significant": len(sig)}
    return out
