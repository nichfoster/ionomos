"""Failsafes: instance lock, heartbeat, supervisor, crash files, ledger repair/backup, config backups,
FragPipe error explanations, pause/cancel, graceful stop of a real `labwatch run` process."""
import json
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from labwatch import configio, fragpipe, health, service, testbed
from labwatch import ledger as ledger_mod
from labwatch.config import load
from labwatch.intake import intake
from labwatch.ledger import Job, Ledger
from labwatch.worker import Worker, pause, paused, request_cancel, resume


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("LABWATCH_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("LABWATCH_FAKE_FP_MODE", raising=False)
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": Ledger(cfg.database)}


def _queue(bed, sample="iso_good") -> Path:
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


# ------------------------------------------------------------------- health --


def test_instance_lock_is_exclusive(tmp_path):
    a = health.InstanceLock(tmp_path)
    a.acquire()
    assert health.is_locked(tmp_path)
    with pytest.raises(health.AlreadyRunning):
        health.InstanceLock(tmp_path).acquire()
    a.release()
    assert not health.is_locked(tmp_path)
    with health.InstanceLock(tmp_path):  # reusable after release
        pass


def test_heartbeat_and_staleness(tmp_path):
    hb = health.Heartbeat(tmp_path, min_interval=0)
    hb.beat("watcher")
    hb.beat("worker", "running job 3: IonQuant")
    text, healthy = health.heartbeat_summary(tmp_path)
    assert healthy and "worker" in text and "IonQuant" in text
    text, healthy = health.heartbeat_summary(tmp_path, now=time.time() + 600)
    assert not healthy and "NOT RESPONDING" in text
    hb.clear()
    assert health.heartbeat_summary(tmp_path) == ("no heartbeat", False)


def test_supervisor_restarts_a_crashing_loop():
    stop = threading.Event()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        stop.set()

    crashes = []
    t0 = time.monotonic()
    # shrink the backoff for the test
    orig = stop.wait
    stop.wait = lambda s: orig(0.01)
    health.supervise("x", flaky, stop, on_crash=lambda n, e: crashes.append(str(e)))
    assert len(calls) == 3 and crashes == ["boom", "boom"] and time.monotonic() - t0 < 5


def test_crash_files_are_written_and_pruned(tmp_path):
    for i in range(25):
        assert health.write_crash_file(tmp_path, "t", f"Traceback {i}")
    assert len(list(tmp_path.glob("crash-*.txt"))) == 20
    assert health.recent_crashes(tmp_path, 1)


def test_excepthook_catches_thread_crash(tmp_path):
    old_sys, old_thr = sys.excepthook, threading.excepthook
    try:
        health.install_excepthooks(tmp_path)
        t = threading.Thread(target=lambda: 1 / 0, name="doomed")
        t.start()
        t.join()
        crash = health.recent_crashes(tmp_path, 1)
        assert crash and "ZeroDivisionError" in crash[0].read_text(encoding="utf-8")
    finally:
        sys.excepthook, threading.excepthook = old_sys, old_thr


def test_log_problems_extracts_warnings_and_tracebacks(tmp_path):
    lf = tmp_path / "labwatch.log"
    lf.write_text(
        "2026-09-22 10:00:00,1 INFO    labwatch: fine\n"
        "2026-09-22 10:00:01,1 WARNING labwatch.worker: job 1 waiting: x\n"
        "2026-09-22 10:00:02,1 ERROR   labwatch.watcher: scan failed\n"
        "Traceback (most recent call last):\n  File \"a.py\", line 1\nOSError: gone\n"
        "2026-09-22 10:00:03,1 INFO    labwatch: fine again\n", encoding="utf-8")
    items = health.log_problems(lf)
    assert len(items) == 2 and "waiting" in items[0] and "OSError: gone" in items[1]


def test_system_facts_do_not_raise(tmp_path):
    assert health.disk_free_gb(tmp_path / "does" / "not" / "exist") > 0
    total, _ = health.memory_gb()
    assert total is None or total > 0


# ------------------------------------------------------------------- ledger --


def test_ledger_integrity_backup_and_rebuild(bed):
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    _queue(bed, "dia_good")
    db = bed["cfg"].database
    assert ledger_mod.integrity(db) == "ok"
    b = ledger_mod.backup(db, bed["cfg"].log_dir / "backups")
    assert b.is_file() and ledger_mod.backup(db, bed["cfg"].log_dir / "backups") == b  # once per day
    bed["ledger"].close()

    db.write_bytes(b"this is not a database" * 100)  # disaster
    assert ledger_mod.integrity(db).startswith(("unreadable", "corrupt"))
    moved, n = ledger_mod.rebuild_from_status_files(db, bed["cfg"].users_root)
    assert n == 2 and moved.is_file() and "broken" in moved.name
    jobs = Ledger(db).list()
    assert sorted(j.status for j in jobs) == ["done", "queued"]
    assert any(Path(j.dest_dir) == dest for j in jobs)


def test_cli_repair_ledger(bed, capsys):
    from labwatch.cli import main

    _queue(bed)
    bed["ledger"].close()
    bed["cfg"].database.write_bytes(b"\0" * 4096)
    assert main(["--config", str(bed["cfg_path"]), "repair-ledger"]) == 0
    assert "1 job(s)" in capsys.readouterr().out
    assert main(["--config", str(bed["cfg_path"]), "check"]) in (0, 1)
    assert "ledger" in capsys.readouterr().out


# ------------------------------------------------------------------ config --


def test_config_backups_on_write(tmp_path):
    p = tmp_path / "config.yaml"
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "Gen"))
    configio.write_config(p, d)
    assert not (tmp_path / configio.BACKUP_DIR).exists()  # nothing to back up the first time
    d["watcher"]["stable_seconds"] = 99
    configio.write_config(p, d)
    backups = list((tmp_path / configio.BACKUP_DIR).glob("config-*.yaml"))
    assert len(backups) == 1 and "stable_seconds: 60" in backups[0].read_text(encoding="utf-8")


# --------------------------------------------------------------- fragpipe --


@pytest.mark.parametrize(("console", "expect"), [
    ("java.lang.OutOfMemoryError: Java heap space", "out of memory"),
    ("FASTA file path is empty", "No protein database"),
    ("There is not enough space on the disk.", "disk is full"),
    ("Please download MSFragger to continue", "MSFragger isn't installed"),
    ("IonQuant jar not found", "IonQuant isn't installed"),
    ("The process cannot access the file because it is being used by another process", "open in another program"),
    ("'C:\\x\\fragpipe.bat' is not recognized as an internal or external command", "launcher path is wrong"),
    ("all fine", None),
])
def test_explain(console, expect):
    hints = fragpipe.explain(console)
    if expect is None:
        assert hints == []
    else:
        assert hints and expect in hints[0]


def test_progress_parses_fragpipe_format(tmp_path):
    log = tmp_path / "c.log"
    log.write_text("MSFragger [Work dir: C:/x]\nProcess 'MSFragger' finished, exit code: 0\n"
                   "IonQuant [Work dir: C:/x]\n", encoding="utf-8")
    assert fragpipe.progress(log) == "IonQuant (1 step(s) done)"
    assert fragpipe.progress(tmp_path / "missing.log") == "starting"


def test_fasta_info_and_workflow_inspection(tmp_path):
    fa = tmp_path / "db.fas"
    fa.write_text(">sp|A\nMK\n>sp|B\nMK\n>rev_sp|A\nKM\n", encoding="utf-8")
    assert fragpipe.fasta_info(fa) == {"entries": 3, "decoys": 1, "gb": fa.stat().st_size / 1e9}
    wf = tmp_path / "x.workflow"
    wf.write_text(f"database.db-path={fa.as_posix()}\nionquant.mbr=1\ndiann.run-dia-nn=false\n", encoding="utf-8")
    info = fragpipe.inspect_workflow(wf)
    assert info["database_exists"] and info["settings"] == {"match-between-runs": "1", "DIA-NN": "false"}
    lines = fragpipe.describe_files(tmp_path, tmp_path, "x", "db.fas")
    assert [ok for ok, _ in lines] == [True, True] and "2 targets + 1 decoys" in lines[1][1]
    fa.write_text(">sp|A\nMK\n", encoding="utf-8")
    assert fragpipe.describe_files(tmp_path, tmp_path, "x.workflow", "db.fas")[1][0] is None  # no decoys


def test_import_workflow_copies_fasta_and_keeps_old(tmp_path):
    fa = tmp_path / "elsewhere" / "human.fas"
    fa.parent.mkdir()
    fa.write_text(">rev_x\nM\n", encoding="utf-8")
    src = tmp_path / "run" / "fragpipe.workflow"
    src.parent.mkdir()
    src.write_text(f"database.db-path={fa.as_posix()}\n", encoding="utf-8")
    wf_dir, fa_dir = tmp_path / "wf", tmp_path / "fa"
    wf_dir.mkdir()
    (wf_dir / "isoDTB.workflow").write_text("old\n", encoding="utf-8")
    res = fragpipe.import_workflow(src, "isoDTB", wf_dir, fa_dir)
    assert res["workflow"] == "isoDTB.workflow" and res["fasta"] == "human.fas"
    assert (fa_dir / "human.fas").is_file()
    assert list(wf_dir.glob("isoDTB.workflow.bak-*"))[0].read_text(encoding="utf-8") == "old\n"


def test_install_report_on_a_fragpipe_tree(bed, tmp_path):
    root = tmp_path / "FragPipe-24.0" / "fragpipe"
    for f in ("bin/fragpipe.bat", "lib/fragpipe-24.0.jar", "tools/MSFragger-4.3/MSFragger-4.3.jar",
              "tools/IonQuant-1.11.11.jar"):
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("", encoding="utf-8")
    cfg = replace(bed["cfg"], fragpipe_exe=root / "bin" / "fragpipe.bat")
    rows = {label: (ok, detail) for ok, label, detail in fragpipe.install_report(cfg)}
    assert rows["FragPipe"][1].startswith("version 24.0")
    assert rows["MSFragger"] == (True, "MSFragger-4.3.jar")
    assert rows["diaTracer"][0] is None and "Download" in rows["diaTracer"][1]


@pytest.mark.parametrize(("mode", "hint"), [("oom", "out of memory"), ("msfragger", "MSFragger isn't installed")])
def test_failed_job_gets_plain_english_cause(bed, monkeypatch, mode, hint):
    monkeypatch.setenv("LABWATCH_FAKE_FP_MODE", mode)
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and hint in job.reason
    note = (dest / "FAILED.txt").read_text(encoding="utf-8")
    assert "Most likely cause" in note and hint in note


def test_step_failure_with_exit_code_zero_is_caught(bed, monkeypatch):
    monkeypatch.setenv("LABWATCH_FAKE_FP_MODE", "step-fail-exit0")
    _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "step MSFragger failed (exit code 137)" in job.reason


def test_empty_output_with_exit_zero_fails(bed, monkeypatch):
    monkeypatch.setenv("LABWATCH_FAKE_FP_MODE", "silent-exit0")
    _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert "wrote nothing" in bed["ledger"].get(1).reason


def test_low_disk_holds_job(bed, monkeypatch):
    monkeypatch.setattr(fragpipe, "_disk_free_gb", lambda p: 1.0)
    cfg = replace(bed["cfg"], min_free_gb=20)
    _queue(bed)
    assert not Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "low disk space" in job.reason


# ------------------------------------------------------------ pause/cancel --


def test_pause_and_resume(bed):
    _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    pause(bed["cfg"].log_dir, "test")
    assert paused(bed["cfg"].log_dir) and not w.run_once()
    assert bed["ledger"].get(1).status == "queued"
    resume(bed["cfg"].log_dir)
    assert w.run_once() and bed["ledger"].get(1).status == "done"


def test_cancel_queued_job(bed):
    dest = _queue(bed)
    assert "cancelled" in request_cancel(bed["ledger"], 1)
    assert bed["ledger"].get(1).status == "failed"
    assert (dest / "FAILED.txt").is_file()
    assert not Worker(bed["cfg"], bed["ledger"]).run_once()


def test_cancel_running_job(bed, monkeypatch):
    monkeypatch.setenv("LABWATCH_FAKE_FP_SECONDS", "30")
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"], poll_seconds=0.1)
    t = threading.Thread(target=w.run_once, daemon=True)
    t.start()
    for _ in range(100):
        if bed["ledger"].get(1).status == "running":
            break
        time.sleep(0.1)
    time.sleep(0.5)
    other = Ledger(bed["cfg"].database)  # the app / CLI use their own connection
    assert "stopped within a few seconds" in request_cancel(other, 1)
    t.join(timeout=20)
    job = bed["ledger"].get(1)
    assert job.status == "failed" and job.reason == "cancelled by user"
    assert not (dest / fragpipe.RUN_DIR / fragpipe.CANCEL_FILE).exists()


# -------------------------------------------------- the real `labwatch run` --


def _run_proc(cfg_path: Path, env_extra: dict) -> subprocess.Popen:
    import os

    env = {**os.environ, **env_extra}
    return subprocess.Popen([sys.executable, "-m", "labwatch", "--config", str(cfg_path), "run", "--no-gui"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)


def _wait(cond, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_run_process_single_instance_graceful_stop_and_recovery(bed):
    cfg, log_dir = bed["cfg"], bed["cfg"].log_dir
    p1 = _run_proc(bed["cfg_path"], {"LABWATCH_FAKE_FP_SECONDS": "60"})
    try:
        assert _wait(lambda: health.is_locked(log_dir))
        # a second watcher on the same setup refuses to start
        p2 = _run_proc(bed["cfg_path"], {})
        out2, _ = p2.communicate(timeout=30)
        assert p2.returncode == 3 and "already running" in out2

        testbed.drop(bed["root"], "iso_good")
        led = Ledger(cfg.database)
        assert _wait(lambda: led.list() and led.list()[0].status == "running", 40)
        assert _wait(lambda: "worker" in (health.read_heartbeat(log_dir) or {}).get("parts", {}))

        # graceful stop: FragPipe killed, job back to queued, lock released
        assert service.request_stop(log_dir, proc=p1, timeout=30) == "stopped"
        assert p1.wait(timeout=10) == 0
        job = led.get(1)
        assert job.status == "queued" and "interrupted" in job.reason
        assert not health.is_locked(log_dir) and health.read_heartbeat(log_dir) is None
    finally:
        if p1.poll() is None:
            p1.kill()

    # restart: the interrupted job is picked up again and finishes
    p3 = _run_proc(bed["cfg_path"], {"LABWATCH_FAKE_FP_SECONDS": "0"})
    try:
        assert _wait(lambda: Ledger(cfg.database).get(1).status == "done", 40)
        assert Ledger(cfg.database).get(1).attempts == 2
    finally:
        service.request_stop(log_dir, proc=p3, timeout=30)


def test_run_process_rebuilds_a_corrupt_ledger(bed):
    _queue(bed)
    bed["ledger"].close()
    bed["cfg"].database.write_bytes(b"garbage" * 1000)
    p = _run_proc(bed["cfg_path"], {"LABWATCH_FAKE_FP_SECONDS": "0"})
    try:
        assert _wait(lambda: ledger_mod.integrity(bed["cfg"].database) == "ok"
                     and Ledger(bed["cfg"].database).list()
                     and Ledger(bed["cfg"].database).list()[0].status == "done", 40)
        assert list(bed["cfg"].database.parent.glob("labwatch.db.broken-*"))
    finally:
        service.request_stop(bed["cfg"].log_dir, proc=p, timeout=30)


def test_diagnostics_report_and_zip(bed, monkeypatch):
    import zipfile

    monkeypatch.setenv("LABWATCH_FAKE_FP_MODE", "oom")
    _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    health.write_crash_file(bed["cfg"].log_dir, "test", "Traceback: pretend")
    text = service.diagnostics(bed["cfg_path"])
    for needle in ("=== system", "=== watcher", "=== check", "=== job 1", "likely cause: FragPipe ran out of memory",
                   "=== ledger", "integrity: ok", "=== crash report"):
        assert needle in text, needle
    z = service.save_diagnostics_zip(bed["cfg_path"])
    names = zipfile.ZipFile(z).namelist()
    assert "report.txt" in names and "config.yaml" in names
    assert any(n.endswith("labwatch_run/fragpipe_console.log") for n in names)
    assert not any(n.endswith(".raw") for n in names)
    rec = json.loads(zipfile.ZipFile(z).read(next(n for n in names if n.endswith("labwatch.json"))))
    assert rec["status"] == "failed"


def test_status_shows_progress_and_pause(bed, capsys):
    from labwatch.cli import main

    pause(bed["cfg"].log_dir)
    _queue(bed)
    assert main(["--config", str(bed["cfg_path"]), "status"]) == 0
    out = capsys.readouterr().out
    assert "PAUSED" in out and "watcher: NOT running" in out


def test_locked_database_waits_instead_of_crashing(bed):
    """Another program holding a write lock briefly must not make intake or the worker fail."""
    con = sqlite3.connect(bed["cfg"].database, timeout=1, check_same_thread=False)
    con.execute("BEGIN EXCLUSIVE")

    def release():
        time.sleep(2)
        con.rollback()
        con.close()

    threading.Thread(target=release).start()
    folder = testbed.drop(bed["root"], "iso_good")
    assert intake(folder, bed["cfg"], Ledger(bed["cfg"].database)).value == "queued"
    assert Ledger(bed["cfg"].database).list()[0].inbox_name


def test_job_insert_uses_status(bed):
    led = bed["ledger"]
    led.insert(Job(inbox_name="x", user="EJQ", method="isoDTB", dest_dir="/nowhere", status="done"))
    assert led.get(1).status == "done"


def test_filed_folder_missing_from_ledger_is_adopted(bed, monkeypatch):
    """DB unwritable right after the move: the folder must not be orphaned forever."""
    led = bed["ledger"]
    monkeypatch.setattr(led, "insert", lambda job: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    folder = testbed.drop(bed["root"], "iso_good")
    assert intake(folder, bed["cfg"], led).value == "queued"  # moved; not a retry
    monkeypatch.undo()
    fresh = Ledger(bed["cfg"].database)
    assert fresh.list() == []
    assert ledger_mod.adopt_orphans(fresh, bed["cfg"].users_root) == [1]
    assert ledger_mod.adopt_orphans(fresh, bed["cfg"].users_root) == []  # idempotent
    Worker(bed["cfg"], fresh).run_once()
    assert fresh.get(1).status == "done"


@pytest.mark.parametrize("case", ["symbol_folder_with_yaml", "emoji_raw_with_resolver", "bug_in_plan"])
def test_intake_never_loops_on_a_bad_folder(bed, monkeypatch, case):
    """Deterministic problems must end in a .REJECTED.txt note, not an endless retry."""
    from labwatch import intake as intake_mod
    from labwatch.intake import note_path

    cfg = bed["cfg"]
    resolver = None
    if case == "symbol_folder_with_yaml":
        f = cfg.inbox / "🧪🧪"
        f.mkdir()
        (f / "x_1_1.raw").write_bytes(b"1")
        (f / "experiment.yaml").write_text("method: isoDTB\nuser: EJQ\n", encoding="utf-8")
    elif case == "emoji_raw_with_resolver":
        f = cfg.inbox / "EJQ_isoDTB_x"
        f.mkdir()
        (f / "🧪.raw").write_bytes(b"1")

        class Skip:
            def resolve(self, d):
                return None

        resolver = Skip()
    else:
        f = cfg.inbox / "EJQ_isoDTB_y"
        f.mkdir()
        (f / "x_1_1.raw").write_bytes(b"1")
        monkeypatch.setattr(intake_mod, "_plan", lambda *a, **k: 1 / 0)
    assert intake(f, cfg, bed["ledger"], resolver).value == "rejected"
    assert note_path(f).is_file() and f.is_dir()
    if case == "bug_in_plan":
        assert "unexpected problem" in note_path(f).read_text(encoding="utf-8")


def test_transient_disk_error_retries(bed, monkeypatch):
    from labwatch import intake as intake_mod

    f = testbed.drop(bed["root"], "iso_good")
    monkeypatch.setattr(intake_mod, "_plan", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    assert intake(f, bed["cfg"], bed["ledger"]).value == "retry"
