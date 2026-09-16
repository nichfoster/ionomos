"""
Intake — turn a stable inbox folder into a queued job, resolve problems, or reject.

    plan(folder, cfg, ledger)              -> Plan          read-only; raises IntakeError(kind=...)
    draft(folder, cfg, error)              -> Draft         best-effort parse for the GUI, never raises
    intake(folder, cfg, ledger, resolver)  -> IntakeResult  QUEUED | REJECTED | RETRY

Flow:
    plan ──ok──▶ move ──▶ labwatch.json ──▶ ledger row            (QUEUED)
      │
      └─IntakeError─▶ kind resolvable & resolver? ──▶ resolver.resolve(Draft)
                          │ overrides                    │ None
                          ▼                              ▼
                     save experiment.yaml, plan again   write .REJECTED.txt   (REJECTED)
    move raises PermissionError (Windows: Explorer still holds a handle)      (RETRY)

Rules: never delete, never overwrite. A rejected folder stays in the inbox
with a note; fixing the folder (or deleting the note) makes the watcher try
again.
"""
from __future__ import annotations

import enum
import errno
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from labwatch import __version__
from labwatch.config import Config
from labwatch.ledger import Job, Ledger, now_iso
from labwatch.manifest import (
    Overrides,
    OverridesError,
    apply_file_overrides,
    load_overrides,
    save_overrides,
)
from labwatch.naming import (
    RAW_SUFFIX,
    FolderName,
    NamingError,
    RawName,
    RawSet,
    build_user_lookup,
    find_date,
    find_method,
    find_user,
    group_raws,
    parse_raw_name,
    sanitize,
    tokens_of,
)

log = logging.getLogger("labwatch.intake")

NAMING_DOC = "docs/NAMING_CONVENTION.md"
REJECT_SUFFIX = ".REJECTED.txt"


class Kind(enum.StrEnum):
    """What went wrong. The GUI can fix the first four; the rest need a rename."""

    USER = "user"
    METHOD = "method"
    RAWS = "raws"  # a file name's tail can't be parsed
    LAYOUT = "layout"  # uneven fractions / mixed single+fractionated / duplicates
    NO_RAWS = "no_raws"
    DEST = "dest"  # destination exists / already in ledger
    OVERRIDES = "overrides"  # experiment.yaml malformed
    OTHER = "other"


RESOLVABLE = {Kind.USER, Kind.METHOD, Kind.RAWS, Kind.LAYOUT}


class IntakeError(ValueError):
    def __init__(self, message: str, kind: Kind = Kind.OTHER):
        super().__init__(message)
        self.kind = kind


class IntakeResult(enum.StrEnum):
    QUEUED = "queued"
    REJECTED = "rejected"
    RETRY = "retry"


@dataclass
class ManifestLine:
    file: str  # relative to the experiment folder, after sanitising
    experiment: str
    bioreplicate: int
    data_type: str


@dataclass
class Plan:
    source: str
    folder: FolderName
    dest: str
    date_source: str  # "name" | "overrides" | "drop"
    raw_dir: str  # "" or "raw"
    renames: dict[str, str] = field(default_factory=dict)
    layout: dict[str, dict[int, list[int]]] = field(default_factory=dict)
    manifest: list[ManifestLine] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)
    overrides: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["folder"]["date"] = self.folder.date.isoformat() if self.folder.date else None
        d["folder"]["tokens"] = list(self.folder.tokens)
        d["layout"] = {s: {str(r): f for r, f in reps.items()} for s, reps in self.layout.items()}
        return d


@dataclass
class DraftFile:
    filename: str
    experiment: str = ""
    bioreplicate: str = ""
    fraction: str = ""
    error: str = ""


@dataclass
class Draft:
    """Best-effort interpretation for a human to correct. Strings, so a GUI can edit them."""

    folder: str
    problem: str
    kind: Kind
    user: str
    method: str
    date: str
    known_users: list[str]
    known_methods: list[str]
    files: list[DraftFile]
    layout_error: str = ""
    allow_uneven: bool = False


class Resolver(Protocol):
    def resolve(self, d: Draft) -> Overrides | None: ...


# ------------------------------------------------------------------- plan --


def _find_raws(folder: Path) -> tuple[str, list[str], list[str]]:
    """(raw_dir, raw filenames, other top-level files). Raws at top level or in raw/."""
    top = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith((".", "~$"))]
    raws = [p.name for p in top if p.name.lower().endswith(RAW_SUFFIX)]
    raw_dir = ""
    sub = folder / "raw"
    if not raws and sub.is_dir():
        raws = [p.name for p in sub.iterdir() if p.is_file() and p.name.lower().endswith(RAW_SUFFIX)]
        raw_dir = "raw"
    others = sorted(p.name for p in top if not p.name.lower().endswith(RAW_SUFFIX))
    return raw_dir, sorted(raws), others


def _users(cfg: Config) -> dict[str, str]:
    return build_user_lookup(cfg.known_users(), cfg.user_aliases)


def _resolve_method(name: str, cfg: Config, raw_names: list[str], ov: Overrides) -> str:
    if ov.method:
        key = next((k for k in cfg.methods if k.lower() == ov.method.lower()), None)
        if not key:
            raise IntakeError(
                f"experiment.yaml method {ov.method!r} is not one of {', '.join(cfg.methods)}", Kind.OVERRIDES
            )
        return key
    try:
        return find_method(name, cfg.method_aliases, fallback_names=raw_names)
    except NamingError as exc:
        raise IntakeError(str(exc), Kind.METHOD) from exc


def _resolve_user(name: str, cfg: Config, ov: Overrides) -> str:
    if ov.user:
        udir = cfg.users_root / ov.user
        if not udir.is_dir():
            if ov.resolved_by == "gui":
                log.info("creating new user folder %s (named in the resolver window)", udir)
                udir.mkdir(parents=True)
            else:
                raise IntakeError(f"experiment.yaml user {ov.user!r}: no folder {udir}", Kind.OVERRIDES)
        return ov.user
    try:
        return find_user(name, _users(cfg), cfg.method_aliases)
    except NamingError as exc:
        if cfg.default_user and "no known user" in str(exc):
            log.warning("%s: no user recognised; filing under %r", name, cfg.default_user)
            return cfg.default_user
        raise IntakeError(str(exc), Kind.USER) from exc


def _lenient(filename: str, method: str) -> RawName:
    """Parse a raw name, falling back to (stem, rep 1, no fraction) so overrides can fill it in."""
    try:
        return parse_raw_name(filename, method)
    except NamingError:
        stem = sanitize(filename[: -len(RAW_SUFFIX)])
        return RawName(filename=filename, safe_filename=stem + RAW_SUFFIX, sample=stem, rep=1, fraction=None)


def plan(folder: Path, cfg: Config, ledger: Ledger | None = None) -> Plan:
    folder = Path(folder)
    if not folder.is_dir():
        raise IntakeError(f"not a folder: {folder}")

    try:
        ov = load_overrides(folder)
    except OverridesError as exc:
        raise IntakeError(str(exc), Kind.OVERRIDES) from exc

    raw_dir, raw_names, others = _find_raws(folder)
    if not raw_names:
        raise IntakeError("no .raw files found (top level or in a raw/ subfolder)", Kind.NO_RAWS)

    method = _resolve_method(folder.name, cfg, raw_names, ov)
    user = _resolve_user(folder.name, cfg, ov)
    d = ov.date or find_date(folder.name)
    fn = FolderName(
        original=folder.name, safe=sanitize(folder.name), method=method, user=user, date=d,
        tokens=tokens_of(folder.name),
    )
    mcfg = cfg.methods[method]

    try:
        raws: RawSet = group_raws(raw_names, method, allow_uneven=ov.allow_uneven_fractions)
    except NamingError as exc:
        msg = str(exc)
        if not ov.files:
            kind = Kind.RAWS if ("must end" in msg or "not a .raw" in msg) else Kind.LAYOUT
            raise IntakeError(msg, kind) from exc
        # per-file overrides may fix a bad tail: parse leniently, then apply them
        raws = RawSet(method=method, files=[_lenient(f, method) for f in raw_names])
    try:
        raws = apply_file_overrides(raws, ov)
    except OverridesError as exc:
        raise IntakeError(str(exc), Kind.OVERRIDES) from exc

    if ledger is not None and ledger.already_taken(fn.safe):
        raise IntakeError(
            f"a job named {fn.safe!r} was already processed; rename the folder (e.g. add _redo)", Kind.DEST
        )
    dest = cfg.users_root / user / fn.safe
    if dest.exists():
        raise IntakeError(f"destination already exists: {dest} — rename the folder (e.g. add _redo)", Kind.DEST)

    renames = {r.filename: r.safe_filename for r in raws.files if r.filename != r.safe_filename}
    if len(set(renames.values())) != len(renames):
        raise IntakeError("two raw files sanitise to the same name; rename them", Kind.RAWS)

    prefix = f"{raw_dir}/" if raw_dir else ""
    manifest = [ManifestLine(prefix + r.safe_filename, r.sample, r.rep, mcfg.data_type) for r in raws.files]
    return Plan(
        source=str(folder), folder=fn, dest=str(dest),
        date_source="overrides" if ov.date else ("name" if fn.date else "drop"),
        raw_dir=raw_dir, renames=renames, layout=raws.layout, manifest=manifest,
        other_files=others, overrides=ov.to_dict(),
    )


# ------------------------------------------------------------------ draft --


def draft(folder: Path, cfg: Config, error: IntakeError | None = None) -> Draft:
    """What we *think* the folder means, with blanks where we failed. For the GUI."""
    folder = Path(folder)
    try:
        ov = load_overrides(folder)
    except OverridesError:
        ov = Overrides()
    _, raw_names, _ = _find_raws(folder)

    method = ov.method or ""
    if not method:
        try:
            method = find_method(folder.name, cfg.method_aliases, fallback_names=raw_names)
        except NamingError:
            method = ""
    user = ov.user or ""
    if not user:
        try:
            user = find_user(folder.name, _users(cfg), cfg.method_aliases)
        except NamingError:
            user = ""
    d = ov.date or find_date(folder.name)

    files: list[DraftFile] = []
    for f in raw_names:
        df = DraftFile(filename=f, experiment=sanitize(f[: -len(RAW_SUFFIX)]), bioreplicate="1")
        if method:
            try:
                r = parse_raw_name(f, method)
                df.experiment, df.bioreplicate = r.sample, str(r.rep)
                df.fraction = "" if r.fraction is None else str(r.fraction)
            except NamingError as exc:
                df.error = str(exc)
        fo = ov.files.get(f)
        if fo:
            df.experiment = fo.experiment or df.experiment
            if fo.bioreplicate is not None:
                df.bioreplicate = str(fo.bioreplicate)
            if fo.fraction is not None:
                df.fraction = "" if fo.fraction < 0 else str(fo.fraction)
        files.append(df)

    layout_error = ""
    if method and all(not f.error for f in files):
        try:
            group_raws(raw_names, method, allow_uneven=ov.allow_uneven_fractions)
        except NamingError as exc:
            layout_error = str(exc)

    return Draft(
        folder=folder.name,
        problem=str(error) if error else "",
        kind=error.kind if error else Kind.OTHER,
        user=user, method=method, date=d.isoformat() if d else "",
        known_users=cfg.known_users(), known_methods=list(cfg.methods),
        files=files, layout_error=layout_error, allow_uneven=ov.allow_uneven_fractions,
    )


# ----------------------------------------------------------------- intake --


def _move_tree(src: Path, dst: Path) -> str:
    """Atomic rename when possible; otherwise copy+verify+delete. Returns how."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
        return "rename"
    except OSError as exc:
        if exc.errno != errno.EXDEV:  # Windows maps "different drive" to EXDEV too
            raise
    log.warning("inbox and users_root are on different volumes; copying %s (slow)", src.name)
    free = shutil.disk_usage(dst.parent).free
    need = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
    if free < need * 1.1:
        raise IntakeError(f"not enough space on {dst.parent}: need {need / 1e9:.1f} GB, have {free / 1e9:.1f} GB")
    shutil.copytree(src, dst)
    for a in src.rglob("*"):
        if a.is_file():
            b = dst / a.relative_to(src)
            if not b.is_file() or a.stat().st_size != b.stat().st_size:
                raise IntakeError(f"copy verification failed for {a.relative_to(src)}; source left in place")
    shutil.rmtree(src)
    return "copy"


def write_status(dest: Path, record: dict) -> None:
    tmp = dest / "labwatch.json.tmp"
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, dest / "labwatch.json")


def note_path(folder: Path) -> Path:
    return folder.parent / f"{folder.name}{REJECT_SUFFIX}"


def _reject(folder: Path, reason: str) -> None:
    body = (
        f"labwatch could not take in this folder.\n\n"
        f"Folder : {folder.name}\nWhen   : {datetime.now(UTC).isoformat(timespec='seconds')}\n"
        f"Reason : {reason}\n\n"
        f"Fix the folder (rename it, rename the files, or add an experiment.yaml) and\n"
        f"labwatch will try again automatically. Deleting this note also triggers a retry.\n"
        f"Naming rules: {NAMING_DOC}\n"
    )
    note_path(folder).write_text(body, encoding="utf-8")
    log.warning("REJECTED %s: %s", folder.name, reason)


def _clear_note(folder: Path) -> None:
    n = note_path(folder)
    if n.exists():
        try:
            n.unlink()
        except OSError:
            pass


def _rename_retry(a: Path, b: Path, attempts: int = 5) -> None:
    for i in range(attempts):
        try:
            os.rename(a, b)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5 * (i + 1))


def intake(folder: Path, cfg: Config, ledger: Ledger, resolver: Resolver | None = None) -> IntakeResult:
    folder = Path(folder)
    try:
        p = plan(folder, cfg, ledger)
    except IntakeError as exc:
        if exc.kind in RESOLVABLE and resolver is not None:
            log.info("asking for help with %s (%s): %s", folder.name, exc.kind.value, exc)
            ov = resolver.resolve(draft(folder, cfg, exc))
            if ov is None:
                _reject(folder, f"{exc} (skipped in the resolver window)")
                return IntakeResult.REJECTED
            ov.resolved_by = ov.resolved_by or "gui"
            save_overrides(folder, ov)
            try:
                p = plan(folder, cfg, ledger)
            except IntakeError as exc2:
                _reject(folder, f"after manual fix: {exc2}")
                return IntakeResult.REJECTED
        else:
            _reject(folder, str(exc))
            return IntakeResult.REJECTED

    dest = Path(p.dest)
    try:
        how = _move_tree(folder, dest)
    except PermissionError as exc:
        # Windows: Explorer / antivirus still has a handle on a just-copied file. Try again later.
        log.warning("cannot move %s yet (%s); will retry", folder.name, exc.strerror or exc)
        return IntakeResult.RETRY
    except IntakeError as exc:
        _reject(folder, str(exc))
        return IntakeResult.REJECTED

    raw_base = dest / p.raw_dir if p.raw_dir else dest
    for old, new in p.renames.items():
        _rename_retry(raw_base / old, raw_base / new)

    mcfg = cfg.methods[p.folder.method]
    record = {
        "labwatch_version": __version__,
        "status": "queued",
        "reason": None,
        "queued_at": now_iso(),
        "moved_by": how,
        "plan": p.to_json(),
        "method_config": {
            "workflow": p.overrides.get("workflow") or mcfg.workflow,
            "fasta": p.overrides.get("fasta") or mcfg.fasta,
            "data_type": mcfg.data_type,
            "postprocess": list(mcfg.postprocess),
        },
    }
    write_status(dest, record)
    _clear_note(folder)

    job = Job(inbox_name=p.folder.safe, user=p.folder.user, method=p.folder.method,
              dest_dir=str(dest), parsed=record)
    ledger.insert(job)
    log.info("queued job %d: %s -> %s (%s, %d raw files)", job.id, folder.name, dest,
             p.folder.method, len(p.manifest))
    return IntakeResult.QUEUED
