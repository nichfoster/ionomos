"""
Intake — turn a stable inbox folder into a queued job, or reject it.

    plan(folder, cfg, ledger)   -> Plan            reads only; raises IntakeError
    intake(folder, cfg, ledger) -> Job | None      plan + move + labwatch.json + ledger row
                                                   None => rejected (.REJECTED.txt written)

Rules:
  * never delete, never overwrite: destination must not exist
  * the move is an atomic rename when inbox and users_root share a volume;
    otherwise copy → verify sizes → remove source (logged loudly)
  * folder and raw names are sanitised (spaces etc.) — originals recorded
"""
from __future__ import annotations

import errno
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from labwatch import __version__
from labwatch.config import Config
from labwatch.ledger import Job, Ledger, now_iso
from labwatch.naming import (
    RAW_SUFFIX,
    FolderName,
    NamingError,
    RawSet,
    build_user_lookup,
    find_date,
    find_method,
    group_raws,
    parse_folder_name,
    sanitize,
    tokens_of,
)

log = logging.getLogger("labwatch.intake")

NAMING_DOC = "docs/NAMING_CONVENTION.md"


class IntakeError(ValueError):
    """User-facing reason a folder cannot be taken in."""


@dataclass
class ManifestLine:
    file: str  # path relative to the experiment folder, after sanitising
    experiment: str
    bioreplicate: int
    data_type: str


@dataclass
class Plan:
    source: str
    folder: FolderName
    dest: str
    date_source: str  # "name" | "drop"
    raw_dir: str  # "" (top level) or "raw"
    renames: dict[str, str] = field(default_factory=dict)  # original -> safe (raw files only)
    layout: dict[str, dict[int, list[int]]] = field(default_factory=dict)
    manifest: list[ManifestLine] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["folder"]["date"] = self.folder.date.isoformat() if self.folder.date else None
        d["folder"]["tokens"] = list(self.folder.tokens)
        d["layout"] = {s: {str(r): f for r, f in reps.items()} for s, reps in self.layout.items()}
        return d


# -------------------------------------------------------------------- plan --


def _find_raws(folder: Path) -> tuple[str, list[str], list[str]]:
    """Return (raw_dir, raw filenames, other top-level files)."""
    top = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith((".", "~$"))]
    raws = [p.name for p in top if p.name.lower().endswith(RAW_SUFFIX)]
    raw_dir = ""
    sub = folder / "raw"
    if not raws and sub.is_dir():
        raws = [p.name for p in sub.iterdir() if p.is_file() and p.name.lower().endswith(RAW_SUFFIX)]
        raw_dir = "raw"
    others = sorted(p.name for p in top if not p.name.lower().endswith(RAW_SUFFIX))
    return raw_dir, sorted(raws), others


def _parse_folder(name: str, cfg: Config) -> FolderName:
    users = build_user_lookup(cfg.known_users(), cfg.user_aliases)
    try:
        return parse_folder_name(name, users, cfg.method_aliases)
    except NamingError as exc:
        if cfg.default_user and "no known user" in str(exc):
            log.warning("%s: no user recognised; filing under default user %r", name, cfg.default_user)
            toks = tokens_of(name)
            return FolderName(
                original=name, safe=sanitize(name), method=find_method(name, cfg.method_aliases),
                user=cfg.default_user, date=find_date(toks), tokens=toks,
            )
        raise IntakeError(str(exc)) from exc


def plan(folder: Path, cfg: Config, ledger: Ledger | None = None, drop_date: date | None = None) -> Plan:
    folder = Path(folder)
    if not folder.is_dir():
        raise IntakeError(f"not a folder: {folder}")

    fn = _parse_folder(folder.name, cfg)
    method = cfg.methods[fn.method]

    raw_dir, raw_names, others = _find_raws(folder)
    try:
        raws: RawSet = group_raws(raw_names, fn.method)
    except NamingError as exc:
        raise IntakeError(str(exc)) from exc

    if ledger is not None and ledger.already_taken(fn.safe):
        raise IntakeError(
            f"a job named {fn.safe!r} already exists in the ledger; rename the folder (e.g. add _redo)"
        )
    dest = cfg.users_root / fn.user / fn.safe
    if dest.exists():
        raise IntakeError(f"destination already exists: {dest} — rename the folder (e.g. add _redo)")

    renames = {r.filename: r.safe_filename for r in raws.files if r.filename != r.safe_filename}
    if len(set(renames.values())) != len(renames):
        raise IntakeError("two raw files sanitise to the same name; rename them")

    prefix = f"{raw_dir}/" if raw_dir else ""
    manifest = [
        ManifestLine(file=prefix + r.safe_filename, experiment=r.sample, bioreplicate=r.rep,
                     data_type=method.data_type)
        for r in raws.files
    ]
    return Plan(
        source=str(folder),
        folder=fn,
        dest=str(dest),
        date_source="name" if fn.date else "drop",
        raw_dir=raw_dir,
        renames=renames,
        layout=raws.layout,
        manifest=manifest,
        other_files=others,
    )


# ------------------------------------------------------------------ intake --


def _move_tree(src: Path, dst: Path) -> str:
    """Atomic rename when possible; otherwise copy+verify+delete. Returns how."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
        return "rename"
    except OSError as exc:
        if exc.errno != errno.EXDEV:  # Windows maps WinError 17 (different drive) to EXDEV too
            raise
    log.warning("inbox and users_root are on different volumes; copying %s (slow)", src.name)
    shutil.copytree(src, dst)
    for root, _d, files in os.walk(src):
        for f in files:
            a = Path(root) / f
            b = dst / a.relative_to(src)
            if not b.is_file() or a.stat().st_size != b.stat().st_size:
                raise IntakeError(f"copy verification failed for {a.relative_to(src)}; source left in place")
    shutil.rmtree(src)
    return "copy"


def write_status(dest: Path, record: dict) -> None:
    (dest / "labwatch.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


def _reject(folder: Path, reason: str) -> None:
    note = folder.parent / f"{folder.name}.REJECTED.txt"
    body = (
        f"labwatch could not take in this folder.\n\n"
        f"Folder : {folder.name}\nWhen   : {datetime.now(UTC).isoformat(timespec='seconds')}\n"
        f"Reason : {reason}\n\n"
        f"Fix the problem, delete this note, and drop the folder again.\n"
        f"Naming rules: {NAMING_DOC}\n"
    )
    note.write_text(body, encoding="utf-8")
    log.warning("REJECTED %s: %s", folder.name, reason)


def intake(folder: Path, cfg: Config, ledger: Ledger) -> Job | None:
    folder = Path(folder)
    try:
        p = plan(folder, cfg, ledger)
    except IntakeError as exc:
        _reject(folder, str(exc))
        return None

    dest = Path(p.dest)
    how = _move_tree(folder, dest)
    raw_base = dest / p.raw_dir if p.raw_dir else dest
    for old, new in p.renames.items():
        os.rename(raw_base / old, raw_base / new)

    record = {
        "labwatch_version": __version__,
        "status": "queued",
        "reason": None,
        "queued_at": now_iso(),
        "moved_by": how,
        "plan": p.to_json(),
        "method_config": {
            "workflow": cfg.methods[p.folder.method].workflow,
            "fasta": cfg.methods[p.folder.method].fasta,
            "data_type": cfg.methods[p.folder.method].data_type,
            "postprocess": list(cfg.methods[p.folder.method].postprocess),
        },
    }
    write_status(dest, record)

    job = Job(inbox_name=p.folder.safe, user=p.folder.user, method=p.folder.method,
              dest_dir=str(dest), parsed=record)
    ledger.insert(job)
    log.info("queued job %d: %s -> %s (%s, %d raw files)", job.id, folder.name, dest,
             p.folder.method, len(p.manifest))
    return job
