"""
Small, dependency-free statistics for proteomics tables (checked against scipy in tests).

    welch_t(a, b)          -> (t, df, p)   two-sided Welch's t-test
    one_sample_t(a, mu=0)  -> (t, df, p)   two-sided one-sample t-test
    bh_adjust(ps)          -> q-values     Benjamini-Hochberg
    median_center(cols)    -> per-sample median normalisation of log2 values

The Student-t tail uses the regularised incomplete beta function (continued
fraction, Numerical Recipes 6.4), accurate to ~1e-12 for the df seen here.
"""
from __future__ import annotations

import math
from collections.abc import Sequence


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def var(xs: Sequence[float]) -> float:
    """Sample variance (n-1)."""
    n = len(xs)
    if n < 2:
        return float("nan")
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return float("nan")
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _betacf(a: float, b: float, x: float) -> float:
    eps, fpmin = 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > fpmin else fpmin)
        c = 1 + aa / c
        c = c if abs(c) > fpmin else fpmin
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > fpmin else fpmin)
        c = 1 + aa / c
        c = c if abs(c) > fpmin else fpmin
        de = d * c
        h *= de
        if abs(de - 1) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(lbt)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    if math.isnan(t) or math.isnan(df) or df <= 0:
        return float("nan")
    if math.isinf(t):
        return 0.0
    return min(1.0, betainc(df / 2, 0.5, df / (df + t * t)))


def qt_upper(alpha2: float, df: float) -> float:
    """t such that P(|T| > t) = alpha2 (e.g. 0.05 -> R's qt(0.975, df)). Bisection on the exact tail."""
    if math.isnan(df) or df <= 0:
        return float("nan")
    if math.isinf(df):
        from ionomos.downstream.rrandom import qnorm

        return qnorm(1 - alpha2 / 2)
    lo, hi = 0.0, 1.0
    while t_two_sided_p(hi, df) > alpha2:
        hi *= 2
        if hi > 1e12:
            return float("inf")
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > alpha2:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-15 * max(1.0, hi):
            break
    return (lo + hi) / 2


def quantile(xs: Sequence[float], q: float) -> float:
    """R's quantile(type = 7)."""
    s = sorted(xs)
    if not s:
        return float("nan")
    h = (len(s) - 1) * q
    lo = math.floor(h)
    return s[lo] + (h - lo) * (s[min(lo + 1, len(s) - 1)] - s[lo])


def mad(xs: Sequence[float]) -> float:
    """R's mad(): 1.4826 * median absolute deviation."""
    m = median(xs)
    return 1.4826 * median([abs(x - m) for x in xs])


def welch_t(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    """Welch's unequal-variance t-test, two-sided. Needs >= 2 values per group."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), float("nan")
    va, vb = var(a) / na, var(b) / nb
    se2 = va + vb
    diff = mean(a) - mean(b)
    if se2 == 0:
        return (float("nan"), float("nan"), float("nan")) if diff == 0 else (math.copysign(math.inf, diff), float(na + nb - 2), 0.0)
    t = diff / math.sqrt(se2)
    df = se2 * se2 / ((va * va) / (na - 1) + (vb * vb) / (nb - 1))
    return t, df, t_two_sided_p(t, df)


def student_t(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    """Two-sample t-test with pooled variance, two-sided."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), float("nan")
    df = na + nb - 2
    sp = ((na - 1) * var(a) + (nb - 1) * var(b)) / df
    diff = mean(a) - mean(b)
    if sp == 0:
        return (float("nan"), float("nan"), float("nan")) if diff == 0 else (math.copysign(math.inf, diff), float(df), 0.0)
    t = diff / math.sqrt(sp * (1 / na + 1 / nb))
    return t, float(df), t_two_sided_p(t, df)


def one_sample_t(a: Sequence[float], mu: float = 0.0) -> tuple[float, float, float]:
    n = len(a)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    v = var(a)
    diff = mean(a) - mu
    if v == 0:
        return (float("nan"), float("nan"), float("nan")) if diff == 0 else (math.copysign(math.inf, diff), float(n - 1), 0.0)
    t = diff / math.sqrt(v / n)
    return t, float(n - 1), t_two_sided_p(t, n - 1)


def bh_adjust(ps: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg q-values; NaN p-values stay NaN and don't count towards m."""
    idx = [i for i, p in enumerate(ps) if p is not None and not math.isnan(p)]
    m = len(idx)
    out = [float("nan")] * len(ps)
    if not m:
        return out
    order = sorted(idx, key=lambda i: ps[i], reverse=True)
    running = 1.0
    for rank_from_top, i in enumerate(order):
        rank = m - rank_from_top
        running = min(running, ps[i] * m / rank)
        out[i] = running
    return out


def median_center(columns: list[list[float | None]]) -> list[list[float | None]]:
    """Shift each sample (column of log2 values) so all samples share the overall median of medians."""
    meds = [median([v for v in col if v is not None]) for col in columns]
    good = [m for m in meds if not math.isnan(m)]
    if not good:
        return columns
    target = median(good)
    return [[None if v is None else v - (m - target) for v in col] if not math.isnan(m) else col
            for col, m in zip(columns, meds, strict=True)]


# ------------------------------------------------ moderated t (limma eBayes) --
#
# Smyth (2004), Stat Appl Genet Mol Biol 3:3 — the empirical-Bayes moderated
# t-statistic behind limma's eBayes(). Each feature's variance is shrunk
# towards a prior fitted to all features' variances, which gives many more
# degrees of freedom when there are only ~3 replicates. Ported from limma's
# fitFDist / squeezeVar / eBayes (no covariate, no robust option) and checked
# against limma itself in tests/test_downstream.py.


def _polygamma_asym(x: float, order: int) -> float:
    """digamma (order 0), trigamma (1), tetragamma (2) for x > 0 via recurrence + asymptotic series."""
    acc = 0.0
    while x < 8:
        if order == 0:
            acc -= 1 / x
        elif order == 1:
            acc += 1 / (x * x)
        else:
            acc -= 2 / (x ** 3)
        x += 1
    x2 = 1 / (x * x)
    if order == 0:
        return acc + math.log(x) - 0.5 / x - x2 * (1 / 12 - x2 * (1 / 120 - x2 * (1 / 252 - x2 * (1 / 240 - x2 / 132))))
    if order == 1:
        return acc + 1 / x + 0.5 * x2 + (1 / x) * x2 * (1 / 6 - x2 * (1 / 30 - x2 * (1 / 42 - x2 * (1 / 30 - x2 * 5 / 66))))
    return acc - x2 - (1 / x) * x2 - x2 * x2 * (0.5 - x2 * (1 / 6 - x2 * (1 / 6 - x2 * (3 / 10 - x2 * 5 / 6))))


def digamma(x: float) -> float:
    return _polygamma_asym(x, 0)


def trigamma(x: float) -> float:
    return _polygamma_asym(x, 1)


def tetragamma(x: float) -> float:
    return _polygamma_asym(x, 2)


def trigamma_inverse(y: float) -> float:
    """Solve trigamma(x) = y (limma's trigammaInverse, Newton's method)."""
    if y > 1e7:
        return 1 / math.sqrt(y)
    if y < 1e-6:
        return 1 / y
    x = 0.5 + 1 / y
    for _ in range(50):
        tri = trigamma(x)
        dif = tri * (1 - tri / y) / tetragamma(x)
        x += dif
        if -dif / x < 1e-8:
            break
    return x


def _logmdigamma(x: float) -> float:
    return math.log(x) - digamma(x)


def brent_fmin(f, ax: float, bx: float, tol: float = 2.220446049250313e-16 ** 0.25) -> float:
    """R's optimize() (Brent 1973, R's src/library/stats/src/optimize.c), for identical minima."""
    c = (3.0 - math.sqrt(5.0)) * 0.5
    eps = math.sqrt(2.220446049250313e-16)
    a, b = ax, bx
    v = w = x = a + c * (b - a)
    d = e = 0.0
    fx = fv = fw = f(x)
    tol3 = tol / 3.0
    while True:
        xm = (a + b) * 0.5
        tol1 = eps * abs(x) + tol3
        t2 = tol1 * 2.0
        if abs(x - xm) <= t2 - (b - a) * 0.5:
            break
        p = q = r = 0.0
        if abs(e) > tol1:
            r = (x - w) * (fx - fv)
            q = (x - v) * (fx - fw)
            p = (x - v) * q - (x - w) * r
            q = (q - r) * 2.0
            if q > 0.0:
                p = -p
            else:
                q = -q
            r = e
            e = d
        if abs(p) >= abs(q * 0.5 * r) or p <= q * (a - x) or p >= q * (b - x):
            e = (b - x) if x < xm else (a - x)
            d = c * e
        else:
            d = p / q
            u = x + d
            if u - a < t2 or b - u < t2:
                d = tol1 if x < xm else -tol1
        if abs(d) >= tol1:
            u = x + d
        elif d > 0.0:
            u = x + tol1
        else:
            u = x - tol1
        fu = f(u)
        if fu <= fx:
            if u < x:
                b = x
            else:
                a = x
            v, w, x = w, x, u
            fv, fw, fx = fw, fx, fu
        else:
            if u < x:
                a = u
            else:
                b = u
            if fu <= fw or w == x:
                v, fv, w, fw = w, fw, u, fu
            elif fu <= fv or v == x or v == w:
                v, fv = u, fu
    return x


def _fit_f_dist_equal(xs: list[float], dfs: list[float]) -> tuple[float, float]:
    """limma fitFDist (method of moments), used when every feature has the same df."""
    xs = [max(v, 0.0) for v in xs]
    m = median(xs)
    if m == 0:
        m = 1.0
    xs = [max(v, 1e-5 * m) for v in xs]
    es = [math.log(v) + _logmdigamma(d / 2) for v, d in zip(xs, dfs, strict=True)]
    n = len(es)
    emean = sum(es) / n
    evar = sum((e - emean) ** 2 for e in es) / (n - 1)
    evar -= sum(trigamma(d / 2) for d in dfs) / n
    if evar > 0:
        d0 = 2 * trigamma_inverse(evar)
        return d0, math.exp(emean - _logmdigamma(d0 / 2))
    return math.inf, sum(xs) / len(xs)


def _fit_f_dist_unequal(xs: list[float], dfs: list[float]) -> tuple[float, float]:
    """limma fitFDistUnequalDF1 (maximum likelihood, non-robust), used when features have different df."""
    inf = [v for v in xs if v > 0]
    if len(inf) < 2:
        return math.nan, math.nan
    m = median(inf)
    xpos = [max(v, 1e-12 * m) for v in xs]
    d1 = [d / 2 for d in dfs]
    e = [math.log(v) + _logmdigamma(h) for v, h in zip(xpos, d1, strict=True)]
    w = [1 / trigamma(h) for h in d1]
    emean = sum(wi * ei for wi, ei in zip(w, e, strict=True)) / sum(w)
    d1x = [h * v for h, v in zip(d1, xpos, strict=True)]

    def minus_twice_loglik(par: float) -> float:
        d2 = par / (1 - par)
        d2s20 = d2 * math.exp(emean - _logmdigamma(d2))
        lg_d2 = math.lgamma(d2)
        tot = 0.0
        for h, hx in zip(d1, d1x, strict=True):
            tot += -(h + d2) * math.log1p(hx / d2s20) - h * math.log(d2s20) + math.lgamma(h + d2) - lg_d2
        return -2 * tot

    par = brent_fmin(minus_twice_loglik, 0.5, 0.9998)
    d2 = par / (1 - par)
    return 2 * d2, math.exp(emean - _logmdigamma(d2))


def fit_f_dist(s2: Sequence[float], df: Sequence[float]) -> tuple[float, float]:
    """(prior df d0, prior variance s0^2), as limma's squeezeVar chooses: moments when all df are equal,
    maximum likelihood when they differ (features with missing values)."""
    pairs = [(v, d) for v, d in zip(s2, df, strict=True) if d > 1e-15 and math.isfinite(v) and v > -1e-15]
    if len(pairs) < 3:
        return 0.0, float("nan")
    xs, dfs = [v for v, _ in pairs], [d for _, d in pairs]
    if min(dfs) == max(dfs):
        return _fit_f_dist_equal(xs, dfs)
    return _fit_f_dist_unequal(xs, dfs)


def moderated_t(coef: Sequence[float], s2: Sequence[float], df: Sequence[float],
                stdev_unscaled: Sequence[float]) -> tuple[list[float], list[float], list[float], float, float]:
    """limma eBayes for one coefficient. Inputs per feature; NaN coef = not tested.

    Returns (t, df_total, p, d0, s0^2).
    """
    ok = [i for i, c in enumerate(coef) if not math.isnan(c) and df[i] > 0 and math.isfinite(s2[i])]
    d0, s0 = fit_f_dist([s2[i] for i in ok], [df[i] for i in ok])
    if math.isnan(s0):  # too few features to estimate a prior: plain t-tests
        d0, s0 = 0.0, 1.0
    df_pooled = sum(df[i] for i in ok)
    n = len(coef)
    ts, dfs, ps = [math.nan] * n, [math.nan] * n, [math.nan] * n
    for i in ok:
        if math.isinf(d0):
            post = s0
        elif d0 == 0:
            post = s2[i]
        else:
            post = (df[i] * s2[i] + d0 * s0) / (df[i] + d0)
        dft = min(df[i] + d0, df_pooled)
        if post <= 0:
            continue
        t = coef[i] / (math.sqrt(post) * stdev_unscaled[i])
        ts[i], dfs[i], ps[i] = t, dft, t_two_sided_p(t, dft)
    return ts, dfs, ps, d0, s0
