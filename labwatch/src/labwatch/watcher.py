"""
Inbox watcher — detects new experiment FOLDERS and waits for the copy to finish.

Contract (Phase 1):
    Watcher(config, on_stable: Callable[[Path], None])
        .run_forever()  — blocking poll loop, Ctrl-C to stop
        .scan_once()    — one pass (used by tests and `labwatch dry-run`)

Detection:
    Every `poll_seconds`, list top-level entries in `paths.inbox`. Directories
    only; loose files are ignored (logged once). Skip names ending in
    ".REJECTED.txt" or starting with "." / "~$".

Stability (why not "on created"):
    A drag-and-drop of 20 GB from a USB drive lands file by file over minutes.
    We fingerprint the whole tree — sorted list of (relative path, size,
    mtime_ns) — each poll. Only when the fingerprint is unchanged for
    `stable_seconds` AND at least `min_raw_files` *.raw are present do we call
    on_stable(path). Folders already handed off are remembered in-process; the
    ledger (not this module) is what prevents re-intake across restarts.

Reuse: reference/prior-work/proteomics-qc-pkg/watcher.py — same idea, but per
file. Generalise _PendingFile to a tree fingerprint.
"""
from __future__ import annotations


class Watcher:
    def __init__(self, config, on_stable):
        raise NotImplementedError("Phase 1")
