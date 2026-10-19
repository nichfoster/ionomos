"""
The hand-off to the lab's cysteine atlas (docs/ATLAS.md, D120-D125), contract "ionomos-atlas-handoff/1".

    picks = select(run_dir)                   # what harvest_fragpipe.ps1 takes from a FragPipe output folder
    destination(src, root)                    # where harvest's Get-RelativeDestination puts a file or folder
    res = send(cfg, job)                      # copy + verify + completion record; returns a Result, never raises
    mark_pending(cfg, job)                    # a finished search goes on the list (the worker, after a job)
    state(dest)                               # the `atlas` block of an experiment's ionomos.json ({} = never)
    HandoffLoop(get_cfg).run_forever()        # the watcher's thread: hands off what is due, with a back-off
    check(cfg)                                # destination writable, the atlas's lookup present and valid
    dropbox_suggestion()                      # "...\\Nicholas Foster Data\\fragpipe-runs" when Dropbox has it

The rules, from CLAUDE.md and the contract:
  - the experiment folder is only read: nothing in it is moved, renamed or deleted; the only write is the
    `atlas` block of its ionomos.json
  - nothing at the destination is overwritten: a file there with the same size and SHA-256 is skipped; one with
    other content is a conflict, which stops the hand-off before anything is copied
  - every copy goes to <name>.partial, is read back and checked by SHA-256, then renamed to its name
  - ionomos-handoff.json is written once every file is verified, then the one-row runs CSV under a temporary name,
    renamed last: its presence means the copy is complete

Nothing here needs Tk or the network. The destination is a folder the Dropbox client syncs; when it is offline or
missing the hand-off waits and is tried again (HandoffLoop), up to atlas.max_tries times.
"""
from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import logging
import ntpath
import os
import re
import stat
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ionomos import __version__, names

log = logging.getLogger("ionomos.atlas")

CONTRACT = "ionomos-atlas-handoff/1"
LOOKUP_CONTRACT = "ionomos-atlas-lookup/1"
ATTENTION_KIND = "atlas_handoff_failed"
PROBE_CLASS = "isoDTB"  # config.py lets only FragPipe isoDTB methods into atlas.methods

DEFAULTS = {"enabled": False, "handoff_dir": "", "methods": ["isoDTB"], "max_tries": 10, "retry_minutes": 15}
MAX_RETRY_HOURS = 12  # the back-off doubles from retry_minutes up to this

# ------------------------------------------------------------ the selection rule (harvest_fragpipe.ps1) --
# Copied from the script's constants, in its order; PowerShell's -like and -contains ignore case, and so do we.

MAX_FILE_MB = 500  # harvest's -MaxFileMB default: a file larger than this many MiB is never copied
SKIP_DIR_NAMES = ("Windows", "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin", "$RECYCLE.BIN",
                  "System Volume Information", "Recovery", "PerfLogs", "AppData", "node_modules", ".git", ".venv",
                  "venv", "__pycache__", "site-packages", "MSBooster", "tmp", "temp", "Temp", ".dropbox.cache",
                  "OneDriveTemp", "Config.Msi", "Intel", "NVIDIA", "MSOCache", "Boot", "EFI")
RUN_MARKERS = ("fragpipe.workflow", "fragpipe-files.fp-manifest", "fragpipe.job", "combined_protein.tsv",
               "combined_peptide.tsv", "combined_ion.tsv", "combined_modified_peptide.tsv",
               "combined_modified_peptide_label_quant.tsv")
ROOT_ALLOW = ("*.workflow", "*.fp-manifest", "*.job", "*.tsv", "*.txt", "*.csv", "*.log", "*.fas", "*.fasta",
              "*.params", "*.yml", "*.yaml", "*.json", "*.manifest", "*.xml")
REPLICATE_ALLOW = ("psm.tsv", "peptide.tsv", "protein.tsv", "*.log", "log_*.txt")
NEVER_COPY = ("*.raw", "*.d", "*.wiff", "*.wiff2", "*.mzML", "*.mzXML", "*.mzBIN", "*.mzBIN_calibrated", "*.pepXML",
              "*.pep.xml", "*.prot.xml", "*.pin", "*.mgf", "*.pkl", "*.zip", "*.7z", "*.rar", "*.gz", "*.tar", "*.exe",
              "*.dll", "*.jar", "*.pdf", "*.png", "*.jpg", "*.pptx", "*.docx", "*.xlsx", "*.partial",
              "*_calibrated.mgf", "*.speclib", "*.predicted.bin", "*.ms1", "*.ms2", "*.sqt", "*.dta")

# harvest's runs-<time>.csv columns, in its order; then the ones the contract adds
HARVEST_COLUMNS = ("Run", "Owner", "RunName", "LastModified", "FragPipeVersion", "ProbeClass", "Database", "Enzyme",
                   "VarMods", "HasWorkflow", "HasManifest", "PsmFiles", "RawFiles", "RawMissing", "Experiments",
                   "RawPaths", "FilesSelected", "MBSelected", "FilesTooLarge", "Destination")
IONOMOS_COLUMNS = ("IonomosJob", "IonomosUser", "IonomosExperiment", "IonomosMethod", "IonomosVersion",
                   "HandoffSha256")
CSV_COLUMNS = HARVEST_COLUMNS + IONOMOS_COLUMNS

# the states of ionomos.json's `atlas` block
PENDING, SENDING, SENT, FAILED, CONFLICT, GAVE_UP = "pending", "sending", "sent", "failed", "conflict", "gave_up"
STATES = (PENDING, SENDING, SENT, FAILED, CONFLICT, GAVE_UP)
LOCK_STALE_S = 6 * 3600  # a send lock older than this was left by a process that died


class AtlasError(ValueError):
    """The `atlas:` block of config.yaml is wrong."""


def settings_from(raw) -> dict:
    """The `atlas:` block of config.yaml, checked; {} / None = the defaults (off). handoff_dir may hold spaces (it
    is the lab's Dropbox folder and FragPipe never sees it) but has no default, so no default path has one."""
    out = dict(DEFAULTS, methods=list(DEFAULTS["methods"]))
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise AtlasError("must be a mapping (enabled, handoff_dir, methods, max_tries, retry_minutes)")
    bad = sorted(set(map(str, raw)) - set(DEFAULTS))
    if bad:
        raise AtlasError(f"{bad[0]}: unknown setting (known: {', '.join(DEFAULTS)})")
    v = raw.get("enabled")
    if v is not None:
        if not isinstance(v, bool):
            raise AtlasError("enabled must be true or false (without quotes)")
        out["enabled"] = v
    v = raw.get("handoff_dir")
    if v is not None:
        if not isinstance(v, str):
            raise AtlasError("handoff_dir must be a folder path, e.g. the Dropbox folder "
                             "'...\\Nicholas Foster Data\\fragpipe-runs' (in quotes)")
        v = v.strip()
        if v and not _absolute(v):
            raise AtlasError(f"handoff_dir must be a full folder path (C:\\... or \\\\server\\share\\...), not {v!r}")
        out["handoff_dir"] = v
    v = raw.get("methods")
    if v is not None:
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in v):
            raise AtlasError("methods must be a list of method names, e.g. [isoDTB]")
        out["methods"] = [x.strip() for x in v]
    for k, lo, hi, kind in (("max_tries", 1, 100, int), ("retry_minutes", 1, 24 * 60, float)):
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or (kind is int and v != int(v)):
            raise AtlasError(f"{k} must be a {'whole number' if kind is int else 'number'} from {lo} to {hi}")
        out[k] = kind(v)
    return out


def settings(cfg) -> dict:
    """The checked settings of a loaded Config (or of anything with an `atlas` dict); the defaults when absent."""
    raw = getattr(cfg, "atlas", None)
    try:
        return settings_from(raw or None)
    except AtlasError:
        return settings_from(None)


def _absolute(p: str) -> bool:
    return ntpath.isabs(p) and bool(ntpath.splitdrive(p)[0]) or os.path.isabs(p)


def handoff_dir(cfg) -> Path | None:
    """The configured destination, or None (no default: the lab's path has spaces, CLAUDE.md)."""
    d = settings(cfg)["handoff_dir"]
    return Path(d) if d else None


def lookup_dir(cfg) -> Path | None:
    """Where the atlas publishes its site lookup (<dest>/_atlas), when the hand-off is on and has a destination."""
    s = settings(cfg)
    return Path(s["handoff_dir"]) / names.ATLAS_RETURN_DIR if s["enabled"] and s["handoff_dir"] else None


def applies(cfg, job) -> bool:
    """A job whose method goes to the atlas (it may not be done yet)."""
    s = settings(cfg)
    return bool(s["enabled"] and s["handoff_dir"]) and job.method in s["methods"]


# -------------------------------------------------------------------------------------------- selection --


def _like(name: str, patterns: tuple[str, ...]) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, p.lower()) for p in patterns)


def _skip_dir(name: str) -> bool:
    return name.lower() in {d.lower() for d in SKIP_DIR_NAMES}


def _reparse(entry: os.DirEntry) -> bool:
    """A junction or link (harvest skips every reparse point: it never follows one out of the run folder)."""
    try:
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return True


@dataclass(frozen=True)
class Picked:
    """One file of a run folder, as harvest's inventory lists it."""
    path: Path
    rel: str           # relative to the run folder, with '/'
    level: str         # root | replicate
    size: int
    mtime: float
    take: bool
    reason: str        # selected | not needed | never-copy pattern | larger than 500 MB


def select(run_dir: Path, max_mb: int | None = None) -> list[Picked]:
    """Every file harvest_fragpipe.ps1 would list in a FragPipe output folder, and whether it takes it: the top
    folder's files by ROOT_ALLOW; each subfolder's (one level, not SKIP_DIR_NAMES, no links) by REPLICATE_ALLOW;
    then NEVER_COPY and the size cap. Raises OSError when a folder can't be listed: an incomplete run is never
    sent (harvest logs it and goes on; a hand-off waits and tries again)."""
    run_dir = Path(run_dir)
    max_mb = MAX_FILE_MB if max_mb is None else max_mb
    max_bytes = int(max_mb) * 1024 * 1024
    found: list[tuple[os.DirEntry, str, str, bool]] = []
    with os.scandir(_fs(run_dir)) as it:
        top = sorted(it, key=lambda e: e.name.lower())
    for e in top:
        if e.is_dir():
            if _reparse(e) or _skip_dir(e.name):
                continue
            with os.scandir(e.path) as it:
                sub = sorted(it, key=lambda x: x.name.lower())
            for f in sub:
                if f.is_file():
                    found.append((f, f"{e.name}/{f.name}", "replicate", _like(f.name, REPLICATE_ALLOW)))
        elif e.is_file():
            found.append((e, e.name, "root", _like(e.name, ROOT_ALLOW)))
    out = []
    for entry, rel, level, take in found:
        st = entry.stat()
        reason = ""
        if take and _like(entry.name, NEVER_COPY):
            take, reason = False, "never-copy pattern"
        if take and st.st_size > max_bytes:
            take, reason = False, f"larger than {max_mb} MB"
        if take:
            reason = "selected"
        out.append(Picked(run_dir / Path(*rel.split("/")), rel, level, st.st_size, st.st_mtime, take,
                          reason or "not needed"))
    return out


def is_run_folder(run_dir: Path) -> bool:
    """harvest's test: the folder holds one of RUN_MARKERS (or a psm.tsv)."""
    try:
        have = {p.name for p in Path(run_dir).iterdir() if p.is_file()}
    except OSError:
        return False
    return bool(have & set(RUN_MARKERS)) or "psm.tsv" in {h.lower() for h in have}


# ----------------------------------------------------------------------------------------------- layout --


def _plain(path: str) -> str:
    r"""A Windows path without the \\?\ prefix (\\?\UNC\server\share -> \\server\share)."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def windows_path(p: str | Path) -> str:
    """A path as the PC writes it: absolute, with backslashes on Windows (os.path.abspath; links are not followed,
    like harvest's GetFullPath). Elsewhere (tests, a Mac) the absolute POSIX path."""
    return _plain(os.path.abspath(str(p)))


def layout_parts(source: str) -> list[str]:
    r"""harvest's Get-RelativeDestination as path parts under the destination: C:\Fragpipe_General\EJQ\run ->
    [C, Fragpipe_General, EJQ, run]; \\server\share\x -> [server_share, x]. A POSIX path (a test off Windows) goes
    under '_'."""
    s = _plain(str(source))
    if re.match(r"^[A-Za-z]:[\\/]", s) or s.startswith(("\\\\", "//")):
        drive, rest = ntpath.splitdrive(s)
        label = drive[0].upper() if re.match(r"^[A-Za-z]:$", drive) else \
            re.sub(r"[\\/:]+", "_", drive.lstrip("\\/")).rstrip("_")
        return [label, *[p for p in re.split(r"[\\/]+", rest) if p]]
    return ["_", *[p for p in s.split("/") if p]]


def destination(source: str | Path, root: Path) -> Path:
    """Where a file or folder of the PC lands under the destination root (the layout harvest and
    ingest_fragpipe.local_run_dir agree on)."""
    return Path(root).joinpath(*layout_parts(windows_path(source)))


def _fs(p: Path | str) -> str:
    r"""A path the file functions can open even past Windows' 260 characters (\\?\ form); unchanged elsewhere."""
    s = str(p)
    if os.name != "nt" or len(s) < 240 or s.startswith("\\\\?\\"):
        return s
    s = os.path.abspath(s)
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


# ------------------------------------------------------------------------------------- harvest's facts --


def read_workflow_info(path: Path) -> dict:
    """harvest's Read-WorkflowInfo: FragPipe version, database, enzyme and modifications of a .workflow (the last
    line of each wins; case is ignored), and its probe class from the modification masses."""
    info = {"Version": "", "Database": "", "VarMods": "", "FixMods": "", "ProbeClass": "", "Enzyme": ""}
    try:
        with open(_fs(path), encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\r\n") for _, ln in zip(range(4000), fh, strict=False)]
    except OSError:
        return dict(info, ProbeClass="unknown")
    pats = (("Version", r"^#\s*FragPipe\s*\(([^)]+)\)"), ("Database", r"^database\.db-path=(.*)$"),
            ("VarMods", r"^msfragger\.table\.var-mods=(.*)$"), ("FixMods", r"^msfragger\.table\.fix-mods=(.*)$"),
            ("Enzyme", r"^msfragger\.search_enzyme_name_1=(.*)$"))
    for ln in lines:
        for key, pat in pats:
            m = re.match(pat, ln, re.I)
            if m:
                info[key] = m.group(1) if key == "Version" else m.group(1).strip()
                break
    info["Database"] = re.sub(r"\\([:\\])", r"\1", info["Database"])
    mods = info["VarMods"] + " " + info["FixMods"]
    classes = [c for c, pat in (("isoDTB", r"561\.33|567\.34"), ("isoTOP", r"464\.28|470\.29"),
                                ("TMT", r"229\.16|304\.20|304\.21")) if re.search(pat, mods)]
    info["ProbeClass"] = "+".join(classes or ["other"])
    return info


def read_manifest_info(path: Path) -> dict:
    """harvest's Read-ManifestInfo: raw files (and how many are missing) and experiment labels of an fp-manifest;
    plus the rows themselves (the replicate plan of the completion record)."""
    out = {"RawCount": 0, "RawPaths": "", "Experiments": "", "RawMissing": 0, "rows": []}
    try:
        text = Path(_fs(path)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    raws, exps, rows = [], [], []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        cols = ln.split("\t")
        raw = cols[0].strip()
        if raw:
            raws.append(raw)
        if len(cols) >= 2 and cols[1].strip():
            exps.append(cols[1].strip())
        cols += [""] * (4 - len(cols))
        rows.append({"raw": raw, "experiment": cols[1].strip(), "bioreplicate": cols[2].strip(),
                     "data_type": cols[3].strip()})
    out.update(RawCount=len(raws), RawMissing=sum(1 for r in raws if not os.path.exists(r)),
               RawPaths=" | ".join(raws), Experiments=" | ".join(dict.fromkeys(exps)), rows=rows)
    return out


def _ps_number(x: float) -> str:
    """[math]::Round(x, 1) as PowerShell's Export-Csv writes it: 12.3, 12 (not 12.0), 0."""
    r = round(x, 1)
    return str(int(r)) if r == int(r) else f"{r}"


def _ps_time(t: float | None) -> str:
    """DateTime.ToString('s') of a local modification time; harvest's MinValue when there is none."""
    return datetime.fromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S") if t else "0001-01-01T00:00:00"


# ------------------------------------------------------------------------------------------------- state --


def state(dest: Path | str) -> dict:
    """The `atlas` block of an experiment's ionomos.json; {} when it has none (never handed off, nor asked to)."""
    try:
        rec = json.loads(names.status_path(Path(dest)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    a = rec.get("atlas") if isinstance(rec, dict) else None
    return dict(a) if isinstance(a, dict) else {}


_STATUS_LOCK = threading.Lock()


def _set_state(dest: Path | str, block: dict) -> None:
    """Replace the `atlas` block of ionomos.json, keeping everything else (read just before the write). Never
    raises: the hand-off must not break a job's status file, and a failure here is only logged."""
    from ionomos.intake import write_status

    dest = Path(dest)
    with _STATUS_LOCK:
        try:
            rec = json.loads(names.status_path(dest).read_text(encoding="utf-8"))
            if not isinstance(rec, dict):
                raise ValueError("not a mapping")
        except (OSError, ValueError) as exc:
            log.warning("atlas: could not read %s to record the hand-off (%s); left as it is", dest, exc)
            return
        rec["atlas"] = block
        try:
            write_status(dest, rec)
        except OSError as exc:
            log.warning("atlas: could not write the hand-off state into %s: %s", names.status_path(dest), exc)


def _iso(t: datetime | None = None) -> str:
    return (t or datetime.now()).astimezone().isoformat(timespec="seconds")


def _when(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def backoff(tries: int, retry_minutes: float) -> timedelta:
    """How long to wait after the nth failed try: retry_minutes, doubling, at most MAX_RETRY_HOURS."""
    return timedelta(minutes=min(retry_minutes * 2 ** max(tries - 1, 0), MAX_RETRY_HOURS * 60))


def is_due(block: dict, now: datetime | None = None) -> bool:
    """A hand-off the watcher should try now: pending, or failed and its next try has come. sent, conflict and
    gave_up wait for a person (Send to atlas); sending is another process's (its lock says whether it is alive)."""
    st = block.get("state")
    if st == PENDING:
        return True
    if st in (FAILED, SENDING):
        nxt = _when(block.get("next_try"))
        return nxt is None or (now or datetime.now().astimezone()) >= nxt
    return False


def _job_finished(job) -> str:
    """The search's finish time from ionomos.json (run.finished_at), the ledger's otherwise."""
    try:
        rec = json.loads(names.status_path(Path(job.dest_dir)).read_text(encoding="utf-8"))
        run = rec.get("run") if isinstance(rec, dict) else None
        if isinstance(run, dict) and run.get("finished_at"):
            return str(run["finished_at"])
    except (OSError, ValueError):
        pass
    return str(job.finished_at or "")


def mark_pending(cfg, job) -> bool:
    """Put a job that just finished on the hand-off list (Worker.run_once, after the job and its analysis are done).
    Only a job that is done, whose method goes to the atlas, while the hand-off is on. A hand-off of an earlier
    search of the same job is kept in `history`. True when marked. Never raises."""
    try:
        if job is None or job.status != "done" or not applies(cfg, job):
            return False
        old = state(job.dest_dir)
        finished = _job_finished(job)
        if old.get("search_finished_at") == finished and old.get("state") in (PENDING, SENDING, SENT):
            return False  # this search is already on its way, or there
        block = {"state": PENDING, "tries": 0, "queued_at": _iso(), "search_finished_at": finished,
                 "history": _history(old)}
        _set_state(job.dest_dir, block)
        log.info("job %s: queued for the cysteine atlas hand-off", job.id)
        return True
    except Exception:  # noqa: BLE001 - never in the job's way
        log.exception("job %s: could not queue the atlas hand-off", getattr(job, "id", "?"))
        return False


def _history(old: dict) -> list[dict]:
    """Earlier sends of this experiment (the newest 10), each {sent_at, record, handoff_sha256}."""
    hist = [h for h in old.get("history", []) if isinstance(h, dict)] if isinstance(old.get("history"), list) else []
    if old.get("state") == SENT and old.get("record"):
        hist.append({k: old.get(k) for k in ("sent_at", "record", "handoff_sha256")})
    return hist[-10:]


# -------------------------------------------------------------------------------------------------- send --


@dataclass
class Result:
    """What one hand-off did. ok: every file is at the destination, verified, and the record is written."""
    job_id: int | None
    state: str
    message: str
    ok: bool = False
    setup: bool = False                      # the cause is the setup (off, no folder): nothing was tried
    run_dir: str = ""
    dest_dir: str = ""
    record: str = ""                         # the runs CSV
    handoff_sha256: str = ""
    copied: int = 0
    already: int = 0
    files: int = 0
    bytes: int = 0
    conflicts: list[str] = field(default_factory=list)
    too_large: list[str] = field(default_factory=list)


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(_fs(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rename_new(src: Path, dst: Path) -> None:
    """Rename that never replaces: Windows' rename refuses an existing target; elsewhere a hard link does."""
    if os.name == "nt":
        os.rename(_fs(src), _fs(dst))
        return
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError:  # a file system without hard links
        if os.path.lexists(dst):
            raise FileExistsError(dst) from None
        os.rename(src, dst)
        return
    os.unlink(src)


def _copy_verified(src: Path, dst: Path) -> str:
    """src -> dst.partial (hashed while copying) -> read back and compared -> renamed to dst. The source's
    modification time is kept. Returns the SHA-256; OSError when the copy does not match or the source changed."""
    part = dst.with_name(dst.name + names.ATLAS_PARTIAL_SUFFIX)
    before = os.stat(_fs(src))
    h = hashlib.sha256()
    with open(_fs(src), "rb") as fi, open(_fs(part), "wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    after = os.stat(_fs(src))
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"{src} changed while it was being copied; it is copied again on the next try")
    sha = h.hexdigest()
    if _sha256(part) != sha:
        raise OSError(f"the copy of {src} does not match it (SHA-256); it is left as {part.name} and copied again "
                      "on the next try")
    os.utime(_fs(part), ns=(after.st_atime_ns, after.st_mtime_ns))
    _rename_new(part, dst)
    return sha


def _write_new_or_same(path: Path, data: bytes) -> bool:
    """Our own small file (the completion record): written through .partial; an identical one is kept; a different
    one is replaced (it is the record of an earlier send of this run, which the new record supersedes). True when
    written."""
    try:
        if Path(_fs(path)).read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    part = path.with_name(path.name + names.ATLAS_PARTIAL_SUFFIX)
    with open(_fs(part), "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if hashlib.sha256(Path(_fs(part)).read_bytes()).digest() != hashlib.sha256(data).digest():
        raise OSError(f"{part} does not read back as written")
    os.replace(_fs(part), _fs(path))
    return True


def _status_record(dest: Path) -> dict:
    try:
        rec = json.loads(names.status_path(dest).read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def run_folder(job) -> Path:
    """The job's FragPipe output folder: ionomos.json run.workdir, else <experiment>/fragpipe."""
    dest = Path(job.dest_dir)
    run = _status_record(dest).get("run")
    wd = run.get("workdir") if isinstance(run, dict) else None
    return Path(wd) if wd else dest / "fragpipe"


def summary(job, run_dir: Path, manifest: dict, wf: dict) -> dict:
    """The job part of ionomos-handoff.json: who, what, how it was searched. No send time, so a re-send of the same
    files and job gives the same record (and the same HandoffSha256)."""
    rec = _status_record(Path(job.dest_dir))
    run = rec.get("run") if isinstance(rec.get("run"), dict) else {}
    fp_version = wf.get("Version") or ""
    if not fp_version and run.get("fingerprint"):
        try:
            fpj = json.loads(Path(run["fingerprint"]).read_text(encoding="utf-8"))
            fp_version = str(((fpj.get("engine") or {}).get("version")) or "")
        except (OSError, ValueError, AttributeError):
            pass
    return {
        "number": job.id,
        "user": job.user,
        "experiment": Path(job.dest_dir).name,
        "inbox_name": job.inbox_name,
        "method": job.method,
        "fragpipe_version": fp_version,
        "workflow": str(run.get("workflow_source") or run.get("workflow") or ""),
        "fasta": str(run.get("fasta") or wf.get("Database") or ""),
        "raw_files": [r["raw"] for r in manifest["rows"] if r["raw"]],
        "replicate_plan": manifest["rows"],
        "search_finished_at": str(run.get("finished_at") or job.finished_at or ""),
        "probe_class_by_masses": wf.get("ProbeClass") or "",
        "ionomos_version": __version__,
    }


def _stamp_name(log_dir: Path, job_id) -> Path:
    """runs-ionomos-<YYYYMMDD-HHMMSS>-job<N>.csv that isn't there yet (a second send in the same second takes the
    next second's name)."""
    t = datetime.now()
    for i in range(3600):
        p = log_dir / names.ATLAS_RUNS_CSV.format(stamp=(t + timedelta(seconds=i)).strftime("%Y%m%d-%H%M%S"),
                                                  job=job_id)
        if not os.path.lexists(_fs(p)) and not os.path.lexists(_fs(str(p) + names.ATLAS_PARTIAL_SUFFIX)):
            return p
    raise OSError(f"no free record name in {log_dir}")


def runs_csv_bytes(row: dict) -> bytes:
    """One row, as PowerShell 5.1's Export-Csv -NoTypeInformation -Encoding UTF8 writes it: UTF-8 with BOM, every
    field quoted, CRLF."""
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    w.writerow([str(row.get(c, "")) for c in CSV_COLUMNS])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


class _Lock:
    """One hand-off of a job at a time across processes (the watcher's thread, the app, `ionomos atlas send`):
    <log_dir>/atlas-send-job<N>.lock, made exclusively; one older than LOCK_STALE_S is taken over."""

    def __init__(self, log_dir: Path | None, job_id):
        self.path = Path(log_dir) / names.ATLAS_SEND_LOCK.format(job=job_id) if log_dir else None
        self.held = False

    def __enter__(self):
        if self.path is None:
            self.held = True
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                return self
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()} {_iso()}\n")
            self.held = True
            return self
        return self

    def __exit__(self, *exc):
        if self.held and self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def send(cfg, job, trigger: str = "auto", force: bool = False) -> Result:
    """Hand one done job's FragPipe run to the atlas destination, and record what happened in its ionomos.json
    `atlas` block (plus an attention item when it failed, closed when it succeeds). Never raises.
    trigger: auto (the watcher's loop) | app | cli, for the log. force: try even when the job's method is not in
    atlas.methods (never when the hand-off is off or has no destination)."""
    jid = getattr(job, "id", None)
    s = settings(cfg)
    if not s["enabled"]:
        return Result(jid, "", "the atlas hand-off is off (config.yaml atlas.enabled: false)", setup=True)
    if not s["handoff_dir"]:
        return Result(jid, "", "atlas.handoff_dir is not set: the lab Dropbox folder "
                               "'...\\Nicholas Foster Data\\fragpipe-runs' (app: Folders tab)", setup=True)
    if job is None:
        return Result(None, "", "no such job")
    if job.status != "done":
        return Result(jid, "", f"job {jid} is {job.status}; only a finished search is handed to the atlas")
    if job.method not in s["methods"] and not force:
        return Result(jid, "", f"job {jid} is {job.method}; atlas.methods sends {', '.join(s['methods'])}")
    log_dir = getattr(cfg, "log_dir", None)
    with _Lock(log_dir, jid) as lock:
        if not lock.held:
            return Result(jid, SENDING, f"job {jid} is being handed to the atlas by another Ionomos process right now")
        old = state(job.dest_dir)
        tries = int(old.get("tries") or 0) + 1 if old.get("state") in (FAILED, SENDING) else 1
        base = {"search_finished_at": old.get("search_finished_at") or _job_finished(job), "history": _history(old),
                "trigger": trigger}
        _set_state(job.dest_dir, {**base, "state": SENDING, "tries": tries, "last_try": _iso()})
        try:
            res = _send(cfg, job, Path(s["handoff_dir"]))
        except Exception as exc:  # noqa: BLE001 - a full disk, Dropbox offline, a bug: the job is not touched
            log.exception("job %s: atlas hand-off failed", jid)
            res = Result(jid, FAILED, f"{type(exc).__name__}: {exc}" if not isinstance(exc, OSError) else str(exc))
        _record(cfg, job, res, base, tries, s)
    return res


def _send(cfg, job, root: Path) -> Result:
    jid = job.id
    run_dir = run_folder(job)
    res = Result(jid, FAILED, "", run_dir=windows_path(run_dir))
    if not run_dir.is_dir():
        res.message = f"the FragPipe output folder {run_dir} is not there"
        return res
    if not is_run_folder(run_dir):
        res.message = f"{run_dir} holds no FragPipe result (no fragpipe.workflow, manifest or psm.tsv)"
        return res
    root_s = windows_path(root)
    if not os.path.isdir(_fs(root)):
        parent = os.path.dirname(root_s)
        if not os.path.isdir(_fs(parent)):
            res.message = (f"the atlas folder {root_s} can't be reached (its folder {parent} isn't there either): "
                           "is Dropbox running and signed in on this PC?")
            return res
        os.mkdir(_fs(root))  # harvest makes the destination when its parent is there; never more than that
    src_n, dst_n = os.path.normcase(res.run_dir), os.path.normcase(root_s)
    if dst_n.startswith(src_n.rstrip("\\/") + os.sep) or src_n.startswith(dst_n.rstrip("\\/") + os.sep) \
            or src_n == dst_n:
        res.message = f"the atlas folder {root_s} and the run folder {res.run_dir} are inside one another"
        return res
    picks = select(run_dir)
    take = [p for p in picks if p.take]
    res.too_large = [p.rel for p in picks if p.reason.startswith("larger")]
    res.files, res.bytes = len(take), sum(p.size for p in take)
    run_dest = destination(res.run_dir, root)
    res.dest_dir = str(run_dest)

    # 1. compare with what is already there: nothing is copied if anything conflicts
    plan: list[tuple[Picked, Path, str]] = []  # (file, its destination, sha if already there)
    for p in take:
        dst = run_dest.joinpath(*p.rel.split("/"))
        if os.path.isdir(_fs(dst)):
            res.conflicts.append(f"{dst} is a folder")
            continue
        if os.path.lexists(_fs(dst)):
            there = os.stat(_fs(dst)).st_size
            if there != p.size:
                res.conflicts.append(f"{dst} ({there:,} bytes; this run's {p.rel} has {p.size:,})")
                continue
            sha = _sha256(p.path)
            if _sha256(dst) != sha:
                res.conflicts.append(f"{dst} (same size, other content than this run's {p.rel})")
                continue
            plan.append((p, dst, sha))
        else:
            plan.append((p, dst, ""))
    if res.conflicts:
        res.state = CONFLICT
        res.message = (f"{len(res.conflicts)} file(s) at the atlas folder differ from this run's; nothing was copied "
                       f"or overwritten. First: {res.conflicts[0]}")
        return res

    # 2. copy what isn't there, each verified
    files = []
    for p, dst, sha in plan:
        if not sha:
            os.makedirs(_fs(dst.parent), exist_ok=True)
            sha = _copy_verified(p.path, dst)
            res.copied += 1
        else:
            res.already += 1
        files.append({"path": p.rel, "size": p.size, "sha256": sha})

    # 3. the completion record, then the runs CSV (written last, renamed into place: it means "complete")
    wf_path, mf_path = run_dir / "fragpipe.workflow", run_dir / "fragpipe-files.fp-manifest"
    wf = read_workflow_info(wf_path) if wf_path.is_file() else {"Version": "", "Database": "", "VarMods": "",
                                                                  "ProbeClass": "unknown", "Enzyme": ""}
    mf = read_manifest_info(mf_path) if mf_path.is_file() else read_manifest_info(Path(os.devnull))
    record = {"contract": CONTRACT, "run": res.run_dir,
              "files": sorted(files, key=lambda f: f["path"].lower()), "job": summary(job, run_dir, mf, wf)}
    data = (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _write_new_or_same(run_dest / names.ATLAS_HANDOFF_FILE, data)
    res.handoff_sha256 = hashlib.sha256(data).hexdigest()
    row = {
        "Run": res.run_dir, "Owner": job.user, "RunName": Path(res.run_dir).name,
        "LastModified": _ps_time(max((p.mtime for p in picks), default=None)),
        "FragPipeVersion": wf.get("Version") or record["job"]["fragpipe_version"], "ProbeClass": PROBE_CLASS,
        "Database": wf.get("Database", ""), "Enzyme": wf.get("Enzyme", ""), "VarMods": wf.get("VarMods", ""),
        "HasWorkflow": str(wf_path.is_file()), "HasManifest": str(mf_path.is_file()),
        "PsmFiles": sum(1 for p in take if Path(p.rel).name.lower() == "psm.tsv"),
        "RawFiles": mf["RawCount"], "RawMissing": mf["RawMissing"], "Experiments": mf["Experiments"],
        "RawPaths": mf["RawPaths"], "FilesSelected": len(take), "MBSelected": _ps_number(res.bytes / 1024 / 1024),
        "FilesTooLarge": len(res.too_large), "Destination": str(run_dest),
        "IonomosJob": job.id, "IonomosUser": job.user, "IonomosExperiment": record["job"]["experiment"],
        "IonomosMethod": job.method, "IonomosVersion": __version__, "HandoffSha256": res.handoff_sha256,
    }
    logs = root / names.ATLAS_LOG_DIR
    os.makedirs(_fs(logs), exist_ok=True)
    final = _stamp_name(logs, job.id)
    part = final.with_name(final.name + names.ATLAS_PARTIAL_SUFFIX)
    blob = runs_csv_bytes(row)
    with open(_fs(part), "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    _rename_new(part, final)
    res.record, res.state, res.ok = str(final), SENT, True
    res.message = (f"handed to the atlas: {res.copied} file(s) copied, {res.already} already there "
                   f"({res.bytes / 1024 / 1024:,.1f} MB); record {final.name}")
    return res


def _record(cfg, job, res: Result, base: dict, tries: int, s: dict) -> None:
    """The outcome into ionomos.json, the log and the attention list."""
    now = datetime.now().astimezone()
    block = {**base, "tries": tries, "last_try": _iso(now), "state": res.state, "message": res.message,
             "files": res.files, "copied": res.copied, "already": res.already, "dest": res.dest_dir,
             "run": res.run_dir}
    if res.too_large:
        block["too_large"] = res.too_large
    if res.ok:
        block.update(sent_at=_iso(now), record=res.record, handoff_sha256=res.handoff_sha256, tries=0)
        log.info("job %s: %s", job.id, res.message)
        _close(cfg, job)
    elif res.state == CONFLICT:
        block["conflicts"] = res.conflicts[:20]
        log.error("job %s: atlas hand-off stopped: %s", job.id, res.message)
        _tell(cfg, job, res, block, conflict=True)
    else:
        if tries >= s["max_tries"]:
            res.state = block["state"] = GAVE_UP
            log.error("job %s: atlas hand-off failed %d times; no more tries until someone presses Send to atlas: "
                      "%s", job.id, tries, res.message)
        else:
            block["next_try"] = _iso(now + backoff(tries, s["retry_minutes"]))
            log.warning("job %s: atlas hand-off failed (try %d of %d; next %s): %s", job.id, tries, s["max_tries"],
                        block["next_try"], res.message)
        _tell(cfg, job, res, block)
    _set_state(job.dest_dir, block)


def _tell(cfg, job, res: Result, block: dict, conflict: bool = False) -> None:
    try:
        from ionomos import attention

        if conflict:
            causes = ["The atlas folder already holds a different copy of this run (an earlier search of the same "
                      "folder, or a copy someone edited); Ionomos never overwrites it"]
            fixes = ["Ask the person who keeps the atlas which copy is right; they move the old copy aside in "
                     "Dropbox, then press Send to atlas (Jobs tab) or run  ionomos atlas send " + str(job.id)]
        elif block.get("state") == GAVE_UP:
            causes = [f"It failed {block['tries']} times in a row: " + res.message]
            fixes = ["Fix the cause (is Dropbox running and signed in? is the folder on the Folders tab right?), then "
                     "press Send to atlas (Jobs tab) or run  ionomos atlas send " + str(job.id)]
        else:
            causes = [res.message]
            fixes = [f"Nothing to do if Dropbox was only offline: it is tried again by itself at "
                     f"{(_when(block.get('next_try')) or datetime.now()).strftime('%H:%M')} "
                     f"(try {block['tries']} of {settings(cfg)['max_tries']})",
                     "Or fix the cause and press Send to atlas (Jobs tab); `ionomos atlas check` checks the folder"]
        attention.raise_item(getattr(cfg, "log_dir", None), ATTENTION_KIND,
                             f"{job.user}/{Path(job.dest_dir).name} wasn't handed to the cysteine atlas",
                             res.message, key=f"{ATTENTION_KIND}:job{job.id}", severity="warning",
                             dest=job.dest_dir, job_id=job.id, causes=causes, fixes=fixes,
                             details="\n".join(res.conflicts[:20]),
                             data={"state": block.get("state"), "tries": block.get("tries"), "dest": res.dest_dir})
    except Exception:  # noqa: BLE001 - telling a person must not break the hand-off
        log.exception("could not record %s for job %s", ATTENTION_KIND, job.id)


def _close(cfg, job) -> None:
    try:
        from ionomos import attention

        attention.resolve_where(getattr(cfg, "log_dir", None), kind=ATTENTION_KIND, job_id=job.id)
    except Exception:  # noqa: BLE001
        log.exception("could not close %s for job %s", ATTENTION_KIND, job.id)


# --------------------------------------------------------------------------------------------- the loop --


class HandoffLoop:
    """The watcher's hand-off thread (cli.cmd_run): every `poll_seconds`, each done job of an atlas method whose
    hand-off is pending, or failed and due again, is sent. Its own thread, so a slow Dropbox folder never holds up
    a search; its own ledger connection. Reads the config through `get_cfg` (LiveConfig.get), so turning the
    hand-off on or off in the app counts without a restart."""

    def __init__(self, get_cfg, poll_seconds: float = 30.0, heartbeat=None):
        self.get_cfg = get_cfg
        self.poll_seconds = poll_seconds
        self.heartbeat = heartbeat
        self._stop = threading.Event()
        self._seen: dict[int, tuple] = {}  # job id -> (ionomos.json mtime, due-able) for jobs with nothing to do

    def stop(self) -> None:
        self._stop.set()

    def run_once(self, now: datetime | None = None) -> list[Result]:
        cfg = self.get_cfg()
        s = settings(cfg)
        if not (s["enabled"] and s["handoff_dir"]):
            return []
        from ionomos.ledger import Ledger

        out: list[Result] = []
        led = Ledger(cfg.database)
        try:
            jobs = [j for j in led.list("done") if j.method in s["methods"]]
        finally:
            led.close()
        for job in jobs:
            if self._stop.is_set():
                break
            try:
                mtime = names.status_path(Path(job.dest_dir)).stat().st_mtime_ns
            except OSError:
                continue
            seen = self._seen.get(job.id)
            if seen is not None and seen[0] == mtime and seen[1] is None:
                continue  # nothing to do last time, and nothing changed since
            block = state(job.dest_dir)
            if not is_due(block, now):
                nxt = block.get("next_try") if block.get("state") in (FAILED, SENDING) else None
                self._seen[job.id] = (mtime, nxt)
                continue
            self._seen.pop(job.id, None)
            if self.heartbeat is not None:
                self.heartbeat.beat("atlas", f"busy: handing job {job.id} to the atlas", force=True)
            out.append(send(cfg, job, trigger="auto"))
        return out

    def run_forever(self) -> None:
        log.info("atlas hand-off thread started")
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("atlas hand-off pass failed; trying again later")
            if self.heartbeat is not None:
                self.heartbeat.beat("atlas", "ok")
            self._stop.wait(self.poll_seconds)
        log.info("atlas hand-off thread stopped")


# ----------------------------------------------------------------------------------------------- checks --


@dataclass(frozen=True)
class Finding:
    ok: bool
    what: str
    fix: str = ""


def check(cfg, write_probe: bool = True) -> list[Finding]:
    """What `ionomos atlas check` and the Setup checklist say: on, the destination set, there and writable, and the
    atlas's site lookup there and readable. write_probe: make and remove a small file in the destination."""
    s = settings(cfg)
    out: list[Finding] = []
    if not s["enabled"]:
        return [Finding(True, "the atlas hand-off is off (atlas.enabled: false); nothing is sent",
                        "turn it on in the app (Folders tab) or config.yaml atlas.enabled: true")]
    out.append(Finding(True, f"the atlas hand-off is on for {', '.join(s['methods'])}"))
    root = s["handoff_dir"]
    if not root:
        sug = dropbox_suggestion()
        return out + [Finding(False, "atlas.handoff_dir is not set",
                              f"pick the lab Dropbox folder on the Folders tab{f' (found: {sug})' if sug else ''}")]
    p = Path(root)
    if not os.path.isdir(_fs(p)):
        parent_there = os.path.isdir(_fs(p.parent))
        out.append(Finding(parent_there, f"{root} isn't there yet" + ("; the first hand-off makes it" if parent_there
                                                                       else f", nor {p.parent}"),
                           "" if parent_there else "is Dropbox running and signed in on this PC, with the folder "
                                                   "synced (not online-only)? Or fix atlas.handoff_dir"))
    elif write_probe:
        probe = p / f".ionomos-write-test-{os.getpid()}-{int(time.time())}"
        try:
            with open(_fs(probe), "x", encoding="utf-8") as fh:
                fh.write("ok\n")
            os.unlink(_fs(probe))
            out.append(Finding(True, f"{root} is there and this account can write to it"))
        except OSError as exc:
            out.append(Finding(False, f"can't write to {root}: {exc.strerror or exc}",
                               "make the folder available offline in Dropbox, or give this Windows account write "
                               "access"))
    else:
        out.append(Finding(True, f"{root} is there"))
    try:
        from ionomos.downstream import atlasctx

        lk, note = atlasctx.read_lookup(p / names.ATLAS_RETURN_DIR)
        if lk is None:
            out.append(Finding(not note.startswith("malformed"), "atlas site lookup: " + note,
                               "" if not note.startswith("malformed") else "tell the person who keeps the atlas: "
                                                                            "the report ignores this lookup"))
        else:
            out.append(Finding(True, f"atlas site lookup: {lk.describe()}"))
    except ImportError:
        pass
    return out


# -------------------------------------------------------------------------------------- Dropbox folder --

DATA_FOLDER = "Nicholas Foster Data"
RUNS_FOLDER = "fragpipe-runs"


def dropbox_roots(env: dict | None = None) -> list[str]:
    """The Dropbox folders the client on this PC knows (harvest's Get-DropboxRoots): every account's root_path /
    path in %LOCALAPPDATA% or %APPDATA%\\Dropbox\\info.json."""
    env = os.environ if env is None else env
    found: list[str] = []
    for base in (env.get("LOCALAPPDATA"), env.get("APPDATA")):
        if not base:
            continue
        info = Path(base) / "Dropbox" / "info.json"
        try:
            data = json.loads(info.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for acct in (data.values() if isinstance(data, dict) else []):
            if isinstance(acct, dict):
                for key in ("root_path", "path"):
                    if acct.get(key):
                        found.append(str(acct[key]))
    return list(dict.fromkeys(found))


def dropbox_suggestion(env: dict | None = None, roots: list[str] | None = None) -> str:
    r"""<the folder "Nicholas Foster Data" up to two levels under a Dropbox root>\fragpipe-runs, as harvest finds
    it; "" when there is none. A suggestion the person confirms, never a default (the path has spaces). Lists a
    few folders: call it off the Tk thread."""
    for root in (dropbox_roots(env) if roots is None else roots):
        queue = [(Path(root), 0)]
        while queue:
            cur, depth = queue.pop(0)
            cand = cur / DATA_FOLDER
            if cand.is_dir():
                return str(cand / RUNS_FOLDER)
            if depth >= 2:
                continue
            try:
                with os.scandir(cur) as it:
                    kids = sorted((e for e in it if e.is_dir(follow_symlinks=False) and not _reparse(e)
                                   and e.name != ".dropbox.cache"), key=lambda e: e.name.lower())
            except OSError:
                continue
            queue += [(Path(e.path), depth + 1) for e in kids]
    return ""


# ------------------------------------------------------------------------------------------ command line --


def add_parser(sub):
    """`ionomos atlas send | status | check` (D123); exit codes as cli.py's table."""
    import argparse

    p = sub.add_parser("atlas", help="hand finished isoDTB searches to the lab's cysteine atlas (docs/ATLAS.md)",
                       description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="atlas_cmd", required=True)
    se = s.add_parser("send", help="hand one finished job to the atlas now, whatever its hand-off state (exit 0 "
                                   "sent; 1 not sent, with why; 2 the hand-off is off or has no folder)")
    se.add_argument("job_id", type=int, help="the job's number (ionomos status --all lists them)")
    st = s.add_parser("status", help="the hand-off state of one job, or of every finished job of atlas.methods")
    st.add_argument("job_id", type=int, nargs="?", help="one job's number (default: every finished atlas job)")
    st.add_argument("--json", action="store_true", help="print the `atlas` block(s) of ionomos.json as JSON")
    ck = s.add_parser("check", help="the destination folder set, there and writable; the atlas's site lookup there "
                                    "and readable (exit 0 all good or off, 1 a problem)")
    ck.add_argument("--no-write", action="store_true",
                    help="don't make and remove a small test file in the destination folder")
    p.set_defaults(fn=run_cli)
    return p


def describe(block: dict) -> str:
    """One line for a job's `atlas` block, as the Jobs tab and `ionomos atlas status` say it."""
    st = block.get("state")
    if not st:
        return "not handed off"
    if st == SENT:
        return f"sent {str(block.get('sent_at') or '')[:16].replace('T', ' ')}"
    if st == PENDING:
        return "waiting to be handed off"
    if st == SENDING:
        return "being handed off"
    if st == FAILED:
        nxt = _when(block.get("next_try"))
        return (f"failed (try {block.get('tries', 0)}); next try {nxt:%Y-%m-%d %H:%M}" if nxt else "failed") + \
            (f": {block['message']}" if block.get("message") else "")
    if st == CONFLICT:
        return "stopped: the atlas folder holds a different copy (see the attention list)"
    if st == GAVE_UP:
        return f"gave up after {block.get('tries', 0)} tries: {block.get('message', '')}"
    return str(st)


def run_cli(args) -> int:
    import sys

    from ionomos import cli
    from ionomos.ledger import Ledger

    cfg = cli._load(args, check_paths=False)
    s = settings(cfg)
    if args.atlas_cmd == "check":
        found = check(cfg, write_probe=not args.no_write)
        for f in found:
            print(f"{'✓' if f.ok else '✗'} {f.what}" + (f"\n    ↳ {f.fix}" if f.fix and not f.ok else ""))
        return 0 if all(f.ok for f in found) else 1
    if not cfg.database.is_file():
        print("no ledger yet (nothing has been dropped)", file=sys.stderr if args.atlas_cmd == "send" else sys.stdout)
        return 1 if args.atlas_cmd == "send" or args.job_id is not None else 0
    led = Ledger(cfg.database)
    try:
        if args.job_id is not None:
            job = led.get(args.job_id)
            if job is None:
                print(f"no job {args.job_id}", file=sys.stderr)
                return 1
            jobs = [job]
        else:
            jobs = [j for j in led.list("done") if j.method in s["methods"]]
    finally:
        led.close()
    if args.atlas_cmd == "send":
        res = send(cfg, jobs[0], trigger="cli")
        print(res.message, file=sys.stdout if res.ok else sys.stderr)
        for c in res.conflicts[:10]:
            print(f"  differs: {c}", file=sys.stderr)
        if res.ok and res.record:
            print(f"record: {res.record}")
        return 0 if res.ok else (2 if res.setup else 1)
    blocks = {j.id: state(j.dest_dir) for j in jobs}
    if args.json:
        print(json.dumps({str(k): v for k, v in blocks.items()}, indent=2))
        return 0
    if not s["enabled"]:
        print("the atlas hand-off is off (config.yaml atlas.enabled: false)")
    if not jobs:
        print(f"no finished {', '.join(s['methods'])} jobs")
        return 0
    for j in jobs:
        print(f"{j.id:>4}  {j.method:<7} {j.user:<10} {Path(j.dest_dir).name:<40} {describe(blocks[j.id])}")
    return 0
