"""Loose raw files in the inbox, Xcalibur timestamps, and updates from GitHub (report 2026-09-23 15:05)."""
import hashlib
import io
import json
import threading
import time
from pathlib import Path

import pytest

from ionomos import loose, service, updates
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.naming import NamingError, group_raws, parse_raw_name, strip_acq_stamp
from ionomos.watcher import Watcher

CHRIS = ["CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1.raw", "CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1_20260508180610.raw",
         "CS_22rv1_FLAG-AR_MA25-10uM_DMSO_2_20260508204737.raw", "CS_22rv1_FLAG-AR_MA25-10uM_DMSO_3.raw",
         "CS_22rv1_FLAG-AR_MA25-10uM_MA25_1.raw", "CS_22rv1_FLAG-AR_MA25-10uM_MA25_2.raw",
         "CS_22rv1_FLAG-AR_MA25-10uM_MA25_3.raw"]


# ------------------------------------------------------------ naming: timestamps --


@pytest.mark.parametrize(("name", "sample", "rep"), [
    ("CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1.raw", "CS_22rv1_FLAG-AR_MA25-10uM_DMSO", 1),
    ("CS_22rv1_FLAG-AR_MA25-10uM_DMSO_2_20260508204737.raw", "CS_22rv1_FLAG-AR_MA25-10uM_DMSO", 2),
    ("CS_22rv1_FLAG-AR_MA25-10uM_MA25_3.raw", "CS_22rv1_FLAG-AR_MA25-10uM_MA25", 3),
])
def test_xcalibur_timestamps_are_not_replicates(name, sample, rep):
    r = parse_raw_name(name, "DIA")
    assert (r.sample, r.rep) == (sample, rep) and r.filename == name  # the file keeps its name


def test_timestamp_only_when_it_is_one():
    assert strip_acq_stamp("X_1_20260508180610") == "X_1"
    assert strip_acq_stamp("X_1_20261399999999") == "X_1_20261399999999"  # not a real date/time
    assert strip_acq_stamp("X_20260508") == "X_20260508"  # a date alone is not an acquisition stamp


def test_a_re_acquired_replicate_is_flagged_not_guessed():
    with pytest.raises(NamingError, match="two files resolve to sample=CS_22rv1_FLAG-AR_MA25-10uM_DMSO rep=1"):
        group_raws(CHRIS, "DIA")


# ---------------------------------------------------------------- loose files --


def test_grouping_real_and_mixed_drops():
    assert loose.group(CHRIS) == {"CS_22rv1_FLAG-AR_MA25-10uM": sorted(CHRIS)}
    g = loose.group(["EJQ_PK_A_1_1.raw", "EJQ_PK_A_2_1.raw", "IJ_DIA_X_DMSO_1.raw", "IJ_DIA_X_Drug_1.raw",
                     "KL_TMT_F1.raw", "KL_TMT_F2.raw", "lonely.raw"])
    assert set(g) == {"EJQ_PK_A", "IJ_DIA_X", "KL_TMT", "lonely"}


def _put(folder: Path, names, size=64):
    for n in names:
        (folder / n).write_bytes(b"\0" * size)


def test_group_loose_files_moves_never_overwrites(tmp_path):
    _put(tmp_path, ["EJQ_DIA_x_DMSO_1.raw", "EJQ_DIA_x_DMSO_2.raw", "notes.xlsx"])
    folders = loose.group_loose_files(tmp_path)
    assert [f.name for f in folders] == ["EJQ_DIA_x_DMSO"]
    assert sorted(p.name for p in folders[0].glob("*.raw")) == ["EJQ_DIA_x_DMSO_1.raw", "EJQ_DIA_x_DMSO_2.raw"]
    assert (tmp_path / "notes.xlsx").is_file()  # non-raw loose files stay
    # a late file with the same prefix joins our folder; a name clash is never overwritten
    _put(tmp_path, ["EJQ_DIA_x_DMSO_3.raw"])
    (tmp_path / "EJQ_DIA_x_DMSO_4.raw").write_bytes(b"new")
    (folders[0] / "EJQ_DIA_x_DMSO_4.raw").write_bytes(b"old")
    loose.group_loose_files(tmp_path)
    assert (folders[0] / "EJQ_DIA_x_DMSO_3.raw").is_file()
    assert (folders[0] / "EJQ_DIA_x_DMSO_4.raw").read_bytes() == b"old"
    assert (tmp_path / "EJQ_DIA_x_DMSO_4.raw").read_bytes() == b"new"  # left alone, not lost


def test_a_user_folder_with_that_name_is_not_joined(tmp_path):
    (tmp_path / "EJQ_DIA_x").mkdir()  # somebody's own folder, no marker
    _put(tmp_path, ["EJQ_DIA_x_1.raw", "EJQ_DIA_x_2.raw"])
    assert [f.name for f in loose.group_loose_files(tmp_path)] == ["EJQ_DIA_x_2"]


def test_a_locked_file_waits_for_a_later_pass(tmp_path, monkeypatch):
    _put(tmp_path, ["EJQ_DIA_x_1.raw", "EJQ_DIA_x_2.raw"])
    real = loose.os.rename

    def flaky(a, b):
        if Path(a).name.endswith("_2.raw"):
            raise PermissionError("open in Xcalibur")
        real(a, b)

    monkeypatch.setattr(loose.os, "rename", flaky)
    loose.group_loose_files(tmp_path)
    assert (tmp_path / "EJQ_DIA_x_2.raw").is_file()
    monkeypatch.setattr(loose.os, "rename", real)
    loose.group_loose_files(tmp_path)
    assert (tmp_path / "EJQ_DIA_x" / "EJQ_DIA_x_2.raw").is_file()


def test_watcher_turns_loose_files_into_a_queued_job(lab):
    cfg, ledger = lab["cfg"], Ledger(lab["cfg"].database)
    w = Watcher(cfg.inbox, lambda f: intake(f, cfg, ledger), poll_seconds=0.05, stable_seconds=0.3, min_raw_files=1)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    try:
        _put(cfg.inbox, ["20260923_EJQ_DIA_pulldown_DMSO_1.raw", "20260923_EJQ_DIA_pulldown_DMSO_2.raw",
                         "20260923_EJQ_DIA_pulldown_MA25_1.raw", "20260923_EJQ_DIA_pulldown_MA25_2.raw"])
        for _ in range(100):
            if ledger.list():
                break
            time.sleep(0.05)
        jobs = ledger.list()
        assert len(jobs) == 1 and jobs[0].user == "EJQ" and jobs[0].method == "DIA"
        assert not list(cfg.inbox.glob("*.raw"))
        dest = Path(jobs[0].dest_dir)
        assert dest.name == "20260923_EJQ_DIA_pulldown" and len(list(dest.glob("*.raw"))) == 4
    finally:
        w.stop()
        t.join(timeout=5)


# -------------------------------------------------------------------- updates --


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _fake_github(monkeypatch, payload: bytes, api: dict):
    def urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        return _Resp(json.dumps(api).encode() if "api.github.com" in url else payload)

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("IONOMOS_OFFLINE", raising=False)


def _api(payload: bytes, version="9.9.9", digest=True, size=None):
    return {"tag_name": f"v{version}", "html_url": f"https://github.com/nichfoster/ionomos/releases/tag/v{version}",
            "body": "notes", "assets": [
                {"name": f"Ionomos-{version}-windows.zip", "browser_download_url": "https://x/zip", "size": 1},
                {"name": f"Ionomos-Setup-{version}.exe", "browser_download_url": "https://x/setup.exe",
                 "size": len(payload) if size is None else size,
                 "digest": ("sha256:" + hashlib.sha256(payload).hexdigest()) if digest else None}]}


def test_check_and_download_a_verified_release(tmp_path, monkeypatch):
    payload = b"MZ fake installer" * 1000
    _fake_github(monkeypatch, payload, _api(payload))
    rel, why = updates.check_latest()
    assert rel.version == "9.9.9" and rel.asset_name == "Ionomos-Setup-9.9.9.exe" and not why
    assert updates.is_newer(rel, "0.5.1") and not updates.is_newer(rel, "9.9.9")
    seen = []
    path = updates.download(rel, tmp_path, progress=lambda g, t: seen.append(g))
    assert path.read_bytes() == payload and seen[-1] == len(payload)
    assert not list(tmp_path.glob("*.part"))
    assert updates.download(rel, tmp_path) == path  # already there and verified: no second download


def test_a_tampered_or_short_download_is_refused(tmp_path, monkeypatch):
    payload = b"real installer"
    api = _api(payload)
    _fake_github(monkeypatch, b"something else!", api)
    rel, _ = updates.check_latest()
    with pytest.raises(updates.DownloadError, match="checksum|incomplete"):
        updates.download(rel, tmp_path)
    assert not list(tmp_path.iterdir())  # nothing left that could be run


def test_offline_or_no_setup_asset(monkeypatch):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    assert updates.check_latest()[0] is None
    payload = b"x"
    api = _api(payload)
    api["assets"] = api["assets"][:1]
    _fake_github(monkeypatch, payload, api)
    rel, why = updates.check_latest()
    assert rel is None and "no Ionomos-Setup" in why


def test_links_open_in_the_browser_not_as_paths(monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    service.open_path("https://github.com/nichfoster/ionomos/releases/latest")
    assert opened == ["https://github.com/nichfoster/ionomos/releases/latest"]
