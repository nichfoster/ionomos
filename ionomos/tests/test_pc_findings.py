"""Regressions from the first real install (report 2026-09-23, Windows 11, FragPipe 24.0 via its installer)."""
import sys
from pathlib import Path

import pytest

from ionomos import fragpipe, service, setupcheck
from ionomos.config import not_a_user


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """C:\\FragPipe with the 23.1 and 24.0 installer layouts + an old 22.0 zip under 'Daniel Nomura' (a space)."""
    fp = tmp_path / "FragPipe"
    new = _touch(fp / "FragPipe-24.0" / "bin" / "FragPipe-24.0.exe")
    _touch(fp / "FragPipe-23.1" / "bin" / "FragPipe-23.1.exe")
    old = _touch(tmp_path / "Users" / "Daniel Nomura" / "Downloads" / "FragPipe-jre-22.0" / "fragpipe" / "bin" / "fragpipe.bat")
    _touch(old.parent / "fragpipe.exe")
    monkeypatch.setattr(fragpipe, "LAUNCHER_GLOBS", (
        str(fp / "*" / "bin" / "fragpipe.bat"), str(fp / "*" / "bin" / "FragPipe*.exe"),
        str(tmp_path / "Users" / "Daniel Nomura" / "Downloads" / "FragPipe*" / "fragpipe" / "bin" / "fragpipe.bat")))
    return {"new": new, "old": old, "fp": fp}


def test_find_fragpipe_picks_the_installed_24_not_the_old_copy_under_a_space(pc):
    assert fragpipe.detect_launcher() == pc["new"]
    cands = fragpipe.launcher_candidates()
    assert cands[0] == pc["new"] and cands[-1] == pc["old"]  # the unusable one is last, never chosen


def test_a_fragpipe_bat_beside_the_24_exe_is_preferred(pc, tmp_path):
    bat = _touch(pc["new"].parent / "fragpipe.bat")
    assert fragpipe.detect_launcher() == bat


def test_only_a_copy_under_a_space_means_nothing_found(pc):
    import shutil

    shutil.rmtree(pc["fp"])
    assert fragpipe.detect_launcher() is None


@pytest.mark.parametrize(("name", "person"), [
    ("Isaac", True), ("EJQ_2", True), ("QC", True), ("Taylor_FPG", True),
    ("FragPipe-23.1", False), ("FragPipe-jre-22.0", False), ("Fasta-files", False), ("New folder", False),
    ("Some One", False),
])
def test_folders_that_are_not_people(name, person):
    assert (not_a_user(name) is None) is person


def test_known_users_skips_them(lab):
    for n in ("FragPipe-23.1", "New folder", "Fasta-files"):
        (lab["general"] / n).mkdir()
    users = lab["cfg"].known_users()
    assert "FragPipe-23.1" not in users and "New folder" not in users and "EJQ" in users
    assert set(lab["cfg"].ignored_user_folders()) == {"FragPipe-23.1", "New folder", "Fasta-files"}


def test_checklist_names_the_path_with_a_space(lab):
    from ionomos import configio

    d = configio.read_config(lab["cfg_path"])
    d["paths"]["fragpipe_exe"] = "C:/Users/Daniel Nomura/Downloads/FragPipe-jre-22.0/fragpipe/bin/fragpipe.bat"
    item = next(i for i in setupcheck.run(lab["cfg_path"], d, probe_watcher=False) if i.key == "layout")
    assert "fragpipe_exe = C:/Users/Daniel Nomura" in item.detail


def test_config_saved_in_the_program_folder_moves_next_to_the_data(tmp_path, monkeypatch):
    from ionomos import configio

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    prog = tmp_path / "Ionomos"
    _touch(prog / "unins000.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(_touch(prog / "Ionomos.exe")))
    monkeypatch.setattr(service, "task_status", lambda: "missing")
    data = tmp_path / "Fragpipe_Auto"
    data.mkdir()
    cfg = prog / "config.yaml"
    configio.write_config(cfg, configio.defaults(str(data), str(tmp_path / "General")))
    configio.write_config(cfg, configio.defaults(str(data), str(tmp_path / "General")))  # makes a backup too
    moved = service.relocate_config_from_program_dir(cfg)
    assert moved == data / "config.yaml" and moved.is_file() and not cfg.exists()
    assert (data / configio.BACKUP_DIR).is_dir()
    assert service.remembered_config_path() == moved.resolve()
    assert service.relocate_config_from_program_dir(moved) is None  # already with the data: nothing to do
