"""
The analysis doctor: every analysis ends with a check-up that turns problems into
plain-English issues a person can act on.

    issues = check(Findings(...))       # after analyze() has done what it could
    suggest_conditions(["CS_x_DMSO_1", "CS_x_MA25_2"]) -> {"CS_x_DMSO_1": "DMSO", ...}

Severity decides what happens next (postprocess.record_issues):
    input    the analysis needs a decision (which samples belong together, which is
             the control, ...). It usually still ran on a guess; a window asks.
    error    something is missing or broken (no result table, a stage crashed,
             no volcano). A window explains the likely causes.
    warning  worth knowing, shown in the report and the attention list, no pop-up.

Each issue carries likely causes (most likely first) and fixes, written for the
person at the PC, not for a programmer.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ionomos.downstream import stats

POPUP = ("input", "error")


@dataclass
class Issue:
    code: str
    severity: str
    title: str
    message: str
    causes: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Findings:
    """What analyze() saw, for the doctor. Everything optional: a crash early leaves most of it empty."""
    method: str | None = None
    workdir: Path | None = None
    loaded: object = None               # QuantMatrix as read
    processed: object = None            # fpa.Processed
    settings: object = None             # analysis.Settings
    comparisons: list = field(default_factory=list)
    comparison_error: str | None = None  # explicit comparisons that couldn't be used
    control_guessed: str | None = None
    small_groups: list = field(default_factory=list)      # [(treatment, control, [(group, n)])]
    diffs: list = field(default_factory=list)
    volcanos: dict = field(default_factory=dict)          # comparison name -> Path or None
    stage_errors: dict = field(default_factory=dict)      # stage -> (message, traceback)
    load_notes: list = field(default_factory=list)
    read_problem: str | None = None      # the result table exists but holds nothing usable (e.g. isoDTB SiteError)
    enrichment_notes: list = field(default_factory=list)


# the tables each method needs, and why they might be missing
EXPECTED = {
    "DIA": ("a DIA-NN protein matrix (…pg_matrix.tsv)",
            ["The workflow's DIA quantification step (DIA-NN) is switched off or failed",
             "FragPipe stopped before quantification — the FragPipe log shows the last step it reached",
             "This isn't DIA data (pick the right method and re-run analysis)"]),
    "isoDTB": ("combined_modified_peptide_label_quant.tsv",
               ["Label quantification (IonQuant with the isoDTB labels) is off in the workflow",
                "FragPipe stopped before quantification — check the FragPipe log",
                "This isn't an isoDTB experiment (pick the right method)"]),
    "TMT": ("tmt-report/abundance_*_MD.tsv",
            ["TMT-Integrator is switched off or failed in the workflow",
             "The annotation (channel → sample) file was missing, so TMT-Integrator made no report",
             "This isn't TMT data (pick the right method)"]),
    "LFQ": ("combined_protein.tsv",
            ["IonQuant / label-free quantification is off in the workflow",
             "FragPipe stopped before quantification — check the FragPipe log"]),
}


def _tables_present(workdir: Path | None, limit: int = 12) -> list[str]:
    if workdir is None or not Path(workdir).is_dir():
        return []
    try:
        found = sorted(p.relative_to(workdir).as_posix() for p in Path(workdir).rglob("*.tsv"))
    except OSError:
        return []
    return found[:limit]


def suggest_conditions(samples: list[str]) -> dict[str, str]:
    """Best guess at each sample's condition from its name: drop the part every name shares,
    trailing replicate numbers and Xcalibur timestamps. 'CS_22rv1_MA25_DMSO_1' -> 'DMSO'."""
    from ionomos.naming import strip_acq_stamp

    cleaned = {s: re.sub(r"(?:[_\-. ](?:rep|r)?\d{1,3})+$", "", strip_acq_stamp(re.sub(r"\.\d+$", "", s)),
                         flags=re.IGNORECASE) for s in samples}
    toks = {s: [t for t in re.split(r"[_\-. ]+", c) if t] for s, c in cleaned.items()}
    if len(samples) > 1:
        lists = list(toks.values())
        n = 0
        while all(len(t) > n + 1 for t in lists) and len({t[n].lower() for t in lists}) == 1:
            n += 1
        toks = {s: t[n:] for s, t in toks.items()}
    return {s: ("_".join(t) or cleaned[s] or s) for s, t in toks.items()}


def check(f: Findings) -> list[Issue]:
    out: list[Issue] = []
    add = out.append
    m = f.loaded
    p = f.processed
    s = f.settings
    method = f.method or "unknown"

    # ---- stages that crashed
    for stage, (msg, _tb) in f.stage_errors.items():
        fatal = stage in ("read", "statistics", "report", "volcano")
        add(Issue(f"CRASH_{stage.upper()}", "error" if fatal else "warning",
                  f"The {stage} step failed", f"{msg}",
                  ["An unexpected table layout or value in this experiment's FragPipe output",
                   "A bug in Ionomos (the details are saved in results/analysis_error.txt)"],
                  ["Re-run analysis once; if it fails again, use Report a problem so it can be fixed",
                   "The rest of the analysis still ran; the report says what's missing"],
                  {"stage": stage}))

    # ---- nothing to analyse
    if m is None and f.read_problem:
        if method == "table":
            causes = ["The file isn't one row per protein/peptide with a numeric column per sample",
                      "It has results but no recognisable fold-change and p-value columns",
                      "It is a long-format table (one row per PSM / precursor) or a log, not a matrix"]
            fixes = ["Export one row per protein with one numeric column per sample (names like DMSO_1, Drug_2), "
                     "or columns named log2FC and p-value, then Run analysis again"]
        else:
            causes = ["The probe/label mass in the workflow differs from the one Ionomos looks for (isoDTB: 561.3387)",
                      "Label quantification ran without the isoDTB labels, or found no labelled peptides",
                      "The search failed quietly (wrong FASTA or species) and the table is empty"]
            fixes = ["Open the table in fragpipe/ and check the modification masses and ratio columns",
                     "Check the workflow for this method (tab 3), then Retry the job"]
        add(Issue("UNUSABLE_TABLE", "error", "The result table has nothing Ionomos can use",
                  f"{f.read_problem}, so there are no statistics or volcano plot.", causes, fixes, {"method": method}))
        return out
    if m is None:
        if "read" not in f.stage_errors:
            want, causes = EXPECTED.get(method, ("a FragPipe result table",
                                                 ["The method isn't known, so Ionomos doesn't know which table to read",
                                                  "FragPipe's quantification didn't run"]))
            present = _tables_present(f.workdir)
            add(Issue("NO_TABLE", "error", "No result table to analyse",
                      f"Ionomos looked for {want} in {f.workdir or 'the experiment folder'} and didn't find it, "
                      "so there are no statistics or volcano plot yet.",
                      causes, ["Open the FragPipe log from the Jobs tab and look at the last steps",
                               "Check the workflow for this method (tab 3) has quantification on, then Retry the job",
                               "If the method is wrong, pick the right one and Re-run analysis"],
                      {"method": method, "tables_found": present}))
        return out
    if not m.features:
        add(Issue("EMPTY_TABLE", "error", "The result table is empty",
                  f"{Path(m.source).name} has no rows, so nothing was quantified.",
                  ["The search found no proteins/peptides passing FDR (wrong FASTA or wrong species?)",
                   "The raw files are empty or from a failed acquisition",
                   "Wrong search settings (enzyme, modifications, mass tolerances) for this experiment"],
                  ["Check the FASTA and workflow for this method (tab 3) and the raw files, then Retry"],
                  {"source": m.source}))
        return out
    if m.meta.get("precomputed"):  # a results table: no samples to check, only what it plots
        _result_checks(f, s, add)
        return out
    if not any(v is not None for row in m.values for v in row):
        add(Issue("NO_QUANTITIES", "error", "The result table has no usable numbers",
                  f"{Path(m.source).name} lists {len(m.features):,} features but not one measured value in the "
                  f"sample columns ({', '.join(m.samples[:6]) or 'none found'}), so nothing can be analysed.",
                  ["Quantification failed or was switched off (every value blank or zero)",
                   "The sample columns hold text instead of numbers (edited or exported by another program)"],
                  ["Open the table in fragpipe/ and check the sample columns, then Retry the job"],
                  {"source": m.source, "samples": list(m.samples)}))
        return out

    pm = p.m if p is not None else m
    conds = pm.conditions
    samples = list(pm.samples)
    if not pm.features or not samples:
        what = "sample" if not samples else "feature"
        add(Issue("NOTHING_LEFT", "error", f"Every {what} was removed before the statistics",
                  f"The table had {len(m.features):,} features in {len(m.samples)} samples, but after leaving out "
                  f"samples and filtering missing values none were left.",
                  ["Every sample is in 'Samples left out'", "The missing-value filter is stricter than the data allows"],
                  ["Check 'Samples left out' and the filter settings here, then Run analysis"],
                  {"excluded": list(getattr(s, "exclude_samples", []) or [])}))
        return out
    if len(samples) == 1 and pm.kind == "intensity":  # a single ratio sample still has a fold-change plot
        add(Issue("ONE_SAMPLE", "error", "Only one sample was quantified",
                  f"The table has a single sample ({samples[0]}), so there is nothing to compare and no volcano plot.",
                  ["The other runs failed or are missing from the result table",
                   "The experiment really is a single run"],
                  ["Check the FragPipe log for the other runs; re-acquire or re-search them"],
                  {"sample": samples[0]}))

    # ---- runs that didn't make it / didn't match
    missing = list(m.meta.get("missing_runs") or [])
    if missing:
        add(Issue("MISSING_RUNS", "error", f"{len(missing)} run(s) have no quantities",
                  "These runs were searched but are missing from the result table: " + ", ".join(missing[:10]) +
                  ("…" if len(missing) > 10 else "") + ". They are not in the statistics.",
                  ["The run failed in DIA-NN / quantification (corrupt or very short acquisition)",
                   "The file identified too few peptides and was dropped",
                   "The run was renamed after the search"],
                  ["Look for the run in the FragPipe log; re-acquire or leave it out",
                   "If the rest of the samples are fine, the analysis below is still valid without it"],
                  {"runs": missing}))
    unmatched = list(m.meta.get("unmatched_runs") or [])
    if unmatched:
        add(Issue("UNMATCHED_RUNS", "input", "Some runs couldn't be matched to their samples",
                  f"{len(unmatched)} run(s) weren't in the experiment's file list, so their condition was guessed "
                  "from the name: " + ", ".join(unmatched[:8]) + ("…" if len(unmatched) > 8 else "") + ".",
                  ["Files were renamed after they were filed", "FragPipe converted the files and changed their names"],
                  ["Check each sample's condition in this window and Run analysis"],
                  {"runs": unmatched}))

    # ---- duplicates (two runs of one sample)
    dups = sorted(x for x in samples if re.search(r"\.\d+$", x))
    if dups:
        add(Issue("DUPLICATE_SAMPLES", "input", "Two runs have the same sample name",
                  "These look like repeat runs of a sample (re-acquisitions): " + ", ".join(dups) +
                  ". Both are used as separate replicates, which inflates the replicate count.",
                  ["A sample was re-acquired and both files were dropped", "Two files were given the same replicate number"],
                  ["Leave out the run you don't want, or give it the right condition, then Run analysis"],
                  {"samples": dups}))

    if pm.kind == "intensity" and len(samples) >= 2:
        # ---- comparisons
        if len(conds) == 1:
            from ionomos.downstream.quant import run_stem

            runs = {x: run_stem(pm.columns[x]) if x in pm.columns else x for x in samples}
            by_run = suggest_conditions(list(runs.values()))
            suggested = {x: by_run[runs[x]] for x in samples}
            if len(set(suggested.values())) < 2:
                suggested = suggest_conditions(samples)
            add(Issue("ONE_CONDITION", "input", "Every sample is in the same condition",
                      f"All {len(samples)} samples were read as '{conds[0]}', so there is nothing to compare and no "
                      "volcano plot can be made.",
                      ["The file names don't carry the condition in the part Ionomos reads (e.g. DMSO_1, Drug_2)",
                       "The conditions were typed the same in the naming window"],
                      ["Give each sample its condition here ('Guess from names' fills in a suggestion) and Run analysis"],
                      {"samples": samples, "runs": runs, "suggested": suggested}))
        if len(conds) == len(samples) >= 4 and not s.sample_conditions:
            lettered = {x: re.sub(r"[_\-. ][A-Za-z]$", "", x) for x in samples}  # DMSO_a, DMSO_b -> DMSO
            by = suggest_conditions(list(lettered.values()))
            suggested = {x: by[lettered[x]] for x in samples}
            if 2 <= len(set(suggested.values())) < len(samples):
                add(Issue("EACH_OWN_CONDITION", "input", "Every sample is its own condition",
                          f"The {len(samples)} sample names carry no replicate numbers Ionomos recognises, so each "
                          "became its own condition and every comparison is fold change only. They look like "
                          f"{len(set(suggested.values()))} conditions with replicates.",
                          ["Replicates are marked with letters or words instead of _1, _2, _3"],
                          ["Check the suggested conditions here and Run analysis — replicates give real statistics"],
                          {"samples": samples, "suggested": suggested}))
        if f.comparison_error:
            add(Issue("BAD_COMPARISON", "input", "The chosen comparisons don't fit this experiment",
                      f"{f.comparison_error}. Ionomos compared each condition with the control instead, so the "
                      "volcano plots below are the default ones.",
                      ["A condition was renamed after the comparisons were chosen", "A typo in the comparison list"],
                      ["Pick the comparisons again here and Run analysis"], {"conditions": conds}))
        if f.control_guessed:
            add(Issue("NO_CONTROL", "input", "Which condition is the control?",
                      f"None of the conditions ({', '.join(conds)}) looks like a control (DMSO, vehicle, WT, …), so "
                      f"'{f.control_guessed}' was used. Every volcano plot is 'condition vs control', so please confirm.",
                      ["The control has a name Ionomos doesn't know (add it to Control keywords, Analysis tab)"],
                      ["Pick the control here and Run analysis (the choice is remembered for this experiment)"],
                      {"conditions": conds, "guessed": f.control_guessed}))
        # ---- sample quality
        counts = [sum(1 for r in (p.measured if p else pm.values) if r[j] is not None) for j in range(len(samples))]
        if len(counts) >= 3:
            med = stats.median(counts)
            low = [(samples[j], counts[j]) for j in range(len(samples)) if med and counts[j] < 0.4 * med]
            if low:
                add(Issue("LOW_SAMPLE", "input", "A sample has far fewer identifications",
                          ", ".join(f"{x}: {n:,}" for x, n in low) + f" (the other samples: ~{int(med):,}). "
                          "A failed injection like this distorts the statistics.",
                          ["The injection or acquisition failed (air bubble, clogged column, low load)",
                           "The sample was lost or degraded in prep"],
                          ["Leave the sample out here and Run analysis (you can always add it back)"],
                          {"samples": [x for x, _ in low], "counts": dict(low), "median": med}))
        if p is not None and p.n_imputed:
            total = len(p.m.values) * len(p.m.samples) or 1
            pct = 100 * p.n_imputed / total
            if pct > 40:
                add(Issue("HIGH_IMPUTATION", "warning", f"{pct:.0f}% of the values were imputed",
                          "More than 40 % of the numbers in the statistics are imputed, not measured; fold changes "
                          "for low-abundance proteins are unreliable.",
                          ["Very different samples (e.g. pulldown vs input)", "Low identifications in some runs"],
                          ["Consider imputation 'none' for this experiment, or a stricter missing-value filter"],
                          {"percent": round(pct, 1)}))

    labelled = {(d.treatment, d.control) for d in f.diffs if getattr(d, "confidence", "")}
    for t, c, groups in f.small_groups:
        if (t, c) in labelled:
            continue  # tested anyway and labelled low confidence / fold change only (see below)
        add(Issue("SMALL_GROUP", "input", f"Not enough replicates to test {t} vs {c or 0}",
                  "; ".join(f"{g} has {n} sample(s)" for g, n in groups) +
                  f" — at least {s.min_valid if s else 2} per group are needed, so this comparison has no p-values.",
                  ["A replicate's condition was mistyped, so it formed its own group",
                   "Runs are missing (see above) or were left out",
                   "The experiment really has a single replicate — then no statistics are possible"],
                  ["Fix the samples' conditions here and Run analysis"],
                  {"treatment": t, "control": c, "groups": groups}))

    if len(pm.features) < 100 and pm.kind == "intensity":
        add(Issue("FEW_FEATURES", "warning", f"Only {len(pm.features)} features in the analysis",
                  "Very few proteins passed the filters; statistics and enrichment will be weak.",
                  ["Low sample amount or a short gradient", "A strict missing-value filter"],
                  ["Check the identifications per sample in the report's QC section"], {"n": len(pm.features)}))

    # ---- statistics and plots
    _result_checks(f, s, add)
    return out


def _result_checks(f: Findings, s, add) -> None:
    """Per-comparison checks: confidence labels, nothing tested, no hits, missing plots, enrichment."""
    small = {(t, c) for t, c, _ in f.small_groups}
    for d in f.diffs:
        conf = getattr(d, "confidence", "")
        if conf == "low":
            add(Issue("LOW_CONFIDENCE", "warning", f"{d.name}: low confidence (a group has one sample)",
                      d.confidence_note,
                      ["The experiment has a single replicate of a condition",
                       "A replicate's condition was mistyped, so it formed its own group", "Runs are missing or left out"],
                      ["If replicates exist, fix the samples' conditions on the Analysis tab and Run analysis",
                       "Otherwise confirm hits by another experiment before relying on them"],
                      {"comparison": d.name}))
        elif conf == "none":
            add(Issue("FOLD_CHANGE_ONLY", "warning", f"{d.name}: fold change only — no statistics possible",
                      d.confidence_note,
                      ["Every group in this comparison has a single sample, with no replicates anywhere to borrow from",
                       "The results table has no usable p-value column"],
                      ["Add replicates for statistics; meanwhile the plot ranks features by fold change"],
                      {"comparison": d.name}))
        if d.tested == 0 and not conf and (d.treatment, d.control) not in small:
            add(Issue("ZERO_TESTED", "error", f"{d.name}: nothing could be tested",
                      "Not a single feature had enough values in both groups, so the volcano plot is empty.",
                      ["Most values are missing in one of the groups (and imputation is off)",
                       "The missing-value filter removed everything", "Samples were assigned to the wrong conditions"],
                      ["Check the conditions here; try imputation 'auto' for this experiment; Run analysis"],
                      {"comparison": d.name}))
        elif d.tested and d.up + d.down == 0 and conf != "none":
            add(Issue("NO_HITS", "warning", f"{d.name}: no significant changes",
                      f"{d.tested:,} features were tested and none passed {s.describe() if s else 'the cut-offs'}. "
                      "The volcano plot is still made.",
                      ["No real difference at this depth / replicate number", "One noisy replicate (check the PCA)"],
                      ["Explore looser cut-offs in the report (they update live)"], {"comparison": d.name}))
        v = f.volcanos.get(d.name)
        if v is None or not Path(v).is_file() or Path(v).stat().st_size < 200:
            add(Issue("NO_VOLCANO", "error", f"{d.name}: the volcano plot wasn't written",
                      "The statistics exist but the plot file is missing.",
                      ["The results folder is read-only or the disk is full", "A bug in the plotting step"],
                      ["Check free disk space and that results/ can be written, then Re-run analysis"],
                      {"comparison": d.name}))
    if f.enrichment_notes:
        add(Issue("ENRICHMENT", "warning", "Enrichment was incomplete", "; ".join(f.enrichment_notes),
                  ["No internet the first time a gene-set library is needed"],
                  ["Re-run analysis when the PC is online; after that it works offline"], {}))


def popups(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity in POPUP]
