"""
Failsafes and health facts shared by the watcher process, the app and diagnostics.

    lock = InstanceLock(log_dir); lock.acquire()      # one watcher per config, even across users/sessions
    hb = Heartbeat(log_dir); hb.beat("watcher")       # "am I alive?" file the app/diagnose read
    supervise("watcher", w.run_forever, stop_event)   # restart a crashed loop with backoff
    install_excepthooks(log_dir)                       # uncaught errors in any thread -> log + crash file
    disk_free_gb(path), memory_gb(), log_problems(log_file)

Nothing here raises on the happy path's failure modes: a health helper that
can't answer returns None / "" rather than taking the watcher down.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger("ionomos.health")

LOCK_NAME = "ionomos.lock"  # names.LOCK_FILE; a LabWatch-era watcher holds labwatch.lock
HEARTBEAT_NAME = "heartbeat.json"
CRASH_PREFIX = "crash-"
STALE_AFTER = 90  # seconds without a heartbeat -> "not responding"


# ------------------------------------------------------------ single instance --


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    """An OS-level exclusive lock on <log_dir>/ionomos.lock, held while the watcher runs.

    Unlike a PID file it can't go stale: the OS drops the lock when the process
    dies, however it dies. Two watchers on one inbox would race to move the
    same folder and start FragPipe twice, so the second one refuses to start.
    """

    def __init__(self, log_dir: Path):
        self.path = Path(log_dir) / LOCK_NAME
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from ionomos.names import LEGACY_LOCK_FILES

        for old in LEGACY_LOCK_FILES:  # an old LabWatch watcher still running on this setup
            if _held(self.path.parent / old):
                raise AlreadyRunning("an old LabWatch watcher is still running for this setup. Stop it first "
                                     "(close LabWatch, or: taskkill /IM LabWatch.exe /F).")
        fh = open(self.path, "a+b")  # noqa: SIM115 - held open for the process lifetime
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            other = holder_pid(self.path.parent)
            raise AlreadyRunning(
                f"another ionomos watcher is already running for this setup"
                f"{f' (pid {other})' if other else ''}. Stop it first (app: Run & Test -> Stop watcher)."
            ) from None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def _held(path: Path) -> bool:
    """True if some process holds an exclusive lock on `path` (a probe that never blocks)."""
    if not path.is_file():
        return False
    try:
        fh = open(path, "a+b")  # noqa: SIM115
    except OSError:
        return True  # can't even open it: someone has it
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def is_locked(log_dir: Path) -> bool:
    """True if some process currently holds the watcher lock (Ionomos's, or a LabWatch-era one)."""
    from ionomos.names import lock_files

    return any(_held(p) for p in lock_files(log_dir))


def holder_pid(log_dir: Path) -> int | None:
    """PID of the running watcher: from its lock file, else its pid file (either name)."""
    from ionomos.names import lock_files, pid_files

    for p in [*lock_files(log_dir), *pid_files(log_dir)]:
        try:
            # On Windows the locked byte can't be read by others; the pid file is the fallback.
            txt = p.read_text(encoding="utf-8").strip()
            if txt:
                return int(txt)
        except (OSError, ValueError):
            continue
    return None


# ------------------------------------------------------------------ heartbeat --


class Heartbeat:
    """<log_dir>/heartbeat.json: {"pid", "started", "parts": {name: {"at": epoch, "state": str}}}.

    Parts beat independently (watcher, worker); a part that stops beating while
    the process is alive is hung, not dead — that's what this detects.
    """

    def __init__(self, log_dir: Path, min_interval: float = 2.0):
        self.path = Path(log_dir) / HEARTBEAT_NAME
        self.min_interval = min_interval
        self._parts: dict[str, dict] = {}
        self._last_write = 0.0
        self._lock = threading.Lock()
        self._started = time.time()

    def beat(self, part: str, state: str = "ok", force: bool = False) -> None:
        with self._lock:
            self._parts[part] = {"at": time.time(), "state": state}
            if not force and time.monotonic() - self._last_write < self.min_interval:
                return
            self._last_write = time.monotonic()
            data = {"pid": os.getpid(), "started": self._started, "parts": dict(self._parts)}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a heartbeat must never break the thing it reports on

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def read_heartbeat(log_dir: Path) -> dict | None:
    try:
        return json.loads((Path(log_dir) / HEARTBEAT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heartbeat_summary(log_dir: Path, now: float | None = None) -> tuple[str, bool]:
    """('watcher 2s ago, worker 1s ago (running job 3)', healthy?)."""
    hb = read_heartbeat(log_dir)
    if not hb:
        return "no heartbeat", False
    now = time.time() if now is None else now
    parts, healthy = [], True
    for name, p in sorted((hb.get("parts") or {}).items()):
        age = now - float(p.get("at", 0))
        state = p.get("state") or ""
        busy = state.startswith("busy:")  # announced long operation (resolver window open, big move)
        stale = age > STALE_AFTER and not busy
        healthy &= not stale
        parts.append(f"{name} {age:.0f}s ago" + (f" ({state})" if state and state != "ok" else "")
                     + (" — NOT RESPONDING" if stale else ""))
    return ", ".join(parts) or "no parts", healthy


# ----------------------------------------------------------------- supervisor --


def supervise(name: str, target: Callable[[], None], stop: threading.Event, max_backoff: float = 300,
              on_crash: Callable[[str, BaseException], None] | None = None) -> None:
    """Run target() until `stop` is set; if it raises (or returns early), log and restart with backoff.

    The watcher and worker loops already catch per-iteration errors; this is the
    last line of defence for anything that escapes them (a bug, MemoryError, a
    broken disk). Backoff doubles from 5 s to max_backoff and resets after an
    hour of healthy running.
    """
    backoff = 5.0
    while not stop.is_set():
        started = time.monotonic()
        try:
            target()
            if stop.is_set():
                return
            log.error("%s loop returned unexpectedly; restarting in %.0fs", name, backoff)
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            log.critical("%s crashed; restarting in %.0fs\n%s", name, backoff, traceback.format_exc())
            if on_crash:
                try:
                    on_crash(name, exc)
                except Exception:  # noqa: BLE001
                    pass
        if time.monotonic() - started > 3600:
            backoff = 5.0
        if stop.wait(backoff):
            return
        backoff = min(backoff * 2, max_backoff)


# -------------------------------------------------------------- crash handling --


def write_crash_file(log_dir: Path | None, where: str, exc_text: str) -> Path | None:
    """<log_dir>/crash-<ts>.txt with the traceback; keeps the newest 20."""
    if not log_dir:
        return None
    try:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        stem = f"{CRASH_PREFIX}{datetime.now():%Y%m%d-%H%M%S-%f}"
        p = d / f"{stem}.txt"
        n = 1
        while p.exists():  # Windows' clock ticks every ~15 ms: two crashes can share a timestamp
            p = d / f"{stem}-{n}.txt"
            n += 1
        from ionomos import __version__

        p.write_text(f"ionomos {__version__} crash in {where}\n{datetime.now().isoformat()}\n"
                     f"python {sys.version.split()[0]} {sys.platform}\n\n{exc_text}", encoding="utf-8")
        prune(d, f"{CRASH_PREFIX}*.txt", keep=20)
        return p
    except OSError:
        return None


def install_excepthooks(log_dir: Path | None) -> None:
    """Uncaught exceptions in the main thread or any thread -> log.critical + crash file."""

    def main_hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.critical("uncaught exception\n%s", text)
        write_crash_file(log_dir, "main thread", text)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        name = args.thread.name if args.thread else "?"
        log.critical("uncaught exception in thread %s\n%s", name, text)
        write_crash_file(log_dir, f"thread {name}", text)

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def recent_crashes(log_dir: Path, n: int = 3) -> list[Path]:
    d = Path(log_dir)
    if not d.is_dir():
        return []
    return sorted(d.glob(f"{CRASH_PREFIX}*.txt"))[-n:]


def prune(folder: Path, pattern: str, keep: int) -> None:
    """Delete all but the newest `keep` files matching pattern (ionomos's own files only)."""
    try:
        files = sorted(Path(folder).glob(pattern), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for p in files[:-keep] if keep else files:
        try:
            p.unlink()
        except OSError:
            pass


# -------------------------------------------------------------- system facts --


def disk_free_gb(path: Path) -> float | None:
    """Free space on the drive holding `path` (walks up to an existing parent)."""
    p = Path(path)
    for cand in (p, *p.parents):
        if cand.exists():
            try:
                return shutil.disk_usage(cand).free / 1e9
            except OSError:
                return None
    return None


def memory_gb() -> tuple[float | None, float | None]:
    """(total, available) RAM in GB; None where the OS won't say."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = MEMSTAT()
            m.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1e9, m.ullAvailPhys / 1e9
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        try:
            avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / 1e9
        except (ValueError, OSError):
            avail = None
        return total, avail
    except (ValueError, OSError, AttributeError):
        return None, None


def folder_size_gb(folder: Path, suffix: str = "") -> float:
    total = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if suffix and not f.lower().endswith(suffix):
                continue
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total / 1e9


# ---------------------------------------------------------------- log reading --

_LEVEL = re.compile(r"^\S+ \S+ (WARNING|ERROR|CRITICAL)\s")


def log_problems(log_file: Path, max_items: int = 25, scan_bytes: int = 2_000_000) -> list[str]:
    """The last WARNING/ERROR/CRITICAL entries (with their tracebacks) from ionomos.log."""
    try:
        with open(log_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - scan_bytes))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    items: list[str] = []
    cur: list[str] | None = None
    for ln in lines:
        if _LEVEL.match(ln):
            if cur:
                items.append("\n".join(cur))
            cur = [ln]
        elif cur is not None and (ln.startswith((" ", "\t", "Traceback")) or not re.match(r"^\d{4}-\d\d-\d\d", ln)):
            cur.append(ln)
        else:
            if cur:
                items.append("\n".join(cur))
            cur = None
    if cur:
        items.append("\n".join(cur))
    return items[-max_items:]


# ------------------------------------------------------------- detailed logging --

DEBUG_FILE = "DEBUG_UNTIL"


def set_debug(log_dir: Path, hours: float = 24) -> datetime:
    """Detailed (DEBUG) logging for the watcher for `hours`; picked up within a minute, no restart needed."""
    from datetime import timedelta

    until = (datetime.now() + timedelta(hours=hours)).replace(microsecond=0)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    (Path(log_dir) / DEBUG_FILE).write_text(until.isoformat(timespec="seconds"), encoding="utf-8")
    return until


def clear_debug(log_dir: Path) -> None:
    try:
        (Path(log_dir) / DEBUG_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def debug_until(log_dir: Path) -> datetime | None:
    """When detailed logging ends, or None if it's off (an expired switch is removed)."""
    p = Path(log_dir) / DEBUG_FILE
    try:
        until = datetime.fromisoformat(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if until <= datetime.now():
        clear_debug(log_dir)
        return None
    return until
