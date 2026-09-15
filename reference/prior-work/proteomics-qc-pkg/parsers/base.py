"""
The normalized record — the single data contract between parsers and storage.

Both the DDA and DIA parsers must produce a RunRecord. Everything downstream
(store.py, dashboard.py) only ever sees this shape and never needs to know
which acquisition type produced it, except via the `acquisition` label.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MonitorResult:
    """Intensity + RT for one monitor peptide in one run.

    `detected` is the honest flag for the identified-only design: a peptide
    that FragPipe did not identify is recorded with detected=False and
    intensity/rt left as None — not silently dropped, not zeroed.
    """
    sequence: str
    charge: int
    detected: bool
    intensity: float | None = None
    rt: float | None = None


@dataclass
class RunRecord:
    """Everything we persist about a single search."""
    run_id: str                       # raw filename stem, unique per run
    raw_file: str                     # original .raw path/name
    acquisition: str                  # 'DDA' or 'DIA'
    run_timestamp: str                # ISO8601, when the search completed
    n_proteins: int | None
    n_peptides: int | None
    status: str                       # 'success' | 'failed' | 'running'
    fragpipe_dir: str | None          # path to this run's output, for traceback
    monitor_results: list[MonitorResult] = field(default_factory=list)
