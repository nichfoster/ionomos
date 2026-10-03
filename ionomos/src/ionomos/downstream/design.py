"""
Experimental designs: blocks, pairs and covariates in limma's linear model, and a moderated F-test.

FragPipe-Analyst fits one group-means model, ~0 + condition (fpa.py). When
samples come in batches, plexes, pairs or patients, that model leaves the
batch in the residuals and loses power. Here the design matrix is condition
plus nuisance terms, fitted as limma does:

    X = [condition indicators | block dummies | covariates]      model.matrix(~0 + condition + block + covariate)
    lm_fit()           lmFit: per row, missing values dropped, QR with lm.fit's limited pivoting (tol 1e-7);
                       coefficients a row can't estimate are NA; residual df = observed − rank
    contrasts_fit()    contrasts.fit: c'β, and the unscaled SD from the full design's coefficient correlation
                       (exact without missing values; limma's approximation with them in a non-orthogonal design)
    squeeze()          eBayes' squeezeVar across features (fpa._ebayes), or DEqMS (deqms.py)
    f_test()           classifyTestsF / topTableF: the moderated F on the condition contrasts ("any change")

Blocks are fixed effects (Smyth's advice for a handful of batches or pairs);
duplicateCorrelation's random block is left for later. A design that can't be
used (a block identical to condition, no residual df, a sample without a
value) raises DesignError with a sentence for the person, and the caller
falls back to the plain model. Checked against limma 3.68 in
tests/test_design.py (tests/golden/design/).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ionomos.downstream import fpa, stats

Matrix = list[list[float | None]]
TOL = 1e-7  # lm.fit's tolerance for a column that adds nothing (dqrdc2's limited pivoting)


@dataclass
class Design:
    samples: list[str]
    conditions: list[str]                 # the first len(conditions) columns are their indicators
    columns: list[str]
    x: list[list[float]]                  # [sample][column]
    formula: str                          # "~0 + condition + replicate", for Methods and analysis.json
    terms: list[dict] = field(default_factory=list)   # [{"name", "kind": block | factor | numeric, "levels"}]

    def describe(self) -> str:
        """'replicate as a block of 4 levels, age as a numeric covariate'."""
        parts = []
        for t in self.terms:
            if t["kind"] == "numeric":
                parts.append(f"{t['name']} as a numeric covariate")
            else:
                what = "a block" if t["kind"] == "block" else "a factor covariate"
                parts.append(f"{t['name']} as {what} of {len(t['levels'])} levels")
        return ", ".join(parts)

    def as_dict(self) -> dict:
        return {"formula": self.formula, "columns": list(self.columns), "terms": [dict(t) for t in self.terms],
                "residual_df": len(self.samples) - len(self.columns)}


class DesignError(ValueError):
    """The design asked for can't be used on this data; the plain model is used instead."""


# ------------------------------------------------------------------ design --


def _block_values(m, spec, pattern: str) -> tuple[str, dict[str, str]]:
    """(term name, sample -> block label) from analysis.block / block_from."""
    if pattern:
        rx = re.compile(pattern)
        out, bad = {}, []
        for s in m.samples:
            hit = rx.search(s)
            groups = hit.groupdict() if hit else {}
            val = None
            if hit:
                val = groups.get("block") if "block" in groups else (hit.group(1) if rx.groups else hit.group(0))
            if val is None or val == "":
                bad.append(s)
                continue
            out[s] = val
        if bad:
            raise DesignError(f"block_from {pattern!r} doesn't match sample(s) {', '.join(bad[:5])}")
        return "block", out
    if spec == "replicate":
        miss = [s for s in m.samples if m.replicate.get(s) is None]
        if miss:
            raise DesignError("block: replicate needs every sample's replicate number, and these have none: "
                              + ", ".join(miss[:5]))
        return "replicate", {s: str(m.replicate[s]) for s in m.samples}
    miss = [s for s in m.samples if s not in spec]
    if miss:
        raise DesignError("block: these samples have no block: " + ", ".join(miss[:5]))
    return "block", {s: str(spec[s]) for s in m.samples}


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    try:
        x = float(str(v).strip())
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def _factor(name: str, values: dict[str, str], samples: list[str], kind: str):
    """Treatment coding with levels in order of appearance: (term, column names, columns)."""
    levels: list[str] = []
    for s in samples:
        if values[s] not in levels:
            levels.append(values[s])
    cols = [[1.0 if values[s] == lv else 0.0 for s in samples] for lv in levels[1:]]
    return {"name": name, "kind": kind, "levels": levels}, [f"{name}{lv}" for lv in levels[1:]], cols


def build(m, block="", block_from: str = "", covariates: dict | None = None) -> Design | None:
    """The design for these samples (m: a QuantMatrix), or None when no block or covariate is asked for.
    Raises DesignError when it can't be used."""
    covariates = covariates or {}
    if not block and not block_from and not covariates:
        return None
    samples = list(m.samples)
    conds = m.conditions
    names = list(conds)
    cols = [[1.0 if m.condition[s] == c else 0.0 for s in samples] for c in conds]
    terms: list[dict] = []
    formula = ["~0 + condition"]
    if block or block_from:
        name, vals = _block_values(m, block, block_from)
        term, cn, cc = _factor(name, vals, samples, "block")
        if len(term["levels"]) < 2:
            raise DesignError(f"the {name} has a single level ({term['levels'][0]}), so it can't be a block")
        terms.append(term)
        names += cn
        cols += cc
        formula.append(name)
    for cname, spec in covariates.items():
        miss = [s for s in samples if s not in spec or spec[s] is None or str(spec[s]).strip() == ""]
        if miss:
            raise DesignError(f"covariate {cname!r} has no value for: " + ", ".join(miss[:5]))
        nums = {s: _num(spec[s]) for s in samples}
        if all(v is not None for v in nums.values()):
            if len({nums[s] for s in samples}) < 2:
                raise DesignError(f"covariate {cname!r} has the same value for every sample")
            terms.append({"name": cname, "kind": "numeric"})
            names.append(cname)
            cols.append([nums[s] for s in samples])
        else:
            term, cn, cc = _factor(cname, {s: str(spec[s]).strip() for s in samples}, samples, "factor")
            if len(term["levels"]) < 2:
                raise DesignError(f"covariate {cname!r} has the same value for every sample")
            terms.append(term)
            names += cn
            cols += cc
        formula.append(cname)
    x = [[c[j] for c in cols] for j in range(len(samples))]
    _check_rank(x, names, conds, terms, m)
    return Design(samples, conds, names, x, " + ".join(formula), terms)


def _check_rank(x, names, conds, terms, m) -> None:
    """Terms are added one at a time; the first that adds a column the others already explain is named."""
    n, p = len(x), len(names)
    start = len(conds)
    for t in terms:
        width = 1 if t["kind"] == "numeric" else len(t["levels"]) - 1
        upto = start + width
        kept, _q, _r = _qr([[row[j] for row in x] for j in range(upto)])
        if len(kept) < upto:
            nested = ""
            if t["kind"] != "numeric":
                by_level: dict[str, set[str]] = {}
                for s, row in zip(m.samples, x, strict=True):
                    hit = next((k for k in range(width) if row[start + k] == 1.0), None)
                    lv = t["levels"][0] if hit is None else t["levels"][hit + 1]
                    by_level.setdefault(lv, set()).add(m.condition[s])
                if all(len(c) == 1 for c in by_level.values()):
                    nested = " (each of its levels holds a single condition)"
            raise DesignError(f"{t['name']} is confounded with condition{'' if nested else ' or the other terms'}"
                              f"{nested}: the model can't tell their effects apart (the design matrix is "
                              "rank-deficient)")
        start = upto
    if n - p <= 0:
        raise DesignError(f"no residual degrees of freedom: {n} samples and {p} parameters "
                          f"({', '.join(names)}); use fewer blocks or covariates")


# ------------------------------------------------------------------ fitting --


def _qr(cols: list[list[float]]):
    """Gram-Schmidt (twice, for stability) with lm.fit's rule: a column whose part orthogonal to the
    columns before it is < TOL × its own norm is aliased and moved to the end. Returns (kept column
    indices, orthonormal q per kept column, R by column: r[k] = coefficients of kept[k] on q[0..k])."""
    kept, q, r = [], [], []
    for j, c in enumerate(cols):
        norm0 = math.sqrt(sum(v * v for v in c)) or 1.0
        v = list(c)
        coef = [0.0] * len(q)
        for _ in range(2):
            for k, qk in enumerate(q):
                d = sum(a * b for a, b in zip(qk, v, strict=True))
                coef[k] += d
                v = [a - d * b for a, b in zip(v, qk, strict=True)]
        nv = math.sqrt(sum(a * a for a in v))
        if nv < TOL * norm0:
            continue
        kept.append(j)
        q.append([a / nv for a in v])
        r.append(coef + [nv])
    return kept, q, r


def _rinv(r: list[list[float]]) -> list[list[float]]:
    """Inverse of the upper-triangular R (r[k] is column k)."""
    p = len(r)
    inv = [[0.0] * p for _ in range(p)]
    for j in range(p):
        inv[j][j] = 1 / r[j][j]
        for i in range(j - 1, -1, -1):
            inv[i][j] = -sum(r[k][i] * inv[k][j] for k in range(i + 1, j + 1)) / r[i][i]
    return inv


@dataclass
class LmFit:
    coef: list[list[float]]      # [row][column], NaN where the row can't estimate it
    su: list[list[float]]        # unscaled SDs, NaN where not estimable
    s2: list[float]              # residual variance (NaN when df = 0)
    df: list[int]


def lm_fit(values: Matrix, x: list[list[float]]) -> LmFit:
    """limma lmFit on a design matrix (samples × columns), row by row with missing values dropped.
    Rows sharing a pattern of missing values share one QR."""
    p = len(x[0]) if x else 0
    nan = math.nan
    cache: dict[tuple, tuple] = {}
    coefs, sus, s2s, dfs = [], [], [], []
    for row in values:
        obs = tuple(j for j, v in enumerate(row) if v is not None and not math.isnan(v))
        if not obs:
            coefs.append([nan] * p)
            sus.append([nan] * p)
            s2s.append(nan)
            dfs.append(0)
            continue
        hit = cache.get(obs)
        if hit is None:
            kept, q, r = _qr([[x[j][c] for j in obs] for c in range(p)])
            inv = _rinv(r)
            su = [nan] * p
            for a, c in enumerate(kept):
                su[c] = math.sqrt(sum(inv[a][b] ** 2 for b in range(len(kept))))
            hit = cache[obs] = (kept, q, inv, su, [[x[j][c] for c in kept] for j in obs])
        kept, q, inv, su, xk = hit
        y = [row[j] for j in obs]
        qty = [sum(a * b for a, b in zip(qk, y, strict=True)) for qk in q]
        b = [sum(inv[a][k] * qty[k] for k in range(a, len(kept))) for a in range(len(kept))]
        coef = [nan] * p
        for a, c in enumerate(kept):
            coef[c] = b[a]
        df = len(obs) - len(kept)
        if df > 0:
            rss = sum((yi - sum(xa * ba for xa, ba in zip(xr, b, strict=True))) ** 2
                      for yi, xr in zip(y, xk, strict=True))
            s2s.append(rss / df)
        else:
            s2s.append(nan)
        coefs.append(coef)
        sus.append(su)
        dfs.append(df)
    return LmFit(coefs, sus, s2s, dfs)


def cov_unscaled(x: list[list[float]]) -> list[list[float]]:
    """(X'X)^-1 of the full design (limma's cov.coefficients). Raises DesignError when rank-deficient."""
    p = len(x[0])
    kept, _q, r = _qr([[row[j] for row in x] for j in range(p)])
    if len(kept) < p:
        raise DesignError("the design matrix is rank-deficient")
    inv = _rinv(r)
    return [[sum(inv[i][k] * inv[j][k] for k in range(p)) for j in range(p)] for i in range(p)]


def _cor(v: list[list[float]]) -> list[list[float]]:
    d = [math.sqrt(v[i][i]) for i in range(len(v))]
    return [[v[i][j] / (d[i] * d[j]) for j in range(len(v))] for i in range(len(v))]


def contrasts_fit(fit: LmFit, v: list[list[float]], contrasts: list[list[float]]):
    """limma contrasts.fit: ([row][contrast] estimates, unscaled SDs); contrasts: one weight vector each.
    Without missing values the SD is sqrt(c'(X'X)^-1 c); with them, limma combines each row's own
    coefficient SDs with the full design's correlation (exact when the design is orthogonal)."""
    cor = _cor(v)
    p = len(v)
    orthog = all(abs(cor[i][j]) < 1e-14 for i in range(p) for j in range(i))
    cache: dict[tuple, list[float]] = {}
    est, sus = [], []
    for coef, su in zip(fit.coef, fit.su, strict=True):
        key = tuple(None if math.isnan(u) else u for u in su)
        us = cache.get(key)
        if us is None:
            us = []
            for c in contrasts:
                if any(w and math.isnan(s) for w, s in zip(c, su, strict=True)):
                    us.append(math.nan)  # a contrast of a coefficient this row can't estimate
                    continue
                d = [w * s if w else 0.0 for w, s in zip(c, su, strict=True)]
                if orthog:
                    us.append(math.sqrt(sum(a * a for a in d)))
                else:
                    us.append(math.sqrt(max(0.0, sum(d[i] * cor[i][j] * d[j] for i in range(p) if d[i]
                                                     for j in range(p) if d[j]))))
            cache[key] = us
        est.append([math.nan if math.isnan(u) else sum(w * b for w, b in zip(c, coef, strict=True) if w)
                    for c, u in zip(contrasts, us, strict=True)])
        sus.append(us)
    return est, sus


# ------------------------------------------------------------ moderation --


@dataclass
class Moderated:
    post: list[float]            # posterior variance per row
    dft: list[float]             # df of the moderated t per row
    df2: list[float]             # d0 + residual df per row (the moderated F's denominator; uncapped)
    d0: float
    s0: float                    # the prior variance (DEqMS: the median of the per-feature priors)
    info: dict = field(default_factory=dict)


def squeeze(s2: list[float], df: list[int], counts: list[int | None] | None = None,
            method: str = "limma") -> Moderated:
    """The variance prior: limma's squeezeVar (one prior for every feature) or DEqMS (a prior per feature
    from its peptide count; features without a count, or without residual df, keep limma's)."""
    post, dft, d0, s0 = fpa._ebayes(s2, df)
    df2 = [d0 + d for d in df]
    if method != "deqms" or counts is None:
        return Moderated(post, dft, df2, d0, s0)
    from ionomos.downstream import deqms

    res = deqms.spectra_count_ebayes(s2, df, counts)
    if res is None:
        why = deqms.TOO_FEW if any(c is not None for c in counts) else "the table has no peptide or PSM counts"
        return Moderated(post, dft, df2, d0, s0, {"variance_prior": "limma", "deqms_used": False, "reason": why})
    for i in res.used:
        post[i], dft[i], df2[i] = res.post[i], res.dft[i], res.dft[i]
    med = stats.median([res.prior[i] for i in res.used])
    info = {"variance_prior": "deqms", "deqms_used": True, "d0": res.d0, "features": len(res.used),
            "limma_prior_features": len(s2) - len(res.used), "median_prior": med, "limma_d0": d0, "limma_s0": s0}
    return Moderated(post, dft, df2, res.d0, med, info)


def squeezer(counts: list[int | None] | None, method: str):
    """squeeze() in fpa._ebayes' shape, for fpa.limma_contrasts / limma_others (the plain model with DEqMS).
    The last call's info is kept on the function (.info)."""
    def run(s2, df):
        mod = squeeze(s2, df, counts, method)
        run.info = mod.info
        return mod.post, mod.dft, mod.d0, mod.s0
    run.info = {}
    return run


def moderated_f(t_rows: list[list[float]], cov_contrasts: list[list[float]], df2: list[float]):
    """limma classifyTestsF(fstat.only = TRUE) + pf: (F, p, df1). t_rows: moderated t per row per contrast;
    cov_contrasts: C'(X'X)^-1 C (its correlation decorrelates the t's); df2: d0 + residual df per row."""
    k = len(cov_contrasts)
    diag = [cov_contrasts[i][i] or 1.0 for i in range(k)]
    cor = [[cov_contrasts[i][j] / math.sqrt(diag[i] * diag[j]) for j in range(k)] for i in range(k)]
    vals, vecs = _eigen_sym(cor)
    order = sorted(range(k), key=lambda i: -vals[i])
    vals = [vals[i] for i in order]
    vecs = [[row[i] for i in order] for row in vecs]
    r = sum(1 for v in vals if v / vals[0] > 1e-8)
    qm = [[vecs[i][j] / math.sqrt(vals[j]) / math.sqrt(r) for j in range(r)] for i in range(k)]
    fs, ps = [], []
    for t, d2 in zip(t_rows, df2, strict=True):
        if any(math.isnan(v) for v in t) or math.isnan(d2):
            fs.append(math.nan)
            ps.append(math.nan)
            continue
        f = sum(sum(t[i] * qm[i][j] for i in range(k)) ** 2 for j in range(r))
        fs.append(f)
        ps.append(f_upper(f, r, d2))
    return fs, ps, r


def f_upper(f: float, df1: float, df2: float) -> float:
    """pf(f, df1, df2, lower.tail = FALSE); df2 = Inf is the chi-square limit."""
    if math.isnan(f) or f < 0 or df2 <= 0:
        return math.nan
    if math.isinf(df2):
        return stats.gamma_upper(df1 / 2, df1 * f / 2)
    return stats.betainc(df2 / 2, df1 / 2, df2 / (df2 + df1 * f))


def _eigen_sym(a: list[list[float]], sweeps: int = 100):
    """Cyclic Jacobi: (eigenvalues, eigenvectors as columns) of a small symmetric matrix."""
    n = len(a)
    a = [row[:] for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        if sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j) < 1e-30:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    return [a[i][i] for i in range(n)], v


# ------------------------------------------------------------ comparisons --


def _group_stats(values: Matrix, idx: list[int]):
    ns, means = [], []
    for row in values:
        xs = [row[j] for j in idx if row[j] is not None]
        ns.append(len(xs))
        means.append(sum(xs) / len(xs) if xs else math.nan)
    return ns, means


def _members(design: Design, k: int) -> list[int]:
    return [j for j, row in enumerate(design.x) if row[k] == 1.0]


def _contrast(design: Design, a: str, b: str) -> list[float]:
    c = [0.0] * len(design.columns)
    c[design.conditions.index(a)] += 1.0
    c[design.conditions.index(b)] -= 1.0
    return c


def limma_design(values: Matrix, design: Design, contrasts: list[tuple[str, str]], min_valid: int = 0,
                 counts: list[int | None] | None = None, variance_prior: str = "limma", needs: dict | None = None):
    """Each (treatment, control) contrast from one fit of the design and one variance prior (limma's
    lmFit → contrasts.fit → eBayes → topTable(confint = TRUE)). min_valid and needs as fpa.limma_contrasts.
    Returns ([fpa.ContrastResult], info about the variance prior)."""
    fit = lm_fit(values, design.x)
    v = cov_unscaled(design.x)
    est, sus = contrasts_fit(fit, v, [_contrast(design, a, b) for a, b in contrasts])
    mod = squeeze(fit.s2, fit.df, counts, variance_prior)
    out = []
    for k, (a, b) in enumerate(contrasts):
        na, ma = _group_stats(values, _members(design, design.conditions.index(a)))
        nb, mb = _group_stats(values, _members(design, design.conditions.index(b)))
        va, vb = (needs or {}).get((a, b), (min_valid, min_valid))
        coef, su = [], []
        for i in range(len(values)):
            ok = not min_valid or (na[i] >= va and nb[i] >= vb)
            coef.append(est[i][k] if ok else math.nan)
            su.append(sus[i][k] if ok else math.nan)
        t, pv, lo, hi, q = fpa._toptable(coef, su, mod.post, mod.dft)
        out.append(fpa.ContrastResult(a, b, coef, lo, hi, t, pv, q, na, nb, ma, mb, (mod.d0, mod.s0),
                                      *fpa.se_df(coef, su, mod.post, mod.dft)))
    return out, mod.info


def limma_design_others(values: Matrix, design: Design, counts=None, variance_prior: str = "limma"):
    """test_limma(type = "others") with the design's nuisance terms: per condition, [condition, the rest,
    blocks, covariates] with its own variance prior. Returns ([ContrastResult], info)."""
    k = len(design.conditions)
    out, info = [], {}
    for ci, c in enumerate(design.conditions):
        x = [[row[ci], 1.0 - row[ci], *row[k:]] for row in design.x]
        try:
            cov_unscaled(x)
        except DesignError:
            raise DesignError(f"{c} vs others: the blocks or covariates are confounded with {c}") from None
        sub = Design(design.samples, [c, "others"], [c, "others", *design.columns[k:]], x, design.formula,
                     design.terms)
        res, info = limma_design(values, sub, [(c, "others")], 0, counts, variance_prior)
        out.append(res[0])
    return out, info


@dataclass
class FTest:
    """Per feature: is there any difference between the conditions? limma's moderated F on the condition
    contrasts (every condition against the reference, a basis of all their differences)."""
    reference: str
    conditions: list[str]
    df1: int
    f: list[float]
    p: list[float]
    q: list[float]
    formula: str = ""
    info: dict = field(default_factory=dict)


def f_test(values: Matrix, design: Design, reference: str, min_valid: int = 0,
           counts: list[int | None] | None = None, variance_prior: str = "limma") -> FTest:
    conds = design.conditions
    cmat = [_contrast(design, a, reference) for a in conds if a != reference]
    fit = lm_fit(values, design.x)
    v = cov_unscaled(design.x)
    est, sus = contrasts_fit(fit, v, cmat)
    mod = squeeze(fit.s2, fit.df, counts, variance_prior)
    ns = [_group_stats(values, _members(design, k))[0] for k in range(len(conds))]
    t_rows = []
    for i in range(len(values)):
        enough = not min_valid or all(n[i] >= min_valid for n in ns)
        pv = mod.post[i]
        t_rows.append([e / (u * math.sqrt(pv)) if enough and not math.isnan(e) and not math.isnan(u) and u > 0
                       and not math.isnan(pv) and pv > 0 else math.nan for e, u in zip(est[i], sus[i], strict=True)])
    p = len(design.columns)
    cvc = [[sum(ci[a] * v[a][b] * cj[b] for a in range(p) if ci[a] for b in range(p) if cj[b]) for cj in cmat]
           for ci in cmat]
    fs, ps, r = moderated_f(t_rows, cvc, mod.df2)
    return FTest(reference, list(conds), r, fs, ps, stats.bh_adjust(ps), design.formula, mod.info)


def plain(m) -> Design:
    """~0 + condition as a Design (the F-test when no block or covariate is set)."""
    conds = m.conditions
    x = [[1.0 if m.condition[s] == c else 0.0 for c in conds] for s in m.samples]
    return Design(list(m.samples), conds, list(conds), x, "~0 + condition", [])
