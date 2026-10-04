"""
Natural cubic spline bases, as R's splines::ns() makes them (D77), for time courses with many time points.

    b = ns(x, df)              # the basis at x: b.basis [len(x)][df], with b.knots, b.boundary
    b.predict(xnew)            # the same basis at other points (R's predict(ns_object, xnew))

Ported from R 4.6's splines::ns and splineDesign, step by step, so the columns are R's own (not just the same
column space):
    interior knots    df - 1 of them, at the quantiles 1/df, 2/df, ... of x (type 7, as quantile()); a knot that
                      lands on a boundary is moved 1/8 of the way to the next knot inside (R's "shoving")
    boundary knots    min(x), max(x), each four times (cubic: order 4)
    B-spline basis    the Cox-de Boor recursion (splineDesign), one column dropped (intercept = FALSE)
    natural           the second derivative at both boundary knots is 0: the basis is multiplied by the columns
                      3.. of Q from the QR decomposition of the constraint matrix (LINPACK's Householder dqrdc2
                      and qr.qty, as R's qr()), which spans the functions meeting both constraints.

Checked against R in tests/test_timecourse.py (tests/golden/timecourse/ns_basis.tsv). Pure Python.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


class SplineError(ValueError):
    pass


def quantile7(x: list[float], probs: list[float]) -> list[float]:
    """R's quantile(x, probs, type = 7, names = FALSE)."""
    xs = sorted(x)
    n = len(xs)
    out = []
    for p in probs:
        index = 1 + max(n - 1, 0) * p
        lo, hi = math.floor(index), math.ceil(index)
        q = xs[lo - 1]
        if index > lo and xs[hi - 1] != q:
            h = index - lo
            q = (1 - h) * q + h * xs[hi - 1]
        out.append(q)
    return out


def _interval(knots: list[float], x: float, order: int) -> int:
    """The knot interval [t_mu, t_mu+1) whose polynomial piece is evaluated at x (splines.c set_cursor): the
    last knot <= x with a following knot above it; at the right boundary knot, the last proper interval."""
    nk = len(knots)
    last = nk - order          # index of the right boundary knot (0-based)
    if x >= knots[last]:
        mu = last - 1
        while mu > 0 and knots[mu] == knots[mu + 1]:
            mu -= 1
        return mu
    mu = order - 1
    while mu + 1 < last and knots[mu + 1] <= x:
        mu += 1
    return mu


def spline_design(knots: list[float], xs: list[float], order: int = 4, derivs: list[int] | None = None):
    """R's splineDesign(knots, x, ord, derivs): [len(xs)][len(knots) - order], x within the boundary knots."""
    knots = sorted(knots)
    nb = len(knots) - order
    derivs = derivs or [0]
    out = []
    for a, x in enumerate(xs):
        d = derivs[a % len(derivs)]
        if x < knots[order - 1] or x > knots[nb]:
            raise SplineError(f"{x} is outside the boundary knots")
        mu = _interval(knots, x, order)
        k0 = order - d
        if k0 < 1:
            out.append([0.0] * nb)
            continue
        # order-1 basis on the interval, then the recursion up to order k0
        b = [0.0] * (len(knots) - 1)
        b[mu] = 1.0
        for k in range(2, k0 + 1):
            nxt = [0.0] * (len(knots) - k)
            for i in range(len(nxt)):
                v = 0.0
                den = knots[i + k - 1] - knots[i]
                if den > 0 and b[i]:
                    v += (x - knots[i]) / den * b[i]
                den = knots[i + k] - knots[i + 1]
                if den > 0 and b[i + 1]:
                    v += (knots[i + k] - x) / den * b[i + 1]
                nxt[i] = v
            b = nxt
        # derivatives: d/dx B_{i,j} = (j-1) [B_{i,j-1} / (t_{i+j-1} - t_i) - B_{i+1,j-1} / (t_{i+j} - t_{i+1})]
        for j in range(k0 + 1, order + 1):
            nxt = [0.0] * (len(knots) - j)
            for i in range(len(nxt)):
                v = 0.0
                den = knots[i + j - 1] - knots[i]
                if den > 0:
                    v += b[i] / den
                den = knots[i + j] - knots[i + 1]
                if den > 0:
                    v -= b[i + 1] / den
                nxt[i] = (j - 1) * v
            b = nxt
        out.append(b[:nb])
    return out


def _householder(cols: list[list[float]]):
    """LINPACK dqrdc2 without pivoting (the columns here are independent): (Householder vectors, qraux)."""
    n = len(cols[0])
    x = [list(c) for c in cols]
    qraux = [0.0] * len(x)
    for l in range(min(len(x), n - 1)):
        nrm = math.sqrt(sum(v * v for v in x[l][l:]))
        if nrm == 0:
            continue
        if x[l][l] != 0:
            nrm = math.copysign(nrm, x[l][l])
        for i in range(l, n):
            x[l][i] /= nrm
        x[l][l] += 1.0
        for j in range(l + 1, len(x)):
            t = -sum(x[l][i] * x[j][i] for i in range(l, n)) / x[l][l]
            for i in range(l, n):
                x[j][i] += t * x[l][i]
        qraux[l] = x[l][l]
        x[l][l] = -nrm
    return x, qraux


def _qty(x: list[list[float]], qraux: list[float], y: list[float]) -> list[float]:
    """LINPACK dqrsl: Q'y."""
    n = len(y)
    y = list(y)
    for j in range(min(len(x), n - 1)):
        if qraux[j] == 0:
            continue
        u = [qraux[j]] + x[j][j + 1:]
        t = -sum(a * b for a, b in zip(u, y[j:], strict=True)) / u[0]
        for i in range(j, n):
            y[i] += t * u[i - j]
    return y


@dataclass
class NsBasis:
    knots: list[float]           # interior knots
    boundary: tuple[float, float]
    basis: list[list[float]]     # [point][column]

    @property
    def df(self) -> int:
        return len(self.knots) + 1

    def predict(self, xs: list[float]) -> list[list[float]]:
        """The basis at other points inside the boundary knots (predict.ns)."""
        return _ns_at(xs, self.knots, self.boundary)


def _ns_at(xs: list[float], knots: list[float], boundary: tuple[float, float]) -> list[list[float]]:
    lo, hi = boundary
    allk = sorted([lo] * 4 + list(knots) + [hi] * 4)
    basis = [row[1:] for row in spline_design(allk, list(xs), 4)]
    const = [row[1:] for row in spline_design(allk, [lo, hi], 4, [2, 2])]
    hh, qraux = _householder([const[0], const[1]])   # qr(t(const)): its two columns are const's rows
    return [_qty(hh, qraux, row)[2:] for row in basis]


def ns(x: list[float], df: int) -> NsBasis:
    """splines::ns(x, df) with intercept = FALSE and the boundary knots at the range of x."""
    x = [float(v) for v in x]
    if not x:
        raise SplineError("no values")
    df = int(df)
    if df < 1:
        raise SplineError("df must be at least 1")
    lo, hi = min(x), max(x)
    if lo == hi:
        raise SplineError("every value is the same: no curve can be fitted")
    n_in = df - 1
    knots: list[float] = []
    if n_in > 0:
        by = 1 / (n_in + 1)          # seq.int(0, 1, length.out = df + 1)[-ends]
        probs = [k * by for k in range(1, n_in + 1)]
        knots = quantile7(x, probs)
        if knots[0] == lo:
            inside = [k for k in knots if k > lo]
            if not inside:
                raise SplineError("all interior knots match the left boundary knot")
            step = (min(inside) - lo) / 8
            knots = [k + step if k == lo else k for k in knots]
        if knots[-1] == hi:
            inside = [k for k in knots if k < hi]
            if not inside:
                raise SplineError("all interior knots match the right boundary knot")
            step = (hi - max(inside)) / 8
            knots = [k - step if k == hi else k for k in knots]
    return NsBasis(knots, (lo, hi), _ns_at(x, knots, (lo, hi)))
