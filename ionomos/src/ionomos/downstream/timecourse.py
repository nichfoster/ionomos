"""
Time courses: which features change over time, in which direction, and with which shape (D53, ROADMAP 5C #1).

    plan = plan_series(conditions, settings, control)     # which conditions are time points, and of what
    result = run(processed, settings, control, model)     # TimeResult, or None with plan.reason

Times come from analysis.times (condition -> "2 h"), else from condition names (Drug_0h, Drug_30min, Drug_4h,
T24h, 2d). The conditions sharing the rest of the name are one series (Drug; DMSO). A control condition with no
time in its name is time 0 of every series that has no time 0 of its own. A series needs
analysis.time_min_points time points (default 3), or it is left to the ordinary comparisons.

The statistics are limma's for a time course with few time points (limma User's Guide 9.6.1): time is a
factor in the comparisons' own model (~0 + condition, plus any block / covariates), and each question is a
set of contrasts between its coefficients, with the same variance prior as the comparisons:

    change over time   moderated F on every time point against the first (T - 1 contrasts)
    trend              moderated t on the linear contrast over the ordered time points (equally spaced: the
                       order of the time points, not the hours, so 0 / 1 h / 24 h is not dominated by the gap)
    vs the control     (2 series sharing time points, one of them the control) moderated F on the interaction
    series             contrasts (A_t - A_0) - (B_t - B_0): does the series respond differently over time

Then, descriptively: each feature's log2 fold change against the first time point, its largest change and
when, a class (up / down / mixed / not, at the report's alpha and |log2FC| cut-offs on the F q-value), and
for the changing features a pattern: k-means on the profiles scaled to their largest change, started from
the strongest feature so the result is the same on every run.

Checked against limma itself (tests/golden/timecourse/, tests/test_timecourse.py).
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field

from ionomos.downstream import stats

HOURS = {"s": 1 / 3600, "min": 1 / 60, "h": 1.0, "d": 24.0}
_UNIT = {"s": "s", "sec": "s", "secs": "s", "min": "min", "mins": "min", "h": "h", "hr": "h", "hrs": "h",
         "hour": "h", "hours": "h", "d": "d", "day": "d", "days": "d"}
_UNITS_RX = "secs|sec|s|mins|min|hours|hour|hrs|hr|h|days|day|d"
# a number and a time unit inside a name: 0h, 30min, 4h, T24h, 0p5h, 2d (not 10mM, not 3d7, not Cmpd2h)
_TIME_RE = re.compile(rf"(?<![A-Za-z0-9.])[tT]?(\d+(?:[.p]\d+)?)\s?({_UNITS_RX})(?![A-Za-z0-9])")
CLASSES = ("up", "down", "mixed", "not")
MAX_PATTERNS = 6
COLUMNS = ["series", "id", "label", "description", "class", "pattern", "F", "pvalue", "qvalue", "trend_t",
           "trend_pvalue", "trend_qvalue", "max_log2fc", "peak_time", "times", "log2fc", "mean_log2",
           "interaction_vs", "interaction_F", "interaction_pvalue", "interaction_qvalue"]


class TimeError(ValueError):
    pass


# ------------------------------------------------------------------- times --


def normalize_unit(unit: str) -> str:
    u = _UNIT.get(str(unit).strip().lower())
    if u is None:
        raise TimeError(f"time unit {unit!r} must be one of s, min, h, d")
    return u


def parse_time(value, unit: str = "") -> tuple[float, str]:
    """'30 min' -> (0.5, 'min'); 0 -> (0.0, ''); 4 with unit='h' -> (4.0, 'h'). Hours. TimeError when a number
    has no unit (and no time_unit is set) or the text isn't a time."""
    if isinstance(value, bool):
        raise TimeError(f"{value!r} is not a time")
    if isinstance(value, (int, float)):
        num, u = float(value), ""
    else:
        m = re.fullmatch(rf"\s*[tT]?([0-9]*\.?[0-9]+)\s*({_UNITS_RX})?\s*", str(value))
        if not m:
            raise TimeError(f"{value!r} is not a time (write it like 30 min, 4 h, 2 d or 0)")
        num, u = float(m.group(1)), (_UNIT[m.group(2)] if m.group(2) else "")
    if not math.isfinite(num) or num < 0:
        raise TimeError(f"{value!r} is not a time")
    if num == 0:
        return 0.0, u
    if not u:
        if not unit:
            raise TimeError(f"{value!r} has no unit: write e.g. {value} h, or set analysis.time_unit")
        u = normalize_unit(unit)
    return num * HOURS[u], u


def time_in_name(name: str) -> tuple[float, str, str] | None:
    """'Drug_30min' -> (0.5, 'min', 'Drug'); 'T24h' -> (24.0, 'h', ''); no time -> None. Two times in one name
    are ambiguous: TimeError."""
    hits = list(_TIME_RE.finditer(name))
    if not hits:
        return None
    if len(hits) > 1:
        raise TimeError(f"{name!r} has {len(hits)} times in its name ({', '.join(h.group(0) for h in hits)})")
    h = hits[0]
    u = _UNIT[h.group(2)]
    rest = re.sub(r"[_\-. ]+", "_", name[: h.start()] + "_" + name[h.end():]).strip("_")
    return float(h.group(1).replace("p", ".")) * HOURS[u], u, rest


def fmt_time(hours: float, unit: str | None = None) -> str:
    """0.5 -> '30 min', 4 -> '4 h', 48 -> '2 d' (the series' own unit when given)."""
    if hours == 0 and not unit:
        return "0"
    if unit is None:
        unit = "s" if hours < 1 / 60 else "min" if hours < 1 else "h" if hours < 48 or hours % 24 else "d"
    return f"{hours / HOURS[unit]:.4g} {unit}"


@dataclass
class Series:
    """One time course: its conditions in time order (hours)."""
    name: str
    time_of: dict[str, float]
    unit: str
    baseline_shared: bool = False     # time 0 is the control condition, shared with other series

    @property
    def conditions(self) -> list[str]:
        return sorted(self.time_of, key=lambda c: self.time_of[c])

    @property
    def times(self) -> list[float]:
        return [self.time_of[c] for c in self.conditions]


@dataclass
class Plan:
    series: list[Series] = field(default_factory=list)
    skipped: list[Series] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)   # (severity, message) for the doctor
    reason: str = ""


def _main_unit(units: list[str]) -> str:
    units = [u for u in units if u]
    return max(dict.fromkeys(units), key=units.count) if units else "h"


def plan_series(conditions: list[str], s, control: str | None) -> Plan:
    """Which conditions are time points of which series. s: analysis.Settings (times, time_unit, time_min_points)."""
    out = Plan()
    need = max(3, int(getattr(s, "time_min_points", 3)))
    explicit = getattr(s, "times", None) or {}
    found: dict[str, dict[str, float]] = {}
    units: dict[str, list[str]] = {}
    untimed: list[str] = []
    if explicit:
        by_low = {c.lower(): c for c in conditions}
        for key, val in explicit.items():
            c = by_low.get(str(key).lower())
            if c is None:
                out.problems.append(("input", f"analysis.times names {key!r}, which is not a condition here "
                                              f"(conditions: {', '.join(conditions)})"))
                continue
            try:
                t, u = parse_time(val, getattr(s, "time_unit", ""))
            except TimeError as exc:
                out.problems.append(("input", f"analysis.times {key}: {exc}"))
                continue
            try:
                hit = time_in_name(c)
            except TimeError:
                hit = None
            name = hit[2] if hit else ""
            found.setdefault(name, {})[c] = t
            units.setdefault(name, []).append(u)
        left = [c for c in conditions if not any(c in v for v in found.values())]
        if left and found:
            out.notes.append("time course: not in analysis.times, so left out: " + ", ".join(left))
    else:
        for c in conditions:
            try:
                hit = time_in_name(c)
            except TimeError as exc:
                out.problems.append(("warning", f"{exc}; that condition is left out of the time course. Set "
                                                "analysis.times to say which time it is."))
                continue
            if hit is None:
                untimed.append(c)
                continue
            t, u, name = hit
            found.setdefault(name, {})[c] = t
            units.setdefault(name, []).append(u)
        if not found:
            out.reason = ("No times were found: name the conditions with their time (Drug_0h, Drug_4h, Drug_24h) "
                          "or list them in experiment.yaml analysis.times.")
            return out
    if not found:
        out.reason = "analysis.times lists no condition of this experiment."
        return out
    for name, time_of in found.items():
        label = f"{name}: " if name else ""
        sr = Series(name, dict(time_of), _main_unit(units[name]))
        if 0.0 not in time_of.values() and control in untimed and control not in time_of:
            sr.time_of[control] = 0.0   # the untimed control is the start of this series
            sr.baseline_shared = True
        by_time: dict[float, list[str]] = {}
        for c, t in sr.time_of.items():
            by_time.setdefault(t, []).append(c)
        twice = {t: cs for t, cs in by_time.items() if len(cs) > 1}
        if twice:
            t, cs = next(iter(twice.items()))
            out.skipped.append(sr)
            out.problems.append(("warning", f"time course {label}{' and '.join(cs)} are both at {fmt_time(t, sr.unit)}"
                                            "; give them one condition name, or say which series each belongs to "
                                            "by naming them differently"))
            continue
        if len(sr.time_of) < need:
            out.skipped.append(sr)
            out.notes.append(f"time course {label}skipped: {len(sr.time_of)} time point"
                             f"{'s' if len(sr.time_of) != 1 else ''} ({', '.join(fmt_time(t, sr.unit) for t in sr.times)})"
                             f"; it needs at least {need} (analysis.time_min_points)")
            continue
        out.series.append(sr)
    if not out.series and not out.reason:
        out.reason = "; ".join(out.notes[-1:] or [m for _, m in out.problems[-1:]]) or "No series has enough time points."
    return out


# --------------------------------------------------------------- statistics --


@dataclass
class Course:
    series: Series
    rows: list[dict]                       # one per tested feature, strongest first
    patterns: list[dict]                   # [{"n", "profile": [log2fc per time]}], largest first
    samples: list[int]                     # columns of the processed matrix in this series, in time order
    sample_time: list[float]
    interaction_vs: str = ""
    untested: int = 0


@dataclass
class TimeResult:
    courses: list[Course]
    notes: list[str]
    alpha: float
    log2fc: float
    formula: str


def _weights(design, pairs: list[tuple[str, float]]) -> list[float]:
    c = [0.0] * len(design.columns)
    for cond, w in pairs:
        c[design.conditions.index(cond)] += w
    return c


def _f(est, sus, mod, idx: list[int], cmat, v, enough) -> tuple[list[float], list[float], int]:
    """Moderated F on the contrasts idx of est / sus (limma classifyTestsF)."""
    from ionomos.downstream import design as dz

    t_rows = []
    for i in range(len(est)):
        pv = mod.post[i]
        t_rows.append([est[i][k] / (sus[i][k] * math.sqrt(pv)) if enough[i] and not math.isnan(est[i][k])
                       and not math.isnan(sus[i][k]) and sus[i][k] > 0 and not math.isnan(pv) and pv > 0
                       else math.nan for k in idx])
    p = len(v)
    cs = [cmat[k] for k in idx]
    cvc = [[sum(ci[a] * v[a][b] * cj[b] for a in range(p) if ci[a] for b in range(p) if cj[b]) for cj in cs]
           for ci in cs]
    return dz.moderated_f(t_rows, cvc, mod.df2)


def _control_series(series: list[Series], s) -> Series | None:
    from ionomos.downstream.analysis import find_control

    names = [x.name for x in series if x.name]
    if len(series) < 2 or len(names) != len(series):
        return None
    try:
        hit = find_control(names, s)
    except Exception:  # noqa: BLE001 - analysis.control names a condition, not a series
        from dataclasses import replace

        hit = find_control(names, replace(s, control=None))
    return next((x for x in series if x.name == hit), None)


def patterns(profiles: list[list[float]], k_max: int = MAX_PATTERNS, iters: int = 50) -> list[int]:
    """Cluster index per profile: k-means on each profile scaled to its largest absolute value, k growing with
    the number of profiles (at most k_max), started from the first profile and then the farthest ones, so the
    result does not depend on a random seed."""
    n = len(profiles)
    if not n:
        return []
    scaled = []
    for pr in profiles:
        top = max((abs(v) for v in pr), default=0.0) or 1.0
        scaled.append([v / top for v in pr])
    k = max(1, min(k_max, round(math.sqrt(n / 2)) or 1, n))
    dist = lambda a, b: sum((x - y) ** 2 for x, y in zip(a, b, strict=True))  # noqa: E731
    centres = [scaled[0]]
    while len(centres) < k:
        far = max(range(n), key=lambda i: min(dist(scaled[i], c) for c in centres))
        if min(dist(scaled[far], c) for c in centres) < 1e-12:
            break
        centres.append(scaled[far])
    assign = [0] * n
    for _ in range(iters):
        new = [min(range(len(centres)), key=lambda j: dist(x, centres[j])) for x in scaled]
        if new == assign and _ > 0:
            break
        assign = new
        for j in range(len(centres)):
            mem = [scaled[i] for i in range(n) if assign[i] == j]
            if mem:
                centres[j] = [sum(col) / len(mem) for col in zip(*mem, strict=True)]
    return assign


def run(p, s, control: str | None, model=None) -> tuple[TimeResult | None, Plan]:
    """The time-course stage on processed data (fpa.Processed). model: analysis.Model (the comparisons' design)."""
    from ionomos.downstream import design as dz
    from ionomos.downstream import fpa

    m = p.m
    plan = plan_series(m.conditions, s, control)
    if not plan.series:
        return None, plan
    if m.kind != "intensity" or s.test != "limma":
        plan.reason = ("A time course was found, but its tests need intensity data and the limma model "
                       "(ratio data and the Welch / Student tests are compared condition by condition only).")
        plan.series = []
        return None, plan
    des = model.design if model is not None and getattr(model, "design", None) is not None else dz.plain(m)
    counts = [f.peptides for f in m.features] if s.variance_prior == "deqms" else None
    fit = dz.lm_fit(m.values, des.x)
    v = dz.cov_unscaled(des.x)
    mod = dz.squeeze(fit.s2, fit.df, counts, s.variance_prior)
    mv = 0 if p.imputation != "none" else s.min_valid
    ref = _control_series(plan.series, s)
    notes = list(plan.notes)
    courses = []
    for sr in plan.series:
        conds, times = sr.conditions, sr.times
        nt = len(conds)
        cmat = [_weights(des, [(conds[k], 1.0), (conds[0], -1.0)]) for k in range(1, nt)]
        centre = (nt - 1) / 2
        cmat.append(_weights(des, [(c, k - centre) for k, c in enumerate(conds)]))
        inter: list[int] = []
        if ref is not None and ref is not sr:
            other = {t: c for c, t in ref.time_of.items()}
            shared = [k for k in range(1, nt) if times[k] in other]
            if times[0] in other and shared and not (sr.baseline_shared and ref.baseline_shared):
                for k in shared:
                    inter.append(len(cmat))
                    cmat.append(_weights(des, [(conds[k], 1.0), (conds[0], -1.0), (other[times[k]], -1.0),
                                               (other[times[0]], 1.0)]))
        est, sus = dz.contrasts_fit(fit, v, cmat)
        groups = [dz._group_stats(m.values, dz._members(des, des.conditions.index(c))) for c in conds]
        enough = [not mv or all(g[0][i] >= mv for g in groups) for i in range(len(m.values))]
        fs, ps, _r = _f(est, sus, mod, list(range(nt - 1)), cmat, v, enough)
        qs = stats.bh_adjust(ps)
        tk = nt - 1
        tt, tp, _lo, _hi, tq = fpa._toptable([est[i][tk] if enough[i] else math.nan for i in range(len(est))],
                                             [sus[i][tk] for i in range(len(est))], mod.post, mod.dft)
        if inter:
            others = [dz._group_stats(m.values, dz._members(des, des.conditions.index(c)))
                      for c in ref.conditions if ref.time_of[c] in times]
            both = [enough[i] and (not mv or all(g[0][i] >= mv for g in others)) for i in range(len(m.values))]
            ifs, ips, _ = _f(est, sus, mod, inter, cmat, v, both)
            iqs = stats.bh_adjust(ips)
        rows, untested = [], 0
        for i, feat in enumerate(m.features):
            if math.isnan(ps[i]):
                untested += 1
                continue
            fc = [0.0] + [est[i][k] for k in range(nt - 1)]
            peak = max(range(nt), key=lambda k: abs(fc[k]))
            sig = qs[i] <= s.alpha and abs(fc[peak]) >= s.log2fc
            up, down = max(fc) >= s.log2fc, min(fc) <= -s.log2fc
            row = {"index": i, "series": sr.name, "id": feat.id, "label": feat.label, "description": feat.description,
                   "class": "not" if not sig else "mixed" if up and down else "up" if up else "down", "pattern": None,
                   "F": fs[i], "pvalue": ps[i], "qvalue": qs[i], "trend_t": _nan(tt[i]), "trend_pvalue": _nan(tp[i]),
                   "trend_qvalue": _nan(tq[i]), "max_log2fc": fc[peak], "peak_time": fmt_time(times[peak], sr.unit),
                   "fc": fc, "means": [g[1][i] for g in groups],
                   "times": "; ".join(fmt_time(t, sr.unit) for t in times),
                   "log2fc": "; ".join(f"{x:.4g}" for x in fc),
                   "mean_log2": "; ".join("" if math.isnan(g[1][i]) else f"{g[1][i]:.4g}" for g in groups),
                   "interaction_vs": "", "interaction_F": None, "interaction_pvalue": None,
                   "interaction_qvalue": None}
            if inter:
                row.update({"interaction_vs": ref.name, "interaction_F": _nan(ifs[i]),
                            "interaction_pvalue": _nan(ips[i]), "interaction_qvalue": _nan(iqs[i])})
            rows.append(row)
        rows.sort(key=lambda r: (r["pvalue"], -abs(r["max_log2fc"])))
        changing = [r for r in rows if r["class"] != "not"]
        found = []
        if changing:
            assign = patterns([r["fc"] for r in changing])
            order = sorted(set(assign), key=lambda j: -assign.count(j))
            for r, j in zip(changing, assign, strict=True):
                r["pattern"] = order.index(j) + 1
            for j in order:
                mem = [r["fc"] for r, a in zip(changing, assign, strict=True) if a == j]
                found.append({"n": len(mem), "profile": [statistics.median(col) for col in zip(*mem, strict=True)]})
        cols = [j for c in conds for j, x in enumerate(m.samples) if m.condition[x] == c]
        courses.append(Course(sr, rows, found, cols, [sr.time_of[m.condition[m.samples[j]]] for j in cols],
                              ref.name if inter else "", untested))
        label = f" ({sr.name})" if sr.name else ""
        if untested:
            notes.append(f"time course{label}: {untested:,} features not measured at every time point were not tested")
        if sr.baseline_shared:
            notes.append(f"time course{label}: {control} (no time in its name) is used as time 0")
    return TimeResult(courses, notes, s.alpha, s.log2fc, des.formula), plan


def _nan(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


# ------------------------------------------------------------------- outputs --


def table_rows(res: TimeResult) -> list[dict]:
    return [r for c in res.courses for r in c.rows]


def summary(res: TimeResult | None, plan: Plan, table: str | None) -> dict:
    """analysis.json "time_course"."""
    if res is None:
        return {"ran": False, "reason": plan.reason or "; ".join(m for _, m in plan.problems)}
    out = {"ran": True, "table": table, "alpha_adjusted": res.alpha, "log2fc": res.log2fc, "model": res.formula,
           "series": []}
    for c in res.courses:
        cls = [r["class"] for r in c.rows]
        entry = {"name": c.series.name, "times": [fmt_time(t, c.series.unit) for t in c.series.times],
                 "conditions": c.series.conditions, "tested": len(c.rows), "not_tested": c.untested,
                 **{k: cls.count(k) for k in CLASSES}, "patterns": [x["n"] for x in c.patterns],
                 "trend": sum(1 for r in c.rows if r["trend_qvalue"] is not None and r["trend_qvalue"] <= res.alpha)}
        if c.interaction_vs:
            entry["differs_from"] = {"series": c.interaction_vs,
                                     "features": sum(1 for r in c.rows if r["interaction_qvalue"] is not None
                                                     and r["interaction_qvalue"] <= res.alpha)}
        out["series"].append(entry)
    return out


def _num(v, digits: int = 4):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(f"{v:.{digits}g}") if v and abs(v) < 1e-3 else round(v, digits)


def report_payload(res: TimeResult | None, plan: Plan) -> dict:
    """The report's "time" data (report.js renderTime): per series the tested features as columns, each one's
    log2 fold changes against the first time point, the patterns, and the series' samples with their times
    (the page draws a feature's replicates from the matrix it already has)."""
    found = bool(plan.series or plan.skipped or plan.problems)
    if res is None:
        return {"ran": False, "found": found, "reason": plan.reason or "; ".join(m for _, m in plan.problems)}
    series = []
    for c in res.courses:
        rows = c.rows
        series.append({"name": c.series.name, "unit": c.series.unit, "times": c.series.times,
                       "labels": [fmt_time(t, c.series.unit) for t in c.series.times], "conds": c.series.conditions,
                       "samples": c.samples, "stime": c.sample_time, "i": [r["index"] for r in rows],
                       "cls": [r["class"] for r in rows], "pat": [r["pattern"] for r in rows],
                       "F": [_num(r["F"]) for r in rows], "p": [_num(r["pvalue"], 8) for r in rows],
                       "q": [_num(r["qvalue"], 8) for r in rows], "tt": [_num(r["trend_t"]) for r in rows],
                       "tq": [_num(r["trend_qvalue"], 8) for r in rows], "max": [_num(r["max_log2fc"]) for r in rows],
                       "peak": [r["fc"].index(r["max_log2fc"]) for r in rows],
                       "fc": [[_num(x, 3) for x in r["fc"]] for r in rows],
                       "patterns": [{"n": x["n"], "profile": [_num(v, 3) for v in x["profile"]]} for x in c.patterns],
                       "vs": c.interaction_vs,
                       "iq": [_num(r["interaction_qvalue"], 8) for r in rows] if c.interaction_vs else None,
                       "untested": c.untested})
    return {"ran": True, "found": True, "alpha": res.alpha, "lfc": res.log2fc, "model": res.formula,
            "series": series, "reason": ""}
