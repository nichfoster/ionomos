"""
Instrument QC trending: the recurring QC standard (a HeLa / K562 digest) tracked run after run (D45).

    after_job(job, cfg)          # postprocess hook: a done job with QC-standard runs -> metrics, store, page, item
    rows, notes = scan(cfg)      # read-only: every past QC run under users_root (`ionomos qc-trend --rebuild`)
    series = analyse(load(log_dir), settings)
    page = write_page(log_dir, settings)

Which runs count (config `qc_trend:`, docs/NAMING_CONVENTION.md "QC standard runs"): a run whose .raw name
or folder name contains one of `match` (case-insensitive; `_`, `-`, `.` and spaces are all one separator, so
`_qc_` matches QC as a word), or any run of a method listed in `methods`. A folder that is an experiment —
two or more samples with two or more replicates each — is not a QC standard even if a name matches (HeLa is
also a cell line people do experiments on). `exclude` patterns win over everything.

Per run (downstream/qcmetrics.py): IDs, signal, peak width, mass error, missed cleavages, charge and the RT of
the most intense peptides, from the tables the search wrote. Rows go to <log_dir>/qc_trend.jsonl (names.py):
one JSON object per line, appended; the last line for a run wins, so re-running a job updates its row
instead of adding one. The file is Ionomos' own bookkeeping; experiment folders are only read.

Trending, per series (instrument · method · standard · amount): a baseline (the first `baseline_runs` runs,
or those acquired between `baseline_from` and `baseline_to`) sets each metric's mean and SD; later runs get
Levey-Jennings z-scores, the Westgard rules 1-3s, 2-2s, R-4s, 10-x (1-2s is a warning), and a tabular CUSUM
(k = 0.5 SD, h = 5 SD, from the end of the baseline; z clipped at ±3 and the sums reset after a run another
rule rejected, so one gross outlier doesn't read as drift for weeks) for slow drift. Only a change in the bad direction (fewer IDs, broader peaks, any
mass-error shift) raises an item; better-than-baseline is reported as "watch". A broken rule on the newest
run of a series raises an attention item (severity warning; no pop-up unless `popup: true`), and the next
run that is back within the baseline closes it.

Everything here is isolated: after_job never raises, and a table that can't be read leaves a note on the run.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ionomos import names

log = logging.getLogger("ionomos.qctrend")

DEFAULTS: dict = {
    "enabled": True,
    "match": ["hela", "k562", "qc_std", "qcstd", "_qc_"],
    "exclude": [],
    "methods": [],
    "instrument": "",
    "baseline_runs": 10,
    "baseline_from": "",
    "baseline_to": "",
    "popup": False,
    "max_file_mb": 4096,
}
MIN_BASELINE = 3          # a baseline needs at least this many runs with the metric
CUSUM_K, CUSUM_H, CUSUM_CLIP = 0.5, 5.0, 3.0  # in SD; z is clipped at ±3 before it is summed
KIND = "qc_trend"         # attention item kind


class QCTrendError(ValueError):
    """qc_trend: in config.yaml is malformed."""


def settings_from(raw) -> dict:
    """config.yaml qc_trend: -> a complete settings dict (DEFAULTS filled in). Raises QCTrendError."""
    if raw is None:
        return dict(DEFAULTS)
    if not isinstance(raw, dict):
        raise QCTrendError("must be a mapping (enabled, match, exclude, methods, instrument, baseline_runs, ...)")
    bad = sorted(set(map(str, raw)) - set(DEFAULTS))
    if bad:
        raise QCTrendError(f"{bad[0]}: unknown setting (known: {', '.join(DEFAULTS)})")
    s = {**DEFAULTS, **raw}
    for k in ("match", "exclude", "methods"):
        v = s[k] if s[k] is not None else []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise QCTrendError(f"{k} must be a list of words, e.g. [hela, k562]")
        s[k] = [x.strip() for x in v]
    for k in ("enabled", "popup"):
        if not isinstance(s[k], bool):
            raise QCTrendError(f"{k} must be true or false")
    try:
        s["baseline_runs"] = int(s["baseline_runs"])
        s["max_file_mb"] = float(s["max_file_mb"] or 0)
    except (TypeError, ValueError):
        raise QCTrendError("baseline_runs and max_file_mb must be numbers") from None
    if s["baseline_runs"] < MIN_BASELINE:
        raise QCTrendError(f"baseline_runs must be at least {MIN_BASELINE}")
    s["instrument"] = str(s["instrument"] or "").strip()
    dates = []
    for k in ("baseline_from", "baseline_to"):
        v = s[k]
        if isinstance(v, date):
            v = v.isoformat()
        v = str(v or "").strip()
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise QCTrendError(f"{k} must be a date like 2026-09-01") from None
        s[k] = v
        dates.append(v)
    if bool(dates[0]) != bool(dates[1]):
        raise QCTrendError("baseline_from and baseline_to: give both (or neither)")
    if dates[0] and dates[0] > dates[1]:
        raise QCTrendError("baseline_from is after baseline_to")
    return s


def settings_of(cfg) -> dict:
    return getattr(cfg, "qc_trend", None) or dict(DEFAULTS)


# ---------------------------------------------------------------- matching --


def _words(name: str) -> str:
    return "_" + re.sub(r"[\s\-.]+", "_", str(name).lower()) + "_"


def matched(name: str, patterns: list[str]) -> str | None:
    """The first pattern found in name (case-insensitive, any separator), or None."""
    text = _words(name)
    for p in patterns:
        if re.sub(r"[\s\-.]+", "_", p.lower()) in text:
            return p
    return None


_AMOUNT = re.compile(r"(?<![A-Za-z0-9])(\d+(?:[.p]\d+)?)[_\s-]?(ng|ug|µg)(?![A-Za-z])", re.IGNORECASE)


def _amount(*names_: str) -> str:
    for n in names_:
        m = _AMOUNT.search(n or "")
        if m:
            return f"{m.group(1).replace('p', '.')}{m.group(2).lower().replace('µ', 'u')}"
    return ""


_PRETTY = {"hela": "HeLa", "k562": "K562"}


def _standard(pattern: str) -> str:
    p = pattern.strip("_ -.")
    return _PRETTY.get(p.lower(), p.upper() if len(p) <= 4 else p)


def _stem(file: str) -> str:
    from ionomos.downstream.quant import run_stem

    return run_stem(str(file).replace("\\", "/"))


def qc_runs(record: dict, method: str | None, s: dict) -> tuple[list[dict], str]:
    """The QC-standard runs of one experiment, and why (or why not). Each run:
    {file, stem, experiment, rep, pattern, series_part}."""
    plan = record.get("plan") or {}
    folder = (plan.get("folder") or {}).get("original") or Path(str(plan.get("dest") or "")).name
    manifest = plan.get("manifest") or []
    if not manifest:
        return [], "no raw files recorded"
    if s["exclude"] and matched(folder, s["exclude"]):
        return [], f"folder matches qc_trend.exclude '{matched(folder, s['exclude'])}'"
    by_method = bool(method) and method in s["methods"]
    folder_hit = matched(folder, s["match"])
    reps: dict[str, set] = {}
    for line in manifest:
        reps.setdefault(str(line.get("experiment")), set()).add(line.get("bioreplicate"))
    designed = sum(len(v) >= 2 for v in reps.values()) >= 2
    if designed and not by_method:
        return [], "an experiment (2+ samples with 2+ replicates each), not a QC standard"
    out = []
    for line in manifest:
        stem = _stem(line.get("file", ""))
        if s["exclude"] and matched(stem, s["exclude"]):
            continue
        hit = matched(stem, s["match"]) or folder_hit
        if not (hit or by_method):
            continue
        out.append({"file": str(line.get("file", "")), "stem": stem, "experiment": str(line.get("experiment", "")),
                    "rep": int(line.get("bioreplicate") or 1), "pattern": hit or "",
                    "standard": _standard(hit) if hit else "QC", "amount": _amount(stem, folder)})
    why = (f"method {method}" if by_method else f"name matches '{folder_hit}'" if folder_hit
           else "file names match" if out else "no name matches qc_trend.match")
    return out, why


# -------------------------------------------------------------- run rows --


def _key(dest, stem: str) -> str:
    return f"{os.path.normcase(os.path.abspath(str(dest)))}|{stem}"


ACQ_SOURCE = {"file name": "name"}  # acqtime's word -> the word older store rows use


def acquired(raw: Path, stem: str, fallback: str = "", known: dict | None = None) -> tuple[str, str]:
    """When the run was acquired (acqtime.py, D78): what intake recorded in ionomos.json ("acquisition", passed
    as `known`), else the raw file's own header, ThermoRawFileParser's output, the Xcalibur stamp in its name,
    the raw file's modification time; else when it was filed. Returns (ISO local time, source)."""
    from ionomos import acqtime

    info = known if known and known.get("time") else None
    if info is None:
        raw = Path(raw)
        info = acqtime.read(raw, [raw.parent / "sage_mzml", raw.parent.parent / "sage_mzml"], stem)
    if info.get("time"):
        src = info.get("source") or "file time"
        return str(info["time"])[:19], ACQ_SOURCE.get(src, src)
    when = str(fallback or "")[:19]
    return (when or datetime.now().replace(microsecond=0).isoformat()), "filed"


def _workdir(dest: Path, record: dict) -> Path:
    wd = (record.get("run") or {}).get("workdir")
    if wd and Path(wd).is_dir():
        return Path(wd)
    for name in ("fragpipe", "diann", "sage"):
        if (dest / name).is_dir():
            return dest / name
    return dest


def build_rows(dest: Path, record: dict, method: str | None, s: dict, job_id: int | None = None,
               data_type: str | None = None) -> tuple[list[dict], list[str]]:
    """Store rows for one experiment's QC-standard runs (empty when it has none). Only reads."""
    from ionomos.downstream import qcmetrics

    dest = Path(dest)
    runs, why = qc_runs(record, method, s)
    if not runs:
        return [], [why]
    wd = _workdir(dest, record)
    found, notes = qcmetrics.run_metrics(wd, {r["stem"]: (r["experiment"], r["rep"]) for r in runs},
                                         max_mb=s["max_file_mb"])
    folder = ((record.get("plan") or {}).get("folder") or {})
    dtype = data_type or (record.get("method_config") or {}).get("data_type") or ""
    now = datetime.now().replace(microsecond=0).isoformat()
    from ionomos.downstream import RESULTS

    report = dest / RESULTS / "report.html"
    rows = []
    for r in runs:
        got = found.get(r["stem"]) or {"metrics": {}, "sources": {}, "rt": {}}
        when, source = acquired(dest / r["file"], r["stem"], record.get("queued_at") or "",
                                (record.get("acquisition") or {}).get(r["file"]))
        acq = dtype or ("DIA" if "precursors" in got["metrics"] else "DDA" if "psms" in got["metrics"] else "")
        series = " · ".join(x for x in (s["instrument"], method or "", r["standard"], r["amount"]) if x)
        rows.append({
            "key": _key(dest, r["stem"]), "series": series, "instrument": s["instrument"], "method": method or "",
            "acquisition": acq, "run": r["stem"], "raw": r["file"], "dest": str(dest),
            "experiment": folder.get("original") or dest.name, "user": folder.get("user") or "",
            "job_id": job_id, "acquired": when, "acquired_from": source, "searched": now, "matched": why,
            "metrics": got["metrics"], "sources": got["sources"], "rt": got.get("rt") or {},
            "charges": got.get("charges") or {}, "report": str(report) if report.is_file() else "",
            "notes": ([] if got["metrics"] else ["no QC numbers found for this run in the search output"])
            + notes[:5],
        })
    return rows, notes


# ------------------------------------------------------------------- store --


def store_path(log_dir: Path) -> Path:
    return Path(log_dir) / names.QC_TREND_STORE


def page_path(log_dir: Path) -> Path:
    return Path(log_dir) / names.QC_TREND_PAGE


def load(log_dir: Path) -> list[dict]:
    """Every run in the store, the last line per run winning. A damaged line is skipped."""
    p = store_path(log_dir)
    rows: dict[str, dict] = {}
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("key"):
                    rows.pop(r["key"], None)
                    rows[r["key"]] = r
    except OSError:
        return []
    return list(rows.values())


def _lines(p: Path) -> int:
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def append(log_dir: Path, rows: list[dict]) -> None:
    """Add (or update) runs: appended, one JSON object per line; compacted when mostly superseded lines."""
    if not rows:
        return
    p = store_path(log_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, separators=(",", ":"), allow_nan=False, default=str) + "\n" for r in rows)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(text)
    if _lines(p) > 2 * len(load(log_dir)) + 20:
        compact(log_dir)


def compact(log_dir: Path) -> None:
    """Rewrite the store with one line per run (temp file + replace; our own file only)."""
    p = store_path(log_dir)
    rows = load(log_dir)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, separators=(",", ":"), allow_nan=False, default=str) + "\n"
                           for r in rows), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- trending --


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    bad: str          # "low" | "high" | "both": which direction is a problem
    scale: str        # "relative" (% of baseline) | "absolute" (difference in unit)
    advice: str
    log: bool = False  # trended as log10
    floor: float = 0.0  # smallest SD used (absolute metrics); relative ones use 2 % of the mean


IDS = "check the column and the spray, and how much was injected"
METRICS = (
    Metric("precursors", "Precursors", "", "low", "relative", IDS),
    Metric("proteins", "Proteins", "", "low", "relative", IDS),
    Metric("peptides", "Peptides", "", "low", "relative", IDS),
    Metric("psms", "PSMs", "", "low", "relative", IDS),
    Metric("signal", "Signal", "log10 summed intensity", "low", "relative",
           "check the spray (emitter, source) and the injection amount", log=True, floor=0.02),
    Metric("fwhm", "Peak width", "FWHM, min", "high", "relative", "peaks are broader: check the column and the LC gradient"),
    Metric("ms1_ppm", "MS1 mass error", "ppm", "both", "absolute", "calibrate the instrument", floor=0.2),
    Metric("ms2_ppm", "MS2 mass error", "ppm", "both", "absolute", "calibrate the instrument", floor=0.2),
    Metric("rt_shift", "RT shift", "min", "both", "absolute",
           "check the LC (pressure, leaks, column temperature, solvents)", floor=0.05),
    Metric("missed", "Missed cleavages", "per peptide", "high", "absolute",
           "digestion looks less complete: check the standard (a new batch, an old aliquot?)", floor=0.01),
    Metric("charge", "Mean charge", "", "both", "absolute", "the charge mix changed: check the spray and source",
           floor=0.01),
)
BY_KEY = {m.key: m for m in METRICS}
REJECT = ("1-3s", "2-2s", "R-4s", "10-x")
RULES = {
    "1-3s": "one run beyond 3 SD",
    "2-2s": "two runs in a row beyond 2 SD on the same side",
    "R-4s": "two runs in a row more than 4 SD apart (one above +2 SD, one below -2 SD)",
    "10-x": "ten runs in a row on the same side of the mean",
    "1-2s": "one run beyond 2 SD (a warning, not a rejection)",
    "CUSUM": f"a slow drift: the runs since the baseline add up to more than {CUSUM_H:g} SD on one side "
             f"(tabular CUSUM, k = {CUSUM_K:g} SD)",
}


def _baseline(runs: list[dict], s: dict) -> tuple[list[int], str, bool]:
    """Indices of the baseline runs, a sentence saying which they are, and whether it is complete."""
    if s["baseline_from"]:
        idx = [i for i, r in enumerate(runs)
               if s["baseline_from"] <= (r.get("acquired") or "")[:10] <= s["baseline_to"]]
        if len(idx) >= MIN_BASELINE:
            return idx, f"runs acquired {s['baseline_from']} to {s['baseline_to']} (pinned, {len(idx)} runs)", True
        note = (f"only {len(idx)} run(s) acquired {s['baseline_from']} to {s['baseline_to']}; "
                f"using the first {s['baseline_runs']} instead. ")
    else:
        note = ""
    n = min(s["baseline_runs"], len(runs))
    return list(range(n)), note + f"the first {s['baseline_runs']} runs", len(runs) >= s["baseline_runs"]


def _value(m: Metric, r: dict):
    v = (r.get("metrics") or {}).get(m.key)
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    if m.log:
        return math.log10(v) if v > 0 else None
    return float(v)


def _rt_shifts(runs: list[dict], base: list[int]) -> None:
    """rt_shift per run: median RT difference from the baseline over the peptides both share (>= 5)."""
    per: dict[str, list[float]] = {}
    for i in base:
        for p, rt in (runs[i].get("rt") or {}).items():
            per.setdefault(p, []).append(rt)
    ref = {p: statistics.median(v) for p, v in per.items() if len(v) >= max(1, len(base) // 2)}
    for r in runs:
        diffs = [rt - ref[p] for p, rt in (r.get("rt") or {}).items() if p in ref]
        r.setdefault("metrics", {})
        r["metrics"] = dict(r["metrics"])
        if len(diffs) >= 5:
            r["metrics"]["rt_shift"] = round(statistics.median(diffs), 4)
        else:
            r["metrics"].pop("rt_shift", None)


def _change(m: Metric, x: float, mean: float) -> tuple[float, str]:
    """(signed change, words) of a value against the baseline mean, on the metric's own scale."""
    if m.scale == "relative":
        pct = (10 ** (x - mean) - 1) * 100 if m.log else ((x - mean) / mean * 100 if mean else 0.0)
        return pct, f"{abs(pct):.0f}% {'below' if pct < 0 else 'above'} baseline"
    d = x - mean
    unit = f" {m.unit}" if m.unit and m.unit not in ("per peptide",) else ""
    return d, f"{d:+.2f}{unit} from baseline"


def _bad(m: Metric, sign: float, rule: str) -> bool:
    if rule == "R-4s" or m.bad == "both":
        return True
    return (sign < 0) if m.bad == "low" else (sign > 0)


def analyse(rows: list[dict], s: dict) -> list[dict]:
    """Group runs into series and judge each. Returns JSON-ready dicts (newest run last in each series)."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("series") or "QC", []).append(dict(r))
    out = []
    for name in sorted(groups):
        runs = sorted(groups[name], key=lambda r: (r.get("acquired") or "", r.get("run") or ""))
        base, base_text, ready = _baseline(runs, s)
        _rt_shifts(runs, base)
        in_base = set(base)
        last_base = max(base) if base else -1
        metrics = []
        for m in METRICS:
            xs = [_value(m, r) for r in runs]
            if all(x is None for x in xs):
                continue
            bx = [xs[i] for i in base if xs[i] is not None]
            if len(bx) < MIN_BASELINE:
                metrics.append({"key": m.key, "label": m.label, "unit": m.unit, "log": m.log, "mean": None, "sd": None,
                                "n": len(bx), "values": xs, "z": [None] * len(runs)})
                continue
            mean = statistics.fmean(bx)
            sd = statistics.stdev(bx) if len(bx) > 1 else 0.0
            sd = max(sd, m.floor, 0.02 * abs(mean) if m.scale == "relative" and not m.log else 0.0, 1e-9)
            z = [None if x is None else (x - mean) / sd for x in xs]
            metrics.append({"key": m.key, "label": m.label, "unit": m.unit, "log": m.log, "mean": mean, "sd": sd,
                            "n": len(bx), "values": xs, "z": z})
        for r in runs:
            r["flags"] = {}
        for mt in metrics:
            if mt["mean"] is None:
                continue
            m = BY_KEY[mt["key"]]
            z = mt["z"]
            seen: list[float] = []
            cp = cm = 0.0
            for i, r in enumerate(runs):
                zi = z[i]
                if zi is None:
                    continue
                prev = seen[-1] if seen else None
                seen.append(zi)
                if i in in_base:
                    continue
                if i > last_base:  # drift is measured from the end of the baseline on (not from runs before it)
                    zc = max(-CUSUM_CLIP, min(CUSUM_CLIP, zi))  # one gross outlier is the 1-3s rule's business
                    cp, cm = max(0.0, cp + zc - CUSUM_K), max(0.0, cm - zc - CUSUM_K)
                hits = []
                if abs(zi) > 3:
                    hits.append(("1-3s", zi))
                if prev is not None and ((zi > 2 and prev > 2) or (zi < -2 and prev < -2)):
                    hits.append(("2-2s", zi))
                if prev is not None and ((zi > 2 and prev < -2) or (zi < -2 and prev > 2)):
                    hits.append(("R-4s", zi))
                last = seen[-10:]
                if len(last) == 10 and (all(v > 0 for v in last) or all(v < 0 for v in last)):
                    hits.append(("10-x", zi))
                if cp > CUSUM_H:
                    hits.append(("CUSUM", 1.0))
                if cm > CUSUM_H:
                    hits.append(("CUSUM", -1.0))
                if abs(zi) > 2 and not any(h[0] in REJECT for h in hits):
                    hits.append(("1-2s", zi))
                if hits:
                    r["flags"][m.key] = [{"rule": h, "bad": _bad(m, sgn, h), "dir": 1 if sgn > 0 else -1}
                                         for h, sgn in hits]
                if any(h[0] in REJECT for h in hits):
                    cp = cm = 0.0  # a run a rule already rejected is dealt with: drift counting starts again
        for i, r in enumerate(runs):
            r["status"], r["verdict"] = _verdict(r, i, runs, metrics, in_base, ready, s)
        latest = runs[-1]
        out.append({"name": name, "acquisition": latest.get("acquisition") or "", "runs": runs, "metrics": metrics,
                    "baseline": base, "baseline_text": base_text, "ready": ready,
                    "status": latest["status"], "verdict": latest["verdict"]})
    return out


def _verdict(r: dict, i: int, runs: list[dict], metrics: list[dict], in_base: set, ready: bool, s: dict):
    if not ready or i in in_base:
        if not ready:
            return "baseline", f"Building the baseline: {len(runs)} of {s['baseline_runs']} QC runs"
        return "baseline", "Part of the baseline"
    if not any(mt["values"][i] is not None for mt in metrics):
        why = "; ".join(r.get("notes") or []) or "no table named this run"
        return "nodata", f"No QC numbers were found for this run ({why})"
    bad, good, watch = [], [], []
    for mt in metrics:
        flags = r["flags"].get(mt["key"]) or []
        if not flags or mt["values"][i] is None:
            continue
        m = BY_KEY[mt["key"]]
        _d, words = _change(m, mt["values"][i], mt["mean"])
        if all(f["rule"] == "CUSUM" for f in flags):  # a slow drift: the run itself may still look fine
            words = f"drifting {'down' if flags[0]['dir'] < 0 else 'up'} since the baseline ({words} now)"
        rules = ", ".join(f["rule"] for f in flags)
        if any(f["bad"] and f["rule"] in (*REJECT, "CUSUM") for f in flags):
            bad.append((m.advice, f"{m.label} {words} ({rules})"))
        elif any(f["bad"] for f in flags):
            watch.append(f"{m.label} {words} ({rules})")
        else:
            good.append(f"{m.label} {words} ({rules}, better than baseline: a new column or standard? "
                        "If it stays, pin a new baseline)")
    if bad:  # in METRICS order (IDs first), metrics with the same advice together
        by_advice: dict[str, list[str]] = {}
        for advice, text in bad:
            by_advice.setdefault(advice, []).append(text)
        return "warning", "; ".join(f"{', '.join(texts)} — {advice}" for advice, texts in by_advice.items())
    if watch or good:
        return "watch", "Within limits; watch: " + "; ".join(watch + good)
    return "ok", "All metrics within the baseline"


# ------------------------------------------------------------------ output --


def write_page(log_dir: Path, s: dict, rows: list[dict] | None = None) -> Path:
    """(Re)write <log_dir>/qc_trend.html from the store. Returns its path."""
    from ionomos.downstream import qcpage

    rows = load(log_dir) if rows is None else rows
    html = qcpage.render(analyse(rows, s), s, store=store_path(log_dir))
    p = page_path(log_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(html, encoding="utf-8", newline="\n")
    os.replace(tmp, p)
    return p


def page_for(config_path) -> Path:
    """The page to open (the app's Instrument QC button): rebuilt from the store when missing or older."""
    from ionomos.config import load as load_config

    cfg = load_config(config_path, check_paths=False)
    page, store = page_path(cfg.log_dir), store_path(cfg.log_dir)
    try:
        stale = not page.is_file() or (store.is_file() and store.stat().st_mtime > page.stat().st_mtime)
    except OSError:
        stale = True
    return write_page(cfg.log_dir, settings_of(cfg)) if stale else page


def sync_attention(log_dir: Path, series: list[dict], s: dict, only: set[str] | None = None,
                   job_id: int | None = None) -> None:
    """Raise an item for each series whose newest run broke a rule; close it once the newest run is fine."""
    from ionomos import attention

    page = page_path(log_dir)
    for ser in series:
        if only is not None and ser["name"] not in only:
            continue
        key = f"{KIND}:{ser['name']}"
        latest = ser["runs"][-1]
        if ser["status"] == "nodata":
            continue  # nothing to judge: an open item stays open
        if ser["status"] != "warning":
            for it in attention.items(log_dir):
                if it.key == key:
                    attention.resolve(log_dir, it.id)
            continue
        advice = []
        for mt in ser["metrics"]:
            if any(f["bad"] for f in latest["flags"].get(mt["key"], [])):
                a = BY_KEY[mt["key"]].advice
                if a not in advice:
                    advice.append(a[0].upper() + a[1:])
        attention.raise_item(
            log_dir, KIND, f"Instrument QC: {ser['name']} is off its baseline",
            f"{latest['run']} ({latest['acquired'][:16].replace('T', ' ')}): {latest['verdict']}", key=key,
            severity="warning", dest=latest.get("dest"), job_id=latest.get("job_id") or job_id, causes=advice,
            fixes=["Open the QC trend page: Jobs tab → Instrument QC, or `ionomos qc-trend --open`",
                   "Fix the cause and run the QC standard again: this closes by itself when a run is back "
                   "within the baseline",
                   "A deliberate change (new column, new standard)? Pin a new baseline with qc_trend.baseline_from "
                   "/ baseline_to in config.yaml"],
            details="\n".join(f"{mt['label']}: {', '.join(f['rule'] for f in latest['flags'][mt['key']])}"
                              for mt in ser["metrics"] if latest["flags"].get(mt["key"])),
            data={"page": str(page), "series": ser["name"], "run": latest["run"], "popup": bool(s["popup"])})


def after_job(job, cfg) -> list[str]:
    """Postprocess hook for a done job: its QC-standard runs (if any) -> store, page, attention item.
    Returns one verdict line per run for the job's record. Never raises: QC trending must not touch the job."""
    try:
        s = settings_of(cfg)
        if not s.get("enabled", True):
            return []
        dest = Path(job.dest_dir)
        try:
            record = json.loads(names.status_path(dest).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = dict(getattr(job, "parsed", None) or {})
        dtype = None
        mc = (getattr(cfg, "methods", {}) or {}).get(job.method)
        if mc is not None:
            dtype = mc.data_type
        rows, _notes = build_rows(dest, record, job.method, s, job_id=job.id, data_type=dtype)
        if not rows:
            return []
        log_dir = cfg.log_dir
        append(log_dir, rows)
        allrows = load(log_dir)
        series = analyse(allrows, s)
        write_page(log_dir, s, allrows)
        touched = {r["series"] for r in rows}
        sync_attention(log_dir, series, s, only=touched, job_id=job.id)
        keys = {r["key"] for r in rows}
        lines = []
        for ser in series:
            for r in ser["runs"]:
                if r["key"] in keys:
                    lines.append(f"{r['run']}: {r['status']} — {r['verdict']}")
                    log.info("job %s QC %s (%s): %s", job.id, r["run"], ser["name"], r["verdict"])
        return lines
    except Exception:  # noqa: BLE001 - trending is an extra; the job is done whatever happens here
        log.exception("instrument QC trending failed for job %s (the job itself is fine)", getattr(job, "id", "?"))
        return []


def scan(cfg) -> tuple[list[dict], list[str]]:
    """Every past QC-standard run of a done job under users_root. Read-only (for `ionomos qc-trend --rebuild`)."""
    s = settings_of(cfg)
    rows, notes = [], []
    for status in names.status_files(cfg.users_root):
        try:
            record = json.loads(status.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            notes.append(f"{status}: {exc}")
            continue
        if record.get("status") != "done":
            continue
        method = ((record.get("plan") or {}).get("folder") or {}).get("method")
        mc = (getattr(cfg, "methods", {}) or {}).get(method)
        try:
            got, _n = build_rows(status.parent, record, method, s, job_id=record.get("job_id"),
                                 data_type=mc.data_type if mc is not None else None)
        except Exception as exc:  # noqa: BLE001 - one odd folder must not stop the scan
            notes.append(f"{status.parent.name}: {exc}")
            continue
        rows += got
    return rows, notes
