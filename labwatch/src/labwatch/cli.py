"""
Command line.

    labwatch run      [--config PATH]              watcher + intake, forever (worker: Phase 2)
    labwatch status   [--config PATH] [--all]      jobs from the ledger
    labwatch dry-run  FOLDER [--config PATH]       parse + validate + show the plan; touches nothing
    labwatch retry    JOB_ID [--config PATH]       failed -> queued

--config defaults to $LABWATCH_CONFIG, then ./config.yaml.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from labwatch import __version__
from labwatch.config import Config, ConfigError, load
from labwatch.intake import IntakeError, intake, plan
from labwatch.ledger import Ledger
from labwatch.watcher import Watcher

log = logging.getLogger("labwatch")


def _setup_logging(log_dir: Path | None, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
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
        raise SystemExit(2)
    for w in cfg.warnings:
        log.warning("config: %s", w)
    return cfg


# ---------------------------------------------------------------- commands --


def cmd_run(args) -> int:
    cfg = _load(args, check_paths=True)
    _setup_logging(cfg.log_dir, args.verbose)
    log.info("labwatch %s starting", __version__)
    ledger = Ledger(cfg.database)
    for jid in ledger.recover_on_startup():
        log.warning("job %d was running at shutdown; marked failed", jid)

    def on_stable(folder: Path) -> bool:
        intake(folder, cfg, ledger)
        return True  # moved or rejected either way

    Watcher(cfg.inbox, on_stable, cfg.poll_seconds, cfg.stable_seconds, cfg.min_raw_files).run_forever()
    return 0


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
        print(f"WOULD REJECT: {folder.name}\n  reason: {exc}")
        return 1
    f = p.folder
    print(f"WOULD ACCEPT: {f.original}")
    if f.safe != f.original:
        print(f"  renamed to : {f.safe}")
    print(f"  user       : {f.user}")
    print(f"  method     : {f.method}  (workflow {cfg.methods[f.method].workflow})")
    print(f"  date       : {f.date or '(not in name; would use drop date)'}")
    print(f"  destination: {p.dest}")
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


# ------------------------------------------------------------------- main --


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="labwatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.environ.get("LABWATCH_CONFIG", "config.yaml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"labwatch {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="watch the inbox forever").set_defaults(fn=cmd_run)
    s = sub.add_parser("status", help="list jobs")
    s.add_argument("--all", action="store_true", help="include done jobs")
    s.set_defaults(fn=cmd_status)
    d = sub.add_parser("dry-run", help="show what would happen to a folder; touches nothing")
    d.add_argument("folder")
    d.set_defaults(fn=cmd_dry_run)
    r = sub.add_parser("retry", help="re-queue a failed job")
    r.add_argument("job_id", type=int)
    r.set_defaults(fn=cmd_retry)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
