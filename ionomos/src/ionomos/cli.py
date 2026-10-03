"""
Command line.

    ionomos                                       no arguments -> the setup/control app
    ionomos setup    [--config PATH]              setup wizard / control panel (tkinter)
    ionomos run      [--config PATH] [--no-gui]   watcher + intake, forever (worker: Phase 2)
    ionomos check    [--config PATH]              doctor: config, paths, users, GUI, ledger
    ionomos status   [--config PATH] [--all]      jobs from the ledger
    ionomos dry-run  FOLDER [--config PATH]       parse + validate + show the plan; touches nothing
    ionomos names test NAME... [--method M]       how folder / .raw names are read with this config (naming:)
    ionomos retry    JOB_ID [--config PATH]       failed -> queued
    ionomos testbed  ...                          build/drive a fake lab for testing (see testbed.py)
    ionomos diagnose [--zip [PATH]]               everything needed to report a problem (text, or a .zip bundle)
    ionomos bundle   [JOB|FOLDER ...] [--level diagnose|validate] [--out DIR] [--no-anonymise]
                                                   an anonymised zip on the Desktop for troubleshooting / validation;
                                                   bundle inspect ZIP | unpack ZIP DIR | translate KEY [FILE] (bundle.py)
    ionomos analyze  JOB_ID|FOLDER [--control C] [--compare 'A vs B'] [--de-type all] [--imputation none]
                     [--exclude SAMPLE] [--log2fc F] [--open]
                                                   statistics + volcano plots + results/report.html
    ionomos export   JOB_ID|FOLDER [--preset slide169|slide43|half|col1|col2] [--palette colorblind]
                     [--font-pt N] [--figures volcano,pca,dose,...] [--list] [--features EGFR,BTK] [--top N]
                     [--format svg|png|both] [--style FILE] [--out DIR]
                                                   figures for slides from the finished report (volcano, PCA, heatmap,
                                                   correlation, dose-response, time course, liganded sites), in the
                                                   lab's export style (analysis.export); PNG with a renderer (D68)
    ionomos demo     [FOLDER] [--open]            a simulated experiment + its report (offline, no lab setup)
    ionomos compare  FOLDER REFERENCE [--by id|gene] [--open]
                                                   an Ionomos analysis against a reference result of the same experiment
                                                   (another Ionomos run, FragPipe-Analyst, limma, MSstats, Perseus, R):
                                                   agreement of fold changes, hit calls and p-values, with a verdict
    ionomos benchmark [--grid quick|standard] [--kind dia|isodtb|tmt] [--like FOLDER]
                                                   accuracy on simulated data with planted changes: sensitivity and
                                                   observed false discoveries for each imputation / normalisation
                                                   (isoDTB: test, mixing error; TMT: IRS, a pulldown)
    ionomos benchmark FOLDER --expected hye.yaml  a mixed-species / spike-in run: measured against expected ratios
    ionomos help     [TOPIC] [--open]             plain-language help: prints TOPIC (NO_TABLE, pca, ...) and
                                                   writes help.html (--open: in the browser, at TOPIC)
    ionomos ask      "QUESTION" [--experiment JOB_ID|NAME] [--item ID] [--json]
                                                   the local assistant: an answer grounded in the job's log, the
                                                   doctor's findings and the help; changes nothing (docs/ASSISTANT.md)
    ionomos ask-eval [--base-url URL] [--model NAME] [--out FILE] [--only IDS] [--scripted]
                                                   score a model on this PC over the scenario corpus: rubric
                                                   pass rate, injection failures, time to first token
    ionomos init    [--root DIR] [--users DIR]   create folders + a config without the app (headless setup)
    ionomos qc-trend [--rebuild] [--open]         instrument QC: the QC-standard runs trended (logs/qc_trend.html)
    ionomos cancel   JOB_ID                       stop a running search / drop a queued job
    ionomos pause | resume                        hold / release the FragPipe queue
    ionomos notify-test                           send a test message to the channels in config.yaml notify:
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
from pathlib import Path

from ionomos import __version__
from ionomos.config import Config, ConfigError, LiveConfig, load, remember_alias
from ionomos.intake import IntakeError, draft, intake, plan
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
        from ionomos import health
        from ionomos.names import LOG_FILE

        fh = health.rotating_log_handler(log_dir / LOG_FILE)  # size-based, names.LOG_BACKUPS old files kept
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
    try:
        from ionomos import attention

        attention.purge(cfg.log_dir, older_than_days=30)
    except Exception:  # noqa: BLE001
        log.exception("attention purge failed")
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
    from ionomos.worker import recover

    for jid, st in recover(cfg, ledger):
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


    live = LiveConfig(cfg)  # users / aliases / naming added in the app count for the next drop, no restart
    resolver = None
    root = None
    if cfg.gui_enabled and not args.no_gui:
        from ionomos.resolve import TkResolver, gui_available

        ok, why = gui_available()
        if ok:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            resolver = TkResolver(root, cfg.gui_timeout_seconds,
                                  remember=lambda user, alias: remember_alias(live.get(), user, alias),
                                  refresh=lambda d: draft(Path(d.source), live.get(), review=d.review))
            log.info("resolver window enabled (%s)", "every drop is shown for review before filing"
                     if cfg.review_drops else "opens only when a folder can't be interpreted")
        else:
            log.warning("resolver window disabled: %s — problems will be rejected with a note", why)

    intake_ledger = ledger

    def on_stable(folder: Path):
        return intake(folder, live.get(), intake_ledger, resolver)

    w = Watcher(cfg.inbox, on_stable, cfg.poll_seconds, cfg.stable_seconds, cfg.min_raw_files, heartbeat=hb,
                group_loose=cfg.group_loose_files)

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
            _pops = _watcher_popups(cfg, root)  # noqa: F841 - keeps the pop-up poller alive

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


def _watcher_popups(cfg, root):
    """Pop-up windows from the watcher itself, for when the app isn't open (attention.py / popups.py)."""
    try:
        from ionomos import service
        from ionomos.popups import PopupHost, Popups

        host = PopupHost(root=root, log_dir=lambda: cfg.log_dir, config_path=lambda: cfg.config_path,
                         open_path=service.open_path, lab_settings=lambda: dict(cfg.analysis or {}),
                         popups_enabled=lambda: cfg.gui_popups, database=lambda: cfg.database)
        pops = Popups(root, host, is_app=False)
        pops.start(first_ms=5000)
        log.info("pop-up windows enabled (analysis decisions, failed searches) when the app isn't open")
        return pops
    except Exception:  # noqa: BLE001 - pop-ups are a convenience; the watcher runs without them
        log.exception("pop-up windows unavailable")
        return None


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
    from ionomos import notify

    row(True if cfg.notify.get("enabled") else None, "notifications", notify.describe(cfg.notify))
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
    from ionomos import assistant

    st, why = assistant.state(assistant.settings_of(cfg))
    row(True if st == "ready" else None, "assistant", why if st != "not_set_up" else f"not set up: {why}")
    print("\nall good" if ok_all else "\nfix the ✗ items above")
    return 0 if ok_all else 1


def cmd_preflight(args) -> int:
    """Everything about FragPipe that can be checked without a search (preflight.py, D59)."""
    from ionomos import preflight

    print(f"ionomos {__version__}  FragPipe preflight  config: {args.config}")
    try:
        cfg = load(args.config, check_paths=False)
    except ConfigError as exc:
        print(f" ✗ config  {exc}")
        return 1
    if not args.static:
        print("starting FragPipe for --help" + ("" if args.no_dry_run else " and one dry run per method")
              + " (no search; this can take a few minutes)…", flush=True)
    checks = preflight.run(cfg, dry=not args.no_dry_run, methods=args.method or None,
                           raw=Path(args.raw) if args.raw else None, start=not args.static)
    print(preflight.text(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


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


def cmd_names(args) -> int:
    """names test: how the config reads each name (user, method, date, sample, replicate, fraction)."""
    from ionomos.namecheck import check_names, format_readings

    cfg = _load(args, check_paths=False)
    method = None
    if args.method:
        method = next((k for k in cfg.methods if k.lower() == args.method.lower()), None)
        if method is None:
            print(f"--method {args.method!r} is not one of {', '.join(cfg.methods)}", file=sys.stderr)
            return 2
    readings = check_names(args.names, cfg, method)
    print(format_readings(readings), end="")
    return 0 if all(r.ok for r in readings) else 1


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
    from ionomos import attention

    attention.resolve_where(cfg.log_dir, kind="search_failed", job_id=job.id)
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


def cmd_bundle(args) -> int:
    from ionomos import bundle

    return bundle.run_cli(args)


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


# what `analyze --method` takes besides the config's own method keys
ANALYZE_METHODS = ("isoDTB", "TMT", "DIA", "LFQ", "DIA-NN", "MaxQuant", "Sage", "Spectronaut", "AlphaDIA",
                   "MSstats", "MSstatsTMT", "PD", "table", "auto")


def cmd_analyze(args) -> int:
    """Re-run the downstream analysis for a job (by id) or any experiment / FragPipe folder."""
    from ionomos import postprocess

    cfg = None
    try:
        cfg = load(args.config, check_paths=False)
    except ConfigError:
        pass  # analysing a folder works without a lab config (defaults)
    if args.method and args.method not in ANALYZE_METHODS:
        # a method of the lab's own: postprocess.prepare reads it as its kind (like: / engine:, D54)
        own = list(cfg.methods) if cfg is not None else []
        key = next((k for k in own if k.lower() == args.method.lower()), None)
        if key is None:
            print(f"--method {args.method!r} is not one of {', '.join([*ANALYZE_METHODS, *own])}", file=sys.stderr)
            return 2
        args.method = key
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
    table = None
    if dest.is_file():  # any protein / results table (csv, tsv, txt, xlsx): results go to <stem>_ionomos/
        table = dest.resolve()
        try:
            dest = postprocess.table_workspace(table)
        except OSError as exc:
            print(f"cannot create a results folder next to {table}: {exc}", file=sys.stderr)
            return 2
    if not dest.is_dir():
        print(f"not a folder or table: {dest}", file=sys.stderr)
        return 2
    extra = {}
    if args.control:
        extra["control"] = args.control
    if args.compare:
        extra["comparisons"] = args.compare
    for key in ("log2fc", "alpha", "test", "min_valid", "de_type", "imputation", "normalize", "filter_condition_pct",
                "filter_global_pct"):
        if getattr(args, key) is not None:
            extra[key] = getattr(args, key)
    if args.raw_p:
        extra["use_adjusted"] = False
    if args.exclude:
        extra["exclude_samples"] = args.exclude
    if args.no_enrichment:
        extra["enrichment"] = False
    out = postprocess.run_for_folder(dest, cfg, args.method, extra,
                                     progress=(lambda m: print(f"  … {m}", flush=True)) if not args.quiet else None,
                                     table=table)
    _print_outcome(out)
    if cfg is not None:
        job_id = int(target) if target.isdigit() else None
        postprocess.record_issues(cfg.log_dir, dest, out, job_id)
    if out.report:
        print(f"report: {out.report}")
        if args.open:
            _open_report(out.report)
    return 0 if out.report else 1


_EXPORT_SIZES = ("slide169", "slide43", "half", "col1", "col2")  # charts.SIZES without "custom" (a test compares them)


def cmd_export(args) -> int:
    """Figures for slides from a finished analysis: SVG (and PNG with a renderer) in the export style
    (downstream/slides.py, raster.py)."""
    import json

    from ionomos.downstream import charts, raster, sectionfigs, slides

    formats = ("svg", "png") if args.format == "both" else (args.format,)
    renderer = None
    if "png" in formats and not args.list:
        renderer = raster.find(args.renderer)
        if renderer is None:
            print((f"the renderer {args.renderer} was not found. " if args.renderer != "auto" else "") + raster.HOW,
                  file=sys.stderr)
            return 2
    if args.top is not None and not 1 <= args.top <= sectionfigs.MAX_PANELS:
        print(f"--top takes 1 to {sectionfigs.MAX_PANELS}", file=sys.stderr)
        return 2
    features = [x.strip() for x in args.features.split(",") if x.strip()] if args.features else None
    cfg = None
    try:
        cfg = load(args.config, check_paths=False)
    except ConfigError:
        pass  # exporting works without a lab config: the report carries the style it was made with
    target = args.target
    if target.isdigit():
        if cfg is None or not cfg.database.is_file():
            print("no job ledger here; give a folder path instead", file=sys.stderr)
            return 2
        job = Ledger(cfg.database).get(int(target))
        if job is None:
            print(f"no job {target}", file=sys.stderr)
            return 2
        target = job.dest_dir
    flags = {"size": args.preset, "width": args.width, "height": args.height, "unit": args.unit,
             "font_pt": args.font_pt, "font_family": args.font_family, "palette": args.palette, "up": args.up,
             "down": args.down, "neutral": args.neutral, "background": args.background, "line_scale": args.line_scale,
             "point_scale": args.point_scale, "label_count": args.labels, "png_dpi": args.png_dpi,
             "png_scale": args.png_scale}
    flags = {k: v for k, v in flags.items() if v is not None}
    if args.png_scale is not None and args.png_dpi is None:
        flags["png_dpi"] = 0  # a scale asked for wins over a resolution the style may carry
    if (args.width is not None or args.height is not None) and args.preset is None:
        flags["size"] = "custom"
    if args.labels == 0:
        flags["labels"] = "none"
    for off in ("title", "subtitle", "legend", "note"):
        if getattr(args, f"no_{off}"):
            flags[off] = False
    try:
        report = slides.find_report(Path(target))
        d = slides.read_payload(report)
        saved = {}
        if args.style:
            try:
                saved = json.loads(Path(args.style).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise slides.SlidesError(f"cannot read the style file {args.style}: {exc}") from exc
        # the style the report was made with, the lab's style now, a saved style file, the command line
        style = charts.style_from(charts.style_layer(d.get("exportDefaults"), lenient=True),
                                  ((cfg.analysis or {}).get("export") if cfg is not None else None), saved, flags)
        spec = [x for x in args.figures.split(",") if x.strip()] if args.figures else None
        figs = slides.select(d, spec, features, args.top)
    except (slides.SlidesError, charts.StyleError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.list:
        print(slides.listing(d, figs))
        return 0
    for f in figs:
        for n in f.notes:
            print(f"note: {f.name}: {n}", file=sys.stderr)
    folder = Path(args.out) if args.out else report.parent / slides.FOLDER
    try:
        written = slides.write(folder, d, style, generator=f"ionomos export (Ionomos {__version__})", formats=formats,
                               renderer=renderer, figs=figs)
    except raster.RasterError as exc:
        print(f"no PNG made, nothing written: {exc}. --format svg works without a renderer.", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"cannot write to {folder}: {exc}", file=sys.stderr)
        return 1
    if not written:
        print(f"nothing to draw: {report} has none of the figures asked for (ionomos export --list shows what it has)",
              file=sys.stderr)
        return 1
    print(f"style: {charts.style_text(style)}" + (f"; PNG by {renderer}" if renderer else ""))
    for p in written:
        print(f"  {p}")
    print(f"{len(written) - 1} file(s) and {slides.README} in {folder}. Other charts, other cut-offs: open report.html "
          "and use Export.")
    return 0


def _open_report(report) -> None:
    """--open: the report in the default browser. A machine without one (a server, no xdg-open) gets a note."""
    from ionomos.service import open_path

    try:
        open_path(report)
    except OSError as exc:
        print(f"could not open it here ({exc}); copy report.html to a computer with a browser", file=sys.stderr)


def _print_outcome(out) -> None:
    """What `analyze` and `demo` print about a downstream.Outcome (comparisons, notes, issues)."""
    print(f"method: {out.method or 'unknown'}")
    for c in out.summary.get("comparisons", []):
        conf = {"low": "  [LOW CONFIDENCE]", "none": "  [FOLD CHANGE ONLY]"}.get(c.get("confidence"), "")
        print(f"  {c['name']}: {c['up']} up, {c['down']} down of {c['tested']} tested  ({c['table']}){conf}")
        q = out.summary.get("quality") or {}
        more = []
        if (q.get("only_in_one_condition") or {}).get(c["name"]):
            more.append(f"{q['only_in_one_condition'][c['name']]} only in one condition")
        if (q.get("pi0") or {}).get(c["name"]) is not None:
            more.append(f"~{100 * (1 - q['pi0'][c['name']]):.0f}% changed (pi0)")
        if more:
            print("      " + " · ".join(more))
    for w in out.warnings:
        print(f"  note: {w}")
    for i in out.issues:
        print(f"  [{i.severity}] {i.title}: {i.message}")
        for fix in i.fixes[:2]:
            print(f"      → {fix}")


def cmd_demo(args) -> int:
    """Write a small simulated experiment, analyse it and print where the report is. Offline, no lab config."""
    from ionomos import demo

    try:
        folder = demo.pick_folder(args.folder)
    except demo.DemoError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        demo.write_demo(folder)
    except (OSError, demo.DemoError) as exc:
        print(f"cannot write the demo into {folder}: {exc}", file=sys.stderr)
        return 2
    print(f"demo experiment (simulated DIA, 3 conditions x 4 replicates): {folder}")
    out = demo.analyze(folder, progress=(lambda m: print(f"  … {m}", flush=True)) if not args.quiet else None)
    _print_outcome(out)
    if not out.report:
        return 1
    print(f"report: {out.report}")
    print(f"what is planted and what to look for: {folder / 'README.txt'}")
    if args.open:
        _open_report(out.report)
    else:
        print("open it in a browser, or run again with --open")
    return 0


def cmd_compare(args) -> int:
    """Compare an Ionomos analysis with a reference result for the same experiment (downstream/compare.py).
    Reads both, changes neither; writes compare.tsv / .json / .html. Exit 0: every comparison agrees, 1: one
    differs or could not be judged, 2: nothing to compare. The app's Check accuracy runs the same code
    (accuracy.py)."""
    from ionomos import accuracy

    out = accuracy.run_compare(args.analysis, args.reference, print, by=args.by, comparison=args.comparison,
                               ref_comparison=args.ref_comparison, flip=args.flip, alpha=args.alpha,
                               log2fc=args.log2fc, raw_p=args.raw_p, ref_alpha=args.ref_alpha,
                               ref_log2fc=args.ref_log2fc, out=args.out)
    if out.error:
        print(out.error, file=sys.stderr)
        return out.code
    if args.open and out.page:
        _open_report(out.page)
    return out.code


def cmd_benchmark(args) -> int:
    """Accuracy against known truth (downstream/benchmark.py): simulated data over a grid of designs and
    settings, or an analysed mixed-species / spike-in experiment against the expected ratios in a YAML.
    The app's Check accuracy runs the same code (accuracy.py)."""
    from ionomos import accuracy

    if args.folder or args.expected:
        if not (args.folder and args.expected):
            print("a real benchmark needs both: ionomos benchmark FOLDER --expected hye.yaml (see `ionomos help "
                  "benchmark`). Without a folder, the simulated benchmark runs", file=sys.stderr)
            return 2
        out = accuracy.run_benchmark_real(args.folder, args.expected, print, out=args.out)
    else:
        out = accuracy.run_benchmark_simulated(
            args.grid, print, like=args.like, seeds=args.seeds, out=args.out, kind=args.kind,
            progress=None if args.quiet else (lambda m: print(f"  … {m}", flush=True)))
    if out.error:
        print(out.error, file=sys.stderr)
        return out.code
    if args.open and out.page:
        _open_report(out.page)
    return out.code


def cmd_help(args) -> int:
    """Print one help topic and write the full help page (help.html) where the lab's logs are, or in the
    app-data folder; --open shows it in the browser at that topic. Needs no config and no Tk."""
    from ionomos import help as helpdoc

    hid = helpdoc.topic(args.topic) if args.topic else None
    if args.topic and hid is None:
        print(f"no help topic {args.topic!r}. Try an issue code (NO_TABLE), a word (volcano, imputation) or a "
              f"section: {', '.join(s.id for s in helpdoc.sections())}", file=sys.stderr)
        return 1
    print(helpdoc.text(hid) if hid else "\n".join(f"{s.title}   (ionomos help {s.id})" for s in helpdoc.sections()))
    log_dir = None
    if not args.out:
        try:
            log_dir = load(args.config, check_paths=False).log_dir
        except Exception:  # noqa: BLE001 - no lab config (a pip install): the app-data folder instead
            log_dir = None
    try:
        out = Path(args.out) if args.out else helpdoc.default_dir(log_dir)
        page = helpdoc.write_page(out, hid)
    except OSError as exc:
        print(f"could not write the help page: {exc}", file=sys.stderr)
        return 2
    print(f"\nfull help: {page}" + ("" if args.open else "  (--open shows it in the browser)"))
    if args.open:
        _open_report(page)
    return 0


def cmd_ask(args) -> int:
    """Ask the local assistant (assistant/, D49 / D57). Read-only. When the assistant is not set up, the model
    is not answering or its answer can't be backed by what Ionomos knows, Ionomos's own text is printed."""
    import json

    from ionomos import assistant

    cfg = _load(args, check_paths=False)
    ans = assistant.ask(cfg, " ".join(args.question), experiment=args.experiment, item_id=args.item)
    if args.json:
        print(json.dumps(ans.as_dict(), indent=2, default=str))
        return 0
    print(ans.text)
    if ans.sources:
        print("\nSources:")
        for line in ans.sources:
            print(f"  {line}")
    print(f"\n(assistant: {ans.outcome}" + (f", model {ans.model}" if ans.grounded else "") + ")")
    return 0


def cmd_ask_eval(args) -> int:
    """Score a model over the assistant's scenario corpus (assistant/evaluate.py, D72). 0: every exit criterion
    that was measured is met; 1: not (or the runtime stopped answering); 2: nothing was asked (refused)."""
    from datetime import datetime

    from ionomos.assistant import evaluate, fake, runtime

    cfg = _load(args, check_paths=False)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    server = fake.ScriptedServer(delay=0.01) if args.scripted else None
    try:
        settings = evaluate.settings_for_eval(
            cfg.assistant, base_url=server.base_url if server else args.base_url,
            model=fake.MODEL if server else args.model, stream=args.stream)
        selected = evaluate.select(args.only)
        json_path, txt_path = evaluate.out_paths(args.out, stamp)
        workdir = Path(args.workdir) if args.workdir else evaluate.default_workdir(stamp)
    except evaluate.EvalError as exc:
        print(f"ask-eval: {exc}", file=sys.stderr)
        return 2
    if args.mode == "auto":
        searching = lambda: runtime.search_running(cfg.log_dir)[0]  # noqa: E731
    else:
        searching = args.mode == "searching"
    print(f"Scoring model {settings['model']} at {settings['base_url']} on {len(selected)} scenarios; "
          f"fixture states in {workdir}")
    try:
        if server:
            with server:
                card = evaluate.run(settings, workdir=workdir, only=args.only, searching=searching, progress=print,
                                    before=lambda s: server.use(s["model"]))
        else:
            card = evaluate.run(settings, workdir=workdir, only=args.only, searching=searching, progress=print)
        evaluate.write(card, json_path, txt_path)
    except evaluate.EvalError as exc:
        print(f"ask-eval: {exc}", file=sys.stderr)
        return 2
    print()
    print(evaluate.table(card), end="")
    print(f"\nscorecard: {json_path}\n           {txt_path}")
    return 0 if evaluate.meets_exit_criteria(card) else 1


def cmd_attention(args) -> int:
    """What needs a person: list, show one, dismiss."""
    from ionomos import attention

    cfg = _load(args, check_paths=False)
    if args.action == "dismiss":
        ok = attention.dismiss(cfg.log_dir, args.item)
        print("dismissed" if ok else f"no item {args.item}")
        return 0 if ok else 1
    its = attention.items(cfg.log_dir)
    if args.action == "show":
        it = attention.get(cfg.log_dir, args.item)
        if it is None:
            print(f"no item {args.item}", file=sys.stderr)
            return 1
        print(f"{it.title}\n{it.message}\n")
        for c in it.causes:
            print(f"  likely: {c}")
        for f in it.fixes:
            print(f"  do: {f}")
        if it.details:
            print("\n" + it.details)
        return 0
    if not its:
        print("nothing needs attention")
        return 0
    for it in its:
        print(f"{it.id:<48} [{it.severity:<7}] {it.kind:<16} {it.title}")
    return 0


def cmd_qc_trend(args) -> int:
    """(Re)build logs/qc_trend.html from the QC store; --rebuild (or an empty store) first re-reads every past
    QC-standard run under users_root (read-only)."""
    from ionomos import qctrend

    cfg = _load(args, check_paths=False)
    s = qctrend.settings_of(cfg)
    rows = qctrend.load(cfg.log_dir)
    if args.rebuild or not rows:
        print(f"looking for QC-standard runs under {cfg.users_root} (read-only) …", flush=True)
        found, notes = qctrend.scan(cfg)
        for n in notes[:20]:
            print(f"  note: {n}")
        try:
            qctrend.append(cfg.log_dir, found)
            if found:
                qctrend.compact(cfg.log_dir)
        except OSError as exc:
            print(f"cannot write the QC store in {cfg.log_dir}: {exc}", file=sys.stderr)
            return 2
        print(f"  {len(found)} QC run(s) found")
        rows = qctrend.load(cfg.log_dir)
    try:
        page = qctrend.write_page(cfg.log_dir, s, rows)
    except OSError as exc:
        print(f"cannot write the QC page in {cfg.log_dir}: {exc}", file=sys.stderr)
        return 2
    for ser in qctrend.analyse(rows, s):
        print(f"  {ser['name']}: {len(ser['runs'])} run(s), {ser['status']} — {ser['verdict']}")
    if not rows:
        print("  no QC-standard runs yet (qc_trend.match: " + ", ".join(s["match"]) + ")")
    print(f"page: {page}")
    if args.open:
        _open_report(page)
    return 0


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


def cmd_notify_test(args) -> int:
    """Send a test message on every channel in config.yaml notify: and say what happened to each. The app's
    Notifications tab -> Send test runs the same code (notify.run_test)."""
    from ionomos import notify

    cfg = _load(args, check_paths=False)
    return notify.run_test(cfg.notify, lambda line: print(line, flush=True))


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
    pf = sub.add_parser("preflight", help="check FragPipe without a search: starts it for --help and a dry run "
                                          "of each method")
    pf.add_argument("--method", action="append", help="only this method (repeatable)")
    pf.add_argument("--raw", metavar="PATH", help="a real .raw file or folder for the dry run's file list "
                                                  "(default: a placeholder file)")
    pf.add_argument("--no-dry-run", action="store_true", help="start FragPipe for --help only")
    pf.add_argument("--static", action="store_true", help="read the disk only; start nothing")
    pf.set_defaults(fn=cmd_preflight)
    s = sub.add_parser("status", help="list jobs")
    s.add_argument("--all", action="store_true", help="include done jobs")
    s.set_defaults(fn=cmd_status)
    d = sub.add_parser("dry-run", help="show what would happen to a folder; touches nothing")
    d.add_argument("folder")
    d.set_defaults(fn=cmd_dry_run)
    nm = sub.add_parser("names", help="check how folder and .raw names are read with this config")
    nms = nm.add_subparsers(dest="names_cmd", required=True)
    nt = nms.add_parser("test", help="print how each name parses, or why it is rejected; touches nothing")
    nt.add_argument("names", nargs="+", metavar="NAME",
                    help="folder names, .raw file names, or folders on disk; .raw names after a folder name are "
                         "read as that folder's files")
    nt.add_argument("--method", help="read .raw names as this method (default: from the folder or file name)")
    nt.set_defaults(fn=cmd_names)
    rt = sub.add_parser("retry", help="re-queue a failed job")
    rt.add_argument("job_id", type=int)
    rt.set_defaults(fn=cmd_retry)
    dg = sub.add_parser("diagnose", help="print + save a diagnostics report")
    dg.add_argument("--zip", nargs="?", const="auto", metavar="PATH",
                    help="write a .zip bundle (report, logs, failed jobs' FragPipe logs) instead")
    dg.set_defaults(fn=cmd_diagnose)
    from ionomos import bundle

    bundle.add_parser(sub).set_defaults(fn=cmd_bundle)
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
    az.add_argument("target", help="job id, experiment folder, a results folder from FragPipe, DIA-NN, MaxQuant, "
                                   "Spectronaut, AlphaDIA, or any protein / results table (.csv .tsv .txt .xlsx .parquet)")
    az.add_argument("--method", default=None, metavar="METHOD",
                    help=f"one of {', '.join(ANALYZE_METHODS)}, or a method of your config.yaml (analysed as the "
                         "method it is like:). Default: from ionomos.json, else detected from the files (FragPipe, "
                         "DIA-NN, MaxQuant, Spectronaut, AlphaDIA, MSstats / MSstatsTMT format, Proteome "
                         "Discoverer, any table)")
    az.add_argument("--control", help="control condition (default: recognised by name, e.g. DMSO)")
    az.add_argument("--compare", action="append", metavar="'A vs B'", help="comparison; repeatable")
    az.add_argument("--log2fc", type=float, help="fold-change threshold (log2)")
    az.add_argument("--alpha", type=float, help="significance threshold")
    az.add_argument("--test", choices=["limma", "moderated", "welch", "student"])
    az.add_argument("--de-type", dest="de_type", choices=["control", "all", "others"],
                    help="each condition vs the control | every pair | each vs the rest")
    az.add_argument("--imputation", choices=["auto", "none", "perseus", "min", "zero", "mindet", "minprob", "knn"])
    az.add_argument("--normalize", choices=["median", "gn", "none"])
    az.add_argument("--filter-condition-pct", dest="filter_condition_pct", type=float,
                    help="keep features measured in >= this %% of one condition (default 50)")
    az.add_argument("--filter-global-pct", dest="filter_global_pct", type=float)
    az.add_argument("--exclude", action="append", metavar="SAMPLE", help="leave a sample out; repeatable")
    az.add_argument("--no-enrichment", action="store_true", help="skip gene-set enrichment")
    az.add_argument("--min-valid", dest="min_valid", type=int)
    az.add_argument("--raw-p", action="store_true", help="apply alpha to raw p-values instead of adjusted p")
    az.add_argument("--quiet", action="store_true", help="no progress lines")
    az.add_argument("--open", action="store_true", help="open the report when done")
    az.set_defaults(fn=cmd_analyze)
    ex = sub.add_parser("export", help="figures for slides (SVG) from a finished analysis, in the export style")
    ex.add_argument("target", help="job id, experiment folder, its results folder, or a report.html")
    ex.add_argument("--preset", choices=list(_EXPORT_SIZES),
                    help="size: 16:9 slide (1280x720 px), 4:3 slide, half a slide, journal column (85 mm), "
                         "two columns (180 mm). Default: the lab's analysis.export, else slide169")
    ex.add_argument("--width", type=float, help="a custom width (with --height; --unit px or mm)")
    ex.add_argument("--height", type=float)
    ex.add_argument("--unit", choices=["px", "mm"])
    ex.add_argument("--font-pt", dest="font_pt", type=float, help="text size in points in the finished figure")
    ex.add_argument("--font-family", dest="font_family", help="font name, e.g. Arial or 'Times New Roman'")
    ex.add_argument("--palette", choices=["default", "colorblind", "grey", "custom"])
    ex.add_argument("--up", help="colour of up hits with --palette custom, e.g. '#d55e00'")
    ex.add_argument("--down")
    ex.add_argument("--neutral")
    ex.add_argument("--background", choices=["light", "dark", "transparent"])
    ex.add_argument("--line-scale", dest="line_scale", type=float, help="line width as a multiple (default 1)")
    ex.add_argument("--point-scale", dest="point_scale", type=float, help="point size as a multiple (default 1)")
    ex.add_argument("--labels", type=int, metavar="N", help="name the N most significant hits (0: none)")
    for off in ("title", "subtitle", "legend", "note"):
        ex.add_argument(f"--no-{off}", dest=f"no_{off}", action="store_true",
                        help=f"leave the {'cut-offs line' if off == 'note' else off} out")
    ex.add_argument("--figures", help="which, comma-separated: kinds (volcano, pca, heatmap, correlation, dose_potency, "
                                      "dose_curves, time_patterns, time_profiles, liganded_rank, liganded_selectivity), "
                                      "groups (dose, time, liganded), or names from --list (* and ? allowed). Default: all")
    ex.add_argument("--list", action="store_true", help="list the figures this report can draw and what can be chosen "
                                                        "for each; write nothing")
    ex.add_argument("--features", metavar="NAMES",
                    help="comma-separated genes, proteins or sites (* and ? allowed) for the dose-response curves, the "
                         "time-course profiles and the liganded-site figures (default: the most relevant)")
    ex.add_argument("--top", type=int, metavar="N", help="how many curves / time profiles without --features (default 6)")
    ex.add_argument("--format", default="svg", choices=["svg", "png", "both"],
                    help="svg (default), png or both. PNG needs cairosvg, resvg, rsvg-convert or Inkscape on this "
                         "computer; its size follows png_scale / png_dpi of the style")
    ex.add_argument("--renderer", default="auto", choices=["auto", "cairosvg", "resvg", "rsvg-convert", "inkscape"],
                    help="which program draws the PNG (default: the first one found)")
    ex.add_argument("--png-dpi", dest="png_dpi", type=float, metavar="DPI",
                    help="PNG resolution in dots per inch, 72 to 1200 (e.g. 300 for print); default: the style's")
    ex.add_argument("--png-scale", dest="png_scale", type=float, metavar="K",
                    help="PNG pixels per px of the figure, 1 to 4 (default 2: a 16:9 slide is 2560 x 1440)")
    ex.add_argument("--style", metavar="FILE", help="an export style saved from the report (export_style.json)")
    ex.add_argument("--out", metavar="DIR", help="where to write (default: <results>/figures)")
    ex.set_defaults(fn=cmd_export)
    dm = sub.add_parser("demo", help="write a small simulated experiment and its report (offline; try this first)")
    dm.add_argument("folder", nargs="?", help="a new or empty folder (default: ./ionomos_demo, or ionomos_demo_2, "
                                              "... if taken; nothing existing is touched)")
    dm.add_argument("--quiet", action="store_true", help="no progress lines")
    dm.add_argument("--open", action="store_true", help="open the report when done")
    dm.set_defaults(fn=cmd_demo)
    cp = sub.add_parser("compare", help="compare an Ionomos analysis with a reference result for the same experiment",
                        description="Compare an Ionomos analysis with another result for the same experiment: "
                                    "features matched by ID or gene, agreement of log2 fold changes (Pearson, "
                                    "Spearman, slope, offset), of hit calls and of p-values, the largest "
                                    "disagreements, and a verdict (agrees / agrees after an offset / differs). "
                                    "Writes compare.tsv, compare.json and compare.html into the analysis' results "
                                    "folder. Reads both results and changes neither.")
    cp.add_argument("analysis", help="the Ionomos analysis: an experiment folder (with results/analysis.json), its "
                                     "results folder, or a <table>_ionomos folder")
    cp.add_argument("reference", help="the reference: another analysed folder, or a results table with a fold-change "
                                      "and a p-value column per comparison (FragPipe-Analyst export, limma topTable, "
                                      "MSstats, Perseus, R output; .tsv .csv .txt .xlsx)")
    cp.add_argument("--comparison", metavar="'A vs B'", help="compare only this comparison of the analysis")
    cp.add_argument("--ref-comparison", dest="ref_comparison", metavar="NAME",
                    help="the reference's comparison to use (default: matched by name)")
    cp.add_argument("--by", choices=["auto", "id", "gene"], default="auto",
                    help="match features by protein ID or by gene (default: whichever matches more)")
    cp.add_argument("--flip", action="store_true", help="the reference is the comparison the other way round")
    cp.add_argument("--alpha", type=float, help="common significance cut-off (default: the analysis' own)")
    cp.add_argument("--log2fc", type=float, help="common fold-change cut-off (default: the analysis' own)")
    cp.add_argument("--raw-p", action="store_true", help="apply the common alpha to raw p instead of adjusted p")
    cp.add_argument("--ref-alpha", dest="ref_alpha", type=float,
                    help="the reference's own alpha, when its table has no significance column")
    cp.add_argument("--ref-log2fc", dest="ref_log2fc", type=float, help="the reference's own fold-change cut-off")
    cp.add_argument("--out", metavar="DIR", help="where to write (default: the analysis' results folder)")
    cp.add_argument("--open", action="store_true", help="open compare.html when done")
    cp.set_defaults(fn=cmd_compare)
    bm = sub.add_parser("benchmark", help="accuracy against known truth: simulated data, or a mixed-species run",
                        description="Without a folder: run the analysis on simulated data with planted changes over "
                                    "a grid (replicates 2 to 6, 2 controls vs 4 treated, effect sizes, missing "
                                    "values) and report sensitivity, the observed false discovery proportion "
                                    "against the nominal alpha and the fold-change bias for each imputation / "
                                    "normalisation setting. --kind isodtb: site ratios (replicates, sites changed "
                                    "one way, a heavy / light mixing error); --kind tmt: several TMT plexes with a "
                                    "pooled reference (IRS settings, a pulldown). With FOLDER and --expected: "
                                    "compare an analysed mixed-species (human / yeast / E. coli) or spike-in "
                                    "experiment with the expected ratio per species or protein list.")
    bm.add_argument("folder", nargs="?", help="an analysed benchmark experiment (with --expected)")
    bm.add_argument("--expected", metavar="YAML", help="expected ratios per species or protein list, e.g. "
                                                       "expected: {HUMAN: 1, YEAST: 2, ECOLI: 0.25}")
    bm.add_argument("--grid", choices=["quick", "standard"], default="standard",
                    help="simulated: quick (seconds) or standard (about a minute; default)")
    bm.add_argument("--kind", choices=["dia", "isodtb", "tmt"],
                    help="simulated: the kind of data (default dia: label-free protein intensities; with --like the "
                         "experiment's own kind)")
    bm.add_argument("--like", metavar="FOLDER", help="simulated: add the settings and group sizes of this analysed "
                                                     "experiment, and write into its results folder")
    bm.add_argument("--seeds", type=int, help="simulated: tables per scenario (default 5; quick 2)")
    bm.add_argument("--out", metavar="DIR", help="where to write (default: ./ionomos_benchmark, or the experiment's "
                                                 "results folder)")
    bm.add_argument("--quiet", action="store_true", help="no progress lines")
    bm.add_argument("--open", action="store_true", help="open the page when done")
    bm.set_defaults(fn=cmd_benchmark)
    hp = sub.add_parser("help", help="plain-language help: a topic here, the full help page in the browser")
    hp.add_argument("topic", nargs="?", help="an issue code (NO_TABLE), a word (volcano, imputation), a section "
                                             "(start, report, glossary, trouble, safety, faq) or a topic id")
    hp.add_argument("--open", action="store_true", help="open help.html in the browser, at the topic")
    hp.add_argument("--out", metavar="DIR", help="folder for help.html (default: the log folder, else app data)")
    hp.set_defaults(fn=cmd_help)
    ak = sub.add_parser("ask", help="ask the local assistant about a job, an issue or the help (read-only)")
    ak.add_argument("question", nargs="+", help="the question, in plain words")
    ak.add_argument("--experiment", metavar="JOB_ID|NAME", help="the job the question is about")
    ak.add_argument("--item", metavar="ID", help="an attention item (ionomos attention lists them)")
    ak.add_argument("--json", action="store_true", help="print the answer with its tool calls and citations as JSON")
    ak.set_defaults(fn=cmd_ask)
    ae = sub.add_parser("ask-eval", help="score a model on this PC over the assistant's scenario corpus (scorecard)")
    ae.add_argument("--base-url", metavar="URL", help="the runtime's OpenAI-compatible address (default: "
                                                      "assistant.base_url); only this PC is accepted")
    ae.add_argument("--model", metavar="NAME", help="the model's name in the runtime (default: assistant.model)")
    ae.add_argument("--out", metavar="FILE", help="the scorecard's JSON file (the table goes beside it as .txt; "
                                                  "default: app data, assistant-scorecard-<time>.json); never replaced")
    ae.add_argument("--only", metavar="IDS", help="only these scenarios and/or states, comma-separated")
    ae.add_argument("--workdir", metavar="DIR", help="a new folder for the fixture states (default: "
                                                     "C:/ionomos-ask-eval/<time> on Windows, the temp folder elsewhere)")
    ae.add_argument("--mode", choices=["auto", "idle", "searching"], default="auto",
                    help="auto: use assistant.while_searching whenever this lab's worker is running a search")
    ae.add_argument("--stream", action=argparse.BooleanOptionalAction, default=None,
                    help="stream replies (needed for time to first token; default: assistant.stream)")
    ae.add_argument("--scripted", action="store_true",
                    help="no model: a scripted one on 127.0.0.1 checks the runner and the fixture states here")
    ae.set_defaults(fn=cmd_ask_eval)
    at = sub.add_parser("attention", help="what needs a person (analysis decisions, failed searches, ...)")
    at.add_argument("action", nargs="?", choices=["list", "show", "dismiss"], default="list")
    at.add_argument("item", nargs="?", help="item id (from the list)")
    at.set_defaults(fn=cmd_attention)
    qt = sub.add_parser("qc-trend", help="instrument QC: trend the QC-standard runs (HeLa, K562 ...) in logs/qc_trend.html")
    qt.add_argument("--rebuild", action="store_true",
                    help="re-read every past QC run under users_root first (read-only; kept runs are updated)")
    qt.add_argument("--open", action="store_true", help="open the page when done")
    qt.set_defaults(fn=cmd_qc_trend)
    cn = sub.add_parser("cancel", help="cancel a queued or running job")
    cn.add_argument("job_id", type=int)
    cn.set_defaults(fn=cmd_cancel)
    sub.add_parser("pause", help="start no new FragPipe searches").set_defaults(fn=cmd_pause)
    sub.add_parser("resume", help="undo pause").set_defaults(fn=cmd_pause)
    sub.add_parser("notify-test", help="send a test message to the channels in config.yaml notify:").set_defaults(
        fn=cmd_notify_test)
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
    if argv[:1] == ["sage-job"]:  # hidden: the two steps of an `engine: sage` job (convert, search), run by the worker
        from ionomos.sage import run_job

        return run_job(argv[1:])
    if argv[:1] == ["fake-sage"]:  # hidden: the testbed's stand-ins for Sage and ThermoRawFileParser (engine: sage)
        from ionomos.testbed import fake_sage

        return fake_sage(argv[1:])
    if argv[:1] == ["fake-rawparser"]:
        from ionomos.testbed import fake_rawparser

        return fake_rawparser(argv[1:])
    if argv[:1] == ["fake-maxquant"]:  # hidden: the testbed's stand-in for MaxQuantCmd (engine: maxquant)
        from ionomos.testbed import fake_maxquant

        return fake_maxquant(argv[1:])
    if argv[:1] == ["fake-diann"]:  # hidden: the testbed's stand-in for DIA-NN (engine: diann)
        from ionomos.testbed import fake_diann

        return fake_diann(argv[1:])
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
