"""The exe entry point must never die silently: crashes land in Ionomos-crash.txt."""
import os
import subprocess
import sys


def test_crash_is_reported_and_saved(tmp_path, monkeypatch):
    code = (
        "import sys, ionomos.cli as c\n"
        "c.main = lambda argv: 1/0\n"
        "sys.argv = ['ionomos', 'check']\n"
        "import runpy; runpy.run_module('ionomos', run_name='__main__')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 70
    assert "ZeroDivisionError" in r.stderr and "Ionomos crashed" in r.stderr
    assert "ZeroDivisionError" in (tmp_path / "Ionomos-crash.txt").read_text(encoding="utf-8")


def test_normal_exit_codes_pass_through(tmp_path):
    r = subprocess.run([sys.executable, "-m", "ionomos", "--config", str(tmp_path / "none.yaml"), "check"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "config file not found" in r.stdout
    assert not (tmp_path / "Ionomos-crash.txt").exists()
    r = subprocess.run([sys.executable, "-m", "ionomos", "--version"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "Ionomos" in r.stdout


def test_a_frozen_exe_makes_its_children_unpack_their_own_python(monkeypatch):
    """D81: an update's installer inherits the app's environment and starts the new Ionomos.exe after the app has
    exited; with PyInstaller's variables pointing at the app's deleted %TEMP%\\_MEI folder it failed with "Failed
    to load Python DLL". The frozen app (and the installer, for updates from older apps) resets them."""
    import sys
    from pathlib import Path

    from ionomos import __main__ as entry

    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    entry.independent_children()  # from source: nothing to do
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    entry.independent_children()
    assert os.environ["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    iss = (Path(__file__).resolve().parents[2] / "deploy" / "ionomos.iss").read_text(encoding="utf-8")
    setup = iss[iss.index("function InitializeSetup"):]
    assert "SetEnvironmentVariable('PYINSTALLER_RESET_ENVIRONMENT', '1')" in setup[:setup.index("end;")]
