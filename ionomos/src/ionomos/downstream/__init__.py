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
      figures/*.svg + README.txt    (analysis.export.figures, or `ionomos export`) the figures for slides in the
                                    lab's export style: volcano, PCA, heatmap, correlation, and the dose-
                                    response, time-course and liganded-site figures (slides.py, D62, D68)
      sample_qc.tsv                 the per-sample scorecard (insights.py)
      presence_absence.tsv          features measured in one group and never in the other
      gene_set_ranks.tsv            rank-based gene-set test on every protein (enrichment on)
      dose_response.tsv             a titration (4+ doses): a fitted curve per feature, pEC50, F, p, class
      time_course.tsv               a time course (3+ time points): change over time (F), trend, class, pattern
      cysteine_sites.tsv            isoDTB: per site and compound the competition ratio, liganded call, selectivity
      cysteine_proteins.tsv         isoDTB: proteins with a liganded cysteine, how many of their sites are
      <condition>_log2_H_L_vs_0_protein-corrected_differential.tsv
                                    isoDTB with analysis.protein_correction: each site's ratio minus its protein's
                                    from an unenriched proteome, MSstatsPTM's adjustment (proteincorr.py, D70)
      specific_targets.tsv          a competition experiment: per compound, enriched against the control and competed off
      psm_qc.tsv                    search quality per run: PSMs, mass error, missed cleavages, charge states
      sdrf.tsv                      SDRF-Proteomics sample metadata: a row per raw file (and label), for PRIDE
      analysis.json               what was done, with which settings (reproducibility), + "quality", "trust"
      compare.* / benchmark.*       written by `ionomos compare` / `ionomos benchmark`, never by analyze(); when
                                    present, their verdicts are shown under "How far to trust this"

Pipeline stages, each a module:
    method prep   isodtb.py / tmt.py           (ports of the lab R scripts)
    quantities    quant.py   -> QuantMatrix    (one shape for every method)
                  engines.py                   (other engines' outputs: DIA-NN, MaxQuant, Sage, Spectronaut, AlphaDIA,
                                                MSstats / MSstatsTMT format, Proteome Discoverer; provenance)
    SDRF design   sdrfdesign.py                (an SDRF in the folder sets conditions / replicates / plexes)
    TMT plexes    plex.py                      (IRS: several plexes on one scale, before the processing)
    statistics    analysis.py + stats.py       (comparisons, Welch / one-sample t, BH)
                  roles.py                     (control / compound / competition: the default comparisons, specific targets)
                  design.py + deqms.py         (blocks / covariates, the moderated F, DEqMS; D42, D43)
    QC, insights  qc.py + insights.py + enrich.py  (PCA, scorecard, batch, missingness, on/off, gene sets)
    dose-response doseresponse.py              (CurveCurator's curves, when the conditions are doses)
    time course   timecourse.py                (limma's F over time, trend, series vs control; patterns)
    cysteines     cys.py                       (site ratio data: liganded calls, selectivity, a site annotation)
                  proteincorr.py               (site ratios corrected for protein abundance, MSstatsPTM; D70)
    search QC     psmqc.py + qcmetrics.py      (per run, from psm.tsv: mass error, missed cleavages, charge states)
    metadata      sdrf.py                      (SDRF-Proteomics, from the manifest, workflow and FASTA)
    guards, trust guards.py + trust.py         (implausible input made safe and said; statistics that may not
                                                mean what they say; the "How far to trust this" list; D60)
    presentation  charts.py + report.py        (SVG + HTML)
    accuracy      compare.py + benchmark.py    (separate commands: against a reference result, against known truth)

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

from ionomos.downstream import analysis, anytable, charts, cys, engines, isodtb, plex, quant, report, sdrfdesign, tmt
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
    issues: list = field(default_factory=list)  # doctor.Issue: what needs a person (see doctor.py)

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
    found = engines.detect(workdir)  # DIA-NN standalone, MaxQuant, Sage, Spectronaut, AlphaDIA, MSstats format, PD
    if found:
        return found.method
    if anytable.find_table(workdir):
        return "table"
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
                    mod_mass: str = "561.3387", table: Path | None = None, sdrf_factor=None,
                    dest: Path | None = None) -> tuple[quant.QuantMatrix | None, list[Path], list[str]]:
    """Method prep + loading. Returns (matrix or None, files written, notes). table: a file to read with
    the any-format loader (anytable.py) instead of looking for FragPipe's tables. An SDRF the user put in the
    experiment folder (or next to the table) then sets the design (sdrfdesign.py, D47); sdrf_factor picks its
    factor value column(s). dest: the experiment folder (default: the folder holding fragpipe/)."""
    workdir = Path(workdir)
    m, files, notes = _load_quantities(method, workdir, results, record, mod_mass, table)
    if m is not None:
        if dest is None:
            dest = workdir.parent if workdir.name == "fragpipe" else workdir
        m, dnotes = sdrfdesign.load_into(m, Path(dest), workdir, Path(table) if table is not None else None, sdrf_factor)
        notes += dnotes
    return m, files, notes


def _load_quantities(method: str | None, workdir: Path, results: Path, record: dict | None,
                     mod_mass: str = "561.3387", table: Path | None = None
                     ) -> tuple[quant.QuantMatrix | None, list[Path], list[str]]:
    files: list[Path] = []
    notes: list[str] = []
    results.mkdir(parents=True, exist_ok=True)
    if method in engines.METHODS:  # results from another engine (engines.py)
        tmt_map = (((record or {}).get("plan") or {}).get("overrides") or {}).get("tmt")  # experiment.yaml tmt:
        m, enotes = engines.load(method, Path(table) if table is not None else workdir, _sample_map(record), tmt_map)
        return m, files, notes + enotes
    if table is not None or method == "table":
        path = Path(table) if table is not None else anytable.find_table(workdir)
        if path is None:
            return None, files, [f"no protein or result table found in {workdir.name}/"]
        return anytable.load(path), files, notes
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

def _guard(p, comps, settings) -> tuple[list[tuple[int, str]], list[str], list]:
    """Comparisons whose groups are too small to test: [(index, reason)], notes, [(t, c, [(group, n)])]."""
    bad, notes, small = [], [], []
    m = p.m
    for k, (t, c) in enumerate(comps):
        groups = [(t, len(m.samples_of(t)))]
        if c == "others":
            groups.append(("the other conditions", len(m.samples) - groups[0][1]))
        elif c is not None:
            groups.append((c, len(m.samples_of(c))))
        few = [(g, n) for g, n in groups if n < settings.min_valid]
        if few:
            bad.append((k, "small"))
            small.append((t, c, few))
    return bad, notes, small

def _few_text(groups) -> str:
    return ", ".join(f"{g} has {n} sample{'s' if n != 1 else ''}" for g, n in groups)

def _libraries(settings, notes, base: Path | None = None) -> dict:
    """base: the experiment folder; a relative enrichment_gmt found there is read from there (so a folder
    carrying its own .gmt can be moved or analysed from anywhere)."""
    from ionomos.downstream import enrich

    if not settings.enrichment:
        return {}
    gmt = settings.enrichment_gmt or None
    if gmt and base is not None and not Path(gmt).is_absolute() and (Path(base) / gmt).is_file():
        gmt = str(Path(base) / gmt)
    libs, lnotes = enrich.load_libraries(settings.enrichment_libraries, gmt)
    notes += lnotes
    return libs

def _enrichment(diffs, libs) -> list[dict]:
    from ionomos.downstream import enrich

    if not libs or not diffs:
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

def _gene_scores(d) -> dict[str, tuple[float, int]]:
    """gene -> (signed statistic, feature index): the moderated t, else sign(log2FC) * -log10 p; a gene measured
    as several proteins / sites keeps its strongest."""
    import math

    from ionomos.downstream import enrich

    out: dict[str, tuple[float, int]] = {}
    for r in d.rows:
        g = enrich.gene_symbol(r["label"]) if r["label"] else ""
        if not g:
            continue
        t = r.get("t")
        if t is None or not math.isfinite(t):
            if r["pvalue"] is None or r["log2fc"] is None:
                continue
            t = math.copysign(-math.log10(max(r["pvalue"], 1e-300)), r["log2fc"])
        if g not in out or abs(t) > abs(out[g][0]):
            out[g] = (t, r["index"])
    return out

def _rank_enrichment(diffs, p, libs, limit: int = 40) -> list[dict]:
    """Rank-based gene-set test per comparison and library, on every tested gene (no cut-off)."""
    from ionomos.downstream import enrich

    if not libs or not diffs:
        return []
    resid_rows = None
    if p is not None:
        m = p.m
        groups: dict[str, list[int]] = {}
        for j, x in enumerate(m.samples):
            groups.setdefault(m.condition[x], []).append(j)
        resid_rows = []
        for row in m.values:
            res = [0.0] * len(row)
            for idx in groups.values():
                obs = [row[j] for j in idx if row[j] is not None]
                mu = sum(obs) / len(obs) if obs else 0.0
                for j in idx:
                    res[j] = row[j] - mu if row[j] is not None else 0.0
            resid_rows.append(res)
    out = []
    for d in diffs:
        sc = _gene_scores(d)
        if len(sc) < 20:
            continue
        scores = {g: v for g, (v, _i) in sc.items()}
        resid = {g: resid_rows[i] for g, (_v, i) in sc.items()} if resid_rows else None
        for name, lib in libs.items():
            terms = enrich.rank_test(scores, lib, resid, limit=limit)
            out.append({"comparison": d.name, "library": name, "tested": len(scores),
                        "adjusted": resid is not None, "terms": terms})
    return out

def _insights(p, diffs, qcd) -> dict:
    """Deeper QC and discovery (insights.py) for the report, the doctor and analysis.json."""
    from ionomos.downstream import insights

    m = p.m
    out: dict = {}
    out["scorecard"] = insights.sample_scorecard(p.measured, m.samples, m.condition, p.normalized_from,
                                                 qcd.get("correlation"), m.kind)
    out["pcs"] = insights.pc_association(qcd.get("pca") or {}, m.samples, m.condition, m.replicate)
    out["missingness"] = insights.missingness(p.measured)
    prior = next((d.prior for d in diffs if d.prior and all(x == x for x in d.prior)), None)
    pairs = [(d.name, *d.groups) for d in diffs if len(d.groups) == 2 and d.control != "others"]
    pooled = bool(diffs) and diffs[0].test_used == "limma"
    out["power"] = insights.power(p.measured, m.samples, m.condition, prior, m.kind, pairs=pairs, pooled=pooled)
    out["phist"], out["onoff"], out["imputation_driven"] = {}, {}, {}
    for d in diffs:
        out["phist"][d.name] = insights.p_histogram([r["pvalue"] for r in d.rows])
        if d.confidence != "none":
            out["onoff"][d.name] = insights.presence_absence(p.measured, m.samples, m.condition, d.treatment, d.control)
        out["imputation_driven"][d.name] = insights.imputation_driven(p.imputed, m.samples, m.condition, d) \
            if p.n_imputed else []
    return out

def analyze(dest: Path, method: str | None = None, analysis_cfg: dict | None = None, overrides: dict | None = None,
            record: dict | None = None, context: dict | None = None, mod_mass: str = "561.3387",
            progress=None, table: Path | None = None) -> Outcome:
    """Run every downstream stage for one experiment folder. Never raises.

    Each stage is isolated: if QC, enrichment or an export fails, the volcano plots and the report are
    still made; if the statistics fail, a simpler test is tried; if the report fails, a plain fallback
    page lists the volcano plots. Everything that went wrong or needs a decision ends up in
    out.issues (doctor.py), which the worker and the app turn into pop-up windows.
    progress(text) is called between stages (the app shows it)."""
    from ionomos import __version__
    from ionomos.downstream import doctor, export, fpa, guards, insights, roles, trust

    dest = Path(dest)
    workdir = dest / "fragpipe" if (dest / "fragpipe").is_dir() else dest
    results = dest / RESULTS
    out = Outcome(method=method, results_dir=results)
    f = doctor.Findings(workdir=workdir)
    errors_file = results / "analysis_error.txt"

    def say(msg: str) -> None:
        log.info("%s: %s", dest.name, msg)
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    def stage(name: str, fn, *args, **kw):
        """Run one stage; a crash is recorded (with its traceback) and the pipeline continues."""
        try:
            return fn(*args, **kw)
        except Exception as exc:  # noqa: BLE001 - one stage must never take the others down
            tb = traceback.format_exc()
            log.error("%s: analysis stage %r failed: %s", dest.name, name, exc)
            f.stage_errors[name] = (f"{type(exc).__name__}: {exc}", tb)
            try:
                with open(errors_file, "a", encoding="utf-8") as fh:
                    fh.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} stage {name}\n{tb}\n")
            except OSError:
                pass
            return None

    try:
        results.mkdir(parents=True, exist_ok=True)
        errors_file.unlink(missing_ok=True)
    except OSError as exc:
        out.warnings.append(f"cannot write {results}: {exc}")
        out.issues = [doctor.Issue("NO_RESULTS_FOLDER", "error", "The results folder can't be written",
                                   f"{results}: {exc}", ["The disk is full", "The folder is read-only or open elsewhere"],
                                   ["Free disk space / close programs using the folder, then Re-run analysis"])]
        return out
    if table is not None:  # a file: another engine's own format when recognised, else any table
        found = engines.detect(Path(table))
        method = found.method if found else "table"
    out.method = method = method if method and method != "auto" else detect_method(workdir)
    f.method = method
    settings, snotes = analysis.settings_lenient(analysis_cfg, overrides)
    out.warnings += snotes
    f.settings = settings
    notes: list[str] = []
    diffs: list[analysis.DiffResult] = []
    processed = None
    qcd: dict = {}
    enrichment: list[dict] = []
    ranked: list[dict] = []
    insight: dict = {}
    enr_notes: list[str] = []
    comps: list = []
    dose_info: dict = {"ran": False, "reason": "no processed quantities to fit"}
    dose_view: dict | None = None
    time_info: dict = {"ran": False, "reason": "no processed quantities to test"}
    time_view: dict | None = None
    cys_info: dict = {"ran": False, "reason": "not site ratio data (isoDTB)"}
    cys_view: dict | None = None
    prot_info: dict = {"ran": False, "reason": "not asked for (analysis.protein_correction)"}
    protein_hl: dict | None = None
    psm_view: dict | None = None
    model = analysis.Model()
    ftest = None
    design_plan = None            # roles.Plan: the conditions' roles and the comparisons they give (D61)
    specific: list = []           # roles.Specific per compound with a competition
    spec_info: list[dict] = []

    def read():
        try:
            return load_quantities(method, workdir, results, record, mod_mass, table, settings.sdrf_factor, dest)
        except (isodtb.SiteError, anytable.TableError) as exc:
            f.read_problem = str(exc)
            notes.append(f"the result table has nothing usable: {exc}")
            return None

    say(f"reading {method or 'FragPipe'} results")
    loaded = stage("read", read)
    m = None
    if loaded is not None:
        m, files, lnotes = loaded
        out.files += files
        notes += lnotes
        if m is not None:
            notes += m.notes
            notes += stage("input-check", guards.check_input, m) or []  # implausible values, repeated names (D60)
    tmt_info = None
    if m is not None and m.features and not m.meta.get("precomputed"):  # TMT plexes on one scale (plex.py, D48)
        bridged = stage("plex", plex.normalise, m, settings)
        if bridged is not None:
            m, tmt_info, pnotes = bridged
            notes += pnotes
    if m is not None and m.meta.get("roles"):  # roles from an SDRF column; analysis.roles wins (roles.py)
        settings = stage("roles", _sdrf_roles, m, settings, notes) or settings
        f.settings = settings
    f.loaded = m
    f.tmt = tmt_info
    precomputed = (m.meta.get("precomputed") or []) if m is not None and m.features else []
    if precomputed:  # a results table: plot what it says, nothing to process or test
        say("plotting the results in " + m.meta.get("table", "the table"))
        for d in stage("statistics", analysis.precomputed_diffs, m, settings) or []:
            diffs.append(d)
            tsv = results / f"{d.slug()}_differential.tsv"
            if stage("tables", write_tsv, tsv, analysis.DIFF_COLUMNS, d.rows):
                out.files.append(tsv)
            f.volcanos[d.name] = _write_volcano(results, d, stage)
            if f.volcanos[d.name]:
                out.files.append(f.volcanos[d.name])
        if diffs:
            insight = stage("insights", lambda: {"phist": {d.name: insights.p_histogram([r["pvalue"] for r in d.rows])
                                                          for d in diffs}}) or {}
        if diffs and settings.enrichment:
            say("enrichment")
            libs = stage("enrichment", _libraries, settings, enr_notes, dest) or {}
            enrichment = stage("enrichment", _enrichment, diffs, libs) or []
            ranked = stage("enrichment", _rank_enrichment, diffs, None, libs) or []
            _write_enrichment(results, enrichment, ranked, stage, out)
    elif m is not None and m.features:
        files_mx = results / f"{m.level}_matrix_log2.tsv"
        if stage("tables", write_tsv, files_mx, ["id", "label", "description", *m.samples],
                 [[x.id, x.label, x.description, *vals] for x, vals in zip(m.features, m.values, strict=True)]):
            out.files.append(files_mx)
        say("filtering, normalising, imputing")
        res = stage("process", fpa.process, m, exclude=settings.exclude_samples, conditions=settings.sample_conditions,
                    contaminants=settings.remove_contaminants, global_pct=settings.filter_global_pct,
                    condition_pct=settings.filter_condition_pct, normalization=settings.normalize,
                    imputation=settings.imputation, shift=settings.impute_shift, scale=settings.impute_scale,
                    seed=settings.seed, ratio_centre=settings.ratio_centre)
        if res is None:  # fall back to the data as loaded, so there are still statistics and a volcano
            notes.append("processing failed; statistics use the values as loaded (no filtering or imputation)")
            res = stage("process-fallback", fpa.process, m, exclude=settings.exclude_samples,
                        conditions=settings.sample_conditions, imputation="none")
        if res is not None:
            processed, pnotes = res
            notes += pnotes
        f.processed = processed
    elif m is not None:
        notes.append("the result table had no rows")

    if processed is not None and processed.m.features:
        pm = processed.m
        try:
            comps, cnotes = analysis.choose_comparisons(pm, settings)
        except analysis.AnalysisError as exc:
            # explicit comparisons / control that don't fit: fall back to the defaults so a volcano is made
            f.comparison_error = str(exc)
            notes.append(f"{exc}; used the default comparisons instead")
            fallback = analysis.Settings(**{**settings.__dict__, "comparisons": [], "control": None})
            try:
                comps, cnotes = analysis.choose_comparisons(pm, fallback)
            except analysis.AnalysisError as exc2:
                comps, cnotes = [], [str(exc2)]
        notes += cnotes
        design_plan = stage("roles", roles.plan, pm, settings if not f.comparison_error else fallback)
        f.roles = design_plan
        by_roles = design_plan is not None and design_plan.active
        if (pm.kind == "intensity" and len(pm.conditions) >= 2 and not settings.comparisons and not settings.control
                and settings.de_type == "control" and not by_roles
                and analysis.find_control(pm.conditions, settings) is None):
            ctrls = {c for _, c in comps if c not in (None, "others")}
            f.control_guessed = next(iter(ctrls), None)
        f.comparisons = comps
        bad, gnotes, f.small_groups = _guard(processed, comps, settings)
        notes += gnotes
        if comps:
            say("statistics (" + ", ".join(analysis.comparison_name(t, c) for t, c in comps) + ")")
            low = {comps[k] for k, _ in bad}
            small_of = {(t, c): few for t, c, few in f.small_groups}
            model = stage("design", analysis.make_model, pm, settings, comps) or analysis.Model()
            notes += model.notes
            f.model = model
            results_ = stage("statistics", analysis.run_contrasts, processed, comps, settings, low, model)
            if results_ is None and model.design is not None:  # the design itself failed: the plain model
                model.problem = f.stage_errors.pop("statistics", ("the fit failed",))[0]
                model.design = None
                notes.append(f"the experimental design failed ({model.problem}); the comparisons use ~0 + condition")
                results_ = stage("statistics", analysis.run_contrasts, processed, comps, settings, low, model)
            if results_ is not None:
                ftest = stage("F-test", analysis.f_test, processed, comps, settings, model)
            if results_ is None and settings.test == "limma":
                notes.append("limma failed on this data; a Welch t-test was used instead")
                welch = analysis.Settings(**{**settings.__dict__, "test": "welch"})
                results_ = stage("statistics-fallback", analysis.run_contrasts, processed, comps, welch, low)
            for k, (_t, c) in enumerate(comps):
                r = results_[k] if results_ is not None else None
                d = stage("statistics", analysis.to_diff, processed, r, c, settings) if r is not None else None
                if d is None:
                    continue
                if by_roles:
                    d.role = design_plan.kinds.get(comps[k], "")
                if comps[k] in low:
                    few = _few_text(small_of[comps[k]])
                    if d.tested:
                        d.confidence = "low"
                        d.confidence_note = (f"Low confidence: {few}. The p-values borrow the replicate spread from the "
                                             "rest of the experiment, so treat hits as leads to confirm.")
                    elif any(x["log2fc"] is not None or x["mean_treatment"] is not None for x in d.rows):
                        analysis.fold_change_only(d, few[:1].upper() + few[1:] if few else "No replicates")
                    if d.confidence:
                        notes.append(f"{d.name}: {d.confidence_note}")
                if d.tested == 0 and not d.confidence and comps[k] not in low:
                    notes.append(f"{d.name}: zero features could be tested. No valid volcano can be drawn; "
                                 "check replicate grouping and missing quantities.")
                if d.relaxed and len(set(d.groups)) > 1:
                    small = d.treatment if d.groups[0] < d.groups[1] else d.control
                    notes.append(f"{d.name}: {d.relaxed:,} features were tested with fewer than {settings.min_valid} "
                                 f"measured values in {small} ({min(d.groups)} samples against {max(d.groups)}); "
                                 "their n is in the table. small_group_min_valid: same leaves them untested.")
                diffs.append(d)
                tsv = results / f"{d.slug()}_differential.tsv"
                if stage("tables", write_tsv, tsv, analysis.DIFF_COLUMNS, d.rows):
                    out.files.append(tsv)
                f.volcanos[d.name] = _write_volcano(results, d, stage)
                if f.volcanos[d.name]:
                    out.files.append(f.volcanos[d.name])
        if by_roles and diffs:
            got = stage("specific_targets", _specific_targets, design_plan, diffs, results, out)
            if got is not None:
                specific, spec_info = got
        if settings.protein_correction.get("proteome") and pm.kind != "ratio":
            prot_info = {"ran": False, "reason": "protein_correction applies to site ratio data (isoDTB) only"}
        elif settings.protein_correction.get("proteome") and diffs:
            say("site ratios corrected for protein abundance")
            got = stage("protein_correction", _protein_correction, processed, diffs, settings, results, out, dest)
            if got is None:
                prot_info = {"ran": False, "reason": "the protein-correction step failed (see analysis_error.txt)"}
            else:
                prot_info, corrected, f.protein_problems, pnotes, protein_hl = got
                notes += pnotes
                for d in corrected:
                    diffs.append(d)
                    f.volcanos[d.name] = _write_volcano(results, d, stage)
                    if f.volcanos[d.name]:
                        out.files.append(f.volcanos[d.name])
        if pm.features:
            pmx = stage("tables", export.processed_matrix, results / f"{m.level}_matrix_processed.tsv", processed)
            if pmx:
                out.files.append(pmx)
            if diffs:
                rt = stage("tables", export.results_table, results / f"{m.level}_results.tsv", processed, diffs,
                           ftest)
                if rt:
                    out.files.append(rt)
            say("quality control (PCA, correlation, missing values)")
            qcd = stage("qc", _qc, processed, diffs, settings) or {}
            say("sample scorecard, batch and missingness checks, on/off features, power")
            insight = stage("insights", _insights, processed, diffs, qcd) or {}
            for name, fn, arg in (("sample_qc.tsv", export.sample_qc_table, insight.get("scorecard")),
                                  ("presence_absence.tsv", export.presence_absence_table,
                                   (processed, insight["onoff"]) if any((insight.get("onoff") or {}).values()) else None)):
                if arg:
                    t = stage("tables", fn, results / name, arg)
                    if t:
                        out.files.append(t)
            dose = stage("dose_response", _dose_response, processed, settings, results, out, say)
            if dose is None:
                dose_info = {"ran": False, "reason": "the dose-response step failed (see analysis_error.txt)"}
            else:
                dose_info, dose_view, f.dose_problems, dnotes = dose
                notes += dnotes
            tc = stage("time_course", _time_course, processed, settings, results, out, model)
            if tc is None:
                time_info = {"ran": False, "reason": "the time-course step failed (see analysis_error.txt)"}
            else:
                time_info, time_view, f.time_problems, tnotes = tc
                notes += tnotes
            if cys.applies(pm):
                say("liganded cysteines")
                called = stage("cysteines", _cysteines, processed, settings, results, out, dest, protein_hl)
                if called is None:
                    cys_info = {"ran": False, "reason": "the liganded-site step failed (see analysis_error.txt)"}
                else:
                    cys_info, cys_view, f.cys_problems, cnotes2 = called
                    notes += cnotes2
        if diffs and settings.enrichment:
            say("enrichment (hits, and every protein ranked)")
            libs = stage("enrichment", _libraries, settings, enr_notes, dest) or {}
            enrichment = stage("enrichment", _enrichment, diffs, libs) or []
            ranked = stage("enrichment", _rank_enrichment, diffs, processed, libs) or []
            _write_enrichment(results, enrichment, ranked, stage, out)
        exported = stage("export", export.fragpipe_analyst, results / "fragpipe-analyst", m, processed, diffs,
                         settings, comps, model)
        out.files += exported or []
    searched = stage("psm_qc", _psm_qc, workdir, settings, results, out, say)
    if searched is None:
        psm_info = {"ran": False, "reason": "the search-quality step failed (see analysis_error.txt)"}
    else:
        psm_info, psm_view, f.psm_problems, qnotes = searched
        notes += qnotes
    say("sample metadata (SDRF)")
    sdrf_info = stage("sdrf", _sdrf, method, dest, workdir, record, m, processed, settings, __version__, results,
                      out) or {"file": None, "reason": "the SDRF step failed (see analysis_error.txt)"}
    f.diffs = diffs
    f.enrichment_notes = enr_notes
    f.insights = insight
    if processed is not None and diffs:  # statistics that ran but may not mean what they say (guards.py, D60)
        f.guards = stage("guards", guards.statistics, processed, diffs, settings, model) or []
        notes += stage("guards", guards.notes, processed, diffs, settings) or []
    out.issues = doctor.check(f)
    out.warnings += notes
    trusted = stage("trust", trust.build, m, processed, diffs, insight, qcd, settings, f.guards, results) or {}
    ctx = {"version": __version__, **(context or {})}
    ctx["engine"] = stage("provenance", engines.provenance, Path(table) if table is not None else workdir, method,
                          m.source if m is not None else "", m.meta if m is not None else {}) or {}
    if not ctx.get("method") or ctx["method"] == "?":
        ctx["method"] = method or "unknown method"
    ctx.setdefault("experiment", dest.name)
    ctx["enrichment_notes"] = enr_notes
    ctx["issues"] = [i.as_dict() for i in out.issues]
    ctx["sdrf"] = sdrf_info
    ctx["model"], ctx["ftest"] = model, ftest
    ctx["roles"], ctx["specific"] = design_plan, specific
    figure_files: list[Path] = []
    if settings.export.get("figures") and (processed is not None or diffs):
        say("figures for slides")
        figure_files = stage("figures", _static_figures, results, ctx, m, processed, diffs, settings, qcd, enrichment,
                             ranked, insight, dose_view, cys_view, time_view) or []
        out.files += figure_files
    ctx["trust"] = trusted
    say("writing the report")
    rel = [str(p.relative_to(results)).replace("\\", "/") if p.is_relative_to(results) else p.name
           for p in out.files]
    out.report = results / "report.html"
    html = stage("report", report.render, ctx, m, processed, diffs, out.warnings, rel, settings, qcd, enrichment,
                 ranked, insight, dose=dose_view, cys=cys_view, time=time_view, psm=psm_view)
    if html is None:  # the fallback page: issues, notes and the volcano plots themselves
        out.issues = doctor.check(f)
        ctx["issues"] = [i.as_dict() for i in out.issues]
        html = report.fallback(ctx, diffs, out.warnings, rel)
    try:
        out.report.write_text(html, encoding="utf-8")
    except OSError as exc:
        out.warnings.append(f"could not write the report: {exc}")
        out.report = None
    pm = processed.m if processed else m
    out.summary = {
        "report": f"{RESULTS}/report.html" if out.report else None,
        "method": method,
        "features": len(pm.features) if pm else 0,
        "features_loaded": len(m.features) if m else 0,
        "level": m.level if m else None,
        "samples": {x: pm.condition[x] for x in pm.samples} if pm else {},
        "comparisons": [{"name": d.name, "up": d.up, "down": d.down, "tested": d.tested,
                         "samples": dict(zip(("treatment", "control"), d.groups, strict=False)),
                         **({"kind": d.role} if d.role else {}),
                         **({"tested_below_min_valid": d.relaxed} if d.relaxed else {}),
                         "confidence": d.confidence or "normal", "confidence_note": d.confidence_note,
                         "table": f"{RESULTS}/{d.slug()}_differential.tsv",
                         "volcano": (f"{RESULTS}/{Path(f.volcanos[d.name]).name}" if f.volcanos.get(d.name) else None)}
                        for d in diffs],
        "processing": processed.steps if processed else [],
        "imputation": processed.imputation if processed else None,
        "normalisation": processed.normalization if processed else {},
        "model": model.as_dict(settings) if processed is not None and comps else {},
        "f_test": _f_summary(ftest, settings),
        "settings": analysis.as_dict(settings),
        "issues": [i.as_dict() for i in out.issues],
        "state": _state(out.issues),
        "enrichment": [{k: v for k, v in b.items() if k != "terms"} | {"top": [t["term"] for t in b["terms"][:5]
                        if t["q"] <= 0.05]} for b in enrichment],
        "enrichment_notes": enr_notes,
        "gene_set_ranks": [{"comparison": b["comparison"], "library": b["library"],
                            "top": [f"{t['term']} ({t['direction']})" for t in b["terms"][:5] if t["q"] <= 0.05]}
                           for b in ranked],
        "quality": _quality_summary(insight),
        "trust": trusted,
        "dose_response": dose_info,
        "time_course": time_info,
        "cysteines": cys_info,
        "protein_correction": prot_info,
        "roles": design_plan.as_dict() if design_plan is not None else {},
        "specific_targets": spec_info,
        "psm_qc": psm_info,
        "sdrf": sdrf_info,
        "figures": [f"{RESULTS}/{p.relative_to(results).as_posix()}" for p in figure_files],
        "design": _design_summary(m, settings),
        "tmt": tmt_info,
        "source": m.source if m else None,
        "engine": ctx.get("engine") or {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ionomos_version": __version__,
        "notes": out.warnings,
    }
    for name, (msg, _tb) in f.stage_errors.items():
        out.warnings.append(f"analysis step {name} failed: {msg}")
    try:
        (results / "analysis.json").write_text(json.dumps(out.summary, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        out.warnings.append(f"could not write analysis.json: {exc}")
    return out

def _dose_response(p, settings, results: Path, out: Outcome, say) -> tuple[dict, dict, list, list[str]]:
    """results/dose_response.tsv when the conditions are a titration (doseresponse.py). Returns (analysis.json
    summary, the report's payload, problems for the doctor, notes)."""
    from ionomos.downstream import doseresponse as dr

    if not settings.dose_response:
        off = "dose-response is switched off (analysis.dose_response)"
        return {"ran": False, "reason": off}, {"ran": False, "found": False, "reason": off}, [], []
    try:
        control = analysis.find_control(p.m.conditions, settings)
    except analysis.AnalysisError:
        control = None
    res, plan = dr.run(p, settings, control, progress=lambda i: say(f"dose-response curves ({i:,} features)"))
    table = None
    if res is not None:
        out.files.append(write_tsv(results / "dose_response.tsv", dr.COLUMNS, dr.table_rows(res)))
        table = f"{RESULTS}/dose_response.tsv"
    return (dr.summary(res, plan, table), dr.report_payload(res, plan), plan.problems,
            res.notes if res is not None else plan.notes)

def _time_course(p, settings, results: Path, out: Outcome, model) -> tuple[dict, dict, list, list[str]]:
    """results/time_course.tsv when the conditions are time points (timecourse.py). Returns (analysis.json
    summary, the report's payload, problems for the doctor, notes)."""
    from ionomos.downstream import timecourse as tc

    if not settings.time_course:
        off = "the time-course tests are switched off (analysis.time_course)"
        return {"ran": False, "reason": off}, {"ran": False, "found": False, "reason": off}, [], []
    try:
        control = analysis.find_control(p.m.conditions, settings)
    except analysis.AnalysisError:
        control = None
    res, plan = tc.run(p, settings, control, model)
    table = None
    if res is not None:
        out.files.append(write_tsv(results / "time_course.tsv", tc.COLUMNS, tc.table_rows(res)))
        table = f"{RESULTS}/time_course.tsv"
    problems = [("TIMES", sev, msg) for sev, msg in plan.problems] + \
        [("TIME_SPLINE", sev, msg) for sev, msg in plan.spline_problems]
    return (tc.summary(res, plan, table), tc.report_payload(res, plan), problems,
            res.notes if res is not None else plan.notes)

def _protein_correction(p, diffs, settings, results: Path, out: Outcome, dest: Path):
    """Site comparisons corrected for protein abundance (proteincorr.py, D70): one differential table each. Returns
    (analysis.json summary, the corrected DiffResults, problems for the doctor, notes, {condition: {site index:
    protein log2 H/L}} for the liganded table)."""
    from ionomos.downstream import proteincorr

    pc = settings.protein_correction
    path = proteincorr.find(pc["proteome"], dest)
    if path is None:
        msg = (f"protein_correction: the proteome {pc['proteome']!r} was not found (give the folder of an analysed "
               f"Ionomos experiment or a protein table, as a full path or inside {dest.name}/)")
        return proteincorr.off(msg), [], [("PROTEIN_CORRECTION", "input", msg)], [], None
    try:
        proteome = proteincorr.load(path, pc.get("match", "gene"))
    except proteincorr.ProteomeError as exc:
        msg = f"protein_correction: {exc}"
        return proteincorr.off(msg), [], [("PROTEIN_CORRECTION", "input", msg)], [], None
    res = proteincorr.run(p, diffs, settings, proteome)
    cols = analysis.DIFF_COLUMNS + proteincorr.CORRECTION_COLUMNS
    for d, c in zip(res.diffs, [x for x in res.summary["conditions"] if x.get("proteome_comparison")], strict=True):
        tsv = write_tsv(results / f"{d.slug()}_differential.tsv", cols, d.rows)
        out.files.append(tsv)
        c["table"] = f"{RESULTS}/{tsv.name}"
    return res.summary, res.diffs, res.problems, res.notes, res.protein_hl


def _cysteines(p, settings, results: Path, out: Outcome, dest: Path,
               protein_hl: dict | None = None) -> tuple[dict, dict, list, list[str]]:
    """results/cysteine_sites.tsv and cysteine_proteins.tsv for site ratio data (cys.py). Returns (analysis.json
    summary, the report's payload, problems for the doctor, notes). protein_hl: the proteome's ratio per site
    (proteincorr.py), shown beside the calls."""
    if not settings.liganded:
        off = "liganded-site calls are switched off (analysis.liganded)"
        return cys.summary(None, off), cys.report_payload(None, off), [], []
    problems: list[tuple[str, str]] = []
    annotation = None
    if settings.site_annotation:
        path = cys.find_annotation(settings.site_annotation, dest)
        if path is None:
            problems.append(("warning", f"site annotation {settings.site_annotation!r} was not found in "
                                        f"{dest.name}/ (give a file in the experiment folder, or a full path)"))
        else:
            try:
                annotation = cys.load_annotation(path)
            except cys.AnnotationError as exc:
                problems.append(("warning", str(exc)))
    res = cys.run(p, settings, annotation, protein_hl)
    out.files.append(write_tsv(results / "cysteine_sites.tsv", cys.columns(res), cys.table_rows(res)))
    prot = None
    if res.proteins:
        out.files.append(write_tsv(results / "cysteine_proteins.tsv", cys.protein_columns(res), cys.protein_rows(res)))
        prot = f"{RESULTS}/cysteine_proteins.tsv"
    return (cys.summary(res, table=f"{RESULTS}/cysteine_sites.tsv", proteins=prot), cys.report_payload(res),
            problems + res.problems, res.notes)

def _sdrf_roles(m, settings, notes: list[str]):
    """Roles an input SDRF gave its conditions (sdrfdesign.apply -> m.meta["roles"]) become analysis.roles for
    the conditions the settings don't name. Returns the new settings."""
    from dataclasses import replace

    from ionomos.downstream import roles

    given = {k.lower() for k in settings.roles}
    add = {}
    for cond, text in m.meta["roles"].items():
        if cond.lower() in given:
            continue
        try:
            add[cond] = roles.format_role(*roles.parse_role(text))
        except roles.RoleError as exc:
            notes.append(f"SDRF role of {cond} ignored: {exc}")
    if not add:
        return settings
    src = (m.meta.get("sdrf") or {}).get("file") or "the SDRF"
    m.meta["role_sources"] = {c: f"SDRF {src}" for c in add}
    notes.append(f"roles from the SDRF {src}: " + ", ".join(f"{c} = {r}" for c, r in add.items()))
    return replace(settings, roles={**add, **settings.roles})

def _specific_targets(plan, diffs, results: Path, out: Outcome) -> tuple[list, list[dict]]:
    """results/specific_targets.tsv for a competition experiment (roles.py): per compound, the features enriched
    against the control and competed off. Returns (roles.Specific list, the analysis.json summary)."""
    from ionomos.downstream import roles

    found = roles.specific_targets(plan, diffs)
    if not found:
        return [], []
    out.files.append(write_tsv(results / "specific_targets.tsv", roles.SPECIFIC_COLUMNS,
                               roles.specific_table_rows(found)))
    return found, roles.specific_summary(found, f"{RESULTS}/specific_targets.tsv")

def _psm_qc(workdir: Path, settings, results: Path, out: Outcome, say) -> tuple[dict, dict | None, list, list[str]]:
    """results/psm_qc.tsv when the search wrote psm.tsv files or a DIA-NN stats.tsv (psmqc.py). Returns
    (analysis.json summary, the report's payload or None, problems for the doctor, notes)."""
    from ionomos.downstream import psmqc

    if not settings.psm_qc:
        return psmqc.summary(None, reason="search quality is switched off (analysis.psm_qc)"), None, [], []
    say("search quality per run (PSMs, mass error, missed cleavages)")
    res = psmqc.run(workdir)
    table = None
    if res.found:
        out.files.append(write_tsv(results / "psm_qc.tsv", psmqc.columns(res), psmqc.table_rows(res)))
        table = f"{RESULTS}/psm_qc.tsv"
    return (psmqc.summary(res, table), psmqc.report_payload(res), res.problems,
            [f"search quality: {n}" for n in res.notes])

def _design_summary(m, settings) -> dict:
    """Where each sample's condition came from, highest first (D47): sample_conditions, an input SDRF, the
    engine's own columns, the ionomos.json manifest, the names."""
    if m is None:
        return {}
    src = m.meta.get("conditions_from") or ("ionomos.json manifest" if m.meta.get("manifest_run") else "sample names")
    out: dict = {"conditions_from": "the engine's table" if src == "engine" else src}
    if m.meta.get("sdrf"):
        out["sdrf"] = m.meta["sdrf"]
    over = [x for x in (getattr(settings, "sample_conditions", {}) or {}) if x in m.condition]
    if over:
        out["overridden_by_sample_conditions"] = over
    return out


def _sdrf(method, dest: Path, workdir: Path, record, m, processed, settings, version: str, results: Path,
          out: Outcome) -> dict:
    """results/sdrf.tsv (SDRF-Proteomics sample metadata) and its analysis.json entry."""
    from ionomos.downstream import sdrf

    sd, reason = sdrf.build(method, dest, workdir, record, m, processed, settings, version)
    if sd is None:
        return sdrf.summary(None, reason, "")
    out.files.append(sdrf.write(results / "sdrf.tsv", sd))
    return sdrf.summary(sd, "", f"{RESULTS}/sdrf.tsv")

def _write_enrichment(results: Path, enrichment, ranked, stage, out) -> None:
    from ionomos.downstream import export

    if enrichment:
        et = stage("enrichment", export.enrichment_table, results / "enrichment.tsv", enrichment)
        if et:
            out.files.append(et)
    if any(b["terms"] for b in ranked):
        rt = stage("enrichment", export.rank_enrichment_table, results / "gene_set_ranks.tsv", ranked)
        if rt:
            out.files.append(rt)

def _quality_summary(insight: dict) -> dict:
    """The deeper checks in a few fields for analysis.json (the app and the CLI read it)."""
    if not insight:
        return {}
    card = insight.get("scorecard") or []
    miss = insight.get("missingness") or {}
    return {
        "samples_flagged": {r["sample"]: r["flags"] for r in card if r["status"] != "ok"},
        "batch": (insight.get("pcs") or {}).get("batch"),
        "missingness": miss.get("verdict", ""),
        "pi0": {k: v.get("pi0") for k, v in (insight.get("phist") or {}).items()},
        "p_value_shape": {k: v.get("shape") for k, v in (insight.get("phist") or {}).items()},
        "only_in_one_condition": {k: len(v) for k, v in (insight.get("onoff") or {}).items()},
        "imputation_driven_hits": {k: len(v) for k, v in (insight.get("imputation_driven") or {}).items()},
        # the smallest |log2FC| a typical feature shows with 80 % power, with the samples each comparison has
        "detectable_log2fc": {c["name"]: {"samples": c["n"], **{f"p{a}": (r or {}).get("q50")
                                                                for a, r in c["mdfc"].items()}}
                              for c in (insight.get("power") or {}).get("comparisons") or []},
    }

def _f_summary(ftest, settings) -> dict | None:
    """analysis.json "f_test": the moderated F across the conditions (3+ conditions with limma)."""
    if ftest is None:
        return None
    qs = [q for q in ftest.q if q == q]
    return {"reference": ftest.reference, "conditions": ftest.conditions, "df1": ftest.df1, "tested": len(qs),
            "any_change": sum(1 for q in qs if q <= settings.alpha), "alpha_adjusted": settings.alpha,
            "table": "the F, F_p and F_p_adj columns of <level>_results.tsv"}

def _state(issues) -> str:
    sev = {i.severity for i in issues}
    return "failed" if "error" in sev else "needs_input" if "input" in sev else "ok"

def _static_figures(results: Path, ctx: dict, m, processed, diffs, settings, qcd, enrichment, ranked,
                    insight, dose=None, cys_=None, time=None) -> list[Path]:
    """results/figures/: the figures for slides the lab asks for after every analysis (analysis.export.figures),
    drawn from the report's own data in the lab's export style (slides.py, D62)."""
    from ionomos.downstream import slides

    d = report.payload({**ctx, "issues": []}, m, processed, diffs, [], [], settings, qcd, enrichment, ranked, insight,
                       dose, cys_, time)
    style = charts.style_from(settings.export, lenient=True)
    return slides.write(results / slides.FOLDER, d, style, style["figures"],
                        f"Ionomos {ctx.get('version', '')}".strip())

def _write_volcano(results: Path, d, stage) -> Path | None:
    """Every comparison gets a volcano file, checked after writing; an empty comparison gets an empty plot."""
    import time as _time

    svg = results / f"volcano_{d.slug()}.svg"
    text = stage("volcano", charts.volcano, d, standalone=True)
    if text is None:
        return None
    problem = "the file was empty"
    for attempt in range(3):  # a virus scanner or sync client can hold the file for a moment
        try:
            svg.write_text(text, encoding="utf-8")
            if svg.stat().st_size > 200:
                return svg
        except OSError as exc:
            problem = str(exc)
        _time.sleep(0.3 * (attempt + 1))
    stage("volcano", _raise, f"could not write {svg.name}: {problem}")
    return None

def _raise(msg: str):
    raise OSError(msg)

def _qc(p, diffs, settings) -> dict:
    from ionomos.downstream import qc

    pm = p.m
    before = p.before_filter or pm
    out = {
        "pca": qc.pca(pm.values, settings.pca_features),
        "pca_before": plex.pca_before(p, settings) if pm.meta.get("bridge_before") else None,
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
