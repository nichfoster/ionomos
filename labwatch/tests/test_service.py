import os
import subprocess
import sys
from pathlib import Path

from labwatch import service


def test_config_path_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("LABWATCH_CONFIG", raising=False)
    assert service.remembered_config_path() is None
    service.remember_config_path(tmp_path / "x" / "config.yaml")
    assert service.remembered_config_path() == (tmp_path / "x" / "config.yaml").resolve()
    assert service.default_config_path() == (tmp_path / "x" / "config.yaml").resolve()
    monkeypatch.setenv("LABWATCH_CONFIG", "/env/c.yaml")
    assert service.default_config_path() == Path("/env/c.yaml")


def test_pid_file(tmp_path):
    assert service.running_pid(tmp_path) is None
    service.write_pid(tmp_path)
    assert service.running_pid(tmp_path) == os.getpid()
    service.clear_pid(tmp_path)
    assert service.running_pid(tmp_path) is None
    service.pid_file(tmp_path).write_text("999999999")  # dead pid -> cleaned up
    assert service.running_pid(tmp_path) is None
    assert not service.pid_file(tmp_path).exists()


def test_run_cli_and_start_stop(tmp_path):
    code, out = service.run_cli(["--version"])
    assert code == 0 and "labwatch" in out
    cmd = service.labwatch_command()
    assert cmd[0] == sys.executable and cmd[1:] == ["-m", "labwatch.cli"]
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    service.stop_process(proc, timeout=3)
    assert proc.poll() is not None


def test_task_status_off_windows():
    if os.name != "nt":
        assert service.task_status() == "n/a"
        assert service.install_task(Path("c.yaml"))[0] is False
