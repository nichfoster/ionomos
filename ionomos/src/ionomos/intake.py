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
    EXPERIMENT_YAML,
    NumberOutOfRange,
    Overrides,
    OverridesError,
    apply_file_overrides,
    load_overrides,
    parse_overrides,
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
# NamingError texts that mean "one file's name doesn't fit its method's rule" (fixable per file in the window)
RAW_NAME_PROBLEMS = ("must end", "must look like", "must match the pattern", "not a .raw", "is not a number",
                     "no sample name left")
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
    # a drop laid out as <plex>/*.raw (one folder per TMT plex, D69): safe raw file name -> its (safe) folder
    raw_subdirs: dict[str, str] = field(default_factory=dict)
    subdir_renames: dict[str, str] = field(default_factory=dict)  # folder as dropped -> safe name
    warnings: list[str] = field(default_factory=list)  # filed anyway, but worth saying (log, ionomos.json)

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
    review: bool = False  # nothing is wrong: the person checks how the drop was read before it is filed
    control: str = ""  # experiment.yaml analysis.control, if set
    control_keywords: list[str] = field(default_factory=list)  # how the analysis recognises a control
    condition_codes: dict[str, str] = field(default_factory=dict)  # DIA X_D1 -> DMSO rep 1
    file_rules: dict = field(default_factory=dict)  # method -> naming.FileRule (config naming.methods); {} = built-in
    kinds: dict[str, str] = field(default_factory=dict)  # method -> what it behaves as (Config.kind); {} = its key
    lab_analysis: dict = field(default_factory=dict)  # config.yaml analysis: (roles preview, D65)
    exp_analysis: dict = field(default_factory=dict)  # experiment.yaml analysis:, as written (its roles: included)

    def kind_of(self, method: str) -> str:
        return self.kinds.get(method, method)


class Resolver(Protocol):
    def resolve(self, d: Draft) -> Overrides | None: ...

    # optional: review(d) -> Overrides | None, the "check before filing" window for drops that parsed cleanly


# ------------------------------------------------------------------- plan --


def _find_raws(folder: Path) -> tuple[str, list[str], list[str]]:
    """(raw_dir, raw filenames, other top-level files). Raws at top level or in raw/, else one folder down
    (<plex>/*.raw; _raw_places says which folder each is in).

    Raises IntakeError(Kind.LAYOUT) when .raw files sit in both places — the manifest
    would silently cover just one of the two sets (issue #14).
    """
    return _raw_places(folder)[:3]


def _raw_places(folder: Path) -> tuple[str, list[str], list[str], dict[str, str]]:
    """_find_raws, and {raw file name: the subfolder it is in} for a drop laid out one folder per plex (D69):
    used only when neither the top level nor raw/ has a .raw, so a drop that was filed before is read as before.
    The layout is kept as dropped; FragPipe takes each plex's annotation.txt from the folder of its files."""
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
    subdirs: dict[str, str] = {}
    if not raws:
        seen: dict[str, str] = {}
        for d in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith((".", "~$"))
                        and p.name.lower() != "raw"):
            for f in sorted(p for p in d.iterdir() if p.is_file() and p.name.lower().endswith(RAW_SUFFIX)
                            and not p.name.startswith((".", "~$"))):
                if f.name.lower() in seen:
                    raise IntakeError(f"{f.name} is in both {seen[f.name.lower()]}/ and {d.name}/: FragPipe needs every "
                                      f"raw file name once — rename one of them", Kind.RAWS)
                seen[f.name.lower()] = d.name
                subdirs[f.name] = d.name
        raws = list(subdirs)
    return raw_dir, sorted(raws), others, subdirs


def raw_paths(folder: Path) -> list[Path]:
    """Every .raw file of a drop, where it is: top level, raw/, or one folder per plex (D69). For the app's
    inbox list and the review window's Delete. Raises IntakeError for a layout intake would refuse."""
    raw_dir, raws, _others, subdirs = _raw_places(folder)
    return [folder / (subdirs.get(n) or raw_dir) / n for n in raws]


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


def _lenient(filename: str, method: str, codes: dict[str, str] | None = None, rules=None) -> RawName:
    """Parse a raw name, falling back to (stem, rep 1, no fraction) so overrides can fill it in."""
    try:
        return parse_raw_name(filename, method, codes, rules)
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
    except NumberOutOfRange as exc:  # a replicate the window can correct (0.5.1 wrote time stamps, D85)
        raise IntakeError(f"experiment.yaml: {exc}", Kind.RAWS) from exc
    except OverridesError as exc:
        raise IntakeError(str(exc), Kind.OVERRIDES) from exc

    raw_dir, raw_names, others, subdirs = _raw_places(folder)
    if not raw_names:
        raise IntakeError("no .raw files found (top level, in a raw/ subfolder, or one folder per plex)",
                          Kind.NO_RAWS)

    method = _resolve_method(folder.name, cfg, raw_names, ov)
    user = _resolve_user(folder.name, cfg, ov)
    d = ov.date or find_date(folder.name, formats=cfg.date_formats)
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
        raws: RawSet = group_raws(raw_names, method, allow_uneven=ov.allow_uneven_fractions,
                                  codes=cfg.condition_codes, rules=cfg.file_rules)
    except NamingError as exc:
        msg = str(exc)
        if not ov.files:
            kind = Kind.RAWS if any(s in msg for s in RAW_NAME_PROBLEMS) else Kind.LAYOUT
            raise IntakeError(msg, kind) from exc
        # per-file overrides may fix a bad tail: parse leniently, then apply them
        raws = RawSet(method=method, files=[_lenient(f, method, cfg.condition_codes, cfg.file_rules) for f in raw_names])
    if subdirs and cfg.kind(method) != "TMT":  # a folder per plex is a TMT layout; other methods as before
        raise IntakeError("no .raw files found (top level or in a raw/ subfolder; raws one folder down are read "
                          "only for TMT, one folder per plex)", Kind.NO_RAWS)
    subdir_renames = {d: _safe(d, "plex") for d in dict.fromkeys(subdirs.values())}
    if len({v.lower() for v in subdir_renames.values()}) != len(subdir_renames):
        raise IntakeError("two plex folders end up with the same name after removing spaces/symbols; rename one "
                          "of them", Kind.LAYOUT)
    if subdirs and cfg.kind(method) == "TMT":
        # one folder per plex: the folder is the plex (FragPipe's experiment), whatever the file names say;
        # experiment.yaml files: still wins
        from ionomos.naming import group_from_parsed

        try:
            raws = group_from_parsed([RawName(r.filename, r.safe_filename, subdir_renames[subdirs[r.filename]], r.rep,
                                              r.fraction) for r in raws.files], method,
                                     allow_uneven=ov.allow_uneven_fractions)
        except NamingError as exc:
            raise IntakeError(f"{exc} (each plex folder is one plex)", Kind.LAYOUT) from exc
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

    def where(r: RawName) -> str:
        sub = subdir_renames.get(subdirs.get(r.filename, ""), "")
        return f"{raw_dir}/" if raw_dir else (f"{sub}/" if sub else "")

    manifest = [ManifestLine(where(r) + r.safe_filename, r.sample, r.rep, mcfg.data_type) for r in raws.files]
    warnings = _plex_warnings(cfg.kind(method), manifest)
    return Plan(
        source=str(folder), folder=fn, dest=str(dest),
        date_source="overrides" if ov.date else ("name" if fn.date else "drop"),
        raw_dir=raw_dir, renames=renames, layout=raws.layout, manifest=manifest,
        other_files=others, overrides=ov.to_dict(),
        raw_subdirs={r.safe_filename: subdir_renames[subdirs[r.filename]] for r in raws.files if r.filename in subdirs},
        subdir_renames={d: v for d, v in subdir_renames.items() if d != v}, warnings=warnings,
    )


def _plex_warnings(kind: str, manifest: list[ManifestLine]) -> list[str]:
    """A TMT drop whose plexes share a folder: filed as it is (how a drop is laid out is the lab's choice), with
    what FragPipe will do about the channel names (D69, fragpipe.shared_plex_warning)."""
    if kind != "TMT":
        return []
    folders: dict[str, set[str]] = {}
    for m in manifest:
        folders.setdefault(str(Path(m.file).parent), set()).add(m.experiment)
    shared = max((len(e) for e in folders.values()), default=0)
    if shared < 2:
        return []
    from ionomos.fragpipe import shared_plex_warning

    return [shared_plex_warning(len({m.experiment for m in manifest}))]


# ------------------------------------------------------------------ draft --


def _without_files(folder: Path) -> Overrides:
    """experiment.yaml without its files: block (one of its numbers is out of range, D85), or nothing."""
    import yaml

    try:
        data = yaml.safe_load((Path(folder) / EXPERIMENT_YAML).read_text(encoding="utf-8")) or {}
        return parse_overrides({k: v for k, v in data.items() if k != "files"})
    except (OSError, ValueError, AttributeError, yaml.YAMLError):  # OverridesError is a ValueError
        return Overrides()


def draft(folder: Path, cfg: Config, error: IntakeError | None = None, review: bool = False) -> Draft:
    """What we *think* the folder means, with blanks where we failed. For the GUI.
    review: nothing failed; the window shows the reading so a person can confirm or correct it."""
    folder = Path(folder)
    try:
        ov = load_overrides(folder)
    except NumberOutOfRange:  # the window shows the files as their names read; the rest of the file still counts
        ov = _without_files(folder)
    except OverridesError:
        ov = Overrides()
    subdirs: dict[str, str] = {}
    try:
        _, raw_names, _, subdirs = _raw_places(folder)
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
    d = ov.date or find_date(folder.name, formats=cfg.date_formats)

    files: list[DraftFile] = []
    for f in raw_names:
        df = DraftFile(filename=f, experiment=_safe(f[: -len(RAW_SUFFIX)]), bioreplicate="1")
        if method:
            try:
                r = parse_raw_name(f, method, cfg.condition_codes, cfg.file_rules)
                df.experiment, df.bioreplicate = r.sample, str(r.rep)
                df.fraction = "" if r.fraction is None else str(r.fraction)
            except NamingError as exc:
                df.error = str(exc)
        if f in subdirs and method in cfg.methods and cfg.kind(method) == "TMT":
            df.experiment = _safe(subdirs[f], "plex")  # one folder per plex: the folder is the plex (D69)
        fo = ov.files.get(f) or ov.files.get(_safe(f[: -len(RAW_SUFFIX)]) + RAW_SUFFIX)  # as apply_file_overrides
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
            group_raws(raw_names, method, allow_uneven=ov.allow_uneven_fractions, codes=cfg.condition_codes, rules=cfg.file_rules)
        except NamingError as exc:
            layout_error = str(exc)
    from ionomos.downstream.analysis import DEFAULT_CONTROL_KEYWORDS

    keywords = [str(k) for k in ((cfg.analysis or {}).get("control_keywords") or DEFAULT_CONTROL_KEYWORDS)]

    return Draft(
        folder=folder.name,
        problem=str(error) if error else "",
        kind=error.kind if error else Kind.OTHER,
        user=user, method=method, date=d.isoformat() if d else "",
        known_users=cfg.known_users(), known_methods=list(cfg.methods),
        files=files, layout_error=layout_error, allow_uneven=ov.allow_uneven_fractions, source=str(folder),
        review=review and error is None, control=str((ov.analysis or {}).get("control") or ""),
        control_keywords=keywords, condition_codes=dict(cfg.condition_codes), file_rules=dict(cfg.file_rules),
        kinds={m: cfg.kind(m) for m in cfg.methods},
        lab_analysis={k: v for k, v in (cfg.analysis or {}).items() if k != "enabled"},
        exp_analysis=dict(ov.analysis or {}),
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
    # Never dirs_exist_ok: copytree refusing an existing dst is what makes every
    # cleanup of dst below safe — all of it was written by this call (D29).
    try:
        shutil.copytree(src, dst)
    except FileExistsError:
        raise  # dst is not ours; leave it alone
    except OSError as exc:
        # Disk full, a locked file mid-copy, ... The source is untouched; discard the
        # half-copy so a retry isn't wedged by "destination already exists".
        leftover = _discard_partial_copy(dst)
        if leftover:
            raise IntakeError(f"copy to {dst} failed ({exc}); source left in place.{leftover}") from exc
        raise
    # Content hashes, not sizes — a same-size corrupt copy must never delete the source.
    for a in src.rglob("*"):
        if a.is_file():
            b = dst / a.relative_to(src)
            if not b.is_file() or _sha256(a) != _sha256(b):
                leftover = _discard_partial_copy(dst)
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


def _discard_partial_copy(dst: Path) -> str:
    """Remove a half-made copy at dst (ours, not user data). Returns "" or a note naming the leftover."""
    if not dst.exists():
        return ""
    try:
        _rmtree_retry(dst)
        return ""
    except OSError:  # a Windows handle outlived the retries
        return f" A partial copy remains at {dst} — delete it before retrying."


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


def _acquisition(dest: Path, p: Plan) -> dict:
    """ionomos.json "acquisition": when each raw file was acquired (acqtime.py, D78), {manifest file: info}.
    Reads the first bytes of each file; a file that can't be read gets what else is known. Never raises: a
    time is never a reason not to file a folder."""
    try:
        from ionomos import acqtime

        return acqtime.for_manifest(dest, [m.file for m in p.manifest])
    except Exception:  # noqa: BLE001
        log.exception("could not read the acquisition times of %s", dest)
        return {}


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
    _write_note(note_path(folder), body)
    log.warning("REJECTED %s: %s", folder.name, reason)


def _write_note(note: Path, body: str) -> None:
    """Whole or not at all: a note that exists is never seen empty by a person or the app.
    The temp name starts with '.', which intake ignores."""
    tmp = note.with_name(f".{note.name}.tmp")
    tmp.write_text(body, encoding="utf-8")
    try:
        os.replace(tmp, note)
    except OSError:  # Windows: the old note is open in an editor — overwrite it in place instead
        tmp.unlink(missing_ok=True)
        note.write_text(body, encoding="utf-8")


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


def _ask(folder: Path, cfg: Config, ledger: Ledger, resolver: Resolver, exc: IntakeError | None
         ) -> Plan | IntakeResult:
    """Show the folder to a person: exc = what went wrong, or None to review a clean reading before filing.
    Returns the plan after their answer (saved to experiment.yaml, so it sticks), or what to tell the watcher."""
    from ionomos.watcher import fingerprint

    if exc is not None:
        log.info("asking for help with %s (%s): %s", folder.name, exc.kind.value, exc)
        ask = resolver.resolve
    else:
        log.info("showing %s in the review window before filing", folder.name)
        ask = resolver.review
    before = fingerprint(folder)
    ov = ask(draft(folder, cfg, exc, review=exc is None))
    if not folder.is_dir() or fingerprint(folder) != before:
        _clear_note(folder)
        return IntakeResult.RETRY
    if ov is None:
        _reject(folder, f"{exc} (skipped in the resolver window)" if exc is not None
                else "skipped in the review window — fix anything that was read wrong, or delete this note to "
                     "see the window again")
        return IntakeResult.REJECTED
    ov.resolved_by = ov.resolved_by or "gui"
    save_overrides(folder, ov, replace_files=True)  # the window showed every raw: its answer is the files: block
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
    return p


def _intake(folder: Path, cfg: Config, ledger: Ledger, resolver: Resolver | None = None) -> IntakeResult:
    try:
        p = plan(folder, cfg, ledger)
    except IntakeError as exc:
        if exc.kind in RESOLVABLE and resolver is not None:
            got = _ask(folder, cfg, ledger, resolver, exc)
            if isinstance(got, IntakeResult):
                return got
            p = got
        else:
            _reject(folder, str(exc))
            return IntakeResult.REJECTED
    else:
        # A clean reading can still be the wrong one (X_D1 read as its own condition, KC not known yet):
        # a person sees it first, unless one already answered for this folder (experiment.yaml resolved_by gui).
        if (resolver is not None and getattr(resolver, "review", None) is not None and cfg.review_drops
                and (p.overrides or {}).get("resolved_by") != "gui"):
            got = _ask(folder, cfg, ledger, resolver, None)
            if isinstance(got, IntakeResult):
                return got
            p = got

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
        for old, new in p.subdir_renames.items():
            _rename_retry(dest / old, dest / new)
        for old, new in p.renames.items():
            sub = p.raw_subdirs.get(new, "")
            _rename_retry(raw_base / sub / old, raw_base / sub / new)
        for w in p.warnings:
            log.warning("%s: %s", p.folder.safe, w)

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
                "analysis_method": cfg.analysis_method(p.folder.method),  # what it is analysed as (D54)
            },
        }
        record["acquisition"] = _acquisition(dest, p)
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
