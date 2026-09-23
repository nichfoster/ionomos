"""
Worker — takes queued jobs one at a time and runs FragPipe on them.

    w = Worker(cfg)              # own ledger connection (thread-safe alongside the watcher)
    w.run_forever()              # in a thread; w.stop() kills a running FragPipe and returns
    w.run_once()                 # one scheduling pass (tests)

For each job, in id order:

    prepare  --Hold------>  stays queued; reason shown in labwatch.json/status ("waiting: ...")
       |     --JobError-->  failed
       v
    running  (ledger + labwatch.json; attempt counted)
       |
    FragPipe --exit 0 + output-->  done    (DONE.txt)
             --anything else---->  failed  (FAILED.txt with the reason and log tail)
             --labwatch stopped-->  queued  (runs again at next start)

One search at a time: FragPipe already uses every core it is given.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from labwatch import fragpipe
from labwatch.config import Config
from labwatch.intake import write_status
from labwatch.ledger import Job, Ledger, now_iso

log = logging.getLogger("labwatch.worker")

DONE_NOTE = "DONE.txt"
FAILED_NOTE = "FAILED.txt"


def _read_status(dest: Path, fallback: dict) -> dict:
    import json

    try:
        return json.loads((dest / "labwatch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(fallback)


def _update_status(job: Job, **changes) -> None:
    dest = Path(job.dest_dir)
    if not dest.is_dir():
        return
    rec = _read_status(dest, job.parsed)
    run = dict(rec.get("run") or {})
    run.update(changes.pop("run", {}))
    rec.update(changes)
    if run:
        rec["run"] = run
    try:
        write_status(dest, rec)
    except OSError as exc:  # never let a status file break the queue
        log.warning("could not write labwatch.json for job %s: %s", job.id, exc)


def _note(dest: Path, name: str, text: str) -> None:
    """Write DONE.txt / FAILED.txt and remove the other one (both are ours)."""
    other = FAILED_NOTE if name == DONE_NOTE else DONE_NOTE
    try:
        (dest / other).unlink(missing_ok=True)
        (dest / name).write_text(text, encoding="utf-8")
    except OSError as exc:
        log.warning("could not write %s in %s: %s", name, dest, exc)


class Worker:
    def __init__(self, cfg: Config, ledger: Ledger | None = None, poll_seconds: float = 5.0):
        self.cfg = cfg
        self.ledger = ledger or Ledger(cfg.database)
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._held: dict[int, str] = {}  # job id -> last hold reason (log once per change)
        self.current: Job | None = None

    # ------------------------------------------------------------- control --

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        log.info("FragPipe worker started (one job at a time, launcher %s)", self.cfg.fragpipe_exe)
        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception:  # noqa: BLE001 - the worker must survive anything a job throws
                log.exception("worker error; continuing")
                ran = False
            if not ran:
                self._stop.wait(self.poll_seconds)
        log.info("FragPipe worker stopped")

    # ---------------------------------------------------------- scheduling --

    def run_once(self) -> bool:
        """Run the first runnable queued job. True if one ran (whatever the outcome)."""
        for job in self.ledger.list("queued"):
            if self._stop.is_set():
                return False
            try:
                spec = fragpipe.prepare(job, self.cfg)
            except fragpipe.Hold as exc:
                self._hold(job, str(exc))
                continue
            except fragpipe.JobError as exc:
                self._fail(job, str(exc))
                return True
            self._held.pop(job.id, None)
            self._run(job, spec)
            return True
        return False

    def _hold(self, job: Job, reason: str) -> None:
        if self._held.get(job.id) == reason:
            return
        self._held[job.id] = reason
        log.warning("job %d waiting: %s", job.id, reason)
        self.ledger.requeue(job.id, f"waiting: {reason}")
        _update_status(job, status="queued", reason=f"waiting: {reason}")

    # ------------------------------------------------------------- running --

    def _run(self, job: Job, spec: fragpipe.RunSpec) -> None:
        dest = spec.dest
        attempt = self.ledger.start_attempt(job.id)
        self.current = job
        try:
            moved = fragpipe.write_inputs(spec)
        except OSError as exc:
            self.current = None
            self._fail(job, f"could not prepare FragPipe inputs: {exc}")
            return
        if moved:
            spec.warnings.append(f"previous attempt's output kept as {moved}/")
        log.info("job %d: FragPipe %s on %s (%d raw files, attempt %d)", job.id, job.method, dest,
                 len(spec.manifest_lines), attempt)
        for w in spec.warnings:
            log.warning("job %d: %s", job.id, w)
        _update_status(job, status="running", reason=None, run={
            "attempt": attempt, "started_at": now_iso(), "finished_at": None, "exit_code": None,
            "workflow": str(spec.workflow), "workflow_source": str(spec.workflow_src),
            "fasta": str(spec.fasta) if spec.fasta else None, "manifest": str(spec.manifest),
            "workdir": str(spec.workdir), "console_log": str(spec.console_log),
            "command": spec.command(), "warnings": list(spec.warnings),
        })
        (dest / FAILED_NOTE).unlink(missing_ok=True)

        def started(pid, cmd):
            log.info("job %d: FragPipe pid %d: %s", job.id, pid, " ".join(cmd))
            _update_status(job, run={"pid": pid})

        res = fragpipe.run(spec, self._stop, on_start=started)
        self.current = None
        finished = now_iso()

        if res.stopped:
            self.ledger.requeue(job.id, "interrupted (labwatch stopped); will run again")
            _update_status(job, status="queued", reason="interrupted (labwatch stopped); will run again",
                           run={"finished_at": finished, "exit_code": res.code})
            log.warning("job %d: FragPipe stopped; job re-queued", job.id)
            return
        if not res.ok:
            _update_status(job, run={"finished_at": finished, "exit_code": res.code})
            self._fail(job, res.reason, spec)
            return

        warnings = spec.warnings + fragpipe.missing_outputs(spec) + self._postprocess(job, spec)
        self.ledger.set_status(job.id, "done", "; ".join(warnings) or None)
        _update_status(job, status="done", reason=None,
                       run={"finished_at": finished, "exit_code": 0, "warnings": warnings})
        _note(dest, DONE_NOTE,
              f"FragPipe finished {datetime.now():%Y-%m-%d %H:%M}.\n"
              f"Results: {spec.workdir}\n"
              + ("".join(f"Note: {w}\n" for w in warnings)))
        log.info("job %d: done%s", job.id, f" ({len(warnings)} warning(s))" if warnings else "")

    def _postprocess(self, job: Job, spec: fragpipe.RunSpec) -> list[str]:
        """Post-processing hook. Steps are named per method in config (methods.X.postprocess)."""
        from labwatch import postprocess

        return postprocess.run_all(job, spec, self.cfg)

    def _fail(self, job: Job, reason: str, spec: fragpipe.RunSpec | None = None) -> None:
        self.ledger.set_status(job.id, "failed", reason)
        _update_status(job, status="failed", reason=reason)
        dest = Path(job.dest_dir)
        if dest.is_dir():
            log_hint = f"\nFragPipe console output: {spec.console_log}\n" if spec else "\n"
            _note(dest, FAILED_NOTE,
                  f"labwatch could not finish this experiment ({datetime.now():%Y-%m-%d %H:%M}).\n\n"
                  f"Reason: {reason}\n{log_hint}\n"
                  f"After fixing the cause: LabWatch app -> Run & Test -> Retry a failed job, "
                  f"or  labwatch retry {job.id}\n")
        log.error("job %d failed: %s", job.id, reason)
