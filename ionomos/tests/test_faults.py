"""Fault injection around the FragPipe worker (D69): every way a search can go wrong that the lab PC can produce,
acted out by the testbed's fake FragPipe (fake_fragpipe.py MODES, chosen per experiment with
fake_fragpipe_mode.txt) or by the test itself, through the real worker.

For each fault the job must end in a clear state (done, failed with a plain-English cause from
fragpipe.EXPLANATIONS and a FAILED.txt, or held), nothing the user made is deleted, and the worker carries on
with the next job. No test waits on the wall clock beyond polling for a condition: time limits run on a fake
clock (fragpipe._clock) and every "while it runs" step waits for what the console log says.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from ionomos import fragpipe, health, names, runner, service, testbed
from ionomos import worker as worker_mod
from ionomos.config import load
from ionomos.intake import intake, plan
from ionomos.ledger import Job, Ledger
from ionomos.worker import Worker


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    monkeypatch.setattr(fragpipe, "POLL_SECONDS", 0.05)
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": Ledger(cfg.database)}


def _queue(bed, sample: str = "iso_good", mode: str = "", name: str = "") -> Path:
    """Drop a testbed sample (renamed to `name`), with the fake's fault for it, and file it. Returns the folder."""
    folder = testbed.drop(bed["root"], sample)
    if name:
        folder = folder.rename(folder.with_name(name))
    if mode:
        (folder / names.FAKE_FP_MODE_FILE).write_text(mode + "\n", encoding="utf-8")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _raws(dest: Path) -> dict[str, int]:
    return {p.relative_to(dest).as_posix(): p.stat().st_size for p in dest.rglob("*.raw")
            if "fragpipe" not in p.relative_to(dest).parts[0]}


def _status(dest: Path) -> dict:
    return json.loads((dest / names.STATUS_FILE).read_text(encoding="utf-8"))


def _console(dest: Path) -> str:
    return fragpipe.read_tail_text(dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG, 50_000_000)


def _job(bed, dest: Path) -> Job:
    return next(j for j in bed["ledger"].list() if Path(j.dest_dir) == dest)


def _then_a_good_job_runs(bed) -> None:
    """The worker is not stuck after a fault: the next experiment is searched to the end."""
    dest = _queue(bed, "dia_good", name=f"20260914_Isaac_DIA_after-{len(bed['ledger'].list())}")
    _drain(Worker(bed["cfg"], bed["ledger"]))
    assert _job(bed, dest).status == "done", _job(bed, dest).reason


def _drain(w: Worker, most: int = 20) -> None:
    """run_once until the queue is empty; a job that keeps coming back fails the test instead of looping (D73)."""
    for _ in range(most):
        if not w.run_once():
            return
    raise AssertionError(f"the worker still had work after {most} jobs")


def _wait(cond, seconds: float = 60, step: float = 0.05) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _alive(pid: int) -> bool:
    return health.process_started(pid) is not None


def _in_step(dest: Path, step: str = "MSFragger") -> bool:
    return fragpipe.progress(dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG).startswith(step)


# ----------------------------------------------------- what FragPipe does wrong --

# (fault mode(s), final status, words in the reason, words of the plain-English cause or None)
FAILS = [
    ("oom", "failed", "step MSFragger failed (exit code 1)", "ran out of memory"),
    ("killed", "failed", "FragPipe exited with code", "ended from outside"),
    ("disk-full", "failed", "There is not enough space on the disk", "The disk is full"),
    ("raw-vanished", "failed", "FileNotFoundException", "A raw file disappeared while FragPipe searched it"),
    ("garbled-log", "failed", "C:\\Users\\Müller\\Proben", "open in another program"),
    ("empty-table", "failed", "is empty (0 bytes)", "not written to the end"),
    ("truncated-table", "failed", "ends in the middle of a row", "not written to the end"),
    ("header-only", "failed", "has only its header line", "found no identifications"),
    ("missing-table,no-done-line", "failed", "did not finish", "did not finish"),
    ("step-fail-exit0", "failed", "although FragPipe exited 0", None),
    ("cancel-exit0", "failed", "FragPipe stopped early", None),
    ("silent-exit0", "failed", "wrote nothing", None),
]


@pytest.mark.parametrize(("mode", "status", "reason", "cause"), FAILS, ids=[f[0] for f in FAILS])
def test_a_search_that_goes_wrong_ends_failed_with_its_cause(bed, mode, status, reason, cause):
    dest = _queue(bed, mode=mode)
    before = _raws(dest)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == status and reason in job.reason, job.reason
    note = (dest / "FAILED.txt").read_text(encoding="utf-8")
    assert not (dest / "DONE.txt").exists() and reason in note
    if cause:
        assert cause in note.split("Most likely cause:")[1], note
        assert cause in (_status(dest)["run"].get("hints") or [""])[0]
    assert all(ch == "\n" or ch == "\t" or ch >= " " for ch in note), "control characters in FAILED.txt"
    assert _status(dest)["status"] == "failed"
    assert _raws(dest) == before  # nothing of the user's is touched
    assert not (dest / fragpipe.RUN_DIR / names.ENGINE_PID_FILE).exists()
    _then_a_good_job_runs(bed)


# (fault mode(s), the warning the done job carries)
WARNS = [
    ("no-done-line", "no 'ALL JOBS DONE' line"),
    ("missing-table", "none of the expected isoDTB outputs"),
    ("truncated-psm", "psm.tsv ends in the middle of a row"),
]


@pytest.mark.parametrize(("mode", "warning"), WARNS, ids=[w[0] for w in WARNS])
def test_a_search_that_finished_with_something_odd_is_done_with_a_note(bed, mode, warning):
    dest = _queue(bed, mode=mode)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == "done" and warning in job.reason, job.reason
    assert "Note: " in (dest / "DONE.txt").read_text(encoding="utf-8")
    assert warning in (dest / "DONE.txt").read_text(encoding="utf-8")


def test_a_huge_console_log_is_read_from_its_end_only(bed, monkeypatch):
    """Several MB of console output, one line of 1 MB without a break: the search is judged, the fingerprint and
    FAILED-note readers stay small, and nothing reads the whole file into memory."""
    monkeypatch.setenv("IONOMOS_FAKE_FP_LOG_MB", "4")
    dest = _queue(bed, mode="huge-log")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert _job(bed, dest).status == "done", _job(bed, dest).reason
    log = dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG
    assert log.stat().st_size > 5_000_000
    fp = json.loads((dest / fragpipe.RUN_DIR / names.FINGERPRINT_FILE).read_text(encoding="utf-8"))
    assert fp["console"]["facts"]["all_jobs_done_minutes"] is not None
    assert (dest / fragpipe.RUN_DIR / names.FINGERPRINT_FILE).stat().st_size < 200_000
    assert len(fragpipe.tail(log, 4)) <= 600


def test_a_tool_printing_forever_is_stopped_before_it_fills_the_disk(bed, monkeypatch):
    monkeypatch.setattr(fragpipe, "MAX_CONSOLE_BYTES", 400_000)
    dest = _queue(bed, mode="runaway-log")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == "failed" and "wrote more than 0.4 MB to its console log" in job.reason
    assert "printed the same thing over and over" in (dest / "FAILED.txt").read_text(encoding="utf-8")
    _then_a_good_job_runs(bed)


def test_garbled_console_text_is_read_as_windows_writes_it():
    data = (b"MSFragger [Work dir: C:\\x]\n"
            + b"\x1b[31mERROR\x1b[0m Pr\xfcfe 5 \xb5L\r\n"           # Windows code page 1252, colour codes
            + "Reading spectra 50%\n".encode("utf-16-le")            # a tool writing UTF-16
            + bytes(range(0, 9)) + b"\xff\xfe\x81junk\n"             # binary junk
            + "Größe ok\n".encode())                                 # real UTF-8 stays UTF-8
    text = fragpipe.decode_console(data)
    assert "ERROR Prüfe 5 µL" in text and "Reading spectra 50%" in text and "Größe ok" in text
    assert "\x1b" not in text and "\x00" not in text and "MSFragger [Work dir" in text
    assert fragpipe.progress  # (the same reader feeds progress, explain and the fingerprint)


def test_tables_that_were_not_written_to_the_end(tmp_path):
    t = tmp_path / "t.tsv"
    cases = {b"": "is empty", b"a\tb\tc\n": "no rows", b"a\tb\tc\n1\t2\t3\n4\t5": "cut off",
             b"a\tb\tc\n1\t2\t3\n": "", b"a\tb\tc\n1\t2\t3": "", b"a\tb\n\x00\x01\x02\n": "binary"}
    for data, want in cases.items():
        t.write_bytes(data)
        got = fragpipe.table_problem(t)
        assert (want in got) if want else got == "", (data, got)


@pytest.mark.parametrize(("code", "text", "want"), [
    (-9, "", "ended by SIGKILL"),
    (-15, "", "ended by SIGTERM"),
    (-11, "", "crashed with SIGSEGV"),
    (0xC000013A, "", "ended by Ctrl+C or the console window closing"),
    (0xC0000005, "", "crashed with an access violation"),
    (1, "MSFragger [Work dir: C:\\x]\nChecking database...\n", "stopped in the middle of a step without saying why"),
    (1, "2026-10-01 14:03:11,532 ERROR - No decoys found in the FASTA file.\n", ""),
    (1, "", ""),
])
def test_what_an_exit_code_alone_says(code, text, want):
    if code < 0 and os.name == "nt":
        pytest.skip("signals are POSIX")
    got = fragpipe.exit_code_reason(code, text)
    assert got.startswith(want) if want else got == ""
    if want:
        assert fragpipe.explain(f"FragPipe {got}")


class _FakeOs:
    """The module's os with some names replaced (never os.name globally: CLAUDE.md)."""

    def __init__(self, real, **over):
        self._real, self._over = real, over

    def __getattr__(self, item):
        return self._over[item] if item in self._over else getattr(self._real, item)


# --------------------------------------------------------------- time and kills --


def test_a_hung_fragpipe_is_killed_with_everything_it_started_at_the_time_limit(bed, monkeypatch):
    """The fake hangs in MSFragger with a child process of its own. The time limit is reached on a fake clock the
    moment that child exists, so the test never waits for the limit itself."""
    dest = _queue(bed, mode="hang,child")
    child_pid_file = dest / "fragpipe" / "child.pid"
    monkeypatch.setattr(fragpipe, "_clock", lambda: time.monotonic() + (1e7 if child_pid_file.exists() else 0))
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == "failed" and "timed out after 5 min" in job.reason, job.reason
    note = (dest / "FAILED.txt").read_text(encoding="utf-8")
    assert "ran longer than the time limit" in note
    child = int(child_pid_file.read_text(encoding="utf-8"))
    assert _wait(lambda: not _alive(child), 15), "a process FragPipe started outlived the time limit"
    assert not (dest / fragpipe.RUN_DIR / names.ENGINE_PID_FILE).exists()
    _then_a_good_job_runs(bed)


def test_fragpipe_killed_from_outside_fails_the_job_with_that_cause(bed):
    dest = _queue(bed, mode="hang")
    w = Worker(bed["cfg"], bed["ledger"])
    t = threading.Thread(target=w.run_once, daemon=True)
    t.start()
    marker = dest / fragpipe.RUN_DIR / names.ENGINE_PID_FILE
    assert _wait(lambda: marker.exists() and _in_step(dest))
    rec = json.loads(marker.read_text(encoding="utf-8"))
    fragpipe.kill_pid_tree(rec["pid"], rec["group"])  # Task Manager, from the worker's point of view
    t.join(timeout=60)
    assert not t.is_alive()
    job = _job(bed, dest)
    assert job.status == "failed" and "FragPipe exited with code" in job.reason, job.reason
    assert "ended from outside" in (dest / "FAILED.txt").read_text(encoding="utf-8")
    _then_a_good_job_runs(bed)


def test_a_leftover_fragpipe_is_stopped_only_when_it_is_really_ours(tmp_path):
    """stop_leftover kills the process an earlier Ionomos recorded, and never a process that only has its
    number (the OS reuses them)."""
    run_dir = tmp_path / "ionomos_run"
    run_dir.mkdir()
    spec = fragpipe.RunSpec(job_id=7, method="isoDTB", dest=tmp_path, exe=Path("x"), workflow_src=Path("x"),
                            fasta=None, manifest_lines=[], threads=1, ram_gb=1, timeout_minutes=1)
    with open(tmp_path / "sleeper.txt", "wb") as sink:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdout=sink,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **fragpipe._popen_kwargs())
    try:
        assert _wait(lambda: _alive(proc.pid), 10)
        fragpipe._record_engine(spec, proc.pid)
        marker = run_dir / names.ENGINE_PID_FILE
        rec = json.loads(marker.read_text(encoding="utf-8"))
        assert rec["pid"] == proc.pid and rec["started"] is not None

        rec["started"] -= 3600  # the same number, but a process that started an hour later: not ours
        marker.write_text(json.dumps(rec), encoding="utf-8")
        assert fragpipe.stop_leftover(run_dir) == "" and not marker.exists()
        assert proc.poll() is None

        fragpipe._record_engine(spec, proc.pid)
        assert "was still running after Ionomos stopped" in fragpipe.stop_leftover(run_dir)
        assert proc.wait(timeout=15) is not None and not marker.exists()
        assert fragpipe.stop_leftover(run_dir) == ""  # nothing recorded: nothing to do
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)


def test_a_taskkill_that_never_returns_does_not_hold_the_worker(monkeypatch):
    """Windows: every stop goes through `taskkill /T`. One that hangs (a wedged process table, an antivirus hook)
    is given up after a time limit, so the worker, `ionomos run`'s shutdown and the app's Stop go on (D73)."""
    from ionomos import service

    calls = []

    def hung_run(cmd, **kw):
        calls.append((cmd, kw.get("timeout")))
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 0)

    class Proc:
        pid = 4242

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("fragpipe", timeout)

    monkeypatch.setattr(fragpipe, "os", _FakeOs(os, name="nt"))
    monkeypatch.setattr(service, "os", _FakeOs(os, name="nt"))
    monkeypatch.setattr(service, "_creationflags", lambda: 0)  # Windows-only constants
    monkeypatch.setattr(fragpipe.subprocess, "run", hung_run)
    fragpipe.kill_tree(Proc())
    fragpipe.kill_pid_tree(4242, group=False)
    service.kill_pid(4242)
    assert service._alive(4242) is False  # can't tell: never reported as running
    assert len(calls) == 4 and all(timeout and timeout <= 60 for _cmd, timeout in calls)
    assert [c[0][0] for c in calls] == ["taskkill", "taskkill", "taskkill", "tasklist"]


def _run_proc(cfg_path: Path, env_extra: dict) -> subprocess.Popen:
    """`ionomos run` as a real process; its output in a file (a pipe nobody reads blocks it on Windows)."""
    env = {**os.environ, **env_extra}
    with open(Path(cfg_path).parent / "run_output.txt", "ab") as sink:
        return subprocess.Popen([sys.executable, "-m", "ionomos", "--config", str(cfg_path), "run", "--no-gui"],
                                stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)


def test_ionomos_killed_mid_search_restarts_without_a_second_fragpipe_or_a_lost_job(bed):
    """Ionomos ended from Task Manager while FragPipe runs: FragPipe lives on (its own process group). The next
    Ionomos stops it, then searches the job again from the start: one attempt more, never two FragPipes on one
    folder, and the job ends done."""
    cfg = bed["cfg"]
    dest = _queue(bed)
    bed["ledger"].close()
    p1 = _run_proc(bed["cfg_path"], {"IONOMOS_FAKE_FP_SECONDS": "300"})
    marker = dest / fragpipe.RUN_DIR / names.ENGINE_PID_FILE
    try:
        # FragPipe is past its checks and into its steps (the settings block is printed just before the first)
        assert _wait(lambda: marker.exists() and "~~~~~~~~~ fragpipe.config ~~~~~~~~~" in _console(dest), 60)
        orphan = json.loads(marker.read_text(encoding="utf-8"))["pid"]
        # Ionomos' own process (from its pid file: a Windows venv's python.exe is a launcher with the real
        # interpreter as its child), ended with no clean-up of any kind: SIGKILL / TerminateProcess
        ionomos_pid = int(names.pid_files(cfg.log_dir)[0].read_text(encoding="utf-8").strip())
        os.kill(ionomos_pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        p1.wait(timeout=30)
    finally:
        if p1.poll() is None:
            p1.kill()
    assert _alive(orphan), "the FragPipe of a killed Ionomos keeps running (the hazard this test is about)"
    assert Ledger(cfg.database).get(1).status == "running"

    p2 = _run_proc(bed["cfg_path"], {"IONOMOS_FAKE_FP_SECONDS": "0"})
    try:
        assert _wait(lambda: Ledger(cfg.database).get(1).status == "done", 90), Ledger(cfg.database).get(1).reason
    finally:
        service.request_stop(cfg.log_dir, proc=p2, timeout=30)
    assert _wait(lambda: not _alive(orphan), 15)
    job = Ledger(cfg.database).get(1)
    assert job.attempts == 2
    console = _console(dest)
    assert console.count("# ionomos job 1 ") == 2 and console.count("ALL JOBS DONE IN") == 1
    log_text = names.log_file(cfg.log_dir).read_text(encoding="utf-8", errors="replace")
    assert "was still running after Ionomos stopped" in log_text
    assert list(dest.glob("fragpipe_previous_*"))  # the killed attempt's output is kept


def test_recovery_says_in_the_folder_what_happened_to_an_interrupted_job(bed):
    """A job found 'running' at start-up: re-queued (its folder says so), or after MAX_ATTEMPTS failed with a
    FAILED.txt, so the folder never claims 'running' for ever."""
    dest = _queue(bed)
    led = bed["ledger"]
    led.start_attempt(1)
    worker_mod._update_status(led.get(1), status="running")
    assert worker_mod.recover(bed["cfg"], led) == [(1, "queued")]
    assert _status(dest)["status"] == "queued" and "interrupted" in _status(dest)["reason"]
    for _ in range(3):
        led.start_attempt(1)
    assert worker_mod.recover(bed["cfg"], led) == [(1, "failed")]
    assert _status(dest)["status"] == "failed" and "interrupted 4 times" in (dest / "FAILED.txt").read_text(
        encoding="utf-8")


# ------------------------------------------------------------------ Ionomos' own writes --


def test_a_full_disk_for_the_console_log_fails_the_job_without_starting_fragpipe(bed, monkeypatch):
    dest = _queue(bed)
    real_open = open

    def no_space(path, *a, **kw):
        if Path(path).name == fragpipe.CONSOLE_LOG and "a" in (a[0] if a else kw.get("mode", "r")):
            raise OSError(28, "No space left on device")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(fragpipe, "open", no_space, raising=False)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == "failed" and "No space left on device" in job.reason
    assert "The disk is full" in (dest / "FAILED.txt").read_text(encoding="utf-8")
    assert not any((dest / "fragpipe").iterdir())  # FragPipe never started
    monkeypatch.undo()
    _then_a_good_job_runs(bed)


def test_an_error_in_ionomos_while_a_search_runs_fails_the_job_and_the_worker_goes_on(bed, monkeypatch):
    dest = _queue(bed)

    def broken(spec):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fragpipe, "missing_outputs", broken)
    w = Worker(bed["cfg"], bed["ledger"])
    assert w.run_once()
    job = _job(bed, dest)
    assert job.status == "failed" and "Ionomos hit an unexpected error while running the search" in job.reason
    assert "Ionomos itself hit a problem" in (dest / "FAILED.txt").read_text(encoding="utf-8")
    monkeypatch.undo()
    _then_a_good_job_runs(bed)


# --------------------------------------------------------------------- while queued --


def test_a_raw_file_that_cannot_be_read_holds_the_job_until_it_can(bed, monkeypatch):
    dest = _queue(bed)
    locked = sorted(dest.glob("*.raw"))[0]
    real = fragpipe._readable
    monkeypatch.setattr(fragpipe, "_readable", lambda p: p != locked and real(p))
    w = Worker(bed["cfg"], bed["ledger"])
    assert not w.run_once()
    job = _job(bed, dest)
    assert job.status == "queued" and "can't be read yet" in job.reason and locked.name in job.reason
    assert not (dest / "fragpipe").exists() and job.attempts == 0
    monkeypatch.setattr(fragpipe, "_readable", real)
    assert w.run_once() and _job(bed, dest).status == "done"


@pytest.mark.skipif(os.name != "nt", reason="a byte-range lock as Xcalibur holds one: Windows only")
def test_a_raw_file_locked_by_another_program_is_not_readable_on_windows(tmp_path):
    import msvcrt

    raw = tmp_path / "a.raw"
    raw.write_bytes(b"x" * 64)
    with open(raw, "r+b") as fh:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 64)
        assert not fragpipe._readable(raw)
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 64)
    assert fragpipe._readable(raw)


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="file permissions as POSIX has them, for a user that isn't root")
def test_a_raw_file_without_read_permission_is_not_readable(tmp_path):
    raw = tmp_path / "a.raw"
    raw.write_bytes(b"x" * 64)
    raw.chmod(0)
    try:
        assert not fragpipe._readable(raw)
    finally:
        raw.chmod(0o644)
    assert fragpipe._readable(raw)


def test_a_raw_file_removed_while_queued_fails_the_job_and_keeps_the_rest(bed):
    dest = _queue(bed)
    gone = sorted(dest.glob("*.raw"))[0]
    gone.unlink()  # (by a person, in Explorer)
    before = _raws(dest)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == "failed" and gone.name in job.reason
    assert "Raw files were moved or deleted" in (dest / "FAILED.txt").read_text(encoding="utf-8")
    assert _raws(dest) == before
    _then_a_good_job_runs(bed)


@pytest.mark.parametrize("what", ["workflow", "fasta"])
def test_a_workflow_or_fasta_that_vanishes_just_before_the_search_holds_the_job(bed, monkeypatch, what):
    dest = _queue(bed)
    real = runner.prepare

    def prepare_then_vanish(job, cfg):
        spec = real(job, cfg)
        target = spec.workflow_src if what == "workflow" else spec.fasta
        target.rename(target.with_name(target.name + ".away"))
        return spec

    monkeypatch.setattr(runner, "prepare", prepare_then_vanish)
    w = Worker(bed["cfg"], bed["ledger"])
    assert not w.run_once()  # waiting is not "ran": the worker sleeps instead of spinning on it
    job = _job(bed, dest)
    assert job.status == "queued" and "missing" in job.reason and "disappeared just before" in job.reason
    assert job.attempts == 0  # a search that never started doesn't count towards MAX_ATTEMPTS
    monkeypatch.undo()
    for p in (bed["cfg"].workflow_dir.glob("*.away"), bed["cfg"].fasta_dir.glob("*.away")):
        for f in p:
            f.rename(f.with_name(f.name[: -len(".away")]))
    assert w.run_once() and _job(bed, dest).status == "done"


def test_a_fasta_with_a_space_in_its_name_holds_the_job_on_windows(bed, monkeypatch):
    fa = bed["cfg"].fasta_dir / "human_reviewed_decoys.fas"
    spaced = fa.with_name("human reviewed decoys.fas")
    fa.rename(spaced)
    cfg = replace(bed["cfg"], methods={**bed["cfg"].methods,
                                       "isoDTB": replace(bed["cfg"].methods["isoDTB"], fasta=spaced.name)})
    dest = _queue(bed)
    monkeypatch.setattr(fragpipe, "_refuses_spaces", lambda: True)  # as on the lab PC
    w = Worker(cfg, bed["ledger"])
    assert not w.run_once()
    job = _job(bed, dest)
    assert job.status == "queued" and "has a space in its path" in job.reason and spaced.name in job.reason
    assert worker_mod._waiting_causes(job.reason)[0].startswith("FragPipe can't use a path with a space")
    monkeypatch.setattr(fragpipe, "_refuses_spaces", lambda: False)  # elsewhere: a note, and it runs
    assert w.run_once() and _job(bed, dest).status == "done"
    assert any("has a space in its path" in n for n in _status(dest)["run"]["notes"])


# ------------------------------------------------------------- one experiment, two jobs --


def test_two_jobs_for_one_experiment_folder_search_it_once(bed):
    dest = _queue(bed)
    bed["ledger"].insert(Job(inbox_name="copy-of-the-same", user="EJQ", method="isoDTB", dest_dir=str(dest),
                             parsed=_status(dest)))
    _drain(Worker(bed["cfg"], bed["ledger"]))
    first, second = bed["ledger"].get(1), bed["ledger"].get(2)
    assert first.status == "done" and first.attempts == 1
    assert second.status == "failed" and "duplicate of job 1" in second.reason and second.attempts == 0
    assert (dest / "DONE.txt").is_file() and not (dest / "FAILED.txt").exists()  # job 1's folder, untouched
    assert _console(dest).count("# ionomos job ") == 1
    assert not list(dest.glob("fragpipe_previous_*"))


def test_the_same_raw_files_dropped_twice_are_two_experiments(bed):
    a = _queue(bed, name="20260902-isoDTB_EJQ-2-027")
    b = _queue(bed, name="20260902-isoDTB_EJQ-2-027_redo")
    _drain(Worker(bed["cfg"], bed["ledger"]))
    assert _job(bed, a).status == "done" and _job(bed, b).status == "done" and a != b


# --------------------------------------------------------------- earlier output --


def test_reruns_within_one_second_keep_every_earlier_output(bed):
    """fragpipe_previous_<time> is named to the second: two moves in one second used to collide (POSIX replaced
    nothing but failed the job; Windows refused the move)."""
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    for _ in range(2):
        (dest / "fragpipe" / "mine.txt").write_text("a note the user left in the output", encoding="utf-8")
        bed["ledger"].requeue(1)
        w.run_once()
        assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    prev = sorted(dest.glob("fragpipe_previous_*"))
    assert len(prev) == 2 and all((p / "mine.txt").is_file() for p in prev)
    assert all((p / "combined_modified_peptide_label_quant.tsv").stat().st_size > 0 for p in prev)


def test_earlier_output_that_cannot_be_moved_aside_holds_the_job_and_is_never_written_into(bed, monkeypatch):
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    old = (dest / "fragpipe" / "combined_modified_peptide_label_quant.tsv").read_bytes()
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)

    def locked(a, b):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(fragpipe, "os", _FakeOs(os, rename=locked))
    assert not w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "can't be moved aside" in job.reason and job.attempts == 0
    assert (dest / "fragpipe" / "combined_modified_peptide_label_quant.tsv").read_bytes() == old
    monkeypatch.undo()
    assert w.run_once() and bed["ledger"].get(1).status == "done"
    prev = list(dest.glob("fragpipe_previous_*"))
    assert len(prev) == 1 and (prev[0] / "combined_modified_peptide_label_quant.tsv").read_bytes() == old


# ------------------------------------------------------------------ the loop goes on --


def test_the_worker_loop_runs_through_a_queue_of_faults(bed):
    """Faults back to back in one running worker, the way the lab PC meets them."""
    modes = ["oom", "truncated-table", "garbled-log", "killed", "no-done-line"]
    dests = [_queue(bed, mode=m, name=f"20260902-isoDTB_EJQ-fault-{i}") for i, m in enumerate(modes)]
    good = _queue(bed, "dia_good")
    w = Worker(bed["cfg"], bed["ledger"], poll_seconds=0.05)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    try:
        assert _wait(lambda: all(j.status in ("done", "failed") for j in bed["ledger"].list()), 120)
    finally:
        w.stop()
        t.join(timeout=30)
    states = [_job(bed, d).status for d in dests]
    assert states == ["failed", "failed", "failed", "failed", "done"]
    assert _job(bed, good).status == "done"
    assert all((d / ("FAILED.txt" if s == "failed" else "DONE.txt")).is_file() for d, s in zip(dests, states,
                                                                                            strict=True))


# ---------------------------------------------------------------------- TMT plexes --


def test_a_drop_with_a_folder_per_plex_keeps_it_and_gets_an_annotation_per_plex(bed):
    dest = _queue(bed, "tmt_plexes")
    assert (dest / "plexA" / "KL6160A_TMT_F1.raw").is_file() and (dest / "plexB" / "KL6160B_TMT_F2.raw").is_file()
    manifest = _status(dest)["plan"]["manifest"]
    assert {(m["file"], m["experiment"]) for m in manifest} >= {("plexA/KL6160A_TMT_F1.raw", "plexA"),
                                                               ("plexB/KL6160B_TMT_F1.raw", "plexB")}
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == "done", job.reason
    for plex in ("plexA", "plexB"):
        assert f"126\tDMSO_{plex}_126" in (dest / plex / "annotation.txt").read_text(encoding="utf-8")
    assert not (dest / "annotation.txt").exists()
    head = (dest / "fragpipe" / "tmt-report" / "abundance_gene_MD.tsv").read_text(encoding="utf-8").splitlines()[0]
    assert "DMSO_plexA_126" in head and "Drug_plexB_127C" in head  # FragPipe found both annotation files
    assert not any("share one folder" in w for w in _status(dest)["run"]["warnings"])


def test_a_flat_drop_with_several_plexes_is_filed_as_it_is_with_a_clear_warning(bed):
    dest = _queue(bed, "tmt_flat_plexes")
    assert sorted(p.name for p in dest.glob("*.raw")) == ["KL6161A_TMT_F1.raw", "KL6161A_TMT_F2.raw",
                                                           "KL6161B_TMT_F1.raw", "KL6161B_TMT_F2.raw"]
    warned = _status(dest)["plan"]["warnings"]
    assert len(warned) == 1 and "2 TMT plexes share one folder" in warned[0] and "<plex>\\*.raw" in warned[0]
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = _job(bed, dest)
    assert job.status == "done" and "share one folder" in job.reason
    assert not list(dest.glob("*annotation.txt"))


def test_plex_folders_are_read_only_when_nothing_is_at_the_top(bed, tmp_path):
    cfg = bed["cfg"]
    drop = cfg.inbox / "20260127_Aman_TMT_layout"
    (drop / "plex A").mkdir(parents=True)
    (drop / "plex B").mkdir()
    (drop / "plex A" / "P1_TMT_F1.raw").write_bytes(b"x" * 64)
    (drop / "plex B" / "P2_TMT_F1.raw").write_bytes(b"x" * 64)
    p = plan(drop, cfg)
    assert p.subdir_renames == {"plex A": "plex-A", "plex B": "plex-B"}
    assert sorted((m.file, m.experiment) for m in p.manifest) == [("plex-A/P1_TMT_F1.raw", "plex-A"),
                                                                  ("plex-B/P2_TMT_F1.raw", "plex-B")]
    assert intake(drop, cfg, bed["ledger"]).value == "queued"
    dest = Path(bed["ledger"].list()[-1].dest_dir)
    assert (dest / "plex-A" / "P1_TMT_F1.raw").is_file()

    same = cfg.inbox / "20260127_Aman_TMT_same-names"
    for sub in ("plexA", "plexB"):
        (same / sub).mkdir(parents=True)
        (same / sub / "P_TMT_F1.raw").write_bytes(b"x" * 64)
    with pytest.raises(Exception, match="FragPipe needs every raw file name once"):
        plan(same, cfg)

    mixed = cfg.inbox / "20260127_Aman_TMT_top-level"
    (mixed / "old").mkdir(parents=True)
    (mixed / "Q_TMT_F1.raw").write_bytes(b"x" * 64)
    (mixed / "old" / "Q_TMT_F9.raw").write_bytes(b"x" * 64)
    assert [m.file for m in plan(mixed, cfg).manifest] == ["Q_TMT_F1.raw"]  # as before: top level wins
