"""
Watcher — the producer side.

Monitors the two incoming folders (DDA and DIA) for new .raw files and, once a
file looks fully written, hands a Job to the worker. Acquisition type is
decided purely by WHICH folder the file landed in — no guessing from contents.

THE SIZE-STABILITY PROBLEM (why this isn't just "on file created"):
A .raw file appears on disk the moment the instrument starts writing it, and
keeps growing for the length of the acquisition. If we searched on first sight
we'd hand FragPipe a half-written file. So instead we poll: a file is only
"ready" once its size has been UNCHANGED for `stable_seconds`. Only then is it
enqueued. This is robust and needs no OS-specific file-lock trickery.

We use polling rather than filesystem events deliberately: it's simple, works
identically on every OS/network share, and at this cadence (a handful of files
a week) the efficiency cost of polling every few seconds is irrelevant.

State: we remember which files we've already enqueued (by path) so a file
that's been handed off isn't enqueued again on the next poll.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from queue_worker import Job, QueueWorker

log = logging.getLogger("qc.watcher")

RAW_SUFFIX = ".raw"


@dataclass
class _PendingFile:
    """Tracks a file we've seen but not yet enqueued (still stabilising)."""
    last_size: int
    stable_since: float  # monotonic time the size last changed


class Watcher:
    """Polls the two incoming folders and enqueues stable files."""

    def __init__(self, cfg: dict, worker: QueueWorker):
        self.cfg = cfg
        self.worker = worker
        wcfg = cfg.get("watcher", {})
        self.stable_seconds: float = wcfg.get("stable_seconds", 30)
        self.poll_seconds: float = wcfg.get("poll_seconds", 5)

        paths = cfg["paths"]
        # Map each watched folder to the acquisition type it represents.
        self.folders: dict[Path, str] = {
            Path(paths["incoming_dda"]): "DDA",
            Path(paths["incoming_dia"]): "DIA",
        }

        # Files currently stabilising: path -> _PendingFile
        self._pending: dict[Path, _PendingFile] = {}
        # Files already handed to the worker (don't re-enqueue).
        self._enqueued: set[Path] = set()
        self._running = False

    def _scan_once(self) -> None:
        """One polling pass over both folders."""
        now = time.monotonic()
        for folder, acquisition in self.folders.items():
            if not folder.exists():
                # Folder missing isn't fatal — log once-ish and move on.
                log.debug("Watch folder does not exist yet: %s", folder)
                continue
            for path in folder.glob(f"*{RAW_SUFFIX}"):
                self._evaluate(path, acquisition, now)

    def _evaluate(self, path: Path, acquisition: str, now: float) -> None:
        if path in self._enqueued:
            return
        try:
            size = path.stat().st_size
        except OSError:
            # File vanished or is locked mid-stat; try again next pass.
            return

        pending = self._pending.get(path)
        if pending is None:
            # First time we've seen it — start tracking.
            self._pending[path] = _PendingFile(last_size=size, stable_since=now)
            log.info("Detected new file (stabilising): %s", path.name)
            return

        if size != pending.last_size:
            # Still being written — reset the stability clock.
            pending.last_size = size
            pending.stable_since = now
            return

        # Size unchanged since stable_since. Has it been long enough?
        if (now - pending.stable_since) >= self.stable_seconds:
            self._enqueue(path, acquisition)

    def _enqueue(self, path: Path, acquisition: str) -> None:
        self._enqueued.add(path)
        self._pending.pop(path, None)
        log.info("File stable, enqueuing: %s (%s)", path.name, acquisition)
        self.worker.enqueue(Job(raw_file=str(path), acquisition=acquisition))

    def run_forever(self) -> None:
        """Blocking poll loop. Ctrl-C to stop."""
        self._running = True
        log.info("Watching:\n  DDA: %s\n  DIA: %s\n  (stable=%ss, poll=%ss)",
                 *[p for p in self.folders],
                 self.stable_seconds, self.poll_seconds)
        try:
            while self._running:
                self._scan_once()
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            log.info("Watcher interrupted by user.")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
