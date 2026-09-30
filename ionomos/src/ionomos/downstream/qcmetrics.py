"""
Per-run instrument QC metrics, read from what the searches already wrote (D45).

    found = run_metrics(workdir, runs)      # {run stem: {"metrics": {...}, "sources": {...}, "rt": {...}}}

runs maps each run's file stem (the manifest's raw names) to (experiment, bioreplicate). Nothing is
re-searched or re-computed from spectra; every number comes from an engine's own table:

    DIA-NN  *stats.tsv       File.Name, Precursors.Identified, Proteins.Identified, Total.Quantity, FWHM.RT,
                             Median.Mass.Acc.MS1, Median.Mass.Acc.MS2, Average.Peptide.Charge,
                             Average.Missed.Tryptic.Cleavages  (DIA-NN's per-run summary; FragPipe-DIA and
                             DIA-NN standalone both write it next to report.tsv)
            *pg_matrix.tsv   proteins per run (cells > 0), when stats.tsv has no protein count
            report.tsv       RT of the 200 most intense precursors (Q.Value <= 1 %), for the RT drift check
    FragPipe psm.tsv         per run (the Spectrum column's run name): PSMs, peptides, proteins, summed
                             Intensity, median precursor mass error (Observed Mass vs Calculated Peptide Mass,
                             isotope-error corrected, ppm), mean missed cleavages and charge, charge-state
                             mix, RT of the 200 most intense peptides (Retention, seconds -> minutes)
            combined_protein.tsv  proteins for a run that is its experiment's only run (<exp> Spectral Count > 0)

Reading is bounded: tables are streamed row by row, a file bigger than max_mb is skipped with a note, and
at most MAX_ROWS rows are read from any one file. Every file is read in its own try: a broken table leaves a
note and the other metrics stand. Nothing here writes anything.
"""
from __future__ import annotations

import csv
import math
import re
import statistics
from pathlib import Path

from ionomos.downstream.quant import run_stem
from ionomos.downstream.tables import num

MAX_ROWS = 5_000_000   # rows read from any one table
RT_PEPTIDES = 200      # most intense peptides / precursors whose RT is kept per run
ISOTOPE = 1.00335      # C13 - C12, for isotope-error correction of precursor mass errors
MAX_PPM = 50.0         # |ppm| beyond this is a mass offset (open / offset search), not an error
SKIP = ("_previous_", "temp")

# DIA-NN stats.tsv column -> our metric (the first present wins)
DIANN_STATS = {
    "precursors": ("Precursors.Identified",),
    "proteins": ("Proteins.Identified",),
    "signal": ("Total.Quantity",),
    "fwhm": ("FWHM.RT",),
    "ms1_ppm": ("Median.Mass.Acc.MS1",),
    "ms2_ppm": ("Median.Mass.Acc.MS2",),
    "charge": ("Average.Peptide.Charge",),
    "missed": ("Average.Missed.Tryptic.Cleavages",),
}


class MetricsError(ValueError):
    """A table is there but can't be read as the format it claims."""


def _find(workdir: Path, pattern: str, depth: int = 3) -> list[Path]:
    out = []
    for p in sorted(Path(workdir).rglob(pattern)):
        try:
            rel = p.relative_to(workdir).parts
        except ValueError:
            continue
        if len(rel) > depth or any(s in part for part in rel[:-1] for s in SKIP):
            continue
        if p.is_file():
            out.append(p)
    return out


def _rows(path: Path, max_mb: float):
    """(header, iterator of dict rows), streamed. Raises MetricsError for a file too big to read."""
    size = path.stat().st_size
    if max_mb and size > max_mb * 1024 * 1024:
        raise MetricsError(f"{path.name} is {size / 1024 ** 2:,.0f} MB (over qc_trend.max_file_mb); skipped")
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        header = next(csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE), [])

    def gen():  # opens the file only when iterated, so an early return leaves nothing open
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
            next(reader, None)
            for k, line in enumerate(reader):
                if k >= MAX_ROWS:
                    break
                if line:
                    yield dict(zip(header, line, strict=False))

    return header, gen()


class _Matcher:
    """Table run names (full paths, stems with _uncalibrated or an Xcalibur stamp) -> the manifest's run stems."""

    def __init__(self, runs):
        from ionomos.naming import strip_acq_stamp

        self.runs = set(runs)
        self._strip = strip_acq_stamp
        self._by_canon: dict[str, list[str]] = {}
        for r in self.runs:
            self._by_canon.setdefault(strip_acq_stamp(r).lower(), []).append(r)
        self._cache: dict[str, str | None] = {}

    def __call__(self, name: str) -> str | None:
        if name in self._cache:
            return self._cache[name]
        stem = run_stem(name) if name else ""
        found = None
        for cand in (stem, re.sub(r"_(?:uncalibrated|calibrated)$", "", stem, flags=re.IGNORECASE)):
            if cand in self.runs:
                found = cand
                break
        if found is None:
            hits = self._by_canon.get(self._strip(stem).lower(), [])
            found = hits[0] if len(hits) == 1 else None
        self._cache[name] = found
        return found


def _top_rt(best: dict[str, tuple[float, float]]) -> dict[str, float]:
    """{peptide: (intensity, rt min)} -> the RT_PEPTIDES most intense peptides' RT, rounded."""
    top = sorted(best.items(), key=lambda kv: -kv[1][0])[:RT_PEPTIDES]
    return {p: round(rt, 3) for p, (_i, rt) in sorted(top)}


def _new(found: dict, run: str) -> dict:
    return found.setdefault(run, {"metrics": {}, "sources": {}, "rt": {}})


# ------------------------------------------------------------------ DIA-NN --


def read_diann_stats(path: Path, match, found: dict, max_mb: float) -> None:
    header, rows = _rows(path, max_mb)
    if "File.Name" not in header:
        return  # some other tool's *stats.tsv
    cols = {k: next((c for c in names if c in header), None) for k, names in DIANN_STATS.items()}
    for r in rows:
        run = match(r.get("File.Name", ""))
        if run is None:
            continue
        rec = _new(found, run)
        for key, col in cols.items():
            v = num(r.get(col)) if col else None
            if v is not None:
                rec["metrics"][key] = v
                rec["sources"][key] = f"{path.name}: {col}"


def read_pg_matrix(path: Path, match, found: dict, max_mb: float) -> None:
    """Proteins per run = protein groups with a quantity > 0 in the run's column (used only when the stats file
    gave no protein count)."""
    header, rows = _rows(path, max_mb)
    runs = {h: match(h) for h in header}
    runs = {h: r for h, r in runs.items() if r is not None}
    counts = dict.fromkeys(runs, 0)
    for r in rows:
        for h in runs:
            v = num(r.get(h))
            if v is not None and v > 0:
                counts[h] += 1
    for h, run in runs.items():
        rec = _new(found, run)
        if "proteins" not in rec["metrics"]:
            rec["metrics"]["proteins"] = counts[h]
            rec["sources"]["proteins"] = f"{path.name}: protein groups > 0"


def read_diann_report_rt(path: Path, match, found: dict, max_mb: float) -> None:
    header, rows = _rows(path, max_mb)
    need = ("Run", "RT")
    if not all(c in header for c in need):
        return
    seq = "Stripped.Sequence" if "Stripped.Sequence" in header else ("Modified.Sequence" if "Modified.Sequence"
                                                                      in header else None)
    if seq is None:
        return
    qcol = "Q.Value" if "Q.Value" in header else None
    icol = next((c for c in ("Precursor.Quantity", "Precursor.Normalised") if c in header), None)
    best: dict[str, dict[str, tuple[float, float]]] = {}
    for r in rows:
        if qcol is not None:
            q = num(r.get(qcol))
            if q is None or q > 0.01:
                continue
        run = match(r.get("Run", ""))
        rt = num(r.get("RT"))
        pep = r.get(seq) or ""
        if run is None or rt is None or not pep:
            continue
        inten = (num(r.get(icol)) if icol else None) or 0.0
        per = best.setdefault(run, {})
        if pep not in per or inten > per[pep][0]:
            per[pep] = (inten, rt)
    for run, per in best.items():
        rec = _new(found, run)
        rec["rt"] = _top_rt(per)
        rec["sources"]["rt"] = f"{path.name}: RT (min), {len(rec['rt'])} most intense precursors"


# ---------------------------------------------------------------- FragPipe --


def spectrum_run(spectrum: str) -> str:
    """'HeLa_QC_1.04512.04512.2' -> 'HeLa_QC_1' (FragPipe's run.scan.scan.charge)."""
    parts = spectrum.rsplit(".", 3)
    return parts[0] if len(parts) == 4 else spectrum


def ppm_error(observed: float | None, calculated: float | None, delta: float | None = None) -> float | None:
    """Precursor mass error in ppm, isotope-error corrected; None beyond MAX_PPM (a mass offset, not an error)."""
    if calculated is None or calculated <= 0:
        return None
    d = (observed - calculated) if observed is not None else delta
    if d is None:
        return None
    iso = round(d / ISOTOPE)
    if abs(iso) > 3:
        return None
    ppm = (d - iso * ISOTOPE) / calculated * 1e6
    return ppm if abs(ppm) <= MAX_PPM else None


def read_psm(path: Path, match, found: dict, max_mb: float) -> None:
    header, rows = _rows(path, max_mb)
    for c in ("Spectrum", "Peptide", "Charge"):
        if c not in header:
            raise MetricsError(f"{path.name} has no {c} column (not a FragPipe psm.tsv)")
    acc: dict[str, dict] = {}
    for r in rows:
        run = match(spectrum_run(r.get("Spectrum", "")))
        if run is None:
            continue
        a = acc.get(run)
        if a is None:
            a = acc[run] = {"psms": 0, "peptides": set(), "proteins": set(), "signal": 0.0, "ppm": [], "missed": [],
                            "charge": {}, "best": {}}
        a["psms"] += 1
        pep = r.get("Peptide") or ""
        a["peptides"].add(pep)
        if r.get("Protein"):
            a["proteins"].add(r["Protein"])
        inten = num(r.get("Intensity"))
        if inten is not None and inten > 0:
            a["signal"] += inten
        ppm = ppm_error(num(r.get("Observed Mass")), num(r.get("Calculated Peptide Mass")), num(r.get("Delta Mass")))
        if ppm is not None:
            a["ppm"].append(ppm)
        mc = num(r.get("Number of Missed Cleavages"))
        if mc is not None:
            a["missed"].append(mc)
        z = num(r.get("Charge"))
        if z is not None:
            a["charge"][int(z)] = a["charge"].get(int(z), 0) + 1
        rt = num(r.get("Retention"))
        if rt is not None and pep and (pep not in a["best"] or (inten or 0) > a["best"][pep][0]):
            a["best"][pep] = (inten or 0.0, rt / 60.0)
    src = path.name
    for run, a in acc.items():
        rec = _new(found, run)
        m, s = rec["metrics"], rec["sources"]
        m["psms"], s["psms"] = a["psms"], f"{src}: rows"
        m["peptides"], s["peptides"] = len(a["peptides"]), f"{src}: distinct Peptide"
        m["proteins"], s["proteins"] = len(a["proteins"]), f"{src}: distinct Protein"
        if a["signal"] > 0:
            m["signal"], s["signal"] = a["signal"], f"{src}: summed Intensity"
        if a["ppm"]:
            m["ms1_ppm"] = round(statistics.median(a["ppm"]), 4)
            s["ms1_ppm"] = f"{src}: median Observed vs Calculated Peptide Mass (ppm, isotope-corrected)"
        if a["missed"]:
            m["missed"], s["missed"] = round(sum(a["missed"]) / len(a["missed"]), 4), f"{src}: Number of Missed Cleavages"
        n = sum(a["charge"].values())
        if n:
            m["charge"] = round(sum(z * k for z, k in a["charge"].items()) / n, 4)
            s["charge"] = f"{src}: Charge"
            rec["charges"] = {str(z): round(100 * k / n, 1) for z, k in sorted(a["charge"].items())}
        if "Retention" in header:
            rec["rt"] = _top_rt(a["best"])
            s["rt"] = f"{src}: Retention (s -> min), {len(rec['rt'])} most intense peptides"


def read_combined_protein(path: Path, runs: dict[str, tuple[str, int]], found: dict, max_mb: float) -> None:
    """Protein count for a run that is the only run of its FragPipe experiment (<exp>_<rep> or <exp>)."""
    header, rows = _rows(path, max_mb)
    groups: dict[tuple[str, int], list[str]] = {}
    for run, (exp, rep) in runs.items():
        groups.setdefault((exp, rep), []).append(run)
    cols = {}
    for (exp, rep), members in groups.items():
        if len(members) != 1:
            continue
        for name in (f"{exp}_{rep}", exp):
            col = next((f"{name} {s}" for s in ("Total Spectral Count", "Spectral Count")
                        if f"{name} {s}" in header), None)
            if col:
                cols[members[0]] = col
                break
    if not cols:
        return
    counts = dict.fromkeys(cols, 0)
    for r in rows:
        for run, col in cols.items():
            v = num(r.get(col))
            if v is not None and v > 0:
                counts[run] += 1
    for run, col in cols.items():
        rec = _new(found, run)
        rec["metrics"]["proteins"] = counts[run]
        rec["sources"]["proteins"] = f"{path.name}: {col} > 0"


# ------------------------------------------------------------------- entry --


def run_metrics(workdir: Path, runs: dict[str, tuple[str, int]], max_mb: float = 4096) -> tuple[dict, list[str]]:
    """Metrics for the given runs from a search's output folder. Returns ({run: record}, notes).
    A run the tables don't mention is absent from the result."""
    workdir = Path(workdir)
    found: dict[str, dict] = {}
    notes: list[str] = []
    if not workdir.is_dir():
        return found, [f"no search output folder at {workdir}"]
    match = _Matcher(runs)

    def attempt(fn, path, *args):
        try:
            fn(path, *args)
        except (OSError, MetricsError, csv.Error, ValueError) as exc:
            notes.append(f"{path.name}: {exc}")

    stats = _find(workdir, "*stats.tsv")
    for p in stats:
        attempt(read_diann_stats, p, match, found, max_mb)
    for p in _find(workdir, "*pg_matrix.tsv"):
        attempt(read_pg_matrix, p, match, found, max_mb)
    for p in _find(workdir, "report.tsv") + _find(workdir, "*.report.tsv"):
        attempt(read_diann_report_rt, p, match, found, max_mb)
    for p in _find(workdir, "psm.tsv"):
        attempt(read_psm, p, match, found, max_mb)
    for p in _find(workdir, "combined_protein.tsv", depth=1):
        attempt(read_combined_protein, p, runs, found, max_mb)
    for rec in found.values():
        for k, v in list(rec["metrics"].items()):
            if isinstance(v, float) and not math.isfinite(v):
                del rec["metrics"][k]
    if not found and not notes:
        notes.append("no table with per-run QC numbers (DIA-NN stats.tsv / pg_matrix, FragPipe psm.tsv) "
                     "named these runs")
    return found, notes
