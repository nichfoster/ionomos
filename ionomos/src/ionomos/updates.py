"""
Getting a new version onto the PC with as few steps as possible.

  1. check_latest() asks GitHub (public repo, no login) for the newest release
     and its Ionomos-Setup-<version>.exe. The app does this at start and every
     few hours and shows "Update to <version>" when it's newer.
  2. download() fetches the installer into Downloads and checks its size and
     SHA-256 (GitHub publishes the digest) before anything runs it.
  3. install(): stops the watcher gracefully (a running search is re-queued),
     leaves a RESTART_WATCHER note, starts the installer silently and quits.
     The installer replaces the program (data untouched) and reopens the app,
     which sees the note and starts the watcher again.

Offline, or GitHub unreachable: nothing happens; a Setup file the user
downloaded by hand is still found in Downloads (find_downloaded_installer).

A git checkout (deploy/dev_install.ps1) updates with git instead (service.update_source).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ionomos import __version__

API_LATEST = "https://api.github.com/repos/nichfoster/ionomos/releases/latest"
RELEASES_PAGE = "https://github.com/nichfoster/ionomos/releases/latest"
USER_AGENT = f"Ionomos/{__version__} (update check)"


@dataclass
class Release:
    version: str
    page: str
    asset_name: str
    asset_url: str
    size: int
    sha256: str | None
    notes: str = ""


def check_latest(timeout: float = 8, url: str = API_LATEST) -> tuple[Release | None, str]:
    """(newest published release with a Setup asset, "") or (None, why). Never raises."""
    if os.environ.get("IONOMOS_OFFLINE"):
        return None, "update checks are off (IONOMOS_OFFLINE)"
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"could not reach GitHub ({exc})"
    tag = str(data.get("tag_name") or "")
    for a in data.get("assets") or []:
        m = INSTALLER_RE.match(a.get("name") or "")
        if m:
            digest = a.get("digest") or ""
            return Release(version=m.group(1), page=data.get("html_url") or "", asset_name=a["name"],
                           asset_url=a["browser_download_url"], size=int(a.get("size") or 0),
                           sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
                           notes=(data.get("body") or "")[:2000]), ""
    return None, f"release {tag or '?'} has no Ionomos-Setup-*.exe"


def is_newer(release: Release | None, current: str = __version__) -> bool:
    return release is not None and parse_version(release.version) > parse_version(current)


class DownloadError(RuntimeError):
    pass


def download(release: Release, dest_dir: Path | None = None, progress=None, timeout: float = 30) -> Path:
    """Fetch the installer (to a .part file), verify size + SHA-256, then give it its real name."""
    import hashlib
    import urllib.request

    dest_dir = Path(dest_dir or downloads_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / release.asset_name
    if final.is_file() and _verified(final, release):
        return final  # already downloaded earlier
    part = final.with_name(final.name + ".part")
    req = urllib.request.Request(release.asset_url, headers={"User-Agent": USER_AGENT})
    h = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as out:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, release.size)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise DownloadError(f"download failed: {exc}") from exc
    if release.size and got != release.size:
        part.unlink(missing_ok=True)
        raise DownloadError(f"download incomplete ({got} of {release.size} bytes)")
    if release.sha256 and h.hexdigest() != release.sha256.lower():
        part.unlink(missing_ok=True)
        raise DownloadError("downloaded file failed its checksum; not installing it")
    os.replace(part, final)
    return final


def _verified(path: Path, release: Release) -> bool:
    import hashlib

    if release.size and path.stat().st_size != release.size:
        return False
    if release.sha256:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest() == release.sha256.lower()
    return True


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
