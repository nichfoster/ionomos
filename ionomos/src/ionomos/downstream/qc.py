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

import math

from ionomos.downstream import stats

Matrix = list[list[float | None]]


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


def pca(values: Matrix, n_top: int = 500, scale: bool = False) -> dict:
    """{"scores": [[PC1, PC2, ...] per sample], "percent": [% variance per PC], "n": features used}.

    Complete rows only, the n_top with the largest standard deviation (plot_pca's n = 500)."""
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
    g = [[sum(f[a] * f[b] for f in x) for b in range(ns)] for a in range(ns)]  # samples x samples
    lam, vec = _jacobi_eigen(g)
    idx = sorted(range(ns), key=lambda k: -lam[k])
    lam = [max(lam[k], 0.0) for k in idx]
    total = sum(lam) or 1.0
    npc = min(ns, 6)
    scores = [[vec[s][idx[k]] * math.sqrt(lam[k]) for k in range(npc)] for s in range(ns)]
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
    for i in range(n):
        ri = rows[i]
        for j in range(i + 1, n):
            rj = rows[j]
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
                    mat[i][j] = mat[j][i] = sum(a * b for a, b in zip(centred[i], centred[j], strict=True)) / (norms[i] * norms[j])
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
