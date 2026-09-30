"""
DEqMS: a feature's prior variance depends on how many peptides it was quantified from.

limma's eBayes gives every protein the same prior variance. Proteins quantified
from many peptides (or PSMs) have smaller variances than one-hit proteins, so
one prior is too large for the former and too small for the latter. DEqMS
(Zhu et al., Mol. Cell. Proteomics 2020, doi:10.1074/mcp.TIR119.001646) fits
the log residual variance against log2(peptide count) and uses the fitted
value as each protein's prior variance. This is a port of DEqMS 1.30
spectraCounteBayes(fit, fit.method = "loess"):

    logVAR = log(s²);  x = log2(count)
    loess(logVAR ~ x, span = 0.75)                    R's loess: degree 2, tricube, surface = "interpolate"
    eg     = logVAR − digamma(df/2) + log(df/2)       (the expected log of a scaled chi-square)
    egpred = fitted − digamma(df/2) + log(df/2)
    d0     = the 0.1-grid value where trigamma(d0/2) ≈ mean((eg − egpred)² − trigamma(df/2))
    s0²    = exp(egpred + digamma(d0/2) − log(d0/2))  per feature
    post   = (d0·s0² + df·s²) / (d0 + df),  post df = d0 + df

loess() here reproduces R's (Cleveland, Grosse & Shyu's dloess) for one
predictor: a k-d tree of cells holding at most floor(n·span·0.2) points, local
quadratic fits (value and slope) at the cell vertices, cubic Hermite
interpolation in between. Checked against R's loess and DEqMS in
tests/test_design.py (golden files in tests/golden/design/).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ionomos.downstream import stats

# -------------------------------------------------------------------- loess --


def _local_fit(xs: list[float], ys: list[float], at: float, q: int) -> tuple[float, float]:
    """Weighted least-squares quadratic around `at` (tricube weights on the q nearest points): (value, slope)."""
    dist = sorted(abs(x - at) for x in xs)
    rho = dist[q - 1]
    s = [[0.0] * 3 for _ in range(3)]
    b = [0.0] * 3
    for x, y in zip(xs, ys, strict=True):
        d = abs(x - at)
        if d >= rho:
            continue
        w = (1 - (d / rho) ** 3) ** 3
        u = x - at
        basis = (1.0, u, u * u)
        for i in range(3):
            b[i] += w * basis[i] * y
            for j in range(3):
                s[i][j] += w * basis[i] * basis[j]
    coef = _solve(s, b)
    return coef[0], coef[1]


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a small symmetric system; columns that are (numerically) dependent get 0 (a pseudo-inverse
    in spirit: with a single distinct x the slope and curvature are 0 and the value is the weighted mean)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    scale = [math.sqrt(a[i][i]) if a[i][i] > 0 else 1.0 for i in range(n)]
    for i in range(n):  # equilibrate, as dloess scales its columns
        for j in range(n):
            m[i][j] /= scale[i] * scale[j]
        m[i][n] /= scale[i]
    used = []
    for col in range(n):
        piv = m[col][col]
        if abs(piv) < 1e-10:
            continue
        used.append(col)
        for r in range(n):
            if r != col and m[r][col] != 0:
                f = m[r][col] / piv
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    out = [0.0] * n
    for col in used:
        out[col] = m[col][n] / m[col][col] / scale[col]
    return out


def _kd_cells(xs: list[float], span: float, cell: float = 0.2) -> tuple[list[float], list[tuple[float, float]]]:
    """dloess' k-d tree for one predictor: (vertices, leaf cells as (low, high) vertex pairs)."""
    x = sorted(xs)
    n = len(x)
    fc = math.floor(n * span * cell)
    lo, hi = x[0], x[-1]
    mu = 0.005 * max(hi - lo, 1e-10 * max(abs(lo), abs(hi)) + 1e-30)  # ehg126: expand the box a little
    verts = [lo - mu, hi + mu]
    leaves = []
    todo = [(1, n, verts[0], verts[1])]  # 1-based inclusive ranges, as ehg124
    while todo:
        lo_i, u, vlo, vhi = todo.pop(0)
        if u - lo_i + 1 <= fc:
            leaves.append((vlo, vhi))
            continue
        m = (lo_i + u) // 2
        off = 0
        while lo_i <= m + off < u:  # all ties go with one son: the nearest position where the value changes
            if x[m + off - 1] == x[m + off]:
                off = -off
                if off >= 0:
                    off += 1
                continue
            m += off
            break
        xi = x[m - 1]
        if xi == vlo or xi == vhi:
            leaves.append((vlo, vhi))
            continue
        verts.append(xi)
        todo += [(lo_i, m, vlo, xi), (m + 1, u, xi, vhi)]
    return verts, leaves


def loess(xs: list[float], ys: list[float], span: float = 0.75) -> list[float]:
    """fitted(loess(y ~ x, span = span)) for one predictor with R's defaults (degree 2, gaussian,
    surface = "interpolate", cell = 0.2)."""
    n = len(xs)
    if n == 0:
        return []
    q = min(n, math.floor(n * span + 1e-5))
    verts, leaves = _kd_cells(xs, span)
    vval = {v: _local_fit(xs, ys, v, q) for v in verts}
    leaves.sort()
    starts = [a for a, _ in leaves]
    out = []
    for x in xs:
        k = _bisect(starts, x)
        a, b = leaves[k]
        if x > b and k + 1 < len(leaves):
            a, b = leaves[k + 1]
        (f0, d0), (f1, d1) = vval[a], vval[b]
        w = b - a
        h = (x - a) / w
        phi0, phi1 = (1 - h) ** 2 * (1 + 2 * h), h * h * (3 - 2 * h)
        psi0, psi1 = h * (1 - h) ** 2, -h * h * (1 - h)
        out.append(phi0 * f0 + phi1 * f1 + (psi0 * d0 + psi1 * d1) * w)
    return out


def _bisect(starts: list[float], x: float) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= x:
            lo = mid
        else:
            hi = mid - 1
    return lo


# ------------------------------------------------------------------- DEqMS --

TOO_FEW = "fewer than 20 features with a peptide count and residual df, or fewer than 3 different counts"


@dataclass
class CountPrior:
    post: list[float]      # posterior variance per row (NaN where not used)
    dft: list[float]       # d0 + df
    prior: list[float]     # s0² per row (NaN where not used)
    d0: float
    used: list[int]        # rows moderated by DEqMS; the others keep limma's prior


def spectra_count_ebayes(s2: list[float], df: list[int | float], counts: list[int | None]) -> CountPrior | None:
    """DEqMS spectraCounteBayes(fit.method = "loess") on the rows with a count >= 1, residual df and a
    positive variance (DEqMS itself stops on a missing count). None when too few rows to fit a trend."""
    used = [i for i, (v, d, c) in enumerate(zip(s2, df, counts, strict=True))
            if c is not None and c >= 1 and d > 0 and v is not None and math.isfinite(v) and v > 0]
    if len(used) < 20 or len({counts[i] for i in used}) < 3:
        return None
    log_var = [math.log(s2[i]) for i in used]
    y_pred = loess([math.log2(counts[i]) for i in used], log_var, span=0.75)
    half = [df[i] / 2 for i in used]
    shift = [math.log(h) - stats.digamma(h) for h in half]
    eg = [lv + s for lv, s in zip(log_var, shift, strict=True)]
    egpred = [yp + s for yp, s in zip(y_pred, shift, strict=True)]
    mean_fct = sum((a - b) ** 2 - stats.trigamma(h) for a, b, h in zip(eg, egpred, half, strict=True)) / len(used)
    grid, dist = [], []
    for i in range(1, len(used) * 10 + 1):  # DEqMS: d0 on a 0.1 grid, stopping once the distance grows
        grid.append(i / 10)
        dist.append(abs(mean_fct - stats.trigamma(grid[-1] / 2)))
        if i > 2 and dist[i - 3] < dist[i - 2]:
            break
    d0 = grid[dist.index(min(dist))]
    n = len(s2)
    post, dft, prior = [math.nan] * n, [math.nan] * n, [math.nan] * n
    for k, i in enumerate(used):
        s02 = math.exp(egpred[k] + stats.digamma(d0 / 2) - math.log(d0 / 2))
        prior[i] = s02
        post[i] = (d0 * s02 + df[i] * s2[i]) / (d0 + df[i])
        dft[i] = d0 + df[i]
    return CountPrior(post, dft, prior, d0, used)
