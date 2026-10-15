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
    from ionomos import configio, fragpipe, health
    from ionomos.config import ConfigError, load

    config_path = Path(config_path)
    broken = None  # the saved file does not parse (#136): the checks run on the defaults and say so first
    if data is not None:
        d = data
    else:
        try:
            d = configio.read_config(config_path)
        except ConfigError as exc:
            broken, d = str(exc), configio.defaults()
    p = {k: Path(v) for k, v in d["paths"].items() if v}
    items: list[Item] = []
    add = items.append
    if broken:
        add(Item("config", "Settings saved", "fail", broken,
                 "The file was left as it is. Fix that line in a text editor and press Reload, or Advanced -> "
                 f"Restore a previous config ({configio.BACKUP_DIR}/ next to it), then Save. The checks below "
                 "use the default settings until then.", "open_tab:4"))

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
    probs, warn = layout_findings(d["paths"], d["users"].get("default") or "")
    if probs:
        add(Item("layout", "Folder layout is safe", "fail", " ".join(probs), "Fix the paths on tab 1.", "open_tab:1"))
    elif warn:
        add(Item("layout", "Folder layout is safe", "warn", " ".join(warn), "Recommended: fix on tab 1.", "open_tab:1"))
    else:
        add(Item("layout", "Folder layout is safe", "ok", "inbox and users folder are separate, same drive"))

    # 3. users
    ur = p.get("users_root")
    from ionomos.config import DEFAULT_USER_IGNORE, _subfolders, not_a_user

    ignore = tuple(d["users"].get("ignore") if d["users"].get("ignore") is not None else DEFAULT_USER_IGNORE)
    folders = _subfolders(ur) if ur else []
    users = [u for u in folders if not_a_user(u, ignore) is None]
    skipped = [u for u in folders if u not in users]
    stale = [u for u in (d["users"].get("aliases") or {}) if u not in users]
    if not users:
        add(Item("users", "Lab members added", "todo", "no user folders yet", "Add each person on tab 2.", "open_tab:2"))
    elif stale:
        add(Item("users", "Lab members added", "warn", f"{len(users)} user(s); initials set for missing folder(s): "
                 + ", ".join(stale), "Add or remove those on tab 2.", "open_tab:2"))
    else:
        n_alias = sum(1 for u in users if (d["users"].get("aliases") or {}).get(u))
        note = (f"; not treated as people: {', '.join(skipped)} (FragPipe copies, FASTA folders, names with "
                f"spaces — move them out of the users folder when convenient)") if skipped else ""
        add(Item("users", "Lab members added", "ok", f"{len(users)} user(s), {n_alias} with initials{note}"))

    # 4. config saved + valid
    saved = config_path.is_file()
    cfg = None
    if not saved:
        add(Item("config", "Settings saved", "todo", f"{config_path} doesn't exist yet", "Press Save.", "save"))
    elif broken:
        pass  # said first, above
    else:
        try:
            cfg = load(config_path, check_paths=True)
            if cfg.unknown_keys:  # an unknown key (a typo) is ignored, so the lab PC runs with the default (#137)
                add(Item("config", "Settings saved", "warn", "; ".join(cfg.unknown_keys),
                         "Fix the key in config.yaml (a text editor), then Reload."))
            else:
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
                     "Tab 3: select the method -> Choose… / Find on this PC… (or Import workflow… from a run that "
                     "worked)." if status != "ok" else "",
                     "open_tab:3" if status != "ok" else None))
        add(library_item(cfg))
        # 7. disk
        add(Item("disk", "Enough free disk space", *disk_finding(cfg)))
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
            st, detail, fix = task_finding()
            add(Item("task", "Starts automatically at logon", st, detail, fix, "install_task" if st == "todo" else None))
    return items


def layout_findings(paths: dict, default_user: str = "") -> tuple[list[str], list[str]]:
    """(problems, warnings) of the folder layout: the same for the app's checklist and `ionomos check`, so the two
    never disagree. Problems stop the watcher (it refuses to start); warnings only slow or risk FragPipe."""
    from ionomos.config import layout_problems

    p = {k: Path(v) for k, v in paths.items() if v}
    probs = layout_problems(p.get("inbox", Path(".")), p.get("users_root", Path(".")),
                            p.get("log_dir", Path(".")), p.get("database", Path("x")))
    warn = []
    spaces = [f"{k} = {v}" for k, v in paths.items() if " " in str(v)]
    if spaces:
        (probs if os.name == "nt" else warn).append("a path contains a space, which FragPipe can't handle: "
                                                    + "; ".join(spaces) + ". Pick a location without spaces (tab 1).")
    if "inbox" in p and "users_root" in p and p["inbox"].exists() and p["users_root"].exists() \
            and _drive(p["inbox"]) != _drive(p["users_root"]):
        warn.append("inbox and users folder are on different drives: every drop is copied instead of moved "
                    "(slow for big experiments, needs double the space while copying)")
    if any(not str(v).isascii() for v in paths.values()):
        warn.append("non-English characters in a path; some FragPipe tools may not cope")
    if default_user and "users_root" in p and p["users_root"].is_dir() and not (p["users_root"] / default_user).is_dir():
        probs.append(f"users.default: folder does not exist: {p['users_root'] / default_user} (create it on tab 2, "
                     f"or clear 'default user'); the watcher refuses to start without it")
    return probs, warn


def disk_finding(cfg) -> tuple[str, str, str]:
    """(status ok | warn, detail, fix) of the free space on the data drive, for the checklist and `ionomos check`."""
    from ionomos import health

    free = health.disk_free_gb(cfg.users_root)
    if free is None:
        return "warn", f"free space on the drive of {cfg.users_root} could not be read", \
            "Check that the drive is there (a network drive may be disconnected)."
    if free < max(cfg.min_free_gb, 1):
        return "warn", (f"{free:.0f} GB free on the data drive, below fragpipe.min_free_gb {cfg.min_free_gb:g}: "
                        "searches will wait"), "Free space or lower 'Min free disk' in Advanced."
    return "ok", f"{free:.0f} GB free on the data drive", ""


def task_finding() -> tuple[str, str, str]:
    """(status, detail, fix) of the logon task (Windows), for the checklist and `ionomos check`."""
    from ionomos import service

    st = service.task_status()
    if st == "installed":
        return "ok", "installed", ""
    if st == "unavailable":
        return "warn", "Task Scheduler can't be asked on this PC (schtasks is missing or its service is off)", \
            "Start the watcher from the app after each logon, or ask IT to enable the Task Scheduler service."
    return "todo", {"legacy": "the old LabWatch task is still registered", "missing": "not installed"}.get(st, st), \
        "Install the startup task (replaces the LabWatch one)."


def library_item(cfg) -> Item:
    """'Workflows and FASTAs: N pinned, flags: …' from the last library scan (library.py, D89). Read-only: the
    saved catalog is judged again in memory; nothing is scanned and no FASTA is read during a check."""
    from ionomos import library

    title = "Workflows and FASTAs"
    try:
        cat = library.load_catalog(cfg)
        if not cat.scanned_at and not (cat.workflows or cat.fastas):
            return Item("library", title, "ok", "not looked for on this PC yet (optional)",
                        "Tab 3 → Find on this PC… lists every workflow and FASTA with what is wrong with it.")
        ctx = library.context_from(cfg, tools=False)
        library.judge_catalog(cat, ctx)
    except Exception as exc:  # noqa: BLE001 - a checklist line must never break the checklist
        return Item("library", title, "warn", f"the last scan could not be read ({exc})",
                    "Tab 3 → Find on this PC… → Rescan.", "open_tab:3")
    pinned = [x for x in (*cat.workflows, *cat.fastas) if x.source == "pinned" and Path(x.path).is_file()]
    marks = {"error": "✗", "warn": "!"}
    seen, flags = set(), []
    for x in sorted(pinned, key=lambda x: x.name.lower()):
        for f in x.flags:
            if f.severity in marks and (f.code, x.name) not in seen:
                seen.add((f.code, x.name))
                flags.append(f"{marks[f.severity]} {f.code} ({x.name})")
    errors = any(f.startswith("✗") for f in flags)
    flags.sort(key=lambda t: not t.startswith("✗"))
    when = cat.scanned_at.replace("T", " ")[:16]
    detail = (f"{len(pinned)} pinned, flags: " + ("; ".join(flags[:4]) + (f" … ({len(flags)})" if len(flags) > 4
                                                                          else "") if flags else "none")
              + f" (as of the scan of {when})")
    if not flags:
        return Item("library", title, "ok", detail)
    return Item("library", title, "warn", detail,
                "Tab 3 → Find on this PC…: each mark says what to do; Check for updates for newer files."
                if not errors else "Tab 3 → Find on this PC… → a ✗ file needs another; Review proposals… picks one.",
                "open_tab:3")


def summary(items: list[Item]) -> str:
    done = sum(1 for i in items if i.done)
    blocking = [i for i in items if i.status in ("fail", "todo")]
    if not blocking:
        return f"All {len(items)} checks pass — Ionomos is ready."
    return f"{done} of {len(items)} ready. Next: {blocking[0].title} — {blocking[0].fix or blocking[0].detail}"
