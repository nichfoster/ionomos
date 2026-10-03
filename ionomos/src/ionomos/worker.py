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

from ionomos import fragpipe, runner
from ionomos.config import Config
from ionomos.intake import write_status
from ionomos.ledger import Job, Ledger, LedgerError, now_iso

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
        jobs = self.ledger.list()
        for job in [j for j in jobs if j.status == "queued"]:
            if self._stop.is_set():
                return False
            twin = _twin(job, jobs)
            if twin is not None:
                self._refuse_duplicate(job, twin)
                continue
            try:
                spec = runner.prepare(job, self.cfg)
            except fragpipe.Hold as exc:
                self._hold(job, str(exc))
                continue
            except fragpipe.JobError as exc:
                self._fail(job, str(exc), hints=fragpipe.explain(str(exc)))
                return True
            self._held.pop(job.id, None)
            return self._run(job, spec)
        return False

    def _refuse_duplicate(self, job: Job, twin: Job) -> None:
        """A second job row for an experiment folder that already has one (a rebuilt or hand-edited job list): it
        is never searched, so one folder never gets two FragPipes or two sets of notes (D69). Only the job list
        says so: the folder, its notes and its ionomos.json belong to the other job."""
        reason = (f"duplicate of job {twin.id}: the same experiment folder ({job.dest_dir}); not searched twice — "
                  f"retry job {twin.id} instead")
        log.error("job %d: %s", job.id, reason)
        self.ledger.set_status(job.id, "failed", reason)

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
        _notify(self.cfg, "held", job, reason)

    # ------------------------------------------------------------- running --

    def _run(self, job: Job, spec: fragpipe.RunSpec) -> bool:
        """Run one prepared job. True when it ran (whatever the outcome), False when it went back to waiting.
        Whatever Ionomos itself hits on the way fails the job with that reason instead of leaving it 'running'
        with nobody watching it (D69)."""
        try:
            return self._search(job, spec)
        except Exception as exc:  # noqa: BLE001 - a full disk, an unwritable folder, a bug: the job must end
            log.exception("job %d: Ionomos failed while running the search", job.id)
            self.current = None
            reason = f"Ionomos hit an unexpected error while running the search: {type(exc).__name__}: {exc}"
            try:
                self._fail(job, reason, spec, fragpipe.explain(reason))
            except Exception:  # noqa: BLE001
                log.exception("job %d: could not record the failure; it is re-queued when Ionomos restarts", job.id)
            return True

    def _search(self, job: Job, spec: fragpipe.RunSpec) -> bool:
        dest = spec.dest
        _close(self.cfg, job, "search_waiting", "search_failed")
        # clear a stale CANCEL before the ledger flips to 'running' (issue #17): a cancel delivered
        # after this point must survive into run()'s poll — this unlink used to live in
        # write_inputs, which ate any cancel written in the startup window
        (spec.run_dir / fragpipe.CANCEL_FILE).unlink(missing_ok=True)
        try:
            attempt = self.ledger.start_attempt(job.id)
        except LedgerError as exc:
            # D17: fail where the user will look, not just in the log — without this the
            # job leaves the queue while its folder keeps claiming "queued" forever.
            log.error("job %d: %s", job.id, exc)
            self._fail(job, f"job vanished from the ledger: {exc}")
            return True
        self.current = job
        # an earlier Ionomos that was killed mid-search left its FragPipe running: stop it before starting
        # another on the same folder (D69)
        leftover = fragpipe.stop_leftover(spec.run_dir)
        if leftover:
            log.warning("job %d: %s", job.id, leftover)
            spec.notes.append(leftover)
        try:
            moved = runner.write_inputs(spec)
        except OSError as exc:
            self.current = None
            waiting = _input_hold(spec, exc)
            if waiting:
                self.ledger.requeue(job.id, f"waiting: {waiting}", undo_attempt=True)
                self._held.pop(job.id, None)
                self._hold(job, waiting)
                return False
            self._fail(job, f"could not prepare {spec.engine_name} inputs: {exc}", spec,
                       fragpipe.explain(str(exc)))
            return True
        if moved:
            spec.warnings.append(f"previous attempt's output kept as {moved}/")
        log.info("job %d: %s %s on %s (%d raw files, attempt %d)", job.id, spec.engine_name, job.method, dest,
                 len(spec.manifest_lines), attempt)
        for w in spec.warnings:
            log.warning("job %d: %s", job.id, w)
        for n in getattr(spec, "notes", ()):
            log.info("job %d: %s", job.id, n)
        _update_status(job, status="running", reason=None, run={
            "attempt": attempt, "started_at": now_iso(), "finished_at": None, "exit_code": None,
            "workflow": str(spec.workflow), "workflow_source": str(spec.workflow_src),
            "fasta": str(spec.fasta) if spec.fasta else None, "manifest": str(spec.manifest),
            "workdir": str(spec.workdir), "console_log": str(spec.console_log),
            "command": spec.command(), "warnings": list(spec.warnings), "notes": list(getattr(spec, "notes", ())),
        })
        try:
            (dest / FAILED_NOTE).unlink(missing_ok=True)
        except OSError:
            pass

        def started(pid, cmd):
            log.info("job %d: %s pid %d: %s", job.id, spec.engine_name, pid, " ".join(cmd))
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
        spec.warnings.extend(res.warnings)
        _fingerprint(job, spec, res, attempt, finished)
        if res.cancelled:
            (spec.run_dir / fragpipe.CANCEL_FILE).unlink(missing_ok=True)
            _update_status(job, run={"finished_at": finished, "exit_code": res.code})
            self._fail(job, "cancelled by user", spec)
            return True

        if res.stopped:
            self.ledger.requeue(job.id, "interrupted (ionomos stopped); will run again")
            _update_status(job, status="queued", reason="interrupted (ionomos stopped); will run again",
                           run={"finished_at": finished, "exit_code": res.code})
            log.warning("job %d: FragPipe stopped; job re-queued", job.id)
            return True
        if not res.ok:
            _update_status(job, run={"finished_at": finished, "exit_code": res.code, "hints": res.hints})
            self._fail(job, res.reason, spec, res.hints)
            return True

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
              f"{spec.engine_name} finished {datetime.now():%Y-%m-%d %H:%M}.\n{report}"
              f"{spec.engine_name} output: {spec.workdir}\n"
              + (f"Hits:\n{hits}" if hits else "")
              + ("".join(f"Note: {w}\n" for w in warnings)))
        log.info("job %d: done%s", job.id, f" ({len(warnings)} warning(s))" if warnings else "")
        _notify(self.cfg, "done", job, headline or "", summary)
        return True

    def _postprocess(self, job: Job, spec: fragpipe.RunSpec) -> tuple[list[str], dict]:
        """Downstream analysis (never fails the job; see postprocess.py)."""
        from ionomos import postprocess

        try:
            return postprocess.run_all(job, spec, self.cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %d: analysis crashed", job.id)
            return [f"analysis crashed: {exc}"], {}

    def _fail(self, job: Job, reason: str, spec: fragpipe.RunSpec | None = None, hints: list[str] | None = None) -> None:
        try:
            self.ledger.set_status(job.id, "failed", reason)
        except Exception:  # noqa: BLE001 - e.g. a full disk: still tell the person, in the folder (D69)
            log.exception("job %d: could not record 'failed' in the job list", job.id)
        _update_status(job, status="failed", reason=reason)
        engine = spec.engine_name if spec else "The search"
        if reason != "cancelled by user":
            tail = fragpipe.read_tail_text(spec.console_log, 8000) if spec else ""
            _tell(self.cfg, "search_failed", job, f"{engine} failed on {job.user}/{job.inbox_name}", reason,
                  severity="error", causes=list(hints or []) or [
                      f"See the last lines of {engine}'s log below — the first ERROR / Exception line is usually the cause"],
                  fixes=["Fix the cause, then press Retry here (or Jobs tab → Retry)",
                         "If it's unclear: Report a problem sends the log with everything needed"],
                  details="\n".join(tail.splitlines()[-40:]),
                  data={"console_log": str(spec.console_log) if spec else None})
        dest = Path(job.dest_dir)
        if dest.is_dir():
            log_hint = f"\n{engine} console output: {spec.console_log}\n" if spec else "\n"
            likely = "".join(f"  - {h}\n" for h in hints or [])
            _note(dest, FAILED_NOTE,
                  f"ionomos could not finish this experiment ({datetime.now():%Y-%m-%d %H:%M}).\n\n"
                  f"Reason: {reason}\n"
                  + (f"\nMost likely cause:\n{likely}" if likely else "")
                  + f"{log_hint}\n"
                  f"After fixing the cause: Ionomos app -> Run & Test -> Retry a failed job, "
                  f"or  ionomos retry {job.id}\n")
        log.error("job %d failed: %s", job.id, reason)
        if reason != "cancelled by user":
            _notify(self.cfg, "failed", job, reason)


def _fingerprint(job: Job, spec: fragpipe.RunSpec, res: fragpipe.RunResult, attempt: int, finished: str) -> None:
    """The run fingerprint (fingerprint.py, D59): what this search did, for checking the parsers. Whatever the
    outcome, and never in the job's way."""
    try:
        from ionomos import fingerprint

        path = fingerprint.write(spec, res, attempt, finished)
        if path is not None:
            _update_status(job, run={"fingerprint": str(path), "seconds": res.seconds})
    except Exception:  # noqa: BLE001
        log.exception("job %s: could not write the run fingerprint", job.id)


def _same_folder(a: str, b: str) -> bool:
    import os

    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _twin(job: Job, jobs: list[Job]) -> Job | None:
    """The job that owns `job`'s experiment folder when `job` is a second row for it: one that is running, or one
    with a smaller id that has not been refused as a duplicate itself."""
    for other in jobs:
        if other.id == job.id or not _same_folder(other.dest_dir, job.dest_dir):
            continue
        if other.status == "running" or (other.id < job.id and not (other.reason or "").startswith("duplicate of")):
            return other
    return None


def _input_hold(spec: fragpipe.RunSpec, exc: OSError) -> str:
    """When writing a search's inputs failed for a reason outside the job, the hold reason; '' to fail it.
    The workflow or FASTA vanished since prepare() looked (setup: held like a missing one), or the previous
    attempt's output can't be moved aside because a file in it is open (Excel, Explorer's preview): Ionomos never
    writes a new search into it, so it waits (D69)."""
    if not spec.workflow_src.is_file():
        return f"workflow file for {spec.method} missing: {spec.workflow_src} (it disappeared just before the search)"
    if spec.fasta is not None and not spec.fasta.is_file():
        return f"FASTA for {spec.method} missing: {spec.fasta} (it disappeared just before the search)"
    if isinstance(exc, PermissionError) and spec.workdir.is_dir():
        return (f"the previous attempt's output {spec.workdir} can't be moved aside ({exc.strerror or exc}): a file in "
                f"it is open in another program — close it; it is kept as {spec.workdir.name}_previous_<time>")
    return ""


def recover(cfg: Config, ledger: Ledger) -> list[tuple[int, str]]:
    """At start-up: jobs the last Ionomos left 'running' (ledger.recover_on_startup) go back to queued, or to
    failed after MAX_ATTEMPTS starts, and their folders say so; a FragPipe that outlived that Ionomos is stopped
    first, so the re-run is never a second search beside it (D69). Returns [(job id, new status)]."""
    out = ledger.recover_on_startup()
    for jid, status in out:
        job = ledger.get(jid)
        if job is None:
            continue
        dest = Path(job.dest_dir)
        leftover = fragpipe.stop_leftover(dest / fragpipe.RUN_DIR)
        if leftover:
            log.warning("job %d: %s", jid, leftover)
        if status == "queued":
            _update_status(job, status="queued", reason=job.reason)
            continue
        _update_status(job, status="failed", reason=job.reason)
        _tell(cfg, "search_failed", job, f"The search failed on {job.user}/{job.inbox_name}", job.reason or "",
              severity="error", causes=["Ionomos stopped or the PC restarted during this search every time it ran: "
                                        "it may be what takes the PC down (memory, a crash)"],
              fixes=["Look at the end of FragPipe's log, then press Retry (or Jobs tab → Retry)"])
        if dest.is_dir():
            _note(dest, FAILED_NOTE, f"ionomos could not finish this experiment ({datetime.now():%Y-%m-%d %H:%M}).\n\n"
                                     f"Reason: {job.reason}\n\nAfter fixing the cause: Ionomos app -> Run & Test -> "
                                     f"Retry a failed job, or  ionomos retry {jid}\n")
    return out


# -------------------------------------------------------- telling a person --


def _waiting_causes(reason: str) -> list[str]:
    r = reason.lower()
    if "can't be read yet" in r:
        return ["A raw file is still open in another program — Xcalibur still acquiring, a copy still running, or "
                "antivirus scanning it; the search starts by itself once it can be read"]
    if "has a space in its path" in r:
        return ["FragPipe can't use a path with a space in it: rename the file or folder named above (for a FASTA, "
                "pick one without spaces on tab 3 Methods)"]
    if "can't be moved aside" in r:
        return ["A file in the experiment's fragpipe folder is open (Excel, Explorer's preview pane): close it; "
                "the earlier output is then kept as fragpipe_previous_<time> and the search starts"]
    if "window program" in r:
        return ["The launcher set on tab 1 is FragPipe's window program (the .exe); searches need fragpipe.bat, "
                "which FragPipe installs next to it — tab 1 Folders → Find FragPipe"]
    if "can't be searched" in r:
        return ["FragPipe refuses this FASTA: it needs decoys for about half its entries — add them in "
                "FragPipe's Database tab, then pick that file on tab 3 Methods"]
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


def _notify(cfg, event: str, job: Job, reason: str = "", summary: dict | None = None) -> None:
    """A message to the lab's channel (notify.py; off unless config.yaml notify: asks). Called after the
    status is recorded; it returns at once and can't fail the job."""
    try:
        from ionomos import notify

        notify.announce(cfg, event, job, reason, summary)
    except Exception:  # noqa: BLE001
        log.warning("could not notify about job %s", job.id)
