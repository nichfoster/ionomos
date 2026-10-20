"""
Windows Job Objects: a search engine and every process it starts are held together, so they end together
(issue #174, D126).

    job = winjob.contain(proc)       # right after Popen; None off Windows and when Windows won't (logged)
    job.terminate()                  # a time limit, a cancel, a stop: everything in the job, orphans too
    found, gone = job.stop_rest(5)   # the engine ended by itself: end what it left running
    job.close()                      # on every path; whatever still runs in the job ends with it

`taskkill /T` follows parent links, so a process whose parent (the engine) has already ended is out of its reach:
`taskkill /PID <engine> /T` can't stop it, and once the engine is gone nothing tells it from any other program.
A job holds every process started inside it whatever became of its parent, and nested jobs (Windows 8+) let an
engine's job sit inside one Ionomos itself runs in (a venv's python.exe launcher, Task Scheduler, a terminal).

The job is made with two limits:
- JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: when its last handle closes (`close()`, or Ionomos ended from Task Manager or
  crashing) every process in it ends, so a search never runs on unseen after the Ionomos that started it.
- JOB_OBJECT_LIMIT_BREAKAWAY_OK: a program that asks to leave (CREATE_BREAKAWAY_FROM_JOB) may, and is then not
  stopped (as before #174). Without it Windows would refuse to start that program and the search would fail.
  SILENT_BREAKAWAY_OK is not set: it would let every child out.

A process the engine starts in the few milliseconds between CreateProcess and AssignProcessToJobObject is not in
the job (subprocess can't start a process suspended). Every engine's first child comes long after that.

Stdlib ctypes only. The module's `os` and `ctypes` are what the tests replace (never os.name globally).
"""
from __future__ import annotations

import ctypes
import logging
import os
import time

log = logging.getLogger("ionomos.winjob")

ENDED_CODE = 1  # the exit code of a process the job ends: what taskkill /F leaves, so its exit reasons read the same
GRACE_SECONDS = 1.0  # how long a process of the job may take to end by itself before it counts as left behind

_ACCOUNTING = 1  # JobObjectBasicAccountingInformation
_PID_LIST = 3  # JobObjectBasicProcessIdList
_EXTENDED_LIMITS = 9  # JobObjectExtendedLimitInformation
_BREAKAWAY_OK = 0x0800  # JOB_OBJECT_LIMIT_BREAKAWAY_OK
_SILENT_BREAKAWAY_OK = 0x1000  # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK (never set; named for the tests)
_KILL_ON_JOB_CLOSE = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
LIMITS = _KILL_ON_JOB_CLOSE | _BREAKAWAY_OK
_SYNCHRONIZE = 0x00100000
_QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
_WAIT_OBJECT_0 = 0
_ERROR_MORE_DATA = 234


class _BasicLimits(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):  # IO_COUNTERS
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):  # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", ctypes.c_uint32), ("TotalProcesses", ctypes.c_uint32),
                ("ActiveProcesses", ctypes.c_uint32), ("TotalTerminatedProcesses", ctypes.c_uint32)]


def _pid_list(n: int) -> ctypes.Structure:
    """A JOBOBJECT_BASIC_PROCESS_ID_LIST with room for `n` ids."""

    class _Pids(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", ctypes.c_uint32), ("NumberOfProcessIdsInList", ctypes.c_uint32),
                    ("ProcessIdList", ctypes.c_size_t * n)]

    return _Pids()


_K32 = None


def _kernel32():
    """kernel32 with the prototypes used here (a handle is 64 bits: ctypes' default int would cut it). Its own
    WinDLL, so the prototypes reach no other module's kernel32. Cached."""
    global _K32
    if _K32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h, ok, dword, ptr = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p
        for name, res, args in (("CreateJobObjectW", h, (ptr, ctypes.c_wchar_p)),
                                ("SetInformationJobObject", ok, (h, ctypes.c_int, ptr, dword)),
                                ("AssignProcessToJobObject", ok, (h, h)),
                                ("TerminateJobObject", ok, (h, ctypes.c_uint)),
                                ("QueryInformationJobObject", ok, (h, ctypes.c_int, ptr, dword, ptr)),
                                ("IsProcessInJob", ok, (h, h, ptr)),
                                ("OpenProcess", h, (dword, ok, dword)),
                                ("WaitForSingleObject", dword, (h, dword)),
                                ("QueryFullProcessImageNameW", ok, (h, dword, ctypes.c_wchar_p, ptr)),
                                ("CloseHandle", ok, (h,))):
            fn = getattr(k, name)
            fn.restype, fn.argtypes = res, args
        _K32 = k
    return _K32


def _error() -> int:
    return ctypes.get_last_error()


class Job:
    """A Job Object holding one engine and what it starts. One per run, used by the thread that runs it."""

    def __init__(self, handle: int, what: str = "the program"):
        self.handle, self.what = handle, what

    def running(self) -> int:
        """How many processes in the job (and the jobs nested in it) are still running; -1 when Windows won't say."""
        if not self.handle:
            return -1
        info = _Accounting()
        if not _kernel32().QueryInformationJobObject(self.handle, _ACCOUNTING, ctypes.byref(info),
                                                     ctypes.sizeof(info), None):
            return -1
        return int(info.ActiveProcesses)

    def terminate(self) -> bool:
        """End every process in the job, whoever its parent was. False when Windows refused (logged)."""
        if not self.handle:
            return False
        if _kernel32().TerminateJobObject(self.handle, ENDED_CODE):
            return True
        log.warning("Windows would not end the job holding %s (Windows error %s)", self.what, _error())
        return False

    def stop_rest(self, seconds: float) -> tuple[bool, bool]:
        """After the engine ended by itself: end what it left running in the job. -> (a program was still running,
        everything has ended within `seconds`). Each program is waited for by its handle, not only counted: its
        files are closed (a lock on a result table released) once it is signalled. What ends by itself within
        GRACE_SECONDS was on its way out, not left behind: the engine's console host (conhost.exe) often outlives
        it by a few milliseconds, and a process caught while it exits has no name to read yet."""
        live = self._live()
        k = _kernel32()
        try:
            end = time.monotonic() + GRACE_SECONDS
            if live is None:  # the list can't be read: count instead
                while self.running() > 0 and time.monotonic() < end:
                    time.sleep(0.05)
                if self.running() <= 0:
                    return False, True
                live = []
            else:
                for h in live:
                    k.WaitForSingleObject(h, max(0, int((end - time.monotonic()) * 1000)))
                if all(k.WaitForSingleObject(h, 0) == _WAIT_OBJECT_0 for h in live):
                    return False, True
            self.terminate()
            end = time.monotonic() + seconds
            for h in live:
                k.WaitForSingleObject(h, max(0, int((end - time.monotonic()) * 1000)))
            while self.running() > 0 and time.monotonic() < end:
                time.sleep(0.05)
            gone = self.running() <= 0 and all(k.WaitForSingleObject(h, 0) == _WAIT_OBJECT_0 for h in live)
            return True, gone
        finally:
            for h in live or ():
                _kernel32().CloseHandle(h)

    def close(self) -> None:
        """Let go of the job; with KILL_ON_JOB_CLOSE whatever still runs in it ends now. Safe to call twice."""
        handle, self.handle = self.handle, None
        if handle:
            _kernel32().CloseHandle(handle)

    def _pids(self) -> list[int] | None:
        """The ids of the processes in the job (and its nested jobs); None when Windows won't list them."""
        n = 64
        for _ in range(4):
            buf = _pid_list(n)
            if _kernel32().QueryInformationJobObject(self.handle, _PID_LIST, ctypes.byref(buf), ctypes.sizeof(buf),
                                                     None):
                return [int(buf.ProcessIdList[i]) for i in range(min(int(buf.NumberOfProcessIdsInList), n))]
            if _error() != _ERROR_MORE_DATA:
                return None
            n = max(n * 4, int(buf.NumberOfAssignedProcesses) + 16)
        return None

    def _live(self) -> list[int] | None:
        """Handles (to wait on) of the programs in the job still running, console hosts left out; None when the
        job's list can't be read. A number Windows has given to another program since the list was read is not one
        of them (IsProcessInJob)."""
        if not self.handle:
            return []
        pids = self._pids()
        if pids is None:
            return None
        k = _kernel32()
        out = []
        for pid in pids:
            h = k.OpenProcess(_SYNCHRONIZE | _QUERY_LIMITED, 0, pid)
            if not h:
                continue
            inside = ctypes.c_int(0)
            if (k.IsProcessInJob(h, self.handle, ctypes.byref(inside)) and inside.value
                    and k.WaitForSingleObject(h, 0) != _WAIT_OBJECT_0 and not _console_host(h)):
                out.append(h)
            else:
                k.CloseHandle(h)
        return out


CONSOLE_HOSTS = ("conhost.exe", "openconsole.exe")


def _console_host(h: int) -> bool:
    """The process is the console host Windows started for a program of the job (never a program of its own)."""
    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_uint32(len(buf))
    if not _kernel32().QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
        return False  # a running program whose name can't be read is counted
    return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower() in CONSOLE_HOSTS


def contain(proc, what: str = "the program") -> Job | None:
    """Put a process Ionomos has just started into a new job, so that it and everything it starts can be ended
    together. None off Windows, and when Windows won't (a Windows before 8 with Ionomos already in a job, a job
    that forbids nesting): that is logged and the run goes on as before #174 (D126); it never fails a search."""
    if os.name != "nt":
        return None
    handle = None
    try:
        k = _kernel32()
        handle = k.CreateJobObjectW(None, None)
        if not handle:
            return _without("CreateJobObjectW", what, proc, _error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = LIMITS
        if not k.SetInformationJobObject(handle, _EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits)):
            return _without("SetInformationJobObject", what, proc, _error(), handle)
        if not k.AssignProcessToJobObject(handle, int(proc._handle)):
            return _without("AssignProcessToJobObject", what, proc, _error(), handle)
        return Job(handle, what)
    except (AttributeError, OSError, TypeError, ValueError) as exc:  # no kernel32, a Popen without its handle
        return _without("ctypes", what, proc, exc, handle)


def _without(step: str, what: str, proc, why, handle=None) -> None:
    if handle:
        try:
            _kernel32().CloseHandle(handle)
        except (AttributeError, OSError):
            pass
    try:
        ended = proc.poll() is not None
    except OSError:
        ended = False
    if ended:  # it ended before it could be put in a job (a program that can't start): nothing is left to hold
        log.debug("%s (process %s) ended before it could be put in a job", what, getattr(proc, "pid", "?"))
        return None
    log.warning("could not put %s (process %s) in a Windows job (%s: %s); a program it leaves running after it "
                "ends is not stopped", what, getattr(proc, "pid", "?"), step,
                f"Windows error {why}" if isinstance(why, int) else why)
    return None
