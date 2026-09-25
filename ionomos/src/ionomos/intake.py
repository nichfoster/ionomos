"""
Intake — turn a stable inbox folder into a queued job, resolve problems, or reject.

    plan(folder, cfg, ledger)              -> Plan          read-only; raises IntakeError(kind=...)
    draft(folder, cfg, error)              -> Draft         best-effort parse for the GUI, never raises
    intake(folder, cfg, ledger, resolver)  -> IntakeResult  QUEUED | REJECTED | RETRY

Flow:
    plan ──ok──▶ move ──▶ ionomos.json ──▶ ledger row            (QUEUED)
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
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ionomos import __version__
from ionomos.config import Config
from ionomos.ledger import Job, Ledger, now_iso
from ionomos.manifest import (
    Overrides,
    OverridesError,
    apply_file_overrides,
    load_overrides,
    save_overrides,
)
from ionomos.naming import (
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

log = logging.getLogger("ionomos.intake")

NAMING_DOC = "docs/NAMING_CONVENTION.md"
REJECT_SUFFIX = ".REJECTED.txt"


class Kind(enum.StrEnum):
    """What went wrong. The GUI can fix the first four; the rest need a rename."""

    USER = "user"
    METHOD = "method"
    RAWS = "raws"  # a file name's tail can't be parsed
    LAYOUT = "layout"  # uneven fractions / mixed single+fractionated / duplicates / raws in two places
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
    source: str = ""


class Resolver(Protocol):
    def resolve(self, d: Draft) -> Overrides | None: ...


# ------------------------------------------------------------------- plan --


def _find_raws(folder: Path) -> tuple[str, list[str], list[str]]:
    """(raw_dir, raw filenames, other top-level files). Raws at top level or in raw/.

    Raises IntakeError(Kind.LAYOUT) when .raw files sit in both places — the manifest
    would silently cover just one of the two sets (issue #14).
    """
    top = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith((".", "~$"))]
    raws = [p.name for p in top if p.name.lower().endswith(RAW_SUFFIX)]
    sub = folder / "raw"
    sub_raws = (
        [p.name for p in sub.iterdir() if p.is_file() and p.name.lower().endswith(RAW_SUFFIX)]
        if sub.is_dir()
        else []
    )
    if raws and sub_raws:
        # A mixed drop would file a manifest of one set while the watcher counts both (#14).
        raise IntakeError(
            f"{len(raws)} .raw file(s) at the top level and {len(sub_raws)} in raw/ — "
            "keep them in one place so the manifest is complete",
            Kind.LAYOUT,
        )
    raw_dir = ""
    if sub_raws:
        raws = sub_raws
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
    """Read-only interpretation of a folder. Raises IntakeError (never a bare NamingError)."""
    try:
        return _plan(folder, cfg, ledger)
    except NamingError as exc:  # e.g. a name made only of symbols/emoji survives until sanitize()
        msg = str(exc)
        if "no usable characters" in msg:
            msg += " — rename it using letters and digits (e.g. 20260902_EJQ_isoDTB_sample)"
        raise IntakeError(msg, Kind.RAWS if ".raw" in msg.lower() else Kind.OTHER) from exc


def _safe(name: str, fallback: str = "unnamed") -> str:
    try:
        return sanitize(name)
    except NamingError:
        return fallback


def _plan(folder: Path, cfg: Config, ledger: Ledger | None = None) -> Plan:
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
    from ionomos.naming_history import suggestions

    if ov.resolved_by == "gui":
        # A previously resolved raw may have been removed in Explorer or the inbox UI.
        ov.files = {name: value for name, value in ov.files.items()
                    if name in raw_names or name in {sanitize(f[:-4]) + RAW_SUFFIX for f in raw_names}}
    learned = suggestions(cfg, user, method, raw_names)
    ov.files = {**learned, **ov.files}  # explicit experiment.yaml always wins

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
    finals = [r.safe_filename.lower() for r in raws.files]  # lower(): Windows names are case-insensitive
    if len(set(finals)) != len(finals):
        raise IntakeError("two raw files end up with the same name after removing spaces/symbols; "
                          "rename one of them", Kind.RAWS)

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
    try:
        _, raw_names, _ = _find_raws(folder)
    except IntakeError:
        # Mixed top-level + raw/ layout: the naming window still gets the raws it can see.
        raw_names = [p.name for p in folder.iterdir()
                     if p.is_file() and not p.name.startswith((".", "~$"))
                     and p.name.lower().endswith(RAW_SUFFIX)]

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
        df = DraftFile(filename=f, experiment=_safe(f[: -len(RAW_SUFFIX)]), bioreplicate="1")
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
        files=files, layout_error=layout_error, allow_uneven=ov.allow_uneven_fractions, source=str(folder),
    )


# ----------------------------------------------------------------- intake --


def _sha256(path: Path) -> str:
    """SHA-256 of a file's contents, read in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
    # Content hashes, not sizes — a same-size corrupt copy must never delete the source.
    for a in src.rglob("*"):
        if a.is_file():
            b = dst / a.relative_to(src)
            if not b.is_file() or _sha256(a) != _sha256(b):
                # The half-copy is ours, not user data — remove it so re-filing is
                # not wedged by "destination already exists". If cleanup fails
                # (Windows handle), the error names the leftover.
                try:
                    _rmtree_retry(dst)
                    leftover = ""
                except OSError:
                    leftover = (
                        f" A partial copy remains at {dst} — "
                        "delete it before retrying."
                    )
                raise IntakeError(
                    f"copy verification failed for {a.relative_to(src)}; "
                    f"source left in place.{leftover}"
                )
    try:
        _rmtree_retry(src)
    except PermissionError as exc:
        raise IntakeError(
            f"copy complete at {dst}; could not remove the inbox copy at {src}. "
            "The inbox folder may be partial — keep the filed copy and delete the inbox "
            "one (if you already renamed it, merge its contents into the filed copy)."
        ) from exc
    return "copy"


def _rmtree_retry(src: Path, attempts: int = 5) -> None:
    """Remove the inbox copy, retrying Windows locks (same contract as _rename_retry)."""
    for i in range(attempts):
        try:
            shutil.rmtree(src)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5 * (i + 1))


def write_status(dest: Path, record: dict) -> None:
    from ionomos import names

    names.adopt_legacy_status(dest)
    tmp = dest / (names.STATUS_FILE + ".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, dest / names.STATUS_FILE)


def note_path(folder: Path) -> Path:
    return folder.parent / f"{folder.name}{REJECT_SUFFIX}"


def _reject(folder: Path, reason: str) -> None:
    body = (
        f"ionomos could not take in this folder.\n\n"
        f"Folder : {folder.name}\nWhen   : {datetime.now(UTC).isoformat(timespec='seconds')}\n"
        f"Reason : {reason}\n\n"
        f"Fix the folder (rename it, rename the files, or add an experiment.yaml) and\n"
        f"ionomos will try again automatically. Deleting this note also triggers a retry.\n"
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
    """Rename a -> b, retrying Windows locks. Never overwrites: plan() guarantees b is free."""
    if b.exists() and a.resolve() != b.resolve():
        raise FileExistsError(f"refusing to overwrite {b}")
    for i in range(attempts):
        try:
            os.rename(a, b)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5 * (i + 1))


def intake(folder: Path, cfg: Config, ledger: Ledger, resolver: Resolver | None = None) -> IntakeResult:
    """Never raises for a folder's content: transient disk trouble -> RETRY, a bug -> REJECTED with a note."""
    folder = Path(folder)
    try:
        res = _intake(folder, cfg, ledger, resolver)
        _tell_a_person(folder, cfg, res)
        return res
    except (PermissionError, FileNotFoundError, BlockingIOError, InterruptedError) as exc:
        log.warning("transient problem with %s (%s); will retry", folder.name, exc)
        return IntakeResult.RETRY
    except Exception as exc:
        log.exception("unexpected error while taking in %s", folder.name)
        if not folder.is_dir():
            return IntakeResult.RETRY  # half-moved? let the next scan look again
        try:
            _reject(folder, f"ionomos hit an unexpected problem with this folder ({type(exc).__name__}: {exc}). "
                            f"It was left untouched. Please send diagnostics (Ionomos app -> Run & Test).")
        except OSError:
            return IntakeResult.RETRY
        _tell_a_person(folder, cfg, IntakeResult.REJECTED)
        return IntakeResult.REJECTED


def _tell_a_person(folder: Path, cfg: Config, res: IntakeResult) -> None:
    """A rejected folder becomes a pop-up (unless the person just skipped it in the naming window);
    a folder that went through closes its old pop-up."""
    try:
        from ionomos import attention

        log_dir = getattr(cfg, "log_dir", None)
        key = f"intake:{folder.name}"
        if res == IntakeResult.QUEUED:
            for it in attention.items(log_dir):
                if it.key == key:
                    attention.resolve(log_dir, it.id)
            return
        if res != IntakeResult.REJECTED:
            return
        note = note_path(folder)
        text = note.read_text(encoding="utf-8") if note.is_file() else ""
        reason = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("Reason")), text[:300])
        if "skipped in the resolver window" in reason:
            return
        attention.raise_item(
            log_dir, "intake_rejected", f"Couldn't take in “{folder.name}”", reason, key=key, severity="error",
            dest=folder,
            causes=["The folder or file names don't follow the naming rules (user, method, replicate)",
                    "A folder of that name was already filed", "No raw files in the folder"],
            fixes=["Rename the folder or files (the naming window opens when Ionomos can guess), or add an "
                   "experiment.yaml", "Deleting the .REJECTED.txt note makes Ionomos try again"],
            details=text, data={"folder": str(folder), "note": str(note)})
    except Exception:  # noqa: BLE001 - telling a person must never break intake
        log.exception("could not record the rejection of %s for the app", folder.name)


def _intake(folder: Path, cfg: Config, ledger: Ledger, resolver: Resolver | None = None) -> IntakeResult:
    try:
        p = plan(folder, cfg, ledger)
    except IntakeError as exc:
        if exc.kind in RESOLVABLE and resolver is not None:
            log.info("asking for help with %s (%s): %s", folder.name, exc.kind.value, exc)
            from ionomos.watcher import fingerprint

            before = fingerprint(folder)
            ov = resolver.resolve(draft(folder, cfg, exc))
            if not folder.is_dir() or fingerprint(folder) != before:
                _clear_note(folder)
                return IntakeResult.RETRY

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
            try:
                from ionomos.naming_history import remember

                confirmed = draft(folder, cfg)
                remember(cfg, folder.name, p.folder.user, p.folder.method, confirmed.files)
            except OSError:
                log.exception("Could not save naming history")
        else:
            _reject(folder, str(exc))
            return IntakeResult.REJECTED

    dest = Path(p.dest)
    try:
        how = _move_tree(folder, dest)
    except PermissionError as exc:
        # Windows: Explorer / antivirus still has a handle on a just-copied file. Try again later.
        log.warning("cannot move %s yet (%s); will retry", folder.name, exc.strerror or exc)
        return IntakeResult.RETRY  # pre-move: a RETRY is still honest
    except IntakeError as exc:
        _reject(folder, str(exc))
        return IntakeResult.REJECTED

    # From here the folder is at dest. A RETRY would lose it: the watcher forgets
    # folders no longer in the inbox, and both sweeps look for a status file that
    # does not exist yet. File it best-effort instead.
    raw_base = dest / p.raw_dir if p.raw_dir else dest
    try:
        for old, new in p.renames.items():
            _rename_retry(raw_base / old, raw_base / new)

        mcfg = cfg.methods[p.folder.method]
        record = {
            "ionomos_version": __version__,
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
    except Exception as exc:  # noqa: BLE001 - the folder is already moved; don't report a retry
        log.exception("trouble finishing intake for %s; filing a minimal record instead", dest)
        try:
            write_status(dest, {
                "ionomos_version": __version__,
                "status": "queued",
                "reason": f"intake hit {type(exc).__name__} after the move: {exc}",
                "queued_at": now_iso(),
                "moved_by": how,
                "plan": p.to_json(),
            })
        except OSError:
            log.critical("%s is moved but untracked: no status file could be written, "
                         "so the reconciliation sweeps cannot see it", dest)
        return IntakeResult.QUEUED
    _clear_note(folder)

    job = Job(inbox_name=p.folder.safe, user=p.folder.user, method=p.folder.method,
              dest_dir=str(dest), parsed=record)
    try:
        ledger.insert(job)
    except Exception:  # noqa: BLE001 - the folder is already moved; don't report a retry
        log.exception("filed %s but could not record it in the job list; it will be adopted automatically "
                      "(ionomos checks for such folders at start-up and every hour)", dest)
        return IntakeResult.QUEUED
    log.info("queued job %d: %s -> %s (%s, %d raw files)", job.id, folder.name, dest,
             p.folder.method, len(p.manifest))
    return IntakeResult.QUEUED
