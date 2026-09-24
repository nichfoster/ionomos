"""
Things that need a person: a durable queue the GUIs turn into pop-up windows.

Anything in Ionomos that can't sensibly decide on its own (or that went wrong in
a way a person must see) raises an item here instead of only writing a log line:

    raise_item(log_dir, kind, title, message, key=..., dest=..., job_id=..., causes=[...], ...)

    kind                  when                                              window offers
    analysis_input        the analysis ran on a guess or couldn't compare  sample/condition editor, Run analysis
                          (one condition, no control, a group of 1, ...)
    analysis_failed       no result table, analysis crashed, no volcano     likely causes, Re-run, open folder, report
    search_failed         FragPipe failed                                   likely causes, log tail, Retry, open log
    search_waiting        a search is held (FASTA / workflow / disk ...)    what's missing, open the right tab
    intake_rejected       a dropped folder couldn't be taken in             the note, open inbox

Items are JSON files in <log_dir>/attention/, so they survive restarts and are
seen by every process: the watcher's own window (when the app isn't open), the
app (a "⚠ needs attention" button + pop-ups), and `ionomos attention`. The same
key raised again updates the item instead of piling up duplicates; resolving
the underlying problem (Retry, re-analysis without issues) closes it.
Writes are atomic (temp file + replace); a corrupt item file is ignored.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger("ionomos.attention")

DIR = "attention"
APP_ALIVE = "app.alive"  # the app touches this while open; the watcher then leaves pop-ups to it
KINDS = ("analysis_input", "analysis_failed", "search_failed", "search_waiting", "intake_rejected")
POPUP_KINDS = {"analysis_input", "analysis_failed", "search_failed", "search_waiting", "intake_rejected"}


@dataclass
class Item:
    id: str
    kind: str
    title: str
    message: str
    key: str = ""
    severity: str = "input"          # input (a decision) | error | warning
    dest: str | None = None          # experiment folder
    job_id: int | None = None
    causes: list[str] = field(default_factory=list)   # likely causes, most likely first
    fixes: list[str] = field(default_factory=list)    # what to do, in plain words
    details: str = ""                # log tail, traceback, ...
    data: dict = field(default_factory=dict)          # kind-specific (issue codes, samples, ...)
    created: str = ""
    updated: str = ""
    state: str = "open"              # open | resolved | dismissed
    shown: int = 0                   # how many times a window was opened for it
    snooze_until: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def due(self, now: float | None = None) -> bool:
        return self.is_open and (now or time.time()) >= self.snooze_until


def folder(log_dir: Path) -> Path:
    return Path(log_dir) / DIR


def _slug(key: str) -> str:
    """A readable, unique file name for a key: its start + a hash of all of it (paths share long prefixes)."""
    import hashlib

    if not key:
        return uuid.uuid4().hex[:12]
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", key.split(":", 1)[0] + "_" + Path(key.split(":", 1)[-1]).name)
    return f"{readable[:48]}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write(path: Path, item: Item) -> None:
    """Atomic write. Windows refuses a replace while another process has the file open for a moment
    (the app polling it), so retry briefly before giving up."""
    import threading

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(asdict(item), indent=2, default=str), encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 5:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def _read(path: Path) -> Item | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        known = set(Item.__dataclass_fields__)
        return Item(**{k: v for k, v in d.items() if k in known})
    except (OSError, ValueError, TypeError):
        return None


def raise_item(log_dir: Path | None, kind: str, title: str, message: str, *, key: str = "", severity: str = "input",
               dest: Path | str | None = None, job_id: int | None = None, causes: list[str] | None = None,
               fixes: list[str] | None = None, details: str = "", data: dict | None = None) -> Item | None:
    """Create or refresh an item. Never raises (a full disk must not stop a search)."""
    if log_dir is None:
        return None
    key = key or f"{kind}:{job_id or dest or title}"
    path = folder(log_dir) / f"{_slug(key)}.json"
    try:
        old = _read(path) if path.is_file() else None
        now = _now()
        if old is not None and old.is_open:
            item = old
            same = (old.title, old.message, old.causes) == (title, message, causes or [])
            item.title, item.message, item.severity = title, message, severity
            item.causes, item.fixes, item.details = list(causes or []), list(fixes or []), details
            item.data = dict(data or {})
            item.updated = now
            if not same:
                item.shown = 0  # something new to say: pop up again
                item.snooze_until = 0.0
        else:
            item = Item(id=_slug(key), kind=kind, title=title, message=message, key=key, severity=severity,
                        dest=str(dest) if dest else None, job_id=job_id, causes=list(causes or []),
                        fixes=list(fixes or []), details=details, data=dict(data or {}), created=now, updated=now)
        item.kind = kind
        item.dest = str(dest) if dest else item.dest
        item.job_id = job_id if job_id is not None else item.job_id
        _write(path, item)
        log.warning("needs attention [%s] %s: %s", kind, title, message)
        return item
    except OSError as exc:
        log.error("could not record an attention item (%s): %s", exc, title)
        return None


def items(log_dir: Path | None, open_only: bool = True) -> list[Item]:
    if log_dir is None:
        return []
    d = folder(log_dir)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        it = _read(p)
        if it is not None and (it.is_open or not open_only):
            out.append(it)
    out.sort(key=lambda i: (i.severity != "error", i.created))
    return out


def get(log_dir: Path, item_id: str) -> Item | None:
    p = folder(log_dir) / f"{item_id}.json"
    return _read(p) if p.is_file() else None


def _set(log_dir: Path, item_id: str, **changes) -> bool:
    p = folder(log_dir) / f"{item_id}.json"
    it = _read(p) if p.is_file() else None
    if it is None:
        return False
    for k, v in changes.items():
        setattr(it, k, v)
    it.updated = _now()
    try:
        _write(p, it)
    except OSError:
        return False
    return True


def resolve(log_dir: Path | None, item_id: str) -> bool:
    return bool(log_dir) and _set(log_dir, item_id, state="resolved")


def dismiss(log_dir: Path | None, item_id: str) -> bool:
    return bool(log_dir) and _set(log_dir, item_id, state="dismissed")


def snooze(log_dir: Path, item_id: str, minutes: float = 60) -> bool:
    return _set(log_dir, item_id, snooze_until=time.time() + minutes * 60)


def mark_shown(log_dir: Path, item_id: str) -> None:
    it = get(log_dir, item_id)
    if it is not None:
        _set(log_dir, item_id, shown=it.shown + 1)


def resolve_where(log_dir: Path | None, *, kind: str | None = None, job_id: int | None = None,
                  dest: Path | str | None = None) -> int:
    """Close open items matching all given fields (e.g. a retried job's search_failed). Returns how many."""
    n = 0
    for it in items(log_dir):
        if kind is not None and it.kind != kind:
            continue
        if job_id is not None and it.job_id != job_id:
            continue
        if dest is not None and (it.dest is None or Path(it.dest) != Path(dest)):
            continue
        if resolve(log_dir, it.id):
            n += 1
    return n


def purge(log_dir: Path, older_than_days: float = 30) -> int:
    """Delete closed items older than N days (our own bookkeeping files only)."""
    n = 0
    cutoff = time.time() - older_than_days * 86400
    for p in folder(log_dir).glob("*.json") if folder(log_dir).is_dir() else []:
        it = _read(p)
        if it is not None and not it.is_open:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except OSError:
                pass
    return n


# ---------------------------------------------------------- who shows them --


def touch_app_alive(log_dir: Path) -> None:
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        (Path(log_dir) / APP_ALIVE).write_text(f"{os.getpid()} {_now()}\n", encoding="utf-8")
    except OSError:
        pass


def app_is_open(log_dir: Path, within: float = 30) -> bool:
    try:
        return time.time() - (Path(log_dir) / APP_ALIVE).stat().st_mtime < within
    except OSError:
        return False


def clear_app_alive(log_dir: Path) -> None:
    try:
        (Path(log_dir) / APP_ALIVE).unlink(missing_ok=True)
    except OSError:
        pass
