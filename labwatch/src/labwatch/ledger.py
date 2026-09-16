"""
SQLite job ledger — the machine-readable source of truth for job state.

    ledger = Ledger("C:/Fragpipe_Auto/labwatch.db")
    ledger.recover_on_startup()            # running -> failed ("interrupted")
    job_id = ledger.insert(Job(...))        # status queued
    ledger.set_status(job_id, "running")
    job = ledger.next_queued()

Statuses: queued | running | done | failed. Rejected folders never get a row —
their .REJECTED.txt in the inbox is the record.

The same `parsed` dict stored here is what intake writes to labwatch.json.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STATUSES = ("queued", "running", "done", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inbox_name  TEXT NOT NULL UNIQUE,
    user        TEXT NOT NULL,
    method      TEXT NOT NULL,
    dest_dir    TEXT NOT NULL,
    status      TEXT NOT NULL,
    reason      TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    parsed_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    inbox_name: str
    user: str
    method: str
    dest_dir: str
    parsed: dict = field(default_factory=dict)
    status: str = "queued"
    reason: str | None = None
    id: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> Job:
        return cls(
            id=row["id"],
            inbox_name=row["inbox_name"],
            user=row["user"],
            method=row["method"],
            dest_dir=row["dest_dir"],
            status=row["status"],
            reason=row["reason"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            parsed=json.loads(row["parsed_json"]),
        )


class Ledger:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- writes --

    def insert(self, job: Job) -> int:
        job.created_at = job.created_at or now_iso()
        cur = self._conn.execute(
            "INSERT INTO jobs (inbox_name, user, method, dest_dir, status, reason, created_at, parsed_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job.inbox_name, job.user, job.method, job.dest_dir, job.status, job.reason,
             job.created_at, json.dumps(job.parsed)),
        )
        self._conn.commit()
        job.id = cur.lastrowid
        return job.id

    def set_status(self, job_id: int, status: str, reason: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}")
        ts = now_iso()
        col = {"running": "started_at", "done": "finished_at", "failed": "finished_at"}.get(status)
        if col:
            self._conn.execute(
                f"UPDATE jobs SET status=?, reason=?, {col}=? WHERE id=?", (status, reason, ts, job_id)
            )
        else:
            self._conn.execute("UPDATE jobs SET status=?, reason=? WHERE id=?", (status, reason, job_id))
        self._conn.commit()

    def recover_on_startup(self) -> list[int]:
        """Jobs left 'running' by a crash/reboot are failed; FragPipe died with us."""
        rows = self._conn.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
        ids = [r["id"] for r in rows]
        for jid in ids:
            self.set_status(jid, "failed", "interrupted: labwatch restarted while this job was running")
        return ids

    # ----------------------------------------------------------------- reads --

    def get(self, job_id: int) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return Job._from_row(row) if row else None

    def list(self, status: str | None = None) -> list[Job]:
        if status:
            rows = self._conn.execute("SELECT * FROM jobs WHERE status=? ORDER BY id", (status,))
        else:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY id")
        return [Job._from_row(r) for r in rows.fetchall()]

    def next_queued(self) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        return Job._from_row(row) if row else None

    def already_taken(self, inbox_name: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM jobs WHERE inbox_name=?", (inbox_name,)).fetchone()
        return row is not None
