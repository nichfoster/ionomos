"""
SQLite job ledger — the machine-readable source of truth for job state.

    ledger = Ledger("C:/Fragpipe_Auto/labwatch.db")
    ledger.recover_on_startup()            # running -> queued again (or failed after MAX_ATTEMPTS)
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
MAX_ATTEMPTS = 3  # FragPipe starts per job before an interrupted job is failed instead of re-queued

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
    parsed_json TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0
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
    attempts: int = 0

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
            attempts=row["attempts"] if "attempts" in row.keys() else 0,
        )


class Ledger:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(jobs)")}
        if "attempts" not in cols:  # ledgers created by 0.1.x
            self._conn.execute("ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()

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

    def start_attempt(self, job_id: int) -> int:
        """queued -> running and count the attempt. Returns the new attempt number."""
        self._conn.execute("UPDATE jobs SET status='running', reason=NULL, started_at=?, finished_at=NULL,"
                           " attempts=attempts+1 WHERE id=?", (now_iso(), job_id))
        self._conn.commit()
        return self._conn.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()["attempts"]

    def recover_on_startup(self) -> list[tuple[int, str]]:
        """Jobs left 'running' by a crash/reboot/stop: FragPipe died with us.

        Re-queued so they run again automatically, unless they have already been
        started MAX_ATTEMPTS times (then failed, so a job that kills the PC
        can't loop forever). Returns [(job id, new status)].
        """
        rows = self._conn.execute("SELECT id, attempts FROM jobs WHERE status='running'").fetchall()
        out = []
        for r in rows:
            if r["attempts"] >= MAX_ATTEMPTS:
                self.set_status(r["id"], "failed", f"interrupted {r['attempts']} times; not restarting it again "
                                                   f"(labwatch retry {r['id']} to try once more)")
                out.append((r["id"], "failed"))
            else:
                self.set_status(r["id"], "queued", "interrupted (labwatch restarted); will run again")
                out.append((r["id"], "queued"))
        return out

    def requeue(self, job_id: int, reason: str | None = None, reset_attempts: bool = False) -> None:
        sql = "UPDATE jobs SET status='queued', reason=?, finished_at=NULL" + (", attempts=0" if reset_attempts else "")
        self._conn.execute(sql + " WHERE id=?", (reason, job_id))
        self._conn.commit()

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
