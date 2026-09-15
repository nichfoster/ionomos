"""
SQLite job ledger — the source of truth for job state.

Contract (Phase 1):
    Ledger(db_path)
        .insert(job: Job) -> int              status='queued'
        .set_status(job_id, status, reason=None)
        .next_queued() -> Job | None          oldest first
        .get(job_id) / .list(status=None)
        .recover_on_startup()                 any 'running' -> 'failed' ("interrupted")
        .already_taken(inbox_name) -> bool    so a restart doesn't re-intake

Schema (one table, keep it boring):
    jobs(id INTEGER PK, inbox_name TEXT UNIQUE, user TEXT, method TEXT,
         exp_id TEXT, dest_dir TEXT, status TEXT, reason TEXT,
         created_at TEXT, started_at TEXT, finished_at TEXT,
         parsed_json TEXT)   -- the same dict written to labwatch.json

Statuses: queued | running | done | failed   (rejected folders never get a row;
their .REJECTED.txt is the record).

Reuse: reference/prior-work/proteomics-qc-pkg/store.py (connection handling).
"""
from __future__ import annotations


class Ledger:
    def __init__(self, db_path: str):
        raise NotImplementedError("Phase 1")
