"""
Downstream analysis: FragPipe output -> tables, statistics, volcano plots, an HTML report.

    outcome = analyze(experiment_dir)                       # method + samples detected from the folder
    outcome = analyze(dest, method="DIA", analysis_cfg=cfg.analysis, overrides=exp_yaml_analysis, record=labwatch_json)

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
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from labwatch.downstream import analysis, charts, isodtb, quant, report, tmt
from labwatch.downstream.tables import read_header, write_tsv

log = logging.getLogger("labwatch.downstream")

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
        stem = Path(line["file"]).name
        stem = stem[:-4] if stem.lower().endswith(".raw") else stem
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


def analyze(dest: Path, method: str | None = None, analysis_cfg: dict | None = None, overrides: dict | None = None,
            record: dict | None = None, context: dict | None = None, mod_mass: str = "561.3387") -> Outcome:
    """Run every downstream stage for one experiment folder. Never raises; problems become warnings."""
    from labwatch import __version__

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
        m, files, notes = load_quantities(method, workdir, results, record, mod_mass)
        out.files += files
        diffs: list[analysis.DiffResult] = []
        if m is not None and m.features:
            files_mx = results / f"{m.level}_matrix_log2.tsv"
            write_tsv(files_mx, ["id", "label", "description", *m.samples],
                      [[f.id, f.label, f.description, *vals] for f, vals in zip(m.features, m.values, strict=True)])
            out.files.append(files_mx)
            try:
                comps, cnotes = analysis.choose_comparisons(m, settings)
            except analysis.AnalysisError as exc:
                comps, cnotes = [], [str(exc)]
            notes += cnotes
            for t, c in comps:
                d = analysis.differential(m, t, c, settings)
                diffs.append(d)
                out.files.append(write_tsv(results / f"{d.slug()}_differential.tsv", analysis.DIFF_COLUMNS, d.rows))
                svg = results / f"volcano_{d.slug()}.svg"
                svg.write_text(charts.volcano(d, standalone=True), encoding="utf-8")
                out.files.append(svg)
        elif m is not None:
            notes.append("the result table had no rows")
        out.warnings += notes
        ctx = {"version": __version__, **(context or {})}
        if not ctx.get("method") or ctx["method"] == "?":
            ctx["method"] = method or "unknown method"
        ctx.setdefault("experiment", dest.name)
        html = report.render(ctx, m, diffs, out.warnings, [p.name for p in out.files])
        out.report = results / "report.html"
        out.report.write_text(html, encoding="utf-8")
        out.summary = {
            "report": f"{RESULTS}/report.html",
            "method": method,
            "features": len(m.features) if m else 0,
            "level": m.level if m else None,
            "samples": {s: m.condition[s] for s in m.samples} if m else {},
            "comparisons": [{"name": d.name, "up": d.up, "down": d.down, "tested": d.tested,
                             "table": f"{RESULTS}/{d.slug()}_differential.tsv"} for d in diffs],
            "settings": {k: (list(v) if isinstance(v, tuple) else v) for k, v in settings.__dict__.items()},
            "source": m.source if m else None,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "labwatch_version": __version__,
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
