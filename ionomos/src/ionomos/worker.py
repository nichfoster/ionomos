"""
Worker — takes queued jobs one at a time and runs FragPipe on them.

    w = Worker(cfg)              # own ledger connection (thread-safe alongside the watcher)
    w.run_forever()              # in a thread; w.stop() kills a running FragPipe and returns
    w.run_once()                 # one scheduling pass (tests)

For each job, in id order:

    prepare  --Hold------>  stays queued; reason shown in ionomos.json/status ("waiting: ...")
       |     --JobError-->  failed
       v
    running  (ledger + ionomos.json; attempt counted)
       |
    FragPipe --exit 0 + output-->  done    (DONE.txt)
             --anything else---->  failed  (FAILED.txt with the reason and log tail)
             --ionomos stopped-->  queued  (runs again at next start)
             --cancelled--------->  failed  ("cancelled by user")

Controls that work from any process (the app, `ionomos pause`, another shell):
    <log_dir>/PAUSED                 exists -> no new searches start (a running one finishes)
    <job>/ionomos_run/CANCEL        exists -> that job's FragPipe is killed, job failed "cancelled"

One search at a time: FragPipe already uses every core it is given.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from ionomos import fragpipe
from ionomos.config import Config
from ionomos.intake import write_status
from ionomos.ledger import Job, Ledger, now_iso

log = logging.getLogger("ionomos.worker")

DONE_NOTE = "DONE.txt"
FAILED_NOTE = "FAILED.txt"
PAUSE_FILE = "PAUSED"
PROGRESS_EVERY = 30.0  # seconds between progress updates in ionomos.json


# ---------------------------------------------------------------- controls --


def paused(log_dir: Path) -> bool:
    return (Path(log_dir) / PAUSE_FILE).exists()


def pause(log_dir: Path, by: str = "") -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    (Path(log_dir) / PAUSE_FILE).write_text(f"paused {now_iso()} {by}\n", encoding="utf-8")


def resume(log_dir: Path) -> None:
    (Path(log_dir) / PAUSE_FILE).unlink(missing_ok=True)


def request_cancel(ledger: Ledger, job_id: int) -> str:
    """Cancel a queued or running job. Returns a message for the user."""
    job = ledger.get(job_id)
    if job is None:
        return f"no job {job_id}"
    if job.status == "queued":
        ledger.set_status(job_id, "failed", "cancelled by user (before it started)")
        _update_status(job, status="failed", reason="cancelled by user (before it started)")
        _note(Path(job.dest_dir), FAILED_NOTE, "Cancelled before FragPipe started. Retry to run it after all.\n")
        return f"job {job_id} cancelled (it had not started)"
    if job.status == "running":
        run_dir = Path(job.dest_dir) / fragpipe.RUN_DIR
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / fragpipe.CANCEL_FILE).write_text(now_iso(), encoding="utf-8")
        except OSError as exc:
            return f"could not signal job {job_id}: {exc}"
        return f"job {job_id}: FragPipe will be stopped within a few seconds"
    return f"job {job_id} is {job.status}; nothing to cancel"


def _read_status(dest: Path, fallback: dict) -> dict:
    import json

    try:
        from ionomos.names import status_path

        return json.loads(status_path(dest).read_text(encoding="utf-8"))
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
        log.warning("could not write ionomos.json for job %s: %s", job.id, exc)


def _note(dest: Path, name: str, text: str) -> None:
    """Write DONE.txt / FAILED.txt and remove the other one (both are ours)."""
    other = FAILED_NOTE if name == DONE_NOTE else DONE_NOTE
    try:
        (dest / other).unlink(missing_ok=True)
        (dest / name).write_text(text, encoding="utf-8")
    except OSError as exc:
        log.warning("could not write %s in %s: %s", name, dest, exc)


class Worker:
    def __init__(self, cfg: Config, ledger: Ledger | None = None, poll_seconds: float = 5.0, heartbeat=None):
        self.cfg = cfg
        self.ledger = ledger or Ledger(cfg.database)
        self.poll_seconds = poll_seconds
        self.heartbeat = heartbeat
        self._stop = threading.Event()
        self._held: dict[int, str] = {}  # job id -> last hold reason (log once per change)
        self._was_paused = False
        self.current: Job | None = None

    def _beat(self, state: str = "idle") -> None:
        if self.heartbeat is not None:
            self.heartbeat.beat("worker", state)

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
        if paused(self.cfg.log_dir):
            if not self._was_paused:
                log.info("searches paused (%s exists); queued jobs wait", self.cfg.log_dir / PAUSE_FILE)
                self._was_paused = True
            self._beat("paused")
            return False
        if self._was_paused:
            log.info("searches resumed")
            self._was_paused = False
        self._beat("idle")
        for job in self.ledger.list("queued"):
            if self._stop.is_set():
                return False
            try:
                spec = fragpipe.prepare(job, self.cfg)
            except fragpipe.Hold as exc:
                self._hold(job, str(exc))
                continue
            except fragpipe.JobError as exc:
                self._fail(job, str(exc), hints=fragpipe.explain(str(exc)))
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
        _tell(self.cfg, "search_waiting", job, f"{job.user}/{job.inbox_name} is waiting to be searched",
              reason, severity="input", causes=_waiting_causes(reason),
              fixes=["Fix what's named above (the Setup checklist, tab ✓, shows it too); the search then starts "
                     "by itself — nothing else to do"])

    # ------------------------------------------------------------- running --

    def _run(self, job: Job, spec: fragpipe.RunSpec) -> None:
        dest = spec.dest
        _close(self.cfg, job, "search_waiting", "search_failed")
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

        last = [0.0]

        def polled():
            step = fragpipe.progress(spec.console_log)
            self._beat(f"running job {job.id}: {step}")
            if time.monotonic() - last[0] >= PROGRESS_EVERY:
                last[0] = time.monotonic()
                _update_status(job, run={"progress": step, "progress_at": now_iso()})

        res = fragpipe.run(spec, self._stop, on_start=started, on_poll=polled)
        self.current = None
        finished = now_iso()
        if res.cancelled:
            (spec.run_dir / fragpipe.CANCEL_FILE).unlink(missing_ok=True)
            _update_status(job, run={"finished_at": finished, "exit_code": res.code})
            self._fail(job, "cancelled by user", spec)
            return

        if res.stopped:
            self.ledger.requeue(job.id, "interrupted (ionomos stopped); will run again")
            _update_status(job, status="queued", reason="interrupted (ionomos stopped); will run again",
                           run={"finished_at": finished, "exit_code": res.code})
            log.warning("job %d: FragPipe stopped; job re-queued", job.id)
            return
        if not res.ok:
            _update_status(job, run={"finished_at": finished, "exit_code": res.code, "hints": res.hints})
            self._fail(job, res.reason, spec, res.hints)
            return

        _update_status(job, run={"finished_at": finished, "exit_code": 0})
        self._beat(f"running job {job.id}: analysis")
        post_warnings, summary = self._postprocess(job, spec)
        warnings = spec.warnings + fragpipe.missing_outputs(spec) + post_warnings
        state = (summary or {}).get("state")
        headline = {"needs_input": "analysis needs your input — see the pop-up / Analysis tab",
                    "failed": "analysis had a problem — see the pop-up / report"}.get(state)
        self.ledger.set_status(job.id, "done", "; ".join(([headline] if headline else []) + warnings) or None)
        _update_status(job, status="done", reason=None, run={"warnings": warnings},
                       **({"results": summary} if summary else {}))
        report = f"Report:  {dest / summary['report']}\n" if summary.get("report") else ""
        hits = "".join(f"  {c['name']}: {c['up']} up, {c['down']} down of {c['tested']}\n"
                       for c in summary.get("comparisons", []))
        _note(dest, DONE_NOTE,
              f"FragPipe finished {datetime.now():%Y-%m-%d %H:%M}.\n{report}"
              f"FragPipe output: {spec.workdir}\n"
              + (f"Hits:\n{hits}" if hits else "")
              + ("".join(f"Note: {w}\n" for w in warnings)))
        log.info("job %d: done%s", job.id, f" ({len(warnings)} warning(s))" if warnings else "")

    def _postprocess(self, job: Job, spec: fragpipe.RunSpec) -> tuple[list[str], dict]:
        """Downstream analysis (never fails the job; see postprocess.py)."""
        from ionomos import postprocess

        try:
            return postprocess.run_all(job, spec, self.cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %d: analysis crashed", job.id)
            return [f"analysis crashed: {exc}"], {}

    def _fail(self, job: Job, reason: str, spec: fragpipe.RunSpec | None = None, hints: list[str] | None = None) -> None:
        self.ledger.set_status(job.id, "failed", reason)
        _update_status(job, status="failed", reason=reason)
        if reason != "cancelled by user":
            tail = fragpipe.read_tail_text(spec.console_log, 8000) if spec else ""
            _tell(self.cfg, "search_failed", job, f"FragPipe failed on {job.user}/{job.inbox_name}", reason,
                  severity="error", causes=list(hints or []) or [
                      "See the last lines of FragPipe's log below — the first ERROR / Exception line is usually the cause"],
                  fixes=["Fix the cause, then press Retry here (or Jobs tab → Retry)",
                         "If it's unclear: Report a problem sends the log with everything needed"],
                  details="\n".join(tail.splitlines()[-40:]),
                  data={"console_log": str(spec.console_log) if spec else None})
        dest = Path(job.dest_dir)
        if dest.is_dir():
            log_hint = f"\nFragPipe console output: {spec.console_log}\n" if spec else "\n"
            likely = "".join(f"  - {h}\n" for h in hints or [])
            _note(dest, FAILED_NOTE,
                  f"ionomos could not finish this experiment ({datetime.now():%Y-%m-%d %H:%M}).\n\n"
                  f"Reason: {reason}\n"
                  + (f"\nMost likely cause:\n{likely}" if likely else "")
                  + f"{log_hint}\n"
                  f"After fixing the cause: Ionomos app -> Run & Test -> Retry a failed job, "
                  f"or  ionomos retry {job.id}\n")
        log.error("job %d failed: %s", job.id, reason)


# -------------------------------------------------------- telling a person --


def _waiting_causes(reason: str) -> list[str]:
    r = reason.lower()
    if "fasta" in r:
        return ["The method's protein database (FASTA) isn't set or the file was moved — tab 3 Methods"]
    if "workflow" in r:
        return ["The method's FragPipe workflow file isn't there — tab 3 Methods → Import workflow…"]
    if "disk" in r or "space" in r:
        return ["Not enough free disk space for FragPipe's output — free space on the data drive"]
    if "fragpipe" in r or "launcher" in r:
        return ["FragPipe isn't found — tab 1 Folders → Find FragPipe"]
    return [reason]


def _tell(cfg, kind: str, job: Job, title: str, message: str, **kw) -> None:
    try:
        from ionomos import attention

        attention.raise_item(getattr(cfg, "log_dir", None), kind, title, message, key=f"{kind}:job{job.id}",
                             dest=job.dest_dir, job_id=job.id, **kw)
    except Exception:  # noqa: BLE001 - never let this stop a search
        log.exception("could not record %s for job %s", kind, job.id)


def _close(cfg, job: Job, *kinds: str) -> None:
    try:
        from ionomos import attention

        for kind in kinds:
            attention.resolve_where(getattr(cfg, "log_dir", None), kind=kind, job_id=job.id)
    except Exception:  # noqa: BLE001
        log.exception("could not close attention items for job %s", job.id)
