"""
Inbox watcher — detects new experiment FOLDERS and waits for the copy to finish.

    w = Watcher(inbox, on_stable=intake_fn, poll_seconds=10, stable_seconds=60, min_raw_files=1)
    w.run_forever()        # blocking; Ctrl-C to stop
    w.scan_once()          # one pass (tests, dry-run)

Why a tree fingerprint rather than "on created":
    A drag-and-drop of 20 GB from a USB drive lands file by file over minutes.
    Each poll we fingerprint the whole tree — sorted (relative path, size,
    mtime_ns) — and only when it has been unchanged for `stable_seconds` AND
    at least `min_raw_files` *.raw are present do we call on_stable(path).

What is ignored: loose files at the inbox top level, hidden/temp names
(".", "~$"), and our own *.REJECTED.txt files. Folders already handed off are
remembered in-process so we don't hand them off twice in one run; the ledger
(via the intake callback) is what prevents re-intake across restarts.

on_stable(path) -> bool: return True when the folder was consumed (moved or
rejected) so the watcher forgets it; False to keep watching (e.g. transient
error) — it will be re-offered once its fingerprint changes or on the next
stable window.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from labwatch.naming import RAW_SUFFIX

log = logging.getLogger("labwatch.watcher")

Fingerprint = tuple[tuple[str, int, int], ...]


def fingerprint(folder: Path) -> Fingerprint:
    entries = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            p = Path(root) / f
            try:
                st = p.stat()
            except OSError:
                # vanished or locked mid-copy: treat as "changing" by recording -1
                entries.append((str(p.relative_to(folder)), -1, -1))
                continue
            entries.append((str(p.relative_to(folder)), st.st_size, st.st_mtime_ns))
    return tuple(sorted(entries))


def count_raws(fp: Fingerprint) -> int:
    return sum(1 for rel, _s, _m in fp if rel.lower().endswith(RAW_SUFFIX))


def _ignored(name: str) -> bool:
    return name.startswith((".", "~$")) or name.endswith(".REJECTED.txt")


@dataclass
class _Pending:
    fp: Fingerprint
    stable_since: float  # monotonic
    announced: bool = False  # logged "detected" once
    offered: bool = False  # on_stable returned False; wait for a change before re-offering


class Watcher:
    def __init__(
        self,
        inbox: Path,
        on_stable: Callable[[Path], bool],
        poll_seconds: float = 10,
        stable_seconds: float = 60,
        min_raw_files: int = 1,
    ):
        self.inbox = Path(inbox)
        self.on_stable = on_stable
        self.poll_seconds = poll_seconds
        self.stable_seconds = stable_seconds
        self.min_raw_files = min_raw_files
        self._pending: dict[Path, _Pending] = {}
        self._done: set[Path] = set()
        self._running = False

    # ------------------------------------------------------------------ poll --

    def scan_once(self, now: float | None = None) -> list[Path]:
        """One pass. Returns the folders handed to on_stable this pass."""
        now = time.monotonic() if now is None else now
        handed: list[Path] = []
        if not self.inbox.is_dir():
            log.warning("inbox does not exist: %s", self.inbox)
            return handed

        present: set[Path] = set()
        for entry in sorted(self.inbox.iterdir()):
            if _ignored(entry.name) or not entry.is_dir():
                continue
            present.add(entry)
            if entry in self._done:
                continue
            if self._evaluate(entry, now):
                handed.append(entry)

        # forget folders that disappeared (user pulled it back out, or we moved it)
        for gone in [p for p in self._pending if p not in present]:
            del self._pending[gone]
        self._done &= present
        return handed

    def _evaluate(self, folder: Path, now: float) -> bool:
        fp = fingerprint(folder)
        pend = self._pending.get(folder)
        if pend is None:
            self._pending[folder] = _Pending(fp=fp, stable_since=now)
            log.info("detected: %s (%d files, %d raw) — waiting for copy to settle",
                     folder.name, len(fp), count_raws(fp))
            return False
        if fp != pend.fp:
            pend.fp, pend.stable_since, pend.offered = fp, now, False
            return False
        if pend.offered or (now - pend.stable_since) < self.stable_seconds:
            return False
        n_raw = count_raws(fp)
        if n_raw < self.min_raw_files:
            if not pend.announced:
                log.info("stable but only %d .raw file(s) in %s; waiting for %d",
                         n_raw, folder.name, self.min_raw_files)
                pend.announced = True
            return False

        log.info("stable: %s (%d files, %d raw)", folder.name, len(fp), n_raw)
        try:
            consumed = self.on_stable(folder)
        except Exception:
            log.exception("on_stable failed for %s", folder.name)
            consumed = False
        if consumed:
            self._done.add(folder)
            self._pending.pop(folder, None)
        else:
            pend.offered = True
        return True

    # ------------------------------------------------------------------ loop --

    def run_forever(self) -> None:
        self._running = True
        log.info("watching %s (poll=%ss, stable=%ss, min_raw=%d)",
                 self.inbox, self.poll_seconds, self.stable_seconds, self.min_raw_files)
        try:
            while self._running:
                self.scan_once()
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            log.info("watcher stopped by user")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
