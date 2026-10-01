"""
Search quality per run (D55): what the search made of each raw file, next to the sample-level QC.

    res = run(workdir)                      # Result; res.runs is empty when the search wrote no such table
    summary(res, table)                     # analysis.json "psm_qc"
    report_payload(res)                     # the report's Search quality QC tab (report.js qcPsm)
    columns(res), table_rows(res)           # results/psm_qc.tsv

Nothing is parsed here. The numbers come from the reader the instrument QC trend already uses (qcmetrics.py):

    FragPipe psm.tsv     per run (the Spectrum column's run name): PSMs, peptides, proteins; the precursor mass
                         error in ppm (Observed Mass vs Calculated Peptide Mass, isotope-error corrected, |ppm|
                         over 50 left out as a mass offset): median, quartiles, 5th and 95th percentile; PSMs
                         with 0 / 1 / 2+ missed cleavages; PSMs per charge state; peptide lengths
    DIA-NN *stats.tsv    DIA-NN's own per-run summary (precursors, proteins, median MS1 / MS2 mass accuracy,
                         mean missed cleavages and charge, peak width), shown as it is

A run is a raw file: for TMT that is a fraction of a plex, not a sample. The "sample" beside it is the folder
its psm.tsv sits in (FragPipe's <experiment>_<bioreplicate>).

Two warnings, both for the doctor (PSM_MASS_ERROR, PSM_MISSED_CLEAVAGES). The limits are not the lab's: they
were set wide, so that only a run nobody would call normal is flagged, and are constants here until the lab
has looked at its own numbers (ROADMAP):

    PPM_WARN      a run's median precursor mass error is this far from 0 (the instrument's calibration)
    MISSED_WARN   this share of a run's PSMs has a missed cleavage (the digestion)
    MIN_PSMS      a run with fewer PSMs carrying the number is not judged

psm.tsv files are streamed row by row (qcmetrics); one over MAX_MB is left unread, with a note.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ionomos.downstream import qcmetrics

MAX_MB = 4096        # a psm.tsv / stats.tsv larger than this is not read (the QC trend's default limit)
PPM_WARN = 10.0      # |median precursor mass error|, ppm
MISSED_WARN = 0.5    # share of PSMs with at least one missed cleavage
MIN_PSMS = 100       # PSMs a run needs before it is judged
QUANTILES = ("q05", "q25", "median", "q75", "q95")

DIANN_COLUMNS = (("precursors", "precursors"), ("proteins", "proteins"), ("ms1_ppm", "ms1_mass_accuracy_ppm"),
                 ("ms2_ppm", "ms2_mass_accuracy_ppm"), ("missed", "mean_missed_cleavages"), ("charge", "mean_charge"),
                 ("fwhm", "peak_fwhm_min"))


@dataclass
class Result:
    runs: list[dict] = field(default_factory=list)     # FragPipe psm.tsv, a dict per run
    diann: list[dict] = field(default_factory=list)    # DIA-NN stats.tsv, a dict per run
    charges: list[int] = field(default_factory=list)   # every charge state seen, ascending
    lengths: dict[int, int] = field(default_factory=dict)  # peptide length -> PSMs, all runs together
    notes: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)  # (issue code, message) for the doctor
    reason: str = ""                                   # why there is nothing, when there is nothing

    @property
    def found(self) -> bool:
        return bool(self.runs or self.diann)


def _median_of_counts(counts: dict[int, int]) -> float | None:
    n = sum(counts.values())
    if not n:
        return None
    seen = 0
    keys = sorted(counts)
    for i, k in enumerate(keys):
        seen += counts[k]
        if seen * 2 > n:
            return float(k)
        if seen * 2 == n:
            return (k + keys[i + 1]) / 2
    return float(keys[-1])


def _run_row(run: str, rec: dict, workdir: Path) -> dict:
    m, d = rec["metrics"], rec["psm"]
    mc = {int(k): v for k, v in d["missed"].items()}
    mc_n = sum(mc.values())
    row = {"run": run, "sample": d["folder"] if d["folder"] != workdir.name else "",
           "psms": m.get("psms", 0), "peptides": m.get("peptides"), "proteins": m.get("proteins"),
           "ppm": d["ppm"], "ppm_n": d["ppm_n"],
           "missed": [mc.get(0, 0), mc.get(1, 0), sum(v for k, v in mc.items() if k >= 2)] if mc_n else None,
           "missed_n": mc_n, "missed_rate": (mc_n - mc.get(0, 0)) / mc_n if mc_n else None,
           "missed_mean": m.get("missed"), "charge": {int(z): k for z, k in d["charge"].items()},
           "charge_mean": m.get("charge"), "length": _median_of_counts(d["length"]), "flags": []}
    if row["ppm"] and row["ppm_n"] >= MIN_PSMS and abs(row["ppm"][2]) >= PPM_WARN:
        row["flags"].append("mass error")
    if mc_n >= MIN_PSMS and row["missed_rate"] >= MISSED_WARN:
        row["flags"].append("missed cleavages")
    return row


def _named(rows: list[dict], text, limit: int = 6) -> str:
    return ", ".join(f"{r['run']} ({text(r)})" for r in rows[:limit]) + (f" and {len(rows) - limit} more"
                                                                         if len(rows) > limit else "")


def run(workdir: Path, max_mb: float = MAX_MB) -> Result:
    """Per-run search quality from the tables in a search's output folder. Reads; writes nothing."""
    workdir = Path(workdir)
    psm, diann, notes = qcmetrics.search_tables(workdir, max_mb)
    res = Result(notes=notes)
    res.runs = [_run_row(run, psm[run], workdir) for run in sorted(psm) if psm[run].get("psm")]
    res.diann = [{"run": run, **{k: diann[run]["metrics"].get(k) for k, _c in DIANN_COLUMNS}}
                 for run in sorted(diann) if diann[run]["metrics"]]
    res.charges = sorted({z for r in res.runs for z in r["charge"]})
    for run in psm:
        for n, k in (psm[run].get("psm") or {}).get("length", {}).items():
            res.lengths[int(n)] = res.lengths.get(int(n), 0) + k
    if not res.found:
        res.reason = ("; ".join(notes) if notes else
                      f"the search wrote no psm.tsv (FragPipe) or DIA-NN stats.tsv in {workdir.name}/")
        return res
    off = [r for r in res.runs if "mass error" in r["flags"]]
    if off:
        res.problems.append(("PSM_MASS_ERROR",
                             f"{len(off)} of {len(res.runs)} run(s) have a median precursor mass error of "
                             f"{PPM_WARN:g} ppm or more: " + _named(off, lambda r: f"{r['ppm'][2]:+.1f} ppm") + "."))
    missed = [r for r in res.runs if "missed cleavages" in r["flags"]]
    if missed:
        res.problems.append(("PSM_MISSED_CLEAVAGES",
                             f"In {len(missed)} of {len(res.runs)} run(s), {MISSED_WARN:.0%} or more of the PSMs "
                             "have a missed cleavage: " + _named(missed, lambda r: f"{r['missed_rate']:.0%}") + "."))
    return res


def limits() -> dict:
    return {"mass_error_ppm": PPM_WARN, "missed_cleavage_rate": MISSED_WARN, "min_psms": MIN_PSMS}


def summary(res: Result | None, table: str | None = None, reason: str = "") -> dict:
    """analysis.json "psm_qc"."""
    if res is None or not res.found:
        return {"ran": False, "reason": reason or (res.reason if res else "")}
    med = [r["ppm"][2] for r in res.runs if r["ppm"]]
    rate = [r["missed_rate"] for r in res.runs if r["missed_rate"] is not None]
    return {"ran": True, "table": table, "runs": len(res.runs), "psms": sum(r["psms"] for r in res.runs),
            "mass_error_ppm": ({"median": round(statistics.median(med), 3), "min": round(min(med), 3),
                                "max": round(max(med), 3)} if med else None),
            "missed_cleavage_rate": ({"median": round(statistics.median(rate), 4), "max": round(max(rate), 4)}
                                     if rate else None),
            "flagged": {r["run"]: r["flags"] for r in res.runs if r["flags"]},
            "diann_runs": len(res.diann), "limits": limits(), "notes": res.notes}


def _r(v, digits: int = 3):
    return None if v is None else round(v, digits)


def report_payload(res: Result | None) -> dict | None:
    """The report's d["qc"]["psm"] (report.js qcPsm), or None when there is nothing to show. z holds each run's
    share of PSMs per charge state, in the order of "z"; len is the PSMs per peptide length from "lo" up."""
    if res is None or not res.found:
        return None
    runs = []
    for r in res.runs:
        n = sum(r["charge"].values())
        runs.append({"run": r["run"], "sample": r["sample"], "psms": r["psms"], "pep": r["peptides"],
                     "prot": r["proteins"], "ppm": r["ppm"], "ppmN": r["ppm_n"], "mc": r["missed"],
                     "mcRate": _r(r["missed_rate"], 4), "mcMean": _r(r["missed_mean"]),
                     "z": [_r(100 * r["charge"].get(z, 0) / n, 1) if n else None for z in res.charges],
                     "zMean": _r(r["charge_mean"]), "len": r["length"], "flags": r["flags"]})
    lo = min(res.lengths) if res.lengths else 0
    return {"runs": runs, "z": res.charges,
            "len": {"lo": lo, "n": [res.lengths.get(k, 0) for k in range(lo, max(res.lengths) + 1)]
                    if res.lengths else []},
            "diann": [{k: (_r(v, 4) if isinstance(v, float) else v) for k, v in d.items()} for d in res.diann],
            "limits": {"ppm": PPM_WARN, "missed": MISSED_WARN, "minPsms": MIN_PSMS}, "notes": res.notes}


def columns(res: Result) -> list[str]:
    return ["run", "sample", "source", "psms", "peptides", "proteins",
            *(f"mass_error_{q}_ppm" for q in QUANTILES), "mass_error_psms", "missed_cleavage_rate", "missed_0",
            "missed_1", "missed_2plus", "mean_missed_cleavages", "mean_charge",
            *(f"charge_{z}_pct" for z in res.charges), "peptide_length_median", "flags",
            *(c for k, c in DIANN_COLUMNS if k not in ("proteins", "missed", "charge"))]


def table_rows(res: Result) -> list[dict]:
    """results/psm_qc.tsv: a row per run and source; a column a source doesn't have is NA."""
    rows = []
    for r in res.runs:
        n = sum(r["charge"].values())
        row = {"run": r["run"], "sample": r["sample"], "source": "FragPipe psm.tsv", "psms": r["psms"],
               "peptides": r["peptides"], "proteins": r["proteins"], "mass_error_psms": r["ppm_n"],
               "missed_cleavage_rate": _r(r["missed_rate"], 4), "mean_missed_cleavages": r["missed_mean"],
               "mean_charge": r["charge_mean"], "peptide_length_median": r["length"], "flags": "; ".join(r["flags"])}
        for q, v in zip(QUANTILES, r["ppm"] or [None] * 5, strict=True):
            row[f"mass_error_{q}_ppm"] = v
        for k, name in enumerate(("missed_0", "missed_1", "missed_2plus")):
            row[name] = r["missed"][k] if r["missed"] else None
        for z in res.charges:
            row[f"charge_{z}_pct"] = _r(100 * r["charge"].get(z, 0) / n, 1) if n else None
        rows.append(row)
    for d in res.diann:
        rows.append({"run": d["run"], "sample": "", "source": "DIA-NN stats.tsv", "flags": "",
                     **{c: d.get(k) for k, c in DIANN_COLUMNS}})
    return rows
