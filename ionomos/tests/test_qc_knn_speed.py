"""#163 / #164: the QC stage's PCA and knn imputation on large tables, fast and with the same numbers.

The timing bounds are generous (shared Windows CI runners are slow); before the fixes these took minutes."""
from __future__ import annotations

import math
import random
import time

import pytest

from ionomos.downstream import fpa, qc

# ------------------------------------------------------------------------ knn --


def _table(nf: int, ns: int, seed: int, kind: str = "real", missing: float = 0.2) -> list[list[float | None]]:
    rng = random.Random(seed)
    effect = [rng.gauss(0, 0.3) for _ in range(ns)]
    out = []
    for _ in range(nf):
        if kind == "iid":
            row = [rng.gauss(0, 1) for _ in range(ns)]
        else:  # proteins: an abundance, sample effects, replicate noise; low abundance missing more often
            mu = rng.gauss(24, 2.5)
            row = [mu + effect[j] + rng.gauss(0, 0.4) for j in range(ns)]
        p = missing * (1.6 if kind == "mnar" and mu < 23 else 1.0) if kind != "iid" else missing
        out.append([None if rng.random() < p else v for v in row])
    return out


def _variants():
    yield "real", _table(300, 12, 1)
    yield "iid", _table(300, 12, 2, "iid")
    yield "mnar", _table(300, 12, 3, "mnar")
    t = _table(300, 12, 4)
    for i in range(0, 300, 7):  # duplicated rows: exact ties, broken by row order
        t[i] = list(t[(i * 3) % 300])
    yield "ties", t
    t = _table(300, 12, 5)
    rng = random.Random(5)
    for r in t:  # a nearly empty sample, an empty one, rows with almost nothing
        if rng.random() < 0.97:
            r[1] = None
        r[2] = None
    t[10] = [None] * 11 + [20.0]
    t[11] = [None] * 12
    yield "sparse", t
    yield "rounded", [[None if v is None else round(v, 1) for v in r] for r in _table(300, 12, 6, missing=0.3)]
    yield "many samples", _table(220, 30, 7)
    yield "tiny", _table(25, 4, 8)


@pytest.mark.parametrize("name, table", list(_variants()), ids=[n for n, _ in _variants()])
def test_knn_search_gives_the_reference_numbers_bit_for_bit(monkeypatch, name, table):
    monkeypatch.setattr(fpa, "KNN_SCAN_ROWS", 0)  # the pruned search even on small tables
    fast = [list(r) for r in table]
    ref = [list(r) for r in table]
    fpa._impute_knn(fast)
    fpa._impute_knn_reference(ref)
    assert fast == ref


def test_knn_with_non_finite_values_uses_the_reference(monkeypatch):
    monkeypatch.setattr(fpa, "KNN_SCAN_ROWS", 0)
    t = _table(60, 6, 9)
    t[3][0] = math.inf
    fast, ref = [list(r) for r in t], [list(r) for r in t]
    fpa._impute_knn(fast)
    fpa._impute_knn_reference(ref)
    assert repr(fast) == repr(ref)


def test_knn_imputation_of_a_large_table_is_quick():
    t = _table(3000, 12, 10)
    t0 = time.perf_counter()
    fpa._impute_knn(t)  # the all-pairs reference: ~30 s here, quadratic (about 4 min at 10,000 rows)
    assert time.perf_counter() - t0 < 30
    assert all(v is not None for r in t for v in r)


# ------------------------------------------------------------------------ PCA --


def _samples(nf: int, ns: int, seed: int, groups: int = 4) -> list[list[float]]:
    rng = random.Random(seed)
    eff = [[rng.gauss(0, 1) for _ in range(groups)] for _ in range(nf)]
    return [[20 + e[s % groups] + rng.gauss(0, 0.5) for s in range(ns)] for e in eff]


def _close(a: dict, b: dict, tol: float) -> None:
    assert a["n"] == b["n"] and len(a["scores"]) == len(b["scores"])
    assert a["percent"] == pytest.approx(b["percent"], abs=1e-9)
    keep = [k for k, p in enumerate(a["percent"]) if p > 1e-9]  # null components are rounding noise in Jacobi
    for ra, rb in zip(a["scores"], b["scores"], strict=True):
        assert [ra[k] for k in keep] == pytest.approx([rb[k] for k in keep], abs=tol)


@pytest.mark.parametrize("nf, ns", [(200, 30), (12, 40), (3, 20), (50, 50), (5, 8)])
def test_top_components_match_jacobi(monkeypatch, nf, ns):
    for seed in range(3):
        v = _samples(nf, ns, seed)
        jacobi = qc.pca(v)
        monkeypatch.setattr(qc, "JACOBI_SAMPLES", 0)
        top = qc.pca(v)
        monkeypatch.setattr(qc, "JACOBI_SAMPLES", 60)
        _close(jacobi, top, 1e-8)


def test_pca_matches_prcomp_without_jacobi(monkeypatch):
    monkeypatch.setattr(qc, "JACOBI_SAMPLES", 0)
    res = qc.pca([[1.0, 2, 3, 4], [2.0, 1, 4, 3], [0.0, 0, 1, 5]], n_top=500)  # as test_fpa's prcomp case
    assert [round(v, 2) for v in res["percent"][:3]] == [82.94, 14.32, 2.74]
    assert [s[0] for s in res["scores"]] == pytest.approx([-2.080051, -1.912282, 0.201526, 3.790807], abs=1e-6)


def test_top_eigen_handles_repeated_and_zero_eigenvalues():
    rng = random.Random(3)
    lam = [5.0, 5.0, 5.0, 2.0, 0.0, 0.0, -1.0]
    m = len(lam)
    q = [[rng.gauss(0, 1) for _ in range(m)] for _ in range(m)]  # a random orthonormal basis
    for i in range(m):
        for j in range(i):
            c = sum(a * b for a, b in zip(q[i], q[j], strict=True))
            q[i] = [a - c * b for a, b in zip(q[i], q[j], strict=True)]
        nrm = math.sqrt(sum(a * a for a in q[i]))
        q[i] = [a / nrm for a in q[i]]
    a = [[sum(lam[k] * q[k][r] * q[k][c] for k in range(m)) for c in range(m)] for r in range(m)]
    vals, vecs = qc._top_eigen(a, 5)
    assert vals == pytest.approx([5, 5, 5, 2, 0], abs=1e-12)
    for x, (val, u) in enumerate(zip(vals, vecs, strict=True)):
        au = [sum(a[r][c] * u[c] for c in range(m)) for r in range(m)]
        assert max(abs(p - val * w) for p, w in zip(au, u, strict=True)) < 1e-12
        for y in range(x + 1):
            assert sum(p * w for p, w in zip(u, vecs[y], strict=True)) == pytest.approx(float(x == y), abs=1e-12)


def test_pca_of_five_hundred_samples_is_quick():
    v = _samples(300, 500, 11, groups=10)  # the issue's table: 10 conditions x 50, 300 proteins
    t0 = time.perf_counter()
    res = qc.pca(v)
    wide = qc.pca(_samples(400, 120, 12))  # more features than samples: the samples' matrix
    assert time.perf_counter() - t0 < 60  # Jacobi: 6 minutes for the first
    assert len(res["scores"]) == 500 and len(res["percent"]) == 6 and res["n"] == 300
    assert sum(res["percent"]) < 100 and res["percent"] == sorted(res["percent"], reverse=True)
    assert len(wide["scores"]) == 120


# ---------------------------------------------------------- correlation, dist --


def _dist_loop(rows):
    n = len(rows)
    d = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            acc, k = 0.0, 0
            for a, b in zip(rows[i], rows[j], strict=False):
                if a is not None and b is not None:
                    acc += (a - b) ** 2
                    k += 1
            d[i][j] = d[j][i] = math.sqrt(acc * len(rows[i]) / k) if k else math.inf
    return d


def test_distance_matrix_is_the_loop_bit_for_bit():
    rows = _samples(40, 9, 13)
    rows[3][2] = None
    rows += [[0.0, 1, 2, 3, 4, 5, 6, 7, 8]]  # ints, as callers may pass
    loop = _dist_loop(rows)
    top = max(v for r in loop for v in r if math.isfinite(v))
    assert qc._dist_matrix(rows) == [[v if math.isfinite(v) else top for v in r] for r in loop]
