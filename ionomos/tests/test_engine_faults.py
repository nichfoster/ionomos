"""Search engines that misbehave (round 4; D69 continued). A stand-in program (tests/standin_engine.py) plays
FragPipe's launcher, DIA-NN, MaxQuant, Sage or the raw converter, and hangs, prints garbage or binary, exits with
odd and negative codes, writes partial or empty tables, locks them, leaves a process behind or floods its log.

Every run ends in a clear state (failed, held, or done with a note) with a reason that says what to do and never a
traceback; a retry never converts a file twice or writes over an earlier attempt. kill_tree, kill_pid_tree and
stop_leftover are checked for Windows and POSIX with the module's os / subprocess / signal stubbed (never os.name
globally). Time limits run on a fake clock (fragpipe._clock); every wait has a limit (D73)."""
import io
import json
import os
import signal
import subprocess
import sys
import time
import types
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pytest

from ionomos import attention, fragpipe, health, maxquant, names, preflight, runner, sage, testbed
from ionomos.config import MethodConfig, load
from ionomos.help import hold_topic
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker
from tests.conftest import make_drop

STANDIN = Path(__file__).with_name("standin_engine.py")
WINDOWS = os.name == "nt"
only_windows = pytest.mark.skipif(not WINDOWS, reason="a Windows exit status / file lock")
only_posix = pytest.mark.skipif(WINDOWS, reason="POSIX signals / process groups")


def write_standin(folder: Path, name: str) -> Path:
    """<name>.bat / <name>.sh running the stand-in, as fragpipe.bat runs Java: a launcher with the program as its
    child (so Windows' taskkill /T and a POSIX process group are what end it)."""
    folder.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        exe = folder / f"{name}.bat"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{STANDIN}" %*\r\n', encoding="utf-8")
    else:
        exe = folder / f"{name}.sh"
        exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{STANDIN}' \"$@\"\n", encoding="utf-8")
        exe.chmod(0o755)
    return exe


def _alive(pid: int) -> bool:
    return health.process_started(pid) is not None


def _wait(cond, seconds: float = 30, step: float = 0.05) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def _end(pid_file: Path) -> None:
    """Clean up a stand-in's grandchild a test left running on purpose."""
    try:
        pid = int(pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if _alive(pid):
        fragpipe.kill_pid_tree(pid)


def _plain(text: str) -> None:
    """A message for a person: no traceback, no control characters, no NUL."""
    assert "Traceback" not in text and "\x00" not in text, text
    assert all(ch in "\n\t" or ch >= " " for ch in text), repr(text)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("IONOMOS_STANDIN", "IONOMOS_STANDIN_OUT", "IONOMOS_STANDIN_HELP", "IONOMOS_FAKE_FP_MODE",
                "IONOMOS_FAKE_CONVERT_MODE", "IONOMOS_FAKE_SAGE_OLD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.setattr(fragpipe, "POLL_SECONDS", 0.05)


# ------------------------------------------------------------------------ the run loop, engine by engine --


@dataclass
class StandinSpec(fragpipe.RunSpec):
    """A search whose engine is the stand-in itself (no launcher in between)."""

    outputs: tuple[str, ...] = ("report.pg_matrix.tsv",)

    @property
    def workdir(self) -> Path:
        return self.dest / "out"

    @property
    def console_log(self) -> Path:
        return self.run_dir / "standin_console.log"

    def command(self) -> list[str]:
        return [sys.executable, str(STANDIN)]

    def expected_outputs(self) -> tuple[str, ...]:
        return self.outputs


def _spec(tmp_path: Path, monkeypatch, steps: str, engine: str = "DIA-NN", **kw) -> StandinSpec:
    spec = StandinSpec(job_id=1, method="DIA", dest=tmp_path / "exp", exe=Path(sys.executable),
                       workflow_src=Path("standin"), fasta=None, manifest_lines=[], threads=0, ram_gb=0,
                       timeout_minutes=5, **kw)
    spec.engine_name = engine
    spec.run_dir.mkdir(parents=True)
    spec.workdir.mkdir()
    monkeypatch.setenv("IONOMOS_STANDIN", steps)
    monkeypatch.setenv("IONOMOS_STANDIN_OUT", str(spec.workdir))
    return spec


def _run(spec: StandinSpec, **kw) -> fragpipe.RunResult:
    res = fragpipe.run(spec, poll=0.05, **kw)
    _plain(res.reason)
    for h in res.hints:
        _plain(h)
    return res


# (stand-in steps, words in the reason, words of the first hint or None)
ODD_EXITS = [
    pytest.param("garbage,exit=3", "DIA-NN exited with code 3; last lines: 046 ", None, id="garbage-3"),
    pytest.param("binary,exit=1", "DIA-NN exited with code 1; last lines: ", None, id="binary-1"),
    pytest.param("say=bye,exit=-1", "exited with code -1;" if WINDOWS else "exited with code 255;", None, id="minus-1"),
    pytest.param("say=x,exit=-2", "exited with code -2;" if WINDOWS else "exited with code 254;", None, id="minus-2"),
    pytest.param("exit=2147483647", "exited with code 2147483647", None, id="int-max", marks=only_windows),
    pytest.param("exit=3221225477", "exited with code 0xC0000005 (crashed with an access violation)",
                 "crashed without a message", id="access-violation", marks=only_windows),
    pytest.param("exit=3221225781", "exited with code 0xC0000135 (could not start: a DLL it needs is missing)",
                 "Visual C++ Redistributable", id="dll-missing", marks=only_windows),
    pytest.param("exit=3221226528", "exited with code 0xC0000420 (crashed with Windows error 0xC0000420)",
                 "crashed without a message", id="unknown-ntstatus", marks=only_windows),
    pytest.param("exit=3221225786", "exited with code 0xC000013A (ended by Ctrl+C", "ended from outside",
                 id="ctrl-c", marks=only_windows),
    pytest.param("say=x,signal=9", "exited with code -9 (ended by SIGKILL)", "ended from outside", id="sigkill",
                 marks=only_posix),
    pytest.param("say=x,signal=11", "exited with code -11 (crashed with SIGSEGV)", "crashed without a message",
                 id="sigsegv", marks=only_posix),
]


@pytest.mark.parametrize(("steps", "reason", "hint"), ODD_EXITS)
def test_an_engine_that_exits_oddly_fails_with_a_readable_reason(tmp_path, monkeypatch, steps, reason, hint):
    spec = _spec(tmp_path, monkeypatch, steps)
    res = _run(spec)
    assert not res.ok and res.code not in (0, None)
    assert reason in res.reason, res.reason
    if hint:
        assert res.hints and hint in res.hints[0], res.hints
    assert not (spec.run_dir / names.ENGINE_PID_FILE).exists()


@only_windows
def test_a_fragpipe_step_that_crashed_is_named_with_what_its_code_means(tmp_path, monkeypatch):
    """Java prints a step's exit code signed (-1073741819) and FragPipe exits with it (0xC0000005)."""
    spec = _spec(tmp_path, monkeypatch, "say=MSFragger [Work dir: C:\\x]\\nProcess 'MSFragger' finished%2C exit "
                                        "code: -1073741819,exit=3221225477", engine="FragPipe")
    res = _run(spec)
    assert "FragPipe step MSFragger failed (exit code -1073741819, crashed with an access violation)" in res.reason
    assert res.hints[0].startswith("FragPipe or a tool it started crashed without a message")


# (stand-in steps that end with exit 0, words in the reason, words of the first hint or None)
EXIT0 = [
    pytest.param("partial=report.pg_matrix.tsv", "report.pg_matrix.tsv ends in the middle of a row",
                 "not written to the end", id="partial"),
    pytest.param("empty=report.pg_matrix.tsv", "is empty (0 bytes)", "not written to the end", id="zero-bytes"),
    pytest.param("binary,header=report.pg_matrix.tsv", "has only its header line", "found no identifications",
                 id="header-only"),
    pytest.param("garbage", "exited 0 but wrote nothing to the output folder", None, id="nothing"),
    pytest.param("table=temp/x.tsv", "DIA-NN exited 0 but did not write its result table report.pg_matrix.tsv",
                 "left no result table", id="no-main-table"),
]


@pytest.mark.parametrize(("steps", "reason", "hint"), EXIT0)
def test_exit_0_with_unusable_output_fails(tmp_path, monkeypatch, steps, reason, hint):
    res = _run(_spec(tmp_path, monkeypatch, steps))
    assert not res.ok and reason in res.reason, res.reason
    if hint:
        assert res.hints and hint in res.hints[0], res.hints


def test_binary_noise_around_a_whole_table_is_still_a_good_run(tmp_path, monkeypatch):
    res = _run(_spec(tmp_path, monkeypatch, "binary,garbage,table=report.pg_matrix.tsv"))
    assert res.ok and res.warnings == [], res.reason


@pytest.mark.parametrize("engine", ["FragPipe", "DIA-NN", "MaxQuant", "Sage"])
def test_a_hung_engine_and_what_it_started_are_ended_at_the_time_limit(tmp_path, monkeypatch, engine):
    spec = _spec(tmp_path, monkeypatch, "grandchild=120,marker,hang", engine=engine)
    started = spec.workdir / "standin.started"
    monkeypatch.setattr(fragpipe, "_clock", lambda: time.monotonic() + (1e7 if started.exists() else 0))
    try:
        res = _run(spec)
        assert res.timed_out and "timed out after 5 min (fragpipe.timeout_minutes)" in res.reason, res.reason
        assert "ran longer than the time limit" in res.hints[0]
        grandchild = int((spec.workdir / "grandchild.pid").read_text(encoding="utf-8"))
        assert _wait(lambda: not _alive(grandchild), 15), "what the engine started outlived the time limit"
        assert "# TIMEOUT after 5 min" in spec.console_log.read_text(encoding="utf-8", errors="replace")
        assert not (spec.run_dir / names.ENGINE_PID_FILE).exists()
    finally:
        _end(spec.workdir / "grandchild.pid")


def test_an_engine_flooding_its_log_is_stopped_before_it_fills_the_disk(tmp_path, monkeypatch):
    """`flood=0` prints for ever (a tool in a loop; the real limit is 2 GB, shrunk here to 3 MB)."""
    monkeypatch.setattr(fragpipe, "MAX_CONSOLE_BYTES", 3_000_000)
    spec = _spec(tmp_path, monkeypatch, "flood=0", engine="Sage")
    res = _run(spec)
    assert not res.ok and "Sage wrote more than 3 MB to its console log" in res.reason, res.reason
    assert "printed the same thing over and over" in res.hints[0]
    assert spec.console_log.stat().st_size > 3_000_000
    assert len(fragpipe.tail(spec.console_log, 4)) <= 600  # the reader still takes only the end


def test_an_engine_that_will_not_stop_is_named_and_stopped_later(tmp_path, monkeypatch):
    """A kill that does not take (taskkill refused, a process that ignores it): the reason says so and how to end
    it, and its engine_pid.json stays, so the next attempt or start of Ionomos stops it first (stop_leftover)."""
    spec = _spec(tmp_path, monkeypatch, "marker,hang")
    started = spec.workdir / "standin.started"
    monkeypatch.setattr(fragpipe, "_clock", lambda: time.monotonic() + (1e7 if started.exists() else 0))
    monkeypatch.setattr(fragpipe, "kill_tree", lambda proc: None)
    res = _run(spec)
    pid = int(started.read_text(encoding="utf-8"))  # the stand-in (a venv's python.exe may be a launcher above it)
    root = json.loads((spec.run_dir / names.ENGINE_PID_FILE).read_text(encoding="utf-8"))["pid"]
    try:
        assert res.timed_out and f"DIA-NN did not stop when told to (process {root})" in res.reason, res.reason
        assert res.hints[0].startswith("The search program did not stop when Ionomos ended it")
        assert _alive(pid)
        monkeypatch.undo()
        assert f"process {root}" in fragpipe.stop_leftover(spec.run_dir)
        assert _wait(lambda: not _alive(pid) and not _alive(root), 15)
    finally:
        for p in (pid, root):
            if _alive(p):
                fragpipe.kill_pid_tree(p)


def test_a_process_left_behind_by_an_engine_that_ended_does_not_hold_the_run(tmp_path, monkeypatch):
    """The engine exits 0 and leaves a process of its own running. Its console log is a file, not a pipe, so the
    run ends with the engine. POSIX: the leftover (the engine's process group) is stopped and noted. Windows: it
    can't be told from other programs once its parent is gone (issue #174); it is not waited for."""
    spec = _spec(tmp_path, monkeypatch, "grandchild=120,table=report.pg_matrix.tsv")
    try:
        res = _run(spec)
        assert res.ok and res.seconds < 60, res.reason
        grandchild = int((spec.workdir / "grandchild.pid").read_text(encoding="utf-8"))
        if not WINDOWS:
            assert len(res.warnings) == 1 and res.warnings[0].startswith(
                "a program DIA-NN started was still running after DIA-NN ended; Ionomos stopped it"), res.warnings
            assert _wait(lambda: not _alive(grandchild), 15)
            assert "# a program DIA-NN started" in spec.console_log.read_text(encoding="utf-8")
        else:
            assert res.warnings == []
    finally:
        _end(spec.workdir / "grandchild.pid")


@only_windows
def test_a_result_table_locked_by_a_leftover_process_is_named_not_called_cut_off(tmp_path, monkeypatch):
    spec = _spec(tmp_path, monkeypatch, "lockchild=report.pg_matrix.tsv")
    try:
        res = _run(spec)
        assert not res.ok and "report.pg_matrix.tsv can't be read (Permission denied)" in res.reason, res.reason
        assert "cut off" not in res.reason and "it is open in another program" in res.reason
        assert res.hints[0].startswith("A result table of the search is open in another program")
    finally:
        _end(spec.workdir / "grandchild.pid")


def test_explanations_stay_fast_on_a_pathological_log():
    """explain() runs every pattern over the end of a log (400 kB). Lines a tool may print in a loop must not make
    a pattern backtrack for minutes."""
    for text in ("error " * 70_000, "RawFileReader " * 30_000, "a" * 400_000, "x\t" * 200_000,
                 fragpipe.decode_console(bytes(range(256)) * 1600), ("msfragger " + "z" * 90 + "\n") * 4000,
                 "Process '" + "M" * 400_000):
        t = time.monotonic()
        fragpipe.explain(text)
        fragpipe.console_facts(text)
        assert time.monotonic() - t < 5, text[:40]


# ---------------------------------------------------------------- exit codes as a person reads them --


@pytest.mark.parametrize(("code", "shown"), [(None, "none"), (0, "0"), (3, "3"), (255, "255"), (-9, "-9"),
                                             (4294967295, "-1"), (4294967294, "-2"), (2147483648, "-2147483648"),
                                             (3221225477, "0xC0000005"), (3221225786, "0xC000013A")])
def test_exit_codes_are_shown_as_people_know_them(code, shown):
    assert fragpipe.code_text(code) == shown


@pytest.mark.parametrize(("code", "want"), [
    (0xC0000135, "could not start: a DLL it needs is missing"),
    (0xC000007B, "could not start: it or a DLL it needs is for another kind of Windows"),
    (0xC0000374, "crashed with a corrupted heap"),
    (0xC0000420, "crashed with Windows error 0xC0000420"),
    (0xFFFFFFFF, ""),  # a program's own exit(-1): its log says why
    (0xFFFFFFFE, ""),
    (0x80000003, ""),  # not an error status
])
def test_what_a_windows_exit_status_says(code, want):
    got = fragpipe.exit_code_reason(code, "")
    assert got == want if not want else got.startswith(want), got
    if want:
        assert fragpipe.explain(f"DIA-NN {got}")


# ------------------------------------------------------------- through the worker, as DIA-NN --


@pytest.fixture
def bed(tmp_path, monkeypatch):
    cfg = load(testbed.init(tmp_path / "bed"))
    exe = write_standin(tmp_path / "engine", "diann")
    m = cfg.methods["DIA"]
    dia = MethodConfig(key="DIA", workflow="", fasta=m.fasta, data_type="DIA", postprocess=m.postprocess,
                       aliases=m.aliases, extra={"engine": "diann", "diann_exe": str(exe)})
    cfg = replace(cfg, methods={**cfg.methods, "DIA": dia})
    return {"root": tmp_path / "bed", "cfg": cfg, "ledger": Ledger(cfg.database), "mp": monkeypatch, "exe": exe}


def _queue(bed, steps: str) -> Path:
    folder = testbed.drop(bed["root"], "dia_good")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    dest = Path(bed["ledger"].list()[-1].dest_dir)
    bed["mp"].setenv("IONOMOS_STANDIN", steps)
    bed["mp"].setenv("IONOMOS_STANDIN_OUT", str(dest / "diann"))
    return dest


def _raws(dest: Path) -> dict[str, int]:
    return {p.relative_to(dest).as_posix(): p.stat().st_size for p in dest.rglob("*.raw")}


def _ended_clearly(bed, dest: Path, status: str, *words: str) -> str:
    """The job is `status` with `words` in its reason; what the person reads says what to do, never a traceback."""
    job = bed["ledger"].get(1)
    assert job.status == status, job.reason
    for w in words:
        assert w in job.reason, job.reason
    _plain(job.reason)
    st = json.loads((dest / names.STATUS_FILE).read_text(encoding="utf-8"))
    assert st["status"] == status
    if status == "failed":
        note = (dest / "FAILED.txt").read_text(encoding="utf-8")
        _plain(note)
        assert "Retry" in note and words[0] in note
        item = next(i for i in attention.items(bed["cfg"].log_dir) if i.kind == "search_failed")
        assert item.fixes and "Retry" in " ".join(item.fixes)
    assert not (dest / "ionomos_run" / names.ENGINE_PID_FILE).exists()
    return job.reason


WORKER_FAILS = [
    pytest.param("garbage,exit=3", "DIA-NN exited with code 3", id="garbage"),
    pytest.param("binary,exit=1", "DIA-NN exited with code 1", id="binary"),
    pytest.param("say=x,exit=-1", "DIA-NN exited with code", id="negative"),
    pytest.param("partial=report.pg_matrix.tsv", "ends in the middle of a row", id="partial"),
    pytest.param("empty=report.pg_matrix.tsv", "is empty (0 bytes)", id="zero-bytes"),
    pytest.param("table=report.stats.tsv", "did not write its result table report.pg_matrix.tsv", id="no-matrix"),
]


@pytest.mark.parametrize(("steps", "words"), WORKER_FAILS)
def test_a_misbehaving_diann_fails_the_job_clearly_and_the_next_job_runs(bed, steps, words):
    dest = _queue(bed, steps)
    before = _raws(dest)
    assert Worker(bed["cfg"], bed["ledger"]).run_once()
    _ended_clearly(bed, dest, "failed", words)
    assert _raws(dest) == before


def test_a_hung_diann_is_failed_at_the_time_limit(bed):
    dest = _queue(bed, "marker,hang")
    started = dest / "diann" / "standin.started"
    bed["mp"].setattr(fragpipe, "_clock", lambda: time.monotonic() + (1e7 if started.exists() else 0))
    Worker(bed["cfg"], bed["ledger"]).run_once()
    _ended_clearly(bed, dest, "failed", "timed out after")
    assert not _alive(int(started.read_text(encoding="utf-8")))


@only_windows
def test_a_locked_table_fails_then_holds_the_retry_until_it_is_closed_and_keeps_it(bed):
    dest = _queue(bed, "lockchild=report.pg_matrix.tsv")
    w = Worker(bed["cfg"], bed["ledger"])
    try:
        w.run_once()
        _ended_clearly(bed, dest, "failed", "can't be read")
        bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
        assert not w.run_once()  # the earlier output can't be moved aside while a file in it is held
        job = bed["ledger"].get(1)
        assert job.status == "queued" and "can't be moved aside" in job.reason and job.attempts == 0, job.reason
        assert hold_topic(job.reason) == "search.hold-previous-output"
    finally:
        _end(dest / "diann" / "grandchild.pid")
    assert _wait(lambda: _try_rename(dest / "diann"), 15)
    bed["mp"].setenv("IONOMOS_STANDIN", "say=again,exit=4")
    w._held.clear()
    assert w.run_once()
    _ended_clearly(bed, dest, "failed", "DIA-NN exited with code 4")
    kept = list(dest.glob("diann_previous_*"))
    assert len(kept) == 1 and (kept[0] / "report.pg_matrix.tsv").read_bytes().startswith(b"Protein\tA_1")


def _try_rename(folder: Path) -> bool:
    try:
        folder.rename(folder.with_name(folder.name + "-probe"))
        folder.with_name(folder.name + "-probe").rename(folder)
        return True
    except OSError:
        return False


def test_a_write_error_for_diann_inputs_fails_with_its_cause_not_a_missing_workflow(bed):
    """DIA-NN has no source workflow (diann.cfg is written from the settings): a full disk while writing it used to
    hold the job for ever as 'workflow file for DIA missing: diann.cfg (it disappeared just before the search)'."""
    dest = _queue(bed, "exit=0")

    def full(spec):
        raise OSError(28, "No space left on device")

    bed["mp"].setattr(runner, "write_inputs", full)
    assert Worker(bed["cfg"], bed["ledger"]).run_once()
    reason = _ended_clearly(bed, dest, "failed", "could not prepare DIA-NN inputs", "No space left on device")
    assert "workflow file" not in reason
    assert "The disk is full" in (dest / "FAILED.txt").read_text(encoding="utf-8")


def test_diann_output_that_cannot_be_moved_aside_holds_with_that_reason(bed):
    dest = _queue(bed, "garbage,exit=3")
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    (dest / "diann" / "mine.txt").write_text("kept", encoding="utf-8")
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)

    def held(a, b):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    bed["mp"].setattr(fragpipe, "os", _Os(os, rename=held))
    assert not w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "can't be moved aside" in job.reason and "workflow file" not in job.reason
    assert (dest / "diann" / "mine.txt").read_text(encoding="utf-8") == "kept"


def test_a_retry_does_not_rewrite_the_tmt_annotation_it_wrote_before(tmp_path, monkeypatch):
    """A person opens a plex's annotation.txt in Excel (which locks it) to check the channels, then presses Retry:
    the retry reads the file the first attempt wrote as the plex's own (tmtkit.plan) and never writes it again."""
    cfg = load(testbed.init(tmp_path / "bed"))
    led = Ledger(cfg.database)
    folder = testbed.drop(tmp_path / "bed", "tmt_plexes")
    assert intake(folder, cfg, led).value == "queued"
    dest = Path(led.list()[-1].dest_dir)
    w = Worker(cfg, led)
    assert w.run_once() and led.get(1).status == "done", led.get(1).reason
    real = Path.write_text

    def excel_has_it(self, *a, **kw):
        if self.name == "annotation.txt":
            raise PermissionError(13, "The process cannot access the file because it is being used by another "
                                      "process", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", excel_has_it)
    led.requeue(1, "retry requested", reset_attempts=True)
    assert w.run_once()
    assert led.get(1).status == "done", led.get(1).reason
    assert (dest / "plexA" / "annotation.txt").is_file()


# ------------------------------------------------------------- retries keep every earlier attempt --


@pytest.mark.parametrize("engine", ["fragpipe", "diann", "maxquant", "sage"])
def test_attempts_in_one_second_keep_every_earlier_output(tmp_path, monkeypatch, engine):
    """Every engine's output folder is moved aside to <name>_previous_<time>, -2, -3 ... DIA-NN, MaxQuant and
    Sage named theirs to the second only: a second retry in that second failed (Windows refused the move) or, on
    POSIX, failed on the full folder of the first."""
    frozen = datetime(2026, 10, 15, 12, 0, 0)
    monkeypatch.setattr(fragpipe, "datetime", types.SimpleNamespace(now=lambda *a: frozen))
    work = tmp_path / engine
    kept = []
    for i in range(3):
        work.mkdir(exist_ok=True)
        (work / "out.tsv").write_text(f"attempt {i}", encoding="utf-8")
        kept.append(fragpipe.move_aside(work))
        assert not work.exists()
    assert kept == [f"{engine}_previous_20261015-120000", f"{engine}_previous_20261015-120000-2",
                    f"{engine}_previous_20261015-120000-3"]
    assert [(tmp_path / k / "out.tsv").read_text(encoding="utf-8") for k in kept] == [f"attempt {i}" for i in range(3)]
    work.mkdir()
    assert fragpipe.move_aside(work) is None and work.is_dir()  # an empty folder stays where it is


def test_every_runner_moves_its_output_aside_the_same_way():
    src = Path(fragpipe.__file__).parent
    for mod in ("diann.py", "maxquant.py", "sage.py", "fragpipe.py"):
        text = (src / mod).read_text(encoding="utf-8")
        assert "moved = move_aside(spec.workdir)" in text, mod
        assert mod == "fragpipe.py" or "_previous_" not in text, mod  # named in one place only


# ------------------------------------------------------------------------------- MaxQuant --


@pytest.fixture
def mq(tmp_path, monkeypatch):
    cfg = load(testbed.init(tmp_path / "bed"))
    exe = testbed.write_fake_maxquant(tmp_path / "mq")
    return {"cfg": cfg, "ledger": Ledger(cfg.database), "exe": exe, "mp": monkeypatch}


def _with_mq(cfg, exe, **extra):
    m = cfg.methods["isoDTB"]
    new = MethodConfig(key="isoDTB", workflow="", fasta=m.fasta, data_type="DDA", postprocess=(), aliases=m.aliases,
                       extra={"engine": "maxquant", "maxquant_exe": str(exe), **extra})
    return replace(cfg, methods={**cfg.methods, "isoDTB": new})


def _queue_mq(mq, cfg) -> Path:
    folder = make_drop(cfg.inbox, "20260930_EJQ_isoDTB_mq-faults", ["DMSO_1_1.raw", "Drug_1_1.raw"])
    assert intake(folder, cfg, mq["ledger"]).value == "queued"
    return Path(mq["ledger"].list()[-1].dest_dir)


@pytest.mark.parametrize(("content", "words"), [
    ("<MaxQuantParams><fastaFiles>", "is not a readable XML file"),
    ("\x00\x01binary", "is not a readable XML file"),
    ('<?xml version="1.0"?><fragpipe/>', "is not a MaxQuant parameter file (its root is <fragpipe>"),
])
def test_a_broken_lab_mqpar_holds_the_job_before_an_attempt(mq, content, words):
    lab = mq["cfg"].workflow_dir / "lab.xml"
    lab.write_text(content, encoding="utf-8")
    cfg = _with_mq(mq["cfg"], mq["exe"], mqpar="lab.xml")
    _queue_mq(mq, cfg)
    w = Worker(cfg, mq["ledger"])
    assert not w.run_once()
    job = mq["ledger"].get(1)
    assert job.status == "queued" and words in job.reason and job.attempts == 0, job.reason
    assert "can't be used" in job.reason and "File -> Save parameters" in job.reason
    assert hold_topic(job.reason) == "search.hold-mqpar"
    lab.write_text(testbed.FAKE_MQPAR, encoding="utf-8")  # saved again from the GUI: it runs
    assert w.run_once() and mq["ledger"].get(1).status == "done", mq["ledger"].get(1).reason


@pytest.mark.parametrize(("steps", "words"), [
    ("say=Unhandled exception. System.IO.FileNotFoundException,exit=7",
     "MaxQuant --create failed (exit code 7, no template written): Unhandled exception"),
    ("binary,exit=0", "MaxQuant --create failed (exit code 0, no template written)"),
    ("table=mqpar_default.xml,exit=0", "MaxQuant's parameter template"),
])
def test_a_maxquant_that_cannot_make_its_template_fails_the_job_with_what_to_do(mq, tmp_path, steps, words):
    exe = write_standin(tmp_path / "mqbad", "MaxQuantCmd")
    cfg = _with_mq(mq["cfg"], exe)
    dest = _queue_mq(mq, cfg)
    mq["mp"].setenv("IONOMOS_STANDIN", steps)
    mq["mp"].setenv("IONOMOS_STANDIN_OUT", str(dest / "ionomos_run"))
    assert Worker(cfg, mq["ledger"]).run_once()
    job = mq["ledger"].get(1)
    assert job.status == "failed" and words in job.reason, job.reason
    assert "Ionomos hit an unexpected error" not in job.reason
    assert "give the method a mqpar saved from the MaxQuant GUI" in job.reason
    _plain(job.reason)
    _plain((dest / "FAILED.txt").read_text(encoding="utf-8"))


def test_a_maxquant_create_that_hangs_with_a_child_holding_its_output_is_ended(mq, tmp_path):
    """`MaxQuantCmd --create` ran under subprocess.run(capture_output=True, timeout=300): at the limit it killed
    MaxQuant but then waited, without a limit, for a pipe its child still held — the worker hung for good."""
    exe = write_standin(tmp_path / "mqhang", "MaxQuantCmd")
    cfg = _with_mq(mq["cfg"], exe)
    dest = _queue_mq(mq, cfg)
    mq["mp"].setenv("IONOMOS_STANDIN", "grandchild=120,marker,hang")
    mq["mp"].setenv("IONOMOS_STANDIN_OUT", str(dest / "ionomos_run"))
    mq["mp"].setattr(maxquant, "CREATE_TIMEOUT", 2.0)
    try:
        t = time.monotonic()
        assert Worker(cfg, mq["ledger"]).run_once()
        assert time.monotonic() - t < 60
        job = mq["ledger"].get(1)
        assert job.status == "failed" and "MaxQuant --create did not end within 2 s and was stopped" in job.reason
        child = int((dest / "ionomos_run" / "grandchild.pid").read_text(encoding="utf-8"))
        assert _wait(lambda: not _alive(child), 15)
        assert (dest / "ionomos_run" / maxquant.CREATE_LOG).is_file()
    finally:
        _end(dest / "ionomos_run" / "grandchild.pid")


# ----------------------------------------------------------------------------------- Sage --


def _sage_job(tmp_path: Path, raws: list[Path]) -> Path:
    """A sage_job.json as sage.write_inputs writes it, with the stand-in as converter and as Sage."""
    mzml_dir = tmp_path / sage.MZML_DIR
    job = {"sage": [sys.executable, str(STANDIN)], "config": str(tmp_path / "sage.json"), "args": [], "threads": 2,
           "converter": [sys.executable, str(STANDIN)],
           "convert": [{"raw": str(r), "mzml": str(mzml_dir / (r.stem + ".mzML"))} for r in raws]}
    path = tmp_path / sage.JOB_NAME
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def _raw(tmp_path: Path, name: str, size: int = 64, mtime: float | None = None) -> Path:
    raw = tmp_path / name
    raw.write_bytes(b"r" * size)
    if mtime is not None:
        os.utime(raw, (mtime, mtime))
    return raw


def _convert_env(monkeypatch, tmp_path: Path, steps: str) -> None:
    monkeypatch.setenv("IONOMOS_STANDIN", steps)
    monkeypatch.setenv("IONOMOS_STANDIN_HELP", "say=--disable-telemetry-i-dont-want-to-improve-sage")  # sage --help
    monkeypatch.setenv("IONOMOS_STANDIN_OUT", str(tmp_path / sage.MZML_DIR / "converting"))


def test_a_half_written_conversion_from_a_killed_attempt_is_never_taken_for_a_finished_one(tmp_path, monkeypatch,
                                                                                            capfd):
    raw = _raw(tmp_path, "A_1.raw")
    job = _sage_job(tmp_path, [raw])
    stale = tmp_path / sage.MZML_DIR / "converting" / "A_1.mzML"
    stale.parent.mkdir(parents=True)
    stale.write_text("<mzML> half of it, the conversion was killed here", encoding="utf-8")
    _convert_env(monkeypatch, tmp_path, "say=converter: nothing written,exit=0")  # exit 0, no file
    assert sage.run_job([str(job)]) == 1
    out = capfd.readouterr().out
    assert "converting A_1.raw failed (ThermoRawFileParser exit code 0, no mzML written)" in out
    assert not (tmp_path / sage.MZML_DIR / "A_1.mzML").exists()


def test_a_retry_converts_nothing_twice_even_with_a_raw_stamped_in_the_future(tmp_path, monkeypatch, capfd):
    raw = _raw(tmp_path, "A_1.raw", mtime=time.time() + 86_400 * 30)  # the instrument PC's clock runs ahead
    job = _sage_job(tmp_path, [raw])
    _convert_env(monkeypatch, tmp_path, "table=A_1.mzML")
    assert sage.run_job([str(job)]) == 0
    assert sage.run_job([str(job)]) == 0
    out = capfd.readouterr().out
    assert out.count("converting A_1.raw -> A_1.mzML") == 1 and "A_1.mzML already converted" in out
    rec = json.loads((tmp_path / sage.MZML_DIR / ("A_1.mzML" + sage.SOURCE_SUFFIX)).read_text(encoding="utf-8"))
    assert rec == {"raw": "A_1.raw", "size": 64, "mtime_ns": raw.stat().st_mtime_ns}


def test_a_raw_file_replaced_since_its_conversion_is_converted_again(tmp_path, monkeypatch, capfd):
    """A truncated copy was converted; the person copies the file again, keeping its (older) acquisition time.
    The mzML is newer than the raw file, but not a conversion of it."""
    raw = _raw(tmp_path, "A_1.raw", size=10)
    job = _sage_job(tmp_path, [raw])
    _convert_env(monkeypatch, tmp_path, "table=A_1.mzML")
    assert sage.run_job([str(job)]) == 0
    _raw(tmp_path, "A_1.raw", size=640, mtime=time.time() - 86_400)
    assert sage.run_job([str(job)]) == 0
    assert capfd.readouterr().out.count("converting A_1.raw -> A_1.mzML") == 2


def test_an_mzml_without_a_record_is_reused_when_it_is_newer_than_the_raw_file(tmp_path, monkeypatch, capfd):
    raw = _raw(tmp_path, "A_1.raw", mtime=time.time() - 3600)
    job = _sage_job(tmp_path, [raw])
    (tmp_path / sage.MZML_DIR).mkdir()
    (tmp_path / sage.MZML_DIR / "A_1.mzML").write_text("converted by an Ionomos before the record", encoding="utf-8")
    _convert_env(monkeypatch, tmp_path, "say=searching")
    assert sage.run_job([str(job)]) == 0
    assert "A_1.mzML already converted" in capfd.readouterr().out


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"sage": []}', '{"sage": ["s"], "convert": [{}]}',
                                     '{"sage": ["s"], "convert": [{"raw": "a.raw", "mzml": "a.mzML"}]}'])
def test_a_sage_job_file_that_is_not_one_ends_with_a_message_not_a_traceback(tmp_path, capfd, content):
    path = tmp_path / sage.JOB_NAME
    path.write_text(content, encoding="utf-8")
    assert sage.run_job([str(path)]) == 2
    out = capfd.readouterr()
    assert "is not a Sage job Ionomos can run" in out.out and "Retry the job" in out.out
    assert "Traceback" not in out.out + out.err


# ------------------------------------------------------------------------ stopping processes --


class _Os:
    """The module's os with some names replaced (never os.name globally: CLAUDE.md)."""

    def __init__(self, real, **over):
        self._real, self._over = real, over

    def __getattr__(self, item):
        return self._over[item] if item in self._over else getattr(self._real, item)


class _Proc:
    """A Popen that never ends by itself; `kill()` ends it."""

    def __init__(self, pid=4242):
        self.pid, self.killed, self.returncode = pid, 0, None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("engine", timeout)
        return self.returncode

    def kill(self):
        self.killed += 1
        self.returncode = 1


POSIX_SIGNALS = types.SimpleNamespace(SIGTERM=15, SIGKILL=9, Signals=signal.Signals)


def test_windows_kill_tree_ends_the_process_itself_when_taskkill_cannot(monkeypatch):
    calls = []

    def no_taskkill(cmd, **kw):
        calls.append(cmd)
        raise FileNotFoundError(2, "The system cannot find the file specified", "taskkill")

    monkeypatch.setattr(fragpipe, "os", _Os(os, name="nt"))
    monkeypatch.setattr(fragpipe.subprocess, "run", no_taskkill)
    proc = _Proc()
    fragpipe.kill_tree(proc)
    assert calls == [["taskkill", "/PID", "4242", "/T", "/F"]] and proc.killed == 1 and proc.poll() == 1


def test_windows_kill_tree_uses_taskkill_on_the_whole_tree(monkeypatch):
    seen = []
    proc = _Proc()

    def taskkill(cmd, **kw):
        seen.append((cmd, kw.get("timeout")))
        proc.returncode = 1
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fragpipe, "os", _Os(os, name="nt"))
    monkeypatch.setattr(fragpipe.subprocess, "run", taskkill)
    fragpipe.kill_tree(proc)
    assert seen == [(["taskkill", "/PID", "4242", "/T", "/F"], fragpipe.TASKKILL_SECONDS)] and proc.killed == 0
    fragpipe.kill_tree(proc)  # already ended: nothing more
    assert len(seen) == 1


def test_posix_kill_tree_terms_then_kills_the_group_and_survives_a_vanished_group(monkeypatch):
    sent = []
    proc = _Proc(pid=777)

    def killpg(pid, sig):
        sent.append((pid, sig))
        if sig == 9:
            proc.returncode = -9

    monkeypatch.setattr(fragpipe, "os", _Os(os, name="posix", killpg=killpg))
    monkeypatch.setattr(fragpipe, "signal", POSIX_SIGNALS)
    fragpipe.kill_tree(proc)
    assert sent == [(777, 15), (777, 9)] and proc.killed == 0

    def gone(pid, sig):
        raise ProcessLookupError(3, "No such process")

    proc = _Proc(pid=778)
    monkeypatch.setattr(fragpipe, "os", _Os(os, name="posix", killpg=gone))
    fragpipe.kill_tree(proc)  # the group is gone but the process answers: ended directly
    assert proc.killed == 1


def test_kill_pid_tree_on_posix_signals_the_group_only_when_it_leads_it(monkeypatch):
    sent = []
    monkeypatch.setattr(fragpipe, "signal", POSIX_SIGNALS)
    monkeypatch.setattr(fragpipe, "_wait_gone", lambda pid, s: sent[-1][2] == 9)  # TERM ignored, KILL works
    fake = _Os(os, name="posix", getpgid=lambda pid: 500,
               killpg=lambda pid, sig: sent.append(("pg", pid, sig)), kill=lambda pid, sig: sent.append(("p", pid, sig)))
    monkeypatch.setattr(fragpipe, "os", fake)
    fragpipe.kill_pid_tree(500, group=True)
    fragpipe.kill_pid_tree(501, group=True)  # no longer the leader of its group: the process alone
    fragpipe.kill_pid_tree(502, group=False)
    assert sent == [("pg", 500, 15), ("pg", 500, 9), ("p", 501, 15), ("p", 501, 9), ("p", 502, 15), ("p", 502, 9)]
    sent.clear()
    for pid in (0, 1, -1):  # os.kill(-1) would signal every process of the user
        fragpipe.kill_pid_tree(pid, group=True)
    assert sent == []


def test_kill_pid_tree_on_windows_is_one_bounded_taskkill(monkeypatch):
    seen = []
    monkeypatch.setattr(fragpipe, "os", _Os(os, name="nt"))
    monkeypatch.setattr(fragpipe.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw["timeout"])))
    fragpipe.kill_pid_tree(4242, group=True)
    assert seen == [(["taskkill", "/PID", "4242", "/T", "/F"], fragpipe.TASKKILL_SECONDS)]


@pytest.mark.parametrize("content", [b"", b"null", b"[]", b"5", b'"x"', b"{}", b'{"pid": "abc"}',
                                     b'{"pid": -1, "started": 0}', b'{"pid": 4242, "started": "soon"}',
                                     b'{"pid": 4242, "started": [1]}', b'{"pid": [1, 2]}', b"\xff\xfe\x00{",
                                     b'{"pid": 99999999, "started": 1.0}'])
def test_stop_leftover_never_raises_on_a_damaged_record(tmp_path, monkeypatch, content):
    monkeypatch.setattr(health, "process_started", lambda pid: 100.0 if int(pid) == 4242 else None)
    killed = []
    monkeypatch.setattr(fragpipe, "kill_pid_tree", lambda pid, group=False: killed.append(pid))
    (tmp_path / names.ENGINE_PID_FILE).write_bytes(content)
    assert fragpipe.stop_leftover(tmp_path) == ""
    assert killed == [] and not (tmp_path / names.ENGINE_PID_FILE).exists()


def test_stop_leftover_with_a_record_it_cannot_read_leaves_it(tmp_path):
    (tmp_path / names.ENGINE_PID_FILE).mkdir()  # not a file
    assert fragpipe.stop_leftover(tmp_path) == "" and (tmp_path / names.ENGINE_PID_FILE).is_dir()
    assert fragpipe.stop_leftover(tmp_path / "missing") == ""


@pytest.mark.parametrize("windows", [True, False])
def test_stop_leftover_says_when_the_leftover_survives(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(health, "process_started", lambda pid: 100.5)  # ours, and it never ends
    calls = []
    monkeypatch.setattr(fragpipe.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    if windows:
        monkeypatch.setattr(fragpipe, "os", _Os(os, name="nt"))
    else:
        monkeypatch.setattr(fragpipe, "signal", POSIX_SIGNALS)
        monkeypatch.setattr(fragpipe, "_wait_gone", lambda pid, s: False)
        monkeypatch.setattr(fragpipe, "os", _Os(os, name="posix", getpgid=lambda pid: pid,
                                                killpg=lambda pid, sig: calls.append(("killpg", pid, sig))))
    (tmp_path / names.ENGINE_PID_FILE).write_text(json.dumps({"pid": 4242, "started": 100.0, "group": True,
                                                              "at": "2026-10-15T09:00:00+02:00"}), encoding="utf-8")
    msg = fragpipe.stop_leftover(tmp_path)
    assert "process 4242, started 2026-10-15T09:00:00+02:00" in msg and "it may still be running" in msg
    assert calls == ([["taskkill", "/PID", "4242", "/T", "/F"]] if windows else
                     [("killpg", 4242, 15), ("killpg", 4242, 9)])
    assert not (tmp_path / names.ENGINE_PID_FILE).exists()


# ------------------------------------------------------------------- preflight on a broken installation --


def _broken_install(tmp_path: Path, monkeypatch, help_steps: str, dry_steps: str = "say=dry") -> tuple:
    """A FragPipe folder with only bin/fragpipe.bat (the stand-in): no lib, no jre, no tools, no python."""
    root = tmp_path / "FragPipe-24.0"
    exe = write_standin(root / "bin", "fragpipe")
    cfg = load(testbed.init(tmp_path / "bed"))
    cfg = replace(cfg, fragpipe_exe=exe, config_tools_folder="", config_python="", config_diann="")
    monkeypatch.setenv("IONOMOS_STANDIN_HELP", help_steps)
    monkeypatch.setenv("IONOMOS_STANDIN", dry_steps)
    monkeypatch.setenv("IONOMOS_STANDIN_OUT", str(tmp_path / "standin_out"))
    return cfg, root


def _checks(cfg, **kw) -> dict:
    checks = preflight.run(cfg, **kw)
    for c in checks:
        _plain(c.detail)
        _plain(c.fix)
    return {c.key: c for c in checks}


FRAGPIPE_HELP = (r"FragPipe v24.0\nSystem OS: Windows 11\nJava Info: 17.0.10\n.NET Core Info: N/A\n"
                 r"Usage: --headless --workflow <string>")


@pytest.mark.parametrize(("help_steps", "words"), [
    ("binary,exit=3", "exit code 3, output: "),
    ("garbage,exit=0", "exit code 0, output: "),
    pytest.param("exit=3221225781", "Windows could not start a program of the search", marks=only_windows),
    pytest.param("say=x,exit=3221225477", "crashed without a message", marks=only_windows),
    ("say=ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH.,exit=1",
     "FragPipe's launcher found no Java"),
])
def test_preflight_on_a_launcher_that_does_not_answer_as_fragpipe(tmp_path, monkeypatch, help_steps, words):
    cfg, _root = _broken_install(tmp_path, monkeypatch, help_steps)
    by = _checks(cfg)
    assert by["starts"].status == "fail" and words in by["starts"].detail, by["starts"].detail
    assert "launcher_help.log" in by["starts"].fix and "Find FragPipe" in by["starts"].fix
    assert all(c.status == "info" and "launcher did not start" in c.detail for k, c in by.items() if k.endswith(":dry"))
    assert by["install:FragPipe"].status == "warn" and "lib/fragpipe*.jar not found" in by["install:FragPipe"].detail
    assert by["install:MSFragger"].status == "warn"


def test_preflight_on_a_launcher_that_hangs_ends_it_and_what_it_started(tmp_path, monkeypatch):
    cfg, _root = _broken_install(tmp_path, monkeypatch, "grandchild=120,hang")
    monkeypatch.setattr(preflight, "HELP_TIMEOUT", 2.0)
    out = tmp_path / "standin_out"
    try:
        t = time.monotonic()
        by = _checks(cfg, methods=["isoDTB"])
        assert time.monotonic() - t < 60
        assert by["starts"].status == "fail" and "no answer within 2 s" in by["starts"].detail
        child = int((out / "grandchild.pid").read_text(encoding="utf-8"))
        assert _wait(lambda: not _alive(child), 15)
    finally:
        _end(out / "grandchild.pid")


@pytest.mark.parametrize(("dry_steps", "status", "words"), [
    ("garbage,exit=0", "fail", "FragPipe refused (exit code 0)"),
    ("binary,exit=1", "fail", "FragPipe refused (exit code 1)"),
    ("marker,hang", "fail", "no end within 2 s; stopped"),
])
def test_preflight_dry_run_of_a_broken_fragpipe(tmp_path, monkeypatch, dry_steps, status, words):
    cfg, _root = _broken_install(tmp_path, monkeypatch, "say=" + FRAGPIPE_HELP + ",exit=1", dry_steps)
    monkeypatch.setattr(preflight, "DRY_RUN_TIMEOUT", 2.0)
    by = _checks(cfg, methods=["isoDTB"])
    assert by["starts"].status == "ok", by["starts"].detail
    dry = by["method:isoDTB:dry"]
    assert dry.status == status and words in dry.detail, dry.detail
    assert dry.fix and "Log: " in dry.detail
    text = preflight.text(list(by.values()))
    assert "to fix before a search can work" in text


def test_preflight_with_a_launcher_that_is_not_a_program(tmp_path, monkeypatch):
    root = tmp_path / "FragPipe-24.0"
    (root / "bin").mkdir(parents=True)
    exe = root / "bin" / "fragpipe-launcher.exe"
    exe.write_bytes(b"MZ but not really a program\x00\x01")
    cfg = replace(load(testbed.init(tmp_path / "bed")), fragpipe_exe=exe)
    by = _checks(cfg, methods=["isoDTB"])
    assert by["starts"].status == "fail" and "could not start" in by["starts"].detail, by["starts"].detail
    out = io.StringIO()
    assert preflight.main(cfg, methods=["isoDTB"], out=out) == 1
    assert "to fix before a search can work" in out.getvalue() and "Traceback" not in out.getvalue()


def test_install_report_survives_a_folder_it_may_not_list(tmp_path, monkeypatch):
    root = tmp_path / "FragPipe-24.0"
    exe = write_standin(root / "bin", "fragpipe")
    cfg = replace(load(testbed.init(tmp_path / "bed")), fragpipe_exe=exe)
    real = Path.iterdir

    def guarded(self):
        if self == tmp_path:  # the folder above the installation (C:\ on the PC)
            raise PermissionError(13, "Access is denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "iterdir", guarded)
    rows = fragpipe.install_report(cfg)
    assert rows[0] == (True, "launcher", str(exe)) and not any(label == "licence file" for _o, label, _d in rows)
