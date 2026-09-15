"""
DDA parser — turns a FragPipe (MSFragger/Philosopher/IonQuant) output folder
into a normalized RunRecord.

WHERE EACH NUMBER COMES FROM (and why):
  * n_proteins  -> row count of protein.tsv  (proteins passing the workflow FDR)
  * n_peptides  -> row count of peptide.tsv   (peptides passing the workflow FDR)
  * monitor peptide intensity + RT -> psm.tsv

  Why psm.tsv for the monitor peptides rather than ion.tsv: across FragPipe
  versions, a clean apex-RT column has not been reliably present in ion.tsv
  (it was a long-standing feature request). psm.tsv reliably carries both a
  per-PSM `Intensity` (IonQuant precursor AUC) and a `Retention` column, so it
  is the dependable source. The trade-off (documented by the FragPipe
  community) is that RT comes from the PSM, not a quant apex, and MBR-only
  hits without a PSM won't have RT here — acceptable for the identified-only
  QC design we agreed on.

  >>> CONFIRM-ON-YOUR-INSTALL markers below flag every exact column name to
  >>> verify against one real psm.tsv / peptide.tsv / protein.tsv. They are
  >>> the documented current names, but pin them to your FragPipe version.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from parsers.base import MonitorResult, RunRecord


# --- CONFIRM-ON-YOUR-INSTALL: file names within a FragPipe run output folder.
PSM_FILE = "psm.tsv"
PEPTIDE_FILE = "peptide.tsv"
PROTEIN_FILE = "protein.tsv"

# --- CONFIRM-ON-YOUR-INSTALL: psm.tsv column headers.
#     Verify these match your psm.tsv header row exactly (case-sensitive).
COL_PEPTIDE = "Peptide"        # stripped (unmodified) sequence
COL_CHARGE = "Charge"
COL_INTENSITY = "Intensity"    # IonQuant precursor AUC; 0 if not quantified
COL_RETENTION = "Retention"    # PSM retention time (instrument units, often sec)


def _count_data_rows(path: Path) -> int | None:
    """Count rows in a TSV excluding the header. None if the file is missing."""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        try:
            next(reader)  # skip header
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def _read_psm_monitors(
    psm_path: Path, monitors: list[dict]
) -> dict[tuple[str, int], MonitorResult]:
    """Scan psm.tsv once, collecting best (highest-intensity) PSM per monitor.

    Returns a dict keyed by (sequence, charge). Monitors not found are simply
    absent from the dict; the caller fills those in as detected=False.
    """
    # Build a fast lookup of what we're hunting for.
    wanted = {(m["sequence"], int(m["charge"])) for m in monitors}
    best: dict[tuple[str, int], MonitorResult] = {}

    if not psm_path.exists():
        return best

    with open(psm_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        # Validate headers up front so a version mismatch fails loudly, early,
        # rather than silently producing all-absent monitor results.
        missing_cols = [
            c for c in (COL_PEPTIDE, COL_CHARGE, COL_INTENSITY, COL_RETENTION)
            if c not in (reader.fieldnames or [])
        ]
        if missing_cols:
            raise ValueError(
                f"psm.tsv is missing expected columns {missing_cols}. "
                f"Found columns: {reader.fieldnames}. "
                f"Update the COL_* constants in dda_parser.py to match your "
                f"FragPipe version."
            )

        for row in reader:
            seq = (row.get(COL_PEPTIDE) or "").strip()
            try:
                charge = int(row.get(COL_CHARGE) or 0)
            except ValueError:
                continue
            key = (seq, charge)
            if key not in wanted:
                continue

            intensity = _to_float(row.get(COL_INTENSITY))
            rt = _to_float(row.get(COL_RETENTION))

            # Keep the most intense PSM as the representative for this precursor.
            prev = best.get(key)
            if prev is None or (intensity or 0) > (prev.intensity or 0):
                best[key] = MonitorResult(
                    sequence=seq, charge=charge, detected=True,
                    intensity=intensity, rt=rt,
                )
    return best


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_dda_run(
    output_dir: str,
    raw_file: str,
    monitors: list[dict],
    run_id: str | None = None,
) -> RunRecord:
    """Parse one FragPipe DDA output folder into a RunRecord.

    output_dir : the per-run FragPipe output folder containing the .tsv files
    raw_file   : the original .raw file name/path (for traceability)
    monitors   : the config's monitor_peptides list ([{sequence, charge}, ...])
    run_id     : defaults to the raw file stem
    """
    out = Path(output_dir)
    rid = run_id or Path(raw_file).stem

    n_proteins = _count_data_rows(out / PROTEIN_FILE)
    n_peptides = _count_data_rows(out / PEPTIDE_FILE)

    found = _read_psm_monitors(out / PSM_FILE, monitors)

    # Every configured monitor gets a row: found ones detected, the rest absent.
    monitor_results: list[MonitorResult] = []
    for m in monitors:
        key = (m["sequence"], int(m["charge"]))
        monitor_results.append(
            found.get(key)
            or MonitorResult(
                sequence=m["sequence"], charge=int(m["charge"]), detected=False
            )
        )

    return RunRecord(
        run_id=rid,
        raw_file=raw_file,
        acquisition="DDA",
        run_timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        n_proteins=n_proteins,
        n_peptides=n_peptides,
        status="success",
        fragpipe_dir=str(out),
        monitor_results=monitor_results,
    )
