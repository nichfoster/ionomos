"""
Every file / system name Ionomos uses, and the LabWatch-era name it replaced.

The project was called LabWatch up to 0.4.0. Installs from then have
labwatch.json in every experiment folder, labwatch_run/ run folders,
logs/labwatch.log, a "labwatch" startup task, %APPDATA%\\labwatch\\config_path.txt
and possibly a still-running LabWatch watcher holding logs/labwatch.lock.
Readers accept both; writers use the new name and quietly rename our own old
status file on first write. Nothing the user made is renamed.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

APP = "Ionomos"
SLUG = "ionomos"

STATUS_FILE, LEGACY_STATUS_FILES = "ionomos.json", ("labwatch.json",)
RUN_DIR, LEGACY_RUN_DIRS = "ionomos_run", ("labwatch_run",)
LOG_FILE, LEGACY_LOG_FILES = "ionomos.log", ("labwatch.log",)
LOCK_FILE, LEGACY_LOCK_FILES = "ionomos.lock", ("labwatch.lock",)
PID_FILE, LEGACY_PID_FILES = "ionomos.pid", ("labwatch.pid",)
TASK, LEGACY_TASKS = "Ionomos", ("labwatch",)
APPDATA_DIR, LEGACY_APPDATA_DIRS = "Ionomos", ("labwatch",)
ENV_CONFIG, LEGACY_ENV_CONFIGS = "IONOMOS_CONFIG", ("LABWATCH_CONFIG",)
SHORTCUT, LEGACY_SHORTCUTS = "Ionomos.lnk", ("LabWatch.lnk",)
LEGACY_EXES = ("LabWatch.exe", "labwatch-cli.exe", "labwatch.exe")
REPO_URL = "https://github.com/nichfoster/ionomos"
RELEASES_URL = f"{REPO_URL}/releases/latest"


def _first_existing(base: Path, names: tuple[str, ...]) -> Path | None:
    return next((base / n for n in names if (base / n).exists()), None)


# ----------------------------------------------------------- experiment folders --


def status_path(dest: Path) -> Path:
    """The status file to READ: ionomos.json, else an old labwatch.json, else (missing) ionomos.json."""
    dest = Path(dest)
    new = dest / STATUS_FILE
    if new.exists():
        return new
    return _first_existing(dest, LEGACY_STATUS_FILES) or new


def adopt_legacy_status(dest: Path) -> None:
    """Before WRITING: rename our own old labwatch.json to ionomos.json (never two status files)."""
    dest = Path(dest)
    if (dest / STATUS_FILE).exists():
        return
    old = _first_existing(dest, LEGACY_STATUS_FILES)
    if old is not None:
        try:
            os.replace(old, dest / STATUS_FILE)
        except OSError:
            pass


def status_files(users_root: Path) -> Iterator[Path]:
    """Every experiment status file under users_root/<user>/<experiment>/ (new name preferred)."""
    root = Path(users_root)
    if not root.is_dir():
        return
    seen: set[Path] = set()
    for name in (STATUS_FILE, *LEGACY_STATUS_FILES):
        for p in root.glob(f"*/*/{name}"):
            if p.parent not in seen:
                seen.add(p.parent)
                yield p


def run_dir(dest: Path) -> Path:
    """Where the latest FragPipe run's inputs/console log are: ionomos_run/, or an old labwatch_run/."""
    dest = Path(dest)
    new = dest / RUN_DIR
    if new.exists():
        return new
    return _first_existing(dest, LEGACY_RUN_DIRS) or new


def console_log(dest: Path) -> Path:
    return run_dir(dest) / "fragpipe_console.log"


# ------------------------------------------------------------------ log dir --


def log_file(log_dir: Path) -> Path:
    """The watcher log to READ: whichever of ionomos.log / labwatch.log was written last."""
    log_dir = Path(log_dir)
    cands = [log_dir / n for n in (LOG_FILE, *LEGACY_LOG_FILES) if (log_dir / n).is_file()]
    if not cands:
        return log_dir / LOG_FILE
    return max(cands, key=lambda p: p.stat().st_mtime)


def lock_files(log_dir: Path) -> list[Path]:
    return [Path(log_dir) / n for n in (LOCK_FILE, *LEGACY_LOCK_FILES)]


def pid_files(log_dir: Path) -> list[Path]:
    return [Path(log_dir) / n for n in (PID_FILE, *LEGACY_PID_FILES)]


def config_env() -> str | None:
    for var in (ENV_CONFIG, *LEGACY_ENV_CONFIGS):
        if os.environ.get(var):
            return os.environ[var]
    return None

# Recoverable inbox removals and confirmed naming examples (introduced in 0.5.3).
REMOVED_DIR = ".removed"
NAMING_HISTORY_FILE = "naming-history.jsonl"

# The watcher log rotates by size (health.rotating_log_handler): ionomos.log, ionomos.log.1 ... .LOG_BACKUPS
LOG_MAX_BYTES = 5_000_000
LOG_BACKUPS = 5


def log_files(log_dir: Path) -> list[Path]:
    """The watcher log and its rotated copies (ionomos.log, ionomos.log.1, ...), old labwatch.log* too."""
    out: list[Path] = []
    for n in (LOG_FILE, *LEGACY_LOG_FILES):
        out += sorted(Path(log_dir).glob(f"{n}*"))
    return out


# Notifications (D58): which "held" messages were already sent, so a restart doesn't repeat them.
NOTIFY_STATE = "notify_state.json"

# Instrument QC trending (D45): the per-run metric store and the trend page, both in log_dir.
QC_TREND_STORE = "qc_trend.jsonl"
QC_TREND_PAGE = "qc_trend.html"

# The assistant's audit log (ROADMAP Phase 6, D49 / D57): append-only JSONL in the app-data folder.
ASSISTANT_AUDIT_FILE = "assistant-audit.jsonl"
# `ionomos ask-eval` (D72): its scorecards (<prefix>-<time>.json + .txt, app data) and the new folder its fixture
# states are built in (C:/ionomos-ask-eval/<time> on Windows: no spaces; elsewhere the temp folder).
ASSISTANT_SCORECARD_PREFIX = "assistant-scorecard"
ASSISTANT_EVAL_DIR = "ionomos-ask-eval"

# FragPipe hardening (D59). The run fingerprint: one small text file per finished search, in the job's run
# folder, for checking Ionomos' parsers against a real FragPipe. The preflight's own scratch folder, in log_dir.
FINGERPRINT_FILE = "run_fingerprint.json"
PREFLIGHT_DIR = "preflight"


# Fault handling (D69). While a search runs, its run folder holds which process Ionomos started (pid + the
# time the OS says it started), so an Ionomos killed mid-search can stop that FragPipe on its next start instead
# of running a second one beside it. Removed when the search ends.
ENGINE_PID_FILE = "engine_pid.json"
# The testbed's fake FragPipe (fake_fragpipe.py) acts out the fault named in this file in an experiment folder.
FAKE_FP_MODE_FILE = "fake_fragpipe_mode.txt"


def fingerprint_path(dest: Path) -> Path:
    """The latest search's fingerprint; earlier ones sit beside it as run_fingerprint_<time>.json."""
    return run_dir(dest) / FINGERPRINT_FILE


# The troubleshooting / validation bundle (bundle.py, D63): a zip a person copies off the PC, and its key file,
# which turns the pseudonyms back into the lab's names and stays in the lab.
BUNDLE_PREFIX = "Ionomos-bundle"
BUNDLE_KEY_SUFFIX = "-KEY-keep-in-the-lab-DO-NOT-SHARE.json"
BUNDLE_MANIFEST = "BUNDLE.json"
BUNDLE_README = "README.txt"
REPORT_PREFIX = "Ionomos-report"  # what "Report a problem" has always called its zip
# A search's "run fingerprint" (the versions and settings of that run), in the run folder when there is one.
RUN_FINGERPRINT = "run_fingerprint.json"
RUN_FINGERPRINT_GLOB = "run_fingerprint*.json"

# Where a simulated `ionomos benchmark` writes when no folder is given: ./ionomos_benchmark on the command line,
# <log_dir>/ionomos_benchmark from the app (accuracy.default_benchmark_dir, D67).
BENCHMARK_DIR = "ionomos_benchmark"
