import os
import subprocess
import sys
from pathlib import Path

import pytest

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


# ------------------------------------------------ dev install: git update + diagnostics ----


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def clone(tmp_path):
    """A bare 'origin' with one commit, and a clone of it; returns (origin, clone)."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "labwatch").mkdir()
    (seed / "labwatch" / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (seed / "a.txt").write_text("1\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "one")
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    return seed, origin, work


def test_updates_available_and_update_source(clone, monkeypatch):
    seed, origin, work = clone
    assert service.updates_available(work) == (0, "")
    assert "main @" in service.git_describe(work)

    # a new commit lands on origin
    (seed / "a.txt").write_text("2\n", encoding="utf-8")
    _git(seed, "commit", "-qam", "two")
    _git(seed, "push", "-q", str(origin), "main")
    n, _ = service.updates_available(work)
    assert n == 1

    # update pulls it and 'reinstalls' (pip stubbed out)
    calls = []
    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        if "pip" in cmd:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    log = []
    ok, msg = service.update_source(work, log=log.append)
    assert ok and "->" in msg, msg
    assert (work / "a.txt").read_text(encoding="utf-8") == "2\n"
    assert calls and "-e" in calls[0]
    assert any("two" in line for line in log)

    ok, msg = service.update_source(work, log=log.append)
    assert ok and msg == "already up to date"


def test_update_source_refuses_local_edits(clone):
    _, _, work = clone
    (work / "a.txt").write_text("edited on the PC\n", encoding="utf-8")
    ok, msg = service.update_source(work, log=lambda _: None)
    assert not ok and "local changes" in msg


def test_source_checkout_finds_repo_root():
    # the test suite itself runs from the checkout
    root = service.source_checkout()
    assert root is not None and (root / "labwatch" / "pyproject.toml").is_file()


def test_diagnostics_contains_everything(lab):
    cfg = lab["cfg_path"]
    log_dir = lab["auto"] / "logs"
    (log_dir / "labwatch.log").write_text("line1\nline2\n", encoding="utf-8")
    (lab["inbox"] / "X.REJECTED.txt").write_text("because\n", encoding="utf-8")
    text, where = service.save_diagnostics(cfg)
    for needle in ("labwatch ", "=== check", "=== status --all", "=== config.yaml", "line2", "=== inbox",
                   "X.REJECTED.txt", "because", "startup task:"):
        assert needle in text, needle
    assert where is not None and where.is_file() and where.name.startswith("diagnostics-")


def test_diagnostics_survives_missing_config(tmp_path):
    text, where = service.save_diagnostics(tmp_path / "nope.yaml")
    assert "exists=False" in text and where is None
