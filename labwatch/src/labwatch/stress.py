"""
Stress test: throw a lot of messy drops and some chaos at a real watcher + worker, then check invariants.

    labwatch testbed stress [--n 60] [--seed 1] [--dir DIR] [--keep]

Builds a throw-away lab in DIR (default: a temp folder), runs the watcher and
the FragPipe worker in threads with fast timings and the fake FragPipe, and:

  drops   valid isoDTB/DIA/TMT folders under random names, unicode/emoji/very
          long/punctuation-only names, folders with no raws, empty raws,
          deep nesting, bad tails, exact duplicate names, slow file-by-file
          copies running concurrently, and searches that fail on purpose
  chaos   the worker is killed and restarted mid-search, the ledger is
          locked by another connection for 2 s, a job's labwatch.json is
          corrupted, searches are paused and resumed, a running job is cancelled

Then it waits for everything to settle and checks:

  1. no raw file was lost or duplicated (count and bytes, inbox + users folders)
  2. every drop is accounted for: filed (with a job) or still in the inbox
     (with a .REJECTED.txt note, or no raws so never taken)
  3. no job is left queued/running; done jobs have DONE.txt + output + a
     results/report.html (analysis didn't crash), failed jobs have FAILED.txt
  4. the ledger passes SQLite's integrity check
  5. nothing was logged at CRITICAL (a crash that a supervisor had to catch)

Exit code 0 = all invariants hold. Also run by tests/test_stress.py.
"""
from __future__ import annotations

import logging
import os
import random
import shutil
import sqlite3
import string
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from labwatch import testbed

log = logging.getLogger("labwatch.stress")

WEIRD_NAMES = [
    "ÉJQ_isoDTB_überprobe_2026-09-01", "Isaac_DIA_日本語テスト", "EJQ_isoDTB_🧪🧪_run", "   EJQ isoDTB leading spaces",
    "EJQ_isoDTB_" + "x" * 150, "!!!###$$$", "EJQ.isoDTB.dots.everywhere.", "con", "EJQ_isoDTB_(1)[2]{3}",
    "Isaac_DIA_semi;colon,comma", "EJQ_TMT_O'Brien", "EJQ  isoDTB  double  spaces", "-EJQ_isoDTB-",
]


class _Counter(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@dataclass
class Report:
    drops: dict[str, str] = field(default_factory=dict)  # folder name -> kind
    violations: list[str] = field(default_factory=list)
    outcomes: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    errors_logged: int = 0
    criticals: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def text(self) -> str:
        lines = [f"stress: {len(self.drops)} drops in {self.seconds:.1f}s — "
                 + ", ".join(f"{k}={v}" for k, v in sorted(self.outcomes.items())),
                 f"log: {self.errors_logged} error record(s) (expected: failed searches, rejected garbage), "
                 f"{len(self.criticals)} critical"]
        if self.violations:
            lines.append(f"VIOLATIONS ({len(self.violations)}):")
            lines += [f"  - {v}" for v in self.violations]
        else:
            lines.append("all invariants hold")
        return "\n".join(lines)


def _raw_census(*roots: Path) -> tuple[int, int]:
    n = size = 0
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".raw"):
                    n += 1
                    size += (Path(dirpath) / f).stat().st_size
    return n, size


def _write(p: Path, nbytes: int, rng: random.Random):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(rng.randbytes(nbytes) if nbytes else b"")


def _make_drop(staging: Path, i: int, rng: random.Random, used: set[str]) -> tuple[Path, str]:
    """Build one drop in `staging`; returns (folder, kind)."""
    unusable = False
    kind = rng.choices(
        ["iso", "dia", "tmt", "weird", "noraws", "empty_raw", "deep", "badtail", "fail", "dup"],
        weights=[20, 12, 6, 12, 5, 4, 4, 6, 5, 4])[0]
    user = rng.choice(["EJQ", "Isaac", "IJD", "Chris", "Aman"])
    tag = "".join(rng.choices(string.ascii_lowercase + string.digits, k=5))
    date = f"2026{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
    if kind == "dup" and used:
        name = rng.choice(sorted(used))
    elif kind == "weird":
        name = rng.choice(WEIRD_NAMES) + (f"_{i}" if rng.random() < 0.7 else "")
        if rng.random() < 0.15:
            name = rng.choice(["🧪🧪", "()", "!!!", "—"])  # nothing usable at all
            unusable = True
    elif kind == "fail":
        name = f"{date}_{user}_DIA_FAKEFAIL_{tag}"
    else:
        method = {"iso": "isoDTB", "dia": "DIA", "tmt": "TMT"}.get(kind, rng.choice(["isoDTB", "DIA"]))
        name = f"{date}_{user}_{method}_{tag}"
    folder = staging / f"{i:03d}" / name
    folder.mkdir(parents=True)
    if unusable:  # user + method supplied, so only the name itself is the problem
        (folder / "experiment.yaml").write_text("method: isoDTB\nuser: EJQ\n", encoding="utf-8")
    size = rng.randint(1_000, 20_000)
    if kind in ("iso", "empty_raw", "badtail", "deep") or (kind in ("weird", "dup") and "DIA" not in name):
        reps, fracs = rng.randint(1, 3), rng.randint(1, 4)
        files = [f"S{tag}_{r}_{f}.raw" for r in range(1, reps + 1) for f in range(1, fracs + 1)]
    elif kind == "tmt":
        files = [f"P{tag}_TMT_F{f}.raw" for f in range(1, rng.randint(2, 5))]
        (folder / "experiment.yaml").write_text(
            "tmt:\n  channels: {126: DMSO_1, 127N: DMSO_2, 127C: Drug_1, 128N: Drug_2}\n", encoding="utf-8")
    else:  # dia, fail
        files = [f"{c}_{r}.raw" for c in ("DMSO", "Drug") for r in range(1, rng.randint(2, 4))]
    if kind == "noraws":
        files = []
        (folder / "notes.txt").write_text("forgot the raws\n", encoding="utf-8")
    if kind == "badtail":
        files[-1] = rng.choice([f"S{tag}_final.raw", "🧪.raw", "().raw"])
    base = folder / "a" / "b" / "c" if kind == "deep" else folder
    for f in files:
        _write(base / f, 0 if kind == "empty_raw" else size, rng)
    if rng.random() < 0.3:
        (folder / "method_notes.xlsx").write_bytes(b"PK")
    used.add(name)
    return folder, kind


def run(n: int = 60, seed: int = 1, root: Path | None = None, keep: bool = False, timeout: float = 240,
        chaos: bool = True) -> Report:
    from labwatch import ledger as ledger_mod
    from labwatch.config import load
    from labwatch.intake import intake
    from labwatch.ledger import Ledger
    from labwatch.watcher import Watcher
    from labwatch.worker import Worker, pause, request_cancel, resume

    rng = random.Random(seed)
    rep = Report()
    tmp = None
    if root is None:
        tmp = tempfile.mkdtemp(prefix="labwatch-stress-")
        root = Path(tmp)
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    os.environ.setdefault("LABWATCH_FAKE_FP_SECONDS", "0.3")
    os.environ.pop("LABWATCH_FAKE_FP_MODE", None)
    cfg_path = testbed.init(root / "bed")
    for s in (root / "bed" / "samples").iterdir():  # the stress drops replace the normal samples
        shutil.rmtree(s)
    cfg = load(cfg_path)
    counter = _Counter()
    logging.getLogger("labwatch").addHandler(counter)
    staging = root / "staging"

    used: set[str] = set()
    drops = [_make_drop(staging, i, rng, used) for i in range(n)]
    rep.drops = {f"{f.parent.name}/{f.name}": k for f, k in drops}

    wat = Watcher(cfg.inbox, lambda f: intake(f, cfg, Ledger(cfg.database)), poll_seconds=0.1,
                  stable_seconds=0.6, min_raw_files=1, retry_seconds=0.5)
    workers: list[Worker] = []
    threads: list[threading.Thread] = []

    def start_worker():
        w = Worker(cfg, Ledger(cfg.database), poll_seconds=0.1)
        t = threading.Thread(target=w.run_forever, name=f"worker{len(workers)}", daemon=True)
        workers.append(w)
        threads.append(t)
        t.start()

    wt = threading.Thread(target=wat.run_forever, name="watcher", daemon=True)
    wt.start()
    start_worker()
    t0 = time.monotonic()

    name_locks: dict[str, threading.Lock] = {}
    delivered = [0, 0]
    delivered_lock = threading.Lock()

    def copy_in(folder: Path, slow: bool):
        # drops with the same name take turns for the inbox slot, like a person dragging it in again later
        dest = cfg.inbox / folder.name
        with delivered_lock:
            lock = name_locks.setdefault(folder.name, threading.Lock())
        with lock:
            for _ in range(150):
                if not dest.exists():
                    break
                time.sleep(0.1)
            else:
                return  # slot never freed (the earlier one was rejected and stays): not delivered
            _copy(folder, dest, slow)
        n_, b_ = _raw_census(folder)
        with delivered_lock:
            delivered[0] += n_
            delivered[1] += b_

    def _copy(folder: Path, dest: Path, slow: bool):
        if slow:
            for src in sorted(folder.rglob("*")):
                target = dest / src.relative_to(folder)
                if src.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
                    time.sleep(0.05)
        else:
            shutil.copytree(folder, dest)

    copiers = []
    for folder, _kind in drops:
        t = threading.Thread(target=copy_in, args=(folder, rng.random() < 0.25), daemon=True)
        copiers.append(t)
        t.start()
        time.sleep(rng.uniform(0, 0.05))

    if chaos:
        def chaos_monkey():
            time.sleep(2)
            # 1. kill the worker mid-whatever and start a fresh one (what a crash + supervisor restart does)
            workers[-1].stop()
            threads[-1].join(timeout=30)
            led = Ledger(cfg.database)
            for jid, st in led.recover_on_startup():
                log.info("stress: job %d -> %s after worker restart", jid, st)
            start_worker()
            time.sleep(1)
            # 2. another program locks the ledger for 2 s
            con = sqlite3.connect(cfg.database, timeout=5, check_same_thread=False)
            con.execute("BEGIN EXCLUSIVE")
            time.sleep(2)
            con.rollback()
            con.close()
            # 3. corrupt a queued job's labwatch.json
            for j in led.list("queued")[:1]:
                (Path(j.dest_dir) / "labwatch.json").write_text("{not json", encoding="utf-8")
            # 4. pause, then resume
            pause(cfg.log_dir, "stress")
            time.sleep(1)
            resume(cfg.log_dir)
            # 5. cancel whatever is running
            for _ in range(40):
                running = led.list("running")
                if running:
                    log.info("stress: %s", request_cancel(led, running[0].id))
                    break
                time.sleep(0.1)
            led.close()

        threading.Thread(target=chaos_monkey, name="chaos", daemon=True).start()

    for t in copiers:
        t.join(timeout=60)

    # settle: every inbox folder has a note or no raws, and no job queued/running
    led = Ledger(cfg.database)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        inbox_left = [p for p in cfg.inbox.iterdir() if p.is_dir()]
        unsettled = [p for p in inbox_left
                     if not (cfg.inbox / f"{p.name}.REJECTED.txt").exists() and _raw_census(p)[0] > 0]
        busy = led.list("queued") + led.list("running")
        if not unsettled and not busy and time.monotonic() - t0 > 5:
            break
    else:
        rep.violations.append(f"did not settle within {timeout:.0f}s: "
                              f"{len(unsettled)} inbox folder(s) untaken, {len(busy)} job(s) queued/running")

    wat.stop()
    for w in workers:
        w.stop()
    for t in (wt, *threads):
        t.join(timeout=30)
    rep.seconds = time.monotonic() - t0
    logging.getLogger("labwatch").removeHandler(counter)

    # ---- invariants
    now_n, now_bytes = _raw_census(cfg.inbox, cfg.users_root)
    if (now_n, now_bytes) != tuple(delivered):
        rep.violations.append(f"raw files: delivered {delivered[0]} ({delivered[1]} B), now {now_n} ({now_bytes} B)")
    rep.outcomes["raw files delivered"] = delivered[0]

    jobs = led.list()
    by_dest = {Path(j.dest_dir): j for j in jobs}
    for j in jobs:
        rep.outcomes[j.status] = rep.outcomes.get(j.status, 0) + 1
        dest = Path(j.dest_dir)
        if j.status in ("queued", "running"):
            rep.violations.append(f"job {j.id} left {j.status}: {j.reason}")
        if not dest.is_dir():
            rep.violations.append(f"job {j.id}: folder missing {dest}")
        elif j.status == "done" and not ((dest / "DONE.txt").is_file() and any((dest / "fragpipe").iterdir())):
            rep.violations.append(f"job {j.id} done without DONE.txt/output")
        elif j.status == "done" and not (dest / "results" / "report.html").is_file():
            rep.violations.append(f"job {j.id} done without results/report.html")
        elif j.status == "done" and (dest / "results" / "analysis_error.txt").is_file():
            rep.violations.append(f"job {j.id}: analysis crashed: "
                                  f"{(dest / 'results' / 'analysis_error.txt').read_text(encoding='utf-8')[-300:]}")
        elif j.status == "failed" and not (dest / "FAILED.txt").is_file():
            rep.violations.append(f"job {j.id} failed without FAILED.txt")
    for status_file in cfg.users_root.glob("*/*/labwatch.json"):
        if status_file.parent not in by_dest:
            rep.violations.append(f"filed folder without a job: {status_file.parent}")
    inbox_left = [p for p in cfg.inbox.iterdir() if p.is_dir()]
    rejected = sum(1 for p in inbox_left if (cfg.inbox / f"{p.name}.REJECTED.txt").exists())
    never = sum(1 for p in inbox_left if _raw_census(p)[0] == 0)
    rep.outcomes["rejected (in inbox, with note)"] = rejected
    rep.outcomes["ignored (no raws)"] = never
    accounted = len(jobs) + rejected + never
    # duplicates of a name that was already filed are rejected into the inbox slot one at a time, so
    # count drops by distinct name that reached the inbox
    if accounted < len({f.name for f, _ in drops}) - sum(1 for _f, k in drops if k == "dup"):
        rep.violations.append(f"only {accounted} of {len(drops)} drops accounted for")
    state = ledger_mod.integrity(cfg.database)
    if state != "ok":
        rep.violations.append(f"ledger integrity: {state}")
    rep.errors_logged = sum(1 for r in counter.records if r.levelno >= logging.ERROR)
    rep.criticals = [r.getMessage()[:200] for r in counter.records if r.levelno >= logging.CRITICAL]
    if rep.criticals:
        rep.violations.append(f"{len(rep.criticals)} CRITICAL log record(s): {rep.criticals[:3]}")
    unexpected = [r for r in counter.records if r.levelno >= logging.ERROR and r.exc_info
                  and "could not evaluate" not in r.getMessage()]
    for r in unexpected[:5]:
        rep.violations.append(f"unexpected exception logged: {r.getMessage()[:160]} "
                              f"({r.exc_info[0].__name__}: {r.exc_info[1]})")
    led.close()
    if tmp and not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return rep


def fuzz_names(n: int = 2000, seed: int = 1) -> list[str]:
    """Random folder/file names through the parsers: anything but NamingError is a bug. Returns failures."""
    from labwatch.naming import NamingError, find_date, group_raws, parse_raw_name, sanitize, tokens_of

    rng = random.Random(seed)
    alphabet = string.printable + "äöüéß日本語🧪—–·  "
    parts = ["isoDTB", "DIA", "TMT", "EJQ", "IJD05", "_", "-", " ", "1", "2", "F1", "rep", "frac", ".raw", "2026",
             "20260902", "09-02-2026", "(", ")", "__", "R3", "biorep"]
    failures = []
    for _ in range(n):
        if rng.random() < 0.5:
            name = "".join(rng.choices(alphabet, k=rng.randint(0, 60)))
        else:
            name = "".join(rng.choices(parts, k=rng.randint(1, 10)))
        try:
            try:
                sanitize(name)
            except NamingError:
                pass  # refusing an unusable name is correct; anything else is a bug
            tokens_of(name)
            find_date(name)
            for method in ("isoDTB", "DIA", "TMT"):
                try:
                    parse_raw_name(name + ".raw", method)
                except NamingError:
                    pass
                try:
                    group_raws([name + ".raw", name + "_1_1.raw"], method)
                except NamingError:
                    pass
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name!r}: {type(exc).__name__}: {exc}")
    return failures
