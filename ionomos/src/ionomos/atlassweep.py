"""
The drive sweep (docs/ATLAS.md "The drive sweep", D128): the FragPipe runs on the lab PC's disks that Ionomos did not
search, handed to the cysteine atlas the way a job's search is (atlas.transfer): the result files by harvest's rule,
the decoys (each .pin, gzip-compressed), the sample design, a completion record and a one-row runs CSV.

    found = walk(roots, exclude)              # harvest_fragpipe.ps1's pass 1: the FragPipe run folders under roots
    res = sweep(cfg)                          # one pass: walk, then hand off each run that is new or changed
    res = sweep(cfg, dry_run=True)            # what a pass would send; reads only, writes nothing anywhere
    SweepLoop(get_cfg).run_forever()          # the watcher's thread: a pass every atlas.sweep_hours, when idle
    request_now(log_dir)                      # the app's "Search now": the watcher's thread starts a pass

The rules, as the hand-off's (atlas.py) and harvest's:
  - everything searched is only read: run folders, experiment folders, raw folders. The one write outside the atlas
    folder is the `atlas` block of a done Ionomos job's ionomos.json when the sweep queues the job (mark_pending,
    as the worker does), and Ionomos' own atlas-sweep.json
  - a run goes when its workflow's modifications that are turned on say isoDTB, or TMT with the Folders tab's TMT
    switch on (harvest's masses; atlas.methods' kinds): the atlas counts isoDTB runs and stores TMT runs. A run
    with no psm.tsv (a FragPipe dry run, an unfinished search) never goes
  - in an Ionomos experiment folder only the latest search goes: a ledger job's as the job's own hand-off (with its
    job record), an earlier attempt (fragpipe_previous_<time>) never
  - Ionomos' own folders (the log folder with its FragPipe preflights, the inbox) are not searched, nor the atlas
    and Dropbox folders; the test bed's fake FragPipe runs never go
  - a run with a file made or changed in the last hour may still be being written or copied: it waits
  - no pass while Ionomos is searching; a pass stops between two runs when a search starts, and goes on later
  - a run sent before and unchanged since (its files' names, sizes and times) is not read again
  - a conflict (another copy of a run's file already in Dropbox) waits for a person, as a job's does
  - the copying stops before the free space of the atlas folder's drive falls below atlas.sweep_min_free_gb

Nothing here needs Tk. What a pass found and sent is in <log_dir>/atlas-sweep.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ionomos import atlas, names

log = logging.getLogger("ionomos.atlas.sweep")

CONTRACT = "ionomos-atlas-sweep/1"  # atlas-sweep.json
ATTENTION_KIND = "atlas_sweep_problem"
SETTLE_S = 3600          # a run with a file younger than this may still be being written (FragPipe, a copy)
RESUME_S = 600           # a pass that stopped for a search (or a restart) goes on after this, once Ionomos is idle
HARD_RETRY_S = 3600      # a pass that stopped for a cause (Dropbox gone, the drive full, a bug): tried again after this
POLL_S = 60.0            # how often the watcher's thread looks whether a pass is due
SAVE_S = 30.0            # the state file is written at least this often during a pass
KEEP_PASSES = 20         # pass summaries kept in the state file
DROPBOX_MARKER = ".dropbox"  # the hidden file the Dropbox client keeps at the top of each Dropbox folder

# what each run came to in a pass
SENT, UNCHANGED, JOB, LEFT, WAITING, FAILED, CONFLICT = ("sent", "unchanged", "job", "left", "waiting", "failed",
                                                          "conflict")


# ------------------------------------------------------------------------------------------- the roots --


def fixed_drives() -> list[str]:
    r"""Every fixed drive of this PC (C:\, D:\, ...), as harvest's Win32_LogicalDisk DriveType 3: the lab PC's C: and
    its USB archive disk, not a flash drive, a DVD or a network drive. [] off Windows."""
    if os.name != "nt":
        return []
    import ctypes

    k = ctypes.windll.kernel32
    mask = k.GetLogicalDrives()
    out = []
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(65 + i)}:\\"
            if k.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:  # DRIVE_FIXED
                out.append(root)
    return out


def roots(s: dict) -> list[str]:
    """atlas.sweep_roots, or every fixed drive when it lists none."""
    return list(s["sweep_roots"]) or fixed_drives()


def exclusions(s: dict, env: dict | None = None, cfg=None) -> list[str]:
    """Never searched: the atlas folder, the Dropbox folders (harvest leaves them out: partial copies, and the
    destination lives there), Ionomos' own log folder (its FragPipe preflights are dry runs of real workflows) and
    inbox, and atlas.sweep_exclude."""
    out = [s["handoff_dir"]] if s["handoff_dir"] else []
    out += atlas.dropbox_roots(env)
    out += [str(p) for p in (getattr(cfg, "log_dir", None), getattr(cfg, "inbox", None)) if p]
    out += list(s["sweep_exclude"])
    return list(dict.fromkeys(p for p in out if p))


def _key(path: str | Path) -> str:
    """A folder as harvest compares it: full path, no trailing separator, case ignored (-ieq)."""
    return os.path.normcase(atlas.windows_path(path)).rstrip("\\/").lower()


def _under(k: str, parents: list[str]) -> bool:
    return any(k == p or k.startswith(p + os.sep) or k.startswith(p + "/") for p in parents)


# -------------------------------------------------------------------------------------------- the walk --


@dataclass
class Walk:
    """harvest's pass 1: the run folders under the roots, and what the search met on the way."""
    roots: list[str] = field(default_factory=list)
    runs: list[str] = field(default_factory=list)
    folders: int = 0
    skipped: int = 0
    errors: list[list[str]] = field(default_factory=list)  # [folder, why] for each folder that couldn't be listed
    at: str = ""
    stopped: bool = False


_MARKERS = {m.lower() for m in atlas.RUN_MARKERS}


def walk(roots_: list[str], exclude: list[str] = (), stop=None, progress=None) -> Walk:
    """Every FragPipe run folder under the roots, as harvest_fragpipe.ps1 finds them: a folder holding one of
    atlas.RUN_MARKERS, or one holding psm.tsv whose parent and grandparent are not run folders. Links and junctions
    are never followed (so no folder is met twice, and a root inside another root is walked once), harvest's skipped
    names (Windows, AppData, MSBooster, ...) never entered, the excluded folders and any folder holding Dropbox's
    marker never listed. A folder that can't be listed is noted and passed over. stop(): checked every 500 folders."""
    ex = [_key(e) for e in exclude]
    out = Walk(roots=list(roots_), at=atlas._iso())
    run_roots: dict[str, str] = {}
    psm_dirs: list[str] = []
    tops = [atlas._plain(os.path.abspath(r)) for r in roots_]
    keys = [_key(r) for r in tops]
    tops = [r for i, r in enumerate(tops)
            if keys[i] not in keys[:i] and not _under(keys[i], [k for j, k in enumerate(keys) if j != i and k != keys[i]])]
    for root in tops:
        stack = [root]
        while stack:
            d = stack.pop()
            if _under(_key(d), ex):
                out.skipped += 1
                continue
            out.folders += 1
            if out.folders % 500 == 0:
                if stop is not None and stop():
                    out.stopped = True
                    return out
                if progress is not None and out.folders % 2000 == 0:
                    progress(out.folders, len(run_roots) + len(psm_dirs))
            try:
                with os.scandir(atlas._fs(d)) as it:
                    entries = list(it)
            except OSError as exc:
                out.errors.append([d, exc.strerror or str(exc)])
                continue
            if any(e.name.lower() == DROPBOX_MARKER for e in entries):
                out.skipped += 1  # a Dropbox folder this account's info.json didn't name: copies, never sources
                continue
            marker = psm = False
            for e in entries:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if atlas._reparse(e) or atlas._skip_dir(e.name):
                        out.skipped += 1
                        continue
                    stack.append(os.path.join(d, e.name))
                else:
                    low = e.name.lower()
                    marker |= low in _MARKERS
                    psm |= low == "psm.tsv"
            if marker:
                run_roots[_key(d)] = d
            if psm:
                psm_dirs.append(d)
    runs = dict(run_roots)
    for p in psm_dirs:  # a psm.tsv folder in a run (one or two levels down) is that run's; otherwise a run itself
        parent = os.path.dirname(p)
        if not any(_key(c) in run_roots for c in (parent, os.path.dirname(parent)) if c):
            runs.setdefault(_key(p), p)
    out.runs = sorted(runs.values(), key=str.lower)
    return out


# --------------------------------------------------------------------------------------------- one run --


def kinds(cfg) -> list[str]:
    """The probe classes a found run may go as: the kinds of atlas.methods (isoDTB; TMT with the TMT switch on)."""
    s = atlas.settings(cfg)
    have = {atlas.kind_of(cfg, m) for m in s["methods"]}
    return [k for k in atlas.KINDS if k in have]


def enabled_class(wf: dict) -> str:
    """harvest's probe class of a workflow (atlas.read_workflow_info), from the modifications that are turned on
    only: a row of FragPipe's tables is mass,sites,enabled,max, and a workflow copied from isoDTB with its probe row
    set to false (a label-free search) is no isoDTB search. A row with no enabled field counts."""
    on = []
    for table in (wf.get("VarMods") or "", wf.get("FixMods") or ""):
        for row in table.split(";"):
            f = [x.strip() for x in row.split(",")]
            if len(f) >= 3 and f[2].lower() == "false":
                continue
            on.append(f[0])
    return atlas.probe_class(" ".join(on))


def is_fake(run_dir: Path) -> bool:
    """A run of the test bed's fake FragPipe (fake_fragpipe.py): its fragger.params says so."""
    try:
        with open(atlas._fs(run_dir / "fragger.params"), "rb") as fh:
            return fh.read(16).startswith(b"# FAKE")
    except OSError:
        return False


def fingerprint(kind: str, pin_max_mb: int, picks: list[atlas.Picked]) -> str:
    """What a run's hand-off would take, by name, size and time: the same fingerprint means nothing to read again."""
    rows = sorted([p.dest_rel if p.level != "design" else atlas.windows_path(p.path), p.size, int(p.mtime)]
                  for p in picks if p.take)
    return hashlib.sha256(json.dumps([kind, pin_max_mb, rows]).encode("utf-8")).hexdigest()


@dataclass
class RunResult:
    """What one run found on the drives came to in a pass."""
    run: str
    outcome: str                     # sent | unchanged | job | left | waiting | failed | conflict
    why: str = ""
    owner: str = ""
    kind: str = ""
    job_id: int | None = None
    files: int = 0
    bytes: int = 0
    copied: int = 0
    pins: int = 0
    design: int = 0
    to_copy: int = 0                 # a dry run: files not at the destination by name and size
    mb_to_copy: float = 0.0
    record: str = ""
    handoff_sha256: str = ""
    fingerprint: str = ""
    low_space: bool = False          # not copied: the atlas folder's drive is below atlas.sweep_min_free_gb
    conflicts: list[str] = field(default_factory=list)
    # how it was searched, as its workflow says (for a person reading a dry run: which TMT, which probe on C)
    fragpipe_version: str = ""
    var_mods: str = ""
    fix_mods: str = ""


def _jobs_by_folder(cfg) -> dict[str, object]:
    """Every job of the ledger by its experiment folder (_key)."""
    from ionomos.ledger import Ledger

    db = getattr(cfg, "database", None)
    if db is None or not Path(db).is_file():
        return {}
    led = Ledger(db)
    try:
        return {_key(j.dest_dir): j for j in led.list()}
    finally:
        led.close()


def _status_record(folder: Path) -> dict | None:
    """The Ionomos (or LabWatch) status file in folder; None when it has none, {} when it can't be read."""
    p = names.status_path(folder)
    if not os.path.isfile(atlas._fs(p)):
        return None
    try:
        with open(atlas._fs(p), encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _missing(picks: list[atlas.Picked], run_dest: Path, root: Path) -> list[atlas.Picked]:
    """The files not at the destination yet, by name and size only (a dry run reads no content)."""
    out = []
    for p in picks:
        if not p.take:
            continue
        dst = atlas.destination(p.path, root) if p.level == "design" else run_dest.joinpath(*p.dest_rel.split("/"))
        try:
            there = os.stat(atlas._fs(dst))
        except OSError:
            out.append(p)
            continue
        if not p.gzip and p.level != "design" and there.st_size != p.size:
            out.append(p)
    return out


def _in_ionomos_folder(cfg, rr: RunResult, run_dir: Path, rec: dict, jobs: dict, dry_run: bool) -> bool:
    """A run folder in an Ionomos experiment folder (one with a status file). True when that settles it (rr says
    how); False when it is the folder's latest done search of no job in this ledger (another PC's, LabWatch's),
    which goes as a found run."""
    s = atlas.settings(cfg)
    parent = run_dir.parent
    job = jobs.get(_key(parent))
    if job is not None:
        if _key(atlas.run_folder(job)) != _key(run_dir):
            rr.outcome, rr.job_id = LEFT, job.id
            rr.why = f"an earlier attempt or another search in job {job.id}'s folder (only its latest search goes)"
            return True
        rr.job_id, rr.outcome = job.id, JOB
        if job.status != "done":
            rr.why = f"job {job.id} is {job.status}; it is handed off when it is done, if its method goes"
        elif job.method not in s["methods"]:
            rr.outcome, rr.why = LEFT, f"job {job.id} is {job.method}, which atlas.methods doesn't send"
        else:
            block = atlas.state(job.dest_dir)
            if not block and not dry_run and atlas.mark_pending(cfg, job):
                rr.why = f"queued as job {job.id}'s hand-off (sent with its job record)"
            elif not block:
                rr.why = f"job {job.id}: to be queued as its own hand-off"
            else:
                rr.why = f"job {job.id}: {atlas.describe(block)}"
        return True
    st = str(rec.get("status") or "")
    run = rec.get("run") if isinstance(rec.get("run"), dict) else {}
    latest = Path(run["workdir"]) if run.get("workdir") else parent / "fragpipe"
    if st in ("queued", "running"):
        rr.outcome, rr.why = WAITING, f"in an Ionomos experiment folder whose search is {st}"
    elif st != "done":
        rr.outcome, rr.why = LEFT, f"in an Ionomos experiment folder whose search is {st or 'not known'}"
    elif _key(latest) != _key(run_dir):
        rr.outcome, rr.why = LEFT, "an earlier attempt or another search in an Ionomos experiment folder (only its " \
                                   "latest search goes)"
    else:
        return False
    return True


def consider(cfg, run: str, jobs: dict, prev: dict | None, retry: bool, dry_run: bool,
             now: float | None = None) -> tuple[RunResult, dict | None]:
    """Decide one run folder found on the drives. Returns what it came to and, when it is to go, the plan (run_dir,
    exp_dir, kind, picks; None otherwise). Reads only, but for queueing a done Ionomos job (mark_pending)."""
    s = atlas.settings(cfg)
    run_dir = Path(run)
    rr = RunResult(atlas.windows_path(run_dir), "", owner=atlas.owner_guess(run))
    rec = _status_record(run_dir.parent)
    if rec is not None and _in_ionomos_folder(cfg, rr, run_dir, rec, jobs, dry_run):
        return rr, None
    wf_path = run_dir / "fragpipe.workflow"
    wf = atlas.read_workflow_info(wf_path) if os.path.isfile(atlas._fs(wf_path)) else {"ProbeClass": "unknown"}
    by_masses = wf.get("ProbeClass") or "unknown"
    rr.kind = kind = enabled_class(wf) if by_masses != "unknown" else by_masses
    rr.fragpipe_version, rr.var_mods, rr.fix_mods = wf.get("Version", ""), wf.get("VarMods", ""), wf.get("FixMods", "")
    if kind not in kinds(cfg):
        rr.outcome = LEFT
        rr.why = f"probe class {kind}" + (" (the TMT switch is off)" if kind == "TMT" else "") + \
            (f" by its enabled modifications (harvest reads {by_masses})" if kind != by_masses else "")
        return rr, None
    if is_fake(run_dir):
        rr.outcome, rr.why = LEFT, "made by the test bed's fake FragPipe"
        return rr, None
    try:
        picks = atlas.select(run_dir, pin_max_mb=s["pin_max_mb"])
    except OSError as exc:
        rr.outcome, rr.why = FAILED, f"can't be listed: {exc.strerror or exc}"
        return rr, None
    if not any(p.take and Path(p.rel).name.lower() == "psm.tsv" for p in picks):
        rr.outcome, rr.why = LEFT, "no psm.tsv (a FragPipe dry run, or a search that didn't finish)"
        return rr, None
    newest = max((max(p.mtime, p.born) for p in picks), default=0.0)
    age = (now or time.time()) - newest
    if age < SETTLE_S:
        rr.outcome, rr.why = WAITING, (f"a file was made or changed {max(age, 0) / 60:.0f} min ago; it may still be "
                                       "being written or copied")
        return rr, None
    mf_path = run_dir / atlas.MANIFEST_FILE
    mf = atlas.read_manifest_info(mf_path) if os.path.isfile(atlas._fs(mf_path)) else {"rows": []}
    exp_dir = atlas.experiment_folder(run_dir)
    every = picks + atlas.design_files(exp_dir, mf["rows"], run_dir=run_dir)
    rr.fingerprint = fingerprint(kind, s["pin_max_mb"], every)
    taken = [p for p in every if p.take]
    rr.files, rr.bytes = len(taken), sum(p.size for p in taken)
    if prev and prev.get("fingerprint") == rr.fingerprint:
        if prev.get("state") == SENT:
            rr.outcome, rr.why = UNCHANGED, f"sent {str(prev.get('at') or '')[:16].replace('T', ' ')}, unchanged since"
            rr.record, rr.handoff_sha256 = prev.get("record", ""), prev.get("handoff_sha256", "")
            return rr, None
        if prev.get("state") == CONFLICT and not retry:
            rr.outcome, rr.why = CONFLICT, prev.get("message") or "a different copy is at the atlas folder"
            rr.conflicts = list(prev.get("conflicts") or [])
            return rr, None
    return rr, {"run_dir": run_dir, "exp_dir": exp_dir, "kind": kind, "picks": every}


# ---------------------------------------------------------------------------------------------- a pass --


@dataclass
class Pass:
    """One sweep: the walk, and what each run found came to."""
    trigger: str
    started: str
    finished: str = ""
    dry_run: bool = False
    ok: bool = False
    setup: bool = False              # off, no folder, no roots: nothing was tried
    message: str = ""
    stopped: str = ""                # why the pass ended before its last run ("" = it went through)
    soft: bool = False               # stopped for a search, a restart or another pass: it goes on by itself soon
    walk: Walk | None = None
    runs: list[RunResult] = field(default_factory=list)

    def counts(self) -> Counter:
        return Counter(r.outcome for r in self.runs)

    def summary(self) -> dict:
        c = self.counts()
        sent = [r for r in self.runs if r.outcome == SENT]
        return {"trigger": self.trigger, "started": self.started, "finished": self.finished, "dry_run": self.dry_run,
                "ok": self.ok, "stopped": self.stopped, "soft": self.soft, "message": self.message,
                "roots": self.walk.roots if self.walk else [], "folders": self.walk.folders if self.walk else 0,
                "unreadable": len(self.walk.errors) if self.walk else 0, "runs": len(self.runs),
                **{k: c.get(k, 0) for k in (SENT, UNCHANGED, JOB, LEFT, WAITING, FAILED, CONFLICT)},
                "copied": sum(r.copied for r in sent), "mb_copied": round(sum(r.bytes for r in sent) / 1024 ** 2, 1),
                "pins": sum(r.pins for r in sent), "design": sum(r.design for r in sent),
                "left_by": dict(Counter(r.why for r in self.runs if r.outcome == LEFT).most_common(12))}

    def describe(self) -> str:
        c = self.counts()
        head = (f"{self.walk.folders:,} folders searched, " if self.walk else "") + f"{len(self.runs)} FragPipe runs"
        if self.dry_run:
            go = [r for r in self.runs if r.outcome == SENT]
            return (f"{head}: {len(go)} would go to the atlas ({sum(r.to_copy for r in go)} files, "
                    f"{sum(r.mb_to_copy for r in go):,.0f} MB not there yet), {c.get(UNCHANGED, 0)} sent before, "
                    f"{c.get(JOB, 0)} Ionomos jobs, {c.get(LEFT, 0)} not for the atlas, {c.get(WAITING, 0)} wait")
        parts = [f"{c.get(SENT, 0)} sent", f"{c.get(UNCHANGED, 0)} unchanged", f"{c.get(JOB, 0)} Ionomos jobs",
                 f"{c.get(LEFT, 0)} not for the atlas"]
        parts += [f"{c[k]} {k}" for k in (WAITING, FAILED, CONFLICT) if c.get(k)]
        return f"{head}: " + ", ".join(parts) + (f"; stopped: {self.stopped}" if self.stopped else "")


def state_path(cfg) -> Path | None:
    d = getattr(cfg, "log_dir", None)
    return Path(d) / names.ATLAS_SWEEP_STATE if d else None


def load_state(cfg) -> dict:
    """atlas-sweep.json; an empty state when it isn't there or can't be read (then every run is compared with the
    destination again, which copies nothing twice)."""
    p = state_path(cfg)
    try:
        st = json.loads(p.read_text(encoding="utf-8")) if p else {}
    except (OSError, ValueError):
        st = {}
    if not isinstance(st, dict) or st.get("contract") != CONTRACT:
        st = {}
    return {"contract": CONTRACT, "passes": list(st.get("passes") or []), "walk": st.get("walk") or {},
            "runs": dict(st.get("runs") or {})}


def save_state(cfg, st: dict) -> None:
    """Written through a temporary file; the replace is tried again a few times (on Windows it fails while the app
    happens to be reading the file)."""
    p = state_path(cfg)
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
        for i in range(5):
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                if i == 4:
                    raise
                time.sleep(0.2)
    except OSError as exc:
        log.warning("atlas sweep: could not write %s: %s", p, exc)


def busy(cfg) -> bool:
    """A search is running (the ledger has a running job): the sweep leaves the PC to it."""
    from ionomos.ledger import Ledger

    db = getattr(cfg, "database", None)
    if db is None or not Path(db).is_file():
        return False
    try:
        led = Ledger(db)
        try:
            return bool(led.list("running"))
        finally:
            led.close()
    except Exception:  # noqa: BLE001 - a ledger being written: try again between the next two runs
        return False


def sweep(cfg, trigger: str = "auto", dry_run: bool = False, retry: bool | None = None, roots_: list[str] | None = None,
          stop=None, walk_stop=None, progress=None, reuse_walk: bool = False) -> Pass:
    """One pass of the drive sweep. Never raises. dry_run: what a pass would send (reads only, writes nothing, not
    even the state file; needs no folder and works while the hand-off is off). retry: conflicts are tried again
    (default: when a person asked for this pass). stop(): checked between runs (the watcher stopping, a search
    starting); walk_stop(): during the walk (default stop; the watcher passes only its own stopping, as the walk only
    lists folders). progress(text): a line now and then."""
    s = atlas.settings(cfg)
    p = Pass(trigger, atlas._iso(), dry_run=dry_run)
    retry = trigger != "auto" if retry is None else retry
    if not dry_run and not s["enabled"]:
        p.setup, p.message = True, "the atlas hand-off is off (config.yaml atlas.enabled: false)"
        return p
    if not dry_run and not s["handoff_dir"]:
        p.setup, p.message = True, ("atlas.handoff_dir is not set: the Dropbox folder the atlas side reads, e.g. "
                                    "'...\\<team folder>\\fragpipe-runs' (app: Folders tab)")
        return p
    where = list(roots_ or roots(s))
    if not where:
        p.setup, p.message = True, "no folders to search: set atlas.sweep_roots (off Windows there are no fixed drives)"
        return p
    say = progress or (lambda _t: None)
    log_dir = getattr(cfg, "log_dir", None)
    lock = atlas._Lock(log_dir if not dry_run else None, None, name=names.ATLAS_SWEEP_LOCK)
    with lock:
        if not lock.held:
            p.message = p.stopped = "another Ionomos process is searching the drives right now"
            p.soft = True
            return p
        st = load_state(cfg) if not dry_run else {"runs": {}, "walk": {}, "passes": []}
        try:
            _pass(cfg, s, p, st, where, retry, stop, stop if walk_stop is None else walk_stop, say, lock, reuse_walk)
        except Exception as exc:  # noqa: BLE001 - a bug, a locked ledger: the pass is tried again later
            log.exception("atlas sweep: the pass failed")
            p.stopped, p.soft = f"{type(exc).__name__}: {exc}", False
        p.finished = atlas._iso()
        c = p.counts()
        p.ok = not p.stopped and not c.get(FAILED) and not c.get(CONFLICT)
        p.message = p.describe()
        if not dry_run:
            st["passes"] = (st.get("passes") or [])[-(KEEP_PASSES - 1):] + [p.summary()]
            save_state(cfg, st)
    if not dry_run:
        _tell(cfg, p)
    return p


def _pass(cfg, s: dict, p: Pass, st: dict, where: list[str], retry: bool, stop, walk_stop, say, lock,
          reuse_walk: bool) -> None:
    root = Path(s["handoff_dir"]) if s["handoff_dir"] else None
    ex = exclusions(s, cfg=cfg)
    w = st.get("walk") or {}
    if reuse_walk and w.get("roots") == where and w.get("exclude") == ex and w.get("runs") is not None:
        p.walk = Walk(roots=where, runs=list(w["runs"]), folders=int(w.get("folders") or 0), at=str(w.get("at") or ""),
                      errors=list(w.get("errors") or []))
        say(f"the search of {w.get('at', '')[:16].replace('T', ' ')} again: {len(p.walk.runs)} FragPipe runs")
    else:
        say(f"searching {', '.join(where)} for FragPipe runs (leaving out {', '.join(ex) or 'nothing'})")
        p.walk = walk(where, ex, stop=walk_stop,
                      progress=lambda n, r: say(f"  ... {n:,} folders searched, {r} runs so far"))
        if p.walk.stopped:
            p.stopped, p.soft = "Ionomos stopped while the drives were being searched; the next pass starts over", True
            return
        say(f"{p.walk.folders:,} folders searched ({len(p.walk.errors)} unreadable): {len(p.walk.runs)} FragPipe runs")
        if not p.dry_run:
            st["walk"] = {**asdict(p.walk), "exclude": ex, "errors": p.walk.errors[:200]}
    jobs = _jobs_by_folder(cfg)
    last_save = time.monotonic()
    for run in p.walk.runs:
        if stop is not None and stop():
            p.stopped, p.soft = "a search started, or Ionomos is stopping; the pass goes on later", True
            break
        k = _key(run)
        try:
            rr, plan = consider(cfg, run, jobs, st["runs"].get(k), retry, p.dry_run)
        except Exception as exc:  # noqa: BLE001 - one odd folder never stops the pass
            log.exception("atlas sweep: %s", run)
            rr, plan = RunResult(atlas.windows_path(run), FAILED, f"{type(exc).__name__}: {exc}"), None
        if plan is not None:
            if p.dry_run:
                rr.outcome, rr.why = SENT, "would go"
                rr.pins = sum(1 for x in plan["picks"] if x.take and x.gzip)
                rr.design = sum(1 for x in plan["picks"] if x.take and x.level == "design")
                if root is not None:
                    miss = _missing(plan["picks"], atlas.destination(run, root), root)
                    rr.to_copy, rr.mb_to_copy = len(miss), round(sum(x.size for x in miss) / 1024 ** 2, 1)
                else:
                    rr.to_copy, rr.mb_to_copy = rr.files, round(rr.bytes / 1024 ** 2, 1)
            else:
                _send(cfg, root, rr, plan)
                lock_touch(lock)
        p.runs.append(rr)
        if rr.low_space:
            p.stopped = rr.why
            break
        if rr.outcome == FAILED and plan is not None and not os.path.isdir(atlas._fs(root)):
            p.stopped = f"the atlas folder {root} can't be reached: is Dropbox running?"
            break
        if rr.outcome in (SENT, FAILED, CONFLICT) and not p.dry_run:
            _remember(st, k, rr)
            if rr.outcome == SENT:
                say(f"  sent {rr.run} ({rr.kind}, {rr.copied} copied, {rr.pins} .pin, {rr.design} design)")
            else:
                say(f"  not sent: {rr.run}: {rr.why}")
        if not p.dry_run and time.monotonic() - last_save > SAVE_S:
            save_state(cfg, st)
            last_save = time.monotonic()


def lock_touch(lock) -> None:
    """Keep a long pass's lock fresh, so no other process takes it over as left by a dead one (6 hours)."""
    if getattr(lock, "path", None) is not None and lock.held:
        try:
            os.utime(lock.path)
        except OSError:
            pass


def _send(cfg, root: Path, rr: RunResult, plan: dict) -> None:
    s = atlas.settings(cfg)
    try:
        res = atlas.transfer(plan["run_dir"], plan["exp_dir"], root, plan["kind"], s["pin_max_mb"], owner=rr.owner,
                             min_free_gb=s["sweep_min_free_gb"])
    except Exception as exc:  # noqa: BLE001 - Dropbox offline, a full disk: tried again on the next pass
        log.warning("atlas sweep: %s not handed off: %s", rr.run, exc)
        rr.outcome, rr.why = FAILED, str(exc) if isinstance(exc, OSError) else f"{type(exc).__name__}: {exc}"
        return
    rr.copied, rr.pins, rr.design = res.copied, res.pins, res.design
    rr.files, rr.bytes = res.files, res.bytes
    if res.ok:
        rr.outcome, rr.why, rr.record, rr.handoff_sha256 = SENT, res.message, res.record, res.handoff_sha256
        log.info("atlas sweep: %s: %s", rr.run, res.message)
    elif res.low_space:
        rr.outcome, rr.why, rr.low_space = WAITING, res.message, True
    elif res.state == atlas.CONFLICT:
        rr.outcome, rr.why, rr.conflicts = CONFLICT, res.message, res.conflicts[:20]
        log.error("atlas sweep: %s not handed off: %s", rr.run, res.message)
    else:
        rr.outcome, rr.why = FAILED, res.message
        log.warning("atlas sweep: %s not handed off: %s", rr.run, res.message)


def _remember(st: dict, k: str, rr: RunResult) -> None:
    st["runs"][k] = {"run": rr.run, "state": rr.outcome, "owner": rr.owner, "kind": rr.kind,
                     "fingerprint": rr.fingerprint, "at": atlas._iso(), "message": rr.why, "record": rr.record,
                     "handoff_sha256": rr.handoff_sha256, "files": rr.files, "copied": rr.copied, "pins": rr.pins,
                     "design": rr.design, "conflicts": rr.conflicts[:20]}


def _tell(cfg, p: Pass) -> None:
    """One attention item for the sweep (a warning, no pop-up): runs not sent, or a pass that stopped for a cause
    (not for a search or a restart); closed by a pass that goes through with nothing left to a person."""
    try:
        from ionomos import attention

        log_dir = getattr(cfg, "log_dir", None)
        bad = [r for r in p.runs if r.outcome in (FAILED, CONFLICT)]
        hard_stop = bool(p.stopped) and not p.soft
        if not bad and not hard_stop:
            if not p.stopped:
                attention.resolve_where(log_dir, kind=ATTENTION_KIND)
            return
        n_conf = sum(1 for r in bad if r.outcome == CONFLICT)
        causes, fixes = [], []
        if hard_stop:
            causes.append(p.stopped)
        if n_conf:
            causes.append(f"{n_conf} run(s): the atlas folder already holds a different copy of a file (an earlier "
                          "harvest of a folder that changed since, or a copy someone edited); Ionomos never "
                          "overwrites it")
            fixes.append("Ask the person who keeps the atlas which copy is right; they move the old copy aside in "
                         "Dropbox, then press Search now (Folders tab, Cysteine atlas) or run  ionomos atlas sweep")
        if len(bad) > n_conf:
            first = next(r.why for r in bad if r.outcome == FAILED)
            causes.append(f"{len(bad) - n_conf} run(s) could not be copied (first: {first})")
            fixes.append("Nothing to do if Dropbox was only offline: the next pass tries them again")
        if hard_stop:
            fixes.append("Fix the cause, then press Search now (Folders tab) or run  ionomos atlas sweep; it is also "
                         "tried again by itself in an hour")
        attention.raise_item(log_dir, ATTENTION_KIND, "Some FragPipe runs on this PC weren't handed to the atlas",
                             p.describe(), key=ATTENTION_KIND, severity="warning", causes=causes, fixes=fixes,
                             details="\n".join(f"{r.run}: {r.why}" for r in bad[:30]),
                             data={"failed": len(bad) - n_conf, "conflicts": n_conf, "stopped": p.stopped})
    except Exception:  # noqa: BLE001 - telling a person must not break the sweep
        log.exception("could not record %s", ATTENTION_KIND)


# ------------------------------------------------------------------------------------------- the loop --


def request_now(log_dir: Path) -> Path:
    """Ask the watcher's sweep thread for a pass now (the app's Search now); it takes the request within a minute."""
    p = Path(log_dir) / names.ATLAS_SWEEP_NOW
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(atlas._iso() + "\n", encoding="utf-8")
    return p


def last_pass(cfg) -> dict:
    """The summary of the newest pass ({} = none yet)."""
    passes = load_state(cfg).get("passes") or []
    return dict(passes[-1]) if passes else {}


def describe_last(summary: dict) -> str:
    """One line for the newest pass, as the Folders tab and `ionomos atlas status` say it."""
    if not summary:
        return "the drives haven't been searched yet"
    when = str(summary.get("finished") or summary.get("started") or "")[:16].replace("T", " ")
    line = (f"last search {when}: {summary.get('runs', 0)} FragPipe runs, {summary.get(SENT, 0)} sent, "
            f"{summary.get(UNCHANGED, 0)} unchanged, {summary.get(LEFT, 0)} not for the atlas")
    extra = [f"{summary[k]} {k}" for k in (WAITING, FAILED, CONFLICT) if summary.get(k)]
    if extra:
        line += ", " + ", ".join(extra)
    if summary.get("stopped"):
        line += f" (stopped: {summary['stopped']})"
    return line


def _thread_background(on: bool) -> None:
    """Windows: this thread's disk and CPU priority lowered while a pass runs (THREAD_MODE_BACKGROUND_BEGIN), so a
    pass never slows a search or a person at the PC. Nothing elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentThread.restype = wintypes.HANDLE
        k.SetThreadPriority.argtypes = (wintypes.HANDLE, ctypes.c_int)
        k.SetThreadPriority(k.GetCurrentThread(), 0x00010000 if on else 0x00020000)
    except Exception:  # noqa: BLE001
        pass


class SweepLoop:
    """The watcher's sweep thread (cli.cmd_run): while atlas.enabled and atlas.sweep are on, a pass every
    atlas.sweep_hours; while the hand-off is on, a pass now when the app asks (atlas-sweep-now); never while a search
    runs, and a pass that stopped for one goes on RESUME_S after. Reads the config through get_cfg (LiveConfig.get):
    turning it on or off in the app counts without a restart."""

    def __init__(self, get_cfg, poll_seconds: float = POLL_S, heartbeat=None):
        self.get_cfg = get_cfg
        self.poll_seconds = poll_seconds
        self.heartbeat = heartbeat
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def due(self, cfg, now: datetime | None = None) -> str:
        """Why a pass should run now ("" = not now): asked for (Search now: also while the daily sweep is off), or,
        with atlas.sweep on, the last one is sweep_hours old, or it stopped: for a search or a restart RESUME_S
        ago, for a cause (Dropbox gone, the drive full) HARD_RETRY_S ago."""
        s = atlas.settings(cfg)
        if not (s["enabled"] and s["handoff_dir"]):
            return ""
        now = now or datetime.now().astimezone()
        log_dir = getattr(cfg, "log_dir", None)
        if log_dir and (Path(log_dir) / names.ATLAS_SWEEP_NOW).exists():
            return "asked"
        if not s["sweep"]:
            return ""
        last = last_pass(cfg)
        if not last:
            return "first"
        at = atlas._when(last.get("finished") or last.get("started"))
        if at is None:
            return "first"
        if last.get("stopped"):
            wait = RESUME_S if last.get("soft") else min(HARD_RETRY_S, s["sweep_hours"] * 3600)
            if now - at >= timedelta(seconds=wait):
                return "resume" if last.get("soft") else "retry"
            return ""
        if now - at >= timedelta(hours=s["sweep_hours"]):
            return "daily"
        return ""

    def run_once(self, now: datetime | None = None) -> Pass | None:
        cfg = self.get_cfg()
        why = self.due(cfg, now)
        if not why:
            return None
        if busy(cfg):
            log.debug("atlas sweep: a pass is due (%s); waiting for the search to end", why)
            return None
        log_dir = getattr(cfg, "log_dir", None)
        if why == "asked":
            try:
                (Path(log_dir) / names.ATLAS_SWEEP_NOW).unlink(missing_ok=True)
            except OSError:
                pass
        hb = self.heartbeat

        def say(text: str) -> None:
            if hb is not None:
                hb.beat("atlas-sweep", f"busy: {text.strip()[:120]}", force=True)
            log.info("atlas sweep: %s", text.strip())

        say("searching the drives for FragPipe runs")
        _thread_background(True)
        try:
            p = sweep(cfg, trigger="app" if why == "asked" else "auto", stop=lambda: self._stop.is_set() or busy(cfg),
                      walk_stop=self._stop.is_set, progress=say, reuse_walk=why == "resume")
        finally:
            _thread_background(False)
        log.info("atlas sweep: %s", p.message)
        return p

    def run_forever(self) -> None:
        log.info("atlas sweep thread started")
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("atlas sweep pass failed; trying again later")
            if self.heartbeat is not None:
                self.heartbeat.beat("atlas-sweep", "ok")
            self._stop.wait(self.poll_seconds)
        log.info("atlas sweep thread stopped")


# ------------------------------------------------------------------------------------------ command line --


def run_cli(args, cfg) -> int:
    """`ionomos atlas sweep [--dry-run] [--root DIR ...] [--json]`: exit 0 went through; 1 runs not sent or the pass
    stopped; 2 the setup (off, no folder, no roots): nothing was tried."""
    import sys

    p = sweep(cfg, trigger="cli", dry_run=args.dry_run, roots_=args.root or None,
              progress=None if args.json else lambda t: print(t, flush=True))
    if args.json:
        out = p.summary()
        out["runs"] = [asdict(r) for r in p.runs]
        out["unreadable_folders"] = p.walk.errors[:200] if p.walk else []
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        if p.dry_run:
            go = [r for r in p.runs if r.outcome == SENT]
            by = Counter((r.kind, r.owner) for r in go)
            if go:
                print("would go (probe class, owner: runs):")
                for (kind, owner), n in sorted(by.items()):
                    print(f"  {kind:<7} {owner or '?':<20} {n}")
            left = Counter(r.why for r in p.runs if r.outcome == LEFT)
            if left:
                print("not for the atlas:")
                for why, n in left.most_common(10):
                    print(f"  {n:>5}  {why}")
        for r in p.runs:
            if r.outcome in (FAILED, CONFLICT):
                print(f"{r.outcome}: {r.run}: {r.why}", file=sys.stderr)
                for c in r.conflicts[:5]:
                    print(f"  differs: {c}", file=sys.stderr)
        print(p.message, file=sys.stdout if p.ok or p.dry_run else sys.stderr)
    if p.setup:
        return 2
    return 0 if p.ok else 1
