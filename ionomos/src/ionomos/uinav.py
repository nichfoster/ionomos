"""
Finding your way around the app (docs/UI.md, Slice 1): the logic, without Tk.

    TABS / tab_help(key)           each tab's stable key and the help topic F1 opens on it
    SHORTCUTS / shortcuts_text()   the keyboard shortcuts, as the Help tab and help/faq.md list them
    load_state / save_state        the window's size and last tab, in <config folder>/ionomos-ui.json
    clamp_geometry / fit_size      a remembered or natural window size that fits this screen
    start_tab(...)                 which tab opens first
    job_matches(job, query)        the Jobs tab's Find box
    snapshot(log_dir, database)    what the Today tab shows: what needs a person, recent jobs (worker thread)

app.py only draws what these return; tests/test_ui_nav.py checks them everywhere, with no display.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ionomos import names

# Every tab's stable key (what ionomos-ui.json remembers; never its position, which can change), the name a
# person sees, and the help entry F1 opens there. Order = the order of the tab row.
TABS: tuple[tuple[str, str, str], ...] = (
    ("today", "Today", "faq.app"),
    ("setup", "✓ Setup", "faq.app"),
    ("folders", "1 Folders", "faq.app"),
    ("users", "2 Users", "faq.app"),
    ("methods", "3 Methods", "faq.library"),
    ("advanced", "4 Advanced", "faq.app"),
    ("run", "5 Run & Test", "trouble.watcher"),
    ("inbox", "Inbox", "start.drop"),
    ("jobs", "6 Jobs", "start.notes"),
    ("analysis", "7 Analysis", "faq.rerun"),
    ("notify", "8 Notifications", "faq.notify"),
    ("help", "Help", "faq.help"),
)
TAB_KEYS = tuple(k for k, _, _ in TABS)

# (Tk event, what to press, what it does). Bound on the main window only, so a pop-up keeps its own keys.
SHORTCUTS: tuple[tuple[str, str, str], ...] = (
    ("<F1>", "F1", "help for the tab you are on (the help page opens at that topic)"),
    ("<F5>", "F5", "refresh the tab you are on"),
    ("<Control-s>", "Ctrl+S", "Save (the same as the Save button)"),
    ("<Control-f>", "Ctrl+F", "find a job (the Jobs tab's Find box)"),
    ("<Control-Key-0>", "Ctrl+0", "the Today tab"),
    ("<Control-Key-1>", "Ctrl+1 … Ctrl+8", "the tab with that number (6 Jobs, 7 Analysis …)"),
    ("<Control-Tab>", "Ctrl+Tab, Ctrl+Shift+Tab", "the next / previous tab"),
)
# Ctrl+<n> -> the tab labelled <n>; Ctrl+0 -> Today.
DIGIT_TABS = {"0": "today", "1": "folders", "2": "users", "3": "methods", "4": "advanced", "5": "run", "6": "jobs",
              "7": "analysis", "8": "notify"}


def tab_help(key: str | None) -> str:
    """The help topic for a tab; the overview of the app for anything unknown."""
    return next((h for k, _, h in TABS if k == key), "faq.app")


def shortcuts_text() -> str:
    """The shortcuts as a plain-text block (the Help tab)."""
    w = max(len(keys) for _, keys, _ in SHORTCUTS)
    lines = ["KEYBOARD (in the main window)"]
    lines += [f"  {keys:<{w}}  {what}" for _, keys, what in SHORTCUTS]
    lines.append("  In a list: double-click or Enter opens the report; the Today tab lists what needs you.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------- window state --


def state_path(config_path: Path) -> Path:
    return Path(config_path).parent / names.UI_STATE_FILE


def load_state(path: Path) -> dict:
    """{'geometry': 'WxH+X+Y', 'zoomed': bool, 'tab': key}; {} when missing or unreadable (never raises)."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    out: dict = {}
    if isinstance(d.get("geometry"), str) and parse_geometry(d["geometry"]):
        out["geometry"] = d["geometry"]
    if isinstance(d.get("zoomed"), bool):
        out["zoomed"] = d["zoomed"]
    if d.get("tab") in TAB_KEYS:
        out["tab"] = d["tab"]
    return out


def save_state(path: Path, state: dict) -> bool:
    """Write atomically, only into a folder that exists (the config's; never created here). False on any error."""
    path = Path(path)
    if not path.parent.is_dir():
        return False
    keep = {k: state[k] for k in ("geometry", "zoomed", "tab") if k in state}
    try:
        fd, tmp = tempfile.mkstemp(prefix=".ui-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except (OSError, UnboundLocalError):
            pass
        return False


_GEOM = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")


def parse_geometry(geom: str) -> tuple[int, int, int, int] | None:
    """'1200x800+10+-8' -> (1200, 800, 10, -8), as Tk's wm geometry reports it; None for anything else."""
    m = _GEOM.match((geom or "").strip()) if isinstance(geom, str) else None
    if not m:
        return None
    w, h, x, y = (int(g) for g in m.groups())
    return (w, h, x, y) if w > 0 and h > 0 else None


def clamp_geometry(geom: str, screen_w: int, screen_h: int, min_w: int = 900, min_h: int = 640,
                   margin: int = 60) -> str | None:
    """A remembered geometry made to fit this screen: no wider or taller than it (less a margin for the taskbar),
    at least the minimum size where the screen allows it, and on screen (a window left on a second monitor that
    is gone comes back to the main one). None if the text is not a geometry."""
    g = parse_geometry(geom)
    if g is None or screen_w <= 0 or screen_h <= 0:
        return None
    w, h, x, y = g
    w, h = fit_size(w, h, screen_w, screen_h, min_w, min_h, margin)
    x = min(max(x, 0), max(screen_w - w, 0))
    y = min(max(y, 0), max(screen_h - margin - h, 0))
    return f"{w}x{h}+{x}+{y}"


def fit_size(w: int, h: int, screen_w: int, screen_h: int, min_w: int = 900, min_h: int = 640,
             margin: int = 60) -> tuple[int, int]:
    """(w, h) no bigger than the screen less the margin, and no smaller than the minimum unless the screen is."""
    max_w, max_h = max(screen_w - 16, 1), max(screen_h - margin, 1)
    return max(min(w, max_w), min(min_w, max_w)), max(min(h, max_h), min(min_h, max_h))


def start_tab(first_run: bool, remembered: str | None) -> str:
    """A new install opens on the setup checklist; afterwards the tab used last, else Today."""
    if first_run:
        return "setup"
    return remembered if remembered in TAB_KEYS else "today"


# --------------------------------------------------------------------- jobs --


def job_state(job) -> str:
    """What the Jobs tab calls a job: its status, or 'waiting' (held) or 'needs a look' (an analysis question)."""
    reason = getattr(job, "reason", None) or ""
    if job.status == "queued" and reason.startswith("waiting:"):
        return "waiting"
    if job.status == "done" and reason.startswith(("analysis needs", "analysis had a problem")):
        return "needs a look"
    return job.status


def job_matches(job, query: str) -> bool:
    """Every word of the query is in the job's number, status, user, method, experiment or reason (any case).
    '12' or '#12' also matches job 12 exactly; an empty query matches everything."""
    words = (query or "").lower().split()
    if not words:
        return True
    hay = " ".join(str(x) for x in (job_state(job), job.status, job.user, job.method, job.inbox_name,
                                     job.reason or "", job.dest_dir)).lower()  # the number is matched exactly
    for w in words:
        if w.lstrip("#").isdigit() and job.id == int(w.lstrip("#")):
            continue
        if w not in hay:
            return False
    return True


# -------------------------------------------------------------------- today --


@dataclass
class JobRow:
    id: int
    state: str
    user: str
    method: str
    experiment: str
    when: str
    note: str
    dest: str
    report: str | None   # results/report.html when it exists
    job: object = None   # the ledger Job, for the app's buttons


@dataclass
class Snapshot:
    attention: list = field(default_factory=list)       # open attention.Item, errors first
    jobs: list[JobRow] = field(default_factory=list)    # newest first
    counts: dict[str, int] = field(default_factory=dict)
    paused: bool = False
    problem: str = ""                                   # why something could not be read (shown, never raised)

    def headline(self) -> str:
        """One line: what needs a person, then the queue."""
        parts = []
        n = len(self.attention)
        parts.append(f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you" if n else "Nothing needs you")
        order = ("running", "queued", "waiting", "failed", "needs a look", "done")
        q = [f"{self.counts[k]} {k}" for k in order if self.counts.get(k)]
        parts.append(", ".join(q) if q else "no jobs yet")
        if self.paused:
            parts.append("searches PAUSED")
        return " · ".join(parts)


def _local(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def snapshot(log_dir: Path | None, database: str | Path | None, limit: int = 15) -> Snapshot:
    """Read what the Today tab shows. Touches files and the ledger, so it runs on a worker thread; never raises."""
    from ionomos import attention

    snap = Snapshot()
    try:
        snap.attention = attention.items(Path(log_dir)) if log_dir else []
    except Exception as exc:  # noqa: BLE001 - a broken item folder must not blank the tab
        snap.problem = f"could not read the attention list: {exc}"
    try:
        from ionomos.worker import paused

        snap.paused = bool(log_dir) and paused(Path(log_dir))
    except OSError:
        pass
    if not database:
        return snap
    try:
        from ionomos.ledger import Ledger, integrity

        if integrity(database) != "ok":
            return snap
        led = Ledger(database)
        try:
            jobs = led.list()
        finally:
            led.close()
    except Exception as exc:  # noqa: BLE001
        snap.problem = (snap.problem + "; " if snap.problem else "") + f"could not read the job list: {exc}"
        return snap
    for j in jobs:
        s = job_state(j)
        snap.counts[s] = snap.counts.get(s, 0) + 1
    for j in reversed(jobs[-limit:]):
        rep = Path(j.dest_dir) / "results" / "report.html"
        has_rep = rep.is_file()
        note = (j.reason or "").replace("\n", " ")[:140]
        if not note and has_rep:
            note = "report ready"
        snap.jobs.append(JobRow(j.id, job_state(j), j.user, j.method, j.inbox_name,
                                _local(j.finished_at or j.started_at or j.created_at), note, j.dest_dir,
                                str(rep) if has_rep else None, j))
    return snap
