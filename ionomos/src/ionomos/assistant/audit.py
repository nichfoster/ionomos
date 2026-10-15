"""
The assistant's audit log: one JSON line per question, appended to <app-data>/assistant-audit.jsonl.

    append(record, path=None)        # never raises; path defaults to default_path()
    read(path=None)                  # the records, oldest first (a damaged line is skipped)

A record says what was asked, which model answered (and the digest of the prompt it was given), every tool
call with a hash of its arguments, which citations were accepted or refused, the proposal made (D75) and
what was shown; a decision on a proposal is a record of its own (actions.py). The file is only ever appended
to; Ionomos never rewrites or trims it. Its name lives in names.py.

Several askers write to it at once: the app's "Ask about this" windows (worker threads), `ionomos ask` and
`ionomos ask-eval` in other processes. Each record is one write of one whole line, made with O_APPEND while
holding a lock: a thread lock, and an OS lock on one byte far past the end of the file (Windows' locks are
mandatory, so readers never meet it). Without the lock, Windows' emulated append lost about one record in ten
when twelve writers appended at once (round 4). A line cut short by a crash is closed before the next record,
so it costs that one record only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from ionomos import names

log = logging.getLogger("ionomos.assistant")

_THREADS = threading.Lock()   # the threads of this process
_LOCK_AT = 1 << 40            # the byte locked between processes: never part of the data
LOCK_WAIT_S = 10.0            # then the record is written anyway (losing it would be worse), and that is logged


def default_path() -> Path:
    from ionomos.service import appdata_dir

    return appdata_dir() / names.ASSISTANT_AUDIT_FILE


def digest(value) -> str:
    """sha256 of a string, bytes, or the canonical JSON of anything else."""
    if isinstance(value, str):
        value = value.encode("utf-8")
    elif not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _lock(fd: int) -> bool:
    """Hold the other processes off this file. False after LOCK_WAIT_S (a writer that hangs)."""
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, _LOCK_AT, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, _LOCK_AT, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:  # closing the file releases it anyway
        pass


def _write_line(p: Path, data: bytes) -> None:
    fd = os.open(p, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        locked = _lock(fd)
        if not locked:
            log.warning("the assistant's audit log %s stayed locked for %.0f s; writing anyway", p, LOCK_WAIT_S)
        try:
            size = os.fstat(fd).st_size
            if size:  # a line cut short (a crash mid-write) is closed first, so only that record is lost
                os.lseek(fd, size - 1, os.SEEK_SET)
                if os.read(fd, 1) != b"\n":
                    data = b"\n" + data
            while data:  # O_APPEND: every write goes to the end, wherever the position was
                data = data[os.write(fd, data):]
        finally:
            if locked:
                _unlock(fd)
    finally:
        os.close(fd)


def append(record: dict, path: Path | str | None = None) -> Path | None:
    try:
        p = Path(path) if path is not None else default_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **record},
                          ensure_ascii=False, default=str)
        with _THREADS:
            _write_line(p, (line + "\n").encode("utf-8"))
        return p
    except OSError as exc:
        log.warning("could not write the assistant's audit log: %s", exc)
        return None


def read(path: Path | str | None = None) -> list[dict]:
    try:
        text = (Path(path) if path is not None else default_path()).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.split("\n"):  # not splitlines(): JSON leaves a U+2028 or U+0085 in a question as it is
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out
