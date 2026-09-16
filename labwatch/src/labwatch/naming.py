"""
Naming rules. Pure functions, no I/O.

Executable form of docs/NAMING_CONVENTION.md. The philosophy is *flexible
folder names, strict raw-file tails*:

Folder name — anything, as long as somewhere in it we can find
    * a METHOD keyword   (isoDTB | TMT | DIA, matched via config aliases)
    * a USER token       (a known user dir name or a configured alias/initials)
    * optionally a DATE  (YYYYMMDD, MMDDYYYY or MMDDYY token)
  Spaces and odd punctuation are tolerated here and sanitised on move.

Raw file name — the END of the stem is reserved and its meaning depends on
the method:
    isoDTB   <sample>_<rep>_<fraction>.raw        e.g. X_1_1.raw, X_R2_F7.raw
             <sample>_<rep>.raw                   (unfractionated)
    TMT      <sample>[_TMT]_[F]<fraction>.raw     e.g. X_TMT_F1.raw, X_F3.raw, X_1.raw
             <sample>.raw                         (single fraction)
             every TMT run holds all replicates in channels -> bioreplicate 1
    DIA      <condition>_<biorep>.raw             e.g. DMSO_1.raw, Drug_R3.raw
  Separators before the numbers may be '_' or '-'. Optional R/F prefixes.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

RAW_SUFFIX = ".raw"

# What a sanitised name may contain. FragPipe cannot handle spaces in paths.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_TOKEN_SPLIT = re.compile(r"[\s_\-.()\[\]{}+&,;]+")

_SEP = r"[_-]"
_REP = r"(?:[Rr](?:ep)?)?"
_FRAC = r"(?:[Ff](?:rac)?)?"

_TAIL = {
    # sample, rep, frac  — frac may be missing for isoDTB (unfractionated)
    "isoDTB": re.compile(
        rf"^(?P<sample>.+?)(?:{_SEP}{_REP}(?P<rep>\d+))(?:{_SEP}{_FRAC}(?P<frac>\d+))?$"
    ),
    # sample[, frac]; rep is always 1
    "TMT": re.compile(rf"^(?P<sample>.+?)(?:{_SEP}[Tt][Mm][Tt])?(?:{_SEP}{_FRAC}(?P<frac>\d+))?$"),
    # condition, biorep
    "DIA": re.compile(rf"^(?P<sample>.+?)(?:{_SEP}{_REP}(?P<rep>\d+))?$"),
}

DEFAULT_METHOD_ALIASES: dict[str, list[str]] = {
    "isoDTB": ["isodtb", "iso-dtb", "iso_dtb"],
    "TMT": ["tmt"],
    "DIA": ["dia", "diann", "dia-nn"],
}


class NamingError(ValueError):
    """A name can't be interpreted. The message is user-facing."""


@dataclass(frozen=True)
class FolderName:
    original: str
    safe: str  # sanitised — what the folder is called after the move
    method: str  # canonical method key
    user: str  # canonical user dir name
    date: date | None  # None => not found in name; caller uses drop date
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class RawName:
    filename: str
    safe_filename: str
    sample: str  # FragPipe experiment
    rep: int  # FragPipe bioreplicate
    fraction: int | None


@dataclass
class RawSet:
    method: str
    files: list[RawName]
    # sample -> rep -> sorted fractions ([] for single-shot)
    layout: dict[str, dict[int, list[int]]] = field(default_factory=dict)

    @property
    def samples(self) -> list[str]:
        return sorted(self.layout)


# ------------------------------------------------------------------ helpers --


def sanitize(name: str) -> str:
    """Make a name safe for FragPipe: no spaces, only [A-Za-z0-9._-].

    Runs of anything else become a single '-'. Leading/trailing junk is trimmed.
    """
    s = _SAFE.sub("-", name.strip())
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"[-_]*_[-_]*", "_", s)  # collapse '-_-' style seams into '_'
    s = s.strip("-_.")
    if not s:
        raise NamingError(f"{name!r} has no usable characters")
    return s


def tokens_of(name: str) -> tuple[str, ...]:
    return tuple(t for t in _TOKEN_SPLIT.split(name) if t)


def _parse_date_token(tok: str) -> date | None:
    if not tok.isdigit():
        return None
    try:
        if len(tok) == 8:
            y, m, d = int(tok[:4]), int(tok[4:6]), int(tok[6:])
            if 2000 <= y <= 2100:
                return date(y, m, d)
            # MMDDYYYY
            m, d, y = int(tok[:2]), int(tok[2:4]), int(tok[4:])
            if 2000 <= y <= 2100:
                return date(y, m, d)
        if len(tok) == 6:  # MMDDYY
            return date(2000 + int(tok[4:]), int(tok[:2]), int(tok[2:4]))
    except ValueError:
        return None
    return None


def find_date(tokens: tuple[str, ...]) -> date | None:
    for t in tokens:
        d = _parse_date_token(t)
        if d:
            return d
    return None


def find_method(name: str, aliases: dict[str, list[str]] | None = None) -> str:
    """Find exactly one method keyword in a folder name.

    Token match first; if nothing, substring match (handles glued names like
    THB10ISODTB). Ambiguous (two methods) or none -> NamingError.
    """
    aliases = aliases or DEFAULT_METHOD_ALIASES
    low_tokens = {t.lower() for t in tokens_of(name)}
    low_name = name.lower()

    def hits(match):
        return {m for m, als in aliases.items() if any(match(a.lower()) for a in als)}

    found = hits(lambda a: a in low_tokens) or hits(lambda a: a in low_name)
    if not found:
        raise NamingError(
            f"no method keyword found in {name!r}; include one of "
            f"{', '.join(aliases)} in the folder name"
        )
    if len(found) > 1:
        raise NamingError(f"ambiguous method in {name!r}: found {', '.join(sorted(found))}")
    return found.pop()


def find_user(name: str, users: dict[str, str]) -> str:
    """Find the user this folder belongs to.

    `users` maps lowercase alias -> canonical user dir name (build with
    build_user_lookup). Tokens are matched exactly; multi-token user names
    (Taylor_Elements) are matched as consecutive tokens. Ambiguity -> error.
    """
    toks = tuple(t.lower() for t in tokens_of(name))
    found: set[str] = set()
    for alias, canonical in users.items():
        parts = tuple(t.lower() for t in tokens_of(alias))
        n = len(parts)
        if n and any(toks[i : i + n] == parts for i in range(len(toks) - n + 1)):
            found.add(canonical)
    if not found:
        raise NamingError(
            f"no known user in {name!r}; include your initials or folder name "
            f"(known: {', '.join(sorted(set(users.values())))})"
        )
    if len(found) > 1:
        raise NamingError(f"ambiguous user in {name!r}: matches {', '.join(sorted(found))}")
    return found.pop()


def build_user_lookup(user_dirs: list[str], aliases: dict[str, list[str]] | None = None) -> dict[str, str]:
    """{lowercase alias -> canonical dir}. Dir names are their own alias."""
    lookup: dict[str, str] = {}
    for d in user_dirs:
        lookup[d.lower()] = d
    for canonical, als in (aliases or {}).items():
        for a in als:
            lookup[a.lower()] = canonical
    return lookup


# ------------------------------------------------------------------ folders --


def parse_folder_name(
    name: str,
    users: dict[str, str],
    method_aliases: dict[str, list[str]] | None = None,
) -> FolderName:
    if not name.strip():
        raise NamingError("empty folder name")
    method = find_method(name, method_aliases)
    user = find_user(name, users)
    toks = tokens_of(name)
    return FolderName(
        original=name,
        safe=sanitize(name),
        method=method,
        user=user,
        date=find_date(toks),
        tokens=toks,
    )


# --------------------------------------------------------------------- raws --


def parse_raw_name(filename: str, method: str) -> RawName:
    if not filename.lower().endswith(RAW_SUFFIX):
        raise NamingError(f"{filename!r} is not a {RAW_SUFFIX} file")
    if method not in _TAIL:
        raise NamingError(f"unknown method {method!r}")
    stem = filename[: -len(RAW_SUFFIX)]
    safe_stem = sanitize(stem)
    m = _TAIL[method].match(safe_stem)
    if not m:  # only reachable for isoDTB (others accept a bare stem)
        raise NamingError(
            f"{filename!r}: isoDTB files must end in _<rep>_<fraction>.raw or _<rep>.raw"
        )
    rep = m.groupdict().get("rep")
    frac = m.groupdict().get("frac")
    return RawName(
        filename=filename,
        safe_filename=safe_stem + RAW_SUFFIX,
        sample=m["sample"],
        rep=int(rep) if rep is not None else 1,
        fraction=int(frac) if frac is not None else None,
    )


def group_raws(filenames: list[str], method: str) -> RawSet:
    """Parse and validate all raws of one folder.

    * at least one file; every file parses
    * no duplicate (sample, rep, fraction)
    * within a sample: all fractionated or all single-shot, and every rep has
      the same fraction set (catches a half-finished copy)
    """
    if not filenames:
        raise NamingError("no .raw files found")
    parsed = [parse_raw_name(f, method) for f in sorted(filenames)]

    seen: set[tuple] = set()
    fr: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    single: dict[str, set[int]] = defaultdict(set)
    for r in parsed:
        key = (r.sample, r.rep, r.fraction)
        if key in seen:
            raise NamingError(
                f"two files resolve to sample={r.sample} rep={r.rep} fraction={r.fraction}"
            )
        seen.add(key)
        (single[r.sample].add(r.rep) if r.fraction is None else fr[r.sample][r.rep].add(r.fraction))

    layout: dict[str, dict[int, list[int]]] = {}
    for sample in set(single) | set(fr):
        if sample in single and sample in fr:
            raise NamingError(f"sample {sample!r} mixes fractionated and single-shot files")
        if sample in single:
            layout[sample] = {rep: [] for rep in sorted(single[sample])}
            continue
        reps = fr[sample]
        first = min(reps)
        expected = sorted(reps[first])
        for rep in sorted(reps):
            got = sorted(reps[rep])
            if got != expected:
                raise NamingError(
                    f"sample {sample!r}: rep {rep} has fractions {got} but rep {first} has "
                    f"{expected} — incomplete copy?"
                )
        layout[sample] = {rep: sorted(reps[rep]) for rep in sorted(reps)}
    return RawSet(method=method, files=parsed, layout=layout)
