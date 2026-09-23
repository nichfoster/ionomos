"""
Getting a new version onto the PC with as few steps as possible.

The repository is private, so the app can't download releases by itself
(that needs a GitHub login). The flow instead:

  1. "Get the latest version" opens the Releases page in the browser (where
     the user is signed in) -> they download Ionomos-Setup-<version>.exe.
  2. The app notices a newer Ionomos-Setup-*.exe in Downloads and shows
     "Install update <version>".
  3. install(): stops the watcher gracefully (a running search is re-queued),
     leaves a RESTART_WATCHER note, starts the installer silently and quits.
     The installer replaces the program (data untouched) and reopens the app,
     which sees the note and starts the watcher again.

A git checkout (deploy/dev_install.ps1) updates with git instead (service.update_source).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from ionomos import __version__

INSTALLER_RE = re.compile(r"^Ionomos-Setup-(\d+(?:\.\d+){1,3})(?:\s*\(\d+\))?\.exe$", re.IGNORECASE)
RESTART_FLAG = "RESTART_WATCHER"


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def downloads_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"
    return Path.home() / "Downloads"


def find_downloaded_installer(folder: Path | None = None, current: str = __version__) -> tuple[Path, str] | None:
    """Newest Ionomos-Setup-X.Y.Z.exe in Downloads that is newer than this version, or None."""
    folder = folder or downloads_dir()
    best: tuple[tuple[int, ...], Path, str] | None = None
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return None
    for p in entries:
        m = INSTALLER_RE.match(p.name)
        if not m or not p.is_file():
            continue
        v = m.group(1)
        if parse_version(v) <= parse_version(current):
            continue
        if best is None or parse_version(v) > best[0] or (parse_version(v) == best[0] and p.stat().st_mtime > best[1].stat().st_mtime):
            best = (parse_version(v), p, v)
    return (best[1], best[2]) if best else None


def can_self_update() -> bool:
    """Only an installed (not portable, not source) Windows build replaces itself with an installer."""
    return os.name == "nt" and getattr(sys, "frozen", False)


def install(installer: Path, log_dir: Path | None, watcher_was_running: bool) -> None:
    """Hand over to the installer (silent, closes and reopens the app). The caller exits right after."""
    if log_dir is not None and watcher_was_running:
        try:
            (Path(log_dir) / RESTART_FLAG).write_text("restart after update\n", encoding="utf-8")
        except OSError:
            pass
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=flags, close_fds=True)


def take_restart_flag(log_dir: Path) -> bool:
    """True once after an update that stopped a running watcher (the note is removed)."""
    p = Path(log_dir) / RESTART_FLAG
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            pass
        return True
    return False
