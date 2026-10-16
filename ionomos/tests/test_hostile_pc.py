"""health.py, service.py and updates.py on a PC that lacks things: no FragPipe, no disk space (or a drive that won't
say), no Task Scheduler, an app-data folder that can't be written, GitHub answering nonsense. Each still answers;
nothing raises into a check, a report or the start of a command."""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess

import pytest

from ionomos import health, names, service, setupcheck, updates
from ionomos.cli import main


class _WindowsOS:
    """The os module as a Windows PC shows it, for one module (never os.name itself: CLAUDE.md)."""
    name = "nt"

    def __getattr__(self, k):
        import os

        return getattr(os, k)


# ------------------------------------------------------------------------------------- Task Scheduler --


def _schtasks_says(monkeypatch, answer):
    """subprocess.run in service.py: `answer(argv)` -> returncode, or an exception to raise."""
    def run(argv, **kw):
        r = answer(argv)
        if isinstance(r, BaseException):
            raise r
        return subprocess.CompletedProcess(argv, r, "", "")

    monkeypatch.setattr(service, "os", _WindowsOS())
    monkeypatch.setattr(service.subprocess, "run", run)


def test_no_schtasks_on_the_pc_is_unavailable_not_a_crash(monkeypatch):
    _schtasks_says(monkeypatch, lambda argv: FileNotFoundError(2, "The system cannot find the file specified"))
    assert service.task_status() == "unavailable"
    ok, msg = service.install_task(service.Path("C:/Fragpipe_Auto/config.yaml"))
    assert not ok and "schtasks could not be run" in msg
    assert service.remove_task()[0] is False and service.run_task()[0] is False


def test_a_hung_task_scheduler_is_unavailable(monkeypatch):
    _schtasks_says(monkeypatch, lambda argv: subprocess.TimeoutExpired(argv, 30))
    assert service.task_status() == "unavailable"


def test_a_stopped_task_scheduler_service_is_unavailable_not_missing(monkeypatch):
    _schtasks_says(monkeypatch, lambda argv: 1)  # every query fails, whatever the message's language
    assert service.task_status() == "unavailable"


def test_a_working_scheduler_without_the_task_is_missing(monkeypatch):
    _schtasks_says(monkeypatch, lambda argv: 1 if "/TN" in argv else 0)
    assert service.task_status() == "missing"


def test_the_checklist_says_what_to_do_without_a_task_scheduler(lab, monkeypatch):
    monkeypatch.setattr(service, "task_status", lambda: "unavailable")
    monkeypatch.setattr(setupcheck, "os", _WindowsOS())
    task = {i.key: i for i in setupcheck.run(lab["cfg_path"], probe_watcher=False)}["task"]
    assert task.status == "warn" and "Task Scheduler" in task.detail and task.action is None and task.fix


def test_diagnostics_survive_a_pc_without_schtasks(lab, monkeypatch):
    real = subprocess.run

    def run(argv, **kw):
        if argv and argv[0] == "schtasks":
            raise FileNotFoundError(2, "no schtasks")
        return real(argv, **kw)

    monkeypatch.setattr(service, "os", _WindowsOS())
    monkeypatch.setattr(service.subprocess, "run", run)
    text = service.diagnostics(lab["cfg_path"])
    assert "startup task: unavailable" in text


# ------------------------------------------------------------------------------------------------ disk --


def test_free_space_of_a_drive_that_does_not_answer_is_none(tmp_path, monkeypatch):
    def refuse(p):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(shutil, "disk_usage", refuse)
    assert health.disk_free_gb(tmp_path) is None


def test_a_full_disk_is_said_by_check_and_the_checklist(lab, monkeypatch, capsys):
    monkeypatch.setattr(health, "disk_free_gb", lambda p: 0.2)
    assert main(["--config", str(lab["cfg_path"]), "check"]) in (0, 1)
    out = capsys.readouterr().out
    assert "below fragpipe.min_free_gb" in out or "0 GB free" in out
    disk = {i.key: i for i in setupcheck.run(lab["cfg_path"], probe_watcher=False)}["disk"]
    assert disk.status == "warn"


def test_memory_that_the_os_will_not_report_is_none(monkeypatch):
    monkeypatch.setattr(health, "os", _WindowsOS())
    import ctypes

    monkeypatch.setattr(ctypes, "windll", None, raising=False)
    assert health.memory_gb() == (None, None)


# ------------------------------------------------------------------------------------ no FragPipe at all --


def test_check_without_fragpipe_says_jobs_wait_and_does_not_fail(lab, capsys):
    assert not lab["cfg"].fragpipe_exe.exists()
    main(["--config", str(lab["cfg_path"]), "check"])
    out = capsys.readouterr().out
    assert "jobs wait until it's set" in out


# ----------------------------------------------------------------------- the lock and the app-data folder --


def test_the_lock_file_name_comes_from_names(tmp_path):
    assert health.LOCK_NAME == names.LOCK_FILE
    lock = health.InstanceLock(tmp_path)
    assert lock.path.name == names.LOCK_FILE
    with lock:
        assert health.is_locked(tmp_path)
    assert not health.is_locked(tmp_path)


def test_a_command_starts_when_the_app_data_folder_cannot_be_made(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "appdata"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(blocker))
    monkeypatch.setenv("HOME", str(blocker))
    monkeypatch.delenv("IONOMOS_CONFIG", raising=False)
    monkeypatch.delenv("LABWATCH_CONFIG", raising=False)
    assert main(["--version"]) == 0
    assert "Ionomos" in capsys.readouterr().out


def test_a_pointer_file_that_is_not_text_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    d = service.appdata_dir()
    (d / "config_path.txt").write_bytes(b"\xff\xfe\x00garbage\x97")
    assert service.remembered_config_path() is None
    service.remember_config_path(tmp_path / "c.yaml")
    assert service.remembered_config_path() == (tmp_path / "c.yaml").resolve()


# --------------------------------------------------------------------------------------- update checks --


class _Resp:
    def __init__(self, body=b"", exc=None):
        self.body, self.exc = body, exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        if self.exc:
            raise self.exc
        b, self.body = self.body, b""
        return b


@pytest.mark.parametrize("answer", [
    b"[]", b'"a proxy said no"', b"null", b'{"assets": "none"}', b'{"assets": [42]}',
    json.dumps({"tag_name": "v9", "assets": [{"name": "Ionomos-Setup-9.0.0.exe"}]}).encode(),  # no download url
    json.dumps({"tag_name": "v9", "assets": [{"name": "Ionomos-Setup-9.0.0.exe", "browser_download_url": "x",
                                               "size": "big"}]}).encode(),
    b"<html>captive portal</html>",
])
def test_an_answer_of_another_shape_is_no_update_never_a_crash(monkeypatch, answer):
    import urllib.request

    monkeypatch.delenv("IONOMOS_OFFLINE", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _Resp(answer))
    rel, why = updates.check_latest()
    assert rel is None and why


def test_a_connection_cut_mid_answer_is_no_update(monkeypatch):
    import urllib.request

    monkeypatch.delenv("IONOMOS_OFFLINE", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp(exc=http.client.IncompleteRead(b"{", 100)))
    rel, why = updates.check_latest()
    assert rel is None and "could not reach GitHub" in why


def test_a_download_cut_mid_file_is_a_download_error_and_leaves_nothing(tmp_path, monkeypatch):
    import urllib.request

    rel = updates.Release("9.9.9", "", "Ionomos-Setup-9.9.9.exe", "https://x/s.exe", 10, "0" * 64)  # no SHA-256, no download
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp(exc=http.client.IncompleteRead(b"MZ", 10)))
    with pytest.raises(updates.DownloadError, match="download failed"):
        updates.download(rel, tmp_path)
    assert not list(tmp_path.iterdir())


def test_a_downloads_folder_that_cannot_be_made_is_a_download_error(tmp_path):
    blocker = tmp_path / "Downloads"
    blocker.write_text("a file", encoding="utf-8")
    rel = updates.Release("9.9.9", "", "Ionomos-Setup-9.9.9.exe", "https://x/s.exe", 10, "0" * 64)  # no SHA-256, no download
    with pytest.raises(updates.DownloadError, match="cannot save into"):
        updates.download(rel, blocker)
