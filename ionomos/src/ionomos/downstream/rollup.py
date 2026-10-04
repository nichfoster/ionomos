"""
Protein roll-up: peptide / precursor / fragment intensities of one protein -> one value per sample (D76).

    rollup.summarise(rows, "median_polish")   # Tukey median polish (MSstats' TMP; engines.median_polish)
    rollup.summarise(rows, "maxlfq")          # MaxLFQ (Cox et al. 2014), scaled to the summed intensity
    rollup.maxlfq(rows, scale="mean")         # MaxLFQ as R's iq::maxLFQ() scales it (the golden test)

rows is features x samples of log2 intensities (None = missing); the result is one log2 value per sample (None
where the protein has no value). Pure Python (no numpy): the least-squares step is a small Gaussian elimination.

MaxLFQ, as Cox et al. (Mol Cell Proteomics 13:2513, 2014) describe it and iq / DIA-NN implement it:

1.  For every pair of samples (i, j), the median of the log-ratios X[f, j] - X[f, i] over the features f measured
    in both (at least `min_ratio_count` of them; 1, as iq and DIA-NN, where MaxQuant's default is 2).
2.  The samples linked by such ratios form connected components. A sample that shares no feature with another is
    a component of its own (its value: the median feature in iq, its summed intensity here); a sample with no
    value stays missing.
3.  Per component, the profile w minimises sum over pairs (w[j] - w[i] - r[i, j])^2. That fixes w up to a
    constant, which is set by the scaling:
      "sum"   (Cox et al., MaxQuant, the default) the profile's summed linear intensity equals the summed intensity
              of every feature value in the component's samples
      "mean"  (iq::maxLFQ) the mean of w equals the mean of every log2 value in the component's samples

Components are not put on one scale with each other: a protein whose samples fall into two groups that share no
peptide gets two separate profiles (iq marks this in its annotation; here `components()` says so).
"""
from __future__ import annotations

import math
import statistics

ROLLUPS = ("median_polish", "maxlfq")
ROLLUP_SETTINGS = ("auto", *ROLLUPS)   # analysis.rollup: auto = each loader's own default (engines.py)
LABELS = {"median_polish": "Tukey median polish", "maxlfq": "MaxLFQ"}
SCALES = ("sum", "mean")

Matrix = list[list[float | None]]


def components(rows: Matrix, min_ratio_count: int = 1) -> list[int | None]:
    """Per sample, the index of its connected component (samples linked by >= min_ratio_count shared features);
    None for a sample with no value. Components are numbered 0, 1, ... in order of their first sample."""
    if not rows:
        return []
    n = len(rows[0])
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    present = [any(r[j] is not None for r in rows) for j in range(n)]
    if min_ratio_count <= 1:
        for r in rows:
            obs = [j for j, v in enumerate(r) if v is not None]
            for j in obs[1:]:
                ra, rb = find(obs[0]), find(j)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    else:
        for i in range(n):
            for j in range(i + 1, n):
                if sum(1 for r in rows if r[i] is not None and r[j] is not None) >= min_ratio_count:
                    ra, rb = find(i), find(j)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    label: dict[int, int] = {}
    out: list[int | None] = []
    for j in range(n):
        if not present[j]:
            out.append(None)
            continue
        out.append(label.setdefault(find(j), len(label)))
    return out


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """x with a x = b, by Gaussian elimination with partial pivoting (a square and non-singular)."""
    n = len(b)
    m = [list(row) + [bi] for row, bi in zip(a, b, strict=True)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-300:
            raise ValueError("singular system")
        m[c], m[p] = m[p], m[c]
        piv = m[c]
        for r in range(c + 1, n):
            f = m[r][c] / piv[c]
            if f:
                row = m[r]
                for k in range(c, n + 1):
                    row[k] -= f * piv[k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def _log2_sum(vals: list[float]) -> float:
    """log2 of the summed linear intensity of log2 values, without overflow."""
    top = max(vals)
    return top + math.log2(sum(2.0 ** (v - top) for v in vals))


def maxlfq(rows: Matrix, scale: str = "sum", min_ratio_count: int = 1) -> list[float | None]:
    """MaxLFQ of features x samples (log2, None = missing) -> one log2 value per sample (see the module text)."""
    if scale not in SCALES:
        raise ValueError(f"scale must be one of {', '.join(SCALES)}")
    if not rows:
        return []
    n = len(rows[0])
    comp = components(rows, min_ratio_count)
    out: list[float | None] = [None] * n
    for c in sorted({x for x in comp if x is not None}):
        cols = [j for j in range(n) if comp[j] == c]
        vals = [r[j] for r in rows for j in cols if r[j] is not None]
        if len(cols) == 1:
            j = cols[0]
            out[j] = statistics.median(vals) if scale == "mean" else _log2_sum(vals)
            continue
        k = len(cols)
        ata = [[0.0] * k for _ in range(k)]
        atb = [0.0] * k
        for a in range(k - 1):
            ia = cols[a]
            for b in range(a + 1, k):
                ib = cols[b]
                d = [r[ib] - r[ia] for r in rows if r[ia] is not None and r[ib] is not None]
                if len(d) < min_ratio_count or not d:
                    continue
                med = statistics.median(d)
                ata[a][b] = ata[b][a] = -1.0
                ata[a][a] += 1.0
                ata[b][b] += 1.0
                atb[a] -= med
                atb[b] += med
        # the profile with mean(w) = mean of the component's values, as iq::maxLFQ (lsfit of the same system)
        mean = sum(vals) / len(vals)
        big = [[2.0 * x for x in row] + [1.0] for row in ata] + [[1.0] * k + [0.0]]
        w = solve(big, [2.0 * x for x in atb] + [mean * k])[:k]
        if scale == "sum":
            shift = _log2_sum(vals) - _log2_sum(w)
            w = [x + shift for x in w]
        for j, x in zip(cols, w, strict=True):
            out[j] = x
    return out


def summarise(rows: Matrix, method: str) -> list[float | None]:
    """One protein's features x samples (log2) -> per-sample log2 values by `method` (ROLLUPS)."""
    if method == "maxlfq":
        return maxlfq(rows)
    if method == "median_polish":
        from ionomos.downstream.engines import median_polish

        return median_polish(rows)
    raise ValueError(f"unknown roll-up {method!r} (known: {', '.join(ROLLUPS)})")


def resolve(asked: str | None, default: str) -> str:
    """analysis.rollup -> the method a loader uses: 'auto' (or nothing) is the loader's own default."""
    asked = (asked or "auto").strip().lower()
    return default if asked == "auto" else asked


def describe(method: str) -> str:
    """For notes and the report's Data source: how the features were rolled up to proteins."""
    if method == "maxlfq":
        return ("MaxLFQ (Cox et al. 2014: median log-ratios of shared features between samples, least squares, "
                "scaled to the summed intensity)")
    return "Tukey median polish (MSstats' TMP)"
