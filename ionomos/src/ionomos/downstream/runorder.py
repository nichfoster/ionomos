"""
Run order (D78): each sample's QC numbers against the order the samples were acquired in.

    res = run(pm, record, dest, scorecard, psm_view)   # Result; res.reason says why there is nothing
    summary(res)                                        # analysis.json "run_order"
    report_payload(res)                                 # the report's Run order QC tab (report.js qcRun) and
                                                        #   the run_order figure (sectionfigs.py)
    res.problems                                        # [(code, message)] for the doctor

Acquisition time per raw file comes from ionomos.json "acquisition" (intake, acqtime.py), else it is read now
from the raw files (read-only). A sample's time is its first raw file's (fractions); samples that share raw
files (TMT channels, acquired together) have no order of their own, so there is nothing to test.

Per sample: identifications, missing values, the median log2 intensity before normalisation (the scorecard,
insights.py), and from the search (psmqc.py, D55) PSMs, the median precursor mass error and the missed-cleavage
rate, or DIA-NN's precursors and MS1 mass accuracy.

    trend(order, y, strata)        Is there a drift? A stratified Mann-Kendall test (Hirsch and Slack's seasonal
                                   Kendall test, the conditions as the seasons): only pairs of samples of the
                                   same condition are compared, so a condition that differs, or was acquired as
                                   a block, is not a drift. The p-value is exact (the conditions' Mahonian
                                   distributions convolved, no ties assumed) up to EXACT_MAX_PAIRS pairs, else
                                   normal with the tie-corrected variance. The size is the stratified Theil-Sen
                                   slope (the median within-condition pairwise slope) times the runs: the change
                                   from the first run to the last. Spearman's rho of the within-condition
                                   residuals with the order is shown beside it.
    confounding(order, conditions) Were the conditions acquired in blocks? eta squared of the run positions by
                                   condition (the share of where a sample sits in the run that its condition
                                   explains; 1 = every condition in one block, about 0 = interleaved), and how
                                   often a random order is as aligned (seeded permutations).

Warnings (constants here, wide, until the lab has seen its own numbers):
    RUN_ORDER_DRIFT        a metric's trend has p < ALPHA and changes by at least its limit over the run
    RUN_ORDER_CONFOUNDED   eta squared >= ETA_WARN with two or more conditions of two or more samples
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ionomos.downstream.quant import RAW_EXTS, match_run_stem, run_stem

MIN_SAMPLES = 6         # samples with a known time (and the metric) before a trend is tested
MIN_CONFOUND = 4        # ... before the confounding is looked at
ALPHA = 0.01            # a trend's p-value must be below this ...
ETA_WARN = 0.6          # conditions explain at least this share of the run positions: acquired in blocks
EXACT_MAX_PAIRS = 1500  # within-condition pairs up to which the trend's p-value is exact
PERMUTATIONS = 4000
SEED = 20261004

# key, label, unit, how the size is judged ("rel": a share of the median, "abs": in the metric's own unit), limit
METRICS = (
    ("ids", "identifications", "", "rel", 0.10),
    ("missing", "missing values", "%", "abs", 5.0),
    ("median", "median log2 intensity (before normalisation)", "log2", "abs", 0.5),
    ("psms", "PSMs", "", "rel", 0.10),
    ("ppm", "median precursor mass error", "ppm", "abs", 3.0),
    ("missed", "missed-cleavage rate", "", "abs", 0.05),
    ("precursors", "precursors (DIA-NN)", "", "rel", 0.10),
    ("ms1_ppm", "MS1 mass accuracy (DIA-NN)", "ppm", "abs", 3.0),
)
SAME_AS = {"missing": "ids"}  # the same fact seen from the other side: not warned about twice


@dataclass
class Result:
    samples: list[dict] = field(default_factory=list)  # in run order; samples without a time last
    metrics: list[dict] = field(default_factory=list)
    confound: dict | None = None
    problems: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def timed(self) -> list[dict]:
        return [s for s in self.samples if s["order"] is not None]


# ------------------------------------------------------------------ statistics --


def mahonian(n: int) -> list[float]:
    """P(k inversions) for a random permutation of n distinct items, k = 0 .. n(n-1)/2."""
    dist = [1.0]
    for m in range(2, n + 1):
        pre = [0.0]
        for v in dist:
            pre.append(pre[-1] + v)
        last = len(dist) - 1
        dist = [(pre[min(k, last) + 1] - pre[max(0, k - m + 1)]) / m for k in range(len(dist) + m - 1)]
    return dist


def _convolve(a: list[float], b: list[float]) -> list[float]:
    out = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                out[i + j] += x * y
    return out


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def trend(order: list[float], y: list[float], strata: list[str] | None = None) -> dict | None:
    """Stratified Mann-Kendall trend of y with order, and the stratified Theil-Sen slope (see the module doc).
    None when no two samples of one stratum can be compared."""
    n = len(y)
    strata = strata or [""] * n
    groups: dict[str, list[int]] = {}
    for i, g in enumerate(strata):
        groups.setdefault(g, []).append(i)
    S, pairs, var, slopes, sizes = 0, 0, 0.0, [], []
    for idx in groups.values():
        idx = sorted(idx, key=lambda i: order[i])
        k = len(idx)
        if k < 2:
            continue
        sizes.append(k)
        pairs += k * (k - 1) // 2
        for a in range(k):
            for b in range(a + 1, k):
                i, j = idx[a], idx[b]
                S += _sign(y[j] - y[i]) if order[j] != order[i] else 0
                if order[j] != order[i]:
                    slopes.append((y[j] - y[i]) / (order[j] - order[i]))
        ties: dict[float, int] = {}
        for i in idx:
            ties[y[i]] = ties.get(y[i], 0) + 1
        var += (k * (k - 1) * (2 * k + 5) - sum(t * (t - 1) * (2 * t + 5) for t in ties.values())) / 18
    if not pairs or not slopes:
        return None
    if pairs <= EXACT_MAX_PAIRS:
        dist = [1.0]
        for k in sizes:
            dist = _convolve(dist, mahonian(k))
        p = min(1.0, sum(pr for q, pr in enumerate(dist) if abs(pairs - 2 * q) >= abs(S) - 1e-9))
        how = "exact"
    else:
        z = (S - _sign(S)) / math.sqrt(var) if var > 0 else 0.0
        p = math.erfc(abs(z) / math.sqrt(2))
        how = "normal"
    slope = statistics.median(slopes)
    span = max(order) - min(order)
    resid = []
    for idx in groups.values():
        med = statistics.median(y[i] for i in idx)
        resid += [(order[i], y[i] - med) for i in idx]
    from ionomos.downstream.insights import spearman

    rho = spearman([a for a, _ in resid], [b for _, b in resid]) if len(groups) < n else None
    return {"S": S, "pairs": pairs, "tau": S / pairs, "p": p, "p_method": how, "slope": slope,
            "change": slope * span, "rho": rho, "n": n}


def _ranks(xs: list[float]) -> list[float]:
    from ionomos.downstream.insights import _ranks as ranks

    return ranks(xs)


def _eta2(ranks: list[float], labels: list[str]) -> float:
    mean = sum(ranks) / len(ranks)
    total = sum((r - mean) ** 2 for r in ranks)
    if total <= 0:
        return 0.0
    by: dict[str, list[float]] = {}
    for r, g in zip(ranks, labels, strict=True):
        by.setdefault(g, []).append(r)
    between = sum(len(v) * (sum(v) / len(v) - mean) ** 2 for v in by.values())
    return between / total


def _spans(positions: list[int]) -> str:
    """[1, 2, 3, 5, 7, 8] -> '1–3, 5, 7–8'."""
    out, ps = [], sorted(positions)
    i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[j] + 1:
            j += 1
        out.append(str(ps[i]) if i == j else f"{ps[i]}–{ps[j]}")
        i = j + 1
    return ", ".join(out)


def confounding(order: list[float], conditions: list[str], permutations: int = PERMUTATIONS,
                seed: int = SEED) -> dict | None:
    """How far the run order follows the conditions (see the module doc). None with fewer than two conditions."""
    n = len(order)
    if n < 2 or len(set(conditions)) < 2:
        return None
    ranks = _ranks(list(order))
    eta = _eta2(ranks, conditions)
    rng = random.Random(seed)
    labels = list(conditions)
    hits = 0
    for _ in range(permutations):
        rng.shuffle(labels)
        if _eta2(ranks, labels) >= eta - 1e-12:
            hits += 1
    seq = [c for _, c in sorted(zip(order, conditions, strict=True), key=lambda t: t[0])]
    changes = sum(1 for a, b in zip(seq, seq[1:], strict=False) if a != b)
    sizes: dict[str, int] = {}
    for c in conditions:
        sizes[c] = sizes.get(c, 0) + 1
    expected = (n - 1) * (1 - sum(k * (k - 1) for k in sizes.values()) / (n * (n - 1)))
    pos = {c: [k + 1 for k, x in enumerate(seq) if x == c] for c in sizes}
    return {"eta2": eta, "p": (hits + 1) / (permutations + 1), "changes": changes, "expected_changes": expected,
            "positions": {c: _spans(v) for c, v in pos.items()},
            "judged": n >= MIN_CONFOUND and sum(1 for k in sizes.values() if k >= 2) >= 2}


# ---------------------------------------------------------------- the samples --


def _folder_lines(dest: Path) -> list[tuple[str, str, int | None]]:
    """(file relative to dest, experiment, replicate) for the raw files in an experiment folder without a
    manifest, named as intake would read them."""
    from ionomos.naming import NamingError, parse_raw_name

    out = []
    for sub in ("", "raw"):
        d = dest / sub if sub else dest
        try:
            items = sorted(d.iterdir())
        except OSError:
            continue
        for p in items:
            if p.name.startswith(".") or not any(p.name.lower().endswith(e) for e in RAW_EXTS):
                continue
            exp, rep = run_stem(p.name), None
            for method in ("DIA", "isoDTB"):
                try:
                    r = parse_raw_name(run_stem(p.name) + ".raw", method)
                    exp, rep = r.sample, r.rep
                    break
                except NamingError:
                    continue
            out.append((f"{sub}/{p.name}" if sub else p.name, exp, rep))
    return out


def sample_files(pm, record: dict | None, dest: Path) -> dict[str, list[str]]:
    """{sample: its raw files (relative to dest)}: the ionomos.json manifest, else the raw files in the folder,
    matched to the samples by the quant table's run names, <experiment>_<replicate>, or the file's own name."""
    samples = list(pm.samples)
    out: dict[str, list[str]] = {s: [] for s in samples}
    lines = [(str(x.get("file", "")), str(x.get("experiment", "")), x.get("bioreplicate"))
             for x in ((record or {}).get("plan") or {}).get("manifest") or [] if isinstance(x, dict)]
    if not lines:
        lines = _folder_lines(Path(dest))
    stems = {run_stem(f): f for f, _e, _r in lines}
    by_stem: dict[str, str] = {}
    for s, stem in (pm.meta.get("manifest_run") or {}).items():
        by_stem.setdefault(str(stem), s)
    for s in samples:  # a column named after its raw file (DIA-NN, a path)
        col = (getattr(pm, "columns", None) or {}).get(s) or ""
        if col and run_stem(col) != col:
            hit = match_run_stem(run_stem(col), stems)
            if hit:
                by_stem.setdefault(hit, s)
    for f, exp, rep in lines:
        stem = run_stem(f)
        s = by_stem.get(stem)
        if s is None and rep is not None and f"{exp}_{rep}" in out:
            s = f"{exp}_{rep}"
        if s is None and stem in out:
            s = stem
        if s is not None:
            out[s].append(f)
    return out


def _times(files: dict[str, list[str]], record: dict | None, dest: Path) -> dict[str, dict]:
    from ionomos import acqtime

    known = (record or {}).get("acquisition") or {}
    look = [dest / "sage_mzml"]
    out = {}
    for fs in files.values():
        for f in fs:
            info = known.get(f) if isinstance(known.get(f), dict) and known[f].get("time") else None
            out[f] = info or acqtime.read(dest / f, look)
    return out


def _psm_by_sample(psm: dict | None, stem_sample: dict[str, str]) -> dict[str, dict]:
    """The search's per-run numbers (psmqc.report_payload) summed / pooled per sample."""
    acc: dict[str, dict[str, list]] = {}
    for r in (psm or {}).get("runs") or []:
        hit = match_run_stem(run_stem(str(r.get("run", ""))), stem_sample)
        if hit is None:
            continue
        a = acc.setdefault(stem_sample[hit], {})
        a.setdefault("psms", []).append(r.get("psms"))
        a.setdefault("ppm", []).append((r.get("ppm") or [None] * 5)[2])
        a.setdefault("missed", []).append(r.get("mcRate"))
    for r in (psm or {}).get("diann") or []:
        hit = match_run_stem(run_stem(str(r.get("run", ""))), stem_sample)
        if hit is None:
            continue
        a = acc.setdefault(stem_sample[hit], {})
        a.setdefault("precursors", []).append(r.get("precursors"))
        a.setdefault("ms1_ppm", []).append(r.get("ms1_ppm"))
    out = {}
    for s, a in acc.items():
        row = {}
        for k, vs in a.items():
            vs = [v for v in vs if isinstance(v, (int, float))]
            if vs:
                row[k] = float(sum(vs)) if k in ("psms", "precursors") else statistics.median(vs)
        out[s] = row
    return out


def _fmt(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.1f}%"
    if unit in ("ppm", "log2"):
        return f"{v:+.2f} {unit}"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{v:.3g}"


def _drift_text(mt: dict) -> str:
    t, unit = mt["trend"], mt["unit"]
    word = "rises" if t["change"] > 0 else "falls"
    if mt["judge"] == "rel" and mt["median"]:
        size = f"by {abs(t['change']) / abs(mt['median']):.0%} ({_fmt(abs(t['change']), unit)} over the run)"
    elif unit in ("ppm", "log2"):
        size = f"by {abs(t['change']):.2f} {unit}"
    elif unit == "%":
        size = f"by {abs(t['change']):.1f} percentage points"
    else:
        size = f"by {abs(t['change']):.3g}"
    p = f"{t['p']:.1e}" if t["p"] < 1e-3 else f"{t['p']:.3f}"
    return f"{mt['label']} {word} {size} (Kendall tau {t['tau']:+.2f}, p {p})"


def run(pm, record: dict | None, dest: Path, scorecard: list[dict] | None = None, psm: dict | None = None) -> Result:
    """Run-order QC for the analysed samples of pm (the processed matrix). Reads raw-file headers when
    ionomos.json has no times; writes nothing."""
    res = Result()
    if pm is None or not pm.samples:
        res.reason = "no samples were analysed"
        return res
    dest = Path(dest)
    files = sample_files(pm, record, dest)
    shared: dict[str, int] = {}
    for fs in files.values():
        for f in fs:
            shared[f] = shared.get(f, 0) + 1
    for stem in (pm.meta.get("manifest_run") or {}).values():
        shared[f"run:{stem}"] = shared.get(f"run:{stem}", 0) + 1
    if getattr(pm, "exp", "") == "TMT" or any(k > 1 for k in shared.values()):
        res.reason = ("the samples share raw files (labelled channels, such as TMT, are acquired together), so a "
                      "sample has no place of its own in the run order")
        return res
    times = _times(files, record, dest)
    rows = []
    for s in pm.samples:
        got = [(times[f]["time"], times[f]) for f in files[s] if times.get(f, {}).get("time")]
        info = min(got, key=lambda t: t[0])[1] if got else {}
        approx = any(t.get("approximate") for _w, t in got)
        rows.append({"sample": s, "condition": pm.condition.get(s, ""), "order": None,
                     "acquired": info.get("time"), "source": info.get("source"), "approximate": approx,
                     "files": len(files[s])})
    timed = sorted((r for r in rows if r["acquired"]), key=lambda r: (r["acquired"], r["sample"]))
    for k, r in enumerate(timed, 1):
        r["order"] = k
    res.samples = timed + [r for r in rows if not r["acquired"]]
    if not timed:
        nofile = sum(1 for r in rows if not r["files"])
        res.reason = ("no raw files were found for the samples" if nofile == len(rows) else
                      "no acquisition time could be read for the samples' raw files")
        return res
    if len(timed) < len(rows):
        res.notes.append(f"acquisition time known for {len(timed)} of {len(rows)} samples; the others are left out")
    if any(r["approximate"] for r in timed):
        res.notes.append("the order of some samples is from their raw files' modification time, which is approximate")

    stem_sample = {run_stem(f): s for s, fs in files.items() for f in fs}
    by_psm = _psm_by_sample(psm, stem_sample)
    card = {r["sample"]: r for r in scorecard or []}
    kind = getattr(pm, "kind", "intensity")
    for key, label, unit, judge, limit in METRICS:
        vals = []
        for r in res.samples:
            c, q = card.get(r["sample"]) or {}, by_psm.get(r["sample"]) or {}
            v = {"ids": c.get("ids"), "missing": c.get("missing_pct"),
                 "median": c.get("median") if kind == "intensity" else None}.get(key, q.get(key))
            vals.append(float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None)
        if sum(1 for v in vals if v is not None) < 2:
            continue
        mt = {"key": key, "label": label, "unit": unit, "judge": judge, "limit": limit, "values": vals,
              "trend": None, "flagged": False, "median": None}
        pts = [(r["order"], v, r["condition"]) for r, v in zip(res.samples, vals, strict=True)
               if r["order"] is not None and v is not None]
        if pts:
            mt["median"] = statistics.median(v for _o, v, _c in pts)
        if len(pts) >= MIN_SAMPLES:
            t = trend([o for o, _v, _c in pts], [v for _o, v, _c in pts], [c for _o, _v, c in pts])
            if t is not None:
                mt["trend"] = t
                big = abs(t["change"]) >= (limit * abs(mt["median"]) if judge == "rel" else limit)
                mt["flagged"] = t["p"] < ALPHA and big and (judge != "rel" or bool(mt["median"]))
        res.metrics.append(mt)
    flagged = [m for m in res.metrics if m["flagged"]]
    flagged = [m for m in flagged if SAME_AS.get(m["key"]) not in {x["key"] for x in flagged}]
    approx = " The order is partly from the raw files' modification times, which is approximate." \
        if any(r["approximate"] for r in timed) else ""
    if flagged:
        res.problems.append(("RUN_ORDER_DRIFT",
                             f"Over the {len(timed)} runs, in the order they were acquired: "
                             + "; ".join(_drift_text(m) for m in flagged)
                             + ". Tested within each condition, so a difference between conditions does not count "
                               "as drift." + approx))
    cf = confounding([r["order"] for r in timed], [r["condition"] for r in timed])
    if cf is not None:
        cf["flagged"] = cf["judged"] and cf["eta2"] >= ETA_WARN
        res.confound = cf
        if cf["flagged"]:
            where = "; ".join(f"{c} in runs {p}" for c, p in cf["positions"].items())
            also = (" A drift was found in this experiment as well (see the other warning), so part of the "
                    "differences between conditions may come from it." if flagged else "")
            res.problems.append(("RUN_ORDER_CONFOUNDED",
                                 f"The conditions were acquired in blocks: {where}. The condition explains "
                                 f"{cf['eta2']:.0%} of where a sample sits in the run order (a random order "
                                 f"gives as much in {cf['p']:.1%} of cases). Anything that drifted during the "
                                 "run then differs between the conditions too, and the statistics cannot tell it "
                                 "from the biology." + also + approx))
    return res


# ------------------------------------------------------------------ outputs --


def _r(v, digits: int = 4):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return round(v, digits)


def limits() -> dict:
    return {"alpha": ALPHA, "eta2": ETA_WARN, "min_samples": MIN_SAMPLES,
            "change": {k: {"rel" if j == "rel" else "abs": lim} for k, _l, _u, j, lim in METRICS}}


def summary(res: Result | None, reason: str = "") -> dict:
    """analysis.json "run_order"."""
    if res is None or not res.samples or not res.timed:
        return {"ran": False, "reason": reason or (res.reason if res else ""),
                **({"samples": res.samples} if res is not None and res.samples else {})}
    return {"ran": True,
            "samples": [{k: r[k] for k in ("sample", "condition", "order", "acquired", "source", "approximate", "files")}
                        for r in res.samples],
            "metrics": {m["key"]: ({"tau": _r(m["trend"]["tau"]), "p": _r(m["trend"]["p"], 8),
                                    "p_method": m["trend"]["p_method"], "slope_per_run": _r(m["trend"]["slope"], 6),
                                    "change_over_run": _r(m["trend"]["change"]), "rho": _r(m["trend"]["rho"]),
                                    "n": m["trend"]["n"], "flagged": m["flagged"]}
                                   if m["trend"] else {"tested": False})
                        for m in res.metrics},
            "confounding": ({k: (_r(v) if isinstance(v, float) else v) for k, v in res.confound.items()}
                            if res.confound else None),
            "flagged": [c for c, _m in res.problems], "limits": limits(), "notes": res.notes}


def report_payload(res: Result | None) -> dict | None:
    """The report's d["qc"]["run"] (report.js qcRun): samples in run order, a list of metrics with their values
    in that order and their trends, the confounding check. None when no sample has a time."""
    if res is None or not res.timed:
        return None
    srcs: dict[str, int] = {}
    for r in res.timed:
        srcs[r["source"] or "?"] = srcs.get(r["source"] or "?", 0) + 1
    cf = res.confound
    return {
        "samples": [{"s": r["sample"], "c": r["condition"], "o": r["order"], "t": (r["acquired"] or "")[:19],
                     "src": r["source"] or "", "approx": bool(r["approximate"]), "nf": r["files"]}
                    for r in res.samples],
        "metrics": [{"key": m["key"], "label": m["label"], "unit": m["unit"], "v": [_r(v) for v in m["values"]],
                     "flag": m["flagged"], "judge": m["judge"], "limit": m["limit"], "med": _r(m["median"]),
                     **({"tau": _r(m["trend"]["tau"], 3), "p": _r(m["trend"]["p"], 8), "slope": _r(m["trend"]["slope"], 6),
                         "change": _r(m["trend"]["change"]), "rho": _r(m["trend"]["rho"], 3), "n": m["trend"]["n"]}
                        if m["trend"] else {})}
                    for m in res.metrics],
        "confound": ({"eta2": _r(cf["eta2"], 3), "p": _r(cf["p"], 5), "changes": cf["changes"],
                      "expected": _r(cf["expected_changes"], 2), "flag": bool(cf.get("flagged")),
                      "positions": cf["positions"]} if cf else None),
        "sources": srcs, "approx": any(r["approximate"] for r in res.timed),
        "limits": {"alpha": ALPHA, "eta2": ETA_WARN, "min": MIN_SAMPLES}, "notes": res.notes,
    }
