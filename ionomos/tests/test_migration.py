"""LabWatch (<= 0.4.0) -> Ionomos: an existing install keeps working after the rename.
Also: updates found in Downloads, problem reports, build info."""
import json
import zipfile
from pathlib import Path

import pytest

from ionomos import health, names, service, testbed, updates
from ionomos import ledger as ledger_mod
from ionomos.config import load
from ionomos.intake import intake, write_status
from ionomos.ledger import Ledger
from ionomos.worker import Worker


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": Ledger(cfg.database)}


def _make_labwatch_experiment(bed) -> Path:
    """Queue one job, then turn its folder into what LabWatch 0.4 left behind."""
    folder = testbed.drop(bed["root"], "iso_good")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    dest = Path(bed["ledger"].list()[-1].dest_dir)
    (dest / names.STATUS_FILE).rename(dest / "labwatch.json")
    (dest / "labwatch_run").mkdir()
    (dest / "labwatch_run" / "fragpipe_console.log").write_text("MSFragger [Work dir: x]\n", encoding="utf-8")
    return dest


def test_old_experiment_folders_are_read_and_migrated_on_write(bed):
    dest = _make_labwatch_experiment(bed)
    assert names.status_path(dest).name == "labwatch.json"
    assert names.console_log(dest).parent.name == "labwatch_run"
    write_status(dest, {"status": "queued"})
    assert (dest / "ionomos.json").is_file() and not (dest / "labwatch.json").exists()  # renamed, never two


def test_old_queued_job_runs_to_done_under_ionomos(bed):
    dest = _make_labwatch_experiment(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done"
    assert (dest / "ionomos.json").is_file() and not (dest / "labwatch.json").exists()
    assert (dest / "labwatch_run").is_dir() and (dest / "ionomos_run" / "fragpipe_console.log").is_file()
    assert names.console_log(dest).parent.name == "ionomos_run"  # the newest run wins
    assert (dest / "results" / "report.html").is_file()


def test_ledger_rebuild_and_orphan_adoption_see_old_status_files(bed):
    _make_labwatch_experiment(bed)
    bed["ledger"].close()
    bed["cfg"].database.write_bytes(b"broken")
    _moved, n = ledger_mod.rebuild_from_status_files(bed["cfg"].database, bed["cfg"].users_root)
    assert n == 1
    fresh = Ledger(bed["cfg"].database)
    fresh._conn.execute("DELETE FROM jobs")
    fresh._conn.commit()
    assert ledger_mod.adopt_orphans(fresh, bed["cfg"].users_root) == [2]


def test_old_config_pointer_and_env_var_are_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("IONOMOS_CONFIG", raising=False)
    monkeypatch.delenv("LABWATCH_CONFIG", raising=False)
    legacy_dir = (tmp_path / "labwatch") if service.os.name == "nt" else (tmp_path / ".config" / "labwatch")
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config_path.txt").write_text("/lab/Fragpipe_Auto/config.yaml", encoding="utf-8")
    assert service.default_config_path() == Path("/lab/Fragpipe_Auto/config.yaml")
    service.remember_config_path(tmp_path / "new" / "config.yaml")  # Ionomos's own pointer wins from now on
    assert service.default_config_path() == (tmp_path / "new" / "config.yaml").resolve()
    monkeypatch.setenv("LABWATCH_CONFIG", "/env/old.yaml")
    assert service.default_config_path() == Path("/env/old.yaml")
    monkeypatch.setenv("IONOMOS_CONFIG", "/env/new.yaml")
    assert service.default_config_path() == Path("/env/new.yaml")


def test_a_running_labwatch_watcher_blocks_a_second_watcher(tmp_path):
    old = health.InstanceLock(tmp_path)
    old.path = tmp_path / "labwatch.lock"  # what a LabWatch 0.4 watcher holds
    old.acquire()
    try:
        assert health.is_locked(tmp_path)
        with pytest.raises(health.AlreadyRunning, match="LabWatch"):
            health.InstanceLock(tmp_path).acquire()
    finally:
        old.release()
    with health.InstanceLock(tmp_path):
        pass


def test_log_file_reader_prefers_the_newest(tmp_path):
    import os
    import time

    (tmp_path / "labwatch.log").write_text("old\n", encoding="utf-8")
    assert names.log_file(tmp_path).name == "labwatch.log"
    (tmp_path / "ionomos.log").write_text("new\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(tmp_path / "ionomos.log", (later, later))
    assert names.log_file(tmp_path).name == "ionomos.log"


# ------------------------------------------------------------------- updates --


def test_find_downloaded_installer(tmp_path):
    for n in ("Ionomos-Setup-0.4.9.exe", "Ionomos-Setup-0.5.2.exe", "Ionomos-Setup-0.5.10 (1).exe",
              "Ionomos-Setup-0.6.0.exe.crdownload", "LabWatch-0.9.zip", "ionomos-setup-0.5.3.EXE"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    path, v = updates.find_downloaded_installer(tmp_path, current="0.5.0")
    assert v == "0.5.10" and path.name == "Ionomos-Setup-0.5.10 (1).exe"  # numeric, not text, comparison
    assert updates.find_downloaded_installer(tmp_path, current="0.5.10") is None
    assert updates.find_downloaded_installer(tmp_path / "nope") is None
    assert updates.parse_version("0.10.1") > updates.parse_version("0.9.9")


def test_restart_flag_round_trip(tmp_path):
    assert not updates.take_restart_flag(tmp_path)
    (tmp_path / updates.RESTART_FLAG).write_text("x", encoding="utf-8")
    assert updates.take_restart_flag(tmp_path) and not updates.take_restart_flag(tmp_path)


# ------------------------------------------------------ reports + build info --


def test_problem_report_has_the_note_build_and_app_log(bed, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (service.appdata_dir() / "app.log").write_text("app started\n", encoding="utf-8")
    z = service.save_problem_report(bed["cfg_path"], "dropped EJQ at 3pm, nothing moved", dest_dir=tmp_path)
    assert z.name.startswith("Ionomos-report-") and z.name.endswith(".zip")
    zf = zipfile.ZipFile(z)
    names_in = zf.namelist()
    assert "note.txt" in names_in and "build.json" in names_in and "logs/app/app.log" in names_in
    assert "nothing moved" in zf.read("note.txt").decode()
    assert json.loads(zf.read("build.json"))["version"]


def test_detailed_logging_switch_expires(tmp_path):
    from datetime import datetime, timedelta

    until = health.set_debug(tmp_path, hours=1)
    assert health.debug_until(tmp_path) == until
    (tmp_path / health.DEBUG_FILE).write_text((datetime.now() - timedelta(minutes=1)).isoformat(), encoding="utf-8")
    assert health.debug_until(tmp_path) is None and not (tmp_path / health.DEBUG_FILE).exists()


def test_version_line_names_the_build(capsys):
    from ionomos.cli import main

    assert main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Ionomos ") and "git checkout" in out  # the test suite runs from the checkout


def test_stop_command_without_a_watcher(bed, capsys):
    from ionomos.cli import main

    assert main(["--config", str(bed["cfg_path"]), "stop"]) == 0
    assert "no watcher running" in capsys.readouterr().out
