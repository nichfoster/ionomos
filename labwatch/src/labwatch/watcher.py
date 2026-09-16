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
(".", "~$"), and our own *.REJECTED.txt files. A queued folder has been moved
out of the inbox, so if the same name shows up again it is a new drop; the
ledger (via intake) is what rejects a duplicate name.

on_stable(path) -> IntakeResult:
    QUEUED   — folder was moved; forget it.
    REJECTED — a .REJECTED.txt note was written. Keep watching: re-offer when
               the tree changes (user fixed it) or the note disappears (user
               deleted it to ask for a retry).
    RETRY    — transient (Windows file lock). Re-offer after `retry_seconds`,
               doubling each time up to 5 min.
Any exception from on_stable is logged and treated as RETRY. The loop itself
never dies: a failing scan is logged and the next poll happens anyway.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from labwatch.intake import IntakeResult, note_path
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
    announced: bool = False  # logged "waiting for raws" once
    rejected: bool = False  # note written; wait for a change or note deletion
    retry_at: float | None = None  # monotonic time before which we don't re-offer
    retry_delay: float = 0.0


class Watcher:
    def __init__(
        self,
        inbox: Path,
        on_stable: Callable[[Path], IntakeResult],
        poll_seconds: float = 10,
        stable_seconds: float = 60,
        min_raw_files: int = 1,
        retry_seconds: float = 15,
    ):
        self.inbox = Path(inbox)
        self.on_stable = on_stable
        self.poll_seconds = poll_seconds
        self.stable_seconds = stable_seconds
        self.min_raw_files = min_raw_files
        self.retry_seconds = retry_seconds
        self._pending: dict[Path, _Pending] = {}
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
            if self._evaluate(entry, now):
                handed.append(entry)

        # forget folders that disappeared (user pulled it back out, or we moved it)
        for gone in [p for p in self._pending if p not in present]:
            del self._pending[gone]
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
            pend.fp, pend.stable_since = fp, now
            pend.rejected, pend.retry_at, pend.retry_delay = False, None, 0.0
            return False
        if (now - pend.stable_since) < self.stable_seconds:
            return False
        if pend.rejected:
            if note_path(folder).exists():
                return False
            log.info("rejection note for %s was removed; trying again", folder.name)
            pend.rejected = False
        if pend.retry_at is not None:
            if now < pend.retry_at:
                return False
            pend.retry_at = None
        n_raw = count_raws(fp)
        if n_raw < self.min_raw_files:
            if not pend.announced:
                log.info("stable but only %d .raw file(s) in %s; waiting for %d",
                         n_raw, folder.name, self.min_raw_files)
                pend.announced = True
            return False

        log.info("stable: %s (%d files, %d raw)", folder.name, len(fp), n_raw)
        try:
            result = self.on_stable(folder)
        except Exception:
            log.exception("on_stable failed for %s", folder.name)
            result = IntakeResult.RETRY
        if result == IntakeResult.QUEUED:
            self._pending.pop(folder, None)  # it was moved; a reappearance is a new drop
        elif result == IntakeResult.REJECTED:
            pend.rejected = True
        else:  # RETRY with exponential backoff, capped at 5 min
            pend.retry_delay = min(max(self.retry_seconds, pend.retry_delay * 2), 300)
            pend.retry_at = now + pend.retry_delay
            log.info("will retry %s in %.0fs", folder.name, pend.retry_delay)
        return True

    # ------------------------------------------------------------------ loop --

    def run_forever(self) -> None:
        self._running = True
        log.info("watching %s (poll=%ss, stable=%ss, min_raw=%d)",
                 self.inbox, self.poll_seconds, self.stable_seconds, self.min_raw_files)
        try:
            while self._running:
                try:
                    self.scan_once()
                except Exception:
                    log.exception("scan failed; will poll again")
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            log.info("watcher stopped by user")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
