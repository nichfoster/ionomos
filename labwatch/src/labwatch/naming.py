"""
Naming convention parsers. Pure functions, no I/O.

This module is the executable form of docs/NAMING_CONVENTION.md. If the two
disagree, the doc is wrong or this is — fix both.

Folder:   <DATE>_<USER>_<METHOD>_<EXPID>[_<DESCRIPTION>]
          20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h

Raw file: <SAMPLE>_<REP>_<FRACTION>.raw   or   <SAMPLE>_<REP>.raw
          EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_3_7.raw
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

# Characters allowed anywhere in a folder or raw file name. Deliberately strict:
# FragPipe rejects spaces, and Windows/URL-unsafe punctuation causes grief later.
_ALLOWED = re.compile(r"^[A-Za-z0-9._-]+$")

# A field may contain '-' and '.', never '_' (that's the field separator).
_FIELD = r"[A-Za-z0-9.-]+"

_FOLDER_RE = re.compile(
    rf"^(?P<date>\d{{8}})_(?P<user>{_FIELD})_(?P<method>{_FIELD})_(?P<exp_id>{_FIELD})"
    rf"(?:_(?P<description>.+))?$"
)

# Trailing numeric suffixes on a raw file stem: "_<rep>" or "_<rep>_<fraction>".
_RAW_RE = re.compile(r"^(?P<sample>.+?)_(?P<rep>\d+)(?:_(?P<fraction>\d+))?$")

RAW_SUFFIX = ".raw"


class NamingError(ValueError):
    """A name does not follow the convention. The message is user-facing."""


@dataclass(frozen=True)
class FolderName:
    date: date
    user: str
    method: str  # canonical key from config (e.g. "isoDTB"), resolved by caller
    exp_id: str
    description: str | None
    raw: str


@dataclass(frozen=True)
class RawName:
    filename: str
    sample: str  # becomes FragPipe experiment
    rep: int  # becomes FragPipe bioreplicate
    fraction: int | None


@dataclass
class RawSet:
    """All raw files of one experiment folder, grouped and validated."""

    files: list[RawName]
    # sample -> rep -> sorted fractions (empty list for single-shot)
    layout: dict[str, dict[int, list[int]]] = field(default_factory=dict)

    @property
    def samples(self) -> list[str]:
        return sorted(self.layout)


def parse_folder_name(name: str, methods: dict[str, str] | None = None) -> FolderName:
    """Parse an experiment folder name.

    `methods` maps lowercase method token -> canonical key (e.g.
    {"isodtb": "isoDTB", "tmt": "TMT"}). When given, an unknown METHOD is an
    error and the returned `.method` is the canonical key. When None, the
    token is returned as typed.
    """
    if not name or name != name.strip():
        raise NamingError("folder name has leading/trailing whitespace")
    if " " in name:
        raise NamingError("folder name contains spaces (FragPipe cannot handle spaces in paths)")
    if not _ALLOWED.match(name):
        bad = sorted({c for c in name if not re.match(r"[A-Za-z0-9._-]", c)})
        raise NamingError(f"folder name contains disallowed characters: {' '.join(bad)!r}")

    m = _FOLDER_RE.match(name)
    if not m:
        raise NamingError(
            "folder name must be <YYYYMMDD>_<USER>_<METHOD>_<EXPID>[_<DESCRIPTION>], "
            "fields separated by '_' (use '-' inside a field)"
        )

    try:
        d = date(int(m["date"][:4]), int(m["date"][4:6]), int(m["date"][6:]))
    except ValueError:
        raise NamingError(f"{m['date']!r} is not a valid YYYYMMDD date") from None

    method_token = m["method"]
    if methods is not None:
        canonical = methods.get(method_token.lower())
        if canonical is None:
            raise NamingError(
                f"unknown METHOD {method_token!r}; expected one of "
                f"{', '.join(sorted(set(methods.values())))}"
            )
        method_token = canonical

    return FolderName(
        date=d,
        user=m["user"],
        method=method_token,
        exp_id=m["exp_id"],
        description=m["description"],
        raw=name,
    )


def parse_raw_name(filename: str) -> RawName:
    """Parse one raw file name into (sample, rep, fraction)."""
    if not filename.lower().endswith(RAW_SUFFIX):
        raise NamingError(f"{filename!r} is not a {RAW_SUFFIX} file")
    stem = filename[: -len(RAW_SUFFIX)]
    if " " in stem:
        raise NamingError(f"{filename!r} contains spaces")
    if not _ALLOWED.match(stem):
        raise NamingError(f"{filename!r} contains disallowed characters")

    m = _RAW_RE.match(stem)
    if not m:
        raise NamingError(
            f"{filename!r} must end in _<rep>.raw or _<rep>_<fraction>.raw "
            "(e.g. Sample_1.raw or Sample_1_3.raw)"
        )
    frac = m["fraction"]
    return RawName(
        filename=filename,
        sample=m["sample"],
        rep=int(m["rep"]),
        fraction=int(frac) if frac is not None else None,
    )


def group_raws(filenames: list[str]) -> RawSet:
    """Parse and validate a folder's worth of raw file names.

    Rules (each raises NamingError with a user-facing reason):
      * at least one file
      * every file parses
      * within one sample, every rep has the same fraction set
      * a sample is either all fractionated or all single-shot
      * no duplicate (sample, rep, fraction)
    """
    if not filenames:
        raise NamingError("no .raw files found")

    parsed = [parse_raw_name(f) for f in sorted(filenames)]

    seen: set[tuple[str, int, int | None]] = set()
    fractions: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    single: dict[str, set[int]] = defaultdict(set)

    for r in parsed:
        key = (r.sample, r.rep, r.fraction)
        if key in seen:
            raise NamingError(f"duplicate raw file for sample={r.sample} rep={r.rep} fraction={r.fraction}")
        seen.add(key)
        if r.fraction is None:
            single[r.sample].add(r.rep)
        else:
            fractions[r.sample][r.rep].add(r.fraction)

    layout: dict[str, dict[int, list[int]]] = {}
    for sample in set(single) | set(fractions):
        if sample in single and sample in fractions:
            raise NamingError(
                f"sample {sample!r} mixes fractionated (_rep_frac) and single-shot (_rep) files"
            )
        if sample in single:
            layout[sample] = {rep: [] for rep in sorted(single[sample])}
            continue
        reps = fractions[sample]
        expected = None
        for rep in sorted(reps):
            fr = sorted(reps[rep])
            if expected is None:
                expected = fr
            elif fr != expected:
                raise NamingError(
                    f"sample {sample!r}: rep {rep} has fractions {fr} but rep "
                    f"{sorted(reps)[0]} has {expected} — every replicate must have the "
                    "same fractions (incomplete copy?)"
                )
        layout[sample] = {rep: sorted(reps[rep]) for rep in sorted(reps)}

    return RawSet(files=parsed, layout=layout)
