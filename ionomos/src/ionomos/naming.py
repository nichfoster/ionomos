"""
Naming rules. Pure functions, no I/O.

Executable form of docs/NAMING_CONVENTION.md. The philosophy is *flexible
folder names, strict raw-file tails*:

Folder name — anything, as long as somewhere in it we can find
    * a METHOD keyword   (isoDTB | TMT | DIA, matched via config aliases)
    * a USER token       (a known user dir name or a configured alias/initials)
    * optionally a DATE  (YYYYMMDD, MMDDYYYY or MMDDYY token by default; naming.date_formats)
  Spaces and odd punctuation are tolerated here and sanitised on move.

Raw file name — the END of the stem is reserved and its meaning depends on
the method:
    isoDTB   <sample>_<rep>_<fraction>.raw        e.g. X_1_1.raw, X_R2_F7.raw
             <sample>_<rep>.raw                   (unfractionated)
    TMT      <sample>[_TMT]_[F]<fraction>.raw     e.g. X_TMT_F1.raw, X_F3.raw, X_1.raw
             <sample>.raw                         (single fraction)
             every TMT run holds all replicates in channels -> bioreplicate 1
    DIA      <condition>_<biorep>.raw             e.g. DMSO_1.raw, Drug_R3.raw
             <prefix>_<code><biorep>.raw          e.g. X_D1.raw = DMSO rep 1, X_C2.raw = Compound rep 2
             (a 1-2 letter condition code glued to the number; codes from naming.condition_codes)
  Separators before the numbers may be '_' or '-'. Optional R/F prefixes.

Another lab's convention is config, not code (config.yaml `naming:`, D37): each method's file
rule is a template ("{sample}_{rep}[_{fraction}]", compile_template) or a named-group regex
(compile_pattern). The defaults above are those templates and compile to the same regexes as
before. Date formats and DIA condition codes are config too. A method that says `like: DIA` is DIA
for the whole pipeline, not only for its names: method_kind() is the one place that says so (D54).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

RAW_SUFFIX = ".raw"

# Replicate and fraction numbers run 1-999 (D30), whoever gives them: a file name, experiment.yaml, the naming
# window, the naming history or a queued job's manifest (D85). FragPipe and DIA-NN keep the replicate in a 32-bit
# integer: on 0.5.1 an Xcalibur time stamp (20260508180610) went in as a replicate and DIA-NN's matrix lost both runs.
NUMBER_MIN, NUMBER_MAX = 1, 999
NUMBER_WHY = ("replicate and fraction numbers run 1-999; a longer number (such as the time stamp Xcalibur adds to a "
              "re-acquired file) does not fit FragPipe's and DIA-NN's whole numbers, and DIA-NN drops that run")


def number_ok(value) -> bool:
    """True for a whole number 1-999 (an int, or digits); False for anything else."""
    if isinstance(value, bool):
        return False
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return False
    return NUMBER_MIN <= n <= NUMBER_MAX

# What a sanitised name may contain. FragPipe cannot handle spaces in paths.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_TOKEN_SPLIT = re.compile(r"[\s_\-.()\[\]{}+&,;]+")

_SEP = r"[_-]"
# optional prefixes people put before the numbers: R1, rep1, Rep_1, bio1, biorep1, n1 / F1, frac1, fraction1
_REP = r"(?:(?:[Rr](?:ep)?|[Bb]io(?:[Rr]ep)?|[Nn])[_-]?)?"
_FRAC = r"(?:(?:[Ff](?:rac(?:tion)?)?)[_-]?)?"

# The built-in file rules, as templates (see compile_template). They compile to exactly the
# regexes used before naming became configurable (tests/test_naming_config.py pins them):
#   isoDTB  sample, rep, frac — frac may be missing (unfractionated)
#   TMT     sample[, frac]; rep is always 1
#   DIA     condition, biorep (plus the short condition codes below)
# Digit runs are capped at three: a longer tail is a date or an instrument counter, never a
# replicate/fraction number (issue #15, D30).
DEFAULT_FILE_TEMPLATES: dict[str, str] = {
    "isoDTB": "{sample}_{rep}[_{fraction}]",
    "TMT": "{sample}[_TMT][_{fraction}]",
    "DIA": "{sample}[_{rep}]",
}

# DIA short form: a condition code glued to the replicate number, X_D1 / X_C2 / D3. Only 1-2 letter codes
# (or codes listed in naming.condition_codes) — "HCD33" is a collision energy, not condition HCD rep 33.
DEFAULT_CONDITION_CODES = {"D": "DMSO", "C": "Compound"}
_CODED = re.compile(r"^(?:(?P<prefix>.+?)(?P<sep>[_-]))?(?P<code>[A-Za-z]{1,8})(?P<rep>\d{1,3})$")


def expand_code(code: str, codes: dict[str, str] | None = None) -> str | None:
    """'D' -> 'DMSO' with the default codes; None if the code isn't a condition code."""
    codes = DEFAULT_CONDITION_CODES if codes is None else codes
    full = next((v for k, v in codes.items() if k.lower() == code.lower()), None)
    return full if full else (code if len(code) <= 2 else None)


# Xcalibur appends _YYYYMMDDhhmmss when a file of that name already exists
# (e.g. X_DMSO_1_20260508180610.raw). Ignored when reading the tail; the file keeps its name.
ACQ_STAMP = re.compile(r"[_-]20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[01]\d|2[0-3])[0-5]\d[0-5]\d$")


def strip_acq_stamp(stem: str) -> str:
    return ACQ_STAMP.sub("", stem)


# An instrument setting at the end of a name: FAIMS compensation voltage (CV-35, cv-55), collision energy
# (HCD33, NCE30), CID. Its number is part of the sample name, never a replicate or fraction: three runs
# at CV-35 / -45 / -55 are three settings of one sample, not replicates 35, 45, 55. Only 2-3 digits
# (settings are >= 10), after a separator and some text, so condition "CV" rep 1 (X_CV_1) still reads as that.
ACQ_SETTING = re.compile(r"(?<=.)(?<=[_-])(?P<key>CV|FAIMS|HCD|NCE|CID)(?P<sep>[_-]?)(?P<num>\d{2,3})$", re.I)
_DIGIT_MASK = str.maketrans("0123456789", "abcdefghij")


def mask_setting(stem: str, codes: dict[str, str] | None = None) -> tuple[str, tuple[str, str] | None]:
    """(stem with a trailing instrument setting hidden from the file rule, (mask, original) to restore it).
    A key configured as a condition code (naming.condition_codes) is left alone: the lab said what it means."""
    m = ACQ_SETTING.search(stem)
    if not m or any(k.lower() == m["key"].lower() for k in (codes or {})):
        return stem, None
    original = stem[m.start("key"):]
    mask = m["key"] + "~" + m["num"].translate(_DIGIT_MASK)  # no separator, no digits: nothing to read
    return stem[:m.start("key")] + mask, (mask, original)


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


# ------------------------------------------------------------- file rules --

# Template fields -> regex group. Several spellings, so a lab can write what it says.
_FIELDS: dict[str, str | None] = {
    "sample": "sample", "condition": "sample",
    "rep": "rep", "replicate": "rep", "biorep": "rep",
    "fraction": "frac", "frac": "frac",
    "any": None,  # text that is matched but ignored
}
_FIELD_REGEX: dict[str | None, str] = {
    "sample": "(?P<sample>.+?)",
    "rep": rf"{_REP}(?P<rep>\d{{1,3}})",
    "frac": rf"{_FRAC}(?P<frac>\d{{1,3}})",
    None: ".+?",
}
# Group names a power user's regex may use, and what they mean.
_PATTERN_GROUPS = {"sample": "sample", "condition": "sample", "rep": "rep", "replicate": "rep",
                   "biorep": "rep", "fraction": "frac", "frac": "frac"}
_TPL_TOKEN = re.compile(r"\{([^{}]*)\}|\[|\]|[_-]+|.", re.S)
_FIELD_HELP = "{sample}, {rep}, {fraction} or {any}"


def compile_template(template: str) -> re.Pattern[str]:
    """A readable file-name template -> the regex that reads it. Raises NamingError.

    {sample} (or {condition}) is the FragPipe experiment, {rep} the bioreplicate (1-3 digits after
    an optional R/rep/bio/n prefix), {fraction} the fraction (1-3 digits after an optional
    F/frac prefix), {any} text that is skipped. [ ... ] is optional. '_' and '-' each stand for
    either separator; letters match either case; a trailing '.raw' is ignored.
    Example: "{sample}_{rep}[_{fraction}]" (the isoDTB default).
    """
    if not isinstance(template, str) or not template.strip():
        raise NamingError("a file template can't be empty; e.g. {sample}_{rep}")
    t = template.strip()
    if t.lower().endswith(RAW_SUFFIX):
        t = t[: -len(RAW_SUFFIX)]
    out: list[str] = []
    lit = ""  # literal text since the last field, at the top level
    group: list[str] | None = None  # inside [ ... ]
    seen: dict[str, bool] = {}  # field -> sits inside an optional part
    prev_number = False  # the last token was {rep} or {fraction}
    for m in _TPL_TOKEN.finditer(t):
        tok = m.group(0)
        if m.group(1) is not None:
            name = m.group(1).strip().lower()
            if name not in _FIELDS:
                raise NamingError(f"unknown field {tok} in {template!r}; use {_FIELD_HELP}")
            fld = _FIELDS[name]
            if fld is not None:
                if fld in seen:
                    raise NamingError(f"{template!r} has the {fld} field twice")
                seen[fld] = group is not None
            number = fld in ("rep", "frac")
            if number and prev_number:
                raise NamingError(f"{template!r}: put a letter or separator between two numbers "
                                  "({rep}{fraction} can't be told apart)")
            rx = _FIELD_REGEX[fld]
            if group is not None:
                group.append(rx)
            elif number:  # a number and the text before it are one unit, as in the built-in regexes
                out.append(f"(?:{lit}{rx})")
                lit = ""
            else:
                out.append(lit + rx)
                lit = ""
            prev_number = number
            continue
        prev_number = False
        if tok == "[":
            if group is not None:
                raise NamingError(f"{template!r}: optional parts [ ] can't be nested")
            out.append(lit)
            lit, group = "", []
            continue
        if tok == "]":
            if group is None:
                raise NamingError(f"{template!r}: ']' without a matching '['")
            if not group:
                raise NamingError(f"{template!r}: empty optional part []")
            out.append(f"(?:{''.join(group)})?")
            group = None
            continue
        if tok[0] in "_-":
            rx = _SEP  # a run of separators: sanitize() collapses them to one
        elif tok.isascii() and tok.isalpha():
            rx = f"[{tok.upper()}{tok.lower()}]"
        elif tok.isascii() and tok.isdigit():
            rx = tok
        elif tok == ".":
            rx = r"\."
        elif tok in "{}":
            raise NamingError(f"{template!r}: unmatched {tok!r}; fields look like {_FIELD_HELP}")
        else:
            raise NamingError(f"{template!r}: {tok!r} can't be in a file name after clean-up; use letters, "
                              f"digits, '_', '-', '.', {_FIELD_HELP} and [optional parts]")
        if group is not None:
            group.append(rx)
        else:
            lit += rx
    if group is not None:
        raise NamingError(f"{template!r}: '[' without a matching ']'")
    out.append(lit)
    if "sample" not in seen or seen["sample"]:
        raise NamingError(f"{template!r} needs {{sample}} once, outside [ ] (it names the experiment)")
    return re.compile("^" + "".join(out) + "$")


def compile_pattern(pattern: str) -> tuple[re.Pattern[str], dict[str, str]]:
    """A power user's regex -> (compiled, {group name: sample|rep|frac}). Raises NamingError.

    Needs a named group `sample` (or `condition`); `rep` and `fraction` are optional. The whole
    cleaned-up file stem must match. Numbers are checked by value when read (1-999, D30).
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise NamingError("a file pattern can't be empty")
    flags = re.match(r"^\(\?[aiLmsux]+\)", pattern)  # (?i) etc. must stay in front
    lead, body = (flags.group(0), pattern[flags.end():]) if flags else ("", pattern)
    try:
        rx = re.compile(f"{lead}^(?:{body})$")
    except re.error as exc:
        raise NamingError(f"pattern '{pattern}' is not a valid regular expression: {exc}") from None
    groups: dict[str, str] = {}
    for name in rx.groupindex:
        canon = _PATTERN_GROUPS.get(name.lower())
        if canon is None:
            raise NamingError(f"pattern '{pattern}': unknown group (?P<{name}>...); use sample, rep, fraction")
        if canon in groups.values():
            raise NamingError(f"pattern '{pattern}' has two groups for {canon}")
        groups[name] = canon
    if "sample" not in groups.values():
        raise NamingError(f"pattern '{pattern}' needs a (?P<sample>...) group (it names the experiment)")
    return rx, groups


@dataclass(frozen=True)
class FileRule:
    """How one method's raw file names are read."""

    regex: re.Pattern[str]
    source: str  # the template or regex as written
    must: str  # what the error says when a name doesn't fit ("must end in ...")
    codes: bool = False  # DIA short forms: X_D1 = condition code D, rep 1 (naming.condition_codes)
    groups: tuple[tuple[str, str], ...] = (("sample", "sample"), ("rep", "rep"), ("frac", "frac"))
    like: str = ""  # the built-in method named by like: (see method_kind); "" = none

    def read(self, m: re.Match[str]) -> tuple[str | None, str | None, str | None]:
        """(sample, rep, frac) from a match; None where the rule has no such part or it was absent."""
        got: dict[str, str | None] = {"sample": None, "rep": None, "frac": None}
        found = m.groupdict()
        for name, canon in self.groups:
            if name in found:
                got[canon] = found[name] or None  # an empty capture counts as absent
        return got["sample"], got["rep"], got["frac"]


def file_rule(method: str, *, like: str | None = None, files: str | None = None,
              pattern: str | None = None, codes: bool | None = None) -> FileRule:
    """Build a method's file rule. Raises NamingError.

    like: start from a built-in method (isoDTB | TMT | DIA; default: the method itself).
    files: a template, or pattern: a regex, replaces its shape. codes: DIA short forms on/off
    (default: on for DIA-like methods)."""
    base = like or method
    if files is not None and pattern is not None:
        raise NamingError("give files: (a template) or pattern: (a regex), not both")
    if base not in DEFAULT_FILE_TEMPLATES and files is None and pattern is None:
        raise NamingError(f"no file rule for {method!r}: give files: (a template like {{sample}}_{{rep}}), "
                          f"pattern: (a regex) or like: {' | '.join(DEFAULT_FILE_TEMPLATES)}")
    use_codes = (base == "DIA") if codes is None else bool(codes)
    if pattern is not None:
        rx, groups = compile_pattern(pattern)
        return FileRule(rx, pattern, f"must match the pattern '{pattern}'", use_codes, tuple(groups.items()),
                        like=like or "")
    template = files if files is not None else DEFAULT_FILE_TEMPLATES[base]
    rx = compile_template(template)
    if template.strip() == DEFAULT_FILE_TEMPLATES["isoDTB"]:
        must = "must end in _<rep>_<fraction>.raw or _<rep>.raw"  # the message users already know
    else:
        t = template.strip()
        must = f"must look like {t[:-len(RAW_SUFFIX)] if t.lower().endswith(RAW_SUFFIX) else t}.raw"
    return FileRule(rx, template, must, use_codes, like=like or "")


DEFAULT_FILE_RULES: dict[str, FileRule] = {m: file_rule(m) for m in DEFAULT_FILE_TEMPLATES}

# ----------------------------------------------------------- method kinds --

# What a search engine the watcher runs itself produces, whatever the lab calls the method
# (config methods.<name>.engine): the kind, and the table the analysis reads (downstream/engines.py).
ENGINE_KINDS: dict[str, str] = {"diann": "DIA", "maxquant": "LFQ", "sage": "LFQ"}
ENGINE_TABLES: dict[str, str] = {"maxquant": "MaxQuant", "sage": "Sage"}


def method_kind(method: str, rules: dict[str, FileRule] | None = None, engine: str | None = None) -> str:
    """The method a method key behaves as, everywhere the pipeline branches on a method (D54):
    isoDTB | TMT | DIA | LFQ, or the key itself for a method of the lab's own (generic label-free).

    The engine decides first (DIA-NN makes DIA results, MaxQuant and Sage label-free ones), then
    the rule's like:, then the key. rules: method -> FileRule (config naming.methods); None = built-in."""
    by_engine = ENGINE_KINDS.get(str(engine or "").lower())
    if by_engine:
        return by_engine
    rule = (DEFAULT_FILE_RULES if rules is None else rules).get(method)
    return rule.like if rule is not None and rule.like else method


def analysis_method(method: str, rules: dict[str, FileRule] | None = None, engine: str | None = None) -> str:
    """What the downstream analysis is asked to read for a method: MaxQuant's or Sage's own table when
    that engine ran the search, else the method's kind."""
    return ENGINE_TABLES.get(str(engine or "").lower()) or method_kind(method, rules, engine)


def check_method_aliases(aliases: dict[str, list[str]]) -> None:
    """Every method keyword belongs to one method and isn't blank. Raises NamingError."""
    owner: dict[str, str] = {}
    for method, als in aliases.items():
        for a in als:
            k = str(a).strip().lower()
            if not k:
                raise NamingError(f"method {method!r} has a blank keyword; every alias needs a letter or digit")
            if owner.get(k, method) != method:
                raise NamingError(f"keyword {a!r} is listed for both {owner[k]} and {method}; a folder name "
                                  "with it would be ambiguous. Keep each keyword under one method")
            owner[k] = method


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


# Date formats a folder name may use (config: naming.date_formats), tried in the listed order.
# All must sit at digit boundaries. Four-digit-year forms may have - _ . between the parts.
_YEAR_FIRST = re.compile(r"(?<![0-9])(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?![0-9])")  # 20260902, 2026-09-02
_YEAR_LAST = re.compile(r"(?<![0-9])(\d{2})[-_.]?(\d{2})[-_.]?(20\d{2})(?![0-9])")  # 09022026, 09-02-2026
_SIX = re.compile(r"(?<![0-9])(\d{2})(\d{2})(\d{2})(?![0-9])")  # 090226
DATE_FORMATS: dict[str, tuple[re.Pattern[str], str]] = {
    "YYYYMMDD": (_YEAR_FIRST, "ymd"),
    "MMDDYYYY": (_YEAR_LAST, "mdy"),
    "DDMMYYYY": (_YEAR_LAST, "dmy"),
    "MMDDYY": (_SIX, "mdy2"),
    "DDMMYY": (_SIX, "dmy2"),
    "YYMMDD": (_SIX, "ymd2"),
}
DEFAULT_DATE_FORMATS: tuple[str, ...] = ("YYYYMMDD", "MMDDYYYY", "MMDDYY")


def _mk_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def check_date_formats(formats) -> tuple[str, ...]:
    """Validate a naming.date_formats list. Raises NamingError."""
    if not isinstance(formats, (list, tuple)):
        raise NamingError(f"date formats must be a list, e.g. [{', '.join(DEFAULT_DATE_FORMATS)}]")
    out: list[str] = []
    for f in formats:
        key = str(f).strip().upper()
        if key not in DATE_FORMATS:
            raise NamingError(f"unknown date format {f!r}; use {', '.join(DATE_FORMATS)}")
        if key in out:
            raise NamingError(f"date format {key} is listed twice")
        out.append(key)
    return tuple(out)


def find_date(name_or_tokens, *, today: date | None = None,
              formats: tuple[str, ...] | list[str] | None = None) -> date | None:
    """Find the first plausible date in a folder name. None if absent.

    formats: names from DATE_FORMATS, tried in order (default DEFAULT_DATE_FORMATS).
    The six-digit forms only fire when 2000+yy falls within [today.year - 25, today.year + 1]
    — run IDs like 113056 (2056) mint no phantom dates (D30). Keyword-only arguments keep every
    caller unchanged.
    """
    today = today or date.today()
    name = " ".join(name_or_tokens) if isinstance(name_or_tokens, tuple) else name_or_tokens
    for fmt in (DEFAULT_DATE_FORMATS if formats is None else formats):
        pat, kind = DATE_FORMATS[fmt]
        for m in pat.finditer(name):
            a, b, c = (int(x) for x in m.groups())
            if kind == "ymd":
                d = _mk_date(a, b, c)
            elif kind == "mdy":
                d = _mk_date(c, a, b)
            elif kind == "dmy":
                d = _mk_date(c, b, a)
            else:
                y = 2000 + (a if kind == "ymd2" else c)
                if not today.year - 25 <= y <= today.year + 1:
                    continue
                d = (_mk_date(y, a, b) if kind == "mdy2" else _mk_date(y, b, a) if kind == "dmy2"
                     else _mk_date(y, b, c))
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
    *,
    date_formats: tuple[str, ...] | None = None,
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
        date=find_date(name, formats=date_formats),
        tokens=toks,
    )


# --------------------------------------------------------------------- raws --


def parse_raw_name(filename: str, method: str, codes: dict[str, str] | None = None,
                   rules: dict[str, FileRule] | None = None) -> RawName:
    """codes: DIA condition codes (X_D1 -> DMSO rep 1); None = DEFAULT_CONDITION_CODES.
    rules: method -> FileRule (config naming.methods); None = DEFAULT_FILE_RULES."""
    if not filename.lower().endswith(RAW_SUFFIX):
        raise NamingError(f"{filename!r} is not a {RAW_SUFFIX} file")
    rule = (DEFAULT_FILE_RULES if rules is None else rules).get(method)
    if rule is None:
        raise NamingError(f"unknown method {method!r}: its file names have no rule; add naming.methods.{method} "
                          "to config.yaml (e.g. files: '{sample}_{rep}', or like: DIA)")
    stem = filename[: -len(RAW_SUFFIX)]
    safe_stem = sanitize(stem)
    read_stem, setting = mask_setting(strip_acq_stamp(safe_stem), codes)
    m = rule.regex.match(read_stem)
    if not m:  # built-in rules: only reachable for isoDTB (the others accept a bare stem)
        if setting:
            raise NamingError(f"{filename!r}: {setting[1]} looks like an instrument setting (FAIMS CV / collision "
                              f"energy), not a replicate; {method} files {rule.must}, e.g. "
                              f"{stem}_1.raw")
        raise NamingError(f"{filename!r}: {method} files {rule.must}")
    sample, rep, frac = rule.read(m)
    if setting and sample:
        sample = sample.replace(setting[0], setting[1])
    if not sample:
        raise NamingError(f"{filename!r}: no sample name left once the {method} rule is applied")
    for what, v in (("replicate", rep), ("fraction", frac)):
        if v is not None and not v.isdigit():  # only a hand-written pattern can capture non-digits
            raise NamingError(f"{filename!r}: {what} {v!r} is not a number")
    if rule.codes and rep is None and not setting:  # "CV35" is a setting, not condition CV rep 35
        c = _CODED.match(strip_acq_stamp(safe_stem))
        full = expand_code(c["code"], codes) if c else None
        if full:
            sample = f"{c['prefix']}{c['sep']}{full}" if c["prefix"] else full
            rep = c["rep"]
    # Regex caps digits at three, so only 0 can slip past this — but check
    # anyway so the bound is enforced by value, not regex shape alone.
    if rep is not None and not number_ok(rep):
        raise NamingError(f"{filename!r}: replicate {rep} is out of range (expected 1–999)")
    if frac is not None and not number_ok(frac):
        raise NamingError(f"{filename!r}: fraction {frac} is out of range (expected 1–999)")
    return RawName(
        filename=filename,
        safe_filename=safe_stem + RAW_SUFFIX,
        sample=sample,
        rep=int(rep) if rep is not None else 1,
        fraction=int(frac) if frac is not None else None,
    )


def group_raws(filenames: list[str], method: str, allow_uneven: bool = False,
               codes: dict[str, str] | None = None, rules: dict[str, FileRule] | None = None) -> RawSet:
    """Parse and validate all raws of one folder.

    * at least one file; every file parses
    * no duplicate (sample, rep, fraction)
    * within a sample: all fractionated or all single-shot, and every rep has
      the same fraction set (catches a half-finished copy) unless allow_uneven
    """
    if not filenames:
        raise NamingError("no .raw files found")
    parsed = [parse_raw_name(f, method, codes, rules) for f in sorted(filenames)]
    return group_from_parsed(parsed, method, allow_uneven)


def group_from_parsed(parsed: list[RawName], method: str, allow_uneven: bool = False) -> RawSet:
    """Validate already-parsed raws (see group_raws)."""
    if not parsed:
        raise NamingError("no .raw files found")
    seen: set[tuple] = set()
    fr: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    single: dict[str, set[int]] = defaultdict(set)
    for r in parsed:
        # parse_raw_name bounds the numbers it reads; overrides and learned names come here too (D85)
        if not number_ok(r.rep):
            raise NamingError(f"{r.filename!r}: replicate {r.rep} is out of range (expected 1–999): {NUMBER_WHY}")
        if r.fraction is not None and not number_ok(r.fraction):
            raise NamingError(f"{r.filename!r}: fraction {r.fraction} is out of range (expected 1–999): {NUMBER_WHY}")
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
