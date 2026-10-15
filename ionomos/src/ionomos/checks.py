"""
Check everything (docs/UI.md S3.1, `ionomos doctor`): one list made from every check Ionomos has, each row with
what is wrong and what to do. No Tk: the app builds it off the Tk thread and shows the rows; the CLI prints them.

    rows = everything(config_path)               # the saved config
    rows = everything(config_path, data=dict)    # with the app's unsaved edits (the setup part only)
    print(text(rows)); print(summary(rows))

Sources, in order (SOURCES), each on its own: one that breaks becomes a single ✗ row and the rest still run.
    setup       setupcheck.run: folders, layout, users, saved config, FragPipe found, each method, the library's
                last scan, disk, analysis, the watcher, the startup task
    fragpipe    preflight.run(dry=False, start=False): the installation read from disk, Java, RAM, threads,
                long paths, odd characters, each method's workflow, tools and FASTA. FragPipe is not started.
                Rows setup already has (the launcher, disk, spaces, write permission, a method's own set-up
                row) are left out
    library     library.update_check on the last scan: newer stock workflows, old or superseded FASTAs. A pinned
                file that is missing is left out (setup's method row says it); setup's "Workflows and FASTAs"
                row reports the scan's own flags, this source only what is newer
    health      crash reports of the last day, warnings and errors in the watcher's log, free disk (when setup
                had no disk row). The heartbeat is setup's "Watcher running" row
    attention   open items that need a person, and searches paused

Row.action names what the app can do in one click: create_folders | find_fragpipe | save | install_task |
start_watcher | open_tab:<uinav key> | open_path:<path> | resume_searches | show_attention | report_problem.
Every row that is not ok says what to do (D46), naming tabs by name.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

SOURCES = {"setup": "Setup", "fragpipe": "FragPipe", "library": "Workflows and FASTAs", "health": "Health",
           "attention": "Needs you"}
MARK = {"ok": "✓", "warn": "!", "fail": "✗", "todo": "○", "info": "·"}
PROBLEM = ("fail", "todo")

# the setup checklist's tab numbers -> uinav's tab keys
_TABS = {"0": "today", "1": "folders", "2": "users", "3": "methods", "4": "advanced", "5": "run"}
# preflight rows the setup checklist already has
_REPEATS = ("launcher", "install:launcher", "disk", "spaces")
# what to do when a row came without its own advice
_DEFAULT_FIX = {
    "setup": "Follow this line on the Setup tab; Help explains each one.",
    "fragpipe": "Open FragPipe's own window once (its Config tab) and check what this line names, then check again.",
    "library": "On the Methods tab, Find on this PC… shows each workflow and FASTA with what to do about it.",
    "health": "Open the log folder and read the newest file; Report a problem… sends it.",
    "attention": "Open Needs you and answer each item.",
}
LOG_RECENT = 24 * 3600  # a crash report younger than this is news


@dataclass
class Row:
    key: str        # unique, "<source>:<key>"
    source: str     # setup | fragpipe | library | health | attention
    status: str     # ok | todo | warn | fail | info
    title: str
    detail: str = ""
    fix: str = ""   # what to do, in words (D46: say what to do)
    action: str | None = None

    @property
    def problem(self) -> bool:
        return self.status in PROBLEM


def _tab_action(action: str | None) -> str | None:
    if action and action.startswith("open_tab:"):
        n = action.split(":", 1)[1]
        return f"open_tab:{_TABS.get(n, n)}"
    return action


def _saved_config(config_path: Path):
    """The saved config (paths not checked), or None when there is none or it does not load."""
    from ionomos.config import ConfigError, load

    if not config_path.is_file():
        return None
    try:
        return load(config_path, check_paths=False)
    except (ConfigError, OSError, ValueError):
        return None


def _wait_for_save(source: str, what: str) -> list[Row]:
    return [Row(f"{source}:saved", source, "info", f"{what} wait for a saved config",
                "there is no saved config, or it does not load (see Setup above)",
                "Press Save, then check again.", "save")]


# ---------------------------------------------------------------- sources --


def _setup(config_path: Path, data: dict | None, probe_watcher: bool) -> list[Row]:
    from ionomos import setupcheck

    return [Row(f"setup:{it.key}", "setup", it.status, it.title, it.detail, it.fix, _tab_action(it.action))
            for it in setupcheck.run(config_path, data, probe_watcher)]


def _preflight_action(key: str) -> str | None:
    if key in ("ram", "threads"):
        return "open_tab:advanced"
    if key in ("longpaths", "ascii"):
        return "open_tab:folders"
    if key.startswith("method:"):
        return "open_tab:methods"
    return None


def _fragpipe(cfg) -> list[Row]:
    from ionomos import preflight

    if cfg is None:
        return _wait_for_save("fragpipe", "The FragPipe checks")
    rows = []
    for c in preflight.run(cfg, dry=False, start=False):
        k = c.key
        if k in _REPEATS or k.startswith("write:") or (k.startswith("method:") and k.count(":") == 1):
            continue
        rows.append(Row(f"fragpipe:{k}", "fragpipe", c.status, c.title, c.detail, c.fix,
                        _preflight_action(k) if c.status != "ok" else None))
    return rows


_FLAG_TITLES = {"UPDATE_STOCK_NEWER": "a newer stock workflow", "UPDATE_FASTA_NEWER": "a newer FASTA",
                "FASTA_OLD": "an old FASTA"}
_SEVERITY = {"error": "fail", "warn": "warn", "info": "info"}


def _library(cfg) -> list[Row]:
    from ionomos import library

    if cfg is None:
        return _wait_for_save("library", "The workflow and FASTA checks")
    cat = library.load_catalog(cfg)
    scanned = bool(cat.scanned_at or cat.workflows or cat.fastas)
    flags = [f for f in library.update_check(cat, cfg) if f.code != "PINNED_MISSING"]
    rows = []
    for i, f in enumerate(flags):
        what = _FLAG_TITLES.get(f.code, f.code.replace("_", " ").lower())
        rows.append(Row(f"library:{f.code}:{f.subject or i}", "library", _SEVERITY.get(f.severity, "info"),
                        f"{f.subject}: {what}" if f.subject else what, f.text, f.fix, "open_tab:methods"))
    if rows:
        return rows
    if not scanned:
        return [Row("library:updates", "library", "info", "Newer workflows and FASTAs",
                    "nothing to compare with: this PC has not been searched for workflows and FASTAs",
                    "Optional: on the Methods tab, Find on this PC… looks (read-only) and remembers what it found.",
                    "open_tab:methods")]
    return [Row("library:updates", "library", "ok", "Newer workflows and FASTAs",
                f"nothing newer or old found (as of the scan of {cat.scanned_at.replace('T', ' ')[:16]})")]


def _health(log_dir: Path | None, users_root: Path | None, min_free_gb: float, have_disk: bool) -> list[Row]:
    from ionomos import health, names

    if log_dir is None:
        return _wait_for_save("health", "The health checks")
    rows = []
    crashes = health.recent_crashes(log_dir, 3)
    newest = None
    if crashes:
        try:
            newest = max(crashes, key=lambda p: p.stat().st_mtime)
            age = time.time() - newest.stat().st_mtime
        except OSError:
            age = LOG_RECENT + 1
        if newest is not None and age <= LOG_RECENT:
            rows.append(Row("health:crash", "health", "warn", "A crash in the last day", str(newest),
                            "Read the crash report in the log folder; Report a problem… sends it to whoever looks "
                            "after Ionomos.", f"open_path:{log_dir}"))
        else:
            rows.append(Row("health:crash", "health", "ok", "No crash in the last day", f"the last one: {newest}"))
    else:
        rows.append(Row("health:crash", "health", "ok", "No crash reports", str(log_dir)))

    log_file = names.log_file(log_dir)
    probs = health.log_problems(log_file)
    if probs:
        first = probs[0].split()
        since = " ".join(first[:2])[:19] if len(first) >= 2 else "?"
        last = probs[-1].splitlines()[0] if probs[-1] else ""
        n = len(probs)
        rows.append(Row("health:log", "health", "info",
                        f"{n} warning{'s' if n != 1 else ''}/error{'s' if n != 1 else ''} in the log since {since}",
                        last[:160],
                        "Most explain themselves; for one you don't understand, open the log or Report a problem….",
                        f"open_path:{log_file}"))
    else:
        rows.append(Row("health:log", "health", "ok", "No warnings or errors in the log", str(log_file)))

    if not have_disk and users_root is not None:
        free = health.disk_free_gb(users_root)
        if free is not None:
            low = free < max(min_free_gb, 1)
            rows.append(Row("health:disk", "health", "warn" if low else "ok", "Enough free disk space",
                            f"{free:.0f} GB free on the data drive",
                            "Free space on the data drive, or lower Min free disk on the Advanced tab." if low else "",
                            "open_tab:advanced" if low else None))
    return rows


def _attention(log_dir: Path | None) -> list[Row]:
    from ionomos import attention, worker

    if log_dir is None:
        return _wait_for_save("attention", "What needs you")
    rows = []
    open_items = attention.items(log_dir)
    if open_items:
        n = len(open_items)
        titles = "; ".join(i.title for i in open_items[:3]) + (f" … ({n})" if n > 3 else "")
        rows.append(Row("attention:items", "attention", "warn",
                        "1 thing needs you" if n == 1 else f"{n} things need you", titles,
                        "Open Needs you and answer each one.", "show_attention"))
    else:
        rows.append(Row("attention:items", "attention", "ok", "Nothing needs you"))
    if worker.paused(log_dir):
        rows.append(Row("attention:paused", "attention", "warn", "Searches are paused",
                        "queued jobs wait; new drops are still filed", "Jobs → Resume searches.", "resume_searches"))
    else:
        rows.append(Row("attention:paused", "attention", "ok", "Searches are not paused"))
    return rows


# -------------------------------------------------------------------- run --


def _error_row(source: str, exc: BaseException) -> Row:
    what = SOURCES.get(source, source)
    return Row(f"{source}:error", source, "fail", f"Could not run the {what} checks",
               f"could not run the {what} checks: {type(exc).__name__}: {exc}",
               "Check again; if it repeats, Report a problem… sends what is needed to fix it.", "report_problem")


def _guarded(source: str, fn) -> list[Row]:
    try:
        return list(fn())
    except Exception as exc:  # noqa: BLE001 - one source must never break the list
        return [_error_row(source, exc)]


def everything(config_path, data: dict | None = None, probe_watcher: bool = True) -> list[Row]:
    """Every check, as rows in SOURCES order. Never raises; reads the disk only (FragPipe is not started,
    nothing is scanned). `data` (the app's unsaved edits) feeds the setup checks; the other sources use the
    saved config."""
    try:
        config_path = Path(config_path)
        rows = _guarded("setup", lambda: _setup(config_path, data, probe_watcher))
        cfg = _saved_config(config_path)
        rows += _guarded("fragpipe", lambda: _fragpipe(cfg))
        rows += _guarded("library", lambda: _library(cfg))
        log_dir = users_root = None
        min_free = 0.0
        if cfg is not None:
            log_dir, users_root, min_free = Path(cfg.log_dir), Path(cfg.users_root), float(cfg.min_free_gb)
        elif isinstance(data, dict) and isinstance(data.get("paths"), dict):
            p = data["paths"]
            log_dir = Path(p["log_dir"]) if p.get("log_dir") else None
            users_root = Path(p["users_root"]) if p.get("users_root") else None
        have_disk = any(r.key == "setup:disk" for r in rows)
        rows += _guarded("health", lambda: _health(log_dir, users_root, min_free, have_disk))
        rows += _guarded("attention", lambda: _attention(log_dir))
    except Exception as exc:  # noqa: BLE001 - belt and braces: the list itself must not fail
        rows = [_error_row("setup", exc)]
    seen: dict[str, int] = {}
    for r in rows:
        if r.status != "ok" and not r.fix:
            r.fix = _DEFAULT_FIX.get(r.source, _DEFAULT_FIX["setup"])
        n = seen.get(r.key, 0)
        seen[r.key] = n + 1
        if n:
            r.key = f"{r.key}#{n + 1}"
    return rows


def problems(rows: list[Row]) -> list[Row]:
    """The rows that need doing (fail and todo)."""
    return [r for r in rows if r.status in PROBLEM]


def summary(rows: list[Row]) -> str:
    """'All 23 checks pass.' / '3 problems: Folders exist and are writable; …' (warnings named separately)."""
    probs = problems(rows)
    warns = [r for r in rows if r.status == "warn"]
    counted = [r for r in rows if r.status != "info"]

    def names(rs: list[Row]) -> str:
        return "; ".join(r.title for r in rs[:3]) + (" …" if len(rs) > 3 else "")

    look = f"{len(warns)} to look at" if warns else ""
    if probs:
        head = f"1 problem: {names(probs)}" if len(probs) == 1 else f"{len(probs)} problems: {names(probs)}"
        return head + (f" ({look})." if look else ".")
    if warns:
        return f"No problems; {look}: {names(warns)}."
    return f"All {len(counted)} checks pass."


def text(rows: list[Row]) -> str:
    """The rows for a terminal, grouped by source: ✓ ok, ! look at, ✗ broken, ○ to do, · for information."""
    lines: list[str] = []
    for source, title in SOURCES.items():
        mine = [r for r in rows if r.source == source]
        if not mine:
            continue
        if lines:
            lines.append("")
        lines.append(title)
        for r in mine:
            lines.append(f" {MARK.get(r.status, '?')} {r.title:<34} {r.detail}".rstrip())
            if r.fix and r.status != "ok":
                lines.append(f"   {'':<34} → {r.fix}")
    return "\n".join(lines)
