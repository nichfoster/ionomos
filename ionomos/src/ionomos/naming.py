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
# optional prefixes people put before the numbers: R1, rep1, Rep_1, bio1, biorep1, n1 / F1, frac1, fraction1
_REP = r"(?:(?:[Rr](?:ep)?|[Bb]io(?:[Rr]ep)?|[Nn])[_-]?)?"
_FRAC = r"(?:(?:[Ff](?:rac(?:tion)?)?)[_-]?)?"

_TAIL = {
    # sample, rep, frac  — frac may be missing for isoDTB (unfractionated)
    # Digit runs are capped at three: a longer tail is a date or an instrument
    # counter, never a replicate/fraction number (issue #15).
    "isoDTB": re.compile(
        rf"^(?P<sample>.+?)(?:{_SEP}{_REP}(?P<rep>\d{{1,3}}))(?:{_SEP}{_FRAC}(?P<frac>\d{{1,3}}))?$"
    ),
    # sample[, frac]; rep is always 1
    "TMT": re.compile(rf"^(?P<sample>.+?)(?:{_SEP}[Tt][Mm][Tt])?(?:{_SEP}{_FRAC}(?P<frac>\d{{1,3}}))?$"),
    # condition, biorep
    "DIA": re.compile(rf"^(?P<sample>.+?)(?:{_SEP}{_REP}(?P<rep>\d{{1,3}}))?$"),
}

# Xcalibur appends _YYYYMMDDhhmmss when a file of that name already exists
# (e.g. X_DMSO_1_20260508180610.raw). Ignored when reading the tail; the file keeps its name.
ACQ_STAMP = re.compile(r"[_-]20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[01]\d|2[0-3])[0-5]\d[0-5]\d$")


def strip_acq_stamp(stem: str) -> str:
    return ACQ_STAMP.sub("", stem)


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


# Date formats, tried in order. All must sit at token boundaries.
_DATE_PATTERNS = (
    (re.compile(r"(?<![0-9])(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?![0-9])"), "ymd"),  # 20260902, 2026-09-02
    (re.compile(r"(?<![0-9])(\d{2})[-_.]?(\d{2})[-_.]?(20\d{2})(?![0-9])"), "mdy"),  # 09022026, 09-02-2026
    (re.compile(r"(?<![0-9])(\d{2})(\d{2})(\d{2})(?![0-9])"), "mdy2"),               # 090226
)


def _mk_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_date(name_or_tokens, *, today: date | None = None) -> date | None:
    """Find the first plausible date in a folder name. None if absent.

    The six-digit mdy2 form only fires when 2000+yy falls within
    [today.year - 25, today.year + 1] — run IDs like 113056 (2056) mint no
    phantom dates. Keyword-only `today` keeps every caller unchanged.
    """
    today = today or date.today()
    name = " ".join(name_or_tokens) if isinstance(name_or_tokens, tuple) else name_or_tokens
    for pat, kind in _DATE_PATTERNS:
        for m in pat.finditer(name):
            a, b, c = (int(x) for x in m.groups())
            if kind == "ymd":
                d = _mk_date(a, b, c)
            elif kind == "mdy":
                d = _mk_date(c, a, b)
            else:
                y = 2000 + c
                if not today.year - 25 <= y <= today.year + 1:
                    continue
                d = _mk_date(y, a, b)
            if d:
                return d
    return None


def _method_hits(name: str, aliases: dict[str, list[str]]) -> set[str]:
    low_tokens = {t.lower() for t in tokens_of(name)}
    low_name = name.lower()

    def hits(match):
        return {m for m, als in aliases.items() if any(match(a.lower()) for a in als)}

    return hits(lambda a: a in low_tokens) or hits(lambda a: a in low_name)


def find_method(
    name: str,
    aliases: dict[str, list[str]] | None = None,
    fallback_names: list[str] | None = None,
) -> str:
    """Find exactly one method keyword in a folder name.

    Token match first; if nothing, substring match (handles glued names like
    THB10ISODTB). If the folder name has none, `fallback_names` (the raw file
    names) are searched — people often put isoDTB/TMT in the file names
    instead. Ambiguous (two methods) or none -> NamingError.
    """
    aliases = aliases or DEFAULT_METHOD_ALIASES
    found = _method_hits(name, aliases)
    if not found and fallback_names:
        for fn in fallback_names:
            found |= _method_hits(fn, aliases)
    if not found:
        raise NamingError(
            f"no method keyword found in {name!r}; include one of "
            f"{', '.join(aliases)} in the folder name"
        )
    if len(found) > 1:
        raise NamingError(f"ambiguous method in {name!r}: found {', '.join(sorted(found))}")
    return found.pop()


def find_user(name: str, users: dict[str, str], method_aliases: dict[str, list[str]] | None = None) -> str:
    """Find the user this folder belongs to.

    `users` maps lowercase alias -> canonical user dir name (build with
    build_user_lookup). Three passes, first one that hits wins:
      1. exact token match ("EJQ_isoDTB_x", multi-token "Taylor_Elements")
      2. initials glued to an ID: token = alias + digits ("IJD05", "EJQ123",
         "THB10" after stripping method keywords: "THB10ISODTB"). Longest
         alias wins so "IJD" beats "IJ" for "IJD05".
    Ambiguity within a pass -> NamingError.
    """
    toks = tuple(t.lower() for t in tokens_of(name))
    exact: set[str] = set()
    for alias, canonical in users.items():
        parts = tuple(t.lower() for t in tokens_of(alias))
        n = len(parts)
        if n and any(toks[i : i + n] == parts for i in range(len(toks) - n + 1)):
            exact.add(canonical)
    if len(exact) == 1:
        return exact.pop()
    if len(exact) > 1:
        raise NamingError(f"ambiguous user in {name!r}: matches {', '.join(sorted(exact))}")

    # pass 2: strip method keywords, then alias+digits prefix match
    kw = [a.lower() for als in (method_aliases or DEFAULT_METHOD_ALIASES).values() for a in als]
    stripped = []
    for t in toks:
        for k in sorted(kw, key=len, reverse=True):
            t = t.replace(k, "")
        stripped.append(t)
    prefix: dict[str, int] = {}  # canonical -> best alias length
    for alias, canonical in users.items():
        a = alias.lower()
        if len(a) < 2 or "_" in a or "-" in a:
            continue
        for t in stripped:
            if t.startswith(a) and t != a and t[len(a):].isdigit():
                prefix[canonical] = max(prefix.get(canonical, 0), len(a))
    if prefix:
        best = max(prefix.values())
        winners = sorted(c for c, n in prefix.items() if n == best)
        if len(winners) == 1:
            return winners[0]
        raise NamingError(f"ambiguous user in {name!r}: matches {', '.join(winners)}")

    raise NamingError(
        f"no known user in {name!r}; include your initials or folder name "
        f"(known: {', '.join(sorted(set(users.values())))})"
    )


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
    raw_names: list[str] | None = None,
) -> FolderName:
    if not name.strip():
        raise NamingError("empty folder name")
    method = find_method(name, method_aliases, fallback_names=raw_names)
    user = find_user(name, users, method_aliases)
    toks = tokens_of(name)
    return FolderName(
        original=name,
        safe=sanitize(name),
        method=method,
        user=user,
        date=find_date(name),
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
    m = _TAIL[method].match(strip_acq_stamp(safe_stem))
    if not m:  # only reachable for isoDTB (others accept a bare stem)
        raise NamingError(
            f"{filename!r}: isoDTB files must end in _<rep>_<fraction>.raw or _<rep>.raw"
        )
    rep = m.groupdict().get("rep")
    frac = m.groupdict().get("frac")
    # Regex caps digits at three, so only 0 can slip past this — but check
    # anyway so the bound is enforced by value, not regex shape alone.
    if rep is not None and not 1 <= int(rep) <= 999:
        raise NamingError(f"{filename!r}: replicate {rep} is out of range (expected 1–999)")
    if frac is not None and not 1 <= int(frac) <= 999:
        raise NamingError(f"{filename!r}: fraction {frac} is out of range (expected 1–999)")
    return RawName(
        filename=filename,
        safe_filename=safe_stem + RAW_SUFFIX,
        sample=m["sample"],
        rep=int(rep) if rep is not None else 1,
        fraction=int(frac) if frac is not None else None,
    )


def group_raws(filenames: list[str], method: str, allow_uneven: bool = False) -> RawSet:
    """Parse and validate all raws of one folder.

    * at least one file; every file parses
    * no duplicate (sample, rep, fraction)
    * within a sample: all fractionated or all single-shot, and every rep has
      the same fraction set (catches a half-finished copy) unless allow_uneven
    """
    if not filenames:
        raise NamingError("no .raw files found")
    parsed = [parse_raw_name(f, method) for f in sorted(filenames)]
    return group_from_parsed(parsed, method, allow_uneven)


def group_from_parsed(parsed: list[RawName], method: str, allow_uneven: bool = False) -> RawSet:
    """Validate already-parsed raws (see group_raws)."""
    if not parsed:
        raise NamingError("no .raw files found")
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
            if got != expected and not allow_uneven:
                raise NamingError(
                    f"sample {sample!r}: rep {rep} has fractions {got} but rep {first} has "
                    f"{expected} — incomplete copy?"
                )
        layout[sample] = {rep: sorted(reps[rep]) for rep in sorted(reps)}
    return RawSet(method=method, files=parsed, layout=layout)
