"""
Settings, comparisons and differential statistics on a QuantMatrix.

The statistics are FragPipe-Analyst's (downstream/fpa.py): filter -> normalise ->
impute -> limma -> add_rejections. Settings come from config.yaml `analysis:`
(lab defaults, edited on the app's Analysis tab) overridden by the experiment's
experiment.yaml `analysis:` block:

    analysis:
      de_type: control            # control (each condition vs the control) | all (every pair) | others (each vs the rest)
      control: DMSO               # default: first condition matching control_keywords
      roles: {DMSO: control, Probe: compound, Probe_Comp: competition of Probe}   # default: read from the names
                                  #   (roles.py, D61); a competition changes the default comparisons to compound vs
                                  #   control, competition vs its compound, competition vs control
      competition_keywords: [comp, competition, competitor, excess]   # a token of the name that means "plus a
                                  #   competitor"; competition_keywords_weak (pre, block ...) count only next to
                                  #   the compound's own condition
      role_comparisons: true      # false: the roles are shown, the comparisons are every condition vs the control
      comparisons: ["Drug vs DMSO"]   # explicit list; wins over de_type
      log2fc: 1.0                 # |log2 fold change| threshold (FragPipe-Analyst "lfc")
      alpha: 0.05                 # significance threshold (FragPipe-Analyst "p")
      use_adjusted: true          # alpha applies to BH-adjusted p (false: raw p)
      test: limma                 # limma (moderated t, default) | welch | student
      remove_contaminants: true
      filter_global_pct: 0        # min % of all samples with a value
      filter_condition_pct: 50    # min % with a value in at least one condition
      normalize: auto             # auto (default: median, or ratio when many features change one way) | median | gn | ratio | none
      imputation: auto            # auto | none | perseus | min | zero | mindet | minprob | knn
      min_valid: 2                # measured values per group needed when nothing is imputed
      small_group_min_valid: half # half: the smaller group of an unbalanced comparison (DMSO n=2 vs n=4) needs
                                  #   half its samples measured, not min_valid | same: every group needs min_valid
      exclude_samples: [DMSO_3]   # leave samples out
      sample_conditions: {Drug_4: DMSO}   # give a sample another condition
      enrichment: true
      enrichment_libraries: [Hallmark, GO Biological Process, Reactome]
      top_labels: 15              # names drawn on each static volcano
      block: replicate            # a block (batch, plex, pair, patient) as a fixed effect: replicate | {sample: block}
      block_from: '_(P\\d+)_'     # ... or read from the sample names (a regex group, or (?P<block>...))
      covariates: {age: {DMSO_1: 54, Drug_1: 61}}   # numeric -> a slope, text -> a factor ({sample: value} = one)
      variance_prior: limma       # limma (one prior, eBayes) | deqms (a prior per peptide count, DEqMS)
      sdrf:                       # sample metadata for results/sdrf.tsv (sdrf.py): lab-wide in config.yaml,
        instrument: Orbitrap Eclipse   # per experiment in experiment.yaml (keys merge; the experiment's win)
        organism: homo sapiens    # default: the FASTA's OS=; also organism_part, cell_type, disease, cleavage_agent
      sdrf_factor: [compound]     # an SDRF in the folder sets the design (sdrfdesign.py): which factor value column(s)
                                  #   make the condition (default: all of them, joined with " | ")
      irs: auto                   # several TMT plexes on one scale (plex.py): auto | reference | sum | none
      tmt_reference: [126]        # IRS reference (pooled / bridge) channel(s) or sample(s); default: the SDRF's
                                  #   pooled rows, else samples named pool / bridge / reference / norm

      doses:                      # dose-response curves (doseresponse.py); default: read from the condition names
        DMSO: 0                   #   (Cmpd_10nM, Cmpd_0p1uM, 10 µM); the control is dose 0
        Cmpd_A: 10 nM
        Cmpd_B: 100 nM            #   ... at least dose_min_doses doses above 0, or no curves are fitted
      dose_unit: nM               # unit for bare numbers in doses (default: none, so every dose names its unit)
      dose_response: true         # false: never fit curves
      dose_min_doses: 4           # doses above 0 a compound needs before curves are fitted
      dose_alpha: 0.05            # CurveCurator's significance asymptote
      dose_fc_lim: 0.45           # CurveCurator's |log2 curve fold change| asymptote

      times:                      # a time course (timecourse.py); default: read from the condition names
        Drug_start: 0             #   (Drug_0h, Drug_30min, Drug_4h, 2d); units s, min, h, d
        Drug_early: 30 min
        Drug_late: 4 h            #   ... at least time_min_points time points per series, or no time-course tests
      time_unit: h                # unit for bare numbers in times
      time_course: true           # false: never run the time-course tests
      time_min_points: 3          # time points a series needs
      time_model: auto            # auto: time is a factor up to 6 time points, a natural spline in hours from 7 |
                                  #   factor | spline (D77)
      time_spline_df: auto        # the spline's degrees of freedom: auto = 4 (at most the time points - 2)

      liganded: true              # site ratio data (isoDTB): call liganded cysteines (cys.py)
      liganded_ratio: 4           # the competition ratio R a replicate must reach ...
      liganded_min_replicates: 2  # ... in at least this many replicates
      liganded_direction: high    # high: R = heavy / light | low: R = light / heavy
      site_annotation: cysdb.csv  # a downloaded site table (CysDB) in the experiment folder, or a full path
      ratio_centre: none          # site ratios (isoDTB): none (default) | median | auto: centre each replicate, which
                                  #   removes a heavy / light mixing error (fpa.centre_ratios, D70)
      protein_correction:         # site ratios (isoDTB): subtract each site's protein ratio from a matching
        proteome: D:/Fragpipe_General/EJQ/20261001-DIA_EJQ-2-030   # unenriched proteome (proteincorr.py, D70):
        match: gene               #   an analysed Ionomos experiment or a protein table; gene | protein
        conditions: {EJQ_2_027: Cmpd vs DMSO}   # site condition -> proteome comparison (default: the same name)

      psm_qc: true                # false: don't read psm.tsv for the per-run search quality (psmqc.py)

      export:                     # the lab's style for exported figures (charts.py STYLE_DEFAULTS; D62): the
        size: slide169            #   report's Export dialog starts from it, `ionomos export` uses it
        font_pt: 14               #   slide169 | slide43 | half | col1 | col2 | custom (+ width, height, unit)
        font_family: Arial
        palette: default          #   default | colorblind | grey | custom (+ up, down, neutral: "#rrggbb")
        background: light         #   light | dark | transparent
        figures: []               #   static figures written to results/figures/ after each analysis:
                                  #   any of volcano, pca, heatmap, correlation (or all)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields, replace

from ionomos.downstream import fpa, stats
from ionomos.downstream.enrich import DEFAULT_LIBRARIES, LIBRARIES
from ionomos.downstream.quant import QuantMatrix
from ionomos.downstream.roles import DEFAULT_COMPETITION_KEYWORDS, DEFAULT_COMPETITION_KEYWORDS_WEAK

DEFAULT_CONTROL_KEYWORDS = ("DMSO", "vehicle", "veh", "ctrl", "control", "mock", "untreated", "NT", "WT", "EV",
                            "scr", "scramble", "siNT", "PBS")
TESTS = {"limma": "limma moderated t-test", "welch": "Welch t-test", "student": "Student t-test"}
DE_TYPES = ("control", "all", "others")

@dataclass
class Settings:
    log2fc: float = 1.0
    alpha: float = 0.05
    use_adjusted: bool = True
    test: str = "limma"
    de_type: str = "control"
    control: str | None = None
    comparisons: list[tuple[str, str]] = field(default_factory=list)
    control_keywords: tuple[str, ...] = DEFAULT_CONTROL_KEYWORDS
    # the roles of the conditions and the comparisons that follow (roles.py, D61)
    roles: dict[str, str] = field(default_factory=dict)   # condition -> control | compound | competition of X | ...
    competition_keywords: tuple[str, ...] = DEFAULT_COMPETITION_KEYWORDS
    competition_keywords_weak: tuple[str, ...] = DEFAULT_COMPETITION_KEYWORDS_WEAK
    role_comparisons: bool = True
    min_valid: int = 2
    small_group_min_valid: str = "half"  # half | same: what the smaller group of an unbalanced comparison needs
    remove_contaminants: bool = True
    filter_global_pct: float = 0.0
    filter_condition_pct: float = 50.0
    normalize: str = "auto"
    imputation: str = "auto"
    impute_shift: float = 1.8
    impute_scale: float = 0.3
    seed: int = 123
    exclude_samples: list[str] = field(default_factory=list)
    sample_conditions: dict[str, str] = field(default_factory=dict)
    enrichment: bool = True
    enrichment_libraries: list[str] = field(default_factory=lambda: list(DEFAULT_LIBRARIES))
    enrichment_gmt: str = ""
    top_labels: int = 15
    pca_features: int = 500
    heatmap_max: int = 300
    sdrf: dict[str, str] = field(default_factory=dict)  # SDRF metadata: organism, instrument, ... (downstream/sdrf.py)
    # design import and TMT plexes (sdrfdesign.py, plex.py; D47, D48)
    sdrf_factor: list[str] = field(default_factory=list)  # factor value column(s) of an input SDRF; [] = all, joined
    tmt_reference: list[str] = field(default_factory=list)  # IRS reference channel(s) (126) or sample(s); [] = found
    irs: str = "auto"  # auto | reference | sum | none: put several TMT plexes on one scale

    doses: dict[str, str | float] = field(default_factory=dict)  # condition -> dose ("10 nM"); doseresponse.py
    dose_unit: str = ""
    dose_response: bool = True
    dose_min_doses: int = 4
    dose_alpha: float = 0.05
    dose_fc_lim: float = 0.45
    times: dict[str, str | float] = field(default_factory=dict)  # condition -> time ("4 h"); timecourse.py
    time_unit: str = ""
    time_model: str = "auto"           # auto | factor | spline (timecourse.py, D77)
    time_spline_df: int = 0            # the spline's df; 0 = auto (4, at most the time points - 2)
    time_course: bool = True
    time_min_points: int = 3
    liganded: bool = True              # liganded-site calls on site ratio data (cys.py)
    liganded_ratio: float = 4.0        # competition ratio R (linear) a replicate must reach
    liganded_min_replicates: int = 2
    liganded_direction: str = "high"   # high: R = heavy / light | low: R = light / heavy
    site_annotation: str = ""          # a site table (CysDB download): known / new sites
    ratio_centre: str = "none"         # site ratios: none | median | auto (fpa.centre_ratios, D70)
    protein_correction: dict = field(default_factory=dict)   # {proteome, match, conditions} (proteincorr.py, D70)
    psm_qc: bool = True                # per-run search quality from psm.tsv / DIA-NN stats.tsv (psmqc.py)
    block: str | dict[str, str] = ""   # "" | "replicate" | {sample: block} (design.py)
    block_from: str = ""               # regex on sample names: the block is group "block", else group 1
    covariates: dict[str, dict] = field(default_factory=dict)   # name -> {sample: value}
    variance_prior: str = "limma"      # limma | deqms
    export: dict = field(default_factory=dict)  # the keys the lab / experiment set for exported figures (charts.py)

    @property
    def has_design(self) -> bool:
        return bool(self.block or self.block_from or self.covariates)

    def describe(self) -> str:
        which = "adjusted p" if self.use_adjusted else "p"
        return f"|log2FC| ≥ {self.log2fc:g} and {which} ≤ {self.alpha:g} ({TESTS[self.test]})"

class AnalysisError(ValueError):
    pass

def parse_comparison(c) -> tuple[str, str]:
    if isinstance(c, (list, tuple)) and len(c) == 2:
        return str(c[0]).strip(), str(c[1]).strip()
    m = re.match(r"^\s*(.+?)\s+(?:vs\.?|versus|/|-vs-)\s+(.+?)\s*$", str(c), re.IGNORECASE)
    if not m:
        raise AnalysisError(f"comparison {c!r} must look like 'Drug vs DMSO' or [Drug, DMSO]")
    return m.group(1), m.group(2)

def _bool(v) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")

def _list(v) -> list[str]:
    items = v if isinstance(v, (list, tuple)) else str(v).split(",")
    return [str(x).strip() for x in items if str(x).strip()]

def _sdrf_meta(v) -> dict[str, str]:
    from ionomos.downstream.sdrf import SETTINGS

    if not isinstance(v, dict):
        raise AnalysisError("sdrf must map a field to a value, e.g. {instrument: Orbitrap Eclipse}")
    out = {}
    for key, val in v.items():
        k = str(key).strip().lower().replace(" ", "_")
        if k not in SETTINGS:
            raise AnalysisError(f"unknown sdrf field {key!r} (known: {', '.join(SETTINGS)})")
        if val is not None and str(val).strip():
            out[k] = str(val).strip()
    return out

VARIANCE_PRIORS = ("limma", "deqms")
SMALL_GROUP_RULES = ("half", "same")

def _roles(v) -> dict[str, str]:
    from ionomos.downstream import roles

    try:
        return roles.normalise(v)
    except roles.RoleError as exc:
        raise AnalysisError(f"roles: {exc}") from None

def _block(v) -> str | dict[str, str]:
    if isinstance(v, dict):
        out = {str(a).strip(): str(b).strip() for a, b in v.items() if b is not None and str(b).strip()}
        if not out:
            raise AnalysisError("block: give each sample its block, e.g. {DMSO_1: A, Drug_1: A, DMSO_2: B, Drug_2: B}")
        return out
    t = str(v).strip()
    if t.lower() in ("", "none", "no", "false"):
        return ""
    if t.lower() in ("replicate", "replicates", "rep"):
        return "replicate"
    raise AnalysisError(f"block {t!r}: use 'replicate' (the replicate number is the block, e.g. a batch or a pair), "
                        "a mapping {sample: block}, or block_from with a pattern on the sample names")

def _block_from(v) -> str:
    t = str(v)
    if not t.strip():
        return ""
    try:
        rx = re.compile(t)
    except re.error as exc:
        raise AnalysisError(f"block_from {t!r} is not a valid regular expression ({exc})") from None
    if not rx.groups:
        raise AnalysisError(f"block_from {t!r} needs a group in ( ) for the block, e.g. '_(P\\d+)_' or "
                            "'(?P<block>[A-Z]+)$'")
    return t

def _covariates(v) -> dict[str, dict]:
    if not isinstance(v, dict) or not v:
        raise AnalysisError("covariates must map a name to {sample: value}, e.g. {age: {DMSO_1: 54, Drug_1: 61}}"
                            " (or be one {sample: value} mapping)")
    if all(isinstance(x, dict) for x in v.values()):
        named = v
    elif not any(isinstance(x, dict) for x in v.values()):
        named = {"covariate": v}
    else:
        raise AnalysisError("covariates: either one {sample: value} mapping, or name: {sample: value} for each")
    out = {}
    for name, spec in named.items():
        n = str(name).strip()
        if not n or n.lower() in ("condition", "replicate", "block"):
            raise AnalysisError(f"covariate name {name!r} is reserved or empty; call it e.g. 'age' or 'batch'")
        if not spec:
            raise AnalysisError(f"covariate {n!r} has no values")
        out[n] = {str(a).strip(): b for a, b in spec.items()}
    return out

def settings_from(*layers: dict | None) -> Settings:
    """Later layers win. Unknown keys are an error (typos shouldn't silently do nothing)."""
    s = Settings()
    names = {f.name for f in fields(Settings)}
    for layer in layers:
        layer = layer or {}
        if layer.get("block") not in (None, "") and layer.get("block_from") not in (None, ""):
            raise AnalysisError("set either block or block_from, not both")
        for k, v in layer.items():
            if k == "enabled":
                continue
            if k not in names:
                raise AnalysisError(f"unknown analysis setting {k!r} (known: {', '.join(sorted(names))})")
            if v is None or (v == "" and k not in ("enrichment_gmt",)):
                continue
            try:
                if k in ("log2fc", "alpha", "impute_shift", "impute_scale", "filter_global_pct", "filter_condition_pct",
                         "dose_alpha", "dose_fc_lim", "liganded_ratio"):
                    v = float(v)
                elif k in ("min_valid", "top_labels", "seed", "pca_features", "heatmap_max", "dose_min_doses",
                           "liganded_min_replicates", "time_min_points"):
                    v = int(v)
                elif k in ("use_adjusted", "remove_contaminants", "enrichment", "dose_response", "liganded", "time_course",
                           "psm_qc", "role_comparisons"):
                    v = _bool(v)
                elif k == "test":
                    v = str(v).lower()
                    v = "limma" if v in ("moderated", "limma") else v
                    if v not in TESTS:
                        raise AnalysisError("test must be limma (moderated), welch or student")
                elif k == "de_type":
                    v = str(v).lower()
                    if v not in DE_TYPES:
                        raise AnalysisError("de_type must be control, all or others")
                elif k == "normalize":
                    v = str(v).lower()
                    v = "median" if v in ("md", "median") else v
                    if v not in fpa.NORMALIZATION_METHODS:
                        raise AnalysisError("normalize must be auto, median, gn, ratio or none")
                elif k == "imputation":
                    v = str(v).lower()
                    v = {"man": "perseus", "perseus-type": "perseus"}.get(v, v)
                    if v not in fpa.IMPUTATION_METHODS:
                        raise AnalysisError("imputation must be one of " + ", ".join(fpa.IMPUTATION_METHODS))
                elif k == "comparisons":
                    v = [parse_comparison(c) for c in (v if isinstance(v, list) else [v])]
                elif k in ("control_keywords", "competition_keywords", "competition_keywords_weak"):
                    v = tuple(_list(v))
                elif k == "roles":
                    v = {**s.roles, **_roles(v)}  # an experiment's roles add to / override the lab's
                elif k == "small_group_min_valid":
                    v = str(v).strip().lower()
                    if v not in SMALL_GROUP_RULES:
                        raise AnalysisError("small_group_min_valid must be half (the smaller group of an unbalanced "
                                            "comparison needs half its samples measured) or same (min_valid)")
                elif k in ("exclude_samples",):
                    v = _list(v)
                elif k == "enrichment_libraries":
                    v = _list(v)
                    bad = [x for x in v if x not in LIBRARIES]
                    if bad:
                        raise AnalysisError(f"unknown enrichment library {bad[0]!r} (known: {', '.join(LIBRARIES)})")
                elif k == "sample_conditions":
                    if not isinstance(v, dict):
                        raise AnalysisError("sample_conditions must map sample -> condition")
                    v = {str(a): str(b) for a, b in v.items()}
                elif k in ("control", "enrichment_gmt", "site_annotation"):
                    v = str(v)
                elif k == "liganded_direction":
                    v = str(v).strip().lower()
                    v = {"hl": "high", "h/l": "high", "heavy/light": "high", "lh": "low", "l/h": "low",
                         "light/heavy": "low"}.get(v.replace(" ", ""), v)
                    if v not in ("high", "low"):
                        raise AnalysisError("liganded_direction must be high (R = heavy / light) or low "
                                            "(R = light / heavy)")
                elif k == "sdrf":
                    v = {**s.sdrf, **_sdrf_meta(v)}  # a later layer adds to / overrides the lab's values
                elif k in ("sdrf_factor", "tmt_reference"):
                    v = _list(v)
                elif k == "irs":
                    from ionomos.downstream.plex import IRS_MODES

                    v = str(v).strip().lower()
                    v = {"off": "none", "false": "none", "no": "none", "on": "auto", "true": "auto"}.get(v, v)
                    if v not in IRS_MODES:
                        raise AnalysisError("irs must be auto, reference, sum or none")

                elif k == "doses":
                    if not isinstance(v, dict):
                        raise AnalysisError("doses must map a condition to its dose, e.g. {DMSO: 0, Cmpd_1: 10 nM}")
                    v = {str(a): (b if isinstance(b, (int, float)) and not isinstance(b, bool) else str(b))
                         for a, b in v.items()}
                elif k == "dose_unit":
                    v = _dose_unit(v)
                elif k == "times":
                    if not isinstance(v, dict):
                        raise AnalysisError("times must map a condition to its time, e.g. {Drug_a: 0, Drug_b: 4 h}")
                    v = {str(a): (b if isinstance(b, (int, float)) and not isinstance(b, bool) else str(b))
                         for a, b in v.items()}
                elif k == "time_unit":
                    v = _time_unit(v)
                elif k == "time_model":
                    v = str(v).strip().lower()
                    v = {"splines": "spline", "ns": "spline", "factors": "factor"}.get(v, v)
                    if v not in ("auto", "factor", "spline"):
                        raise AnalysisError("time_model must be auto (time as a factor up to 6 time points, a spline "
                                            "from 7), factor or spline")
                elif k == "time_spline_df":
                    v = _spline_df(v)
                elif k == "block":
                    v = _block(v)
                    s.block_from = ""  # an experiment's block replaces the lab's block_from, and vice versa
                elif k == "block_from":
                    v = _block_from(v)
                    s.block = ""
                elif k == "covariates":
                    v = _covariates(v)
                elif k == "export":
                    v = {**s.export, **_export_style(v)}  # a later layer adds to / overrides the lab's style
                elif k == "ratio_centre":
                    v = str(v).strip().lower()
                    v = {"off": "none", "false": "none", "no": "none", "centre": "auto", "center": "auto"}.get(v, v)
                    if v not in fpa.RATIO_CENTRE_METHODS:
                        raise AnalysisError("ratio_centre must be none (the ratios as measured), median (each replicate "
                                            "centred on its median site) or auto (centred on the stable sites, only "
                                            "when a replicate is clearly off)")
                elif k == "protein_correction":
                    v = _protein_correction(v, s.protein_correction)
                elif k == "variance_prior":
                    v = str(v).strip().lower()
                    v = "limma" if v == "ebayes" else v
                    if v not in VARIANCE_PRIORS:
                        raise AnalysisError("variance_prior must be limma (one prior for every feature) or deqms "
                                            "(a prior that depends on the peptide count)")
            except (TypeError, ValueError) as exc:
                if isinstance(exc, AnalysisError):
                    raise
                raise AnalysisError(f"analysis.{k}: {exc}") from exc
            setattr(s, k, v)
    if not 0 < s.alpha < 1:
        raise AnalysisError("analysis.alpha must be between 0 and 1")
    if s.log2fc < 0:
        raise AnalysisError("analysis.log2fc must be >= 0")
    if s.min_valid < 2:
        raise AnalysisError("analysis.min_valid must be >= 2")
    for k in ("filter_global_pct", "filter_condition_pct"):
        if not 0 <= getattr(s, k) <= 100:
            raise AnalysisError(f"analysis.{k} must be between 0 and 100")
    if not 0 < s.dose_alpha < 1:
        raise AnalysisError("analysis.dose_alpha must be between 0 and 1")
    if s.dose_fc_lim < 0:
        raise AnalysisError("analysis.dose_fc_lim must be >= 0")
    if s.dose_min_doses < 3:
        raise AnalysisError("analysis.dose_min_doses must be >= 3 (a curve has 4 parameters)")
    if s.liganded_ratio <= 1:
        raise AnalysisError("analysis.liganded_ratio must be above 1 (a competition ratio, e.g. 4)")
    if s.liganded_min_replicates < 1:
        raise AnalysisError("analysis.liganded_min_replicates must be at least 1")
    if s.time_min_points < 3:
        raise AnalysisError("analysis.time_min_points must be >= 3 (two time points are an ordinary comparison)")
    _check_doses(s)
    _check_times(s)
    return s

PROTEIN_MATCH = ("gene", "protein")

def _protein_correction(v, before: dict) -> dict:
    """analysis.protein_correction (proteincorr.py, D70): {proteome: path, match: gene | protein, conditions:
    {site condition: proteome comparison}}. A later layer's keys win; proteome: "" (or false) switches it off."""
    if v is False or (isinstance(v, str) and v.strip().lower() in ("", "none", "off", "false", "no")):
        return {}
    if isinstance(v, str):
        v = {"proteome": v}
    if not isinstance(v, dict):
        raise AnalysisError("protein_correction must be {proteome: <an analysed Ionomos experiment folder or a protein "
                            "table>, match: gene | protein, conditions: {site condition: proteome comparison}}")
    unknown = [k for k in v if k not in ("proteome", "match", "conditions")]
    if unknown:
        raise AnalysisError(f"protein_correction: unknown key {unknown[0]!r} (known: proteome, match, conditions)")
    out = dict(before)
    if "proteome" in v:
        p = "" if v["proteome"] is None else str(v["proteome"]).strip()
        if not p or p.lower() in ("none", "off", "false"):
            return {}
        out["proteome"] = p
    if v.get("match") not in (None, ""):
        mt = str(v["match"]).strip().lower()
        mt = {"genes": "gene", "proteins": "protein", "accession": "protein", "uniprot": "protein"}.get(mt, mt)
        if mt not in PROTEIN_MATCH:
            raise AnalysisError("protein_correction.match must be gene (gene names) or protein (UniProt accessions)")
        out["match"] = mt
    if v.get("conditions") not in (None, "", {}):
        if not isinstance(v["conditions"], dict):
            raise AnalysisError("protein_correction.conditions must map a site condition to a proteome comparison, "
                                "e.g. {EJQ_2_027: Cmpd vs DMSO}")
        out["conditions"] = {**out.get("conditions", {}),
                             **{str(a).strip(): str(b).strip() for a, b in v["conditions"].items() if str(b).strip()}}
    if out and not out.get("proteome"):
        raise AnalysisError("protein_correction needs proteome: the folder of an analysed Ionomos experiment (the "
                            "unenriched proteome) or a protein table")
    out.setdefault("match", "gene")
    return out

def _export_style(v) -> dict:
    from ionomos.downstream.charts import StyleError, style_layer

    try:
        return style_layer(v)
    except StyleError as exc:
        raise AnalysisError(f"analysis.{exc}") from exc

def _dose_unit(v) -> str:
    from ionomos.downstream.doseresponse import DoseError, normalize_unit

    try:
        return normalize_unit(str(v))
    except DoseError as exc:
        raise AnalysisError(f"analysis.dose_unit: {exc}") from exc

def _spline_df(v) -> int:
    """analysis.time_spline_df: a whole number >= 1, or auto (0: 4, or fewer for a short series)."""
    if isinstance(v, str) and v.strip().lower() == "auto":
        return 0
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = math.nan
    if isinstance(v, bool) or not math.isfinite(n) or n != int(n) or n < 1:
        raise AnalysisError(f"time_spline_df must be a whole number of at least 1, or auto (got {v!r})")
    return int(n)

def _time_unit(v) -> str:
    from ionomos.downstream.timecourse import TimeError, normalize_unit

    try:
        return normalize_unit(str(v))
    except TimeError as exc:
        raise AnalysisError(f"analysis.time_unit: {exc}") from exc

def _check_times(s: Settings) -> None:
    from ionomos.downstream.timecourse import TimeError, parse_time

    for c, v in s.times.items():
        try:
            parse_time(v, s.time_unit)
        except TimeError as exc:
            raise AnalysisError(f"analysis.times {c}: {exc}") from exc

def _check_doses(s: Settings) -> None:
    """Every analysis.doses value must read as a dose (after the layers, so dose_unit can come in any order)."""
    from ionomos.downstream.doseresponse import DoseError, parse_dose

    for c, v in s.doses.items():
        try:
            parse_dose(v, s.dose_unit)
        except DoseError as exc:
            raise AnalysisError(f"analysis.doses {c}: {exc}") from exc

def settings_lenient(*layers: dict | None) -> tuple[Settings, list[str]]:
    """settings_from for a run that must go ahead: a bad value drops only that key (with a note)
    instead of every setting. Validation of config.yaml / experiment.yaml stays strict (settings_from)."""
    kept: list[dict] = []
    notes: list[str] = []
    for layer in layers:
        good: dict = {}
        for k, v in sorted((layer or {}).items(), key=lambda kv: kv[0] in ("doses", "times")):  # read dose_unit / time_unit
            try:
                settings_from(*kept, {**good, k: v})
            except AnalysisError as exc:
                notes.append(f"analysis setting {k!r} ignored ({exc}); the default is used")
                continue
            good[k] = v
        kept.append(good)
    return settings_from(*kept), notes

def as_dict(s: Settings) -> dict:
    out = {}
    for f in fields(Settings):
        v = getattr(s, f.name)
        if f.name == "comparisons":
            v = [f"{a} vs {b}" for a, b in v]
        elif isinstance(v, tuple):
            v = list(v)
        out[f.name] = v
    return out

def find_control(conditions: list[str], s: Settings) -> str | None:
    if s.control:
        for c in conditions:
            if c.lower() == s.control.lower():
                return c
        raise AnalysisError(f"control {s.control!r} is not one of the conditions: {', '.join(conditions)}")
    given = {k.lower(): v for k, v in s.roles.items()}  # analysis.roles names the control, or says what isn't one
    for c in conditions:
        if given.get(c.lower()) == "control":
            return c
    for kw in s.control_keywords:
        for c in conditions:
            if c.lower() in given:
                continue
            tokens = re.split(r"[_\-\s.]+", c.lower())
            if kw.lower() in tokens or c.lower() == kw.lower():
                return c
    return None

def choose_comparisons(m: QuantMatrix, s: Settings) -> tuple[list[tuple[str, str | None]], list[str]]:
    """[(treatment, control)]; control None = ratio data vs 0, "others" = one-vs-rest. Plus notes."""
    notes: list[str] = []
    conds = m.conditions
    if m.kind == "ratio":
        return [(c, None) for c in conds], notes
    if s.comparisons:
        for t, c in s.comparisons:
            for x in (t, c):
                if x not in conds:
                    raise AnalysisError(f"comparison uses {x!r}, which is not a condition here "
                                        f"(conditions: {', '.join(conds)})")
        return list(s.comparisons), notes
    if len(conds) < 2:
        notes.append(f"only one condition ({conds[0] if conds else '-'}): quality control only, no comparison")
        return [], notes
    if s.de_type == "others":
        return [(c, "others") for c in conds], notes
    ctrl = find_control(conds, s)
    if s.de_type == "all":
        return fpa.all_pairs(conds, ctrl), notes
    from ionomos.downstream import roles

    design = roles.plan(m, s)  # a competition experiment: the comparisons follow the roles (D61)
    if design.active:
        return list(design.comparisons), notes + design.notes
    if ctrl is None:
        ctrl = sorted(conds)[0]
        notes.append(f"no control condition recognised; using {ctrl!r} as control (alphabetically first). "
                     f"Choose it on the Analysis tab or set analysis.control in experiment.yaml.")
    return [(c, ctrl) for c in conds if c != ctrl], notes

@dataclass
class DiffResult:
    name: str
    treatment: str
    control: str | None
    kind: str
    rows: list[dict]              # sorted by p; each has "index" = row in the processed matrix
    settings: Settings
    y_threshold_p: float | None   # the p-value where significance starts (the volcano's horizontal line)
    test_used: str = ""
    prior: tuple[float, float] = (math.nan, math.nan)  # limma: (prior df, prior variance)
    confidence: str = ""          # "" normal | "low": a group of one, p borrowed | "none": fold change only
    confidence_note: str = ""
    groups: tuple = ()            # samples on each side: (treatment, control); () for a results table
    role: str = ""                # roles.KINDS: enrichment | competition | remaining ("" = no competition design)
    relaxed: int = 0              # features tested with fewer than min_valid values in the smaller group
    correction: dict = field(default_factory=dict)   # a site comparison corrected for protein abundance (D70)

    @property
    def up(self) -> int:
        return sum(1 for r in self.rows if r["significant"] == "up")

    @property
    def down(self) -> int:
        return sum(1 for r in self.rows if r["significant"] == "down")

    @property
    def tested(self) -> int:
        return sum(1 for r in self.rows if r["pvalue"] is not None and not math.isnan(r["pvalue"]))

    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.name).strip("_") or "comparison"

def comparison_name(treatment: str, control: str | None) -> str:
    if control is None:
        return f"{treatment} (log2 H/L vs 0)"
    if control == "others":
        return f"{treatment} vs others"
    return f"{treatment} vs {control}"

def _nan(v) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

def _classic(p: fpa.Processed, a: str, b: str, s: Settings) -> fpa.ContrastResult:
    """Welch / Student t-test per feature (the pre-0.6 tests, kept as options)."""
    m = p.m
    ia = [j for j, x in enumerate(m.samples) if m.condition[x] == a]
    ib = [j for j, x in enumerate(m.samples) if (m.condition[x] != a if b == "others" else m.condition[x] == b)]
    diff, t, pv, na, nb, ma, mb, dfs = [], [], [], [], [], [], [], []
    for row in m.values:
        xa = [row[j] for j in ia if row[j] is not None]
        xb = [row[j] for j in ib if row[j] is not None]
        na.append(len(xa))
        nb.append(len(xb))
        ma.append(stats.mean(xa))
        mb.append(stats.mean(xb))
        if len(xa) >= s.min_valid and len(xb) >= s.min_valid:
            tv, dfv, pp = (stats.welch_t if s.test == "welch" else stats.student_t)(xa, xb)
            diff.append(stats.mean(xa) - stats.mean(xb))
        else:
            tv, dfv, pp = math.nan, math.nan, math.nan
            diff.append(stats.mean(xa) - stats.mean(xb) if xa and xb else math.nan)
        t.append(tv)
        pv.append(pp)
        dfs.append(dfv)
    nan = [math.nan] * len(diff)
    return fpa.ContrastResult(a, b, diff, nan, list(nan), t, pv, stats.bh_adjust(pv), na, nb, ma, mb,
                              se=_se_from_t(diff, t), df=dfs)

def _se_from_t(diff: list[float], t: list[float]) -> list[float]:
    """A t-test's standard error, diff / t (nan where t is 0, infinite or missing)."""
    return [abs(d / tv) if d == d and tv == tv and tv not in (0.0, math.inf, -math.inf) else math.nan
            for d, tv in zip(diff, t, strict=True)]

@dataclass
class Model:
    """The linear model the comparisons used (design.py): for Methods, analysis.json and the doctor."""
    design: object = None            # design.Design, or None for ~0 + condition
    problem: str = ""                # why an asked-for design wasn't used ("" = none asked, or used)
    notes: list[str] = field(default_factory=list)
    prior: dict = field(default_factory=dict)   # the variance prior: DEqMS used or not, d0, ...
    plex_df: bool = False            # residual df reduced by the plexes - 1 (IRS on the plex means, plex.df_spent)

    @property
    def formula(self) -> str:
        return self.design.formula if self.design is not None else "~0 + condition"

    def as_dict(self, s: Settings) -> dict:
        out = {"formula": self.formula, "blocks_or_covariates": self.design.describe() if self.design else "",
               "variance_prior": self.prior.get("variance_prior", "limma" if s.test == "limma" else "")}
        if self.design is not None:
            out.update(self.design.as_dict())
        if self.problem:
            out["not_used"] = self.problem
        if self.prior:
            out["prior"] = {k: v for k, v in self.prior.items() if k != "variance_prior"}
        if self.plex_df:
            out["plex_df"] = "residual df reduced by the plexes - 1 per feature (IRS on the plex means)"
        return out

def make_model(m: QuantMatrix, s: Settings, comps: list[tuple[str, str | None]]) -> Model:
    """The design asked for in the settings, checked against these samples. A design that can't be used
    leaves Model.problem set (the doctor raises DESIGN_NOT_USED) and the plain model is used. After IRS on the
    plex means, limma's residual df are reduced by the plexes - 1 unless the design holds the plexes (D71)."""
    from ionomos.downstream import plex

    out = _make_model(m, s, comps)
    if s.test == "limma" and m.kind == "intensity" and plex.sum_scaled(m) and \
            not (out.design is not None and plex.holds_plexes(out.design, m)):
        out.plex_df = True
        out.notes.append("IRS on the plex means: each plex's level was estimated from the channels that are then "
                         "tested, so limma's residual df are reduced by the plexes - 1 per protein")
    return out

def _make_model(m: QuantMatrix, s: Settings, comps: list[tuple[str, str | None]]) -> Model:
    from ionomos.downstream import design

    out = Model()
    if s.variance_prior == "deqms" and (s.test != "limma" or m.kind != "intensity"):
        out.notes.append("variance_prior: deqms applies to limma comparisons between conditions; not used here")
    if not s.has_design:
        return out
    if s.test != "limma":
        out.notes.append(f"block / covariates apply to the limma model; the {TESTS[s.test]} ignores them")
        return out
    if m.kind != "intensity":
        out.notes.append("block / covariates apply to comparisons between conditions; ratios tested against 0 "
                         "(isoDTB) use the one-sample model")
        return out
    for what, spec in (("block", s.block if isinstance(s.block, dict) else {}),
                       *((f"covariate {k!r}", v) for k, v in s.covariates.items())):
        extra = [x for x in spec if x not in m.samples]
        if extra:
            out.notes.append(f"{what}: not a sample here, ignored: " + ", ".join(extra[:5]))
    try:
        d = design.build(m, s.block, s.block_from, s.covariates)
        if any(b == "others" for _, b in comps):
            k = len(d.conditions)
            for ci, c in enumerate(d.conditions):
                try:
                    design.cov_unscaled([[row[ci], 1.0 - row[ci], *row[k:]] for row in d.x])
                except design.DesignError:
                    raise design.DesignError(f"{c} vs others: the blocks or covariates are confounded with {c}") \
                        from None
        out.design = d
    except design.DesignError as exc:
        out.problem = str(exc)
        out.notes.append(f"the experimental design wasn't used: {exc}; the comparisons use ~0 + condition")
    return out

def group_needs(m: QuantMatrix, pairs: list[tuple[str, str]], s: Settings, min_valid: int) -> dict:
    """{(treatment, control): (values needed in the treatment, in the control)} for a limma comparison.
    Every group needs min_valid measured values. With small_group_min_valid: half, the smaller group of an
    unbalanced comparison needs only half its samples (never fewer than 1), so a feature is not left untested
    because one of two control replicates is missing while the larger group is complete: limma's residual
    variance comes from every group, and the larger group still has to reach min_valid."""
    out = {}
    for a, b in pairs:
        na, nb = len(m.samples_of(a)), len(m.samples_of(b))
        need = [min_valid, min_valid]
        if min_valid and s.small_group_min_valid == "half" and na != nb:
            k = 0 if na < nb else 1
            need[k] = min(min_valid, max(1, math.ceil(min(na, nb) / 2)))
        out[(a, b)] = (need[0], need[1])
    return out

def _counts(m: QuantMatrix, s: Settings) -> list[int | None] | None:
    return [f.peptides for f in m.features] if s.variance_prior == "deqms" else None

def run_contrasts(p: fpa.Processed, comps: list[tuple[str, str | None]], s: Settings,
                  low: frozenset | set = frozenset(), model: Model | None = None) -> list[fpa.ContrastResult]:
    """low: comparisons with a group smaller than min_valid. They are still tested where the model has
    residual df (limma borrows it from every condition; Welch becomes a pooled t-test), labelled low confidence.
    model: the design (make_model); its .prior is filled with what the variance prior did."""
    from ionomos.downstream import design as dz
    from ionomos.downstream import plex

    m = p.m
    pairs = [(a, b) for a, b in comps if b not in (None, "others")]
    out: dict[tuple, fpa.ContrastResult] = {}
    des = model.design if model is not None else None
    counts = _counts(m, s)
    if s.test == "limma":
        spent = plex.df_spent(p, des)  # IRS on the plex means (D71)
        mv = 0 if p.imputation != "none" else s.min_valid
        sq = dz.squeezer(counts, s.variance_prior) if counts is not None else None
        for group, gmv in (([c for c in pairs if c not in low], mv), ([c for c in pairs if c in low], 0)):
            if group:  # eBayes is fitted on every condition's residuals, so splitting contrasts changes nothing else
                needs = group_needs(m, group, s, gmv)
                if des is not None:
                    res, info = dz.limma_design(m.values, des, group, gmv, counts, s.variance_prior, needs, spent)
                else:
                    res = fpa.limma_contrasts(m.values, m.samples, m.condition, group, min_valid=gmv, squeeze=sq,
                                              needs=needs, df_spent=spent)
                    info = sq.info if sq else {}
                if model is not None and info:
                    model.prior = info
                for r in res:
                    out[(r.treatment, r.control)] = r
        if any(b == "others" for _, b in comps):
            if des is not None:
                res, info = dz.limma_design_others(m.values, des, counts, s.variance_prior, spent)
            else:
                res = fpa.limma_others(m.values, m.samples, m.condition, squeeze=sq, df_spent=spent)
                info = sq.info if sq else {}
            if model is not None and info and not model.prior:
                model.prior = info
            for r in res:
                out[(r.treatment, "others")] = r
    else:
        for a, b in comps:
            if b is not None:
                out[(a, b)] = _classic(p, a, b, replace(s, min_valid=1, test="student") if (a, b) in low else s)
    for a, b in comps:
        if b is None:
            cols = [j for j, x in enumerate(m.samples) if m.condition[x] == a]
            out[(a, None)] = (fpa.limma_one_sample(m.values, cols, a, s.min_valid) if s.test == "limma"
                              else _one_sample_classic(m, cols, a, s))
    return [out[(a, b)] for a, b in comps]

def f_test(p: fpa.Processed, comps: list[tuple[str, str | None]], s: Settings, model: Model | None = None):
    """The moderated F ("any change between the conditions") for 3+ conditions with limma, on the same
    model and variance prior as the comparisons. None when it doesn't apply."""
    from ionomos.downstream import design as dz

    m = p.m
    conds = m.conditions
    if s.test != "limma" or m.kind != "intensity" or len(conds) < 3:
        return None
    ref = next((b for _, b in comps if b not in (None, "others")), None)
    if ref not in conds:
        ref = find_control(conds, replace(s, control=None))
    if ref not in conds:
        ref = conds[0]
    from ionomos.downstream import plex

    des = model.design if model is not None and model.design is not None else dz.plain(m)
    mv = 0 if p.imputation != "none" else s.min_valid
    return dz.f_test(m.values, des, ref, mv, _counts(m, s), s.variance_prior, plex.df_spent(p, des))

def _one_sample_classic(m: QuantMatrix, cols: list[int], name: str, s: Settings) -> fpa.ContrastResult:
    diff, t, pv, n_, dfs = [], [], [], [], []
    for row in m.values:
        xs = [row[j] for j in cols if row[j] is not None]
        n_.append(len(xs))
        if len(xs) >= s.min_valid:
            tv, dfv, pp = stats.one_sample_t(xs, 0.0)
        else:
            tv, dfv, pp = math.nan, math.nan, math.nan
        diff.append(stats.mean(xs) if xs else math.nan)
        t.append(tv)
        pv.append(pp)
        dfs.append(dfv)
    nan = [math.nan] * len(diff)
    return fpa.ContrastResult(name, "", diff, nan, list(nan), t, pv, stats.bh_adjust(pv), n_, [0] * len(diff),
                              list(diff), list(nan), se=_se_from_t(diff, t), df=dfs)

def to_diff(p: fpa.Processed, r: fpa.ContrastResult, control: str | None, s: Settings) -> DiffResult:
    """A ContrastResult as table rows with add_rejections() significance."""
    m = p.m
    ia = [j for j, x in enumerate(m.samples) if m.condition[x] == r.treatment]
    if control == "others":
        ib = [j for j, x in enumerate(m.samples) if m.condition[x] != r.treatment]
    elif control is None:
        ib = []
    else:
        ib = [j for j, x in enumerate(m.samples) if m.condition[x] == control]
    rows = []
    thr_p = None
    se = r.se if r.se is not None else [math.nan] * len(m.features)
    dfs = r.df if r.df is not None else [math.nan] * len(m.features)
    for i, f in enumerate(m.features):
        pv, qv, fc = _nan(r.p[i]), _nan(r.q[i]), _nan(r.diff[i])
        score = qv if s.use_adjusted else pv
        sig = fpa.significant(fc if fc is not None else math.nan, score, s.alpha, s.log2fc)
        if score is not None and score <= s.alpha and pv is not None:
            thr_p = pv if thr_p is None else max(thr_p, pv)
        mask = p.imputed[i]
        rows.append({"index": i, "id": f.id, "label": f.label, "description": f.description, "log2fc": fc,
                     "ci_low": _nan(r.ci_low[i]), "ci_high": _nan(r.ci_high[i]), "pvalue": pv, "qvalue": qv,
                     "significant": sig, "t": _nan(r.t[i]),
                     "n_treatment": sum(1 for j in ia if p.measured[i][j] is not None),
                     "n_control": sum(1 for j in ib if p.measured[i][j] is not None) if control is not None else None,
                     "imputed": sum(1 for j in ia + ib if mask[j]),
                     "mean_treatment": _nan(r.mean_treatment[i]),
                     "mean_control": _nan(r.mean_control[i]) if control is not None else None,
                     "se": _nan(se[i]) if pv is not None else None,
                     "df": _nan(dfs[i]) if pv is not None else None})   # df inf (limma's pooled prior) -> "Inf"
    if not s.use_adjusted:
        thr_p = s.alpha
    rows.sort(key=lambda x: (x["pvalue"] is None, x["pvalue"] if x["pvalue"] is not None else 1.0))
    d = DiffResult(comparison_name(r.treatment, control), r.treatment, control, m.kind, rows, s, thr_p)
    d.test_used = s.test
    d.prior = r.prior
    d.groups = (len(ia), len(ib)) if control is not None else (len(ia),)
    if control not in (None, "others") and p.imputation == "none":
        d.relaxed = sum(1 for x in rows if x["pvalue"] is not None
                        and min(x["n_treatment"], x["n_control"]) < s.min_valid)
    return d

def _split_name(name: str) -> tuple[str, str]:
    """'DrugA_vs_DMSO' / 'DrugA vs DMSO' / 'DrugA-DMSO' -> ('DrugA', 'DMSO'); otherwise (name, '')."""
    if name.count("_") == 1 and not re.search(r"\svs\.?\s", name):  # Perseus: DrugA_DMSO
        a, b = name.split("_")
        if a and b and not b.isdigit():
            return a, b
    for sep in (r"\s*[_ .-]vs\.?[_ .-]\s*", r"\s*[_ ]over[_ ]\s*", r" - ", r"(?<=\w)-(?=\w)"):
        parts = re.split(sep, name, maxsplit=1, flags=re.I)
        if len(parts) == 2 and all(p.strip() for p in parts):
            return parts[0].strip(), parts[1].strip()
    return name, ""

def precomputed_diffs(m: QuantMatrix, s: Settings) -> list[DiffResult]:
    """Results tables (anytable.py): fold change and p (and q, or BH from p) as given, with this lab's cut-offs."""
    out = []
    for c in m.meta.get("precomputed") or []:
        q = c["q"] if c["q"] is not None else stats.bh_adjust([math.nan if v is None else v for v in c["p"]])
        rows, thr_p = [], None
        for i, f in enumerate(m.features):
            fc, pv, qv = c["fc"][i], c["p"][i], _nan(q[i])
            score = qv if s.use_adjusted else pv
            sig = fpa.significant(fc if fc is not None else math.nan, score, s.alpha, s.log2fc)
            if score is not None and score <= s.alpha and pv is not None:
                thr_p = pv if thr_p is None else max(thr_p, pv)
            rows.append({"index": i, "id": f.id, "label": f.label, "description": f.description, "log2fc": fc,
                         "ci_low": None, "ci_high": None, "pvalue": pv, "qvalue": qv, "significant": sig, "t": None,
                         "n_treatment": None, "n_control": None, "imputed": 0, "mean_treatment": None,
                         "mean_control": None})
        if not s.use_adjusted:
            thr_p = s.alpha
        rows.sort(key=lambda x: (x["pvalue"] is None, x["pvalue"] if x["pvalue"] is not None else 1.0))
        treatment, control = _split_name(c["name"])
        d = DiffResult(c["name"] or "comparison", treatment, control or "reference", "given", rows, s, thr_p)
        d.test_used = "as given"
        if not any(r["pvalue"] is not None for r in rows):
            fold_change_only(d, f"The table has no usable p-values for {d.name}")
        out.append(d)
    return out

def fold_change_only(d: DiffResult, reason: str) -> None:
    """No replicates anywhere to estimate variance: rank by fold change and call candidates on |log2FC| alone.
    No p-values are invented; every output labels these as fold change only."""
    lfc = d.settings.log2fc or 1.0
    for r in d.rows:
        fc = r["log2fc"]
        if fc is None and d.control is None:  # ratio vs 0 with one replicate: the value itself
            fc = r["log2fc"] = r["mean_treatment"]
        r["significant"] = "" if fc is None or abs(fc) < lfc else ("up" if fc > 0 else "down")
    d.rows.sort(key=lambda x: (x["log2fc"] is None, -abs(x["log2fc"] or 0.0)))
    d.y_threshold_p = None
    d.confidence = "none"
    d.confidence_note = (f"{reason} — no statistics are possible, so this is fold change only: "
                         f"candidates are |log2FC| ≥ {lfc:g}, with no p-values. Treat them as leads to confirm.")

DIFF_COLUMNS = ["id", "label", "description", "log2fc", "ci_low", "ci_high", "pvalue", "qvalue", "significant", "t",
                "n_treatment", "n_control", "imputed", "mean_treatment", "mean_control", "se", "df"]
