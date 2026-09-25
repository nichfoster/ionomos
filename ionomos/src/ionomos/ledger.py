"""
SQLite job ledger — the machine-readable source of truth for job state.

    ledger = Ledger("C:/Fragpipe_Auto/ionomos.db")
    ledger.recover_on_startup()            # running -> queued again (or failed after MAX_ATTEMPTS)
    job_id = ledger.insert(Job(...))        # status queued
    ledger.set_status(job_id, "running")
    job = ledger.next_queued()

Statuses: queued | running | done | failed. Rejected folders never get a row —
their .REJECTED.txt in the inbox is the record.

The same `parsed` dict stored here is what intake writes to ionomos.json.

Missing-id contracts: get() returns None; set_status()/requeue() are silent no-ops
(pinned by tests); start_attempt() raises LedgerError.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STATUSES = ("queued", "running", "done", "failed")
MAX_ATTEMPTS = 3  # FragPipe starts per job before an interrupted job is failed instead of re-queued

log = logging.getLogger("ionomos.ledger")

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


class LedgerError(Exception):
    """A ledger operation hit a missing job row; raised instead of failing on a raw TypeError."""


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
        """Move a job to a new status. A missing job id is a silent no-op (pinned by tests)."""
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
        """queued -> running and count the attempt. Returns the new attempt number.

        Raises LedgerError if the job row doesn't exist (e.g. the ledger was rebuilt
        underneath a running watcher); get()/requeue()/set_status() stay silent no-ops.
        """
        row = self._conn.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise LedgerError(f"job {job_id} is not in the ledger; cannot start an attempt")
        self._conn.execute("UPDATE jobs SET status='running', reason=NULL, started_at=?, finished_at=NULL,"
                           " attempts=attempts+1 WHERE id=?", (now_iso(), job_id))
        self._conn.commit()
        return row["attempts"] + 1

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
                                                   f"(ionomos retry {r['id']} to try once more)")
                out.append((r["id"], "failed"))
            else:
                self.set_status(r["id"], "queued", "interrupted (ionomos restarted); will run again")
                out.append((r["id"], "queued"))
        return out

    def requeue(self, job_id: int, reason: str | None = None, reset_attempts: bool = False) -> None:
        """Send a job back to queued. A missing job id is a silent no-op (pinned by tests)."""
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


# ------------------------------------------------------------------ failsafes --


def integrity(db_path: str | Path) -> str:
    """'ok', 'missing', or SQLite's complaint. Never raises."""
    p = Path(db_path)
    if not p.is_file():
        return "missing"
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            con.execute("SELECT count(*) FROM jobs").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return f"unreadable: {exc}"
    return "ok" if row and row[0] == "ok" else f"corrupt: {row[0] if row else '?'}"


def backup(db_path: str | Path, backup_dir: str | Path, keep: int = 14) -> Path | None:
    """Consistent copy (SQLite online backup) to backup_dir/ionomos-<date>.db; one per day, newest `keep` kept."""
    p = Path(db_path)
    if not p.is_file():
        return None
    d = Path(backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"ionomos-{datetime.now():%Y%m%d}.db"
    if dest.exists():
        return dest
    src = sqlite3.connect(p, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    for old in sorted(d.glob("ionomos-*.db"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def rebuild_from_status_files(db_path: str | Path, users_root: str | Path) -> tuple[Path | None, int]:
    """Replace a broken ledger: move it aside, re-create jobs from every <user>/<exp>/ionomos.json.

    Each experiment folder carries its full intake record, so nothing about
    what was filed is lost with the database. Jobs that were running come
    back queued. Returns (where the broken file was moved, jobs recovered).
    """
    p = Path(db_path)
    moved = None
    if p.exists():
        moved = p.with_name(f"{p.name}.broken-{datetime.now():%Y%m%d-%H%M%S}")
        p.replace(moved)
        for ext in ("-wal", "-shm", "-journal"):
            side = p.with_name(p.name + ext)
            if side.exists():
                side.replace(moved.with_name(moved.name + ext))
    led = Ledger(p)
    records = []
    root = Path(users_root)
    from ionomos.names import status_files

    for status_file in status_files(root):
        try:
            rec = json.loads(status_file.read_text(encoding="utf-8"))
            folder = rec["plan"]["folder"]
            records.append((rec.get("queued_at") or "", status_file.parent, rec, folder))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("rebuild: skipping unreadable status file %s: %s", status_file, exc)
            continue
    n = 0
    for queued_at, dest, rec, folder in sorted(records, key=lambda r: r[0]):
        status = rec.get("status") if rec.get("status") in STATUSES else "queued"
        if status == "running":
            status = "queued"
        job = Job(inbox_name=folder.get("safe") or dest.name, user=folder.get("user") or dest.parent.name,
                  method=folder.get("method") or "?", dest_dir=str(dest), parsed=rec, status=status,
                  reason=rec.get("reason"), created_at=queued_at or None)
        try:
            led.insert(job)
            n += 1
        except sqlite3.IntegrityError:
            log.warning("rebuild: skipping %s — inbox name %r is already taken by another experiment",
                        dest, job.inbox_name)
            continue
    led.close()
    return moved, n


def adopt_orphans(ledger: Ledger, users_root: str | Path) -> list[int]:
    """Queue experiment folders that ionomos filed but that never reached the ledger.

    Happens if the database was locked/unwritable right after a folder was
    moved. Such a folder has ionomos.json with status 'queued' (or 'running')
    and no job row pointing at it. Returns the new job ids.
    """
    root = Path(users_root)
    if not root.is_dir():
        return []
    known = {Path(j.dest_dir) for j in ledger.list()}
    known_names = {j.inbox_name for j in ledger.list()}
    new: list[int] = []
    from ionomos.names import status_files

    for status_file in status_files(root):
        dest = status_file.parent
        if dest in known:
            continue
        try:
            rec = json.loads(status_file.read_text(encoding="utf-8"))
            folder = rec["plan"]["folder"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("adopt: skipping unreadable status file %s: %s", status_file, exc)
            continue
        if rec.get("status") not in ("queued", "running"):
            continue
        name = folder.get("safe") or dest.name
        if name in known_names:
            log.warning("adopt: skipping %s — inbox name %r is already taken", dest, name)
            continue
        job = Job(inbox_name=name, user=folder.get("user") or dest.parent.name, method=folder.get("method") or "?",
                  dest_dir=str(dest), parsed=rec, status="queued",
                  reason="re-adopted: was filed but missing from the job list", created_at=rec.get("queued_at"))
        try:
            new.append(ledger.insert(job))
            known_names.add(name)
        except sqlite3.IntegrityError:
            log.warning("adopt: skipping %s — inbox name %r raced with another adoption", dest, name)
            continue
    return new
