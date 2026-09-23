"""
The setup checklist: everything a working install needs, each item with a
status and what to do about it. Shared by the app's Setup tab and `ionomos init`.

    items = run(config_path)                  # the saved config
    items = run(config_path, data=dict)       # unsaved edits from the app
    for it in items: it.status in ("ok", "todo", "warn", "fail"), it.fix, it.action

`action` names something the app can do in one click (create_folders,
find_fragpipe, save, install_task, start_watcher, open_tab:<n>).
"""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Item:
    key: str
    title: str
    status: str  # ok | todo | warn | fail
    detail: str = ""
    fix: str = ""
    action: str | None = None

    @property
    def done(self) -> bool:
        return self.status == "ok"


def writable(folder: Path) -> tuple[bool, str]:
    """Create and delete a probe file (ionomos's own, never user data)."""
    if not folder.is_dir():
        return False, "does not exist"
    probe = folder / f".ionomos-write-test-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return False, f"not writable ({exc.strerror or exc})"
    return True, ""


def _drive(p: Path) -> str:
    if os.name == "nt":
        return (Path(p).drive or "").upper()
    try:
        return str(os.stat(next(q for q in (Path(p), *Path(p).parents) if q.exists())).st_dev)
    except (OSError, StopIteration):
        return ""


def run(config_path: Path, data: dict | None = None, probe_watcher: bool = True) -> list[Item]:
    from ionomos import configio, fragpipe, health, service
    from ionomos.config import ConfigError, layout_problems, load

    config_path = Path(config_path)
    d = data if data is not None else configio.read_config(config_path)
    p = {k: Path(v) for k, v in d["paths"].items() if v}
    items: list[Item] = []
    add = items.append

    # 1. folders exist and are writable
    missing, unwritable = [], []
    for key in ("inbox", "users_root", "workflow_dir", "fasta_dir", "log_dir"):
        f = p.get(key)
        if f is None:
            missing.append(key)
            continue
        ok, why = writable(f)
        if not ok:
            (missing if why == "does not exist" else unwritable).append(f"{key} ({f}: {why})" if why != "does not exist" else key)
    if unwritable:
        add(Item("folders", "Folders exist and are writable", "fail", "; ".join(unwritable),
                 "Pick folders this Windows account can write to (tab 1).", "open_tab:1"))
    elif missing:
        add(Item("folders", "Folders exist and are writable", "todo", "missing: " + ", ".join(missing),
                 "One click creates them.", "create_folders"))
    else:
        add(Item("folders", "Folders exist and are writable", "ok", str(p.get("inbox", ""))))

    # 2. layout is safe
    probs = layout_problems(p.get("inbox", Path(".")), p.get("users_root", Path(".")),
                            p.get("log_dir", Path(".")), p.get("database", Path("x")))
    warn = []
    spaces = [k for k, v in d["paths"].items() if " " in str(v)]
    if spaces:
        (probs if os.name == "nt" else warn).append("spaces in " + ", ".join(spaces) + " (FragPipe can't handle them)")
    if "inbox" in p and "users_root" in p and p["inbox"].exists() and p["users_root"].exists() \
            and _drive(p["inbox"]) != _drive(p["users_root"]):
        warn.append("inbox and users folder are on different drives: every drop is copied instead of moved "
                    "(slow for big experiments, needs double the space while copying)")
    if any(not str(v).isascii() for v in d["paths"].values()):
        warn.append("non-English characters in a path; some FragPipe tools may not cope")
    if probs:
        add(Item("layout", "Folder layout is safe", "fail", " ".join(probs), "Fix the paths on tab 1.", "open_tab:1"))
    elif warn:
        add(Item("layout", "Folder layout is safe", "warn", " ".join(warn), "Recommended: fix on tab 1.", "open_tab:1"))
    else:
        add(Item("layout", "Folder layout is safe", "ok", "inbox and users folder are separate, same drive"))

    # 3. users
    ur = p.get("users_root")
    users = sorted(x.name for x in ur.iterdir() if x.is_dir() and not x.name.startswith(".")) if ur and ur.is_dir() else []
    stale = [u for u in (d["users"].get("aliases") or {}) if u not in users]
    if not users:
        add(Item("users", "Lab members added", "todo", "no user folders yet", "Add each person on tab 2.", "open_tab:2"))
    elif stale:
        add(Item("users", "Lab members added", "warn", f"{len(users)} user(s); initials set for missing folder(s): "
                 + ", ".join(stale), "Add or remove those on tab 2.", "open_tab:2"))
    else:
        n_alias = sum(1 for u in users if (d["users"].get("aliases") or {}).get(u))
        add(Item("users", "Lab members added", "ok", f"{len(users)} user(s), {n_alias} with initials"))

    # 4. config saved + valid
    saved = config_path.is_file()
    cfg = None
    if not saved:
        add(Item("config", "Settings saved", "todo", f"{config_path} doesn't exist yet", "Press Save.", "save"))
    else:
        try:
            cfg = load(config_path, check_paths=True)
            add(Item("config", "Settings saved", "ok", str(config_path)))
        except ConfigError as exc:
            add(Item("config", "Settings saved", "fail", str(exc).replace("\n", " "), "Fix it, then Save.", "save"))
            try:
                cfg = load(config_path, check_paths=False)
            except ConfigError:
                cfg = None

    # 5. FragPipe
    if cfg is not None:
        report = {label: (ok, detail) for ok, label, detail in fragpipe.install_report(cfg)}
        ok, detail = report.get("launcher", (False, "?"))
        if not ok:
            add(Item("fragpipe", "FragPipe found", "todo", detail, "Find FragPipe (or Browse… on tab 1), then Save.",
                     "find_fragpipe"))
        else:
            mf = report.get("MSFragger")
            if mf and mf[0] is not True and "not in a standard" not in str(report.get("install folder", ("", ""))[1]):
                add(Item("fragpipe", "FragPipe found", "warn", f"{detail}; MSFragger: {mf[1]}",
                         "Open the FragPipe GUI once: Config tab -> download MSFragger/IonQuant.", None))
            else:
                add(Item("fragpipe", "FragPipe found", "ok", detail))
        # 6. methods
        for key in cfg.methods:
            lines = fragpipe.describe_method(cfg, key)
            worst = "fail" if any(o is False for o, _ in lines) else ("warn" if any(o is None for o, _ in lines) else "ok")
            status = {"fail": "todo", "warn": "warn", "ok": "ok"}[worst]
            add(Item(f"method:{key}", f"{key}: workflow + FASTA ready", status, "; ".join(t for _, t in lines),
                     "Tab 3: select the method -> Import workflow… (from a run that worked)." if status != "ok" else "",
                     "open_tab:3" if status != "ok" else None))
        # 7. disk
        free = health.disk_free_gb(cfg.users_root)
        if free is not None:
            low = free < max(cfg.min_free_gb, 1)
            add(Item("disk", "Enough free disk space", "warn" if low else "ok", f"{free:.0f} GB free on the data drive",
                     "Free space or lower 'Min free disk' in Advanced." if low else ""))
        # 8. analysis
        add(Item("analysis", "Analysis settings", "ok",
                 "on: statistics, volcano plots, report after each search" if cfg.analysis.get("enabled", True)
                 else "off: only FragPipe output + the lab's R-script tables", ""))
        # 9. watcher
        if probe_watcher:
            running = health.is_locked(cfg.log_dir)
            hb, healthy = health.heartbeat_summary(cfg.log_dir)
            if running and healthy:
                add(Item("watcher", "Watcher running", "ok", hb))
            elif running:
                add(Item("watcher", "Watcher running", "warn", f"running but {hb}", "Stop and start it (tab 5).",
                         "open_tab:5"))
            else:
                add(Item("watcher", "Watcher running", "todo", "not running", "Start watcher.", "start_watcher"))
        # 10. startup task (Windows)
        if os.name == "nt" or sys.platform == "win32":
            st = service.task_status()
            detail = {"installed": "installed", "legacy": "the old LabWatch task is still registered",
                      "missing": "not installed"}.get(st, st)
            add(Item("task", "Starts automatically at logon", "ok" if st == "installed" else "todo", detail,
                     "" if st == "installed" else "Install the startup task (replaces the LabWatch one).",
                     None if st == "installed" else "install_task"))
    return items


def summary(items: list[Item]) -> str:
    done = sum(1 for i in items if i.done)
    blocking = [i for i in items if i.status in ("fail", "todo")]
    if not blocking:
        return f"All {len(items)} checks pass — Ionomos is ready."
    return f"{done} of {len(items)} ready. Next: {blocking[0].title} — {blocking[0].fix or blocking[0].detail}"
