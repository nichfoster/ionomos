"""The exe entry point must never die silently: crashes land in LabWatch-crash.txt."""
import subprocess
import sys


def test_crash_is_reported_and_saved(tmp_path, monkeypatch):
    code = (
        "import sys, labwatch.cli as c\n"
        "c.main = lambda argv: 1/0\n"
        "sys.argv = ['labwatch', 'check']\n"
        "import runpy; runpy.run_module('labwatch', run_name='__main__')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 70
    assert "ZeroDivisionError" in r.stderr and "LabWatch crashed" in r.stderr
    assert "ZeroDivisionError" in (tmp_path / "LabWatch-crash.txt").read_text(encoding="utf-8")


def test_normal_exit_codes_pass_through(tmp_path):
    r = subprocess.run([sys.executable, "-m", "labwatch", "--config", str(tmp_path / "none.yaml"), "check"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1 and "config file not found" in r.stdout
    assert not (tmp_path / "LabWatch-crash.txt").exists()
    r = subprocess.run([sys.executable, "-m", "labwatch", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and "labwatch" in r.stdout
