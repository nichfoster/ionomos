"""
Queue worker — the consumer side.

Pulls one job at a time off a thread-safe queue and runs the full chain:
    FragPipe search  ->  parse output  ->  write to SQLite

Deliberately SEQUENTIAL: one search at a time. Given the load (max ~2 files/day,
10-20 min/search) there is no reason for parallelism, and sequential execution
means we never contend for cores/RAM or hit FragPipe with two runs at once.
Files that arrive while a search is running simply wait in the queue.

A job is a small dataclass describing one raw file to process. The watcher
creates jobs and puts them on the queue; this worker consumes them.

Failure handling: if FragPipe raises, we still write a run row with
status='failed' so the dashboard shows the failure rather than a silent gap.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fragpipe_runner import FragPipeError, run_fragpipe
from parsers.base import RunRecord
from parsers.dda_parser import parse_dda_run
from parsers.dia_parser import parse_dia_run
from store import Store

log = logging.getLogger("qc.worker")


@dataclass
class Job:
    """One unit of work: a single raw file to search and report."""
    raw_file: str        # full path to the .raw file
    acquisition: str     # 'DDA' or 'DIA' (decided by which folder it landed in)

    @property
    def run_id(self) -> str:
        return Path(self.raw_file).stem


class QueueWorker:
    """Owns the job queue and a single consumer thread."""

    def __init__(self, cfg: dict, store: Store):
        self.cfg = cfg
        self.store = store
        self.queue: "queue.Queue[Job | None]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="qc-worker", daemon=True
        )
        self._thread.start()
        log.info("Worker thread started.")

    def stop(self, drain: bool = False) -> None:
        """Signal the worker to stop. If drain, wait for queued jobs first."""
        if drain:
            self.queue.join()
        self._stop.set()
        self.queue.put(None)  # unblock the get()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Worker thread stopped.")

    def enqueue(self, job: Job) -> None:
        log.info("Enqueued %s (%s). Queue depth ~%d",
                 job.run_id, job.acquisition, self.queue.qsize() + 1)
        self.queue.put(job)

    # ------------------------------------------------------------------- loop

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if job is None:  # sentinel from stop()
                self.queue.task_done()
                break
            try:
                self._process(job)
            except Exception:  # never let one bad job kill the worker
                log.exception("Unhandled error processing %s", job.run_id)
            finally:
                self.queue.task_done()

    # ---------------------------------------------------------------- one job

    def _process(self, job: Job) -> None:
        paths = self.cfg["paths"]
        fp = self.cfg.get("fragpipe", {})
        acq = job.acquisition.upper()

        workflow = paths["workflow_dia"] if acq == "DIA" else paths["workflow_dda"]
        out_dir = str(Path(paths["output_root"]) / job.run_id)

        log.info("Starting %s search for %s", acq, job.run_id)

        # Mark the run as 'running' up front so an in-flight (or crashed) search
        # is visible on the dashboard, not invisible until it finishes.
        self.store.upsert_run(RunRecord(
            run_id=job.run_id, raw_file=job.raw_file, acquisition=acq,
            run_timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            n_proteins=None, n_peptides=None, status="running",
            fragpipe_dir=out_dir, monitor_results=[],
        ))

        try:
            run_fragpipe(
                raw_file=job.raw_file,
                acquisition=acq,
                fragpipe_exe=paths["fragpipe_exe"],
                workflow=workflow,
                output_dir=out_dir,
                threads=fp.get("threads"),
                timeout_minutes=fp.get("timeout_minutes", 90),
                config_tools_folder=paths.get("config_tools_folder") or None,
                config_diann=paths.get("config_diann") or None,
            )
        except FragPipeError as exc:
            log.error("FragPipe failed for %s: %s", job.run_id, exc)
            self.store.mark_status(job.run_id, "failed")
            return

        # Search succeeded — parse and store the real metrics.
        monitors = self.cfg.get("monitor_peptides", [])
        try:
            if acq == "DIA":
                record = parse_dia_run(out_dir, job.raw_file, monitors, job.run_id)
            else:
                record = parse_dda_run(out_dir, job.raw_file, monitors, job.run_id)
        except Exception as exc:
            log.exception("Parsing failed for %s: %s", job.run_id, exc)
            self.store.mark_status(job.run_id, "failed")
            return

        self.store.upsert_run(record)
        log.info("Done %s: proteins=%s peptides=%s, %d monitor peptides",
                 job.run_id, record.n_proteins, record.n_peptides,
                 len(record.monitor_results))
