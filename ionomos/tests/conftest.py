"""Shared fixture: a fake C:\\Fragpipe_Auto + C:\\Fragpipe_General layout in a temp dir."""
import faulthandler
import gc
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("IONOMOS_OFFLINE", "1")  # the app must not git-fetch during tests
import yaml

from ionomos.config import load

# D73: the process must end soon after the last test. Something left waiting (a non-daemon thread, an atexit
# handler, a child it joins) would otherwise keep pytest alive after "N passed" with nothing more on screen until an
# outer time limit. A test that hangs is caught by faulthandler_timeout (pyproject.toml); this covers the exit.
EXIT_SECONDS = float(os.environ.get("IONOMOS_TEST_EXIT_SECONDS") or 120)
_stderr_fd: int | None = None


def pytest_configure(config):
    global _stderr_fd
    try:  # the real stderr (output capture is off here), kept open for the dump at exit
        _stderr_fd = os.dup(sys.__stderr__.fileno())
    except (AttributeError, OSError, ValueError):
        _stderr_fd = None


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if _stderr_fd is not None and EXIT_SECONDS > 0:
        faulthandler.dump_traceback_later(EXIT_SECONDS, exit=True, file=_stderr_fd)


@pytest.fixture
def lab(tmp_path: Path):
    auto = tmp_path / "Fragpipe_Auto"
    general = tmp_path / "Fragpipe_General"
    for d in (auto / "inbox", auto / "workflows", auto / "fasta", auto / "logs", general):
        d.mkdir(parents=True)
    for u in ("EJQ", "Isaac", "Chris", "Aman", "Taylor_Elements", "_unsorted"):
        (general / u).mkdir()
    (auto / "workflows" / "isoDTB.workflow").write_text("x")
    cfg = {
        "paths": {
            "inbox": str(auto / "inbox"),
            "users_root": str(general),
            "fragpipe_exe": str(auto / "fragpipe.exe"),  # absent -> warning only
            "workflow_dir": str(auto / "workflows"),
            "fasta_dir": str(auto / "fasta"),
            "database": str(auto / "ionomos.db"),
            "log_dir": str(auto / "logs"),
        },
        "watcher": {"poll_seconds": 0.01, "stable_seconds": 0.05, "min_raw_files": 1},
        "fragpipe": {"threads": 4, "ram_gb": 8, "timeout_minutes": 10, "min_free_gb": 0},
        "methods": {
            "isoDTB": {"workflow": "isoDTB.workflow", "fasta": "h.fas", "data_type": "DDA",
                       "postprocess": ["isodtb_sites"], "isodtb_mod_mass": "561.3387"},
            "TMT": {"workflow": "TMT10-MS3.workflow", "fasta": "h.fas", "data_type": "DDA"},
            "DIA": {"workflow": "DIA.workflow", "fasta": "h.fas", "data_type": "DIA"},
        },
        "users": {"aliases": {"Isaac": ["IJ", "IJD"], "EJQ": ["EJQ_2"]}},
    }
    cfg_path = auto / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    return {"root": tmp_path, "auto": auto, "general": general, "inbox": auto / "inbox",
            "cfg_path": cfg_path, "cfg_dict": cfg, "cfg": load(cfg_path)}


def make_drop(inbox: Path, name: str, raws: list[str], others: list[str] = (), raw_sub: bool = False):
    d = inbox / name
    d.mkdir()
    base = d / "raw" if raw_sub else d
    base.mkdir(exist_ok=True)
    for r in raws:
        (base / r).write_bytes(b"\0" * 64)
    for o in others:
        (d / o).write_text("note")
    return d


def iso_raws(reps=(1, 2, 3), fracs=range(1, 8), prefix="EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"):
    return [f"{prefix}_{r}_{f}.raw" for r in reps for f in fracs]


def gui_tests() -> tuple[bool, str]:
    """(ok, reason) for tests that open real Tk windows. They run in CI (the Windows runner has a
    display). On a dev machine they would cover the screen with windows while you work, so there
    they only run with IONOMOS_GUI_TESTS=1."""
    if not (os.environ.get("CI") or os.environ.get("IONOMOS_GUI_TESTS")):
        return False, "opens real windows; set IONOMOS_GUI_TESTS=1 to run it here (CI always does)"
    from ionomos.resolve import gui_available

    return gui_available()


def make_tk_root(attempts: int = 4, first_delay: float = 0.25):
    """Create a Tk root, retrying transient Tcl-init failures with escalating backoff.

    GitHub's Windows runners occasionally fail interpreter startup ("init.tcl read
    error") after many interpreters have lived in one process; the failure is
    transient — a plain rerun clears it. Try up to `attempts` times, sleeping
    first_delay, 2*first_delay, ... between tries (gc first, to release any
    interpreter debris), instead of letting one flake turn the CI run red.
    """
    import time
    import tkinter as tk

    attempts = max(1, attempts)
    delay = first_delay
    for attempt in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError:
            if attempt == attempts - 1:
                raise
            gc.collect()
            time.sleep(delay)
            delay *= 2


@pytest.fixture(autouse=True)
def _collect_garbage_on_the_main_thread():
    """Tk objects a test leaves behind are collected here, on the main thread, not later on a
    worker thread of another test (where Tcl would abort the process — see ionomos/tkutil.py)."""
    yield
    gc.collect()
