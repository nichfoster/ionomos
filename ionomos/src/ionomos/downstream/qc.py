"""
Quality-control numbers for the report, following FragPipe-Analyst's plots:

    pca()                 plot_pca: top-n most variable complete features, prcomp(t(x)) (centred)
    correlation()         plot_correlation_heatmap: Pearson, complete observations, clustered
    cluster_order()       ComplexHeatmap's default clustering (hclust, euclidean, complete linkage)
    cv_by_condition()     plot_cvs: sd / mean of un-logged values per condition
    feature_numbers()     plot_feature_numbers: measured features per sample
    missing_pattern()     plot_missval_heatmap (rows with any missing value) + plotCumulativeMissingPercent
    box_stats()           value distributions per sample
    histogram()           p-value histograms, imputed vs measured values

Everything is plain Python; the heavy steps are bounded (pca: n_top features, clustering: a few hundred rows).
"""
from __future__ import annotations

import functools
import math
import sys
from itertools import repeat
from operator import add, mul, sub

from ionomos.downstream import stats

Matrix = list[list[float | None]]

JACOBI_SAMPLES = 60  # pca: up to this many samples, Jacobi on all components (the results before #163, bit for bit)


# ----------------------------------------------------------------------- PCA --


def _jacobi_eigen(a: list[list[float]], sweeps: int = 100) -> tuple[list[float], list[list[float]]]:
    """Eigenvalues/vectors of a small symmetric matrix (cyclic Jacobi). Vectors are columns of v."""
    n = len(a)
    a = [list(r) for r in a]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(i + 1, n))
        if off < 1e-22 * max(1e-300, sum(a[i][i] ** 2 for i in range(n))):
            break
        for p in range(n - 1):
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


def _gram(vectors: list[list[float]]) -> list[list[float]]:
    """All dot products, each summed in the vectors' order (the upper triangle mirrored: a * b == b * a)."""
    m = len(vectors)
    g = [[0.0] * m for _ in range(m)]
    for a in range(m):
        va = vectors[a]
        for b in range(a, m):
            g[a][b] = g[b][a] = sum(map(mul, va, vectors[b]))
    return g


def _tridiagonal(a: list[list[float]]) -> tuple[list[float], list[float], list[tuple[list[float], float]]]:
    """Householder reduction of a symmetric matrix: a = Q T Q', T = (diagonal d, off-diagonal e). Q is kept as
    its reflectors (v, beta): H = I - beta v v' acting on the entries after the reflector's position."""
    m = len(a)
    b = [list(r) for r in a]
    d, e, refl = [], [], []
    for _ in range(m - 2):
        row = b[0]
        d.append(row[0])
        x = row[1:]
        rest = [r[1:] for r in b[1:]]
        norm = math.sqrt(sum(map(mul, x, x)))
        if norm == 0.0:
            e.append(0.0)
            refl.append(([], 0.0))
            b = rest
            continue
        alpha = -math.copysign(norm, x[0])
        v = list(x)
        v[0] -= alpha
        beta = 2.0 / sum(map(mul, v, v))
        p = [beta * sum(map(mul, r, v)) for r in rest]
        kk = beta / 2 * sum(map(mul, p, v))
        w = list(map(sub, p, map(mul, repeat(kk), v)))
        b = [list(map(sub, map(sub, r, map(mul, repeat(vi), w)), map(mul, repeat(wi), v)))
             for r, vi, wi in zip(rest, v, w, strict=True)]
        e.append(alpha)
        refl.append((v, beta))
    if m >= 2:
        d += [b[0][0], b[1][1]]
        e.append(b[1][0])
    elif m == 1:
        d.append(b[0][0])
    return d, e, refl


def _tridiagonal_eigenvalue(d: list[float], e: list[float], index: int) -> float:
    """The index-th smallest eigenvalue (0-based) of the tridiagonal (d, e), by bisection on Sturm counts."""
    m = len(d)
    e2 = [x * x for x in e]
    norm = max(abs(d[i]) + (abs(e[i - 1]) if i else 0.0) + (abs(e[i]) if i < m - 1 else 0.0) for i in range(m))
    pivmin = sys.float_info.min * max(1.0, max(e2, default=0.0))
    lo, hi = -norm - 1e-300, norm + 1e-300

    def below(x):  # how many eigenvalues are < x
        c, piv = 0, 1.0
        for i in range(m):
            piv = d[i] - x - (e2[i - 1] / piv if i else 0.0)
            if abs(piv) < pivmin:
                piv = -pivmin
            if piv < 0:
                c += 1
        return c

    for _ in range(200):
        mid = (lo + hi) / 2
        if mid in (lo, hi) or hi - lo <= 4 * sys.float_info.epsilon * max(abs(lo), abs(hi)) + pivmin:
            break
        if below(mid) <= index:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _tridiagonal_solve(d: list[float], e: list[float], lam: float, y: list[float], tiny: float) -> list[float]:
    """(T - lam I) x = y, Gaussian elimination with partial pivoting (LAPACK dgtsv); zero pivots become tiny."""
    m = len(d)
    dg = [x - lam for x in d]
    du = list(e)
    dl = list(e)
    du2 = [0.0] * m
    b = list(y)
    for i in range(m - 1):
        if abs(dg[i]) >= abs(dl[i]):
            if dg[i] == 0.0:
                dg[i] = tiny
            f = dl[i] / dg[i]
            dg[i + 1] -= f * du[i]
            b[i + 1] -= f * b[i]
        else:
            f = dg[i] / dl[i]
            dg[i] = dl[i]
            tmp = dg[i + 1]
            dg[i + 1] = du[i] - f * tmp
            if i < m - 2:
                du2[i] = du[i + 1]
                du[i + 1] = -f * du2[i]
            du[i] = tmp
            b[i], b[i + 1] = b[i + 1], b[i] - f * b[i + 1]
    if dg[m - 1] == 0.0:
        dg[m - 1] = tiny
    x = [0.0] * m
    for i in range(m - 1, -1, -1):
        s = b[i]
        if i + 1 < m:
            s -= du[i] * x[i + 1]
        if i + 2 < m:
            s -= du2[i] * x[i + 2]
        x[i] = s / dg[i]
    return x


def _top_eigen(a: list[list[float]], k: int) -> tuple[list[float], list[list[float]]]:
    """The k largest eigenvalues of a symmetric matrix (descending) and unit eigenvectors: Householder
    tridiagonalisation, bisection, inverse iteration (orthogonalised against the earlier vectors), back-transform.
    O(m^3 / 3) multiply-adds for the reduction, run in C by map; Jacobi's sweeps are minutes at m = 500 (#163)."""
    m = len(a)
    d, e, refl = _tridiagonal(a)
    norm = max(abs(x) for x in d + e) or 1.0
    tiny = sys.float_info.epsilon * norm
    lams, vecs = [], []
    seed = 12345
    for idx in range(m - 1, m - 1 - k, -1):
        lam = _tridiagonal_eigenvalue(d, e, idx)
        y = []
        for _ in range(m):  # a fixed pseudo-random start (an LCG), never orthogonal to the answer in practice
            seed = (seed * 1103515245 + 12345) % 2 ** 31
            y.append(seed / 2 ** 31 - 0.5)
        for _ in range(6):
            y = _tridiagonal_solve(d, e, lam, y, tiny)
            for u in vecs:
                c = sum(map(mul, u, y))
                y = list(map(sub, y, map(mul, repeat(c), u)))
            nrm = math.sqrt(sum(map(mul, y, y))) or 1.0
            y = [x / nrm for x in y]
        lams.append(lam)
        vecs.append(y)
    out = []
    for y in vecs:
        z = list(y)
        for pos in range(len(refl) - 1, -1, -1):
            v, beta = refl[pos]
            if not v:
                continue
            seg = z[pos + 1:]
            c = beta * sum(map(mul, v, seg))
            z[pos + 1:] = map(sub, seg, map(mul, repeat(c), v))
        out.append(z)
    return lams, out


def pca(values: Matrix, n_top: int = 500, scale: bool = False) -> dict:
    """{"scores": [[PC1, PC2, ...] per sample], "percent": [% variance per PC], "n": features used}.

    Complete rows only, the n_top with the largest standard deviation (plot_pca's n = 500). Up to JACOBI_SAMPLES
    samples, cyclic Jacobi on the samples x samples Gram matrix; above that only the top components, from the smaller
    of the two Gram matrices (_top_eigen, #163)."""
    rows = [r for r in values if r and all(v is not None for v in r)]
    ns = len(values[0]) if values else 0
    if len(rows) < 2 or ns < 2:
        return {"scores": [], "percent": [], "n": len(rows)}
    sd = [math.sqrt(stats.var(r)) for r in rows]
    order = sorted(range(len(rows)), key=lambda i: -sd[i])[: n_top or len(rows)]
    x = []  # features x samples, centred per feature (prcomp centres each column of t(x))
    for i in order:
        r = rows[i]
        mu = sum(r) / ns
        d = [v - mu for v in r]
        if scale:
            s = sd[i] or 1.0
            d = [v / s for v in d]
        x.append(d)
    npc = min(ns, 6)
    if ns <= JACOBI_SAMPLES:
        g = _gram([[f[a] for f in x] for a in range(ns)])  # samples x samples
        lam, vec = _jacobi_eigen(g)
        idx = sorted(range(ns), key=lambda k: -lam[k])
        lam = [max(lam[k], 0.0) for k in idx]
        total = sum(lam) or 1.0
        scores = [[vec[s][idx[k]] * math.sqrt(lam[k]) for k in range(npc)] for s in range(ns)]
    elif len(x) < ns:  # fewer features than samples: the features x features matrix has the same eigenvalues
        g = _gram(x)
        total = sum(g[f][f] for f in range(len(x))) or 1.0
        top, vecs = _top_eigen(g, min(npc, len(x)))
        lam = [max(v, 0.0) for v in top] + [0.0] * (npc - len(top))
        cols = [[f[s] for f in x] for s in range(ns)]
        # the scores are x' u: sqrt(lambda) times the samples' eigenvector
        scores = [[sum(map(mul, cols[s], u)) for u in vecs] + [0.0] * (npc - len(vecs)) for s in range(ns)]
    else:
        g = _gram([[f[a] for f in x] for a in range(ns)])
        total = sum(g[s][s] for s in range(ns)) or 1.0  # the trace: the sum of all eigenvalues
        top, vecs = _top_eigen(g, npc)
        lam = [max(v, 0.0) for v in top]
        scores = [[vecs[k][s] * math.sqrt(lam[k]) for k in range(npc)] for s in range(ns)]
    for k in range(npc):  # a stable sign: the sample with the largest |score| is positive
        big = max(range(ns), key=lambda s: abs(scores[s][k]))
        if scores[big][k] < 0:
            for s in range(ns):
                scores[s][k] = -scores[s][k]
    return {"scores": scores, "percent": [100 * v / total for v in lam[:npc]], "n": len(x)}


# ----------------------------------------------------------------- clustering --


def _dist_matrix(rows: list[list[float | None]]) -> list[list[float]]:
    n = len(rows)
    d = [[0.0] * n for _ in range(n)]
    complete = [all(v is not None for v in r) for r in rows]
    for i in range(n):
        ri = rows[i]
        for j in range(i + 1, n):
            rj = rows[j]
            if complete[i] and complete[j] and len(ri) == len(rj) and ri:
                # the loop below, run in C: the same squares added left to right from 0.0
                acc = functools.reduce(add, map(pow, map(sub, ri, rj), repeat(2)), 0.0)
                d[i][j] = d[j][i] = math.sqrt(acc * len(ri) / len(ri))
                continue
            acc, k = 0.0, 0
            for a, b in zip(ri, rj, strict=False):
                if a is not None and b is not None:
                    acc += (a - b) ** 2
                    k += 1
            # R's dist() with missing values: scale up the sum for the coordinates used
            v = math.sqrt(acc * len(ri) / k) if k else math.inf
            d[i][j] = d[j][i] = v
    finite = [v for r in d for v in r if math.isfinite(v)]
    top = max(finite) if finite else 1.0
    for r in d:
        for j, v in enumerate(r):
            if not math.isfinite(v):
                r[j] = top
    return d


def hclust(rows: list[list[float | None]], method: str = "complete") -> list[tuple[int, int, float]]:
    """Agglomerative clustering (nearest-neighbour chain, O(n^2)). Returns R-style merges:
    (a, b, height) with negative numbers = single rows (-1 = row 0), positive = earlier merge (1-based)."""
    n = len(rows)
    if n < 2:
        return []
    d = _dist_matrix(rows)
    size = [1] * n
    active = set(range(n))
    chain: list[int] = []
    raw: list[tuple[float, int, int]] = []  # (height, rep a, rep b) with representative rows
    rep = list(range(n))
    while len(active) > 1:
        if not chain:
            chain.append(min(active))
        a = chain[-1]
        prev = chain[-2] if len(chain) > 1 else None
        best, bd = None, math.inf
        for x in active:
            if x == a:
                continue
            v = d[a][x]
            if v < bd or (v == bd and x == prev):
                best, bd = x, v
        if best == prev:
            chain.pop()
            chain.pop()
            raw.append((bd, rep[a], rep[best]))
            # merge best into a (Lance-Williams update)
            for x in active:
                if x in (a, best):
                    continue
                if method == "average":
                    nv = (size[a] * d[a][x] + size[best] * d[best][x]) / (size[a] + size[best])
                else:
                    nv = max(d[a][x], d[best][x])
                d[a][x] = d[x][a] = nv
            size[a] += size[best]
            rep[a] = min(rep[a], rep[best])
            active.discard(best)
        else:
            chain.append(best)
    raw.sort(key=lambda t: t[0])
    parent = list(range(n))
    label = {i: -(i + 1) for i in range(n)}  # current label of each set root

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    merges = []
    for k, (h, ra, rb) in enumerate(raw, start=1):
        a, b = find(ra), find(rb)
        la, lb = label[a], label[b]
        # R's convention: singletons first; two singletons / two clusters in increasing order
        if (la > 0) == (lb > 0):
            first, second = (la, lb) if abs(la) < abs(lb) else (lb, la)
        else:
            first, second = (la, lb) if la < 0 else (lb, la)
        merges.append((first, second, h))
        parent[b] = a
        label[a] = k
    return merges


def order_from_merges(merges: list[tuple[int, int, float]], n: int) -> list[int]:
    if not merges:
        return list(range(n))
    out: list[int] = []
    stack = [len(merges)]
    while stack:
        x = stack.pop()
        if x < 0:
            out.append(-x - 1)
        else:
            a, b, _ = merges[x - 1]
            stack.append(b)
            stack.append(a)
    return out


def cluster_order(rows: list[list[float | None]], method: str = "complete") -> list[int]:
    return order_from_merges(hclust(rows, method), len(rows))


# ---------------------------------------------------------------- correlation --


def pearson(a: list[float | None], b: list[float | None]) -> float | None:
    pairs = [(x, y) for x, y in zip(a, b, strict=True) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    mx = sum(p[0] for p in pairs) / len(pairs)
    my = sum(p[1] for p in pairs) / len(pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def correlation(values: Matrix) -> dict:
    """cor(x, use = "complete.obs") between samples, and a clustered sample order."""
    ns = len(values[0]) if values else 0
    rows = [r for r in values if all(v is not None for v in r)]
    cols = [[r[j] for r in rows] for j in range(ns)]
    centred, norms = [], []
    for c in cols:  # complete rows only, so each sample is centred once and r is a dot product
        mu = sum(c) / len(c) if c else 0.0
        d = [x - mu for x in c]
        centred.append(d)
        norms.append(math.sqrt(sum(x * x for x in d)))
    mat = [[1.0 if i == j else None for j in range(ns)] for i in range(ns)]
    if len(rows) >= 3:
        for i in range(ns):
            for j in range(i + 1, ns):
                if norms[i] > 0 and norms[j] > 0:
                    mat[i][j] = mat[j][i] = sum(map(mul, centred[i], centred[j])) / (norms[i] * norms[j])
    order = cluster_order([[v if v is not None else 0.0 for v in r] for r in mat]) if ns > 1 else list(range(ns))
    return {"matrix": mat, "order": order, "complete_rows": len(rows)}


# ------------------------------------------------------------ CV, missingness --


def cv_by_condition(values: Matrix, samples: list[str], condition: dict[str, str]) -> dict:
    """{condition: {"cvs": [...], "median": m}} (sd / mean of 2^x per feature, needs 2 values)."""
    out = {}
    conds = []
    for s in samples:
        if condition[s] not in conds:
            conds.append(condition[s])
    for c in conds:
        idx = [j for j, s in enumerate(samples) if condition[s] == c]
        cvs = []
        for r in values:
            xs = [2 ** r[j] for j in idx if r[j] is not None]
            if len(xs) >= 2:
                mu = sum(xs) / len(xs)
                if mu > 0:
                    cvs.append(math.sqrt(stats.var(xs)) / mu)
        out[c] = {"cvs": cvs, "median": stats.median(cvs) if cvs else None}
    return out


def feature_numbers(values: Matrix) -> list[int]:
    ns = len(values[0]) if values else 0
    return [sum(1 for r in values if r[j] is not None) for j in range(ns)]


def missing_pattern(values: Matrix, samples: list[str], condition: dict[str, str], max_rows: int = 2000) -> dict:
    """Rows with at least one missing value as 0/1 strings (1 = measured), grouped by pattern,
    plus the cumulative-missingness curve."""
    ns = len(samples)
    pat = []
    for i, r in enumerate(values):
        if any(v is None for v in r):
            pat.append((i, "".join("0" if v is None else "1" for v in r)))
    counts = sorted(sum(v is None for v in r) for r in values)
    curve = []  # (missing fraction, features with at most that much missing)
    for k in range(ns + 1):
        curve.append((k / ns if ns else 0, sum(1 for c in counts if c <= k)))
    complete = sum(1 for c in counts if c == 0)
    half = sum(1 for c in counts if ns and c / ns <= 0.5)
    # the heatmap: sort by the pattern (conditions together), cap rows evenly
    pat.sort(key=lambda t: (t[1].count("0"), t[1]))
    step = max(1, math.ceil(len(pat) / max_rows)) if pat else 1
    shown = pat[::step]
    return {"rows": [p for _, p in shown], "row_ids": [i for i, _ in shown], "total": len(pat), "curve": curve,
            "complete": complete, "under_half": half, "all": len(values)}


def box_stats(values: Matrix) -> list[dict | None]:
    ns = len(values[0]) if values else 0
    out = []
    for j in range(ns):
        xs = sorted(r[j] for r in values if r[j] is not None)
        if not xs:
            out.append(None)
            continue
        q1, q2, q3 = (stats.quantile(xs, p) for p in (0.25, 0.5, 0.75))
        iqr = q3 - q1
        lo = min(x for x in xs if x >= q1 - 1.5 * iqr)
        hi = max(x for x in xs if x <= q3 + 1.5 * iqr)
        out.append({"q1": q1, "median": q2, "q3": q3, "lo": lo, "hi": hi, "min": xs[0], "max": xs[-1], "n": len(xs)})
    return out


def histogram(xs: list[float], lo: float, hi: float, bins: int = 20) -> list[int]:
    out = [0] * bins
    w = (hi - lo) / bins or 1.0
    for x in xs:
        if x is None or math.isnan(x) or x < lo or x > hi:
            continue
        out[min(bins - 1, int((x - lo) / w))] += 1
    return out
