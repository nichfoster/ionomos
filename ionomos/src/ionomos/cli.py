"""
Command line.

    ionomos                                       no arguments -> the setup/control app
    ionomos setup    [--config PATH]              setup wizard / control panel (tkinter)
    ionomos run      [--config PATH] [--no-gui]   watcher + intake, forever (worker: Phase 2)
    ionomos check    [--config PATH]              doctor: config, paths, users, GUI, ledger
    ionomos status   [--config PATH] [--all]      jobs from the ledger
    ionomos dry-run  FOLDER [--config PATH]       parse + validate + show the plan; touches nothing
    ionomos retry    JOB_ID [--config PATH]       failed -> queued
    ionomos testbed  ...                          build/drive a fake lab for testing (see testbed.py)
    ionomos diagnose [--zip [PATH]]               everything needed to report a problem (text, or a .zip bundle)
    ionomos analyze  JOB_ID|FOLDER [--control C] [--compare 'A vs B'] [--log2fc F] [--open]
                                                   statistics + volcano plots + results/report.html
    ionomos init     [--root DIR] [--users DIR]   create folders + a config without the app (headless setup)
    ionomos cancel   JOB_ID                       stop a running search / drop a queued job
    ionomos pause | resume                        hold / release the FragPipe queue
    ionomos repair-ledger [--force]               rebuild the job list from the experiment folders
    ionomos update                                git pull + reinstall (only when running from a git checkout)

--config defaults to $IONOMOS_CONFIG, then the path last saved by the app (LabWatch-era paths too),
then ./config.yaml (next to the exe when frozen).
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ionomos import __version__
from ionomos.config import Config, ConfigError, load, remember_alias
from ionomos.intake import IntakeError, intake, plan
from ionomos.ledger import Ledger
from ionomos.service import clear_pid, default_config_path, write_pid
from ionomos.watcher import Watcher

log = logging.getLogger("ionomos")


def _setup_logging(log_dir: Path | None, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    if sys.stderr is not None:  # None under pythonw.exe / windowed exe
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        from ionomos.names import LOG_FILE

        fh = RotatingFileHandler(log_dir / LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
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


def _open_ledger(cfg: Config) -> Ledger:
    """Open the ledger; if the file is corrupt, keep it aside and rebuild from the ionomos.json files."""
    from ionomos import ledger as ledger_mod

    state = ledger_mod.integrity(cfg.database)
    if state not in ("ok", "missing"):
        log.critical("job ledger %s is damaged (%s); rebuilding it from the experiment folders", cfg.database, state)
        moved, n = ledger_mod.rebuild_from_status_files(cfg.database, cfg.users_root)
        log.critical("ledger rebuilt with %d job(s); the damaged file was kept as %s", n, moved)
    return Ledger(cfg.database)


def _maintenance(cfg: Config) -> None:
    """Daily housekeeping: ledger backup, prune our own old diagnostics/crash files."""
    from ionomos import health
    from ionomos import ledger as ledger_mod

    try:
        led = Ledger(cfg.database)
        try:
            for jid in ledger_mod.adopt_orphans(led, cfg.users_root):
                log.warning("job %d: re-adopted a filed experiment that was missing from the job list", jid)
        finally:
            led.close()
    except Exception:  # noqa: BLE001
        log.exception("orphan check failed")
    try:
        b = ledger_mod.backup(cfg.database, cfg.log_dir / "backups")
        if b:
            log.debug("ledger backup: %s", b)
    except Exception:  # noqa: BLE001
        log.exception("ledger backup failed")
    health.prune(cfg.log_dir, "diagnostics-*.txt", keep=20)
    health.prune(cfg.log_dir, "diagnostics-*.zip", keep=10)


def cmd_run(args) -> int:
    from ionomos import health

    cfg = _load(args, check_paths=True)
    _setup_logging(cfg.log_dir, args.verbose)
    health.install_excepthooks(cfg.log_dir)
    lock = health.InstanceLock(cfg.log_dir)
    try:
        lock.acquire()
    except health.AlreadyRunning as exc:
        log.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 3
    log.info("ionomos %s starting (config %s, pid %d)", __version__, cfg.config_path, os.getpid())
    ledger = _open_ledger(cfg)
    for jid, st in ledger.recover_on_startup():
        log.warning("job %d was running when ionomos stopped; now %s", jid, st)
    write_pid(cfg.log_dir)
    _maintenance(cfg)
    hb = health.Heartbeat(cfg.log_dir)
    hb.beat("watcher", "starting", force=True)
    stop = threading.Event()
    threads: list[threading.Thread] = []

    def supervised(name: str, target):
        t = threading.Thread(target=health.supervise, args=(name, target, stop),
                             kwargs={"on_crash": lambda n, e: health.write_crash_file(cfg.log_dir, n, repr(e))},
                             name=name, daemon=True)
        t.start()
        threads.append(t)

    worker = None
    if cfg.auto_run:
        from ionomos.worker import Worker

        worker = Worker(cfg, heartbeat=hb)
        supervised("worker", worker.run_forever)
    else:
        log.info("fragpipe.auto_run is off: jobs are filed and queued, FragPipe is not started")

    from ionomos.service import STOP_FILE, clear_stop

    clear_stop(cfg.log_dir)  # a leftover request must not stop this fresh start

    def apply_debug_switch():
        want = logging.DEBUG if (args.verbose or health.debug_until(cfg.log_dir)) else logging.INFO
        if logging.getLogger().level != want:
            logging.getLogger().setLevel(want)
            log.info("detailed logging %s", "ON" if want == logging.DEBUG else "off")

    def daily():
        last = time.monotonic()
        tick = 0
        while not stop.wait(1):
            tick += 1
            if tick % 30 == 1:
                apply_debug_switch()
            if (cfg.log_dir / STOP_FILE).exists():  # graceful stop requested by the app / another process
                log.info("stop requested via %s", cfg.log_dir / STOP_FILE)
                clear_stop(cfg.log_dir)
                shutdown()
                return
            if time.monotonic() - last > 3600:
                last = time.monotonic()
                _maintenance(cfg)


    resolver = None
    root = None
    if cfg.gui_enabled and not args.no_gui:
        from ionomos.resolve import TkResolver, gui_available

        ok, why = gui_available()
        if ok:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            resolver = TkResolver(root, cfg.gui_timeout_seconds, remember=partial(remember_alias, cfg))
            log.info("resolver window enabled (opens only when a folder can't be interpreted)")
        else:
            log.warning("resolver window disabled: %s — problems will be rejected with a note", why)

    intake_ledger = ledger

    def on_stable(folder: Path):
        return intake(folder, cfg, intake_ledger, resolver)

    w = Watcher(cfg.inbox, on_stable, cfg.poll_seconds, cfg.stable_seconds, cfg.min_raw_files, heartbeat=hb)

    def shutdown(*_):
        if not stop.is_set():
            log.info("stopping")
        stop.set()
        w.stop()
        if worker is not None:
            worker.stop()
        if root is not None:
            try:
                root.after(0, root.quit)
            except Exception:  # noqa: BLE001 - Tk may already be gone
                pass

    threading.Thread(target=daily, name="maintenance", daemon=True).start()
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl-Break / console close
        signal.signal(signal.SIGBREAK, shutdown)

    try:
        if root is None:
            health.supervise("watcher", w.run_forever, stop)
        else:
            # GUI mode: watcher in a thread, Tk on the main thread (the resolver window needs it).
            supervised("watcher", w.run_forever)
            resolver.start()

            def tick():
                if stop.is_set():
                    root.quit()
                    return
                root.after(500, tick)

            root.after(500, tick)
            try:
                root.mainloop()
            except KeyboardInterrupt:
                pass
    finally:
        shutdown()
        for t in threads:
            t.join(timeout=60)  # lets a running FragPipe be killed and its job re-queued
        hb.clear()
        clear_pid(cfg.log_dir)
        lock.release()
        log.info("ionomos stopped")
    return 0


def cmd_setup(args) -> int:
    from ionomos.app import main as app_main

    return app_main(Path(args.config) if args.config_given else None)


def cmd_check(args) -> int:
    ok_all = True

    def row(ok: bool | None, label: str, detail: str = ""):
        nonlocal ok_all
        mark = "✓" if ok else ("!" if ok is None else "✗")
        if ok is False:
            ok_all = False
        print(f" {mark} {label:<28} {detail}")

    print(f"ionomos {__version__}  python {sys.version.split()[0]}  {sys.platform}")
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
    from ionomos import fragpipe, health
    from ionomos import ledger as ledger_mod
    from ionomos.worker import paused

    free = health.disk_free_gb(cfg.users_root)
    if free is not None:
        low = free < max(cfg.min_free_gb, 1)
        row(None if low else True, "disk free (data drive)",
            f"{free:.0f} GB" + (f"  — below fragpipe.min_free_gb {cfg.min_free_gb:g}: searches will wait" if low else ""))
    for ok, label, detail in fragpipe.install_report(cfg):
        row(None if ok is False else ok,
            f"FragPipe {label}" if not label.startswith("FragPipe") else label,
            detail + ("; jobs wait until it's set" if ok is False and label == "launcher" else ""))
    row(True if cfg.auto_run else None, "fragpipe.auto_run",
        "on: queued jobs are searched automatically" if cfg.auto_run else "off: jobs are only filed and queued")
    if paused(cfg.log_dir):
        row(None, "searches", "PAUSED (ionomos resume / app: Jobs -> Resume)")
    users = cfg.known_users()
    row(bool(users), "users", ", ".join(users) if users else "none — create folders under users_root")
    for u, als in cfg.user_aliases.items():
        row(u in users, f"  alias {', '.join(als)}", f"-> {u}" + ("" if u in users else "  (no such user folder!)"))
    for k in cfg.methods:
        for i, (ok, text) in enumerate(fragpipe.describe_method(cfg, k)):
            row(None if ok is False else ok, f"methods.{k}" if i == 0 else "",
                text + (f" — {k} jobs wait" if ok is False else ""))
    if cfg.gui_enabled:
        from ionomos.resolve import gui_available_isolated

        ok, why = gui_available_isolated()
        row(ok or None, "resolver window", "available" if ok else f"disabled: {why}")
    else:
        row(None, "resolver window", "disabled in config")
    state = ledger_mod.integrity(cfg.database)
    if state == "ok":
        jobs = Ledger(cfg.database).list()
        counts = {}
        for j in jobs:
            counts[j.status] = counts.get(j.status, 0) + 1
        row(True, "ledger", ", ".join(f"{k}={v}" for k, v in counts.items()) or "empty")
    elif state == "missing":
        row(True, "ledger", "not created yet")
    else:
        row(False, "ledger", f"{state} — run: ionomos repair-ledger (rebuilds it from the experiment folders)")
    running = health.is_locked(cfg.log_dir)
    hb, healthy = health.heartbeat_summary(cfg.log_dir)
    row(True if running and healthy else None, "watcher",
        f"running (pid {health.holder_pid(cfg.log_dir) or '?'}); {hb}" if running else "not running")
    crashes = health.recent_crashes(cfg.log_dir, 1)
    if crashes:
        row(None, "last crash report", str(crashes[-1]))
    print("\nall good" if ok_all else "\nfix the ✗ items above")
    return 0 if ok_all else 1


def cmd_status(args) -> int:
    from ionomos import fragpipe, health
    from ionomos.worker import paused

    cfg = _load(args, check_paths=False)
    running = health.is_locked(cfg.log_dir)
    hb, healthy = health.heartbeat_summary(cfg.log_dir)
    print(f"watcher: {'running' if running else 'NOT running'}" + (f" — {hb}" if running else "")
          + ("" if healthy or not running else "  (not responding?)"))
    if paused(cfg.log_dir):
        print("searches: PAUSED  (ionomos resume)")
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
        if j.status == "running":
            from ionomos.names import console_log

            step = fragpipe.progress(console_log(Path(j.dest_dir)))
            line += f"\n      ↳ FragPipe: {step}, attempt {j.attempts}, started {(j.started_at or '')[:19]}"
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
    ledger.requeue(job.id, "retry requested", reset_attempts=True)
    print(f"job {job.id} re-queued; the running watcher picks it up within seconds")
    return 0


def cmd_diagnose(args) -> int:
    from ionomos.service import save_diagnostics, save_diagnostics_zip

    if args.zip:
        z = save_diagnostics_zip(Path(args.config), Path(args.zip) if args.zip != "auto" else None)
        print(f"diagnostics bundle: {z}")
        return 0
    text, where = save_diagnostics(Path(args.config))
    print(text)
    if where:
        print(f"(saved to {where})")
    return 0


def cmd_stop(args) -> int:
    """Gracefully stop the watcher of this config (it re-queues a running search). Used by the installer."""
    from ionomos import health
    from ionomos.service import request_stop

    try:
        cfg = load(args.config, check_paths=False)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if not health.is_locked(cfg.log_dir):
        print("no watcher running")
        return 0
    print(f"stopping the watcher: {request_stop(cfg.log_dir, timeout=args.wait)}")
    if args.remember:
        (cfg.log_dir / "RESTART_WATCHER").write_text("restart after update\n", encoding="utf-8")
    return 0


def cmd_init(args) -> int:
    """Headless setup: folders + config + FragPipe detection, then the checklist."""
    from ionomos import configio, fragpipe, setupcheck
    from ionomos.service import remember_config_path

    root = (args.root or configio.defaults()["paths"]["inbox"].rsplit("/", 1)[0]).replace("\\", "/")
    users = args.users or configio.defaults()["paths"]["users_root"]
    cfg_path = Path(args.config if args.config_given else Path(root) / "config.yaml")
    if cfg_path.is_file() and not args.force:
        print(f"{cfg_path} already exists; keeping it (use --force to start from defaults).")
        data = configio.read_config(cfg_path)
    else:
        data = configio.defaults(root, users)
        found = fragpipe.detect_launcher()
        if found:
            data["paths"]["fragpipe_exe"] = str(found).replace("\\", "/")
            print(f"found FragPipe: {found}")
    for key in ("inbox", "users_root", "workflow_dir", "fasta_dir", "log_dir"):
        Path(data["paths"][key]).mkdir(parents=True, exist_ok=True)
    Path(data["paths"]["database"]).parent.mkdir(parents=True, exist_ok=True)
    configio.write_config(cfg_path, data)
    remember_config_path(cfg_path)
    print(f"config: {cfg_path}\n")
    items = setupcheck.run(cfg_path)
    mark = {"ok": "✓", "todo": "·", "warn": "!", "fail": "✗"}
    for it in items:
        print(f" {mark[it.status]} {it.title:<34} {it.detail}")
        if it.status != "ok" and it.fix:
            print(f"   → {it.fix}")
    print("\n" + setupcheck.summary(items))
    return 1 if any(i.status == "fail" for i in items) else 0


def cmd_analyze(args) -> int:
    """Re-run the downstream analysis for a job (by id) or any experiment / FragPipe folder."""
    from ionomos import postprocess

    cfg = None
    try:
        cfg = load(args.config, check_paths=False)
    except ConfigError:
        pass  # analysing a folder works without a lab config (defaults)
    target = args.target
    if target.isdigit():
        if cfg is None or not cfg.database.is_file():
            print("no job ledger here; give a folder path instead", file=sys.stderr)
            return 2
        job = Ledger(cfg.database).get(int(target))
        if job is None:
            print(f"no job {target}", file=sys.stderr)
            return 2
        dest = Path(job.dest_dir)
    else:
        dest = Path(target)
    if not dest.is_dir():
        print(f"not a folder: {dest}", file=sys.stderr)
        return 2
    extra = {}
    if args.control:
        extra["control"] = args.control
    if args.compare:
        extra["comparisons"] = args.compare
    for key in ("log2fc", "alpha", "test", "min_valid"):
        if getattr(args, key) is not None:
            extra[key] = getattr(args, key)
    if args.raw_p:
        extra["use_adjusted"] = False
    out = postprocess.run_for_folder(dest, cfg, args.method, extra)
    print(f"method: {out.method or 'unknown'}")
    for c in out.summary.get("comparisons", []):
        print(f"  {c['name']}: {c['up']} up, {c['down']} down of {c['tested']} tested  ({c['table']})")
    for w in out.warnings:
        print(f"  note: {w}")
    if out.report:
        print(f"report: {out.report}")
        if args.open:
            from ionomos.service import open_path

            open_path(out.report)
    return 0 if out.report else 1


def cmd_cancel(args) -> int:
    from ionomos.worker import request_cancel

    cfg = _load(args, check_paths=False)
    print(request_cancel(Ledger(cfg.database), args.job_id))
    return 0


def cmd_pause(args) -> int:
    from ionomos.worker import pause, resume

    cfg = _load(args, check_paths=False)
    if args.cmd == "pause":
        pause(cfg.log_dir, "from the command line")
        print("searches paused: queued jobs wait; a running search finishes. `ionomos resume` to continue.")
    else:
        resume(cfg.log_dir)
        print("searches resumed")
    return 0


def cmd_repair_ledger(args) -> int:
    from ionomos import health
    from ionomos import ledger as ledger_mod

    cfg = _load(args, check_paths=False)
    if health.is_locked(cfg.log_dir):
        print("stop the watcher first (it has the ledger open)", file=sys.stderr)
        return 1
    state = ledger_mod.integrity(cfg.database)
    if state == "ok" and not args.force:
        print("ledger is fine; nothing to do (use --force to rebuild anyway)")
        return 0
    moved, n = ledger_mod.rebuild_from_status_files(cfg.database, cfg.users_root)
    print(f"ledger rebuilt from the experiment folders: {n} job(s)" + (f"; old file kept as {moved}" if moved else ""))
    return 0


def cmd_update(args) -> int:
    from ionomos.service import source_checkout, update_source

    repo = source_checkout()
    if repo is None:
        print("not running from a git checkout; update by installing a new release instead", file=sys.stderr)
        return 2
    ok, msg = update_source(repo)
    print(msg)
    return 0 if ok else 1


def cmd_testbed(args) -> int:
    from ionomos import testbed

    return testbed.main(args)


# ------------------------------------------------------------------- main --


def main(argv: list[str] | None = None) -> int:
    # Windows pipes/consoles default to cp1252; check/status print ✓ ✗ ↳.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="ionomos", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(default_config_path()))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="store_true", help="print the version and build, then exit")
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
    dg = sub.add_parser("diagnose", help="print + save a diagnostics report")
    dg.add_argument("--zip", nargs="?", const="auto", metavar="PATH",
                    help="write a .zip bundle (report, logs, failed jobs' FragPipe logs) instead")
    dg.set_defaults(fn=cmd_diagnose)
    sp = sub.add_parser("stop", help="gracefully stop the running watcher (re-queues a running search)")
    sp.add_argument("--wait", type=float, default=60, help="seconds to wait before forcing it (default 60)")
    sp.add_argument("--remember", action="store_true", help="start it again when the app next opens (updates)")
    sp.set_defaults(fn=cmd_stop)
    it = sub.add_parser("init", help="create folders + config without the app, then show the setup checklist")
    it.add_argument("--root", help="Ionomos folder (default C:/Fragpipe_Auto on Windows)")
    it.add_argument("--users", help="users folder (default C:/Fragpipe_General on Windows)")
    it.add_argument("--force", action="store_true", help="overwrite an existing config with defaults (a backup is kept)")
    it.set_defaults(fn=cmd_init)
    az = sub.add_parser("analyze", help="(re)run statistics, volcano plots and the report for a job or folder")
    az.add_argument("target", help="job id, experiment folder, or any FragPipe output folder")
    az.add_argument("--method", choices=["isoDTB", "TMT", "DIA", "LFQ", "auto"], default=None,
                    help="default: from ionomos.json, else detected from the files")
    az.add_argument("--control", help="control condition (default: recognised by name, e.g. DMSO)")
    az.add_argument("--compare", action="append", metavar="'A vs B'", help="comparison; repeatable")
    az.add_argument("--log2fc", type=float, help="fold-change threshold (log2)")
    az.add_argument("--alpha", type=float, help="significance threshold")
    az.add_argument("--test", choices=["moderated", "welch", "student"])
    az.add_argument("--min-valid", dest="min_valid", type=int)
    az.add_argument("--raw-p", action="store_true", help="apply alpha to raw p-values instead of BH q-values")
    az.add_argument("--open", action="store_true", help="open the report when done")
    az.set_defaults(fn=cmd_analyze)
    cn = sub.add_parser("cancel", help="cancel a queued or running job")
    cn.add_argument("job_id", type=int)
    cn.set_defaults(fn=cmd_cancel)
    sub.add_parser("pause", help="start no new FragPipe searches").set_defaults(fn=cmd_pause)
    sub.add_parser("resume", help="undo pause").set_defaults(fn=cmd_pause)
    rl = sub.add_parser("repair-ledger", help="rebuild the job ledger from the experiment folders")
    rl.add_argument("--force", action="store_true")
    rl.set_defaults(fn=cmd_repair_ledger)
    sub.add_parser("update", help="git pull + reinstall (dev install only)").set_defaults(fn=cmd_update)

    from ionomos import testbed

    testbed.add_parser(sub).set_defaults(fn=cmd_testbed)

    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv[:2]:
        from ionomos.buildinfo import one_line

        print(one_line())
        return 0
    if argv[:1] == ["probe-gui"]:  # hidden: used by check/diagnose to test the display safely
        from ionomos.resolve import gui_available

        ok, why = gui_available()
        print("ok" if ok else why)
        return 0 if ok else 1
    if argv[:1] == ["fake-fragpipe"]:  # hidden: the testbed's stand-in for FragPipe
        from ionomos.testbed import fake_fragpipe

        return fake_fragpipe(argv[1:])
    if not argv:  # double-clicked exe / bare `ionomos` -> the app
        argv = ["setup"]
    args = ap.parse_args(argv)
    args.config_given = any(a == "--config" or a.startswith("--config=") for a in argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
