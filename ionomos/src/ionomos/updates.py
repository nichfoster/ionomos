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

Offline, or GitHub unreachable (D130): a Setup file brought on a USB stick, or downloaded by hand, is found
in Downloads, at the root of a removable drive or in an Ionomos folder there (find_installer). It runs only
when its SHA-256 matches GitHub's digest for its version or the Ionomos-Setup-<version>.exe.sha256 file beside
it (verify_installer; each release publishes that file too), and only from a checked copy in Downloads
(stage_installer), never from the stick, which can be pulled out mid-install. A .sha256 file on the same stick
catches a bad or short copy, not a forged one: whoever can change the installer there can change it too.

A git checkout (deploy/dev_install.ps1) updates with git instead (service.update_source).
"""
from __future__ import annotations

import contextlib
import errno
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

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
    import http.client
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:  # IncompleteRead too
        return None, f"could not reach GitHub ({exc})"
    try:  # a proxy's error page, an API change: an answer of another shape is "no update", never a crash
        tag = str(data.get("tag_name") or "")
        for a in data.get("assets") or []:
            m = INSTALLER_RE.match(str(a.get("name") or ""))
            if m:
                digest = str(a.get("digest") or "")
                return Release(version=m.group(1), page=str(data.get("html_url") or ""), asset_name=a["name"],
                               asset_url=str(a["browser_download_url"]), size=int(a.get("size") or 0),
                               sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
                               notes=str(data.get("body") or "")[:2000]), ""
    except (AttributeError, KeyError, TypeError, ValueError) as exc:  # never raises: an odd answer is a reason
        return None, f"GitHub's answer about the newest release could not be read ({type(exc).__name__})"
    return None, f"release {tag or '?'} has no Ionomos-Setup-*.exe"


def is_newer(release: Release | None, current: str = __version__) -> bool:
    return release is not None and parse_version(release.version) > parse_version(current)


class DownloadError(RuntimeError):
    pass


def download(release: Release, dest_dir: Path | None = None, progress=None, timeout: float = 30) -> Path:
    """Fetch the installer (to a .part file), verify size + SHA-256, then give it its real name. Refused without a
    published SHA-256, from anything but https://, or under a name that isn't Ionomos-Setup-<version>.exe."""
    import hashlib
    import http.client
    import urllib.request

    if not release.sha256:  # the app says so before asking; this keeps any other caller from running an unchecked file
        raise DownloadError(f"Ionomos {release.version} has no published SHA-256, so its installer can't be checked; "
                            f"download it from {RELEASES_PAGE}")
    if not str(release.asset_url).lower().startswith("https://"):  # urllib also opens file:, ftp: and data: URLs
        raise DownloadError(f"the release's download address is not https:// ({release.asset_url!r}); not fetched")
    if not INSTALLER_RE.match(release.asset_name or ""):  # the name becomes a file in Downloads: never a path
        raise DownloadError(f"{release.asset_name!r} is not an Ionomos installer name; not fetched")
    dest_dir = Path(dest_dir or downloads_dir())
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if (dest_dir / release.asset_name).is_file() and _verified(dest_dir / release.asset_name, release):
            return dest_dir / release.asset_name  # already downloaded earlier
    except OSError as exc:  # no Downloads folder that can be written, or the old copy is open elsewhere
        raise DownloadError(f"cannot save into {dest_dir}: {exc}") from exc
    final = dest_dir / release.asset_name
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
    except (OSError, http.client.HTTPException) as exc:  # a connection cut mid-file is IncompleteRead
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


def release_page(version: str) -> str:
    """The GitHub page of one release: its installer and the installer's .sha256 file."""
    return f"https://github.com/nichfoster/ionomos/releases/tag/v{version}"


def installer_version(name: str) -> str | None:
    """The version in an installer's file name (Ionomos-Setup-0.21.0.exe, or a Windows copy's "... (1).exe")."""
    m = INSTALLER_RE.match(name or "")
    return m.group(1) if m else None


class Found(NamedTuple):
    """A Setup file on this PC, and where it is in words ("Downloads", "the USB stick E:\\")."""
    path: Path
    version: str
    place: str = "Downloads"
    has_sidecar: bool = False  # an Ionomos-Setup-<version>.exe.sha256 is beside it


DRIVE_REMOVABLE = 2  # GetDriveTypeW: USB sticks and memory cards. A USB hard disk (the lab PC's D:) says 3, fixed
_SEM_FAILCRITICALERRORS, _SEM_NOOPENFILEERRORBOX = 0x0001, 0x8000


def _kernel32():
    """Windows' kernel32 (tests give a stand-in; AttributeError off Windows)."""
    import ctypes

    return ctypes.windll.kernel32


def removable_drives() -> list[Path]:
    """The roots of this PC's removable drives ([E:\\, F:\\]); [] off Windows. A: and B: are left out (a floppy
    drive is slow to answer). Reads Windows' table of drives only, never a drive's contents, so a card reader with
    no card costs nothing here. Never raises."""
    if os.name != "nt":
        return []
    try:
        import ctypes

        k32 = _kernel32()
        mask = int(k32.GetLogicalDrives())
        roots = []
        for i in range(2, 26):
            if mask & (1 << i):
                root = f"{chr(ord('A') + i)}:\\"
                if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) == DRIVE_REMOVABLE:
                    roots.append(Path(root))
        return roots
    except Exception:  # noqa: BLE001 - no ctypes, no kernel32, an odd answer: no drives, never an error
        return []


@contextlib.contextmanager
def _no_drive_dialogs():
    """On Windows, no "There is no disk in the drive" box while a drive is read: a card reader with no card is an
    OSError (the caller catches it), never a window that waits for someone. Elsewhere nothing."""
    k32, old = None, None
    if os.name == "nt":
        try:
            import ctypes

            k32, old = _kernel32(), ctypes.c_uint(0)
            k32.SetThreadErrorMode(_SEM_FAILCRITICALERRORS | _SEM_NOOPENFILEERRORBOX, ctypes.byref(old))
        except Exception:  # noqa: BLE001 - the read is still guarded by the caller's own except
            k32 = None
    try:
        yield
    finally:
        if k32 is not None:
            try:
                k32.SetThreadErrorMode(old.value, None)
            except Exception:  # noqa: BLE001
                pass


def installer_places() -> list[tuple[Path, str]]:
    """Where a Setup file is looked for, as (folder, where in words): Downloads, then each removable drive's root and
    the Ionomos folder at its root."""
    places = [(downloads_dir(), "Downloads")]
    for root in removable_drives():
        where = f"the USB stick {root}"
        places += [(root, where), (root / "Ionomos", where)]
    return places


def find_installer(places: list[tuple[Path, str]] | None = None, current: str = __version__) -> Found | None:
    """The newest Ionomos-Setup-X.Y.Z.exe newer than this version in Downloads or on a USB stick (installer_places),
    or None. Of one version: the one with a .sha256 beside it, then the one in Downloads, then the newest file.
    Only lists folders (no file is opened); a missing folder or a card reader with no card is skipped. Never raises."""
    best_key, best = None, None
    with _no_drive_dialogs():
        for folder, place in (installer_places() if places is None else places):
            try:
                names = os.listdir(folder)
            except (OSError, ValueError):
                continue
            sums = {parse_version(m.group(1)) for m in map(SIDECAR_RE.match, names) if m}
            for name in names:
                v = installer_version(name)
                if v is None or parse_version(v) <= parse_version(current):
                    continue
                p = Path(folder) / name
                try:
                    if not p.is_file():
                        continue
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                side = parse_version(v) in sums
                key = (parse_version(v), side, place == "Downloads", mtime)
                if best_key is None or key > best_key:
                    best_key, best = key, Found(p, v, place, side)
    return best


def find_downloaded_installer(folder: Path | None = None, current: str = __version__) -> tuple[Path, str] | None:
    """Newest Ionomos-Setup-X.Y.Z.exe in Downloads (or folder) that is newer than this version, or None."""
    f = find_installer([(Path(folder or downloads_dir()), "Downloads")], current)
    return (f.path, f.version) if f else None


# ----------------------------------------------------------- an installer brought by hand (D130) --

SIDECAR_RE = re.compile(r"^Ionomos-Setup-(\d+(?:\.\d+){1,3})(?:\s*\(\d+\))?(?:\.exe)?(?:\s*\(\d+\))?\.sha256(?:\.txt)?$",
                        re.IGNORECASE)
_SUM_LINE = re.compile(r"^\\?(?P<h>[0-9A-Fa-f]{64})(?:\s+\*?(?P<n>.*?))?\s*$")  # sha256sum: "<hex>  name", "<hex> *name"
_BSD_LINE = re.compile(r"^SHA-?256\s*\((?P<n>.+)\)\s*=\s*(?P<h>[0-9A-Fa-f]{64})\s*$", re.IGNORECASE)
_SIDECAR_MAX = 64 * 1024


class UpdateFileError(RuntimeError):
    """An installer that is not run: what is wrong (the message) and what to do (fix)."""

    def __init__(self, message: str, fix: str = ""):
        super().__init__(message)
        self.fix = fix


def decode_sidecar(data: bytes) -> str:
    """A .sha256 file's text: UTF-8 with or without a BOM, or UTF-16 (what PowerShell 5's Out-File writes)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data[:200]:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def parse_sidecar(text: str, installer_name: str) -> list[str]:
    """The SHA-256s (lower-case hex) a .sha256 file gives for this installer. Reads sha256sum's lines ("<hex>  name",
    "<hex> *name"), a bare hash on its own line (certutil's middle line), BSD's "SHA256 (name) = <hex>"; any case,
    CRLF or LF, a BOM. A line naming another file (another version; SHA256SUMS' other lines) is left out. A name with
    a folder, or with Windows' " (1)", counts as its file name."""
    want = installer_version(installer_name)
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue
        m = _SUM_LINE.match(line) or _BSD_LINE.match(line)
        if not m:
            continue
        name = (m.group("n") or "").strip().strip('"')
        if name:
            v = installer_version(re.split(r"[\\/]", name)[-1].strip())
            if v is None or want is None or parse_version(v) != parse_version(want):
                continue
        digest = m.group("h").lower()
        if digest not in out:
            out.append(digest)
    return out


def sidecars_for(installer: Path) -> list[Path]:
    """The .sha256 files beside an installer that are for its version (Ionomos-Setup-0.21.0.exe.sha256, also as
    "... (1).sha256" or ".sha256.txt"). Never raises."""
    installer = Path(installer)
    want = installer_version(installer.name)
    if want is None:
        return []
    with _no_drive_dialogs():
        try:
            names = sorted(os.listdir(installer.parent))
        except (OSError, ValueError):
            return []
    return [installer.parent / n for n in names
            if (m := SIDECAR_RE.match(n)) and parse_version(m.group(1)) == parse_version(want)]


@dataclass
class Checks:
    """What an installer can be checked against: GitHub's digest for its version, the .sha256 files beside it."""
    release: str | None = None
    release_size: int = 0
    sidecars: list[tuple[Path, list[str]]] = field(default_factory=list)  # (file, its digests for this installer)
    note: str = ""  # why a .sha256 file beside it gave nothing to check with

    def __bool__(self) -> bool:
        return bool(self.release or self.sidecars)


def checks_for(installer: Path, release: Release | None = None) -> Checks:
    """What the installer can be checked against. Cheap: reads only the small .sha256 files, never the installer.
    release is the one GitHub named; its digest counts only for the installer's own version. Never raises."""
    installer = Path(installer)
    c = Checks()
    v = installer_version(installer.name)
    if release is not None and release.sha256 and v and parse_version(v) == parse_version(release.version):
        c.release, c.release_size = release.sha256.lower(), int(release.size or 0)
    for sc in sidecars_for(installer):
        try:
            with _no_drive_dialogs():
                if sc.stat().st_size > _SIDECAR_MAX:
                    c.note = f"{sc.name} is too big to be a .sha256 file"
                    continue
                digests = parse_sidecar(decode_sidecar(sc.read_bytes()), installer.name)
        except OSError as exc:
            c.note = f"{sc.name} can't be read ({exc.strerror or exc})"
            continue
        if digests:
            c.sidecars.append((sc, digests))
        else:
            c.note = f"{sc.name} has no SHA-256 for {installer.name} in it"
    return c


def nothing_to_check_with(installer: Path, checks: Checks | None = None) -> UpdateFileError:
    """The refusal for an installer with no digest from GitHub and no usable .sha256 file beside it."""
    installer = Path(installer)
    v = installer_version(installer.name) or "<version>"
    note = f"; {checks.note}" if checks is not None and checks.note else ""
    return UpdateFileError(
        f"{installer.name} has nothing to be checked against: no SHA-256 from GitHub (it can't be reached, or names "
        f"another version) and no Ionomos-Setup-{v}.exe.sha256 beside it{note}. It won't be installed "
        "automatically.",
        f"Next time copy Ionomos-Setup-{v}.exe.sha256 from {release_page(v)} into the same folder as the installer "
        f"(both files on the stick), then try again. Or run {installer.name} yourself.")


def _sha256_file(path: Path) -> tuple[int, str]:
    import hashlib

    h = hashlib.sha256()
    n = 0
    with _no_drive_dialogs(), open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return n, h.hexdigest()


def verify_installer(installer: Path, release: Release | None = None) -> str:
    """Check a Setup file before anything runs it: its SHA-256 when it passes, else UpdateFileError. It passes when it
    matches GitHub's digest (and size) for its version, or, without one, a .sha256 file beside it. A digest from
    GitHub that differs is a refusal whatever a .sha256 file says."""
    installer = Path(installer)
    v = installer_version(installer.name)
    if v is None:
        raise UpdateFileError(f"{installer.name} is not an Ionomos installer (Ionomos-Setup-<version>.exe).",
                              f"Choose the Ionomos-Setup-<version>.exe from {RELEASES_PAGE}.")
    checks = checks_for(installer, release)
    if not checks:
        raise nothing_to_check_with(installer, checks)
    try:
        size, digest = _sha256_file(installer)
    except OSError as exc:
        raise UpdateFileError(f"{installer} can't be read ({exc.strerror or exc}).",
                              "Put the USB stick back in, or copy the installer into Downloads, then try again.") \
            from exc
    again = f"Copy Ionomos-Setup-{v}.exe and its .sha256 file from {release_page(v)} again, then try again."
    if checks.release:
        if (checks.release_size and size != checks.release_size) or digest != checks.release:
            raise UpdateFileError(f"{installer.name} failed its checksum: it is not the file GitHub publishes for "
                                  f"Ionomos {v} (a bad or incomplete copy).", again)
        return digest
    if any(digest in ds for _, ds in checks.sidecars):
        return digest
    raise UpdateFileError(f"{installer.name} failed its checksum: it does not match {checks.sidecars[0][0].name} "
                          "beside it (a bad or incomplete copy, or the two files are from different releases).", again)


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def _write_sidecar(installer: Path, sha256: str) -> None:
    """Leave <installer>.sha256 beside a copy, so the copy can be checked by itself later. Never over a file."""
    try:
        with open(installer.with_name(installer.name + ".sha256"), "x", encoding="ascii", newline="\n") as fh:
            fh.write(f"{sha256}  {installer.name}\n")
    except OSError:
        pass


def stage_installer(installer: Path, sha256: str, dest_dir: Path | None = None) -> Path:
    """The installer to run: itself when it is in Downloads, else a copy there (D130: a USB stick pulled out
    mid-install would break the install). The copy goes through a new .part file, is read back from the disk and
    checked against sha256, then gets the first name no other file has (Ionomos-Setup-0.21.0.exe, then
    Ionomos-Setup-0.21.0(1).exe, ...); a file of that name with the same content is used as it is. Never overwrites
    a file; a failed copy leaves only what was there before. UpdateFileError on failure."""
    installer, sha256 = Path(installer), sha256.lower()
    dest = Path(dest_dir or downloads_dir())
    if _same_folder(installer.parent, dest):
        return installer
    v = installer_version(installer.name)
    if v is None:
        raise UpdateFileError(f"{installer.name} is not an Ionomos installer (Ionomos-Setup-<version>.exe).",
                              f"Choose the Ionomos-Setup-<version>.exe from {RELEASES_PAGE}.")
    copy_it = f"Copy {installer.name} and its .sha256 file into {dest} yourself, then press Check for updates."
    or_copy_it = "c" + copy_it[1:]
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateFileError(f"cannot save into {dest}: {exc.strerror or exc}", copy_it) from exc
    final = None
    for n in range(100):
        cand = dest / (f"Ionomos-Setup-{v}.exe" if n == 0 else f"Ionomos-Setup-{v}({n}).exe")
        if not cand.exists():
            final = cand
            break
        try:
            if cand.is_file() and _sha256_file(cand)[1] == sha256:  # copied before: an update that did not start
                _write_sidecar(cand, sha256)
                return cand
        except OSError:
            continue
    if final is None:
        raise UpdateFileError(f"{dest} already has 100 other files called Ionomos-Setup-{v}….", copy_it)
    part, fh = None, None
    for n in range(100):
        cand = final.with_name(final.name + (".part" if n == 0 else f".{n}.part"))
        try:
            fh = open(cand, "xb")  # a new file, never one that is there (a browser's own .part, say)
        except FileExistsError:
            continue
        except OSError as exc:
            raise UpdateFileError(f"cannot save into {dest}: {exc.strerror or exc}", copy_it) from exc
        part = cand
        break
    if part is None or fh is None:
        raise UpdateFileError(f"{dest} has too many {final.name}.part files.", copy_it)
    try:
        with fh, _no_drive_dialogs(), open(installer, "rb") as src:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        got = _sha256_file(part)[1]  # what is on the disk now, not what was read from the stick
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateFileError(f"copying {installer.name} into {dest} failed ({exc.strerror or exc}): was the USB stick "
                              "taken out, or is the disk full?",
                              "Leave the stick in until the update starts and try again, or " + or_copy_it) from exc
    if got != sha256:
        part.unlink(missing_ok=True)
        raise UpdateFileError(f"the copy of {installer.name} in {dest} does not match its checksum; it was not kept.",
                              "Try again; if it repeats, " + or_copy_it)
    try:
        if final.exists():
            raise FileExistsError(errno.EEXIST, "a file of that name appeared meanwhile", str(final))
        os.rename(part, final)  # on Windows a rename never replaces a file
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateFileError(f"could not name the copy {final} ({exc.strerror or exc}).", "Try again.") from exc
    _write_sidecar(final, sha256)
    return final


def prepare_installer(installer: Path, release: Release | None = None, dest_dir: Path | None = None) -> Path:
    """verify_installer, then stage_installer: the checked copy in Downloads to run, or UpdateFileError. Reads the
    whole file twice (the check, and the copy read back): run it off the Tk thread."""
    return stage_installer(installer, verify_installer(installer, release), dest_dir)


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
