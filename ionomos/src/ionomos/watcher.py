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

Loose .raw files at the inbox top level (dragged without a folder) are
grouped into folders once they have been unchanged for `stable_seconds`
(loose.py); the new folder then goes through intake without waiting again.
What is ignored: other loose files, hidden/temp names (".", "~$"), and our
own *.REJECTED.txt files. A queued folder has been moved
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
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ionomos.intake import IntakeResult, note_path, path_too_long
from ionomos.naming import RAW_SUFFIX

log = logging.getLogger("ionomos.watcher")

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
    noteless: bool = False  # rejected without a note (its path too long, #139): wait for a change only
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
        heartbeat=None,
        group_loose: bool = True,
        log_dir: Path | None = None,
    ):
        self.inbox = Path(inbox)
        self.log_dir = Path(log_dir) if log_dir else None  # for the needs-attention item of a folder it cannot open
        self._too_long: set[str] = set()  # inbox entries Windows cannot open, each said once (#139)
        self.on_stable = on_stable
        self.poll_seconds = poll_seconds
        self.stable_seconds = stable_seconds
        self.min_raw_files = min_raw_files
        self.retry_seconds = retry_seconds
        self._pending: dict[Path, _Pending] = {}
        self._running = False
        self._stop = threading.Event()
        self.heartbeat = heartbeat
        self._inbox_missing = False
        self.group_loose = group_loose
        self._loose_fp: tuple = ()
        self._loose_since = 0.0
        self._loose_announced = False

    # ------------------------------------------------------------------ poll --

    def scan_once(self, now: float | None = None) -> list[Path]:
        """One pass. Returns the folders handed to on_stable this pass."""
        now = time.monotonic() if now is None else now
        handed: list[Path] = []
        if not self.inbox.is_dir():
            if not self._inbox_missing:  # log once per outage (e.g. a network share dropped), not every poll
                log.error("inbox is not reachable: %s — waiting for it to come back", self.inbox)
                self._inbox_missing = True
            return handed
        if self._inbox_missing:
            log.info("inbox is back: %s", self.inbox)
            self._inbox_missing = False

        if self.group_loose:
            try:
                self._handle_loose(now)
            except Exception:  # noqa: BLE001 - never let loose files stop folder handling
                log.exception("could not group loose files; will look again next poll")

        present: set[Path] = set()
        entries = sorted(self.inbox.iterdir())
        for entry in entries:
            try:
                if _ignored(entry.name) or self._unreachable(entry) or not entry.is_dir():
                    continue
                present.add(entry)
                if self._evaluate(entry, now):
                    handed.append(entry)
            except Exception:  # one unreadable folder must not stop the others from being seen
                log.exception("could not evaluate %s; will look again next poll", entry.name)

        # forget folders that disappeared (user pulled it back out, or we moved it)
        for gone in [p for p in self._pending if p not in present]:
            del self._pending[gone]
        self._forget_unreachable({e.name for e in entries})
        return handed

    def _unreachable(self, entry: Path) -> bool:
        """An inbox entry Windows cannot open because of its length (#139): said once in the log and, with a log
        folder, as a needs-attention item, instead of being skipped in silence for ever. True to leave it."""
        if entry.name in self._too_long:
            return True
        why = path_too_long(entry)
        if why is None:
            return False
        self._too_long.add(entry.name)
        log.warning("cannot read %s: %s", entry.name, why)
        if self.log_dir is not None:
            try:
                from ionomos import attention

                attention.raise_item(
                    self.log_dir, "intake_rejected", f"Couldn't take in “{entry.name}”",
                    f"{why} The folder was left in the inbox as it is.", key=f"intake:{entry.name}",
                    severity="error", dest=entry,
                    causes=["The folder's path is longer than Windows allows"],
                    fixes=["Rename the folder to something shorter (Explorer can); Ionomos then sees it"],
                    data={"folder": str(entry)})
            except Exception:  # noqa: BLE001 - telling a person must never stop the watcher
                log.exception("could not record %s for the app", entry.name)
        return True

    def _forget_unreachable(self, names: set[str]) -> None:
        """A too-long entry that left the inbox (renamed, pulled out): its item is closed."""
        for name in [n for n in self._too_long if n not in names]:
            self._too_long.discard(name)
            if self.log_dir is None:
                continue
            try:
                from ionomos import attention

                for it in attention.items(self.log_dir):
                    if it.key == f"intake:{name}":
                        attention.resolve(self.log_dir, it.id)
            except Exception:  # noqa: BLE001
                log.exception("could not close the item of %s", name)

    def _handle_loose(self, now: float) -> None:
        from ionomos import loose

        files = loose.loose_raws(self.inbox)
        fp = []
        for f in files:
            try:
                st = f.stat()
                fp.append((f.name, st.st_size, st.st_mtime_ns))
            except OSError:
                fp.append((f.name, -1, -1))
        fp = tuple(fp)
        if not fp:
            self._loose_fp, self._loose_announced = (), False
            return
        if fp != self._loose_fp:
            if not self._loose_announced:
                log.info("%d loose .raw file(s) in the inbox (no folder); they will be put into a folder "
                         "once the copy settles", len(fp))
                self._loose_announced = True
            self._loose_fp, self._loose_since = fp, now
            return
        if now - self._loose_since < self.stable_seconds:
            return
        folders = loose.group_loose_files(self.inbox, files)
        for folder in folders:  # already stable: skip the second wait
            self._pending[folder] = _Pending(fp=fingerprint(folder), stable_since=now - self.stable_seconds)
        self._loose_fp, self._loose_announced = (), False

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
            if pend.noteless or note_path(folder).exists():
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
        if self.heartbeat is not None:  # intake can legitimately take long (a person answering, a big copy)
            self.heartbeat.beat("watcher", f"busy: taking in {folder.name}", force=True)
        try:
            result = self.on_stable(folder)
        except Exception:
            log.exception("on_stable failed for %s", folder.name)
            result = IntakeResult.RETRY
        if result == IntakeResult.QUEUED:
            self._pending.pop(folder, None)  # it was moved; a reappearance is a new drop
        elif result == IntakeResult.REJECTED:
            pend.rejected = True
            pend.noteless = not note_path(folder).exists()  # no note could be written (#139): wait for a change
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
            while self._running and not self._stop.is_set():
                try:
                    self.scan_once()
                except Exception:
                    log.exception("scan failed; will poll again")
                if self.heartbeat is not None:
                    waiting = len(self._pending)
                    self.heartbeat.beat("watcher", f"{waiting} folder(s) in inbox" if waiting else "ok")
                self._stop.wait(self.poll_seconds)
        except KeyboardInterrupt:
            log.info("watcher stopped by user")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
        self._stop.set()
