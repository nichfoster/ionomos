"""winjob: an engine and what it starts held in a Windows Job Object (issue #174, D126).

Most tests replace the module's `ctypes` (its WinDLL and get_last_error) and `os` with stand-ins over a table of
processes, so they run on every OS and never touch os.name globally (CLAUDE.md). The last ones use the real
kernel32 on Windows: inside a job of this test run's own (a venv's python.exe launcher makes one), so nesting is
what they exercise."""
import ctypes
import logging
import os
import subprocess
import sys
import time
import types

import pytest

from ionomos import health, winjob

WINDOWS = os.name == "nt"
only_windows = pytest.mark.skipif(not WINDOWS, reason="a real Windows Job Object")
WAIT_TIMEOUT = 0x102


class _Os:
    """The module's os with some names replaced (never os.name globally)."""

    def __init__(self, real, **over):
        self._real, self._over = real, over

    def __getattr__(self, item):
        return self._over[item] if item in self._over else getattr(self._real, item)


class _Fn:
    """A kernel32 function: callable, and argtypes / restype can be set on it as on a real one."""

    def __init__(self, impl):
        self.impl, self.argtypes, self.restype = impl, None, None

    def __call__(self, *args):
        return self.impl(*args)


class FakeKernel32:
    """kernel32 as winjob uses it, over `procs`: pid -> {running, in_job, name, stubborn, openable}."""

    def __init__(self):
        self.calls, self.error, self.fail = [], 0, {}
        self.procs: dict[int, dict] = {}
        self.handles: dict[int, object] = {}  # handle -> "job" or a pid
        self.closed: list[int] = []
        self.limits = self.assigned = self.exit_code = None
        self._next = 100

    def add(self, pid, running=True, in_job=True, name=r"C:\x\java.exe", stubborn=False, openable=True):
        self.procs[pid] = {"running": running, "in_job": in_job, "name": name, "stubborn": stubborn,
                           "openable": openable}

    def open_handles(self) -> set:
        return set(self.handles)

    def __getattr__(self, name):
        impl = getattr(type(self), "_" + name, None)
        if impl is None:
            raise AttributeError(name)
        fn = _Fn(lambda *a: self._call(name, impl, *a))
        self.__dict__[name] = fn  # one object per name, so argtypes set once stay set
        return fn

    def _call(self, name, impl, *args):
        self.calls.append(name)
        if name in self.fail:
            self.error = self.fail[name]
            return None if name in ("CreateJobObjectW", "OpenProcess") else 0
        return impl(self, *args)

    def _new(self, what) -> int:
        self._next += 4
        self.handles[self._next] = what
        return self._next

    def _CreateJobObjectW(self, attrs, name):
        assert attrs is None and name is None  # unnamed, not inheritable
        return self._new("job")

    def _SetInformationJobObject(self, job, cls, ref, size):
        assert self.handles[job] == "job" and cls == winjob._EXTENDED_LIMITS
        assert size == ctypes.sizeof(winjob._ExtendedLimits)
        self.limits = ref._obj.BasicLimitInformation.LimitFlags
        return 1

    def _AssignProcessToJobObject(self, job, process):
        assert self.handles[job] == "job"
        self.assigned = process
        return 1

    def _TerminateJobObject(self, job, code):
        assert self.handles[job] == "job"
        self.exit_code = code
        for p in self.procs.values():
            if p["in_job"] and not p["stubborn"]:
                p["running"] = False
        return 1

    def _QueryInformationJobObject(self, job, cls, ref, size, returned):
        assert self.handles[job] == "job" and returned is None
        buf = ref._obj
        assert size == ctypes.sizeof(buf)
        inside = [pid for pid, p in self.procs.items() if p["in_job"]]
        if cls == winjob._ACCOUNTING:
            buf.ActiveProcesses = sum(1 for pid in inside if self.procs[pid]["running"])
            return 1
        assert cls == winjob._PID_LIST
        room = len(buf.ProcessIdList)
        buf.NumberOfAssignedProcesses = len(inside)
        if len(inside) > room:
            self.error = winjob._ERROR_MORE_DATA
            return 0
        buf.NumberOfProcessIdsInList = len(inside)
        for i, pid in enumerate(inside):
            buf.ProcessIdList[i] = pid
        return 1

    def _OpenProcess(self, access, inherit, pid):
        assert access == winjob._SYNCHRONIZE | winjob._QUERY_LIMITED and not inherit
        p = self.procs.get(pid)
        if p is None or not p["openable"]:
            self.error = 5
            return None
        return self._new(pid)

    def _IsProcessInJob(self, process, job, ref):
        ref._obj.value = int(self.procs[self.handles[process]]["in_job"])
        return 1

    def _WaitForSingleObject(self, h, ms):
        return 0 if not self.procs[self.handles[h]]["running"] else WAIT_TIMEOUT

    def _QueryFullProcessImageNameW(self, h, flags, buf, size):
        buf.value = self.procs[self.handles[h]]["name"]
        return 1

    def _CloseHandle(self, h):
        assert h in self.handles, f"handle {h} closed twice or never opened"
        del self.handles[h]
        self.closed.append(h)
        return 1


class _Ctypes:
    """The module's ctypes with WinDLL and get_last_error replaced; structures and byref are the real ones."""

    def __init__(self, real, k32, dll_error=None):
        self._real, self._k32, self._dll_error = real, k32, dll_error

    def WinDLL(self, name, use_last_error=False):  # noqa: N802 - ctypes' own name
        assert name == "kernel32" and use_last_error
        if self._dll_error:
            raise self._dll_error
        return self._k32

    def get_last_error(self):
        return self._k32.error

    def __getattr__(self, item):
        return getattr(self._real, item)


@pytest.fixture
def k32(monkeypatch):
    fake = FakeKernel32()
    monkeypatch.setattr(winjob, "ctypes", _Ctypes(ctypes, fake))
    monkeypatch.setattr(winjob, "os", _Os(os, name="nt"))
    monkeypatch.setattr(winjob, "_K32", None)
    return fake


def _proc(pid=4242, handle=77, ended=False):
    return types.SimpleNamespace(pid=pid, _handle=handle, poll=lambda: 0 if ended else None)


# ------------------------------------------------------------------------------------------- contain --


def test_contain_puts_the_engine_in_a_job_that_ends_with_its_last_handle(k32):
    job = winjob.contain(_proc(), "DIA-NN")
    assert job is not None and job.handle in k32.open_handles() and job.what == "DIA-NN"
    assert k32.calls == ["CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"]
    assert k32.limits == winjob._KILL_ON_JOB_CLOSE | winjob._BREAKAWAY_OK  # a program may ask to leave ...
    assert not k32.limits & winjob._SILENT_BREAKAWAY_OK  # ... but none leaves without asking
    assert k32.assigned == 77  # the handle Popen holds, not the pid
    assert k32.CreateJobObjectW.restype is ctypes.c_void_p  # a 64-bit handle is never cut to a C int
    assert k32.AssignProcessToJobObject.argtypes == (ctypes.c_void_p, ctypes.c_void_p)
    job.close()
    job.close()  # every path lets go of it; twice is harmless
    assert k32.open_handles() == set() and k32.calls.count("CloseHandle") == 1


def test_off_windows_contain_touches_nothing(monkeypatch):
    class NoCtypes:
        def __getattr__(self, item):
            raise AssertionError(f"ctypes.{item} used off Windows")

    monkeypatch.setattr(winjob, "ctypes", NoCtypes())
    monkeypatch.setattr(winjob, "os", _Os(os, name="posix"))
    assert winjob.contain(_proc(), "DIA-NN") is None


@pytest.mark.parametrize(("step", "error"), [
    ("CreateJobObjectW", 8),  # out of memory
    ("SetInformationJobObject", 87),
    ("AssignProcessToJobObject", 5),  # Ionomos in a job that won't take a nested one (Windows 7, UI limits)
])
def test_a_job_windows_will_not_make_falls_back_and_is_logged(k32, caplog, step, error):
    k32.fail[step] = error
    with caplog.at_level(logging.WARNING, logger="ionomos.winjob"):
        assert winjob.contain(_proc(), "DIA-NN") is None
    assert k32.open_handles() == set()  # a job made before the step that failed is closed
    assert [r.getMessage() for r in caplog.records] == [
        f"could not put DIA-NN (process 4242) in a Windows job ({step}: Windows error {error}); a program it "
        "leaves running after it ends is not stopped"]


def test_no_kernel32_or_no_process_handle_falls_back_and_is_logged(k32, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="ionomos.winjob"):
        assert winjob.contain(types.SimpleNamespace(pid=9, poll=lambda: None), "Sage") is None  # no _handle
        assert k32.open_handles() == set()
        monkeypatch.setattr(winjob, "_K32", None)
        monkeypatch.setattr(winjob, "ctypes", _Ctypes(ctypes, k32, dll_error=OSError("kernel32 not found")))
        assert winjob.contain(_proc(), "Sage") is None
    said = [r.getMessage() for r in caplog.records]
    assert len(said) == 2 and all("in a Windows job (ctypes: " in s for s in said), said


def test_an_engine_that_ended_before_it_was_put_in_a_job_is_not_a_warning(k32, caplog):
    k32.fail["AssignProcessToJobObject"] = 5
    with caplog.at_level(logging.WARNING, logger="ionomos.winjob"):
        assert winjob.contain(_proc(ended=True), "DIA-NN") is None
    assert caplog.records == [] and k32.open_handles() == set()


# ---------------------------------------------------------------------------------- what the job does --


def _job(k32) -> winjob.Job:
    job = winjob.contain(_proc(), "DIA-NN")
    k32.calls.clear()
    return job


def test_terminate_ends_everything_in_the_job_with_the_exit_code_taskkill_leaves(k32, caplog):
    job = _job(k32)
    k32.add(10)
    k32.add(11, in_job=False)  # another program: not the job's
    assert job.terminate()
    assert k32.exit_code == winjob.ENDED_CODE == 1
    assert not k32.procs[10]["running"] and k32.procs[11]["running"]
    k32.fail["TerminateJobObject"] = 5
    with caplog.at_level(logging.WARNING, logger="ionomos.winjob"):
        assert not job.terminate()
    assert "Windows would not end the job holding DIA-NN (Windows error 5)" in caplog.text
    job.close()
    assert not job.terminate() and job.running() == -1 and job.stop_rest(1) == (False, True)  # after close


def test_stop_rest_with_only_the_console_host_left_finds_nothing(k32):
    """conhost.exe outlives the engine by a few milliseconds (seen in 15 runs of 20 on Windows 11)."""
    job = _job(k32)
    k32.add(10, running=False, name=r"C:\Program Files\DIA-NN\diann.exe")  # the engine, ended
    k32.add(11, name=r"C:\Windows\System32\conhost.exe")
    k32.add(12, name=r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal\OpenConsole.exe")
    assert job.stop_rest(5) == (False, True)
    assert "TerminateJobObject" not in k32.calls
    assert k32.open_handles() == {job.handle}  # every process handle it opened is closed


def test_stop_rest_ends_what_the_engine_left_and_waits_for_each(k32):
    job = _job(k32)
    k32.add(10, running=False)
    k32.add(11, name=r"C:\fp\tools\BatmassIoThermoServer.exe")
    k32.add(12, name=r"C:\Windows\System32\conhost.exe")
    k32.add(13, in_job=False)  # the number of a process of the list that ended and was given to another program
    assert job.stop_rest(5) == (True, True)
    assert k32.calls.count("TerminateJobObject") == 1 and not k32.procs[11]["running"]
    assert k32.procs[13]["running"]
    assert k32.open_handles() == {job.handle}


def test_stop_rest_says_when_something_will_not_end(k32):
    job = _job(k32)
    k32.add(11, stubborn=True)  # a process stuck in a network drive's I/O
    t = time.monotonic()
    assert job.stop_rest(0.3) == (True, False)
    assert time.monotonic() - t < 5
    assert k32.open_handles() == {job.handle}


def test_stop_rest_reads_a_long_list_of_processes(k32):
    job = _job(k32)
    for pid in range(1000, 1100):  # more than the first buffer holds: ERROR_MORE_DATA, then a bigger one
        k32.add(pid, running=False)
    k32.add(2000)
    assert job.stop_rest(5) == (True, True)
    assert k32.calls.count("QueryInformationJobObject") >= 2 and not k32.procs[2000]["running"]


def test_stop_rest_counts_when_the_list_cannot_be_read(k32, monkeypatch):
    monkeypatch.setattr(winjob, "GRACE_SECONDS", 0.2)
    job = _job(k32)
    real = k32.__dict__.get("QueryInformationJobObject") or k32.QueryInformationJobObject

    def no_list(h, cls, ref, size, returned):
        if cls == winjob._PID_LIST:
            k32.error = 5
            return 0
        return real(h, cls, ref, size, returned)

    k32.__dict__["QueryInformationJobObject"] = _Fn(no_list)
    k32.add(11, running=False)
    assert job.stop_rest(5) == (False, True)  # the count is 0
    k32.add(12)
    assert job.stop_rest(5) == (True, True) and not k32.procs[12]["running"]


def test_stop_rest_skips_a_process_it_may_not_open(k32):
    job = _job(k32)
    k32.add(11, running=False)
    k32.add(12, running=False, openable=False)
    assert job.stop_rest(5) == (False, True)
    assert k32.open_handles() == {job.handle}


# ------------------------------------------------------------------------------------ the real kernel32 --


SPAWNER = r"""
import subprocess, sys
flags = int(sys.argv[2])
try:
    c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(sys.argv[1], "w").write(str(c.pid))
except OSError as e:
    open(sys.argv[1], "w").write("refused: " + str(e))
"""
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def _alive(pid: int) -> bool:
    return health.process_started(pid) is not None


def _end(pid: int) -> None:
    if _alive(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60)


def _engine(tmp_path, flags: int = 0) -> tuple[subprocess.Popen, winjob.Job, int]:
    """A real 'engine' that starts a sleeper and exits, in a real job; -> (engine, job, the sleeper's pid)."""
    out = tmp_path / f"child-{flags}.pid"
    proc = subprocess.Popen([sys.executable, "-c", SPAWNER, str(out), str(flags)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    job = winjob.contain(proc, "the stand-in")
    assert job is not None
    proc.wait(timeout=60)
    text = out.read_text(encoding="utf-8")
    assert not text.startswith("refused"), text
    return proc, job, int(text)


@only_windows
def test_a_real_job_ends_what_the_engine_left_after_it_ended(tmp_path):
    _proc, job, child = _engine(tmp_path)
    try:
        assert _alive(child)
        assert job.stop_rest(5) == (True, True)
        assert not _alive(child)
    finally:
        job.close()
        _end(child)


@only_windows
def test_a_real_job_ends_what_still_runs_in_it_when_it_is_closed(tmp_path):
    """KILL_ON_JOB_CLOSE: so a search ends with the Ionomos that held its job, however that Ionomos ended."""
    _proc, job, child = _engine(tmp_path)
    try:
        job.close()
        deadline = time.monotonic() + 15
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(child)
    finally:
        _end(child)


@only_windows
def test_a_real_engine_that_leaves_nothing_is_never_said_to_have(tmp_path):
    for _ in range(10):  # its console host often outlives it by a few milliseconds
        proc = subprocess.Popen([sys.executable, "-c", "print(1)"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        job = winjob.contain(proc, "python")
        try:
            assert job is not None
            proc.wait(timeout=60)
            assert job.stop_rest(5) == (False, True)
        finally:
            job.close()


@only_windows
def test_a_real_program_that_asks_to_leave_the_job_may(tmp_path):
    """BREAKAWAY_OK: a program started with CREATE_BREAKAWAY_FROM_JOB starts (without it Windows refuses, and the
    search would fail); it is then not stopped, as before #174."""
    probe = tmp_path / "probe.pid"
    subprocess.run([sys.executable, "-c", SPAWNER, str(probe), str(BREAKAWAY)], timeout=60, check=True)
    text = probe.read_text(encoding="utf-8")
    if text.startswith("refused"):
        pytest.skip(f"this test run's own job lets nothing break away: {text}")
    _end(int(text))
    _proc, job, child = _engine(tmp_path, BREAKAWAY)
    try:
        job.stop_rest(5)
        assert _alive(child)
    finally:
        job.close()
        _end(child)
