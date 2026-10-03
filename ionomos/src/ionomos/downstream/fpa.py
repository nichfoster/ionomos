"""
The FragPipe-Analyst statistics pipeline, ported from FragPipeAnalystR.

FragPipeAnalystR (Nesvilab, https://github.com/Nesvilab/FragPipeAnalystR, GPL-3)
and its web app FragPipe-Analyst (MonashProteomics) process a FragPipe result
table in this order; so does this module, with their defaults:

    make_se_from_files    zeros -> missing, log2, contaminants removed      (quant.py loaders + remove_contaminants)
    global_filter         min % of samples with a value                    filter_missing(global_pct)
    filter_by_condition   min % with a value in at least one condition     filter_missing(condition_pct)
    MD / GN normalization median centring (+ MAD scaling)                  normalize("median" | "gn")
    (not FragPipe-Analyst) ratio normalisation on stable features, and     normalize("ratio" | "auto")
                           auto: median unless the composition check fails
    (not FragPipe-Analyst) site ratios (isoDTB) centred per replicate      centre_ratios("median" | "auto"), D70
    impute(fun = "man")   Perseus-type draws, set.seed(123)                impute("perseus")   (exact, R's RNG)
      "min" "zero" "MinDet" "MinProb" "knn"                                impute(...)          (see each)
    test_limma            ~0 + condition, per-contrast refit when values   limma_contrasts / limma_others
                          are missing, eBayes, topTable(confint = TRUE)
    add_rejections        p.adj <= alpha and |diff| >= lfc                 significant()

Checked against R (limma 3.68 + the functions above transcribed into base R)
in tests/test_fpa.py; the reference script is tests/golden/fpa/run_fpa_reference.R.

Edits for the lab (see docs/DECISIONS.md D24): the missing-value filter
defaults to 50 % in at least one condition (FragPipe-Analyst: 0 %), samples
are median-centred by default (FragPipe-Analyst: no normalisation; a no-op for
data that is already normalised, a rescue for loading differences), the
imputation choice follows the data ("auto": Perseus-type for label-free
intensities, none for TMT ratios and isoDTB ratios), and every imputed value is
remembered so the report can show which points were measured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ionomos.downstream import stats
from ionomos.downstream.quant import QuantMatrix
from ionomos.downstream.rrandom import RRandom

Matrix = list[list[float | None]]

IMPUTATION_METHODS = ("auto", "none", "perseus", "min", "zero", "mindet", "minprob", "knn")
NORMALIZATION_METHODS = ("none", "median", "gn", "ratio", "auto")
NORMALIZATION_LABELS = {"median": "median centring", "gn": "median centring + MAD scaling",
                        "ratio": "ratio normalisation (stable features)"}
# auto: median centring unless it and the ratio method disagree by more than this between conditions (log2)
COMPOSITION_LIMIT = 0.1
RATIO_MIN_FEATURES = 20   # complete features the ratio method needs
RATIO_KEEP = 0.75         # the share of them kept as "stable" (lowest spread across samples)
# site ratios (isoDTB, D70): ratio_centre none | median | auto
RATIO_CENTRE_METHODS = ("none", "median", "auto")
CENTRE_KEEP = 0.5         # the share of the sites, nearest the centre, that the stable centre is taken on
CENTRE_LIMIT = 0.05       # auto: a replicate's offset (log2) must exceed this ...
CENTRE_SE = 3.0           # ... and this many times its standard error before the condition is centred
CENTRE_MIN_SITES = 20     # sites a replicate needs before it is centred


@dataclass
class Processed:
    """A QuantMatrix after the processing steps, plus what happened on the way."""

    m: QuantMatrix                 # values used for statistics (imputed where imputation is on)
    measured: Matrix               # same rows/samples, before imputation (None = not measured)
    imputed: list[list[bool]]      # True where m.values was imputed
    steps: list[dict] = field(default_factory=list)
    before_filter: QuantMatrix | None = None   # after contaminant removal + sample choice, before filtering
    normalized_from: Matrix | None = None      # values before normalisation (distribution plot)
    imputation: str = "none"
    normalization: dict = field(default_factory=dict)   # normalize_info: method asked / used, the composition check;
                                                        # for site ratios also "ratio_centre" (centre_ratios, D70)

    @property
    def n_imputed(self) -> int:
        return sum(sum(r) for r in self.imputed)


def _copy(m: QuantMatrix, features=None, values=None, samples=None, condition=None) -> QuantMatrix:
    out = QuantMatrix(m.kind, m.level, list(features if features is not None else m.features),
                      list(samples if samples is not None else m.samples),
                      values if values is not None else [list(r) for r in m.values],
                      dict(condition if condition is not None else m.condition), m.source, list(m.notes))
    out.exp = m.exp
    out.replicate = {s: m.replicate[s] for s in out.samples if s in m.replicate}
    out.columns = {s: m.columns[s] for s in out.samples if s in m.columns}
    out.meta = dict(m.meta)
    return out


# ------------------------------------------------------------------ samples --


def choose_samples(m: QuantMatrix, exclude: list[str] | None = None,
                   conditions: dict[str, str] | None = None) -> tuple[QuantMatrix, list[str]]:
    """Leave samples out and/or give samples another condition (the Analysis tab's sample table)."""
    notes = []
    exclude = [x for x in (exclude or []) if x]
    unknown = [x for x in exclude if x not in m.samples]
    if unknown:
        notes.append("exclude_samples: not in this data, ignored: " + ", ".join(unknown))
    keep = [j for j, s in enumerate(m.samples) if s not in exclude]
    samples = [m.samples[j] for j in keep]
    cond = {s: m.condition[s] for s in samples}
    for s, c in (conditions or {}).items():
        if s in cond and str(c).strip():
            cond[s] = str(c).strip()
        elif s not in m.samples:
            notes.append(f"sample_conditions: {s!r} is not a sample here, ignored")
    values = [[row[j] for j in keep] for row in m.values]
    out = _copy(m, values=values, samples=samples, condition=cond)
    if len(samples) < len(m.samples):
        notes.append(f"left out {len(m.samples) - len(samples)} sample(s): " +
                     ", ".join(s for s in m.samples if s not in samples))
    return out, notes


def remove_contaminants(m: QuantMatrix) -> tuple[QuantMatrix, int]:
    """FragPipe-Analyst: rows whose protein contains "contam" (FragPipe's contam_ prefix) are dropped; MaxQuant's
    CON__ prefix too."""
    keep = [i for i, f in enumerate(m.features)  # FragPipe contam_, MaxQuant CON__
            if "contam" not in f.id and "contam_" not in f.label and not f.id.startswith("CON__")]
    if len(keep) == len(m.features):
        return m, 0
    return _copy(m, [m.features[i] for i in keep], [list(m.values[i]) for i in keep]), len(m.features) - len(keep)


# ---------------------------------------------------------------- filtering --


def filter_missing(m: QuantMatrix, global_pct: float = 0, condition_pct: float = 0) -> tuple[QuantMatrix, int]:
    """FragPipe-Analyst's global_filter + filter_by_condition (a step with 0 % is skipped, as there).

    global_pct:    keep rows with a value in at least this % of all samples
    condition_pct: keep rows with a value in at least this % of the samples of at least one condition
    """
    n = len(m.samples)
    groups = [[j for j, s in enumerate(m.samples) if m.condition[s] == c] for c in m.conditions]
    keep = []
    for i, row in enumerate(m.values):
        present = [v is not None for v in row]
        if global_pct and n and sum(present) / n < global_pct / 100 - 1e-12:
            continue
        if condition_pct and not any(g and sum(present[j] for j in g) / len(g) >= condition_pct / 100 - 1e-12
                                     for g in groups):
            continue
        keep.append(i)
    return _copy(m, [m.features[i] for i in keep], [list(m.values[i]) for i in keep]), len(m.values) - len(keep)


# ------------------------------------------------------------ normalisation --


def ratio_shifts(cols: list[list[float | None]]) -> tuple[list[float] | None, int]:
    """Per-sample loading (log2) from the features, not from the abundance distribution: the median, over
    stable features, of each value minus that feature's mean across the samples. (shifts centred on 0, number of
    features used); (None, n) when fewer than RATIO_MIN_FEATURES features are measured in every sample.

    Median centring compares the middle of each sample's abundance distribution, which is several log2 units
    wide: when a share of the features is enriched in some samples (a pulldown, a depletion), that middle moves
    and every unchanged feature is shifted the other way. A feature's own ratio to its mean is narrow (the
    replicate noise), so the median of the ratios hardly moves; features that vary most across the samples (the
    changed ones) are left out before it is taken, as edgeR's TMM trims them."""
    n = len(cols)
    rows = [i for i in range(len(cols[0]) if cols else 0) if all(c[i] is not None for c in cols)]
    if n < 2 or len(rows) < RATIO_MIN_FEATURES:
        return None, len(rows)
    diff = {i: [cols[j][i] - sum(cols[k][i] for k in range(n)) / n for j in range(n)] for i in rows}
    use = rows
    shifts = [0.0] * n
    for _ in range(3):  # shifts -> spread of what they leave -> the stable three quarters -> shifts again
        shifts = [stats.median([diff[i][j] for i in use]) for j in range(n)]
        spread = {i: sum((diff[i][j] - shifts[j]) ** 2 for j in range(n)) for i in rows}
        keep = max(RATIO_MIN_FEATURES, int(len(rows) * RATIO_KEEP))
        use = sorted(rows, key=lambda i: (spread[i], i))[:keep]
    shifts = [stats.median([diff[i][j] for i in use]) for j in range(n)]
    mean = sum(shifts) / n
    return [s - mean for s in shifts], len(use)


def normalize_info(m: QuantMatrix, method: str) -> tuple[QuantMatrix, dict]:
    """(normalised matrix, what was done). Methods:

      none     nothing
      median   FragPipeAnalystR MD_normalization: every sample's median moved to the median of the sample medians
               (FragPipeAnalystR centres on 0; the shift keeps the familiar log2-intensity scale and changes no
               fold change, test, PCA or imputation)
      gn       GN_normalization: median centring, then scaled to one MAD
      ratio    ratio_shifts(): robust when many features change in one direction
      auto     median, unless the composition check fails: the two methods' sample shifts, averaged per condition,
               disagree by more than COMPOSITION_LIMIT log2 between two conditions, and by more than 3 times
               the scatter of that disagreement among replicates. Then ratio is used.

    The info always carries the composition check when it could be made ("composition": the largest
    disagreement between two conditions, and which), so median / gn can be warned about."""
    info: dict = {"asked": method, "used": method}
    if method == "none" or m.kind != "intensity" or not m.samples:
        info["used"] = "none"
        return m, info
    cols = [[row[j] for row in m.values] for j in range(len(m.samples))]
    meds = [stats.median([v for v in c if v is not None]) for c in cols]
    good = [x for x in meds if not math.isnan(x)]
    if not good:
        info["used"] = "none"
        return m, info
    target = stats.median(good)
    rshift, n_used = ratio_shifts(cols) if all(not math.isnan(x) for x in meds) else (None, 0)
    if rshift is not None:
        mshift = [x - sum(meds) / len(meds) for x in meds]
        by: dict[str, list[float]] = {}
        for s, a, b in zip(m.samples, mshift, rshift, strict=True):
            by.setdefault(m.condition[s], []).append(a - b)
        mean = {c: sum(v) / len(v) for c, v in by.items()}
        hi, lo = max(mean, key=mean.get), min(mean, key=mean.get)
        shift = mean[hi] - mean[lo]
        # replicates of a condition share its composition, so their scatter is the check's own noise (a short
        # table with many missing values makes sample medians jump): the shift must also stand out from it
        df = sum(len(v) - 1 for v in by.values())
        var = sum((x - mean[c]) ** 2 for c, v in by.items() for x in v) / df if df > 0 else 0.0
        se = math.sqrt(var * (1 / len(by[hi]) + 1 / len(by[lo])))
        info["composition"] = {"shift": shift, "between": [hi, lo], "limit": COMPOSITION_LIMIT, "se": se,
                               "features": n_used, "exceeded": shift > COMPOSITION_LIMIT and shift > 3 * se}
    else:
        info["composition"] = {"shift": None, "features": n_used, "exceeded": False,
                               "reason": f"fewer than {RATIO_MIN_FEATURES} features are measured in every sample"}
    used = method
    if method == "auto":
        used = "ratio" if info["composition"]["exceeded"] else "median"
    elif method == "ratio" and rshift is None:
        used = "median"
        info["fallback"] = info["composition"]["reason"]
    info["used"] = used
    if used == "ratio":
        centered = [[None if v is None else v - s for v in c] for c, s in zip(cols, rshift, strict=True)]
        return _copy(m, values=[[centered[j][i] for j in range(len(m.samples))] for i in range(len(m.values))]), info
    centered = [[None if v is None else v - md for v in c] if not math.isnan(md) else c for c, md in zip(cols, meds, strict=True)]
    if used == "gn":
        mads = [stats.mad([v for v in c if v is not None]) for c in centered]
        okm = [x for x in mads if x and not math.isnan(x)]
        mad0 = stats.median(okm) if okm else 1.0
        centered = [[None if v is None else v / md * mad0 for v in c] if md and not math.isnan(md) else c
                    for c, md in zip(centered, mads, strict=True)]
    shifted = [[None if v is None else v + target for v in c] for c in centered]
    return _copy(m, values=[[shifted[j][i] for j in range(len(m.samples))] for i in range(len(m.values))]), info


def normalize(m: QuantMatrix, method: str) -> QuantMatrix:
    """normalize_info() without the info."""
    return normalize_info(m, method)[0]


def stable_centre(cols: list[list[float | None]]) -> list[dict]:
    """Each replicate's offset from 0 on the sites that do not change (D70): per replicate {"offset", "se",
    "sites"}. cols: the replicates of one condition (site ratios, log2).

    A site's deviation is the mean, over the replicates that measured it, of its ratio minus that replicate's
    current centre. The CENTRE_KEEP of the sites with the smallest |deviation| are kept and each replicate's centre
    is the median of its ratios over them; five passes. Sites a compound engages all move one way and together, so
    they leave the kept half and stop pulling the centre (a plain median moves towards them). The standard error
    is that of a median of the kept sites (1.2533 * robust SD / sqrt(n)) where the robust SD is taken over every
    site of the replicate, so it is on the safe side."""
    n = len(cols)
    rows = len(cols[0]) if cols else 0
    centre = []
    for c in cols:
        obs = [v for v in c if v is not None]
        centre.append(stats.median(obs) if obs else math.nan)
    keep: list[int] = []
    for _ in range(5):
        dev = {}
        for i in range(rows):
            d = [cols[j][i] - centre[j] for j in range(n) if cols[j][i] is not None and not math.isnan(centre[j])]
            if d:
                dev[i] = abs(sum(d) / len(d))
        if not dev:
            break
        cut = stats.quantile(list(dev.values()), CENTRE_KEEP)
        keep = [i for i, a in dev.items() if a <= cut]
        new = []
        for j in range(n):
            vals = [cols[j][i] for i in keep if cols[j][i] is not None]
            new.append(stats.median(vals) if vals else centre[j])
        if all(abs(a - b) < 1e-12 for a, b in zip(new, centre, strict=True) if not math.isnan(a)):
            centre = new
            break
        centre = new
    out = []
    for j in range(n):
        obs = [v for v in cols[j] if v is not None]
        kept = sum(1 for i in keep if cols[j][i] is not None)
        sd = stats.mad([v - centre[j] for v in obs]) if len(obs) > 1 else math.nan
        se = 1.2533 * sd / math.sqrt(kept) if kept and sd == sd else math.nan
        out.append({"offset": centre[j], "se": se, "sites": kept})
    return out


def centre_ratios(m: QuantMatrix, method: str) -> tuple[QuantMatrix, dict]:
    """Site ratios (isoDTB log2 H/L) centred per replicate, opt-in (analysis.ratio_centre, D70). A heavy / light
    mixing error moves every ratio of a replicate by the same amount; nothing else does that.

      none     nothing (the default: the lab decides). The offsets are still measured and reported.
      median   each replicate shifted so the median of its sites is 0. When many sites go one way (a promiscuous
               compound) the median moves with them and every unchanged site is shifted the other way.
      auto     each replicate's offset measured on the stable sites (stable_centre); a condition is centred, all
               its replicates, only when one of them is off by more than CENTRE_LIMIT log2 and more than CENTRE_SE
               times its standard error. Otherwise nothing changes.

    (matrix, info): info {"asked", "used" (none | median | stable), "conditions": {condition: {"centred",
    "largest", "replicates": {sample: {"offset", "se", "sites", "median"}}}}, "centred": [samples shifted]}."""
    info: dict = {"asked": method, "used": "none", "conditions": {}, "centred": []}
    if m.kind != "ratio" or not m.samples or not m.values:
        return m, info
    cols = [[row[j] for row in m.values] for j in range(len(m.samples))]
    shift = [0.0] * len(m.samples)
    for c in m.conditions:
        idx = [j for j, s in enumerate(m.samples) if m.condition[s] == c]
        stable = stable_centre([cols[j] for j in idx])
        reps = {}
        for j, st in zip(idx, stable, strict=True):
            obs = [v for v in cols[j] if v is not None]
            reps[m.samples[j]] = {"offset": st["offset"], "se": st["se"], "sites": st["sites"],
                                  "median": stats.median(obs) if obs else math.nan}
        clear = [s for s, r in reps.items() if r["sites"] >= CENTRE_MIN_SITES and r["offset"] == r["offset"]
                 and abs(r["offset"]) > CENTRE_LIMIT and abs(r["offset"]) > CENTRE_SE * (r["se"] or 0.0)]
        largest = max((abs(r["offset"]) for r in reps.values() if r["offset"] == r["offset"]), default=math.nan)
        centred = False
        if method == "median":
            for j in idx:
                med = reps[m.samples[j]]["median"]
                if med == med:
                    shift[j] = med
            centred = True
        elif method == "auto" and clear:
            for j in idx:
                off = reps[m.samples[j]]["offset"]
                if off == off and reps[m.samples[j]]["sites"] >= CENTRE_MIN_SITES:
                    shift[j] = off
            centred = True
        for r in reps.values():  # analysis.json is strict JSON: no NaN
            for k in ("offset", "se", "median"):
                if r[k] != r[k]:
                    r[k] = None
        info["conditions"][c] = {"centred": centred, "largest": None if largest != largest else largest,
                                 "clear": clear, "replicates": reps}
    if method == "median":
        info["used"] = "median"
    elif method == "auto" and any(v["centred"] for v in info["conditions"].values()):
        info["used"] = "stable"
    info["centred"] = [s for s, x in zip(m.samples, shift, strict=True) if x]
    if not info["centred"]:
        return m, info
    values = [[None if v is None else v - shift[j] for j, v in enumerate(row)] for row in m.values]
    return _copy(m, values=values), info


def centre_label(info: dict) -> str:
    """How the site ratios were centred, in words (the report, analysis.json, the liganded calls)."""
    used = (info or {}).get("used", "none")
    if used == "median":
        return "centred per replicate on the median site (ratio_centre: median)"
    if used == "stable":
        conds = [c for c, v in info["conditions"].items() if v["centred"]]
        return ("centred per replicate on the stable sites (ratio_centre: auto, "
                + ("every condition" if len(conds) == len(info["conditions"]) else ", ".join(conds)) + ")")
    return "as measured (not centred)"


# --------------------------------------------------------------- imputation --


def resolve_imputation(method: str, m: QuantMatrix) -> str:
    """'auto' -> what FragPipe-Analyst selects for the data type: Perseus-type for label-free
    intensities (LFQ, DIA), none for TMT (already ratios to a reference) and for isoDTB ratios."""
    if m.kind != "intensity":
        return "none"
    if method != "auto":
        return method
    return "none" if m.exp == "TMT" else "perseus"


def impute(m: QuantMatrix, method: str, shift: float = 1.8, scale: float = 0.3,
           seed: int = 123) -> tuple[QuantMatrix, list[list[bool]], int, list[str]]:
    """(imputed matrix, mask, rows dropped because every value was missing, notes)."""
    notes: list[str] = []
    ns = len(m.samples)
    keep = [i for i, r in enumerate(m.values) if any(v is not None for v in r)]
    dropped = len(m.values) - len(keep)
    feats = [m.features[i] for i in keep]
    vals = [list(m.values[i]) for i in keep]
    mask = [[v is None for v in r] for r in vals]
    if method == "none" or not any(any(r) for r in mask):
        return _copy(m, feats, vals), [[False] * ns for _ in vals], dropped, notes
    cols = [[r[j] for r in vals] for j in range(ns)]
    if method == "perseus":  # FragPipeAnalystR manual_impute(scale = 0.3, shift = 1.8), seed 123
        rng = RRandom(seed)
        for j in sorted(range(ns), key=lambda j: m.samples[j].encode("utf-8")):  # dplyr group_by order
            obs = [v for v in cols[j] if v is not None]
            miss = [i for i, v in enumerate(cols[j]) if v is None]
            if not miss:
                continue
            if len(obs) < 2:
                notes.append(f"sample {m.samples[j]} has fewer than 2 values; its missing values stay missing")
                for i in miss:
                    mask[i][j] = False
                continue
            sd = math.sqrt(stats.var(obs))
            for i, v in zip(miss, rng.rnorm(len(miss), stats.median(obs) - shift * sd, sd * scale), strict=True):
                vals[i][j] = v
    elif method == "min":  # MSnbase impute(method = "min"): the smallest value in the whole table
        lo = min(v for r in vals for v in r if v is not None)
        for r in vals:
            for j, v in enumerate(r):
                if v is None:
                    r[j] = lo
    elif method == "zero":  # MSnbase impute(method = "zero")
        for r in vals:
            for j, v in enumerate(r):
                if v is None:
                    r[j] = 0.0
    elif method == "mindet":  # imputeLCMD impute.MinDet(q = 0.01): each sample's 1st percentile
        for j in range(ns):
            q = stats.quantile([v for v in cols[j] if v is not None], 0.01)
            for r in vals:
                if r[j] is None:
                    r[j] = q
    elif method == "minprob":  # imputeLCMD impute.MinProb(q = 0.01, tune.sigma = 1), seed 123
        rng = RRandom(seed)
        frac = [sum(v is not None for v in r) / ns for r in vals]
        sds = [math.sqrt(stats.var([v for v in r if v is not None])) for r, f in zip(vals, frac, strict=True)
               if f > 0.5 and sum(v is not None for v in r) >= 2]
        sd = stats.median(sds) if sds else 0.0
        mins = [stats.quantile([v for v in c if v is not None], 0.01) for c in cols]
        for j in range(ns):
            draws = rng.rnorm(len(vals), mins[j], sd)
            for i, r in enumerate(vals):
                if r[j] is None:
                    r[j] = draws[i]
    elif method == "knn":
        _impute_knn(vals)
        notes.append("knn imputation follows impute::impute.knn (k = 10, rowmax = 0.5) without its "
                     "large-table clustering step")
    else:
        raise ValueError(f"unknown imputation {method!r}")
    return _copy(m, feats, vals), mask, dropped, notes


def _impute_knn(vals: Matrix, k: int = 10, rowmax: float = 0.5) -> None:
    """Troyanskaya et al. (2001) as in impute::impute.knn: a missing value becomes the mean of that sample's
    value in the k most similar rows (mean squared difference over shared samples); rows missing more than
    rowmax get the sample mean."""
    ns = len(vals[0]) if vals else 0
    col_mean = []
    for j in range(ns):
        obs = [r[j] for r in vals if r[j] is not None]
        col_mean.append(sum(obs) / len(obs) if obs else 0.0)
    snapshot = [list(r) for r in vals]
    for i, r in enumerate(snapshot):
        miss = [j for j, v in enumerate(r) if v is None]
        if not miss:
            continue
        if len(miss) / ns > rowmax:
            for j in miss:
                vals[i][j] = col_mean[j]
            continue
        own = [j for j in range(ns) if r[j] is not None]
        dists = []
        for i2, r2 in enumerate(snapshot):
            if i2 == i:
                continue
            shared = [j for j in own if r2[j] is not None]
            if not shared:
                continue
            dists.append((sum((r[j] - r2[j]) ** 2 for j in shared) / len(shared), i2))
        dists.sort()
        for j in miss:
            near = [snapshot[i2][j] for _, i2 in dists if snapshot[i2][j] is not None][:k]
            vals[i][j] = sum(near) / len(near) if near else col_mean[j]


def process(m: QuantMatrix, *, exclude: list[str] | None = None, conditions: dict[str, str] | None = None,
            contaminants: bool = True, global_pct: float = 0, condition_pct: float = 0,
            normalization: str = "none", imputation: str = "auto", shift: float = 1.8, scale: float = 0.3,
            seed: int = 123, ratio_centre: str = "none") -> tuple[Processed, list[str]]:
    """Every processing step in FragPipe-Analyst's order. Returns (Processed, notes). ratio_centre: site ratio
    data only (centre_ratios, D70); intensities are normalised by `normalization`."""
    notes: list[str] = []
    steps = [{"step": "loaded", "features": len(m.features), "samples": len(m.samples)}]
    m, n = choose_samples(m, exclude, conditions)
    notes += n
    if len(m.samples) != steps[0]["samples"]:
        steps.append({"step": "samples chosen", "features": len(m.features), "samples": len(m.samples)})
    if contaminants and m.kind == "intensity":
        m, removed = remove_contaminants(m)
        if removed:
            steps.append({"step": "contaminants removed", "removed": removed, "features": len(m.features)})
    before = m
    if (global_pct or condition_pct) and m.kind == "intensity":
        m, removed = filter_missing(m, global_pct, condition_pct)
        what = []
        if global_pct:
            what.append(f"≥{global_pct:g}% of all samples")
        if condition_pct:
            what.append(f"≥{condition_pct:g}% of the samples of at least one condition")
        steps.append({"step": "missing-value filter", "rule": "values in " + " and ".join(what),
                      "removed": removed, "features": len(m.features)})
    pre_norm = [list(r) for r in m.values]
    m, norm = normalize_info(m, normalization)
    if norm["used"] != "none":
        steps.append({"step": "normalisation", "method": NORMALIZATION_LABELS[norm["used"]], "features": len(m.features)})
    comp = norm.get("composition") or {}
    if norm["asked"] == "auto" and norm["used"] == "ratio":
        notes.append(f"normalisation: median centring would have shifted {comp['between'][0]} against "
                     f"{comp['between'][1]} by {comp['shift']:.2f} log2 (many features change in one direction), so "
                     f"the samples were normalised on the ratios of {comp['features']:,} stable features instead")
    if norm.get("fallback"):
        notes.append(f"normalisation: ratio was asked for, but {norm['fallback']}; median centring was used")
    if m.kind == "ratio":
        m, centring = centre_ratios(m, ratio_centre)
        norm["ratio_centre"] = centring
        if centring["centred"]:
            steps.append({"step": "normalisation", "method": "ratios " + centre_label(centring).split(" (")[0],
                          "features": len(m.features)})
            key = "offset" if centring["used"] == "stable" else "median"
            offs = ", ".join(f"{s} {r[key]:+.2f}" for c in centring["conditions"].values() if c["centred"]
                             for s, r in c["replicates"].items() if r[key] is not None)
            notes.append(f"site ratios {centre_label(centring)}; offsets removed (log2): {offs}")
    method = resolve_imputation(imputation, m)
    # the "measured" matrix keeps the same rows as the imputed one (all-missing rows go in both)
    keep = [i for i, r in enumerate(m.values) if any(v is not None for v in r)]
    measured = [list(m.values[i]) for i in keep]
    pre_norm = [pre_norm[i] for i in keep]
    im, mask, dropped, n = impute(m, method, shift, scale, seed)
    notes += n
    if dropped:
        steps.append({"step": "no values at all", "removed": dropped, "features": len(im.features)})
    if method != "none":
        total = len(im.values) * len(im.samples) or 1
        k = sum(sum(r) for r in mask)
        steps.append({"step": "imputation", "method": IMPUTATION_LABELS[method], "values": k,
                      "percent": round(100 * k / total, 1), "features": len(im.features)})
    return Processed(im, measured, mask, steps, before, pre_norm, method, norm), notes


IMPUTATION_LABELS = {"none": "none", "perseus": "Perseus-type (down-shift 1.8 SD, width 0.3 SD)",
                     "min": "table minimum", "zero": "zero", "mindet": "MinDet (1st percentile per sample)",
                     "minprob": "MinProb (random around the 1st percentile)", "knn": "k-nearest neighbours (k = 10)"}


# -------------------------------------------------------------------- limma --


@dataclass
class ContrastResult:
    treatment: str
    control: str  # a condition, "others" (one-vs-rest) or "" (ratio vs 0)
    diff: list[float]
    ci_low: list[float]
    ci_high: list[float]
    t: list[float]
    p: list[float]
    q: list[float]
    n_treatment: list[int]
    n_control: list[int]
    mean_treatment: list[float]
    mean_control: list[float]
    prior: tuple[float, float] = (math.nan, math.nan)
    se: list[float] | None = None      # standard error of diff (limma: sqrt(posterior variance) * unscaled SD)
    df: list[float] | None = None      # the t-statistic's degrees of freedom (limma: residual + prior df)


def _group_fit(values: Matrix, groups: list[list[int]]):
    """lmFit with a group-means design (~ 0 + group), row by row, missing values dropped:
    per-group n and mean, residual variance and df."""
    ns, means, s2, df = [], [], [], []
    for row in values:
        n_r, m_r, rss, nobs, rank = [], [], 0.0, 0, 0
        for g in groups:
            xs = [row[j] for j in g if row[j] is not None]
            n_r.append(len(xs))
            if xs:
                mu = sum(xs) / len(xs)
                rss += sum((x - mu) ** 2 for x in xs)
                nobs += len(xs)
                rank += 1
                m_r.append(mu)
            else:
                m_r.append(math.nan)
        d = nobs - rank
        ns.append(n_r)
        means.append(m_r)
        df.append(d)
        s2.append(rss / d if d > 0 else math.nan)
    return ns, means, s2, df


def _ebayes(s2: list[float], df: list[int | float]):
    """limma squeezeVar as eBayes calls it: (posterior variances, total df, d0, s0^2)."""
    var = [0.0 if d == 0 else v for v, d in zip(s2, df, strict=True)]  # limma: var[df == 0] <- 0
    d0, s0 = stats.fit_f_dist(var, df)
    if math.isnan(s0):
        d0, s0 = 0.0, math.nan
    pooled = sum(d for d in df if not math.isnan(d))
    post, dft = [], []
    for v, d in zip(var, df, strict=True):
        if math.isinf(d0):
            post.append(s0)
        elif d0 == 0:
            post.append(v if d > 0 else math.nan)
        else:
            post.append((d * v + d0 * s0) / (d + d0))
        dft.append(min(d + d0, pooled))
    return post, dft, d0, s0


def _toptable(coef, su, post, dft, level: float = 0.95):
    t, p, lo, hi = [], [], [], []
    qcache: dict[float, float] = {}
    for c, u, v, d in zip(coef, su, post, dft, strict=True):
        if math.isnan(c) or math.isnan(u) or math.isnan(v) or v <= 0 or math.isnan(d) or d <= 0:
            t.append(math.nan)
            p.append(math.nan)
            lo.append(math.nan)
            hi.append(math.nan)
            continue
        se = math.sqrt(v) * u
        tv = c / se
        t.append(tv)
        p.append(stats.t_two_sided_p(tv, d))
        if d not in qcache:
            qcache[d] = stats.qt_upper(1 - level, d)
        lo.append(c - qcache[d] * se)
        hi.append(c + qcache[d] * se)
    return t, p, lo, hi, stats.bh_adjust(p)


def se_df(coef, su, post, dft) -> tuple[list[float], list[float]]:
    """The standard error and df behind _toptable's t, per feature (nan where it gave none): MSstats-style
    columns, which the protein correction (proteincorr.py, D70) combines."""
    se, df = [], []
    for c, u, v, d in zip(coef, su, post, dft, strict=True):
        ok = not (math.isnan(c) or math.isnan(u) or math.isnan(v) or v <= 0 or math.isnan(d) or d <= 0)
        se.append(math.sqrt(v) * u if ok else math.nan)
        df.append(d if ok else math.nan)
    return se, df


def limma_contrasts(values: Matrix, samples: list[str], condition: dict[str, str],
                    contrasts: list[tuple[str, str]], min_valid: int = 0, squeeze=None,
                    needs: dict | None = None) -> list[ContrastResult]:
    """FragPipeAnalystR test_limma(type = "all" | "control" | "manual"): one model over all conditions
    (~ 0 + condition), each contrast refitted from its two coefficients, one eBayes for all contrasts.
    min_valid > 0 additionally leaves a feature untested in a contrast when either group has fewer
    measured values (used when nothing is imputed). squeeze replaces eBayes' variance prior (design.squeezer: DEqMS).
    needs: {(treatment, control): (values needed in each)} instead of min_valid for both (analysis.group_needs)."""
    conds = []
    for s in samples:
        if condition[s] not in conds:
            conds.append(condition[s])
    groups = [[j for j, s in enumerate(samples) if condition[s] == c] for c in conds]
    ns, means, s2, df = _group_fit(values, groups)
    post, dft, d0, s0 = (squeeze or _ebayes)(s2, df)
    out = []
    for a, b in contrasts:
        ia, ib = conds.index(a), conds.index(b)
        va, vb = (needs or {}).get((a, b), (min_valid, min_valid))
        coef, su = [], []
        for n_r, m_r in zip(ns, means, strict=True):
            ok = n_r[ia] > 0 and n_r[ib] > 0 and (not min_valid or (n_r[ia] >= va and n_r[ib] >= vb))
            coef.append(m_r[ia] - m_r[ib] if ok else math.nan)
            su.append(math.sqrt(1 / n_r[ia] + 1 / n_r[ib]) if ok else math.nan)
        t, p, lo, hi, q = _toptable(coef, su, post, dft)
        out.append(ContrastResult(a, b, coef, lo, hi, t, p, q, [n[ia] for n in ns], [n[ib] for n in ns],
                                  [m[ia] for m in means], [m[ib] for m in means], (d0, s0), *se_df(coef, su, post, dft)))
    return out


def limma_others(values: Matrix, samples: list[str], condition: dict[str, str], squeeze=None) -> list[ContrastResult]:
    """test_limma(type = "others"): each condition against all other samples, a separate model each."""
    conds = []
    for s in samples:
        if condition[s] not in conds:
            conds.append(condition[s])
    out = []
    for c in conds:
        g = [[j for j, s in enumerate(samples) if condition[s] == c], [j for j, s in enumerate(samples) if condition[s] != c]]
        ns, means, s2, df = _group_fit(values, g)
        post, dft, d0, s0 = (squeeze or _ebayes)(s2, df)
        coef = [m[0] - m[1] if n[0] and n[1] else math.nan for n, m in zip(ns, means, strict=True)]
        su = [math.sqrt(1 / n[0] + 1 / n[1]) if n[0] and n[1] else math.nan for n in ns]
        t, p, lo, hi, q = _toptable(coef, su, post, dft)
        out.append(ContrastResult(c, "others", coef, lo, hi, t, p, q, [n[0] for n in ns], [n[1] for n in ns],
                                  [m[0] for m in means], [m[1] for m in means], (d0, s0), *se_df(coef, su, post, dft)))
    return out


def limma_one_sample(values: Matrix, cols: list[int], name: str, min_valid: int = 2) -> ContrastResult:
    """Ratio data (isoDTB log2 H/L): is the mean of this condition's samples different from 0?"""
    sub = [[row[j] for j in cols] for row in values]
    ns, means, s2, df = _group_fit(sub, [list(range(len(cols)))])
    ok = [n[0] >= min_valid for n in ns]
    s2 = [v if o else math.nan for v, o in zip(s2, ok, strict=True)]
    df = [d if o else 0 for d, o in zip(df, ok, strict=True)]
    fit_s2 = [v for v, o in zip(s2, ok, strict=True) if o]
    fit_df = [d for d, o in zip(df, ok, strict=True) if o]
    _, _, d0, s0 = _ebayes(fit_s2, fit_df) if fit_s2 else ([], [], 0.0, math.nan)
    pooled = sum(fit_df)
    post, dft = [], []
    for v, d, o in zip(s2, df, ok, strict=True):
        if not o:
            post.append(math.nan)
            dft.append(math.nan)
        elif math.isinf(d0):
            post.append(s0)
            dft.append(pooled)
        elif d0 == 0 or math.isnan(s0):
            post.append(v)
            dft.append(d)
        else:
            post.append((d * v + d0 * s0) / (d + d0))
            dft.append(min(d + d0, pooled))
    coef = [m[0] if o else math.nan for m, o in zip(means, ok, strict=True)]
    su = [1 / math.sqrt(n[0]) if o else math.nan for n, o in zip(ns, ok, strict=True)]
    t, p, lo, hi, q = _toptable(coef, su, post, dft)
    return ContrastResult(name, "", coef, lo, hi, t, p, q, [n[0] for n in ns], [0] * len(ns),
                          [m[0] for m in means], [math.nan] * len(ns), (d0, s0), *se_df(coef, su, post, dft))


def all_pairs(conditions: list[str], control: str | None = None) -> list[tuple[str, str]]:
    """test_limma(type = "all"): combn(conditions, 2) in order of appearance; pairs that start with the
    control are flipped so the control is always the reference."""
    pairs = [(conditions[i], conditions[j]) for i in range(len(conditions)) for j in range(i + 1, len(conditions))]
    return [(b, a) if control is not None and a == control else (a, b) for a, b in pairs]


def significant(diff: float, score: float | None, alpha: float, lfc: float) -> str:
    """add_rejections(): p.adj <= alpha and |diff| >= lfc -> 'up' / 'down' / ''."""
    if score is None or diff is None or math.isnan(score) or math.isnan(diff):
        return ""
    if score <= alpha and abs(diff) >= lfc:
        return "up" if diff > 0 else "down"
    return ""
