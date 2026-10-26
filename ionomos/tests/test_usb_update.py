"""Updating a PC with no internet (D130): a Setup file brought on a USB stick is found, checked against the
.sha256 file beside it (or GitHub's digest), copied into Downloads and only then run; the release publishes the
.sha256 file. No Tk here (the app's side is in test_app.py)."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ionomos import updates
from ionomos.cli import main

REPO = Path(__file__).resolve().parents[2]
PAYLOAD = b"MZ fake installer " * 500
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
OTHER = hashlib.sha256(b"something else").hexdigest()
SIZE = len(PAYLOAD)
EXE = "Ionomos-Setup-0.21.0.exe"


def _stick(folder: Path, name: str = EXE, payload: bytes = PAYLOAD, sidecar: str | bytes | None = None) -> Path:
    """An installer on a "stick" (a folder), with a .sha256 beside it in sha256sum's format unless sidecar says."""
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / name
    exe.write_bytes(payload)
    if sidecar is None:
        sidecar = f"{hashlib.sha256(payload).hexdigest()}  {name}\n"
    if sidecar != "":
        data = sidecar.encode("ascii") if isinstance(sidecar, str) else sidecar
        (folder / f"{EXE}.sha256").write_bytes(data)
    return exe


# ----------------------------------------------------------------------------------- the .sha256 file --


@pytest.mark.parametrize(("data", "exe", "want"), [
    (f"{DIGEST}  {EXE}\n".encode(), EXE, [DIGEST]),  # what the release publishes (sha256sum, text mode)
    (f"{DIGEST} *{EXE}\n".encode(), EXE, [DIGEST]),  # sha256sum -b
    (f"{DIGEST.upper()}  {EXE}\n".encode(), EXE, [DIGEST]),  # PowerShell's Get-FileHash is upper case
    (f"{DIGEST}  {EXE}\r\n".encode(), EXE, [DIGEST]),  # CRLF, saved in Notepad
    (b"\xef\xbb\xbf" + f"{DIGEST}  {EXE}\n".encode(), EXE, [DIGEST]),  # a UTF-8 BOM
    (f"{DIGEST}  {EXE}".encode("utf-16"), EXE, [DIGEST]),  # PowerShell 5's Out-File: UTF-16 with a BOM
    (f"{DIGEST}\n".encode(), EXE, [DIGEST]),  # a bare hash on its own line
    (f"  {DIGEST}  \n".encode(), EXE, [DIGEST]),
    (f"SHA256 hash of {EXE}:\r\n{DIGEST}\r\nCertUtil: -hashfile command completed successfully.\r\n".encode(), EXE,
     [DIGEST]),  # certutil -hashfile X SHA256
    (f"SHA256 ({EXE}) = {DIGEST}\n".encode(), EXE, [DIGEST]),  # BSD / macOS `shasum -a 256 --tag`
    (f"{DIGEST}  {EXE}\n".encode(), "Ionomos-Setup-0.21.0 (1).exe", [DIGEST]),  # Windows' second copy of the exe
    (f"{DIGEST}  Ionomos-Setup-0.21.0 (1).exe\n".encode(), EXE, [DIGEST]),
    (f"{DIGEST}  dist/{EXE}\n".encode(), EXE, [DIGEST]),  # a path: its file name counts
    (f"{DIGEST}  C:\\Users\\lab\\Downloads\\{EXE}\n".encode(), EXE, [DIGEST]),
    (f"# made by hand\n\n{DIGEST}  {EXE}\n".encode(), EXE, [DIGEST]),
    (f"{OTHER}  Ionomos-0.21.0-windows.zip\n{DIGEST}  {EXE}\n".encode(), EXE, [DIGEST]),  # a SHA256SUMS file
    (f"{DIGEST}  Ionomos-Setup-0.20.0.exe\n".encode(), EXE, []),  # another version's
    (f"{DIGEST}  Ionomos-0.21.0-windows.zip\n".encode(), EXE, []),  # another file's
    (f"{DIGEST[:63]}  {EXE}\n".encode(), EXE, []),  # 63 hex digits is no SHA-256
    (f"{DIGEST}0  {EXE}\n".encode(), EXE, []),
    (b"", EXE, []),
    (b"\x00\x01\x02 not text", EXE, []),
    (f"{DIGEST}  {EXE}\n{OTHER}  {EXE}\n".encode(), EXE, [DIGEST, OTHER]),  # both kept; one must match
])
def test_a_sha256_file_is_read_tolerantly(data, exe, want):
    assert updates.parse_sidecar(updates.decode_sidecar(data), exe) == want


@pytest.mark.parametrize(("name", "version"), [
    ("Ionomos-Setup-0.21.0.exe.sha256", "0.21.0"),
    ("ionomos-setup-0.21.0.EXE.SHA256", "0.21.0"),
    ("Ionomos-Setup-0.21.0 (1).exe.sha256", "0.21.0"),  # beside a second copy of the exe
    ("Ionomos-Setup-0.21.0.exe (1).sha256", "0.21.0"),  # a browser's second download of the .sha256
    ("Ionomos-Setup-0.21.0.exe.sha256.txt", "0.21.0"),  # Notepad's Save as
    ("Ionomos-Setup-0.21.0.sha256", "0.21.0"),
    ("Ionomos-Setup-0.21.0.exe", None),
    ("Ionomos-Setup-0.21.0.exe.sha512", None),
    ("Ionomos-0.21.0-windows.zip.sha256", None),
    ("SHA256SUMS", None),
])
def test_which_files_are_an_installers_sha256(name, version):
    m = updates.SIDECAR_RE.match(name)
    assert (m.group(1) if m else None) == version


# --------------------------------------------------------------------------------------- the check --


def test_an_installer_with_a_matching_sha256_passes(tmp_path):
    exe = _stick(tmp_path / "E", sidecar=f"{DIGEST.upper()} *{EXE}\r\n")
    assert updates.verify_installer(exe) == DIGEST
    assert updates.checks_for(exe).sidecars[0][0].name == f"{EXE}.sha256"


@pytest.mark.parametrize("payload", [PAYLOAD[:-1], b"X" + PAYLOAD[1:], b""], ids=["short", "changed", "empty"])
def test_a_copy_that_does_not_match_its_sha256_is_refused(tmp_path, payload):
    exe = _stick(tmp_path / "E", payload=payload, sidecar=f"{DIGEST}  {EXE}\n")
    with pytest.raises(updates.UpdateFileError, match="failed its checksum") as exc:
        updates.verify_installer(exe)
    assert ".sha256" in exc.value.fix and updates.release_page("0.21.0") in exc.value.fix


def test_an_installer_without_a_sha256_is_refused_saying_what_to_copy(tmp_path):
    exe = _stick(tmp_path / "E", sidecar="")
    assert not updates.checks_for(exe)
    with pytest.raises(updates.UpdateFileError, match="nothing to be checked against") as exc:
        updates.verify_installer(exe)
    assert f"{EXE}.sha256" in exc.value.fix and "releases/tag/v0.21.0" in exc.value.fix


@pytest.mark.parametrize("sidecar", [b"", b"no hash in here\n", f"{DIGEST}  Ionomos-Setup-0.20.0.exe\n".encode()])
def test_a_sha256_file_that_gives_nothing_is_no_check(tmp_path, sidecar):
    exe = _stick(tmp_path / "E", sidecar=sidecar or b"\n")
    checks = updates.checks_for(exe)
    assert not checks and "has no SHA-256" in checks.note
    with pytest.raises(updates.UpdateFileError, match="has no SHA-256 for"):
        updates.verify_installer(exe)


def _release(version="0.21.0", sha256=DIGEST, size=SIZE):
    return updates.Release(version=version, page="", asset_name=f"Ionomos-Setup-{version}.exe",
                           asset_url="https://x/s.exe", size=size, sha256=sha256)


def test_githubs_digest_alone_is_enough_and_a_different_one_wins_over_the_sha256_file(tmp_path):
    exe = _stick(tmp_path / "E", sidecar="")
    assert updates.verify_installer(exe, _release()) == DIGEST
    with_sum = _stick(tmp_path / "F")  # its .sha256 matches; GitHub says otherwise: refused
    with pytest.raises(updates.UpdateFileError, match="not the file GitHub publishes"):
        updates.verify_installer(with_sum, _release(sha256=OTHER))
    with pytest.raises(updates.UpdateFileError, match="failed its checksum"):  # GitHub's size is checked too
        updates.verify_installer(with_sum, _release(size=len(PAYLOAD) + 1))
    # a release of another version says nothing about this file: its .sha256 decides
    assert updates.verify_installer(with_sum, _release(version="0.22.0", sha256=OTHER)) == DIGEST


def test_a_file_not_named_as_an_installer_is_refused(tmp_path):
    p = tmp_path / "setup.exe"
    p.write_bytes(PAYLOAD)
    with pytest.raises(updates.UpdateFileError, match="not an Ionomos installer"):
        updates.verify_installer(p)


# ------------------------------------------------------------------------------- the copy in Downloads --


def test_a_checked_installer_is_copied_into_downloads_and_run_from_there(tmp_path):
    exe = _stick(tmp_path / "E")
    dl = tmp_path / "Downloads"
    staged = updates.prepare_installer(exe, None, dl)
    assert staged == dl / EXE and staged.read_bytes() == PAYLOAD
    assert (dl / f"{EXE}.sha256").read_bytes() == f"{DIGEST}  {EXE}\n".encode("ascii")  # checkable by itself later
    assert exe.read_bytes() == PAYLOAD and (tmp_path / "E" / f"{EXE}.sha256").is_file()  # the stick is only read
    assert not list(dl.glob("*.part"))
    assert updates.verify_installer(staged) == DIGEST
    assert updates.prepare_installer(exe, None, dl) == staged  # again: the same copy, no second one
    assert sorted(p.name for p in dl.iterdir()) == [EXE, f"{EXE}.sha256"]


def test_an_installer_already_in_downloads_is_not_copied(tmp_path):
    dl = tmp_path / "Downloads"
    exe = _stick(dl)
    assert updates.prepare_installer(exe, None, dl) == exe
    assert sorted(p.name for p in dl.iterdir()) == [EXE, f"{EXE}.sha256"]


def test_a_different_file_of_the_same_name_in_downloads_is_never_overwritten(tmp_path):
    exe = _stick(tmp_path / "E", name="Ionomos-Setup-0.21.0 (1).exe")
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / EXE).write_bytes(b"half a browser download")
    (dl / f"{EXE}.part").write_bytes(b"a browser's own .part")
    staged = updates.prepare_installer(exe, None, dl)
    assert staged.name == "Ionomos-Setup-0.21.0(1).exe" and staged.read_bytes() == PAYLOAD
    assert updates.installer_version(staged.name) == "0.21.0"
    assert (dl / EXE).read_bytes() == b"half a browser download"
    assert (dl / f"{EXE}.part").read_bytes() == b"a browser's own .part"
    assert (dl / "Ionomos-Setup-0.21.0(1).exe.sha256").read_text(encoding="ascii").startswith(DIGEST)


def test_a_stick_pulled_out_mid_copy_leaves_downloads_as_it_was(tmp_path, monkeypatch):
    exe = _stick(tmp_path / "E")
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / "notes.txt").write_text("mine", encoding="utf-8")
    real_open = open

    class Pulled:
        def __init__(self, fh):
            self.fh, self.n = fh, 0

        def read(self, size=-1):
            self.n += 1
            if self.n > 1:
                raise OSError(21, "The device is not ready")
            return self.fh.read(100)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.fh.close()

    def fake_open(path, mode="r", *a, **k):
        fh = real_open(path, mode, *a, **k)
        return Pulled(fh) if Path(path) == exe and "b" in mode and "r" in mode else fh

    digest = updates.verify_installer(exe)
    monkeypatch.setattr("builtins.open", fake_open)
    with pytest.raises(updates.UpdateFileError, match="stick taken out") as err:
        updates.stage_installer(exe, digest, dl)
    monkeypatch.undo()
    assert "Leave the stick in" in err.value.fix
    assert sorted(p.name for p in dl.iterdir()) == ["notes.txt"]  # no .part, no copy


def test_a_copy_that_reads_back_wrong_is_not_kept(tmp_path, monkeypatch):
    exe = _stick(tmp_path / "E")
    dl = tmp_path / "Downloads"
    real = updates._sha256_file
    monkeypatch.setattr(updates, "_sha256_file", lambda p: (0, OTHER) if str(p).endswith(".part") else real(p))
    with pytest.raises(updates.UpdateFileError, match="does not match its checksum"):
        updates.prepare_installer(exe, None, dl)
    assert list(dl.iterdir()) == []


# --------------------------------------------------------------------------------- where it looks --


def test_the_newest_installer_is_found_across_downloads_and_the_sticks(tmp_path, monkeypatch):
    dl, e, f = tmp_path / "Downloads", tmp_path / "E", tmp_path / "F"
    _stick(dl, "Ionomos-Setup-0.21.0.exe")
    _stick(e / "Ionomos", "Ionomos-Setup-0.22.0.exe")  # in an Ionomos folder on the stick
    _stick(f, "Ionomos-Setup-0.20.0.exe")  # this version: not an update
    monkeypatch.setattr(updates, "downloads_dir", lambda: dl)
    monkeypatch.setattr(updates, "removable_drives", lambda: [e, f, tmp_path / "card-reader-with-no-card"])
    found = updates.find_installer(current="0.20.0")
    assert found.path == e / "Ionomos" / "Ionomos-Setup-0.22.0.exe" and found.version == "0.22.0"
    assert found.place == f"the USB stick {e}"
    # the .sha256 beside it is for 0.21.0, not 0.22.0 (_stick names it EXE): no sidecar for this one
    assert not found.has_sidecar
    assert updates.find_installer(current="0.22.0") is None


def test_of_one_version_the_checkable_copy_then_the_one_in_downloads_wins(tmp_path, monkeypatch):
    dl, e = tmp_path / "Downloads", tmp_path / "E"
    _stick(dl, sidecar="")  # no .sha256 in Downloads
    _stick(e)
    places = [(dl, "Downloads"), (e, f"the USB stick {e}")]
    assert updates.find_installer(places, current="0.20.0") == updates.Found(e / EXE, "0.21.0", places[1][1], True)
    (dl / f"{EXE}.sha256").write_text(f"{DIGEST}  {EXE}\n", encoding="ascii")
    assert updates.find_installer(places, current="0.20.0") == updates.Found(dl / EXE, "0.21.0", "Downloads", True)


def test_an_unreadable_place_is_skipped(tmp_path):
    not_a_folder = tmp_path / "E"
    not_a_folder.write_text("x", encoding="utf-8")
    assert updates.find_installer([(not_a_folder, "the USB stick E:\\"), (tmp_path / "gone", "x")], "0.1") is None


def test_downloads_alone_is_what_find_downloaded_installer_still_says(tmp_path):
    _stick(tmp_path, "Ionomos-Setup-9.9.9.exe")
    assert updates.find_downloaded_installer(tmp_path, current="0.5.0") == (tmp_path / "Ionomos-Setup-9.9.9.exe",
                                                                           "9.9.9")


def test_installer_places_are_downloads_then_each_sticks_root_and_ionomos_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "downloads_dir", lambda: tmp_path / "Downloads")
    monkeypatch.setattr(updates, "removable_drives", lambda: [Path("E:\\")])
    places = updates.installer_places()
    assert [w for _, w in places] == ["Downloads", "the USB stick E:\\", "the USB stick E:\\"]
    assert places[2][0] == Path("E:\\") / "Ionomos"


# ------------------------------------------------------------------------------- the drives (Windows) --


class _Kernel32:
    """GetLogicalDrives / GetDriveTypeW / SetThreadErrorMode as Windows answers them on the lab PC (inventory of
    2026-09-15: C: fixed, D: the WD Elements USB disk, fixed; E: the 30 GB "Proteome Discoverer 2.5" stick,
    removable), plus a stick on F: and an A: drive that must not be asked."""
    TYPES = {"A:\\": 2, "C:\\": 3, "D:\\": 3, "E:\\": 2, "F:\\": 2}

    def __init__(self):
        self.asked, self.modes = [], []

    def GetLogicalDrives(self):  # noqa: N802 - Windows' name
        return sum(1 << (ord(r[0]) - ord("A")) for r in self.TYPES)

    def GetDriveTypeW(self, root):  # noqa: N802
        root = getattr(root, "value", root)
        self.asked.append(root)
        return self.TYPES.get(root, 1)

    def SetThreadErrorMode(self, mode, old):  # noqa: N802
        self.modes.append(mode)
        return 1


def _windows(monkeypatch, k32):
    """Windows as updates.py sees it: its own `os` says nt (never os.name itself), kernel32 is the stand-in."""
    monkeypatch.setattr(updates, "os", SimpleNamespace(name="nt", listdir=os.listdir))
    monkeypatch.setattr(updates, "_kernel32", lambda: k32)


def test_removable_drives_are_the_sticks_not_the_usb_hard_disk_nor_a(monkeypatch):
    k32 = _Kernel32()
    _windows(monkeypatch, k32)
    assert updates.removable_drives() == [Path("E:\\"), Path("F:\\")]
    assert "A:\\" not in k32.asked and "B:\\" not in k32.asked


def test_removable_drives_never_raise(monkeypatch):
    class Broken(_Kernel32):
        def GetLogicalDrives(self):  # noqa: N802
            raise OSError("no")

    _windows(monkeypatch, Broken())
    assert updates.removable_drives() == []

    def no_kernel32():
        raise AttributeError("module 'ctypes' has no attribute 'windll'")

    monkeypatch.setattr(updates, "_kernel32", no_kernel32)
    assert updates.removable_drives() == []
    with updates._no_drive_dialogs():  # and the guard around a listing does nothing
        pass


def test_off_windows_there_are_no_removable_drives():
    if os.name == "nt":
        pytest.skip("this is Windows")
    assert updates.removable_drives() == []


def test_listing_a_drive_turns_windows_no_disk_box_off_and_back_on(tmp_path, monkeypatch):
    k32 = _Kernel32()
    _windows(monkeypatch, k32)
    assert updates.find_installer([(tmp_path / "card-reader", "the USB stick G:\\")], "0.1") is None
    assert k32.modes[0] == 0x8001 and len(k32.modes) == 2  # set, then put back


# --------------------------------------------------------------------------- ionomos update --from --


@pytest.fixture
def no_lab(tmp_path, monkeypatch):
    """No remembered config of the developer's own: `ionomos update --from` reads one to stop its watcher."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(updates, "downloads_dir", lambda: tmp_path / "Downloads")
    return ["--config", str(tmp_path / "no-config.yaml")]


def test_cli_update_from_a_file_checks_it(tmp_path, capsys, no_lab):
    exe = _stick(tmp_path / "E")
    assert main([*no_lab, "update", "--from", str(exe), "--check"]) == 0
    assert f"✓ {EXE} matches {EXE}.sha256" in capsys.readouterr().out
    bad = _stick(tmp_path / "F", payload=PAYLOAD[:-1], sidecar=f"{DIGEST}  {EXE}\n")
    assert main([*no_lab, "update", "--from", str(bad), "--check"]) == 1
    assert "failed its checksum" in capsys.readouterr().err
    bare = _stick(tmp_path / "G", sidecar="")
    assert main([*no_lab, "update", "--from", str(bare), "--check"]) == 1
    assert f"{EXE}.sha256" in capsys.readouterr().err
    assert main([*no_lab, "update", "--from", str(tmp_path / "nope.exe"), "--check"]) == 2
    other = tmp_path / "setup.exe"
    other.write_bytes(PAYLOAD)
    assert main([*no_lab, "update", "--from", str(other)]) == 2
    assert main([*no_lab, "update", "--check"]) == 2  # --check checks the --from file; alone it is wrong usage
    assert not (tmp_path / "Downloads").exists()  # a check copies nothing


def test_cli_update_from_a_file_installs_from_downloads(tmp_path, monkeypatch, capsys, no_lab):
    exe = _stick(tmp_path / "E")
    dl = tmp_path / "Downloads"
    ran = []
    assert main([*no_lab, "update", "--from", str(exe)]) == 2  # not an installed Windows build: checked, nothing run
    assert "can't update itself" in capsys.readouterr().err and not dl.exists()
    monkeypatch.setattr(updates, "can_self_update", lambda: True)
    monkeypatch.setattr(updates, "install", lambda installer, log_dir, running: ran.append((installer, running)))
    assert main([*no_lab, "update", "--from", str(exe)]) == 0
    assert ran == [(dl / EXE, False)] and (dl / EXE).read_bytes() == PAYLOAD
    old = _stick(tmp_path / "H", "Ionomos-Setup-0.0.1.exe")
    assert main([*no_lab, "update", "--from", str(old)]) == 1 and len(ran) == 1  # not newer: nothing run
    bad = _stick(tmp_path / "F", payload=PAYLOAD[:-1], sidecar=f"{DIGEST}  {EXE}\n")
    assert main([*no_lab, "update", "--from", str(bad)]) == 1 and len(ran) == 1  # failed its check: nothing run


def test_cli_update_from_a_file_stops_the_watcher_first(lab, tmp_path, monkeypatch, capsys):
    from ionomos import health, service

    exe = _stick(tmp_path / "E")
    calls = []
    monkeypatch.setattr(updates, "downloads_dir", lambda: tmp_path / "Downloads")
    monkeypatch.setattr(updates, "can_self_update", lambda: True)
    monkeypatch.setattr(health, "is_locked", lambda log_dir: True)
    monkeypatch.setattr(service, "request_stop", lambda log_dir, **k: calls.append(("stop", log_dir)) or "stopped")
    monkeypatch.setattr(updates, "install", lambda installer, log_dir, running: calls.append(("install", running)))
    assert main(["--config", str(lab["cfg_path"]), "update", "--from", str(exe)]) == 0
    assert [c[0] for c in calls] == ["stop", "install"] and calls[1] == ("install", True)
    assert "starts the watcher again" in capsys.readouterr().out


# ------------------------------------------------- the app's glue, without Tk (the real window: test_app.py) --


def _fake_app(finished):
    """What App._update_from_file uses of the app; run_bg runs its work at once, on this thread."""
    def run_bg(label, fn, done, **k):
        try:
            res, err = fn(), None
        except Exception as exc:  # noqa: BLE001 - handed to done(), as App.run_bg does
            res, err = None, exc
        done(res, err)

    return SimpleNamespace(_ask_to_update=lambda version: False, set_status=lambda *a, **k: None, run_bg=run_bg,
                           _finish_update=lambda path, version, running: finished.append((path, version, running)))


def test_the_apps_update_from_a_file_checks_copies_and_finishes(tmp_path, monkeypatch):
    from tkinter import messagebox

    from ionomos.app import App

    shown, finished = [], []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(updates, "can_self_update", lambda: True)
    monkeypatch.setattr(updates, "downloads_dir", lambda: tmp_path / "Downloads")
    App._update_from_file(_fake_app(finished), _stick(tmp_path / "E"), None)
    assert finished == [(tmp_path / "Downloads" / EXE, "0.21.0", False)] and not shown
    App._update_from_file(_fake_app(finished), _stick(tmp_path / "F", sidecar=""), None)  # refused before asking
    assert len(finished) == 1 and f"{EXE}.sha256" in shown[-1][1] and "→" in shown[-1][1]
    App._update_from_file(_fake_app(finished), _stick(tmp_path / "G", payload=b"MZ", sidecar=f"{DIGEST}  {EXE}\n"))
    assert len(finished) == 1 and "failed its checksum" in shown[-1][1]


def test_the_apps_status_line_for_a_found_installer():
    from ionomos.app import USB_UPDATE_HINT, App

    stick = updates.Found(Path("F:/Ionomos-Setup-0.21.0.exe"), "0.21.0", "the USB stick F:\\", True)
    assert App._found_update_text(stick) == "Ready on the USB stick F:\\: Ionomos-Setup-0.21.0.exe"
    assert App._found_update_text(updates.Found(Path("x") / EXE, "0.21.0")).startswith(
        f"Downloaded and ready: {EXE} (no {EXE}.sha256 beside it")
    assert App._found_update_text((Path("x") / EXE, "0.21.0")) == f"Downloaded and ready: {EXE}"  # the old pair
    assert "USB stick" in USB_UPDATE_HINT and "Update from a file" in USB_UPDATE_HINT


# ------------------------------------------------------------------------------------ the release --


def test_the_release_publishes_the_installers_sha256():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["build"]["steps"]
    release = next(s for s in steps if "action-gh-release" in str(s.get("uses", "")))
    assert "dist/Ionomos-Setup-*.exe.sha256" in release["with"]["files"].split()
    upload = next(s for s in steps if "upload-artifact" in str(s.get("uses", "")))
    assert "dist/Ionomos-Setup-*.exe.sha256" in upload["with"]["path"].split()
    names = [s.get("name", "") for s in steps]
    write = next(s for s in steps if ".sha256" in s.get("name", ""))
    assert names.index(write["name"]) < steps.index(release)  # written before the release takes it
    assert "Get-FileHash" in write["run"] and "Encoding]::ASCII" in write["run"] and "`n" in write["run"]
    assert "update --from" in write["run"]  # and the frozen exe reads it back
