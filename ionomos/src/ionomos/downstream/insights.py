"""
Deeper QC and discovery on a processed experiment: the checks a careful analyst does by eye,
done every time and turned into numbers the report and the doctor can use.

    sample_scorecard()     each sample's identifications, missingness, loading, correlation to its own
                           replicates, spread around its group, leave-one-out CV; robust z-scores and flags
    pc_association()       how much of each principal component is explained by condition and by replicate
                           number (a PC explained by replicate number is the classic batch/day effect)
    missingness()          detection probability against intensity: missing-not-at-random (low-abundance
                           dropouts, where left-censored imputation fits) or missing at random
    p_histogram()          p-value histogram, its shape and Storey's pi0 (the share of features that don't change)
    presence_absence()     "on/off" features: measured in most replicates of one group and never in the other,
                           the hits a t-test can't see (MS-DAP's differential detection, simplified)
    imputation_driven()    hits whose significance rests on imputed values (at least half of a group imputed)
    power()                minimum detectable log2 fold change against replicates per group, from this
                           experiment's own variance (limma's prior when available); and for each comparison
                           with the samples it really has on each side (DMSO n=2 against a compound n=4)

Group sizes need not be equal (D61). A sample is never flagged for being in a small group: the spread around
the group is put on one scale before it is compared across samples (a sample with one mate scatters more around
that mate than a sample with three around their mean), and a group of one has no replicate numbers to judge.

All plain Python, bounded by features x samples. Each function tolerates tiny or odd inputs and returns
{} / [] rather than raising; analyze() also runs them as an isolated stage.
"""
from __future__ import annotations

import math

from ionomos.downstream import stats

Matrix = list[list[float | None]]

ONOFF_FRACTION = 0.75   # on/off: measured in at least this share of the "on" group (and at least 2 samples)
IMPUTED_SHARE = 0.5     # a hit is imputation-driven when at least this share of a group's values was imputed


def _groups(samples: list[str], condition: dict[str, str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for j, s in enumerate(samples):
        out.setdefault(condition[s], []).append(j)
    return out


def _robust_z(xs: list[float | None], floor: float) -> list[float | None]:
    """(x - median) / (1.4826 MAD), the MAD floored so a handful of near-identical samples can't make every
    small difference look extreme."""
    obs = [x for x in xs if x is not None]
    if len(obs) < 3:
        return [None for _ in xs]
    med = stats.median(obs)
    spread = max(stats.mad(obs), floor)
    return [None if x is None else (x - med) / spread for x in xs]


def _pearson_pairwise(a: list[float | None], b: list[float | None]) -> float | None:
    from ionomos.downstream.qc import pearson

    return pearson(a, b)


# ------------------------------------------------------------ sample scorecard --


def sample_scorecard(measured: Matrix, samples: list[str], condition: dict[str, str],
                     before_norm: Matrix | None = None, corr: dict | None = None, kind: str = "intensity") -> list[dict]:
    """One row per sample with its numbers, robust z-scores, flags (plain words) and a status ok / warn / fail.

    corr: qc.correlation() output (complete-feature Pearson matrix), reused so this stays cheap."""
    ns = len(samples)
    if ns < 3 or not measured:
        return []
    groups = _groups(samples, condition)
    nf = len(measured)
    ids = [sum(1 for r in measured if r[j] is not None) for j in range(ns)]
    raw = before_norm if before_norm and len(before_norm) == nf else measured
    medians = []
    for j in range(ns):
        xs = [r[j] for r in raw if r[j] is not None]
        medians.append(stats.median(xs) if xs else None)
    mat = (corr or {}).get("matrix") or []
    if len(mat) != ns:  # no usable QC correlation: pairwise-complete on a bounded subset of rows
        rows = measured[:3000]
        cols = [[r[j] for r in rows] for j in range(ns)]
        mat = [[1.0 if a == b else _pearson_pairwise(cols[a], cols[b]) for b in range(ns)] for a in range(ns)]
    corr_group, corr_all = [], []
    for j, s in enumerate(samples):
        mates = [k for k in groups[condition[s]] if k != j]
        others = [k for k in range(ns) if k != j]
        cg = [mat[j][k] for k in mates if mat[j][k] is not None]
        ca = [mat[j][k] for k in others if mat[j][k] is not None]
        corr_group.append(stats.median(cg) if cg else None)
        corr_all.append(stats.median(ca) if ca else None)
    # spread around the group: median |x - mean of the rest of its group| (MS-DAP's within-group fold changes)
    spread: list[float | None] = []
    for j, s in enumerate(samples):
        mates = [k for k in groups[condition[s]] if k != j]
        if not mates:
            spread.append(None)
            continue
        dev = []
        for r in measured:
            if r[j] is None:
                continue
            obs = [r[k] for k in mates if r[k] is not None]
            if obs:
                dev.append(abs(r[j] - sum(obs) / len(obs)))
        spread.append(stats.median(dev) if dev else None)
    loo = _leave_one_out_cv(measured, samples, condition, groups) if kind == "intensity" else [None] * ns
    # x - mean(k mates) has variance s2 (1 + 1/k): put every sample on the scale of the usual group size, so a
    # sample with one mate is not judged against samples with three (equal groups: the factor is 1, nothing changes)
    mates_n = [len(groups[condition[s]]) - 1 for s in samples]
    usual = stats.median([k for k in mates_n if k > 0]) if any(mates_n) else 0
    scale = [math.sqrt((1 + 1 / usual) / (1 + 1 / k)) if k > 0 and usual else 1.0 for k in mates_n]
    judged = [None if x is None else x * f for x, f in zip(spread, scale, strict=True)]
    z_ids = _robust_z([float(x) for x in ids], floor=max(1.0, 0.03 * (stats.median(ids) or 1)))
    z_corr = _robust_z(corr_group, floor=0.005)
    z_spread = _robust_z(judged, floor=0.02)
    med_ids = stats.median(ids) or 0
    obs_med = [x for x in medians if x is not None]
    centre = stats.median(obs_med) if obs_med else None
    med_corr_all = stats.median([x for x in corr_group if x is not None]) if any(x is not None for x in corr_group) else None
    med_spread = stats.median([x for x in judged if x is not None]) if any(x is not None for x in judged) else None
    out = []
    for j, s in enumerate(samples):
        flags, severe = [], 0
        if med_ids and ids[j] < 0.4 * med_ids:
            flags.append(f"far fewer identifications ({ids[j]:,} vs ~{int(med_ids):,})")
            severe += 2
        elif med_ids and ids[j] < 0.7 * med_ids and (z_ids[j] or 0) < -3:
            flags.append(f"fewer identifications ({ids[j]:,} vs ~{int(med_ids):,})")
            severe += 1
        if (corr_group[j] is not None and med_corr_all is not None and (z_corr[j] or 0) < -3.5
                and med_corr_all - corr_group[j] > 0.02):
            flags.append(f"correlates poorly with its replicates (r {corr_group[j]:.3f} vs ~{med_corr_all:.3f})")
            severe += 1
        if (judged[j] is not None and med_spread and (z_spread[j] or 0) > 3.5 and judged[j] > 1.5 * med_spread):
            flags.append(f"values scatter widely around its group (median |Δ| {judged[j]:.2f} vs ~{med_spread:.2f} log2)")
            severe += 1
        if loo[j] is not None and loo[j] <= -0.25:
            flags.append(f"its group's median CV drops {-100 * loo[j]:.0f}% without it")
            severe += 1
        shift = None if medians[j] is None or centre is None else medians[j] - centre
        if kind == "intensity" and shift is not None and abs(shift) >= 1.0:
            flags.append(f"loaded {2 ** abs(shift):.1f}× {'more' if shift > 0 else 'less'} than the others "
                         "(normalisation corrects this)")
        status = "fail" if severe >= 2 else "warn" if severe == 1 else "ok"
        out.append({"sample": s, "condition": condition[s], "ids": ids[j], "missing_pct": 100 * (1 - ids[j] / nf),
                    "median": medians[j], "shift": shift, "corr_group": corr_group[j], "corr_all": corr_all[j],
                    "spread": spread[j], "loo_cv": loo[j], "z_ids": z_ids[j], "z_corr": z_corr[j],
                    "z_spread": z_spread[j], "flags": flags, "status": status})
    return out


def _cv(xs: list[float]) -> float | None:
    lin = [2 ** x for x in xs]
    mu = sum(lin) / len(lin)
    return math.sqrt(stats.var(lin)) / mu if len(lin) >= 2 and mu > 0 else None


def _leave_one_out_cv(measured: Matrix, samples, condition, groups) -> list[float | None]:
    """Relative change of the group's median CV when the sample is left out (-0.3 = CVs 30% lower without it).
    Groups of at least 4 only: with 3, dropping anyone changes the CVs a lot."""
    out: list[float | None] = [None] * len(samples)
    rows = measured[:4000]
    for _c, idx in groups.items():
        if len(idx) < 4:
            continue

        def med_cv(cols):
            cvs = []
            for r in rows:
                xs = [r[k] for k in cols if r[k] is not None]
                if len(xs) >= 2:
                    v = _cv(xs)
                    if v is not None:
                        cvs.append(v)
            return stats.median(cvs) if cvs else None

        full = med_cv(idx)
        if not full:
            continue
        for j in idx:
            part = med_cv([k for k in idx if k != j])
            if part is not None:
                out[j] = part / full - 1
    return out


# -------------------------------------------------------------- PC association --


def _r2(scores: list[float], labels: list) -> float | None:
    """Share of the variance of scores explained by a categorical label (one-way ANOVA R²)."""
    if len(set(labels)) < 2 or len(set(labels)) >= len(labels):
        return None
    mu = sum(scores) / len(scores)
    total = sum((x - mu) ** 2 for x in scores)
    if total <= 0:
        return None
    by: dict = {}
    for x, g in zip(scores, labels, strict=True):
        by.setdefault(g, []).append(x)
    between = sum(len(v) * (sum(v) / len(v) - mu) ** 2 for v in by.values())
    return between / total


def pc_association(pca: dict, samples: list[str], condition: dict[str, str],
                   replicate: dict[str, int] | None = None) -> dict:
    """{"pcs": [{"pc": 1, "percent", "r2_condition", "r2_replicate"}], "batch": {...} | None}.

    Replicate number is only used when every sample has one, there are at least 2 replicate numbers and
    every condition has at least 2 samples (otherwise it is confounded with condition by construction)."""
    scores = pca.get("scores") or []
    pct = pca.get("percent") or []
    if len(scores) != len(samples) or len(samples) < 4:
        return {"pcs": [], "batch": None}
    conds = [condition[s] for s in samples]
    rep = replicate or {}
    reps = [rep.get(s) for s in samples]
    groups = _groups(samples, condition)
    use_rep = (all(r is not None for r in reps) and len(set(reps)) >= 2 and all(len(v) >= 2 for v in groups.values())
               and len(groups) >= 2)
    pcs = []
    for k in range(min(4, len(pct))):
        xs = [s[k] for s in scores]
        pcs.append({"pc": k + 1, "percent": pct[k], "r2_condition": _r2(xs, conds),
                    "r2_replicate": _r2(xs, reps) if use_rep else None})
    batch = None
    for p in pcs[:2]:
        rr, rc = p["r2_replicate"], p["r2_condition"]
        if rr is not None and p["percent"] >= 15 and rr >= 0.5 and rr > (rc or 0):
            batch = {"pc": p["pc"], "percent": p["percent"], "r2_replicate": rr, "r2_condition": rc}
            break
    return {"pcs": pcs, "batch": batch}


# ------------------------------------------------------------------ missingness --


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    sab = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    saa = sum((x - ma) ** 2 for x in ra)
    sbb = sum((y - mb) ** 2 for y in rb)
    return sab / math.sqrt(saa * sbb) if saa > 0 and sbb > 0 else None


def missingness(measured: Matrix, bins: int = 12) -> dict:
    """Detection rate against mean observed log2 value, in equal-count bins, and a verdict:
    "intensity" (low-abundance features go missing: MNAR, left-censored imputation fits),
    "random" (missing at random: kNN or no imputation fits), "mixed", or "" when there's too little to say."""
    ns = len(measured[0]) if measured else 0
    pts = []
    for r in measured:
        obs = [v for v in r if v is not None]
        if obs:
            pts.append((sum(obs) / len(obs), len(obs) / ns))
    incomplete = [p for p in pts if p[1] < 1]
    out = {"bins": [], "rho": None, "gap": None, "verdict": "", "incomplete": len(incomplete), "features": len(pts)}
    if ns < 2 or len(pts) < 30:
        return out
    pts.sort()
    size = math.ceil(len(pts) / bins)
    for k in range(0, len(pts), size):
        chunk = pts[k:k + size]
        out["bins"].append({"mean": sum(p[0] for p in chunk) / len(chunk),
                            "detected": sum(p[1] for p in chunk) / len(chunk), "n": len(chunk)})
    if len(incomplete) < 20:
        out["verdict"] = "few"  # almost nothing is missing: imputation hardly matters
        return out
    rho = spearman([p[0] for p in pts], [p[1] for p in pts])
    complete = [p[0] for p in pts if p[1] == 1]
    gap = (stats.median(complete) - stats.median([p[0] for p in incomplete])) if complete else None
    spread = math.sqrt(stats.var([p[0] for p in pts])) or 1.0
    rel = gap / spread if gap is not None else None  # in SDs of the feature means, so it means the same in any data
    out["rho"], out["gap"] = rho, gap
    if (rho is not None and rho >= 0.3) or (rel is not None and rel >= 0.5):
        out["verdict"] = "intensity"
    elif (rho is None or abs(rho) < 0.12) and (rel is None or abs(rel) < 0.25):
        out["verdict"] = "random"
    else:
        out["verdict"] = "mixed"
    return out


# ----------------------------------------------------------------- p-values --


def pi0(ps: list[float]) -> float | None:
    """Storey's estimate of the share of true nulls: median over lambda 0.5..0.8 of #{p > lambda} / (m (1 - lambda)),
    capped at 1. None below 50 p-values."""
    ps = [p for p in ps if p is not None and 0 <= p <= 1]
    m = len(ps)
    if m < 50:
        return None
    ests = []
    for k in range(7):
        lam = 0.5 + 0.05 * k
        ests.append(sum(1 for p in ps if p > lam) / (m * (1 - lam)))
    return min(1.0, stats.median(ests))


def p_histogram(ps: list[float], nbins: int = 20) -> dict:
    """{"bins", "n", "pi0", "shape"}: shape is "signal" (a peak at 0, flat elsewhere), "flat" (no signal),
    "conservative" (a pile-up near 1: often ties from imputation, or a model that overestimates variance),
    "hump" (a bulge in the middle: the model doesn't fit the data), or "" below 200 p-values."""
    ps = [p for p in ps if p is not None and 0 <= p <= 1]
    bins = [0] * nbins
    for p in ps:
        bins[min(nbins - 1, int(p * nbins))] += 1
    out = {"bins": bins, "n": len(ps), "pi0": pi0(ps), "shape": ""}
    if len(ps) < 200:
        return out
    b = [x / len(ps) * nbins for x in bins]  # density, 1 = flat
    middle = stats.median(b[nbins // 4: nbins - 3])
    tail = sum(b[-2:]) / 2
    centre = sum(b[nbins // 4: nbins // 2 + 2]) / len(b[nbins // 4: nbins // 2 + 2])
    edges = sum(b[nbins // 2 + 3: nbins - 2]) / max(1, len(b[nbins // 2 + 3: nbins - 2]))
    if tail > 1.8 * max(middle, 0.2) and tail > 1.5:
        out["shape"] = "conservative"
    elif centre > 1.6 * max(edges, 0.2) and centre > 1.3:
        out["shape"] = "hump"
    elif b[0] > 1.5 * max(middle, 0.2):
        out["shape"] = "signal"
    else:
        out["shape"] = "flat"
    return out


# ---------------------------------------------------------- presence / absence --


def presence_absence(measured: Matrix, samples: list[str], condition: dict[str, str], treatment: str,
                     control: str | None) -> list[dict]:
    """Features measured in >= max(2, 75%) of one group and in none of the other. Sorted by how complete the
    "on" group is, then by its mean value. Needs a control group (not ratio-vs-0 comparisons)."""
    if control is None:
        return []
    groups = _groups(samples, condition)
    ta = groups.get(treatment) or []
    tb = [j for j in range(len(samples)) if condition[samples[j]] != treatment] if control == "others" else (
        groups.get(control) or [])
    if len(ta) < 2 or len(tb) < 2:
        return []
    out = []
    for i, r in enumerate(measured):
        na = sum(1 for j in ta if r[j] is not None)
        nb = sum(1 for j in tb if r[j] is not None)
        for on, n_on, size, n_off in (("treatment", na, len(ta), nb), ("control", nb, len(tb), na)):
            need = max(2, math.ceil(ONOFF_FRACTION * size))
            if n_off == 0 and n_on >= need:
                idx = ta if on == "treatment" else tb
                obs = [r[j] for j in idx if r[j] is not None]
                out.append({"index": i, "only_in": on, "group": treatment if on == "treatment" else control,
                            "detected": n_on, "of": size, "mean": sum(obs) / len(obs)})
    out.sort(key=lambda x: (-x["detected"] / x["of"], -x["mean"]))
    return out


# ------------------------------------------------------- imputation-driven hits --


def imputation_driven(imputed: list[list[bool]], samples: list[str], condition: dict[str, str], d) -> list[int]:
    """Indices of d's significant features where >= half of either group's values were imputed."""
    groups = _groups(samples, condition)
    ta = groups.get(d.treatment) or []
    if d.control == "others":
        tb = [j for j in range(len(samples)) if condition[samples[j]] != d.treatment]
    else:
        tb = (groups.get(d.control) or []) if d.control else []
    out = []
    for r in d.rows:
        if not r["significant"]:
            continue
        mask = imputed[r["index"]]
        for g in (ta, tb):
            if g and sum(1 for j in g if mask[j]) / len(g) >= IMPUTED_SHARE:
                out.append(r["index"])
                break
    return out


# ------------------------------------------------------------------- power --


def power(values: Matrix, samples: list[str], condition: dict[str, str], prior: tuple[float, float] | None = None,
          kind: str = "intensity", alphas: tuple[float, ...] = (0.05, 0.001), beta: float = 0.2,
          n_range: tuple[int, int] = (2, 10), pairs: list[tuple[str, int, int]] | None = None,
          pooled: bool = True) -> dict:
    """Minimum detectable |log2FC| at 80% power for n replicates per group, at the 25th / 50th / 75th percentile
    of the per-feature SD (moderated with limma's prior when given). Two-group t-test for intensities
    (sqrt(2/n)), one-sample for ratios (sqrt(1/n)). alpha 0.05 is nominal; 0.001 is closer to what survives
    a multiple-testing correction in a typical proteome.

    pairs: [(comparison, samples in the treatment, in the control)] adds "comparisons": the same number for each
    comparison with the samples it has, sqrt(1/n1 + 1/n2) instead of sqrt(2/n). pooled (limma): the residual df
    come from every condition (samples - conditions), as in the model; otherwise from the two groups."""
    groups = _groups(samples, condition)
    d0, s20 = prior if prior else (math.nan, math.nan)
    moderated = d0 is not None and s20 is not None and math.isfinite(d0) and math.isfinite(s20) and d0 > 0
    sds = []
    for r in values:
        ss, df = 0.0, 0
        for idx in groups.values():
            obs = [r[j] for j in idx if r[j] is not None]
            if len(obs) >= 2:
                mu = sum(obs) / len(obs)
                ss += sum((x - mu) ** 2 for x in obs)
                df += len(obs) - 1
        if df:
            s2 = ss / df
            if moderated:
                s2 = (d0 * s20 + df * s2) / (d0 + df)
            sds.append(math.sqrt(s2))
    if len(sds) < 10:
        return {}
    qs = {"q25": stats.quantile(sds, 0.25), "q50": stats.quantile(sds, 0.5), "q75": stats.quantile(sds, 0.75)}
    ns = list(range(n_range[0], n_range[1] + 1))
    curves = {}
    for a in alphas:
        rows = []
        for n in ns:
            df = (n - 1 if kind == "ratio" else 2 * n - 2) + (min(d0, 1000) if moderated else 0)
            if df <= 0:
                rows.append(None)
                continue
            factor = (stats.qt_upper(a, df) + stats.qt_upper(2 * beta, df)) * math.sqrt((1 if kind == "ratio" else 2) / n)
            rows.append({k: factor * v for k, v in qs.items()})
        curves[str(a)] = rows
    current = sorted(len(v) for v in groups.values())
    out = {"n": ns, "sd": qs, "curves": curves, "moderated": moderated, "current_n": current[len(current) // 2],
           "beta": beta}
    model_df = sum(len(v) - 1 for v in groups.values())
    comps = []
    for name, n1, n2 in pairs or []:
        if kind == "ratio" or n1 < 1 or n2 < 1:
            continue
        df = (model_df if pooled else n1 + n2 - 2) + (min(d0, 1000) if moderated else 0)
        row = {"name": name, "n": [n1, n2], "df": df, "balanced_n": 2 / (1 / n1 + 1 / n2), "mdfc": {}}
        for a in alphas:
            row["mdfc"][str(a)] = None if df <= 0 else {
                k: (stats.qt_upper(a, df) + stats.qt_upper(2 * beta, df)) * math.sqrt(1 / n1 + 1 / n2) * v
                for k, v in qs.items()}
        comps.append(row)
    if comps:
        out["comparisons"] = comps
        out["unbalanced"] = any(r["n"][0] != r["n"][1] for r in comps)
    return out
