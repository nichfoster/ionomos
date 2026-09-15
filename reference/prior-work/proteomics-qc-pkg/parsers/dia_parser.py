"""
DIA parser — turns a FragPipe-DIA (DIA-NN) output into a normalized RunRecord.

DIA in FragPipe runs through DIA-NN, whose main output is `report.tsv`
(or `report.parquet` in newer versions). The columns are well documented and
stable across recent DIA-NN releases:

  Run, Protein.Group, Stripped.Sequence, Modified.Sequence, Precursor.Id,
  Precursor.Charge, Precursor.Quantity, Precursor.Normalised, RT,
  Q.Value, PG.Q.Value

WHERE EACH NUMBER COMES FROM:
  * Counts are derived from the report rows passing FDR (Q.Value <= 0.01 and
    PG.Q.Value <= 0.01):
      n_proteins -> distinct Protein.Group
      n_peptides -> distinct Stripped.Sequence
  * monitor peptide intensity -> Precursor.Quantity
      (falls back to Precursor.Normalised when Quantity is blank, which
       happens under QuantUMS — documented DIA-NN behaviour)
  * monitor peptide RT -> RT

  >>> CONFIRM-ON-YOUR-INSTALL markers flag the exact column names and the
  >>> report file name to verify against one real DIA-NN report. Also confirm
  >>> the RT unit (minutes vs seconds) so it's labelled correctly downstream.

Note: report.tsv typically contains a `Run` column, so a multi-file report
could hold several runs. For this QC pipeline each search is one raw file, so
we read the whole report as one run; if you ever batch, filter by `Run` here.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from parsers.base import MonitorResult, RunRecord


# --- CONFIRM-ON-YOUR-INSTALL: DIA-NN report file name (tsv or parquet).
REPORT_TSV = "report.tsv"

# --- CONFIRM-ON-YOUR-INSTALL: DIA-NN column headers.
COL_PROTEIN_GROUP = "Protein.Group"
COL_STRIPPED_SEQ = "Stripped.Sequence"
COL_CHARGE = "Precursor.Charge"
COL_QUANTITY = "Precursor.Quantity"
COL_NORMALISED = "Precursor.Normalised"   # QuantUMS fallback
COL_RT = "RT"
COL_QVALUE = "Q.Value"
COL_PG_QVALUE = "PG.Q.Value"

# FDR thresholds applied when counting (the usual 1% precursor & PG level).
Q_VALUE_MAX = 0.01
PG_Q_VALUE_MAX = 0.01


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_dia_run(
    output_dir: str,
    raw_file: str,
    monitors: list[dict],
    run_id: str | None = None,
) -> RunRecord:
    """Parse one DIA-NN report into a RunRecord.

    Reads report.tsv once: accumulates FDR-passing protein groups / sequences
    for the counts, and captures the best (highest-quantity) precursor row for
    each configured monitor peptide.
    """
    out = Path(output_dir)
    rid = run_id or Path(raw_file).stem
    report_path = out / REPORT_TSV

    wanted = {(m["sequence"], int(m["charge"])) for m in monitors}
    best: dict[tuple[str, int], MonitorResult] = {}
    protein_groups: set[str] = set()
    sequences: set[str] = set()

    if not report_path.exists():
        # No report = failed/empty search. Caller decides; we return absent.
        monitor_results = [
            MonitorResult(m["sequence"], int(m["charge"]), detected=False)
            for m in monitors
        ]
        return RunRecord(
            run_id=rid, raw_file=raw_file, acquisition="DIA",
            run_timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            n_proteins=None, n_peptides=None, status="failed",
            fragpipe_dir=str(out), monitor_results=monitor_results,
        )

    with open(report_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        required = (COL_PROTEIN_GROUP, COL_STRIPPED_SEQ, COL_CHARGE,
                    COL_QUANTITY, COL_RT, COL_QVALUE, COL_PG_QVALUE)
        missing_cols = [c for c in required if c not in (reader.fieldnames or [])]
        if missing_cols:
            raise ValueError(
                f"report.tsv is missing expected columns {missing_cols}. "
                f"Found columns: {reader.fieldnames}. "
                f"Update the COL_* constants in dia_parser.py to match your "
                f"DIA-NN version."
            )
        has_normalised = COL_NORMALISED in (reader.fieldnames or [])

        for row in reader:
            qv = _to_float(row.get(COL_QVALUE))
            pgqv = _to_float(row.get(COL_PG_QVALUE))
            # Apply FDR filter for the counts.
            passes = (
                qv is not None and qv <= Q_VALUE_MAX
                and pgqv is not None and pgqv <= PG_Q_VALUE_MAX
            )
            if passes:
                pg = (row.get(COL_PROTEIN_GROUP) or "").strip()
                seq = (row.get(COL_STRIPPED_SEQ) or "").strip()
                if pg:
                    protein_groups.add(pg)
                if seq:
                    sequences.add(seq)

            # Monitor peptide capture (also require FDR pass to count as detected).
            seq = (row.get(COL_STRIPPED_SEQ) or "").strip()
            try:
                charge = int(row.get(COL_CHARGE) or 0)
            except ValueError:
                continue
            key = (seq, charge)
            if key in wanted and passes:
                quant = _to_float(row.get(COL_QUANTITY))
                if (quant is None or quant == 0) and has_normalised:
                    quant = _to_float(row.get(COL_NORMALISED))  # QuantUMS fallback
                rt = _to_float(row.get(COL_RT))
                prev = best.get(key)
                if prev is None or (quant or 0) > (prev.intensity or 0):
                    best[key] = MonitorResult(
                        sequence=seq, charge=charge, detected=True,
                        intensity=quant, rt=rt,
                    )

    monitor_results: list[MonitorResult] = []
    for m in monitors:
        key = (m["sequence"], int(m["charge"]))
        monitor_results.append(
            best.get(key)
            or MonitorResult(m["sequence"], int(m["charge"]), detected=False)
        )

    return RunRecord(
        run_id=rid,
        raw_file=raw_file,
        acquisition="DIA",
        run_timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        n_proteins=len(protein_groups) or None,
        n_peptides=len(sequences) or None,
        status="success",
        fragpipe_dir=str(out),
        monitor_results=monitor_results,
    )
