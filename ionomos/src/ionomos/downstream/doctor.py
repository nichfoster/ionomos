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
    insights: dict = field(default_factory=dict)          # insights.py: scorecard, pcs, missingness, phist, ...
    tmt: dict | None = None                               # plex.py: what IRS did (or why not) across TMT plexes
    dose_problems: list = field(default_factory=list)     # [(severity, message)] from doseresponse.plan_series
    time_problems: list = field(default_factory=list)     # [(severity, message)] from timecourse.plan_series
    cys_problems: list = field(default_factory=list)      # [(severity, message)] from cys.run / the site annotation
    protein_problems: list = field(default_factory=list)  # [(code, severity, message)] from proteincorr (D70)
    phospho_problems: list = field(default_factory=list)  # [(code, severity, message)] from phospho.py (D79)
    psm_problems: list = field(default_factory=list)      # [(issue code, message)] from psmqc.run
    run_problems: list = field(default_factory=list)      # [(issue code, message)] from runorder.run (D78)
    model: object = None                # analysis.Model: the design used, or why an asked-for one wasn't
    roles: object = None                # roles.Plan: the conditions' roles and the comparisons they gave
    guards: list = field(default_factory=list)            # guards.statistics: p-values that may not mean what they say

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
    # other engines' results (engines.py)
    "DIA-NN": ("a DIA-NN report (report.tsv, report.parquet) or …pg_matrix.tsv",
               ["DIA-NN stopped early (see its .log.txt)", "The results are in another folder than the one given"]),
    "MaxQuant": ("combined/txt/proteinGroups.txt",
                 ["MaxQuant stopped before the protein step (see combined/proc)",
                  "Only the combined/txt folder was copied without proteinGroups.txt"]),
    "Sage": ("Sage's lfq.tsv (label-free) or tmt.tsv (TMT)",
             ["Quantification was off in the Sage settings (quant.lfq, quant.tmt), so only results.sage.tsv exists",
              "Sage was run with --parquet, which Ionomos doesn't read: run it without",
              "Sage stopped before quantification (see its console output)"]),
    "Spectronaut": ("a Spectronaut report (PG.Quantity columns, or the long BGS report)",
                    ["The report was exported with a schema that has no PG.Quantity (`ionomos spectronaut-columns` "
                     "lists the columns to tick)",
                     "The export is a peptide / precursor-only report"]),
    "AlphaDIA": ("AlphaDIA's pg.matrix.tsv", ["AlphaDIA stopped before the protein step (see its log)"]),
    "MSstats": ("an MSstats-format table (ProteinName, Run, Condition, BioReplicate, Intensity)",
                ["The converter wrote another format"]),
    "MSstatsTMT": ("an MSstatsTMT-format table (ProteinName, Mixture, Run, Channel, Condition, BioReplicate, "
                   "Intensity)", ["The converter wrote the label-free MSstats format instead"]),
    "PD": ("a Proteome Discoverer Proteins export (Accession + Abundance columns)",
           ["The Proteins table was exported without abundance columns"]),
}

def _tables_present(workdir: Path | None, limit: int = 12) -> list[str]:
    if workdir is None or not Path(workdir).is_dir():
        return []
    try:
        found = sorted(p.relative_to(workdir).as_posix() for p in Path(workdir).rglob("*.tsv"))
    except OSError:
        return []
    return found[:limit]

def _ratio_checks(f: Findings, p, s, add) -> None:
    """isoDTB site ratios (D70): RATIO_OFFSET when a replicate sits clearly off 0 and nothing centred it;
    PROTEIN_CORRECTION / PROTEIN_CORRECTION_CONDITIONS from the protein-abundance correction."""
    centring = ((getattr(p, "normalization", None) or {}).get("ratio_centre") or {}) if p is not None else {}
    off = [(c, v) for c, v in (centring.get("conditions") or {}).items() if v["clear"] and not v["centred"]]
    if off:
        reps = [(x, r["offset"]) for c, v in off for x, r in v["replicates"].items() if x in v["clear"]]
        add(Issue("RATIO_OFFSET", "warning", "A replicate's site ratios sit off 0",
                  "Measured on the sites that do not change, " + ", ".join(f"{x} is {o:+.2f} log2" for x, o in reps[:6])
                  + (" …" if len(reps) > 6 else "") + " away from 0 (heavy / light "
                  + ", ".join(f"{2 ** o:.2f}" for _x, o in reps[:3]) + " instead of 1). Every ratio of that replicate is "
                  "moved by as much, so unchanged sites look changed and some are called. The ratios were used as "
                  "measured (analysis.ratio_centre: " + (centring.get("asked") or "none") + ").",
                  ["Heavy and light were not mixed exactly 1:1 (protein assay or pipetting)",
                   "A compound that changes most sites in one direction (then centring would be wrong)"],
                  ["If the mixing is the likely cause, set ratio_centre: auto under analysis: (experiment.yaml, or "
                   "config.yaml for the lab) and Run analysis: each replicate is then centred on its stable sites",
                   "Leave it if the compound really moves most sites; the liganded calls use the ratios as measured"],
                  {"replicates": {x: round(o, 3) for x, o in reps}}))
    for code, sev, msg in getattr(f, "protein_problems", None) or []:
        if code == "PROTEIN_CORRECTION_CONDITIONS":
            add(Issue("PROTEIN_CORRECTION_CONDITIONS", sev,
                      "Protein correction: the proteome's comparisons don't match the sites' conditions",
                      msg,
                      ["The isoDTB sample is named after the experiment (EJQ_2_027), the proteome after the compound",
                       "The proteome compares other conditions, or the same compound twice (against two controls)"],
                      ["Name the proteome comparison for each site condition under analysis: protein_correction: "
                       "conditions: {EJQ_2_027: Cmpd vs DMSO} in experiment.yaml (the compound first) and Run analysis",
                       "The uncorrected results are not affected"], {"message": msg}))
        else:
            add(Issue("PROTEIN_CORRECTION", sev, "The protein correction needs a look", msg,
                      ["The proteome folder or table was moved, renamed or not analysed yet",
                       "match: gene with a table that has accessions only (or the other way round)",
                       "The proteome was searched against another database, so few proteins are in both"],
                      ["Give the analysed proteome's folder (or its protein table) as a full path in "
                       "analysis.protein_correction.proteome and Run analysis",
                       "Try match: protein when gene names differ between the two searches",
                       "The uncorrected results are not affected"], {"message": msg}))


_PHOSPHO_ISSUES = {i.code: i for i in (  # phospho.py's problems (D79): the message is filled in by _phospho_checks
    Issue("PHOSPHO_TABLE", "input", "Phospho: no phosphosite table could be used", "",
          ["The search had no PTM site localisation (PTMProphet) or no site report step, so FragPipe wrote no "
           "combined_site_STY_79.9663.tsv or single-site report",
           "DIA-NN was run without a FASTA or without matrices, so report.phosphosites_90.tsv is missing",
           "analysis.phospho_table names a file that was moved, or is not a site table"],
          ["Turn on PSM site localisation and the site reports in the FragPipe workflow and search again",
           "Or give the site table's full path in analysis.phospho_table and Run analysis",
           "The proteins were analysed instead, so the report is still usable"]),
    Issue("PHOSPHO_LOCALISATION", "warning", "Phospho: the localisation filter could not be applied as asked", "",
          ["TMT-Integrator's report and DIA-NN's matrix carry no per-site probabilities: their own threshold was "
           "applied before Ionomos saw the table, and it is lower than phospho_min_localization"],
          ["Set the threshold in the search (tmtintegrator.min_site_prob in the workflow) and search again, or lower "
           "phospho_min_localization to what the search used"]),
    Issue("KINASE_SUBSTRATES", "warning", "Kinase activity: the kinase-substrate table could not be used", "",
          ["The file named in analysis.kinase_substrates was moved or renamed",
           "It is not PhosphoSitePlus's Kinase_Substrate_Dataset (or KSEAapp's PSP&NetworKIN file)",
           "Few measured sites are substrates in it: gene names differ (try ksea_match: protein), another organism "
           "(ksea_organism), or ksea_min_substrates is high for this experiment"],
          ["Download Kinase_Substrate_Dataset from PhosphoSitePlus (free for non-commercial use), put it in the "
           "experiment folder or give its full path in analysis.kinase_substrates, and Run analysis",
           "The site statistics are not affected"]),
    Issue("STRING_NETWORK", "warning", "Interaction partners: the STRING network could not be used", "",
          ["The file named in analysis.string_network was moved or renamed",
           "A protein.links file without its protein.info file in the same folder", "Not a STRING file"],
          ["Download <taxon>.protein.links and <taxon>.protein.info from string-db.org into one folder and give the "
           "links file's full path in analysis.string_network, then Run analysis", "The statistics are not affected"]),
)}


def _phospho_checks(f: Findings, add) -> None:
    """phospho.py's problems (D79); the protein correction's go through _ratio_checks."""
    from dataclasses import replace

    for code, sev, msg in getattr(f, "phospho_problems", None) or []:
        if code in _PHOSPHO_ISSUES:
            add(replace(_PHOSPHO_ISSUES[code], severity=sev, message=msg, causes=list(_PHOSPHO_ISSUES[code].causes),
                        fixes=list(_PHOSPHO_ISSUES[code].fixes), data={"message": msg}))



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

def _within(comp: dict) -> str:
    """The composition check was made within TMT plexes (fpa.normalize_info, D71)."""
    return f" (compared within each of the {comp['plexes']} TMT plexes)" if comp.get("plexes") else ""


def _model_holds_plexes(model, m) -> bool:
    """Does the model the comparisons used fit a level per TMT plex (plex.holds_plexes)?"""
    from ionomos.downstream import plex

    design = getattr(model, "design", None)
    return design is not None and plex.holds_plexes(design, m)


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

    # ---- search quality per run (psmqc.py): said even when there is nothing to analyse, as it may be why
    for code, msg in f.psm_problems:
        if code == "PSM_MASS_ERROR":
            add(Issue("PSM_MASS_ERROR", "warning", "Precursor masses are off in some runs", msg,
                      ["The mass spectrometer's calibration has drifted (the lock mass was off, or it is due a "
                       "calibration)",
                       "The search allows a mass offset or a modification that isn't set, so the errors are not "
                       "instrument errors"],
                      ["Look at the Search quality tab under Quality control: is it every run, or the runs of one day?",
                       "Tell whoever looks after the instrument; calibrate before the next runs",
                       "The search corrects a steady offset, so the results usually stand; a run with far fewer "
                       "PSMs than the others is worth re-acquiring"], {"message": msg}))
        elif code == "PSM_MISSED_CLEAVAGES":
            add(Issue("PSM_MISSED_CLEAVAGES", "warning", "Many missed cleavages in some runs", msg,
                      ["The digestion was incomplete (too little trypsin, too short, the wrong pH, old enzyme)",
                       "The enzyme set in the workflow is not the one used, or the peptides were not made with a "
                       "protease"],
                      ["Look at the Search quality tab under Quality control: is it one sample, or all of them?",
                       "Compare a flagged sample's quantities with its replicates before trusting them; an "
                       "incompletely digested sample measures different peptides",
                       "Check the digestion for the next preparation"], {"message": msg}))

    # ---- run order (runorder.py, D78): drift during the run, conditions acquired in blocks
    for code, msg in f.run_problems:
        if code == "RUN_ORDER_DRIFT":
            add(Issue("RUN_ORDER_DRIFT", "warning", "The samples drift with the order they were run in", msg,
                      ["The spray, the column or the trap got dirtier over the sequence (fewer identifications, "
                       "less signal)",
                       "The mass calibration drifted during the sequence (the mass error moves)",
                       "Samples waited longer in the autosampler the later they were run"],
                      ["Look at the Run order tab under Quality control: is it a steady slope, or a step at one run?",
                       "If the conditions were interleaved, the comparisons are still fair but noisier; if they "
                       "were acquired in blocks, treat differences between them with care",
                       "Next time, randomise the run order, and run a QC standard between the samples"],
                      {"message": msg}))
        elif code == "RUN_ORDER_CONFOUNDED":
            add(Issue("RUN_ORDER_CONFOUNDED", "warning", "The conditions were run in blocks", msg,
                      ["The samples were queued condition by condition (all controls, then all treated)",
                       "A sequence was split over days or columns along the conditions"],
                      ["Look at the Run order tab under Quality control: do the QC numbers change along the run?",
                       "If they do not, the comparisons stand; if they do, part of a difference between the "
                       "conditions may be the instrument",
                       "Next time, randomise or interleave the run order (rep 1 of every condition, then rep 2, ...)"],
                      {"message": msg}))

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
        _insight_checks(f, None, add)
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

    sd = m.meta.get("sdrf") or {}
    if sd and sd.get("unmatched"):
        none = not sd.get("used")
        add(Issue("SDRF_UNMATCHED_RUNS", "input",
                  "The SDRF doesn't describe these runs" if none else "Some runs aren't in the SDRF",
                  (f"{sd.get('file')} names none of the {len(sd['unmatched'])} runs in the table, so it was not used "
                   "and the conditions come from the names." if none else
                   f"{len(sd['unmatched'])} run(s) have no row in {sd.get('file')} (matched by comment[data file] "
                   "and label), so they kept the condition from their name: " + ", ".join(sd["unmatched"][:8]) +
                   ("…" if len(sd["unmatched"]) > 8 else "") + "."),
                  ["The SDRF belongs to another experiment or another search of it",
                   "Files were renamed or converted after the SDRF was written (its data files must match the runs)",
                   "TMT: the SDRF's comment[label] channels don't match the table's channels"],
                  ["Fix the data file names in the SDRF, or give these samples their condition here and Run analysis"],
                  {"runs": list(sd["unmatched"]), "sdrf": sd.get("file")}))
    tmt_ = f.tmt or {}
    if tmt_ and not tmt_.get("applied") and len(tmt_.get("plexes") or {}) > 1 and \
            "irs: none" not in (tmt_.get("reason") or ""):
        add(Issue("TMT_PLEXES_NOT_NORMALISED", "warning", "The TMT plexes are not on one scale",
                  f"This experiment has {len(tmt_['plexes'])} TMT plexes, and they were not normalised to each other: "
                  f"{tmt_.get('reason')}. Differences between plexes will look like differences between samples "
                  "(the PCA, coloured by plex, shows it).",
                  ["No pooled reference (bridge) channel is named for the experiment",
                   "Each plex holds a different mix of conditions, so the plexes' own means can't be used"],
                  ["Set analysis.tmt_reference to the pooled channel (e.g. 126) in experiment.yaml, or mark it "
                   "'pooled' in the SDRF, then Run analysis"],
                  {"plexes": tmt_.get("plexes")}))
    elif tmt_ and not tmt_.get("applied") and len(tmt_.get("plexes") or {}) > 1 and p is not None and \
            not _model_holds_plexes(f.model, p.m):
        add(Issue("TMT_PLEXES_NOT_IN_MODEL", "warning", "The TMT plexes are neither on one scale nor in the model",
                  f"This experiment has {len(tmt_['plexes'])} TMT plexes, IRS is switched off (irs: none), and the "
                  "model has no block for the plex. The plex effect then counts as replicate spread: the tests stay "
                  "conservative but miss many changes (in simulated plexes 40 % of 2-fold changes were found, "
                  "against 95 % with IRS or a plex block).",
                  ["irs: none was set for this experiment or the lab", "A block was asked for, but not by plex"],
                  ["Put the plexes on one scale: name the pooled channel (analysis.tmt_reference) and set irs to "
                   "auto, then Run analysis",
                   "Or keep irs: none and block on the plex (analysis.block: {sample: plex}, or block_from on the "
                   "sample names), so every comparison is made within the plexes"],
                  {"plexes": tmt_.get("plexes")}))

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
        _role_checks(f, add)
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
        if p is not None:
            comp = (getattr(p, "normalization", None) or {}).get("composition") or {}
            asked, used = (p.normalization or {}).get("asked"), (p.normalization or {}).get("used")
            if comp.get("exceeded") and used in ("median", "gn"):
                a, b = comp["between"]
                add(Issue("NORMALISATION_COMPOSITION", "input", "The normalisation shifts the conditions against each other",
                          f"Median centring moves {a} against {b} by {comp['shift']:.2f} log2 compared with a "
                          f"normalisation on {comp['features']:,} stable features{_within(comp)}. Unchanged features "
                          "then look changed by that much, and some pass the fold-change cut-off.",
                          ["Many features are enriched or depleted in one direction (a pulldown, a depletion, a "
                           "strong treatment), so the middle of the abundance distribution moves",
                           "A sample with far fewer identifications than the others",
                           "A short table with many missing values: each sample's median then rests on different features"],
                          ["Set Normalisation to auto (Analysis tab, or normalize: auto under analysis:) and Run "
                           "analysis: it uses the ratio method whenever this happens",
                           "Or choose ratio to use it always, or none if the samples should not be put on one scale"],
                          {"shift": round(comp["shift"], 3), "between": [a, b], "asked": asked}))
            elif comp.get("exceeded") and asked == "auto" and used == "ratio":
                a, b = comp["between"]
                add(Issue("NORMALISATION_COMPOSITION", "warning", "Normalised on stable features, not on the median",
                          f"Median centring would have moved {a} against {b} by {comp['shift']:.2f} log2, because "
                          "many features change in one direction. The samples were normalised on the ratios of "
                          f"{comp['features']:,} stable features{_within(comp)} instead, so unchanged features stay "
                          "unchanged.",
                          ["A pulldown, a depletion or a strong treatment: a large share of the features is "
                           "enriched or depleted"],
                          ["Nothing to do. To force one method, set Normalisation to median or ratio"],
                          {"shift": round(comp["shift"], 3), "between": [a, b], "asked": asked}))
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

    _insight_checks(f, p, add)
    _design_checks(f, s, add)

    # ---- dose-response (doseresponse.py): doses that can't be read, a titration without a control
    for sev, msg in f.dose_problems:
        add(Issue("DOSES", sev, "Dose-response: the doses need a look", msg,
                  ["analysis.doses names a condition that isn't in this experiment, or a dose without a unit",
                   "A condition name holds two doses (a combination), or the vehicle isn't named DMSO / vehicle"],
                  ["List every condition's dose in experiment.yaml analysis.doses (DMSO: 0, Cmpd_A: 10 nM, ...) "
                   "and Run analysis"], {"message": msg}))

    # ---- time course (timecourse.py): times that can't be read, two conditions at one time; a spline whose df
    # the series can't carry (D77)
    for item in f.time_problems:
        code, sev, msg = item if len(item) == 3 else ("TIMES", *item)
        if code == "TIME_SPLINE":
            add(Issue("TIME_SPLINE", sev, "Time course: the spline was not fitted as asked", msg,
                      ["analysis.time_spline_df is as high as or higher than the series' time points - 1, so the "
                       "curve could not smooth anything", "The block or covariates leave the curve no residual "
                       "degrees of freedom"],
                      ["Lower time_spline_df under analysis: in experiment.yaml (or remove it: auto is 4, at most the "
                       "time points - 2) and Run analysis", "Or test time as a factor: time_model: factor"],
                      {"message": msg}))
            continue
        add(Issue("TIMES", sev, "Time course: the time points need a look", msg,
                  ["analysis.times names a condition that isn't in this experiment, or a time without a unit",
                   "A condition name holds two times, or two conditions of one series are at the same time"],
                  ["List every condition's time in experiment.yaml analysis.times (Drug_a: 0, Drug_b: 4 h, ...) "
                   "and Run analysis", "Or switch the time-course tests off: time_course: false"], {"message": msg}))

    # ---- liganded cysteines (cys.py): a ratio that looks the other way round, an annotation that can't be used
    for sev, msg in f.cys_problems:
        if "the other way round" in msg:
            add(Issue("LIGANDED_DIRECTION", sev, "Liganded sites: the ratio may be the other way round", msg,
                      ["The compound-treated sample carries the heavy tag in this experiment, not the light one",
                       "The compound makes many sites more reactive, or the two samples were mixed unevenly"],
                      ["Check which sample got the heavy and which the light tag. If the treated one is heavy, set "
                       "liganded_direction: low under analysis: in experiment.yaml (or for the whole lab in "
                       "config.yaml) and Run analysis"], {"message": msg}))
        else:
            add(Issue("SITE_ANNOTATION", sev, "The site annotation could not be used", msg,
                      ["The file named in analysis.site_annotation was moved or renamed",
                       "The table has no column of site keys like P04406_C152"],
                      ["Put the downloaded table (e.g. CysDB) in the experiment folder, or give its full path in "
                       "analysis.site_annotation, and Run analysis", "The liganded calls themselves are not affected"],
                      {"message": msg}))

    # ---- site ratios (D70): a replicate clearly off 0 that was not centred; the protein correction
    _ratio_checks(f, p, s, add)

    # ---- phosphosites, kinase activity, STRING (D79): a site table or a download that can't be used
    _phospho_checks(f, add)

    # ---- statistics that ran but may not mean what they say (guards.py)
    from ionomos.downstream import guards

    out.extend(guards.issues(f.guards))

    # ---- statistics and plots
    _result_checks(f, s, add)
    return out

def _role_checks(f: Findings, add) -> None:
    """The conditions' roles (roles.py): what a competition experiment was read as, and a question when a role
    can't be told from the names. Nothing is said for an experiment without a competition condition."""
    plan = f.roles
    if plan is None or plan.by_construction or not plan.roles:
        return
    listed = plan.describe()
    how = ["Check each condition's role under Roles in the experiment editor (Analysis tab, or the pop-up window): "
           "Confirm a guess, or pick the role, then Run analysis",
           "Or set them under analysis: in the experiment's experiment.yaml, e.g. roles: {DMSO: control, "
           "Probe: compound, Probe_Comp: competition of Probe}",
           "Or list the comparisons yourself (comparisons: [\"Probe vs DMSO\"]); role_comparisons: false gives "
           "every condition against the control, as for any other experiment"]
    if plan.questions:
        add(Issue("ROLES_UNSURE", "input", "Check the roles of the conditions",
                  " ".join(q.rstrip(".") + "." for q in plan.questions) + f" Read so far: {listed}.",
                  ["A condition's name has a word that often, but not always, means 'plus a competitor' "
                   "(pre, block, 10x)",
                   "There are several compounds, and the competition's name doesn't say which one it competes"],
                  how, {"roles": {c: r.text() for c, r in plan.roles.items()}, "questions": list(plan.questions),
                        "conditions": list(plan.roles)}))
    if plan.active:
        kinds = {"enrichment": "what the compound enriches", "competition": "what the competitor takes off",
                 "remaining": "what is left with the competitor"}
        add(Issue("COMPETITION_DESIGN", "warning", "Read as a competition experiment",
                  f"Roles: {listed}. Compared: "
                  + "; ".join(f"{t} vs {c} ({kinds.get(plan.kinds.get((t, c)), 'comparison')})"
                              for t, c in plan.comparisons)
                  + ("" if not plan.skipped else ". Not compared: " + "; ".join(f"{w} ({y})" for w, y in plan.skipped))
                  + ". Specific targets (enriched and competed off) are in the report and specific_targets.tsv.",
                  ["A condition's name says it is the compound plus a competitor (Comp, competition, excess ...), "
                   "or analysis.roles says so"],
                  ["Nothing to do if the roles are right"] + how,
                  {"roles": {c: r.text() for c, r in plan.roles.items()},
                   "comparisons": [f"{t} vs {c}" for t, c in plan.comparisons]}))

LEFT_CENSORED = ("perseus", "mindet", "minprob", "min", "zero")

def _insight_checks(f: Findings, p, add) -> None:
    """The deeper QC (insights.py): outlier samples, a batch-like structure, imputation that doesn't fit the
    missingness. Warnings only: they are shown in the report and the attention list, never a pop-up."""
    ins = f.insights or {}
    card = [r for r in ins.get("scorecard") or [] if r["status"] == "fail"
            and not any(fl.startswith("far fewer identifications") for fl in r["flags"])]  # LOW_SAMPLE covers those
    if card:
        add(Issue("SAMPLE_OUTLIER", "warning", f"{len(card)} sample(s) look like outliers",
                  "; ".join(f"{r['sample']}: {', '.join(r['flags'])}" for r in card[:4]) +
                  ". An outlier replicate hides real changes and can create false ones.",
                  ["A failed or partial injection, or a sample handled differently in prep",
                   "A sample swap: it behaves like another condition (check the PCA)",
                   "The sample really is different (then keep it)"],
                  ["Look at the report's Sample scorecard and PCA; if it is a technical failure, leave it out on "
                   "the Analysis tab and Run analysis (you can always add it back)"],
                  {"samples": [r["sample"] for r in card]}))
    batch = (ins.get("pcs") or {}).get("batch")
    if batch:
        rc = batch.get("r2_condition")
        model = f.model
        blocked = model is not None and model.design is not None and any(
            t["name"] == "replicate" for t in model.design.terms)
        fixes = (["The comparisons already block on the replicate number (analysis.block: replicate), which takes "
                  "this batch out of the tests; the PCA still shows it",
                  "Randomise the run order next time"] if blocked else
                 ["Block on it: add `block: replicate` under `analysis:` in experiment.yaml (or the lab's settings) and "
                  "Re-run analysis. The replicate number becomes a fixed effect (a paired / batch design), so the "
                  "batch no longer hides changes",
                  "Check the PCA coloured by replicate in the report; randomise the run order next time",
                  "Without blocking, a balanced design still gives valid comparisons, only less sensitive ones"])
        add(Issue("BATCH_SUSPECT", "warning", "Samples group by replicate number, not only by condition",
                  f"PC{batch['pc']} ({batch['percent']:.0f}% of the variance) is {100 * batch['r2_replicate']:.0f}% "
                  f"explained by the replicate number and {100 * (rc or 0):.0f}% by condition. Replicates with the "
                  "same number were probably prepared or run together, and that batch shows in the data.",
                  ["Replicates prepared, digested or acquired on different days / columns",
                   "Instrument drift over a long queue"],
                  fixes, {**batch, "blocked": blocked}))
    loss = ins.get("filter_loss") or {}
    s = f.settings
    if loss.get("complete") and s is not None and getattr(s, "filter_global_pct", 0):
        n, only = loss["complete"], loss.get("only_one", 0)
        where = ", ".join(f"{c} {k}" for c, k in sorted(loss.get("by_condition", {}).items(), key=lambda t: -t[1]))
        cp = getattr(s, "filter_condition_pct", 0) or 0
        add(Issue("FILTER_REMOVES_ONE_CONDITION", "warning",
                  f"The missing-value filter removed {n} feature(s) measured in every sample of a condition",
                  f"The filter keeps features with values in at least {s.filter_global_pct:g}% of all samples"
                  + (f" (and {cp:g}% of one condition)" if cp else "") + f". It removed {n} that a condition had in "
                  f"every sample ({where})"
                  + (f"; {only} of them were never measured in any other condition, the clearest on/off changes"
                     if only else "") + ". They were not tested. Those found by a comparison are still listed "
                  "under 'Only in one condition', marked as removed by the filter.",
                  ["A filter on all samples counts every condition: with three conditions of three, a feature seen "
                   "in one condition only has 3 of 9 values (33%) and fails any filter above that",
                   "In a pull-down or a treatment, what one condition alone has is often the result"],
                  ["Filter on the conditions instead: 0% of all samples and 50% of one condition (Ionomos' default). "
                   "On the Analysis tab, 'Measured in ≥ % of all samples' 0 and '… and ≥ % of one condition' 50; "
                   "or filter_global_pct: 0 and filter_condition_pct: 50 under analysis:. Then Run analysis",
                   "Keep the global filter if the aim is a table of features measured nearly everywhere"],
                  {"global_pct": s.filter_global_pct, "condition_pct": cp, **loss}))
    miss = ins.get("missingness") or {}
    if (p is not None and p.imputation in LEFT_CENSORED and miss.get("verdict") == "random"
            and p.n_imputed > 0.05 * max(1, len(p.m.values) * len(p.m.samples))):
        add(Issue("IMPUTATION_MISMATCH", "warning", "Missing values look random, but were imputed as low values",
                  f"{p.n_imputed:,} values were imputed with {p.imputation} (as if they were below detection), yet "
                  "proteins go missing at every abundance in this data. Imputed low values then create fold changes.",
                  ["Missing values from run failures or identification transfer rather than low abundance"],
                  ["Try imputation 'knn' or 'none' for this experiment on the Analysis tab, and compare the hits"],
                  {"imputation": p.imputation, "rho": miss.get("rho"), "gap": miss.get("gap")}))
    for name, h in (ins.get("phist") or {}).items():
        if h.get("shape") in ("conservative", "hump"):
            what = ("piles up near p = 1" if h["shape"] == "conservative" else "bulges in the middle")
            add(Issue("P_VALUE_SHAPE", "warning", f"{name}: the p-value histogram looks unusual",
                      f"The histogram {what} instead of being flat with a peak near 0, so the p-values (and the "
                      "adjusted ones) may not mean what they say.",
                      ["Many tied values from imputation (identical imputed numbers in both groups)"
                       if h["shape"] == "conservative" else "An outlier sample or a hidden batch inflating the variance",
                       "A group that is much noisier than the others (the model assumes one variance per feature), "
                       "or a condition mislabelled"],
                      ["Check the Sample scorecard and PCA; try a stricter missing-value filter"],
                      {"comparison": name, "shape": h["shape"]}))
    for name, idx in (ins.get("imputation_driven") or {}).items():
        d = next((x for x in f.diffs if x.name == name), None)
        hits = (d.up + d.down) if d else 0
        if len(idx) >= 5 and hits and len(idx) / hits >= 0.3:
            add(Issue("IMPUTATION_DRIVEN", "warning", f"{name}: {len(idx)} of {hits} hits rest on imputed values",
                      "At least half of one group's values were imputed for these hits, so their fold changes come "
                      "partly from the imputation, not from measurements.",
                      ["Proteins present in one condition only (real on/off changes: see 'Only in one condition')",
                       "Low-abundance proteins near the detection limit"],
                      ["In the report, tick 'hide imputation-driven' to see the hits that stand on measured values"],
                      {"comparison": name, "count": len(idx)}))

def _design_checks(f: Findings, s, add) -> None:
    """An experimental design (analysis.block / block_from / covariates) or DEqMS that was asked for and
    couldn't be used. The comparisons still ran, on the plain model; the person should know which."""
    model = f.model
    if model is None or s is None:
        return
    if model.problem:
        add(Issue("DESIGN_NOT_USED", "input", "The experimental design couldn't be used",
                  f"{model.problem[:1].upper()}{model.problem[1:]}. The comparisons were made with the plain model "
                  "(~0 + condition) instead, without the blocks or covariates.",
                  ["The block is the same as the condition (every block holds one condition): a batch processed "
                   "one condition at a time can't be separated from the treatment",
                   "A sample has no block or covariate value (a typo, or samples renamed or left out)",
                   "Too many blocks or covariates for the number of samples"],
                  ["Fix analysis.block / block_from / covariates in experiment.yaml, then Re-run analysis",
                   "In a paired design every pair (block) needs samples from at least two conditions"],
                  {"problem": model.problem, "block": s.block if isinstance(s.block, str) else "mapping",
                   "block_from": s.block_from, "covariates": list(s.covariates)}))
    prior = model.prior or {}
    if s.test == "limma" and s.variance_prior == "deqms" and prior and not prior.get("deqms_used", True):
        add(Issue("DEQMS_NOT_USED", "warning", "DEqMS wasn't used: limma's single variance prior was",
                  f"variance_prior: deqms needs a peptide (or PSM) count per feature, and {prior.get('reason', '')}.",
                  ["The result table has no peptide-count column (e.g. a bare matrix, or an engine export "
                   "without it)", "Very few features were quantified"],
                  ["Nothing to do: the statistics are limma's usual ones. To use DEqMS, analyse a table with "
                   "peptide counts (DIA-NN, FragPipe, MaxQuant, TMT-Integrator give them)"],
                  {"reason": prior.get("reason", "")}))

def _result_checks(f: Findings, s, add) -> None:
    """Per-comparison checks: confidence labels, nothing tested, no hits, missing plots, enrichment."""
    small = {(t, c) for t, c, _ in f.small_groups}
    sizes = {(t, c): min((n for _g, n in few), default=1) for t, c, few in f.small_groups}
    for d in f.diffs:
        conf = getattr(d, "confidence", "")
        if conf == "low":
            n = sizes.get((d.treatment, d.control), 1)  # 1 unless min_valid asks for more than two
            add(Issue("LOW_CONFIDENCE", "warning",
                      f"{d.name}: low confidence (a group has {'one sample' if n == 1 else f'{n} samples'})",
                      d.confidence_note,
                      ["The experiment has a single replicate of a condition" if n == 1 else
                       f"A condition has {n} replicates, fewer than the {getattr(s, 'min_valid', 2)} the settings ask for",
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
