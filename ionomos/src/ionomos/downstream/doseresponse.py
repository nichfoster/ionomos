"""
Dose-response curves: CurveCurator's analysis (Bayer et al., Nat Commun 2023,
doi:10.1038/s41467-023-43696-z; github.com/kusterlab/curve_curator, Copyright 2023 Florian P. Bayer,
Apache License 2.0), translated to pure Python with the changes listed below.

    plan = plan_series(conditions, settings, control, kind)   # which conditions are doses, and of what
    result = run(processed, settings, control)                 # DoseResult, or None with plan.reason
    fit = fit_curve(log10_doses, ratios)                        # one curve: pEC50, slope, plateaus, F, p

What CurveCurator does, and so what this does (its defaults: OLS, "standard" speed, no interpolation):
- Every feature's values become ratios to the control: 2^log2 / mean(2^log2 of the control samples)
  (isoDTB heavy/light ratios are already ratios; a control condition, when there is one, still re-centres
  them). The control enters the fit as ONE point, ratio 1 at dose 0 (log10 dose -inf).
- A 4-parameter log-logistic curve, y = (front - back) / (1 + 10^(slope * (x + pEC50))) + back, x = log10 M,
  is fitted by least squares within CurveCurator's bounds: pEC50 within 2 log units of the doses, slope
  0.01-10, plateaus 1e-4-1e6. Starts: CurveCurator's alternative guesses (flat, outside the doses, a step
  between each pair of doses); the best one is refined from slopes 0.01, 1 and 10, the best fit kept.
  CurveCurator refines with scipy's L-BFGS-B; this uses a bounded Levenberg-Marquardt (the same minimum).
- The null model is the mean. F = (SSE0 - SSE1) / SSE1 * n / 4 (the recalibrated F: n / k, not
  (n - k) / (k - j)), p from F(5, dfd; loc 0.12) with dfd = (0.8 - 1 / ((n - 4)^4 / n + 4)) * (n - 2.5).
- Curve fold change: log2 of the fitted curve at the highest dose over the lowest dose.
- Relevance score (SAM with s0): s0 = fc_lim / sqrt(F^-1(1 - alpha | 5, dfd)),
  F_adj = 1 / (1 / sqrt(F) + s0 / |fold change|)^2, relevance = -log10 p(F_adj).
- Class: up / down when relevance >= -log10(alpha) and |fold change| >= fc_lim; "not" when the curve
  isn't relevant and the mean model is flat (RMSE <= 0.1 around a ratio within 2^(+-fc_lim/2));
  "unclear" otherwise (CurveCurator leaves those blank on purpose). Defaults alpha 0.05, fc_lim 0.45
  (CurveCurator's decryptM settings).
- Added here: a BH q-value of the curve p-values (CurveCurator's own q-values need its decoys), and a
  95% interval for pEC50 from the Jacobian (CurveCurator's pEC50 error x Student t, n - 4 df), clipped to
  the pEC50 range a fit may take (a steep curve between two doses has no local error estimate).

Doses come from analysis.doses (condition -> "10 nM"), else from condition names (Cmpd_10nM, 0p1uM,
10 µM); the control (DMSO / vehicle, analysis.find_control) is dose 0. A series (one compound) needs
analysis.dose_min_doses distinct doses above zero (default 4) or it is skipped with a note.
Checked against CurveCurator 0.6.0 itself (tests/golden/dose_response/, tests/test_dose_response.py).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ionomos.downstream import stats

# CurveCurator's global limits (models.py)
PEC50_DELTA = 2.0
SLOPE_LIMITS = (0.01, 10.0)
Y_LIMITS = (1e-4, 1e6)
F_LOC = 0.12   # location of the recalibrated F distribution
F_DFN = 5.0
DEFAULT_ALPHA = 0.05
DEFAULT_FC_LIM = 0.45
NOT_RMSE_LIMIT = 0.1
LN10 = math.log(10)
MOLAR = {"pM": 1e-12, "nM": 1e-9, "µM": 1e-6, "mM": 1e-3, "M": 1.0}
_PREFIX = {"p": "pM", "n": "nM", "u": "µM", "µ": "µM", "μ": "µM", "m": "mM", "": "M"}
# a number and a molar unit, inside a name: 10nM, 0.1uM, 0p1uM, 10 µM, 1mM, 5M (not 10min, not 3M4)
_DOSE_RE = re.compile(r"(?<![\d.])(\d+(?:[.p]\d+)?|\.\d+)\s?([pPnNuUµμm]?)(M)(?![A-Za-z0-9])")


class DoseError(ValueError):
    pass


# ---------------------------------------------------------------- doses --


def _unit(prefix: str) -> str:
    return _PREFIX[prefix if prefix == "m" else prefix.lower()]


def parse_dose(value, unit: str = "") -> tuple[float, str]:
    """'10 nM' -> (1e-08, 'nM'); 0 -> (0.0, ''); 10 with unit='uM' -> (1e-05, 'µM'). DoseError when a number
    has no unit (and no dose_unit is set) or the text isn't a dose."""
    if isinstance(value, bool):
        raise DoseError(f"{value!r} is not a dose")
    if isinstance(value, (int, float)):
        num, u = float(value), ""
    else:
        m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+(?:[eE][+-]?\d+)?)\s*([pPnNuUµμm]?M)?\s*", str(value))
        if not m:
            raise DoseError(f"{value!r} is not a dose (write it like 10 nM, 0.1 uM or 0)")
        num, u = float(m.group(1)), (_unit(m.group(2)[:-1]) if m.group(2) else "")
    if not math.isfinite(num) or num < 0:
        raise DoseError(f"{value!r} is not a dose")
    if num == 0:
        return 0.0, u
    if not u:
        if not unit:
            raise DoseError(f"{value!r} has no unit: write e.g. {value} nM, or set analysis.dose_unit")
        u = normalize_unit(unit)
    return num * MOLAR[u], u


def normalize_unit(unit: str) -> str:
    m = re.fullmatch(r"\s*([pPnNuUµμm]?)M\s*", str(unit))
    if not m:
        raise DoseError(f"dose unit {unit!r} must be one of pM, nM, uM (µM), mM, M")
    return _unit(m.group(1))


def dose_in_name(name: str) -> tuple[float, str, str] | None:
    """'Cmpd_10nM' -> (1e-08, 'nM', 'Cmpd'); '0p1uM' -> (1e-07, 'µM', ''); no dose -> None.
    Two doses in one name (a combination) are ambiguous: DoseError."""
    hits = list(_DOSE_RE.finditer(name))
    if not hits:
        return None
    if len(hits) > 1:
        raise DoseError(f"{name!r} has {len(hits)} doses in its name ({', '.join(h.group(0) for h in hits)})")
    h = hits[0]
    num = float(h.group(1).replace("p", "."))
    u = _unit(h.group(2))
    rest = re.sub(r"[_\-. ]+", "_", name[: h.start()] + "_" + name[h.end():]).strip("_")
    return num * MOLAR[u], u, rest


def fmt_dose(molar: float, unit: str | None = None) -> str:
    """1e-08 -> '10 nM', 3e-06 -> '3 µM' (the unit that keeps the number >= 1, unless one is given)."""
    if molar == 0:
        return "0"
    if unit is None:
        unit = next((u for u in ("M", "mM", "µM", "nM") if molar >= MOLAR[u] * (1 - 1e-9)), "pM")
    v = molar / MOLAR[unit]
    return f"{v:.4g} {unit}"


@dataclass
class Series:
    """One compound's titration: its conditions with their doses (molar), sharing the controls (dose 0)."""
    name: str
    dose_of: dict[str, float]
    unit: str
    controls: list[str] = field(default_factory=list)

    @property
    def doses(self) -> list[float]:
        return sorted({d for d in self.dose_of.values() if d > 0})


@dataclass
class Plan:
    series: list[Series] = field(default_factory=list)   # the ones with enough doses
    skipped: list[Series] = field(default_factory=list)  # too few doses
    notes: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)  # (severity, message) for the doctor
    reason: str = ""  # why nothing runs (the report's empty state)


def _main_unit(units: list[str]) -> str:
    units = [u for u in units if u]
    return max(dict.fromkeys(units), key=units.count) if units else "M"


def plan_series(conditions: list[str], s, control: str | None, kind: str = "intensity") -> Plan:
    """Which conditions are doses of which compound. s: analysis.Settings (doses, dose_unit, dose_min_doses)."""
    out = Plan()
    min_doses = max(1, int(getattr(s, "dose_min_doses", 4)))
    explicit = getattr(s, "doses", None) or {}
    if explicit:
        by_low = {c.lower(): c for c in conditions}
        dose_of, units = {}, []
        for key, val in explicit.items():
            c = by_low.get(str(key).lower())
            if c is None:
                out.problems.append(("input", f"analysis.doses names {key!r}, which is not a condition here "
                                              f"(conditions: {', '.join(conditions)})"))
                continue
            try:
                d, u = parse_dose(val, getattr(s, "dose_unit", ""))
            except DoseError as exc:
                out.problems.append(("input", f"analysis.doses {key}: {exc}"))
                continue
            dose_of[c] = d
            units.append(u)
        left = [c for c in conditions if c not in dose_of]
        if left and dose_of:
            out.notes.append("dose-response: not in analysis.doses, so left out of the curves: " + ", ".join(left))
        ctrls = [c for c, d in dose_of.items() if d == 0]
        doses = {c: d for c, d in dose_of.items() if d > 0}
        series = [Series("", doses, _main_unit(units), ctrls)] if doses else []
    else:
        found: dict[str, dict[str, float]] = {}
        units_of: dict[str, list[str]] = {}
        zero: dict[str, list[str]] = {}
        for c in conditions:
            if c == control:
                continue
            try:
                hit = dose_in_name(c)
            except DoseError as exc:
                out.problems.append(("warning", f"{exc}; that condition is left out of the dose-response curves. "
                                                "Set analysis.doses to say which dose it is."))
                continue
            if hit is None:
                continue
            d, u, name = hit
            if d == 0:
                zero.setdefault(name, []).append(c)
                continue
            found.setdefault(name, {})[c] = d
            units_of.setdefault(name, []).append(u)
        series = [Series(name, doses, _main_unit(units_of[name]), ([control] if control else []) + zero.get(name, []))
                  for name, doses in found.items()]
        if not series:
            out.reason = ("No doses were found: name the conditions with their concentration (Cmpd_10nM, "
                          "Cmpd_1uM, ...) or list them in experiment.yaml analysis.doses.")
            return out
    if not series:
        out.reason = "analysis.doses lists no condition of this experiment with a dose above 0."
        return out
    for sr in series:
        n = len(sr.doses)
        label = f"{sr.name}: " if sr.name else ""
        if n < min_doses:
            out.skipped.append(sr)
            out.notes.append(f"dose-response {label}skipped: {n} dose{'s' if n != 1 else ''} above zero "
                             f"({', '.join(fmt_dose(d) for d in sr.doses)}); a curve needs at least "
                             f"{min_doses} (analysis.dose_min_doses)")
            continue
        if kind == "intensity" and not sr.controls:
            out.skipped.append(sr)
            out.problems.append(("warning", f"dose-response {label}has {n} doses but no control to divide by: name "
                                            "the vehicle condition DMSO (or set analysis.control), or give it dose 0 in "
                                            "analysis.doses"))
            continue
        out.series.append(sr)
    if not out.series:
        out.reason = (f"The design has fewer than {min_doses} doses per compound, so no dose-response curves were "
                      "fitted." if out.skipped and not out.problems else
                      "Doses were found but the curves could not be set up; see the issues above.")
    return out


# -------------------------------------------------------- F distribution --


def f_logsf(f: float, d1: float, d2: float, loc: float = 0.0) -> float:
    """Natural log of P(F > f) for F(d1, d2) shifted by loc (scipy's f.logsf(f, d1, d2, loc)); exact in the
    far tail (no underflow), so a relevance score can be large."""
    x = f - loc
    if not x > 0:
        return 0.0
    if math.isinf(x):
        return -math.inf
    a, b = d2 / 2, d1 / 2
    den = d2 + d1 * x
    z = d2 / den
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * (math.log(d2) - math.log(den))
           + b * (math.log(d1 * x) - math.log(den)))
    if z < (a + 1) / (a + b + 2):
        return lbt + math.log(stats._betacf(a, b, z) / a)
    tail = 1 - math.exp(lbt) * stats._betacf(b, a, 1 - z) / b
    return math.log(tail) if tail > 0 else -math.inf


def f_sf(f: float, d1: float, d2: float, loc: float = 0.0) -> float:
    return math.exp(f_logsf(f, d1, d2, loc))


def f_ppf(q: float, d1: float, d2: float, loc: float = 0.0) -> float:
    """Inverse CDF: the f with P(F <= f) = q. Bisection on the tail."""
    target = math.log1p(-q)
    lo, hi = 0.0, 1.0
    while f_logsf(hi + loc, d1, d2, loc) > target:
        hi *= 2
        if hi > 1e12:
            return math.inf
    for _ in range(200):
        mid = (lo + hi) / 2
        if f_logsf(mid + loc, d1, d2, loc) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-14 * max(1.0, hi):
            break
    return (lo + hi) / 2 + loc


def dof_denominator(n: int) -> float:
    """CurveCurator's optimized denominator degrees of freedom for the 4-parameter model (Eq. 8, 9)."""
    return (0.8 - 1 / ((n - 4) ** 4 / n + 4)) * (n - 2.5)


# ------------------------------------------------------------- the model --


def curve(x: float, pec50: float, slope: float, front: float, back: float) -> float:
    """The 4-parameter log-logistic function at log10 dose x (-inf = the control: front)."""
    if x == -math.inf:
        return front
    t = slope * (x + pec50)
    if t > 300:
        return back
    if t < -300:
        return front
    return (front - back) / (1 + 10 ** t) + back


def _resid_jac(xs, ys, par):
    p, s, f, b = par
    r, jac = [], []
    for x, y in zip(xs, ys, strict=True):
        if x == -math.inf:
            e, w, xp = 1.0, 0.0, 0.0
        else:
            xp = x + p
            t = s * xp
            if t > 300:
                e, w = 0.0, 0.0
            elif t < -300:
                e, w = 1.0, 0.0
            else:
                u = 10 ** t
                e = 1 / (1 + u)
                w = u * e * e
        r.append((f - b) * e + b - y)
        k = LN10 * (b - f) * w
        jac.append((k * s, k * xp, e, 1 - e))
    return r, jac


def _solve(a: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when singular."""
    n = len(rhs)
    m = [row[:] + [rhs[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda i: abs(m[i][c]))
        if not abs(m[piv][c]) > 1e-300:
            return None
        m[c], m[piv] = m[piv], m[c]
        for i in range(c + 1, n):
            k = m[i][c] / m[c][c]
            if k:
                for j in range(c, n + 1):
                    m[i][j] -= k * m[c][j]
    out = [0.0] * n
    for i in range(n - 1, -1, -1):
        out[i] = (m[i][n] - sum(m[i][j] * out[j] for j in range(i + 1, n))) / m[i][i]
    return out if all(math.isfinite(v) for v in out) else None


def _clip(par, lo, hi):
    return [min(max(v, a), b) for v, a, b in zip(par, lo, hi, strict=True)]


def _lm(xs, ys, start, lo, hi, max_iter: int = 300):
    """Bounded Levenberg-Marquardt on the sum of squares. A parameter on a bound whose descent direction
    points outside is held there for that step. Returns (parameters, SSE)."""
    par = _clip(start, lo, hi)
    r, jac = _resid_jac(xs, ys, par)
    cost = sum(v * v for v in r)
    lam = 1e-3
    for _ in range(max_iter):
        g = [sum(jac[i][k] * r[i] for i in range(len(r))) for k in range(4)]
        a = [[sum(row[p] * row[q] for row in jac) for q in range(4)] for p in range(4)]
        free = [k for k in range(4) if not ((par[k] <= lo[k] and g[k] > 0) or (par[k] >= hi[k] and g[k] < 0))]
        if not free:
            break
        scale = max(a[k][k] for k in free) or 1.0
        new = None
        while lam < 1e16:
            m = [[a[p][q] + (lam * (a[p][p] + 1e-12 * scale) if p == q else 0.0) for q in free] for p in free]
            d = _solve(m, [-g[k] for k in free])
            if d is not None:
                cand = par[:]
                for k, dk in zip(free, d, strict=True):
                    cand[k] = par[k] + dk
                cand = _clip(cand, lo, hi)
                r2, jac2 = _resid_jac(xs, ys, cand)
                c2 = sum(v * v for v in r2)
                if c2 < cost:
                    new = cand
                    break
            lam *= 10
        if new is None:
            break
        step = max(abs(new[k] - par[k]) for k in range(4))
        decrease = cost - c2
        par, r, jac, cost = new, r2, jac2, c2
        lam = max(lam / 10, 1e-15)
        if decrease <= 1e-15 * max(cost, 1e-300) or step <= 1e-12:
            break
    return par, cost


def _svd(jac):
    """One-sided Jacobi SVD of an n x 4 matrix: (singular values, V as columns). Accurate for tiny values."""
    cols = [list(c) for c in zip(*jac, strict=True)]
    k = len(cols)
    v = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    for _ in range(80):
        rotated = False
        for p in range(k - 1):
            for q in range(p + 1, k):
                al = sum(x * x for x in cols[p])
                be = sum(x * x for x in cols[q])
                ga = sum(x * y for x, y in zip(cols[p], cols[q], strict=True))
                if ga == 0 or al == 0 or be == 0 or abs(ga) <= 1e-15 * math.sqrt(al * be):
                    continue
                rotated = True
                zeta = (be - al) / (2 * ga)
                t = (1.0 if zeta >= 0 else -1.0) / (abs(zeta) + math.sqrt(1 + zeta * zeta))
                c = 1 / math.sqrt(1 + t * t)
                sn = c * t
                cp, cq = cols[p], cols[q]
                for i in range(len(cp)):
                    x, y = cp[i], cq[i]
                    cp[i], cq[i] = c * x - sn * y, sn * x + c * y
                for i in range(k):
                    x, y = v[i][p], v[i][q]
                    v[i][p], v[i][q] = c * x - sn * y, sn * x + c * y
        if not rotated:
            break
    return [math.sqrt(sum(x * x for x in c)) for c in cols], v


def _param_errors(xs, ys, par) -> list[float]:
    """CurveCurator's calculate_parameter_error: Moore-Penrose (J^T J)^-1 x SSE / (n - 4), as scipy's curve_fit."""
    r, jac = _resid_jac(xs, ys, par)
    sv, v = _svd(jac)
    smax = max(sv) if sv else 0.0
    if not smax > 0:
        return [math.nan] * 4
    thr = 2.220446049250313e-16 * max(len(xs), 4) * smax
    keep = [i for i, x in enumerate(sv) if x > thr]
    sse = sum(x * x for x in r) / (len(xs) - 4)
    out = []
    for p in range(4):
        cov = sum(v[p][i] * v[p][i] / (sv[i] * sv[i]) for i in keep) * sse
        out.append(math.sqrt(cov) if cov >= 0 else math.nan)
    return out


def _guesses(x, y, lo, hi):
    """CurveCurator's alternative_guesses (x[0] is the control, -inf), clipped to the bounds. Yields
    (pec50, slope, front, back); slope is SLOPE_LIMITS[1] throughout, as there."""
    s = SLOPE_LIMITS[1]
    ym = sum(y) / len(y)

    def g(log_ec50, front, back):
        return _clip([-log_ec50, s, front, back], lo, hi)

    xs = sorted(x)
    k = len(xs)
    med = xs[k // 2] if k % 2 else (xs[k // 2 - 1] + xs[k // 2]) / 2
    yield g(med, ym, ym)
    yield g(-math.inf, 1.0, ym)  # first outside guess: x[0] - (x[1] - x[0]) / 2 with x[0] = -inf
    yield g(x[1] - (x[2] - x[1]) / 2, y[0], sum(y[1:]) / (k - 1))
    for n in range(2, k):
        yield g((x[n - 1] + x[n]) / 2, sum(y[:n]) / n, sum(y[n:]) / (k - n))
    yield g(x[-1] + (x[-1] - x[-2]) / 2, ym, 1.0)


def bounds(log_doses: list[float]) -> tuple[list[float], list[float]]:
    """(lower, upper) for (pEC50, slope, front, back): CurveCurator's set_boundaries."""
    neg = [-v for v in log_doses]
    return ([min(neg) - PEC50_DELTA, SLOPE_LIMITS[0], Y_LIMITS[0], Y_LIMITS[0]],
            [max(neg) + PEC50_DELTA, SLOPE_LIMITS[1], Y_LIMITS[1], Y_LIMITS[1]])


def fit_curve(log_doses: list[float], ratios: list[float], all_log_doses: list[float] | None = None,
              max_iter: int = 300) -> dict | None:
    """Fit one curve. log_doses: log10 molar of each measured point, ascending; ratios: to the control.
    all_log_doses: the experiment's doses (they set the pEC50 bounds; default log_doses). None when there
    are fewer than 4 points (CurveCurator's n <= 4 with the control point)."""
    x = [-math.inf, *log_doses]
    y = [1.0, *ratios]
    n = len(x)
    if n <= 4:
        return None
    lo, hi = bounds(all_log_doses or log_doses)
    # null model: the mean (OLS has the analytic solution)
    ym = sum(y) / n
    sse0 = sum((v - ym) ** 2 for v in y)
    # CurveCurator "standard": the best alternative guess, refined from three slopes, the best kept
    best_guess, best_sse = None, math.inf
    for gs in _guesses(x, y, lo, hi):
        c = sum(v * v for v in _resid_jac(x, y, gs)[0])
        if c <= best_sse:
            best_guess, best_sse = gs, c
    fit, sse1 = None, math.inf
    for slope in (SLOPE_LIMITS[0], 1.0, SLOPE_LIMITS[1]):
        start = best_guess[:]
        start[1] = slope
        par, c = _lm(x, y, start, lo, hi, max_iter)
        if c < sse1:
            fit, sse1 = par, c
    p, s, f, b = fit
    err = _param_errors(x, y, fit)
    ss_tot = sum((v - ym) ** 2 for v in y)
    r2 = 1.0 - ((sse1 + 1e-10) / (ss_tot + 1e-10))
    fstat = (sse0 + 1e-20 - (sse1 + 1e-20)) / (sse1 + 1e-20) * (n / 4)
    fstat = max(fstat, 0.0)
    dfd = dof_denominator(n)
    hi_y, lo_y = curve(max(log_doses), *fit), curve(min(log_doses), *fit)
    return {"pec50": p, "slope": s, "front": f, "back": b, "pec50_error": err[0], "slope_error": err[1],
            "front_error": err[2], "back_error": err[3],
            "fold_change": math.log2(hi_y) - math.log2(lo_y) if hi_y > 0 and lo_y > 0 else math.nan,
            "rmse": math.sqrt(sse1 / n), "r2": r2 if r2 > 0 else 0.0, "null_intercept": ym,
            "null_rmse": math.sqrt(sse0 / n), "f": fstat, "p": f_sf(fstat, F_DFN, dfd, F_LOC), "n": n, "dfd": dfd,
            "sse": sse1}


def relevance(fit: dict, alpha: float = DEFAULT_ALPHA, fc_lim: float = DEFAULT_FC_LIM) -> tuple[float, float]:
    """(s0, relevance score) for one curve: CurveCurator's SAM-adjusted F (thresholding.py)."""
    dfd = fit["dfd"]
    lim = math.sqrt(max(f_ppf(1 - alpha, F_DFN, dfd), 0.0))
    s0 = abs(fc_lim) / lim if lim > 0 else math.inf
    fc = abs(fit["fold_change"]) if math.isfinite(fit["fold_change"]) else 0.0
    inv = (1 / math.sqrt(fit["f"]) if fit["f"] > 0 else math.inf) + (s0 / fc if fc > 0 else math.inf)
    f_adj = 1 / inv ** 2 if math.isfinite(inv) else 0.0
    return s0, -f_logsf(f_adj, F_DFN, dfd, F_LOC) / LN10


def classify(fit: dict, score: float, alpha: float = DEFAULT_ALPHA, fc_lim: float = DEFAULT_FC_LIM,
             not_rmse: float = NOT_RMSE_LIMIT) -> str:
    """up | down | not | unclear (define_regulated_curves; CurveCurator's blank is "unclear")."""
    fc = fit["fold_change"]
    relevant = score >= -math.log10(alpha)
    if relevant and math.isfinite(fc) and abs(fc) >= fc_lim:
        return "up" if fc > 0 else "down" if fc < 0 else "unclear"
    ic = fit["null_intercept"]
    if not relevant and fit["null_rmse"] <= not_rmse and ic > 0 and -abs(fc_lim) / 2 <= math.log2(ic) <= abs(fc_lim) / 2:
        return "not"
    return "unclear"


# ------------------------------------------------------------- the stage --


@dataclass
class Curves:
    """One series' fitted curves."""
    series: Series
    samples: list[int]                  # columns of the matrix used (controls and doses)
    sample_dose: list[float]            # molar, 0 for a control sample
    rows: list[dict] = field(default_factory=list)
    log2: dict[int, list[float | None]] = field(default_factory=dict)  # feature -> log2 ratio per sample
    too_few: int = 0                    # features with fewer than 4 measured doses
    no_control: int = 0                 # features never measured in the control


@dataclass
class DoseResult:
    curves: list[Curves]
    notes: list[str]
    alpha: float
    fc_lim: float


COLUMNS = ["series", "id", "label", "description", "class", "pec50", "pec50_ci_low", "pec50_ci_high", "ec50",
           "ec50_ci_low", "ec50_ci_high", "unit", "slope", "front", "back", "curve_fold_change", "f_value", "pvalue",
           "qvalue", "relevance", "r2", "rmse", "null_rmse", "points", "doses"]


def _ec50(pec50: float, unit: str) -> float:
    """EC50 in the series' unit; NaN for an interval edge off any scale (a flat curve's pEC50 is undetermined)."""
    return 10 ** (-pec50) / MOLAR[unit] if math.isfinite(pec50) and abs(pec50) < 300 else math.nan


def fit_series(m, measured, sr: Series, alpha: float, fc_lim: float, progress=None) -> Curves:
    """Every feature of one series. m: the QuantMatrix (features, samples, conditions, kind); measured: its
    values before imputation (log2; None = missing)."""
    cols = [j for j, x in enumerate(m.samples) if m.condition[x] in sr.dose_of or m.condition[x] in sr.controls]
    dose = {j: sr.dose_of.get(m.condition[m.samples[j]], 0.0) for j in cols}
    ctrl = [j for j in cols if dose[j] == 0]
    order = sorted((j for j in cols if dose[j] > 0), key=lambda j: dose[j])  # stable: replicates keep their order
    xs_all = [math.log10(d) for d in sr.doses]
    lo_b, hi_b = bounds(xs_all)  # the pEC50 range a fit may take
    out = Curves(sr, cols, [dose[j] for j in cols])
    tq = {}
    for i, row in enumerate(measured):
        if progress and i and i % 500 == 0:
            progress(i)
        cv = [2.0 ** row[j] for j in ctrl if row[j] is not None]
        if not cv and m.kind == "intensity":
            out.no_control += 1
            continue
        ref = sum(cv) / len(cv) if cv else 1.0
        lref = math.log2(ref)
        pts = [(math.log10(dose[j]), 2.0 ** (row[j] - lref)) for j in order if row[j] is not None]
        fit = fit_curve([a for a, _ in pts], [b for _, b in pts], xs_all) if len(pts) >= 4 else None
        if fit is None:
            out.too_few += 1
            continue
        s0, score = relevance(fit, alpha, fc_lim)
        k = fit["n"] - 4
        t = tq.get(k) or tq.setdefault(k, stats.qt_upper(0.05, k))
        e = fit["pec50_error"]
        ci = (fit["pec50"] - t * e, fit["pec50"] + t * e) if math.isfinite(e) else (math.nan, math.nan)
        if math.isfinite(e):  # a pEC50 can't leave the fit's bounds, so neither can its interval (a steep curve
            ci = (max(ci[0], lo_b[0]), min(ci[1], hi_b[0]))  # between two doses: "somewhere in the tested range")
        f = m.features[i]
        out.rows.append({
            "index": i, "series": sr.name, "id": f.id, "label": f.label, "description": f.description,
            "class": classify(fit, score, alpha, fc_lim), "pec50": fit["pec50"], "pec50_ci_low": ci[0],
            "pec50_ci_high": ci[1], "ec50": _ec50(fit["pec50"], sr.unit), "ec50_ci_low": _ec50(ci[1], sr.unit),
            "ec50_ci_high": _ec50(ci[0], sr.unit), "unit": sr.unit, "slope": fit["slope"], "front": fit["front"],
            "back": fit["back"], "curve_fold_change": fit["fold_change"], "f_value": fit["f"], "pvalue": fit["p"],
            "qvalue": None, "relevance": score, "s0": s0, "r2": fit["r2"], "rmse": fit["rmse"],
            "null_rmse": fit["null_rmse"], "points": fit["n"],
            "doses": len({a for a, _ in pts})})
        out.log2[i] = [None if row[j] is None else row[j] - lref for j in cols]
    q = stats.bh_adjust([r["pvalue"] for r in out.rows])
    for r, qv in zip(out.rows, q, strict=True):
        r["qvalue"] = qv
    out.rows.sort(key=lambda r: -r["relevance"])
    return out


def run(p, s, control: str | None, progress=None) -> tuple[DoseResult | None, Plan]:
    """The dose-response stage on processed data (fpa.Processed): the curves, or None and why (plan.reason)."""
    m = p.m
    plan = plan_series(m.conditions, s, control, m.kind)
    if not plan.series:
        return None, plan
    alpha = getattr(s, "dose_alpha", DEFAULT_ALPHA)
    fc_lim = getattr(s, "dose_fc_lim", DEFAULT_FC_LIM)
    curves = [fit_series(m, p.measured, sr, alpha, fc_lim, progress) for sr in plan.series]
    notes = list(plan.notes)
    for c in curves:
        label = f" ({c.series.name})" if c.series.name else ""
        if c.no_control:
            notes.append(f"dose-response{label}: {c.no_control} features never measured in the control were left out")
        if c.too_few:
            notes.append(f"dose-response{label}: {c.too_few} features measured at fewer than 4 doses were not fitted")
    return DoseResult(curves, notes, alpha, fc_lim), plan


def table_rows(res: DoseResult) -> list[dict]:
    return [r for c in res.curves for r in c.rows]


def summary(res: DoseResult | None, plan: Plan, table: str | None) -> dict:
    """analysis.json "dose_response"."""
    if res is None:
        return {"ran": False, "reason": plan.reason or "; ".join(m for _, m in plan.problems)}
    out = {"ran": True, "table": table, "alpha": res.alpha, "fc_lim": res.fc_lim, "series": []}
    for c in res.curves:
        cls = [r["class"] for r in c.rows]
        out["series"].append({"name": c.series.name, "unit": c.series.unit,
                              "doses": [fmt_dose(d) for d in c.series.doses],
                              "controls": c.series.controls, "curves": len(c.rows),
                              **{k: cls.count(k) for k in ("up", "down", "not", "unclear")},
                              "not_fitted": c.too_few + c.no_control})
    return out


def _num(v, digits: int = 4):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(f"{v:.{digits}g}") if v and abs(v) < 1e-3 else round(v, digits)


def report_payload(res: DoseResult | None, plan: Plan) -> dict:
    """The report's "dose" data (report.js renderDose): per series, the curves as columns plus each curve's
    measured log2 ratios to the control, so a click can draw the fit over its points."""
    found = bool(plan.series or plan.skipped or plan.problems)
    if res is None:
        return {"ran": False, "found": found, "reason": plan.reason or "; ".join(m for _, m in plan.problems)}
    series = []
    for c in res.curves:
        rows = c.rows
        col = {k: [_num(r[src]) for r in rows] for k, src in
               (("pec50", "pec50"), ("ciL", "pec50_ci_low"), ("ciR", "pec50_ci_high"), ("ec50", "ec50"),
                ("slope", "slope"), ("front", "front"), ("back", "back"), ("fc", "curve_fold_change"),
                ("p", "pvalue"), ("q", "qvalue"), ("rel", "relevance"), ("r2", "r2"))}
        series.append({"name": c.series.name, "unit": c.series.unit, "doses": c.series.doses, "samples": c.samples,
                       "sdose": c.sample_dose, "controls": c.series.controls, "i": [r["index"] for r in rows],
                       "cls": [r["class"] for r in rows], "n": [r["points"] for r in rows], **col,
                       "y": [[None if v is None else round(v, 3) for v in c.log2[r["index"]]] for r in rows],
                       "skipped": c.too_few + c.no_control})
    return {"ran": True, "found": True, "alpha": res.alpha, "fcLim": res.fc_lim, "series": series, "reason": ""}
