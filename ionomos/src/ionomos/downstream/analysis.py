"""
Settings, comparisons and differential statistics on a QuantMatrix.

The statistics are FragPipe-Analyst's (downstream/fpa.py): filter -> normalise ->
impute -> limma -> add_rejections. Settings come from config.yaml `analysis:`
(lab defaults, edited on the app's Analysis tab) overridden by the experiment's
experiment.yaml `analysis:` block:

    analysis:
      de_type: control            # control (each condition vs the control) | all (every pair) | others (each vs the rest)
      control: DMSO               # default: first condition matching control_keywords
      comparisons: ["Drug vs DMSO"]   # explicit list; wins over de_type
      log2fc: 1.0                 # |log2 fold change| threshold (FragPipe-Analyst "lfc")
      alpha: 0.05                 # significance threshold (FragPipe-Analyst "p")
      use_adjusted: true          # alpha applies to BH-adjusted p (false: raw p)
      test: limma                 # limma (moderated t, default) | welch | student
      remove_contaminants: true
      filter_global_pct: 0        # min % of all samples with a value
      filter_condition_pct: 50    # min % with a value in at least one condition
      normalize: median           # median (default) | gn | none
      imputation: auto            # auto | none | perseus | min | zero | mindet | minprob | knn
      min_valid: 2                # measured values per group needed when nothing is imputed
      exclude_samples: [DMSO_3]   # leave samples out
      sample_conditions: {Drug_4: DMSO}   # give a sample another condition
      enrichment: true
      enrichment_libraries: [Hallmark, GO Biological Process, Reactome]
      top_labels: 15              # names drawn on each static volcano
      sdrf:                       # sample metadata for results/sdrf.tsv (sdrf.py): lab-wide in config.yaml,
        instrument: Orbitrap Eclipse   # per experiment in experiment.yaml (keys merge; the experiment's win)
        organism: homo sapiens    # default: the FASTA's OS=; also organism_part, cell_type, disease, cleavage_agent
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields, replace

from ionomos.downstream import fpa, stats
from ionomos.downstream.enrich import DEFAULT_LIBRARIES, LIBRARIES
from ionomos.downstream.quant import QuantMatrix

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
    min_valid: int = 2
    remove_contaminants: bool = True
    filter_global_pct: float = 0.0
    filter_condition_pct: float = 50.0
    normalize: str = "median"
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


def settings_from(*layers: dict | None) -> Settings:
    """Later layers win. Unknown keys are an error (typos shouldn't silently do nothing)."""
    s = Settings()
    names = {f.name for f in fields(Settings)}
    for layer in layers:
        for k, v in (layer or {}).items():
            if k == "enabled":
                continue
            if k not in names:
                raise AnalysisError(f"unknown analysis setting {k!r} (known: {', '.join(sorted(names))})")
            if v is None or (v == "" and k not in ("enrichment_gmt",)):
                continue
            try:
                if k in ("log2fc", "alpha", "impute_shift", "impute_scale", "filter_global_pct", "filter_condition_pct"):
                    v = float(v)
                elif k in ("min_valid", "top_labels", "seed", "pca_features", "heatmap_max"):
                    v = int(v)
                elif k in ("use_adjusted", "remove_contaminants", "enrichment"):
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
                        raise AnalysisError("normalize must be none, median or gn")
                elif k == "imputation":
                    v = str(v).lower()
                    v = {"man": "perseus", "perseus-type": "perseus"}.get(v, v)
                    if v not in fpa.IMPUTATION_METHODS:
                        raise AnalysisError("imputation must be one of " + ", ".join(fpa.IMPUTATION_METHODS))
                elif k == "comparisons":
                    v = [parse_comparison(c) for c in (v if isinstance(v, list) else [v])]
                elif k == "control_keywords":
                    v = tuple(_list(v))
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
                elif k in ("control", "enrichment_gmt"):
                    v = str(v)
                elif k == "sdrf":
                    v = {**s.sdrf, **_sdrf_meta(v)}  # a later layer adds to / overrides the lab's values
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
    return s


def settings_lenient(*layers: dict | None) -> tuple[Settings, list[str]]:
    """settings_from for a run that must go ahead: a bad value drops only that key (with a note)
    instead of every setting. Validation of config.yaml / experiment.yaml stays strict (settings_from)."""
    kept: list[dict] = []
    notes: list[str] = []
    for layer in layers:
        good: dict = {}
        for k, v in (layer or {}).items():
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
    for kw in s.control_keywords:
        for c in conditions:
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
    diff, t, pv, na, nb, ma, mb = [], [], [], [], [], [], []
    for row in m.values:
        xa = [row[j] for j in ia if row[j] is not None]
        xb = [row[j] for j in ib if row[j] is not None]
        na.append(len(xa))
        nb.append(len(xb))
        ma.append(stats.mean(xa))
        mb.append(stats.mean(xb))
        if len(xa) >= s.min_valid and len(xb) >= s.min_valid:
            tv, _, pp = (stats.welch_t if s.test == "welch" else stats.student_t)(xa, xb)
            diff.append(stats.mean(xa) - stats.mean(xb))
        else:
            tv, pp = math.nan, math.nan
            diff.append(stats.mean(xa) - stats.mean(xb) if xa and xb else math.nan)
        t.append(tv)
        pv.append(pp)
    nan = [math.nan] * len(diff)
    return fpa.ContrastResult(a, b, diff, nan, list(nan), t, pv, stats.bh_adjust(pv), na, nb, ma, mb)


def run_contrasts(p: fpa.Processed, comps: list[tuple[str, str | None]], s: Settings,
                  low: frozenset | set = frozenset()) -> list[fpa.ContrastResult]:
    """low: comparisons with a group smaller than min_valid. They are still tested where the model has
    residual df (limma borrows it from every condition; Welch becomes a pooled t-test), labelled low confidence."""
    m = p.m
    pairs = [(a, b) for a, b in comps if b not in (None, "others")]
    out: dict[tuple, fpa.ContrastResult] = {}
    if s.test == "limma":
        mv = 0 if p.imputation != "none" else s.min_valid
        for group, gmv in (([c for c in pairs if c not in low], mv), ([c for c in pairs if c in low], 0)):
            if group:  # eBayes is fitted on every condition's residuals, so splitting contrasts changes nothing else
                for r in fpa.limma_contrasts(m.values, m.samples, m.condition, group, min_valid=gmv):
                    out[(r.treatment, r.control)] = r
        if any(b == "others" for _, b in comps):
            for r in fpa.limma_others(m.values, m.samples, m.condition):
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


def _one_sample_classic(m: QuantMatrix, cols: list[int], name: str, s: Settings) -> fpa.ContrastResult:
    diff, t, pv, n_ = [], [], [], []
    for row in m.values:
        xs = [row[j] for j in cols if row[j] is not None]
        n_.append(len(xs))
        if len(xs) >= s.min_valid:
            tv, _, pp = stats.one_sample_t(xs, 0.0)
        else:
            tv, pp = math.nan, math.nan
        diff.append(stats.mean(xs) if xs else math.nan)
        t.append(tv)
        pv.append(pp)
    nan = [math.nan] * len(diff)
    return fpa.ContrastResult(name, "", diff, nan, list(nan), t, pv, stats.bh_adjust(pv), n_, [0] * len(diff),
                              list(diff), list(nan))


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
                     "mean_control": _nan(r.mean_control[i]) if control is not None else None})
    if not s.use_adjusted:
        thr_p = s.alpha
    rows.sort(key=lambda x: (x["pvalue"] is None, x["pvalue"] if x["pvalue"] is not None else 1.0))
    d = DiffResult(comparison_name(r.treatment, control), r.treatment, control, m.kind, rows, s, thr_p)
    d.test_used = s.test
    d.prior = r.prior
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
                "n_treatment", "n_control", "imputed", "mean_treatment", "mean_control"]
