"""
Command line.

    labwatch                                       no arguments -> the setup/control app
    labwatch setup    [--config PATH]              setup wizard / control panel (tkinter)
    labwatch run      [--config PATH] [--no-gui]   watcher + intake, forever (worker: Phase 2)
    labwatch check    [--config PATH]              doctor: config, paths, users, GUI, ledger
    labwatch status   [--config PATH] [--all]      jobs from the ledger
    labwatch dry-run  FOLDER [--config PATH]       parse + validate + show the plan; touches nothing
    labwatch retry    JOB_ID [--config PATH]       failed -> queued
    labwatch testbed  ...                          build/drive a fake lab for testing (see testbed.py)

--config defaults to $LABWATCH_CONFIG, then the path last saved by the app,
then ./config.yaml (next to the exe when frozen).
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

from labwatch import __version__
from labwatch.config import Config, ConfigError, load, remember_alias
from labwatch.intake import IntakeError, intake, plan
from labwatch.ledger import Ledger
from labwatch.service import clear_pid, default_config_path, write_pid
from labwatch.watcher import Watcher

log = logging.getLogger("labwatch")


def _setup_logging(log_dir: Path | None, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "labwatch.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def _load(args, check_paths: bool) -> Config:
    try:
        cfg = load(args.config, check_paths=check_paths)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    for w in cfg.warnings:
        log.warning("config: %s", w)
    return cfg


# ---------------------------------------------------------------- commands --


def cmd_run(args) -> int:
    cfg = _load(args, check_paths=True)
    _setup_logging(cfg.log_dir, args.verbose)
    log.info("labwatch %s starting (config %s)", __version__, cfg.config_path)
    ledger = Ledger(cfg.database)
    for jid in ledger.recover_on_startup():
        log.warning("job %d was running at shutdown; marked failed", jid)
    write_pid(cfg.log_dir)

    resolver = None
    root = None
    if cfg.gui_enabled and not args.no_gui:
        from labwatch.resolve import TkResolver, gui_available

        ok, why = gui_available()
        if ok:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            resolver = TkResolver(root, cfg.gui_timeout_seconds, remember=partial(remember_alias, cfg))
            log.info("resolver window enabled (opens only when a folder can't be interpreted)")
        else:
            log.warning("resolver window disabled: %s — problems will be rejected with a note", why)

    def on_stable(folder: Path):
        return intake(folder, cfg, ledger, resolver)

    w = Watcher(cfg.inbox, on_stable, cfg.poll_seconds, cfg.stable_seconds, cfg.min_raw_files)
    if root is None:
        try:
            w.run_forever()
        finally:
            clear_pid(cfg.log_dir)
        return 0

    # GUI mode: watcher in a thread, Tk on the main thread.
    t = threading.Thread(target=w.run_forever, name="watcher", daemon=True)
    t.start()
    resolver.start()

    def stop(*_):
        log.info("stopping")
        w.stop()
        root.quit()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    def heartbeat():
        if not t.is_alive():
            log.error("watcher thread died; exiting")
            root.quit()
            return
        root.after(1000, heartbeat)

    root.after(1000, heartbeat)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        w.stop()
        clear_pid(cfg.log_dir)
    return 0


def cmd_setup(args) -> int:
    from labwatch.app import main as app_main

    return app_main(Path(args.config) if args.config_given else None)


def cmd_check(args) -> int:
    ok_all = True

    def row(ok: bool | None, label: str, detail: str = ""):
        nonlocal ok_all
        mark = "✓" if ok else ("!" if ok is None else "✗")
        if ok is False:
            ok_all = False
        print(f" {mark} {label:<28} {detail}")

    print(f"labwatch {__version__}  python {sys.version.split()[0]}  {sys.platform}")
    print(f"config: {args.config}")
    try:
        cfg = load(args.config, check_paths=False)
    except ConfigError as exc:
        row(False, "config", str(exc))
        return 1
    row(True, "config parses")
    for name in ("inbox", "users_root", "workflow_dir", "fasta_dir", "log_dir"):
        p = getattr(cfg, name)
        row(p.is_dir(), f"paths.{name}", str(p))
    row(cfg.database.parent.is_dir(), "paths.database (parent)", str(cfg.database))
    row(cfg.fragpipe_exe.is_file() or None, "paths.fragpipe_exe", f"{cfg.fragpipe_exe}" + (
        "" if cfg.fragpipe_exe.is_file() else "  (not found — needed for Phase 2 searches only)"))
    users = cfg.known_users()
    row(bool(users), "users", ", ".join(users) if users else "none — create folders under users_root")
    for u, als in cfg.user_aliases.items():
        row(u in users, f"  alias {', '.join(als)}", f"-> {u}" + ("" if u in users else "  (no such user folder!)"))
    for k, m in cfg.methods.items():
        wf = cfg.workflow_dir / m.workflow
        row(wf.is_file() or None, f"methods.{k}", f"{m.workflow} ({m.data_type})" + ("" if wf.is_file() else "  (workflow file missing)"))
    if cfg.gui_enabled:
        from labwatch.resolve import gui_available

        ok, why = gui_available()
        row(ok or None, "resolver window", "available" if ok else f"disabled: {why}")
    else:
        row(None, "resolver window", "disabled in config")
    if cfg.database.is_file():
        jobs = Ledger(cfg.database).list()
        counts = {}
        for j in jobs:
            counts[j.status] = counts.get(j.status, 0) + 1
        row(True, "ledger", ", ".join(f"{k}={v}" for k, v in counts.items()) or "empty")
    else:
        row(True, "ledger", "not created yet")
    print("\nall good" if ok_all else "\nfix the ✗ items above")
    return 0 if ok_all else 1


def cmd_status(args) -> int:
    cfg = _load(args, check_paths=False)
    if not cfg.database.is_file():
        print("no ledger yet (nothing has been dropped)")
        return 0
    jobs = Ledger(cfg.database).list()
    if not args.all:
        jobs = [j for j in jobs if j.status != "done"]
    if not jobs:
        print("no jobs" + ("" if args.all else " (use --all to include done)"))
        return 0
    print(f"{'id':>4}  {'status':<8} {'method':<7} {'user':<10} {'created':<20} name")
    for j in jobs:
        line = f"{j.id:>4}  {j.status:<8} {j.method:<7} {j.user:<10} {(j.created_at or '')[:19]:<20} {j.inbox_name}"
        if j.reason:
            line += f"\n      ↳ {j.reason}"
        print(line)
    return 0


def cmd_dry_run(args) -> int:
    cfg = _load(args, check_paths=False)
    _setup_logging(None, args.verbose)
    ledger = Ledger(cfg.database) if cfg.database.is_file() else None
    folder = Path(args.folder)
    try:
        p = plan(folder, cfg, ledger)
    except IntakeError as exc:
        fixable = exc.kind.value in ("user", "method", "raws", "layout")
        print(f"WOULD REJECT: {folder.name}\n  reason: {exc}\n  kind  : {exc.kind.value}"
              + ("  (the resolver window would open for this)" if fixable else ""))
        return 1
    f = p.folder
    print(f"WOULD ACCEPT: {f.original}")
    if f.safe != f.original:
        print(f"  renamed to : {f.safe}")
    print(f"  user       : {f.user}")
    print(f"  method     : {f.method}  (workflow {p.overrides.get('workflow') or cfg.methods[f.method].workflow})")
    print(f"  date       : {f.date or '(not in name; would use drop date)'}  [{p.date_source}]")
    print(f"  destination: {p.dest}")
    if p.overrides:
        print(f"  overrides  : experiment.yaml -> {', '.join(sorted(p.overrides))}")
    if p.renames:
        print("  raw renames:")
        for a, b in p.renames.items():
            print(f"    {a}  ->  {b}")
    print(f"  layout     : {len(p.layout)} experiment(s)")
    for sample, reps in p.layout.items():
        fr = next(iter(reps.values()))
        shape = f"{len(reps)} rep(s) x {len(fr)} fraction(s)" if fr else f"{len(reps)} rep(s), single-shot"
        print(f"    {sample}: {shape}")
    print("  manifest   : (file  experiment  bioreplicate  type)")
    for m in p.manifest:
        print(f"    {m.file}\t{m.experiment}\t{m.bioreplicate}\t{m.data_type}")
    if p.other_files:
        print(f"  other files: {', '.join(p.other_files)}")
    return 0


def cmd_retry(args) -> int:
    cfg = _load(args, check_paths=False)
    ledger = Ledger(cfg.database)
    job = ledger.get(args.job_id)
    if not job:
        print(f"no job {args.job_id}", file=sys.stderr)
        return 1
    if job.status != "failed":
        print(f"job {job.id} is {job.status}, not failed", file=sys.stderr)
        return 1
    ledger.set_status(job.id, "queued")
    print(f"job {job.id} re-queued")
    return 0


def cmd_testbed(args) -> int:
    from labwatch import testbed

    return testbed.main(args)


# ------------------------------------------------------------------- main --


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="labwatch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(default_config_path()))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"labwatch {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="setup wizard / control panel").set_defaults(fn=cmd_setup)
    r = sub.add_parser("run", help="watch the inbox forever")
    r.add_argument("--no-gui", action="store_true", help="never open the resolver window")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("check", help="verify config, folders, users, GUI").set_defaults(fn=cmd_check)
    s = sub.add_parser("status", help="list jobs")
    s.add_argument("--all", action="store_true", help="include done jobs")
    s.set_defaults(fn=cmd_status)
    d = sub.add_parser("dry-run", help="show what would happen to a folder; touches nothing")
    d.add_argument("folder")
    d.set_defaults(fn=cmd_dry_run)
    rt = sub.add_parser("retry", help="re-queue a failed job")
    rt.add_argument("job_id", type=int)
    rt.set_defaults(fn=cmd_retry)

    from labwatch import testbed

    testbed.add_parser(sub).set_defaults(fn=cmd_testbed)

    argv = sys.argv[1:] if argv is None else argv
    if not argv:  # double-clicked exe / bare `labwatch` -> the app
        argv = ["setup"]
    args = ap.parse_args(argv)
    args.config_given = any(a == "--config" or a.startswith("--config=") for a in argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
