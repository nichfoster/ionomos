"""
SQLite storage layer.

Two tables, matching the agreed schema:
  runs            — one row per search (counts, status, traceability)
  monitor_results — one row per (run, peptide, charge); keeps the monitor
                    set flexible so it can change over time without schema edits.

This module is the only place that touches SQL. Parsers produce RunRecords;
this turns them into rows. The dashboard reads back through the query helpers.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from parsers.base import MonitorResult, RunRecord


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    raw_file      TEXT NOT NULL,
    acquisition   TEXT NOT NULL,        -- 'DDA' or 'DIA'
    run_timestamp TEXT NOT NULL,        -- ISO8601, search completion time
    n_proteins    INTEGER,
    n_peptides    INTEGER,
    status        TEXT NOT NULL,        -- 'success' | 'failed' | 'running'
    fragpipe_dir  TEXT
);

CREATE TABLE IF NOT EXISTS monitor_results (
    run_id     TEXT NOT NULL,
    sequence   TEXT NOT NULL,
    charge     INTEGER NOT NULL,
    detected   INTEGER NOT NULL,        -- 1 / 0
    intensity  REAL,                    -- NULL when not detected
    rt         REAL,                    -- NULL when not detected
    PRIMARY KEY (run_id, sequence, charge),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_acq_time
    ON runs (acquisition, run_timestamp);
CREATE INDEX IF NOT EXISTS idx_monitor_seq
    ON monitor_results (sequence, charge);
"""


class Store:
    """Thin wrapper over a SQLite file. Construct with the db path from config."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        # Make sure the parent dir exists (e.g. C:/proteomics-qc/).
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create tables/indexes if they don't exist. Safe to call repeatedly."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ----------------------------------------------------------------- writes

    def upsert_run(self, record: RunRecord) -> None:
        """Insert or replace a run and its monitor results in one transaction.

        Replace semantics mean re-running a search for the same run_id cleanly
        overwrites the prior result rather than erroring or duplicating.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs
                    (run_id, raw_file, acquisition, run_timestamp,
                     n_proteins, n_peptides, status, fragpipe_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    raw_file      = excluded.raw_file,
                    acquisition   = excluded.acquisition,
                    run_timestamp = excluded.run_timestamp,
                    n_proteins    = excluded.n_proteins,
                    n_peptides    = excluded.n_peptides,
                    status        = excluded.status,
                    fragpipe_dir  = excluded.fragpipe_dir
                """,
                (
                    record.run_id, record.raw_file, record.acquisition,
                    record.run_timestamp, record.n_proteins, record.n_peptides,
                    record.status, record.fragpipe_dir,
                ),
            )
            # Replace this run's monitor rows wholesale.
            conn.execute(
                "DELETE FROM monitor_results WHERE run_id = ?", (record.run_id,)
            )
            conn.executemany(
                """
                INSERT INTO monitor_results
                    (run_id, sequence, charge, detected, intensity, rt)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.run_id, m.sequence, m.charge,
                        1 if m.detected else 0, m.intensity, m.rt,
                    )
                    for m in record.monitor_results
                ],
            )

    def mark_status(self, run_id: str, status: str) -> None:
        """Update only the status of an existing run (e.g. -> 'failed')."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id)
            )

    # ------------------------------------------------------------------ reads

    def get_runs(self, acquisition: str | None = None) -> list[sqlite3.Row]:
        """All runs, newest first. Optionally filter to 'DDA' or 'DIA'."""
        with self._connect() as conn:
            if acquisition:
                rows = conn.execute(
                    "SELECT * FROM runs WHERE acquisition = ? "
                    "ORDER BY run_timestamp DESC",
                    (acquisition,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY run_timestamp DESC"
                ).fetchall()
            return rows

    def get_monitor_timeseries(
        self, sequence: str, charge: int, acquisition: str | None = None
    ) -> list[sqlite3.Row]:
        """Time-ordered intensity/RT history for one monitor peptide.

        Joins to runs so each point carries its timestamp and acquisition,
        and so the dashboard can show detected=0 runs as distinct markers.
        """
        with self._connect() as conn:
            query = """
                SELECT r.run_id, r.run_timestamp, r.acquisition,
                       m.detected, m.intensity, m.rt
                FROM monitor_results m
                JOIN runs r ON r.run_id = m.run_id
                WHERE m.sequence = ? AND m.charge = ?
            """
            params: list[object] = [sequence, charge]
            if acquisition:
                query += " AND r.acquisition = ?"
                params.append(acquisition)
            query += " ORDER BY r.run_timestamp ASC"
            return conn.execute(query, params).fetchall()

    def get_monitored_peptides(self) -> list[sqlite3.Row]:
        """Distinct (sequence, charge) pairs that exist in history.

        Lets the dashboard populate its peptide dropdown from real data,
        which naturally handles the monitor list changing over time.
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT DISTINCT sequence, charge FROM monitor_results "
                "ORDER BY sequence, charge"
            ).fetchall()
