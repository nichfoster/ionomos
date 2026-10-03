"""
The troubleshooting / validation bundle (D63): one zip a person copies off the PC, and its key file.

    plan = collect(config_path, targets=["12"], opts=Options(level="validate"))   # reads sizes only
    res = create(config_path, ["12"], Options(level="validate"))                  # the zip + the key file
    inspect(zip) / unpack(zip, folder) / translate(key_file, text)                # the reader's side

Ionomos sends nothing: the zip is written to the Desktop (or the log folder) and a person decides
where it goes. Two levels:

    diagnose   system facts, config (secrets redacted), logs, crash files, attention items, and per job
               its status file, DONE / FAILED notes, experiment.yaml, the run folder (workflow, manifest,
               console logs, the run fingerprint) and results/analysis.json + analysis_error.txt
    validate   also the search engine's result tables the analysis reads, and Ionomos' results/ folder,
               so `ionomos analyze` on the unpacked bundle repeats the analysis

Never raw / mzML / .d files, FASTA files or spectral libraries (a FASTA is described: name, size,
entry count, hash). A bundle only reads the experiment folders; it writes the zip and the key file,
and never over an existing file.

Anonymisation (on by default) replaces user names, the PC name, experiment / folder / raw-file /
sample names, e-mail and IP addresses with pseudonyms that are the same in every file of the bundle,
so tables, logs, ionomos.json and the report still line up and the analysis still runs. Names are
replaced token by token (`Drug_10uM_1` -> `condA_10uM_1`): numbers, doses, times, replicate numbers
and the words the analysis recognises (DMSO, pool, the method names) are kept. Protein and gene
identifiers and every number are kept. The key file (pseudonym -> original) is written next to the
zip, never inside it. After writing, the finished zip is searched again for every original; a hit
fails the bundle.
"""
from __future__ import annotations

import datetime
import fnmatch
import hashlib
import html
import itertools
import json
import os
import re
import string
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ionomos import names

LEVELS = ("diagnose", "validate")
FORMAT = 1
LOG_TAIL = 3_000_000       # bytes kept from the end of a log
SMALL_FILE = 5_000_000     # a status / settings file larger than this is not one
JOBS_DIR = "jobs"
EXTRAS_DIR = "_bundle"     # where unpack puts everything that is not an experiment folder
LAB_DIR = "_lab"           # the stand-in lab folders unpack's config.yaml points at

# never in a bundle, whatever folder they sit in
NEVER_SUFFIXES = (".raw", ".mzml", ".mzxml", ".mgf", ".wiff", ".wiff2", ".tdf", ".tdf_bin", ".dia", ".mzbin",
                  ".fasta", ".fas", ".fa", ".faa", ".speclib", ".dlib", ".elib", ".blib", ".sptxt", ".msp",
                  ".parquet", ".pepxml", ".pin", ".pkl", ".bin", ".db")
NEVER_WORDS = ("speclib", "library", "lib.tsv", ".pep.xml")
NEVER_SAID = "raw / mzML / .d files, FASTA files, spectral libraries"

# the engines' result tables the analysis reads: (pattern, what)
QUANT_TABLES = (
    ("combined_*.tsv", "FragPipe combined table", True), ("abundance_*.tsv", "TMT-Integrator abundance table", True),
    ("*pg_matrix.tsv", "DIA-NN protein matrix", True), ("*stats.tsv", "DIA-NN run statistics", False),
    ("proteinGroups.txt", "MaxQuant protein groups", True), ("summary.txt", "MaxQuant summary", False),
    ("lfq.tsv", "Sage LFQ table", True), ("tmt.tsv", "Sage TMT table", True),
    ("results.json", "Sage search record", False), ("fragpipe.workflow", "FragPipe's copy of the workflow", False),
    ("*.fp-manifest", "FragPipe's copy of the manifest", False),
)
PSM_TABLES = (("psm.tsv", "FragPipe PSM table"), ("results.sage.tsv", "Sage PSM table"))  # row-sampled above the cap
ENGINE_DIRS = ("fragpipe", "diann", "maxquant", "sage")
RESULTS = "results"


class BundleError(Exception):
    """The bundle could not be made (or read); the message is for a person."""


class BundleLeak(BundleError):
    """An original name was still in the finished zip. No zip is left behind."""

    def __init__(self, leaks: list[tuple[str, str, int]]):
        self.leaks = leaks
        shown = "; ".join(f"{o!r} x{n} in {arc}" for arc, o, n in leaks[:8])
        more = f" (+{len(leaks) - 8} more)" if len(leaks) > 8 else ""
        super().__init__(f"LEAK CHECK FAILED: {len(leaks)} name(s) were still in the bundle after anonymising, so no "
                         f"bundle was written: {shown}{more}")


@dataclass
class Options:
    level: str = "diagnose"
    anonymise: bool = True
    keep_conditions: bool = False   # keep every condition / sample word, not only the control / role words
    max_mb: float = 2000            # the whole bundle, before compression
    psm_mb: float = 25              # one PSM-level table; a larger one is row-sampled
    note: str = ""

    def __post_init__(self):
        if self.level not in LEVELS:
            raise BundleError(f"level must be one of {', '.join(LEVELS)}, not {self.level!r}")


# ---------------------------------------------------------------- paths ----


def _long(p: Path | str) -> str:
    """A path Windows will open even past 260 characters (the \\\\?\\ form); unchanged elsewhere."""
    s = str(p)
    if os.name != "nt" or s.startswith("\\\\?\\") or len(s) < 240 or not os.path.isabs(s):
        return s
    s = os.path.normpath(s)
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


def _size(p: Path) -> int:
    try:
        return os.stat(_long(p)).st_size
    except OSError:
        return 0


def unique(path: Path) -> Path:
    """`path`, or `name-2.ext`, `name-3.ext` ... : a bundle never replaces an existing file."""
    path = Path(path)
    if not os.path.lexists(_long(path)):
        return path
    for n in itertools.count(2):
        cand = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not os.path.lexists(_long(cand)):
            return cand
    raise AssertionError("unreachable")


def _writable(d: Path) -> bool:
    try:
        probe = d / f".ionomos-write-test-{os.getpid()}"
        with open(_long(probe), "x", encoding="utf-8"):
            pass
        os.remove(_long(probe))
        return True
    except OSError:
        return False


def output_dir(config_path: Path | None = None) -> Path:
    """Where a bundle goes: the Desktop (also a OneDrive-redirected one), else the log folder, else home."""
    from ionomos import service

    cands = [service.desktop_dir()]
    if config_path is not None:
        log_dir = service._raw_paths(config_path).get("log_dir")
        if log_dir is not None:
            cands.append(Path(log_dir))
    cands.append(Path.home())
    for d in cands:
        if d.is_dir() and _writable(d):
            return d
    raise BundleError("no folder to save the bundle in (the Desktop, the log folder and the home folder cannot be "
                      "written); give one with --out")


def _sniff(p: Path) -> str | None:
    """'utf-8' / 'utf-16' for a text file, None for a binary one (decided on its first 8 kB)."""
    try:
        with open(_long(p), "rb") as f:
            head = f.read(8192)
    except OSError:
        return None
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    return None if b"\0" in head else "utf-8"


# ------------------------------------------------------------ anonymiser ----

_TOKEN = re.compile(r"[^\W_]+")
_CHUNK = re.compile(r"[\w\-]*[^\W\d_][\w\-]*")  # a run of name characters holding at least one letter
_CHUNK_ONLY = re.compile(r"[\w\-]+")
_SEP = re.compile(r"([_\-]+)")
_NUMERIC = re.compile(r"\d+[^\W\d_]{0,5}")       # 027, 1uM, 3h, 30min, 9plex, 127N
_REPLIKE = re.compile(r"(?:[a-z]|fr|frac|fraction|rep|replicate|br|tr|plex|set|batch|run|day|wk|hr|tmt)\d{1,3}")
_EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\w|\.\d)")
_IPV6 = re.compile(r"(?<![\w:.])(?:(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}"
                   r"|(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})*)?::(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})*)?)(?![\w:.])", re.I)
_NAME_PART = r"[^\\/\s\"'<>|:*?]+"
_HOME = re.compile(r"(?i)((?:[a-z]:[\\/]+Users|/Users|/home)[\\/]+)"
                   rf"({_NAME_PART}(?: {_NAME_PART}){{0,3}}(?=[\\/])|{_NAME_PART})")
_NOT_PEOPLE = {"public", "default", "default user", "all users", "shared"}
# Names the job list no longer knows (a folder rejected or removed long ago, still in an old log line) are
# recognised by their shape: a folder that starts with a date, a raw file by its extension.
_DATED = re.compile(r"(?<![\w\-])(?:19|20)\d{6}[_\-](?=[\w\-]*[^\W\d_])[\w\-]+")
_RAWFILE = re.compile(r"([\w\-]+)\.(?:raw|mzml|wiff)(?![\w])", re.IGNORECASE)
EMAIL_DOMAIN = "example.invalid"

# Words of a name that are kept: what the analysis reads meaning from, and words so common in Ionomos' own
# files that replacing them would break the files (or say nothing about the lab).
_ROLE_WORDS = ("pool", "pooled", "bridge", "ref", "reference", "norm", "irs", "blank", "wash", "qc", "std", "standard",
               "hela", "k562", "light", "heavy", "compound", "cmpd", "drug", "treated", "treatment", "input")
_STOP_WORDS = (
    "sample", "samples", "test", "tests", "result", "results", "report", "protein", "proteins", "peptide", "peptides",
    "gene", "genes", "site", "sites", "intensity", "ratio", "data", "file", "files", "raw", "runs", "note", "notes",
    "name", "names", "user", "users", "method", "methods", "experiment", "experiments", "fraction", "fractions",
    "channel", "channels", "analysis", "fragpipe", "ionomos", "labwatch", "diann", "maxquant", "sage", "msfragger",
    "philosopher", "ionquant", "calibrated", "uncalibrated", "redo", "rerun", "final", "copy", "human", "mouse",
    "yeast", "lysate", "cell", "cells", "time", "dose", "conc", "inbox", "logs", "config", "workflow", "workflows",
    "fasta", "mean", "median", "log2", "true", "false", "none", "null", "unsorted", "isodtb", "dda", "lfq", "silac",
    "enrichment", "volcano", "differential", "matrix", "processed", "plan", "status", "ms1", "ms2", "ms3", "log10",
    "sha256", "md5", "utf8", "x64", "x86", "win32", "win64", "h2o",
    # account and folder names that say nothing and are ordinary words in every file
    "home", "root", "admin", "administrator", "owner", "guest", "lab", "proteomics", "desktop", "documents",
)


class Anonymiser:
    """Pseudonyms for the lab's names, the same for the same original everywhere in one bundle.

    Register names with add_*(), then freeze(); text() rewrites a string and find() lists the originals
    still in one. Home-folder names, e-mail and IP addresses are found by pattern while rewriting."""

    def __init__(self, kept=(), keep_conditions: bool = False):
        from dataclasses import fields

        from ionomos.downstream.analysis import DEFAULT_CONTROL_KEYWORDS, Settings

        # the analysis' own setting names are keys of experiment.yaml: a name must never rewrite one
        words = [*DEFAULT_CONTROL_KEYWORDS, *_STOP_WORDS, *_ROLE_WORDS, *(f.name for f in fields(Settings)), *kept]
        self.kept: set[str] = set()
        for w in words:
            self.kept.update(t.casefold() for t in _TOKEN.findall(str(w)))
        self.keep_conditions = keep_conditions
        self._users: dict[str, list[str]] = {}
        self._pcs: list[str] = []
        self._folders: list[str] = []
        self._samples: list[str] = []
        self._other: list[str] = []
        self._dirs: list[str] = []
        self.tok: dict[str, str] = {}             # casefolded original token -> pseudonym
        self.safe: set[str] = set()               # tokens replaced wherever they stand alone
        self.spans: dict[tuple, str | None] = {}  # token run of a whole name -> its pseudonym (None: token by token)
        self._first: set[str] = set()
        self._hot: set[str] = set()
        self._met: set[str] = set()
        self._max_span = 0
        self._odd: dict[str, tuple[str | None, str]] = {}  # names with spaces, dots ...: casefold -> (pseudonym, original)
        self._odd_rx: re.Pattern | None = None
        self._odd_repl: dict[str, str] = {}
        self.key: dict[str, dict[str, str]] = {g: {} for g in ("users", "computers", "experiments", "folders",
                                                             "names", "emails", "addresses")}
        self.kept_seen: set[str] = set()
        self.frozen = False
        self.grew = False  # a name was first met while rewriting: files written before it must be redone
        self._taken: set[str] = set()

    # -- registering ---------------------------------------------------------

    @staticmethod
    def _clean(name) -> str:
        return str(name or "").strip()

    def add_user(self, name, aliases=()) -> None:
        name = self._clean(name)
        if not name:
            return
        if self.frozen:
            self._assign_user(name)
            return
        mine = self._users.setdefault(name, [])
        for a in aliases or ():
            a = self._clean(a)
            if a and a not in mine and a != name:
                mine.append(a)

    def add_pc(self, name) -> None:
        name = self._clean(name)
        if name and name not in self._pcs:
            self._pcs.append(name)

    def add_folder(self, name) -> None:
        """An experiment / inbox folder name: replaced as a whole (exp001)."""
        name = self._clean(name)
        if name and name not in self._folders:
            self._folders.append(name)

    def add_sample(self, name) -> None:
        """A raw-file stem, sample, condition or FragPipe experiment name: replaced token by token."""
        name = self._clean(name)
        if name and name not in self._samples:
            self._samples.append(name)

    def add_name(self, name) -> None:
        """Any other name from the lab (a file a user dropped, an inbox item): token by token."""
        name = self._clean(name)
        if name and name not in self._other:
            self._other.append(name)

    def add_dir(self, path) -> None:
        """A folder path from outside the lab's own folders (where a foreign table's raw files were): replaced
        as one piece (folder01), since nothing is known about its parts."""
        path = self._clean(path).rstrip("\\/")
        if len(path) > 3 and path not in self._dirs:
            self._dirs.append(path)

    # -- assigning -----------------------------------------------------------

    @staticmethod
    def _distinct(cf: str) -> bool:
        """A fragment that is replaced wherever it stands: letters and digits together (KL6159A, JQ1). A plain
        word (pulldown) is replaced only inside the names it came from: alone it is too often just a word."""
        return len(cf) >= 3 and any(c.isdigit() for c in cf) and any(c.isalpha() for c in cf)

    def _keep(self, cf: str) -> bool:
        return cf in self.kept or len(cf) < 2 or bool(_NUMERIC.fullmatch(cf) or _REPLIKE.fullmatch(cf))

    def _free(self, cand: str) -> bool:
        cf = cand.casefold()
        return cf not in self._taken and cf not in self.tok and cf not in self.kept

    def _number(self, prefix: str, width: int, start: int = 1) -> str:
        for n in itertools.count(start):
            cand = f"{prefix}{n:0{width}d}"
            if self._free(cand):
                self._taken.add(cand.casefold())
                return cand
        raise AssertionError("unreachable")

    def _whole(self, original: str, pseudonym: str | None) -> None:
        """Make `original` findable as a whole: one token, a run of tokens, or (spaces, dots ...) a pattern."""
        toks = [t.casefold() for t in _TOKEN.findall(original)]
        if not toks:
            return
        if not _CHUNK_ONLY.fullmatch(original):
            self._odd.setdefault(original.casefold(), (pseudonym, original))
            self._odd_rx = None
        elif len(toks) == 1:
            if pseudonym is not None:
                self.tok[toks[0]] = pseudonym
            if toks[0] in self.tok:
                self.safe.add(toks[0])
        elif pseudonym is not None or any(t in self.tok and t not in self.safe for t in toks):
            self.spans.setdefault(tuple(toks), pseudonym)
            if pseudonym is None:  # a cut-off name (a prefix) still holds its short tokens
                for n in range(2, len(toks)):
                    if any(t in self.tok and t not in self.safe for t in toks[:n]):
                        self.spans.setdefault(tuple(toks[:n]), None)
        for variant in (json.dumps(original)[1:-1], html.escape(original)):  # as JSON / HTML write it
            if variant != original:
                self._odd.setdefault(variant.casefold(), (pseudonym, original))
                self._odd_rx = None

    def _assign_user(self, name: str, pseudonym: str | None = None) -> str:
        toks = [t.casefold() for t in _TOKEN.findall(name)]
        if not toks:
            return name
        if len(toks) == 1 and toks[0] in self.tok:
            self.safe.add(toks[0])
            return self.tok[toks[0]]
        if len(toks) == 1 and self._keep(toks[0]):  # a user folder called "test" or "2024": not a person
            self.kept_seen.add(toks[0])
            return name
        known = self.spans.get(tuple(toks)) if len(toks) > 1 else None
        if known is None and name.casefold() in self._odd:
            known = self._odd[name.casefold()][0]
        if known:
            return known
        pseudonym = pseudonym or self._number("user", 2)
        self._taken.add(pseudonym.casefold())
        self._whole(name, pseudonym)
        self.key["users"][pseudonym] = name
        if self.frozen:
            self.grew = True
            self._index()
        return pseudonym

    def _met_before(self, name: str) -> bool:
        cf = name.casefold()
        if cf in self._met:
            return True
        if len(self._met) < 200_000:
            self._met.add(cf)
        return False

    def _meet(self, name: str) -> None:
        """A name seen while rewriting that nothing registered (recognised by its shape): its words become
        names and the whole a known name. Word by word, never as one piece: a raw file's name may be a
        registered sample name with a suffix, and the two must stay the same words."""
        if self._met_before(name):
            return
        toks = [t.casefold() for t in _TOKEN.findall(name)]
        if all(self._keep(t) or t in self.safe for t in toks):
            return
        size = (len(self.tok), len(self.spans), len(self.safe))
        self._assign_tokens(name)
        self._whole(name, None)
        if size != (len(self.tok), len(self.spans), len(self.safe)):
            self.grew = True
            self._index()

    def _labels(self, n: int) -> Iterator[str]:
        width = 1 if n <= 26 else 2 if n <= 676 else 3
        for t in itertools.product(string.ascii_uppercase, repeat=width):
            yield "".join(t)

    def _assign_conditions(self) -> None:
        """Pseudonyms for the tokens of sample names that keep the names' byte order, kept words included:
        Ionomos' Perseus imputation draws its random numbers sample by sample in that order."""
        first: dict[str, str] = {}
        fixed: dict[str, str] = {}
        for name in self._samples:
            for t in _TOKEN.findall(name):
                cf = t.casefold()
                if cf in self.tok:
                    continue
                if self._keep(cf) or self.keep_conditions:
                    fixed.setdefault(cf, t)
                    if not self._keep(cf):
                        self.kept.add(cf)
                    if cf in self.kept:
                        self.kept_seen.add(cf)
                else:
                    first.setdefault(cf, t)

        def order(s: str) -> bytes:
            return (s + "_").encode("utf-8")

        universe = sorted({**fixed, **first}.items(), key=lambda kv: order(kv[1]))
        labels = self._labels(len(first) + 8)
        families = ["cond", "Cond", "COND", *(c + "cond" for c in string.digits + string.ascii_uppercase
                                                + string.ascii_lowercase)]
        prev: bytes | None = None
        for i, (cf, t) in enumerate(universe):
            if cf in fixed:
                prev = order(t)
                continue
            nxt = next((order(t2) for cf2, t2 in universe[i + 1:] if cf2 in fixed), None)
            label = next((lb for lb in labels if self._free("cond" + lb)), None) or self._number("X", 3)
            chosen = "cond" + label
            for fam in families:
                k = order(fam + label)
                if (prev is None or prev < k) and (nxt is None or k < nxt):
                    chosen = fam + label
                    break
            self._taken.add(chosen.casefold())
            self.tok[cf] = chosen
            self.key["names"][chosen] = t
            if self._distinct(cf):
                self.safe.add(cf)
            prev = order(chosen)

    def _assign_tokens(self, name: str) -> None:
        for t in _TOKEN.findall(name):
            cf = t.casefold()
            if cf in self.tok:
                continue
            if self._keep(cf):
                if cf in self.kept:
                    self.kept_seen.add(cf)
                continue
            p = self._number("name", 2)
            self.tok[cf] = p
            self.key["names"][p] = t
            if self._distinct(cf):
                self.safe.add(cf)

    def freeze(self) -> Anonymiser:
        for name, aliases in self._users.items():
            p = self._assign_user(name)
            for i, alias in enumerate(aliases):
                suffix = string.ascii_lowercase[i] if i < 26 else f"x{i}"
                self._assign_user(alias, f"{p}{suffix}" if p != name else None)
        for pc in self._pcs:
            toks = [t.casefold() for t in _TOKEN.findall(pc)]
            if len(toks) == 1 and (toks[0] in self.tok or toks[0] in self.kept):
                continue
            p = self._number("pc", 2)
            self._whole(pc, p)
            self.key["computers"][p] = pc
        self._assign_conditions()
        for path in sorted(self._dirs, key=len):
            p = self._number("folder", 2)
            self._whole(path, p)
            if "\\" in path:
                self._whole(path.replace("\\", "/"), p)  # as config.yaml and FragPipe's manifest write it
            self.key["folders"][p] = path
        for name in self._folders:  # replaced as a whole
            toks = tuple(t.casefold() for t in _TOKEN.findall(name))
            if all(self._keep(t) for t in toks):
                continue  # "test", "2024_redo": nothing of its own to hide
            if len(toks) == 1 and toks[0] in self.tok and _CHUNK_ONLY.fullmatch(name):
                self.safe.add(toks[0])  # a folder named after its user or its one condition: that pseudonym
                continue
            p = self._number("exp", 3)
            self._whole(name, p)
            self.key["experiments"][p] = name
        for name in (*self._other, *self._folders):  # their words, for the names they turn up in
            self._assign_tokens(name)
        for name in (*self._samples, *self._other):
            self._whole(name, None)
        for name in self._samples:  # what the analysis derives: 'Drug_10uM_1' -> the condition 'Drug' / 'Drug_10uM'
            pieces = name.split("_")
            for n in range(1, len(pieces)):
                self._whole("_".join(pieces[:n]), None)
        self.frozen = True
        self._index()
        return self

    def _index(self) -> None:
        self._first = {k[0] for k in self.spans}
        self._hot = self._first | set(self.tok)  # a run of characters with none of these holds no name
        self._max_span = max((len(k) for k in self.spans), default=0)

    # -- rewriting -----------------------------------------------------------

    def name(self, s: str) -> str:
        """`s` read as one name: every token replaced, also the short ones."""
        return _TOKEN.sub(lambda m: self.tok.get(m.group().casefold(), m.group()), s)

    def _odd_pattern(self) -> re.Pattern | None:
        if self._odd_rx is None and self._odd:
            self._odd_repl = {cf: (p or self.name(orig)) for cf, (p, orig) in self._odd.items()}
            self._odd_repl = {cf: r for cf, r in self._odd_repl.items() if r.casefold() != cf}
            alts = sorted(self._odd_repl, key=len, reverse=True)
            self._odd_rx = re.compile(r"(?<![^\W_])(?:" + "|".join(re.escape(a) for a in alts) + r")(?![^\W_])",
                                      re.IGNORECASE) if alts else None
        return self._odd_rx if self._odd else None

    def _walk(self, chunk: str, found: list | None) -> str:
        parts = _SEP.split(chunk)
        toks = parts[0::2]
        cf = [t.casefold() for t in toks]
        if self._hot.isdisjoint(cf):
            return chunk
        n, i, touched = len(toks), 0, False
        while i < n:
            hit = 0
            if cf[i] in self._first:
                for length in range(min(n - i, self._max_span), 1, -1):
                    if tuple(cf[i:i + length]) in self.spans:
                        hit = length
                        break
            if hit:
                if found is not None:
                    found.append("".join(parts[2 * i:2 * (i + hit) - 1]))
                opaque = self.spans[tuple(cf[i:i + hit])]
                if opaque:
                    parts[2 * i] = opaque
                    for k in range(2 * i + 1, 2 * (i + hit) - 1):
                        parts[k] = ""
                else:
                    for k in range(i, i + hit):
                        parts[2 * k] = self.tok.get(cf[k], toks[k])
                touched = True
                i += hit
            else:
                if cf[i] in self.safe:
                    if found is not None:
                        found.append(toks[i])
                    parts[2 * i] = self.tok[cf[i]]
                    touched = True
                i += 1
        if touched and found is None:  # a run that holds one of the lab's names is a name: its short words go too
            for k in range(n):
                if parts[2 * k] == toks[k] and cf[k] in self.tok:
                    parts[2 * k] = self.tok[cf[k]]
        return "".join(parts)

    def _email(self, m: re.Match, found: list | None) -> str:
        addr = m.group()
        if addr.lower().endswith("@" + EMAIL_DOMAIN):
            return addr
        if found is not None:
            found.append(addr)
            return addr
        for p, orig in self.key["emails"].items():
            if orig.lower() == addr.lower():
                return p
        p = f"{self._number('email', 2)}@{EMAIL_DOMAIN}"
        self.key["emails"][p] = addr
        return p

    def _address(self, addr: str, found: list | None) -> str:
        if found is not None:
            found.append(addr)
            return addr
        for p, orig in self.key["addresses"].items():
            if orig.lower() == addr.lower():
                return p
        p = self._number("ip", 2)
        self.key["addresses"][p] = addr
        return p

    def _ipv4(self, m: re.Match, found: list | None) -> str:
        octets = [int(g) for g in m.groups()]
        # 127.x and 0.0.0.0 say nothing; a first number under 10 is far more often a version (1.4.3.0)
        if any(o > 255 for o in octets) or octets[0] in (0, 127, 255) or octets[0] < 10:
            return m.group()
        return self._address(m.group(), found)

    def _ipv6(self, m: re.Match, found: list | None) -> str:
        groups = [g for g in m.group().split(":") if g]
        if len(groups) < 2 or not any(ch.isdigit() for ch in m.group()) or m.group() == "::1":
            return m.group()
        return self._address(m.group(), found)

    def _home(self, m: re.Match, found: list | None) -> str:
        who = m.group(2)
        if who.casefold() in _NOT_PEOPLE or who in self.key["users"]:
            return m.group()
        toks = [t.casefold() for t in _TOKEN.findall(who)]
        if not toks or (len(toks) == 1 and self._keep(toks[0])):
            return m.group()
        if found is not None:
            found.append(who)
            return m.group()
        return m.group(1) + self._assign_user(who)

    def _scan(self, s: str, found: list | None) -> str:
        rx = self._odd_pattern()
        if rx is not None:
            def odd(m: re.Match) -> str:
                if found is not None:
                    found.append(m.group())
                    return m.group()
                return self._odd_repl[m.group().casefold()]

            s = rx.sub(odd, s)
        if "@" in s:
            s = _EMAIL.sub(lambda m: self._email(m, found), s)
        if "." in s:
            s = _IPV4.sub(lambda m: self._ipv4(m, found), s)
        if ":" in s:
            s = _IPV6.sub(lambda m: self._ipv6(m, found), s)
        if "sers" in s or "home" in s:
            s = _HOME.sub(lambda m: self._home(m, found), s)
        if found is None and self.frozen:  # learn names by their shape, then the pass below replaces them
            if "20" in s or "19" in s:
                for m in _DATED.finditer(s):
                    self._meet(m.group())
            low = s.lower()
            if ".raw" in low or ".mzml" in low or ".wiff" in low:
                for m in _RAWFILE.finditer(s):
                    self._meet(m.group(1))
        return _CHUNK.sub(lambda m: self._walk(m.group(), found), s)

    def text(self, s: str) -> str:
        """`s` with every known name, e-mail address and IP address replaced."""
        return self._scan(s, None)

    def find(self, s: str) -> list[str]:
        """The originals (names, addresses) still in `s`."""
        found: list[str] = []
        self._scan(s, found)
        return found

    def counts(self) -> dict[str, int]:
        return {g: len(v) for g, v in self.key.items() if v}


# -------------------------------------------------------- table columns ----

# columns that hold protein / gene / peptide identifiers: never rewritten, whatever they contain
_ID_COLUMNS = {c.casefold() for c in (
    "Protein", "Protein ID", "Protein IDs", "Entry Name", "Gene", "Genes", "Gene Names", "Gene Name",
    "Protein Description", "Protein.Group", "Protein.Ids", "Protein.Names", "First.Protein.Description",
    "Peptide", "Peptide Sequence", "Modified Sequence", "Modified Peptide", "Light Modified Peptide",
    "Heavy Modified Peptide", "Stripped.Sequence", "Modified.Sequence", "Precursor.Id", "Sequence", "Proteins",
    "Majority protein IDs", "Protein names", "Fasta headers", "Peptide sequences", "Mapped Genes", "Mapped Proteins",
    "Indistinguishable Proteins", "ProteinID", "Index", "ExamplePeptides", "Assigned Modifications",
    "Observed Modifications", "Extended Peptide", "Prev AA", "Next AA", "Leading razor protein", "Accession",
    "Master Protein Accessions", "Gene Symbol", "PG.ProteinGroups", "PG.Genes", "PG.ProteinNames",
    "PG.ProteinDescriptions", "ProteinName", "PeptideSequence", "peptide", "proteins", "leading_genes", "genes",
    "protein", "gene", "site", "term",
)}
_TABLE_SUFFIXES = (".tsv", ".txt")


def protected_columns(header: str) -> frozenset[int]:
    """Indexes of the identifier columns of a tab-separated table, from its header line."""
    cells = header.rstrip("\r\n").split("\t")
    if len(cells) < 2:
        return frozenset()
    keep = {i for i, c in enumerate(cells) if c.strip().casefold() in _ID_COLUMNS}
    if [c.strip() for c in cells[:3]] == ["id", "label", "description"]:  # Ionomos' own result tables
        keep |= {0, 1, 2}
    return frozenset(keep)


class _Scrub:
    """One file's lines, rewritten: secrets always, names when anonymising; identifier columns of a table kept."""

    def __init__(self, anon: Anonymiser | None, hide: list[str], table: bool):
        self.anon, self.hide, self.table = anon, hide, table
        self.cols: frozenset[int] | None = None

    def line(self, s: str) -> str:
        from ionomos import notify

        if self.cols is None:
            self.cols = protected_columns(s) if self.table else frozenset()
        if "http" in s or self.hide:
            s = notify.scrub(s, self.hide)
        if self.anon is None:
            return s
        new = self.anon.text(s)
        if self.cols and new != s:
            a, b = s.split("\t"), new.split("\t")
            if len(a) == len(b):
                for i in self.cols:
                    if i < len(a):
                        b[i] = a[i]
                new = "\t".join(b)
        return new


# -------------------------------------------------------------- the plan ----


@dataclass
class Item:
    arc: str                       # path in the zip, with the lab's names (rewritten when anonymising)
    what: str                      # "log", "status file", "DIA-NN protein matrix", ...
    src: Path | None = None
    mode: str = "text"             # text | tail | sample | config | json | yaml | gen
    size: int = 0                  # bytes that will be read
    full: int = 0                  # the file's size
    every: int = 1                 # sample: keep one data row in `every`
    gen: Callable[[], str] | None = None
    job: int = -1                  # index into Plan.jobs


@dataclass
class JobInfo:
    dest: Path
    arc: str                       # jobs/<id>-<name>
    job_id: int | None = None
    record: dict = field(default_factory=dict)
    status: str = ""
    method: str = ""
    user: str = ""
    names: list[str] = field(default_factory=list)   # its sample-ish names, for the order check
    tables: int = 0
    incomplete: list[str] = field(default_factory=list)


@dataclass
class Plan:
    config_path: Path
    opts: Options
    items: list[Item] = field(default_factory=list)
    jobs: list[JobInfo] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)   # {"path", "why", "bytes"}
    capped: list[dict] = field(default_factory=list)    # {"path", "why", "bytes", "kept"}
    problems: list[str] = field(default_factory=list)   # targets that could not be found

    @property
    def total(self) -> int:
        return sum(i.size for i in self.items)


def _never(p: Path) -> bool:
    low = p.name.lower()
    return low.endswith(NEVER_SUFFIXES) or any(w in low for w in NEVER_WORDS) or p.suffix.lower() == ".d"


def _all_jobs(db: Path | None) -> list:
    """Every job of the ledger; [] when there is none or it is damaged."""
    if db is None or not Path(db).is_file():
        return []
    try:
        from ionomos.ledger import Ledger, integrity

        if integrity(db) != "ok":
            return []
        led = Ledger(db)
        try:
            return led.list()
        finally:
            led.close()
    except Exception:  # noqa: BLE001 - a bundle is made when things are broken
        return []


def _record(dest: Path) -> dict:
    try:
        p = names.status_path(dest)
        if _size(p) > SMALL_FILE:
            return {}
        rec = json.loads(p.read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def default_jobs(config_path: Path, level: str) -> list:
    """With no job named: the running, waiting and last failed ones; `validate` adds the last finished one."""
    from ionomos import service

    db = service._raw_paths(config_path).get("database")
    if db is None or not Path(db).is_file():
        return []
    try:
        jobs = list(service._problem_jobs(db))
    except Exception:  # noqa: BLE001
        jobs = []
    if level == "validate":
        done = [j for j in _all_jobs(db) if j.status == "done"]
        if done and done[-1].id not in {j.id for j in jobs}:
            jobs.append(done[-1])
    return jobs


def recent_jobs(config_path: Path, limit: int = 30) -> list[dict]:
    """The newest jobs first, for the app's list: {"id", "name", "status", "user", "method", "problem"}."""
    from ionomos import service

    db = service._raw_paths(config_path).get("database")
    out = []
    for j in reversed(_all_jobs(db)[-limit:]):
        problem = j.status in ("failed", "running") or (j.status == "queued" and (j.reason or "").startswith("waiting:"))
        out.append({"id": j.id, "name": j.inbox_name, "status": j.status, "user": j.user, "method": j.method,
                    "problem": problem})
    return out


def _resolve(config_path: Path, targets, level: str) -> tuple[list[JobInfo], list[str]]:
    from ionomos import service

    db = service._raw_paths(config_path).get("database")
    ledger = _all_jobs(db)
    by_id = {j.id: j for j in ledger}
    by_dir = {os.path.normcase(os.path.abspath(j.dest_dir)): j for j in ledger}
    chosen, problems, seen = [], [], set()

    def add(job, dest: Path):
        key = os.path.normcase(os.path.abspath(dest))
        if key in seen:
            return
        seen.add(key)
        rec = _record(dest)
        folder = (rec.get("plan") or {}).get("folder") or {}
        name = job.inbox_name if job is not None else dest.name
        chosen.append(JobInfo(
            dest=dest, arc=f"{JOBS_DIR}/{job.id}-{name}" if job is not None else f"{JOBS_DIR}/{name}",
            job_id=job.id if job is not None else rec.get("job_id"), record=rec,
            status=(job.status if job is not None else rec.get("status")) or "",
            method=(job.method if job is not None else folder.get("method")) or "",
            user=(job.user if job is not None else folder.get("user")) or ""))

    if not targets:
        for j in default_jobs(config_path, level):
            add(j, Path(j.dest_dir))
        return chosen, problems
    for t in targets:
        t = str(t)
        if t.isdigit():
            j = by_id.get(int(t))
            if j is None:
                problems.append(f"no job {t} in the job list")
            else:
                add(j, Path(j.dest_dir))
            continue
        p = Path(t).expanduser()
        if p.is_file() and p.name in (names.STATUS_FILE, *names.LEGACY_STATUS_FILES):
            p = p.parent
        if not p.is_dir():
            problems.append(f"not a job number or a folder: {t}")
            continue
        add(by_dir.get(os.path.normcase(os.path.abspath(p))), p.resolve())
    return chosen, problems


def _add(plan: Plan, arc: str, src: Path, what: str, mode: str = "text", job: int = -1) -> Item | None:
    """One file into the plan, unless it is missing; `tail` keeps the end of a long file."""
    try:
        if not os.path.isfile(_long(src)):
            return None
    except OSError:
        return None
    full = _size(src)
    size = min(full, LOG_TAIL) if mode == "tail" else full
    if mode in ("json", "yaml", "config") and full > SMALL_FILE:
        mode, size = "tail", min(full, LOG_TAIL)
    it = Item(arc=arc, what=what, src=src, mode=mode, size=size, full=full, job=job)
    plan.items.append(it)
    if mode == "tail" and full > size:
        plan.capped.append({"path": arc, "why": f"only the last {LOG_TAIL / 1e6:.0f} MB", "bytes": full, "kept": size})
    return it


def _engine_tables(root: Path, skip_dirs: set[str]) -> list[tuple[Path, str, str]]:
    """(file, what, kind) for every result table the analysis reads under `root`: kind is "quant" (the
    analysis starts from it), "psm" (PSM-level: row-sampled above the cap) or "" (read along the way)."""
    found: list[tuple[Path, str, str]] = []
    for base, dirs, files in os.walk(_long(root)):
        rel = Path(os.path.relpath(base, _long(root)))
        here = root if str(rel) == "." else root / rel
        depth = len(here.parts) - len(root.parts)
        dirs[:] = sorted(d for d in dirs if depth < 3 and d not in skip_dirs and "_previous_" not in d
                         and not d.lower().endswith(".d") and not d.startswith("."))
        for f in sorted(files):
            p = here / f
            if _never(p):
                continue
            hit = next(((what, quant) for pat, what, quant in QUANT_TABLES if fnmatch.fnmatch(f, pat)), None)
            if hit is not None:
                if (f == "summary.txt" and "proteinGroups.txt" not in files) or \
                        (f.startswith("abundance_") and here.name != "tmt-report"):
                    continue
                found.append((p, hit[0], "quant" if hit[1] else ""))
            elif f in dict(PSM_TABLES):
                found.append((p, dict(PSM_TABLES)[f], "psm"))
            elif f.lower().endswith("sdrf.tsv"):
                found.append((p, "SDRF sample sheet", ""))
    found.sort(key=lambda t: (t[2] == "psm", len(t[0].parts), str(t[0])))
    return found


def _job_small(plan: Plan, idx: int) -> None:
    """A job's small files (both levels): status, notes, experiment.yaml, the run folder, the analysis summary."""
    from ionomos.manifest import EXPERIMENT_YAML

    job = plan.jobs[idx]
    d, arc = job.dest, job.arc
    _add(plan, f"{arc}/{names.STATUS_FILE}", names.status_path(d), "status file", "json", idx)
    for note in ("FAILED.txt", "DONE.txt"):
        _add(plan, f"{arc}/{note}", d / note, "status note", "tail", idx)
    _add(plan, f"{arc}/{EXPERIMENT_YAML}", d / EXPERIMENT_YAML, "experiment.yaml", "yaml", idx)
    raw_dir = str((job.record.get("plan") or {}).get("raw_dir") or "")
    plexes = sorted(set(((job.record.get("plan") or {}).get("raw_subdirs") or {}).values()))  # <plex>\ drops (D69)
    for folder in dict.fromkeys((d, d / raw_dir if raw_dir else d, d / "raw", *(d / p for p in plexes))):
        try:
            anns = sorted(folder.glob("*annotation*.txt")) if folder.is_dir() else []
        except OSError:
            anns = []
        for a in anns:
            if _size(a) <= SMALL_FILE:
                _add(plan, f"{arc}/{a.relative_to(d).as_posix()}", a, "TMT annotation", "text", idx)
    rd = names.run_dir(d)
    try:
        run_files = sorted(p for p in rd.iterdir() if p.is_file()) if rd.is_dir() else []
    except OSError:
        run_files = []
    for p in run_files:
        if _never(p) or p.name == "CANCEL":
            continue
        what = ("run fingerprint" if fnmatch.fnmatch(p.name, names.RUN_FINGERPRINT_GLOB)
                else "search log" if p.suffix.lower() == ".log" else "what Ionomos gave the search engine")
        _add(plan, f"{arc}/{rd.name}/{p.name}", p, what, "tail", idx)
    for name in ("analysis.json", "analysis_error.txt"):
        _add(plan, f"{arc}/{RESULTS}/{name}", d / RESULTS / name, "analysis summary", "tail", idx)


def _job_tables(plan: Plan, idx: int) -> None:
    """`validate`: the engine's tables the analysis reads, an SDRF the lab supplied, and all of results/."""
    job = plan.jobs[idx]
    d, arc = job.dest, job.arc
    have = {i.arc for i in plan.items}
    rd = names.run_dir(d)
    roots = [d / e for e in ENGINE_DIRS if (d / e).is_dir()]
    skip = {RESULTS, rd.name, names.RUN_DIR, *names.LEGACY_RUN_DIRS, "sage_mzml", "raw"}
    cap = int(plan.opts.psm_mb * 1e6)
    for root in roots or [d]:
        for p, what, kind in _engine_tables(root, {"sage_mzml"} if roots else skip):
            inside = f"{arc}/{p.relative_to(d).as_posix()}"
            it = None if inside in have else _add(plan, inside, p, what, "text", idx)
            if it is None:
                continue
            job.tables += kind == "quant"
            if kind == "psm" and it.full > cap > 0:
                it.mode, it.every = "sample", -(-it.full // cap)
                it.size = it.full // it.every
                why = f"1 row in {it.every} kept (the table is over {plan.opts.psm_mb:g} MB)"
                plan.capped.append({"path": it.arc, "why": why, "bytes": it.full, "kept": it.size})
                job.incomplete.append(f"{p.name}: {why}, so PSM-level numbers will differ")
    if roots:  # an SDRF the lab put in the experiment folder is the design (two levels deep, as the analysis looks)
        for p in sorted([*d.glob("*sdrf.tsv"), *d.glob("*/*sdrf.tsv")]):
            if p.parent.name not in (RESULTS, rd.name, *ENGINE_DIRS) and "_previous_" not in p.parent.name:
                _add(plan, f"{arc}/{p.relative_to(d).as_posix()}", p, "SDRF sample sheet", "text", idx)
    res = d / RESULTS
    if not res.is_dir():
        return
    for base, dirs, files in os.walk(_long(res)):
        dirs.sort()
        rel = Path(os.path.relpath(base, _long(res)))
        for f in sorted(files):
            p = res / f if str(rel) == "." else res / rel / f
            inside = f"{arc}/{RESULTS}/{p.relative_to(res).as_posix()}"
            if inside in have:
                continue
            if _never(p):
                plan.skipped.append({"path": inside, "why": f"never included ({NEVER_SAID})", "bytes": _size(p)})
            else:
                _add(plan, inside, p, "Ionomos result", "text", idx)


def collect(config_path: Path, targets=(), opts: Options | None = None) -> Plan:
    """What a bundle would hold, without reading more than file sizes (the app's dialog shows this)."""
    from ionomos import attention, health, service

    opts = opts or Options()
    config_path = Path(config_path)
    plan = Plan(config_path=config_path, opts=opts)
    paths = service._raw_paths(config_path)
    log_dir = paths.get("log_dir")
    if opts.note.strip():
        from ionomos.buildinfo import one_line

        note = f"{one_line()}\n{datetime.datetime.now():%Y-%m-%d %H:%M}\n\n{opts.note.strip()}\n"
        plan.items.append(Item("note.txt", "the note typed for this bundle", mode="gen", size=len(note),
                               gen=lambda: note))
    plan.items.append(Item("build.json", "Ionomos version and build", mode="gen", size=200, gen=_build_json))
    plan.items.append(Item("report.txt", "system facts, checks, job list, log tail", mode="gen", size=40_000,
                           gen=lambda: service.diagnostics(config_path)))
    _add(plan, "config.yaml", config_path, "settings (secrets replaced by ***)", "config")
    try:
        app_logs = sorted(service.appdata_dir().glob("app.log*"))[:3]
    except OSError:
        app_logs = []
    for f in app_logs:
        _add(plan, f"logs/app/{f.name}", f, "app log", "tail")
    if log_dir is not None and log_dir.is_dir():
        for f in sorted([*names.log_files(log_dir), *log_dir.glob("app.log*")])[:8]:
            _add(plan, f"logs/{f.name}", f, "watcher log", "tail")
        for f in health.recent_crashes(log_dir, 5):
            _add(plan, f"crashes/{f.name}", f, "crash file", "tail")
        _add(plan, "logs/heartbeat.json", log_dir / health.HEARTBEAT_NAME, "heartbeat", "tail")
        folder = attention.folder(log_dir)
        try:
            items = sorted(folder.glob("*.json"))[-40:] if folder.is_dir() else []
        except OSError:
            items = []
        for f in items:
            _add(plan, f"attention/{f.name}", f, "needs-attention item", "tail")
    plan.jobs, plan.problems = _resolve(config_path, targets, opts.level)
    for idx in range(len(plan.jobs)):
        _job_small(plan, idx)
    if opts.level == "validate":  # after every job's small files, so the size limit never drops a log
        small = len(plan.items)
        for idx in range(len(plan.jobs)):
            _job_tables(plan, idx)
        _apply_cap(plan, small)
    return plan


def _apply_cap(plan: Plan, first: int) -> None:
    budget = int(plan.opts.max_mb * 1e6)
    used = sum(i.size for i in plan.items[:first])
    kept = plan.items[:first]
    for it in plan.items[first:]:
        if used + it.size > budget:
            plan.skipped.append({"path": it.arc, "bytes": it.full,
                                 "why": f"over the size limit of {plan.opts.max_mb:g} MB for one bundle (--max-mb)"})
            plan.capped = [c for c in plan.capped if c["path"] != it.arc]
            if it.job >= 0:
                plan.jobs[it.job].incomplete.append(f"{PurePosixPath(it.arc).name} was left out (size limit)")
            continue
        used += it.size
        kept.append(it)
    plan.items = kept


def _build_json() -> str:
    from ionomos.buildinfo import info

    return json.dumps(info(), indent=2)


# ---------------------------------------------------- learning the names ----


def _stem(name: str) -> str:
    from ionomos.downstream.quant import run_stem

    return run_stem(str(name))


_HEADER_SUFFIXES = (" MaxLFQ Intensity", " Intensity", " Spectral Count", " Total Spectral Count",
                    " Unique Spectral Count", " Total Intensity", " Unique Intensity", " Razor Intensity",
                    " Log2 Ratio HL", " Combined Total Peptides", " Match Type")


def _learn_record(anon: Anonymiser, rec: dict, into: list[str] | None = None) -> None:
    """The names in one ionomos.json (also the ledger's copy of it)."""
    def sample(n):
        if n not in (None, ""):
            anon.add_sample(str(n))
            if into is not None:
                into.append(str(n))

    plan = rec.get("plan") if isinstance(rec.get("plan"), dict) else {}
    folder = plan.get("folder") if isinstance(plan.get("folder"), dict) else {}
    for k in ("original", "safe"):
        anon.add_folder(folder.get(k))
    anon.add_user(folder.get("user"))
    for k in ("source", "dest"):
        if plan.get(k):
            anon.add_folder(Path(str(plan[k]).replace("\\", "/")).name)
    for line in plan.get("manifest") or []:
        if isinstance(line, dict):
            sample(_stem(line.get("file") or ""))
            sample(line.get("experiment"))
            if line.get("experiment") and line.get("bioreplicate") is not None:
                sample(f"{line['experiment']}_{line['bioreplicate']}")
    for exp in (plan.get("layout") or {}) if isinstance(plan.get("layout"), dict) else ():
        sample(exp)
    renames = plan.get("renames") if isinstance(plan.get("renames"), dict) else {}
    for a, b in renames.items():
        for n in (a, b):
            anon.add_name(_stem(Path(str(n).replace("\\", "/")).name))
    for n in plan.get("other_files") or []:
        anon.add_name(Path(str(n).replace("\\", "/")).stem)
    _learn_overrides(anon, plan.get("overrides") if isinstance(plan.get("overrides"), dict) else {}, into)


def _learn_overrides(anon: Anonymiser, ov: dict, into: list[str] | None = None) -> None:
    """The names in an experiment.yaml (or ionomos.json's copy of it)."""
    def sample(n):
        if isinstance(n, (str, int)) and not isinstance(n, bool) and str(n).strip():
            anon.add_sample(str(n))
            if into is not None:
                into.append(str(n))

    anon.add_user(ov.get("user"))
    files = ov.get("files") if isinstance(ov.get("files"), dict) else {}
    for fname, spec in files.items():
        sample(_stem(fname))
        if isinstance(spec, dict):
            sample(spec.get("experiment"))
    tmt = ov.get("tmt") if isinstance(ov.get("tmt"), dict) else {}
    for block in (tmt, *(v for v in tmt.values() if isinstance(v, dict))):
        channels = block.get("channels") if isinstance(block.get("channels"), dict) else {}
        for v in channels.values():
            sample(v)
    an = ov.get("analysis") if isinstance(ov.get("analysis"), dict) else {}
    for key in ("sample_conditions", "doses", "times", "block", "covariates"):
        v = an.get(key)
        if isinstance(v, dict):
            for a, b in v.items():
                sample(a)
                if isinstance(b, str):
                    sample(b)
    for key in ("exclude_samples", "control", "tmt_reference"):
        v = an.get(key)
        for x in (v if isinstance(v, list) else [v]):
            sample(x)
    for comp in an.get("comparisons") or []:
        for part in (re.split(r"\s+vs\.?\s+", comp) if isinstance(comp, str) else comp if isinstance(comp, list) else ()):
            sample(part)


def _learn_path(anon: Anonymiser, cell: str, into: list[str], lab_roots: list[str]) -> bool:
    """A raw file's path in a table or manifest: its stem is a sample name; its folder, when it is not one
    of the lab's own (those are rewritten part by part), is replaced as one piece."""
    cell = cell.strip()
    if not (cell.lower().endswith((".raw", ".mzml", ".d", ".dia", ".wiff")) or re.match(r"(?:[A-Za-z]:)?[\\/]", cell)):
        return False
    anon.add_sample(_stem(cell))
    into.append(_stem(cell))
    folder = re.sub(r"[\\/]+[^\\/]*$", "", cell)
    flat = folder.replace("\\", "/").casefold()
    if folder != cell and not any(flat == r or flat.startswith(r + "/") for r in lab_roots):
        anon.add_dir(folder)
    return True


def _learn_header(anon: Anonymiser, path: Path, into: list[str], lab_roots: list[str]) -> None:
    """Run and sample names in a result table's header (a table made outside Ionomos has no manifest)."""
    try:
        with open(_long(path), "rb") as f:
            header = f.readline(4_000_000).decode("utf-8", "replace").rstrip("\r\n")
    except OSError:
        return
    for cell in header.split("\t"):
        cell = cell.strip()
        if not cell or cell.casefold() in _ID_COLUMNS:
            continue
        if _learn_path(anon, cell, into, lab_roots):
            continue
        for suffix in _HEADER_SUFFIXES:
            if cell.endswith(suffix) and len(cell) > len(suffix):
                anon.add_sample(cell[: -len(suffix)])
                into.append(cell[: -len(suffix)])
                break


def learn(plan: Plan) -> Anonymiser:
    """An Anonymiser that knows every name the lab's settings, job list, inbox and the bundled jobs hold."""
    import getpass
    import platform
    import socket

    import yaml

    from ionomos import service

    try:
        raw = yaml.safe_load(plan.config_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a broken config still gets a bundle
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    kept: list[str] = []
    methods = raw.get("methods") if isinstance(raw.get("methods"), dict) else {}
    for key, m in methods.items():
        kept.append(str(key))
        if isinstance(m, dict):
            kept += [str(a) for a in m.get("aliases") or []]
            kept += [str(m.get(k)) for k in ("like", "engine", "data_type") if m.get(k)]
    naming = raw.get("naming") if isinstance(raw.get("naming"), dict) else {}
    codes = naming.get("condition_codes") if isinstance(naming.get("condition_codes"), dict) else {}
    kept += [str(v) for v in codes.values()]
    an = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
    kw = an.get("control_keywords")
    kept += [str(k) for k in (kw if isinstance(kw, list) else [kw] if isinstance(kw, str) else [])]
    kept += [an["control"]] if isinstance(an.get("control"), str) else []
    anon = Anonymiser(kept=kept, keep_conditions=plan.opts.keep_conditions)

    users = raw.get("users") if isinstance(raw.get("users"), dict) else {}
    aliases = users.get("aliases") if isinstance(users.get("aliases"), dict) else {}
    for user, al in aliases.items():
        anon.add_user(user, al if isinstance(al, list) else [al])
    paths = service._raw_paths(plan.config_path)
    learned = users.get("learned_aliases_file") or (plan.config_path.parent / "learned_aliases.yaml")
    try:
        more = yaml.safe_load(Path(learned).read_text(encoding="utf-8")) or {}
        for user, al in (more.items() if isinstance(more, dict) else ()):
            anon.add_user(user, al if isinstance(al, list) else [al])
    except Exception:  # noqa: BLE001
        pass
    users_root = paths.get("users_root")
    if users_root is not None:
        from ionomos.config import DEFAULT_USER_IGNORE, not_a_user

        ignore = tuple(users.get("ignore") or DEFAULT_USER_IGNORE) if isinstance(users.get("ignore"), list) \
            else DEFAULT_USER_IGNORE
        try:
            for p in sorted(users_root.iterdir()):
                if p.is_dir() and not p.name.startswith(".") and not_a_user(p.name, ignore) is None:
                    anon.add_user(p.name)
        except OSError:
            pass
    for who in (getpass.getuser, lambda: os.environ.get("USERNAME"), lambda: os.environ.get("USER"),
                lambda: Path.home().name):
        try:
            anon.add_user(who())
        except Exception:  # noqa: BLE001 - getpass raises without a user database entry
            pass
    for pc in (platform.node, socket.gethostname, lambda: os.environ.get("COMPUTERNAME")):
        try:
            name = pc() or ""
        except Exception:  # noqa: BLE001
            name = ""
        anon.add_pc(name.split(".")[0])
    for j in _all_jobs(paths.get("database")):
        anon.add_user(j.user)
        anon.add_folder(j.inbox_name)
        anon.add_folder(Path(j.dest_dir).name)
        _learn_record(anon, j.parsed if isinstance(j.parsed, dict) else {})
    inbox = paths.get("inbox")
    try:
        entries = sorted(inbox.iterdir()) if inbox is not None and inbox.is_dir() else []
    except OSError:
        entries = []
    for p in entries[:500]:
        if p.name.startswith("."):
            continue
        base = p.name[: -len(".REJECTED.txt")] if p.name.endswith(".REJECTED.txt") else p.name
        anon.add_folder(base if p.is_dir() or p.name.endswith(".REJECTED.txt") else Path(base).stem)
        if p.is_dir():
            try:
                for f in itertools.islice(sorted(p.iterdir()), 400):
                    anon.add_name(_stem(f.name) if f.is_file() else f.name)
            except OSError:
                pass
    try:
        removed = sorted((inbox / names.REMOVED_DIR).iterdir()) if inbox is not None else []
    except OSError:
        removed = []
    for p in removed[:500]:
        anon.add_folder(p.name if p.is_dir() else _stem(p.name))
    for it in plan.items:
        if it.what == "needs-attention item" and it.src is not None:
            try:
                item = json.loads(it.src.read_text(encoding="utf-8"))
                if item.get("dest"):
                    anon.add_folder(Path(str(item["dest"]).replace("\\", "/")).name)
            except (OSError, ValueError, AttributeError):
                pass
    from ionomos.manifest import EXPERIMENT_YAML

    lab_roots = [str(paths[k]).replace("\\", "/").rstrip("/").casefold() for k in ("users_root", "inbox") if k in paths]
    for job in plan.jobs:
        flat = str(job.dest.parent).replace("\\", "/").casefold()
        if not any(flat == r or flat.startswith(r + "/") for r in lab_roots):
            anon.add_dir(str(job.dest.parent))  # a folder from outside the lab's tree: where it sits says who
        anon.add_user(job.user)
        anon.add_folder(job.dest.name)
        anon.add_folder(PurePosixPath(job.arc).name.split("-", 1)[-1] if job.job_id is not None
                        else PurePosixPath(job.arc).name)
        if users_root is not None and job.dest.parent.parent == users_root:
            anon.add_user(job.dest.parent.name)
        _learn_record(anon, job.record, job.names)
        try:
            ov = yaml.safe_load((job.dest / EXPERIMENT_YAML).read_text(encoding="utf-8")) or {}
            _learn_overrides(anon, ov if isinstance(ov, dict) else {}, job.names)
        except Exception:  # noqa: BLE001
            pass
        try:
            for p in job.dest.iterdir():
                if p.is_file() and p.suffix.lower() == ".raw":
                    anon.add_sample(p.stem)
                    job.names.append(p.stem)
        except OSError:
            pass
    for it in plan.items:
        if it.src is None or it.job < 0:
            continue
        names_of = plan.jobs[it.job].names
        if it.what == "TMT annotation":
            try:
                for ln in it.src.read_text(encoding="utf-8", errors="replace").splitlines():
                    cells = re.split(r"[\t ]+", ln.strip(), maxsplit=1)
                    if len(cells) == 2:
                        anon.add_sample(cells[1])
                        names_of.append(cells[1])
            except OSError:
                pass
        elif it.what == "analysis summary" and it.src.name == "analysis.json" and it.full <= SMALL_FILE:
            try:
                summary = json.loads(it.src.read_text(encoding="utf-8"))
                samples = summary.get("samples") if isinstance(summary, dict) else None
                for sample, cond in (samples.items() if isinstance(samples, dict) else ()):
                    for n in (sample, cond):
                        if isinstance(n, str) and n:
                            anon.add_sample(n)
                    names_of.append(str(sample))
            except (OSError, ValueError):
                pass
        elif it.src.suffix.lower() in _TABLE_SUFFIXES and it.what not in ("status note", "Ionomos result"):
            _learn_header(anon, it.src, names_of, lab_roots)
            if it.src.name in ("psm.tsv",):
                anon.add_sample(it.src.parent.name)
        elif it.src.name.endswith(".fp-manifest") and it.full <= SMALL_FILE:
            try:
                for ln in it.src.read_text(encoding="utf-8", errors="replace").splitlines():
                    _learn_path(anon, ln.split("\t")[0], names_of, lab_roots)
            except OSError:
                pass
    return anon.freeze()


def order_kept(anon: Anonymiser, names_of: list[str]) -> bool:
    """Do these sample names sort the same before and after? (Perseus imputation draws in that order.)"""
    uniq = list(dict.fromkeys(names_of))
    new = [anon.text(n) for n in uniq]
    if len(set(new)) != len(new):
        return False
    before = sorted(range(len(uniq)), key=lambda i: uniq[i].encode("utf-8"))
    after = sorted(range(len(uniq)), key=lambda i: new[i].encode("utf-8"))
    return before == after


# ------------------------------------------------------------- writing ----

_REMOVED = "[removed by the bundle: {n} characters of free text]"


def _restructure(obj, anon: Anonymiser):
    """A parsed JSON / YAML document with its free text removed and its name fragments rewritten as names."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "notes" and isinstance(v, str) and v.strip():
                out[k] = _REMOVED.format(n=len(v))
            elif k == "tokens" and isinstance(v, list):
                out[k] = [anon.name(x) if isinstance(x, str) else x for x in v]
            else:
                out[k] = _restructure(v, anon)
        return out
    if isinstance(obj, list):
        return [_restructure(v, anon) for v in obj]
    return obj


def _lines(it: Item, anon: Anonymiser | None, hide: list[str]) -> Iterator[str]:
    """The item's text, line by line, before scrubbing. Structured files are re-written first."""
    from ionomos import notify

    if it.mode == "gen":
        yield from (it.gen() if it.gen else "").splitlines(keepends=True)
        return
    src = _long(it.src)
    if it.mode in ("config", "json", "yaml"):
        with open(src, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if it.mode == "config":
            text = notify.redact_config_text(text, hide)
        elif anon is not None:
            try:
                if it.mode == "json":
                    text = json.dumps(_restructure(json.loads(text), anon), indent=2, ensure_ascii=False) + "\n"
                else:
                    import yaml

                    text = yaml.safe_dump(_restructure(yaml.safe_load(text), anon), sort_keys=False,
                                          allow_unicode=True)
            except Exception:  # noqa: BLE001 - not parseable: it is still scrubbed as text
                pass
        yield from text.splitlines(keepends=True)
        return
    enc = _sniff(it.src) or "utf-8"
    with open(src, "rb") as f:
        if it.mode == "tail" and it.full > LOG_TAIL and enc == "utf-8":
            f.seek(it.full - LOG_TAIL)
            f.readline()  # the cut lands inside a line: drop what is left of it
        if enc == "utf-16":
            yield from f.read().decode("utf-16", "replace").splitlines(keepends=True)
            return
        for n, raw in enumerate(f):
            if it.mode == "sample" and n and (n - 1) % it.every:
                continue
            yield raw.decode("utf-8", "surrogateescape")


def _arc(arc: str, anon: Anonymiser | None, used: set[str]) -> str:
    if anon is not None:
        arc = "/".join(anon.text(part) for part in arc.split("/"))
    base, n = arc, 1
    while arc.casefold() in used:
        n += 1
        p = PurePosixPath(base)
        arc = str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
    used.add(arc.casefold())
    return arc


def _put(z: zipfile.ZipFile, arc: str, lines, scrub: _Scrub) -> tuple[int, str]:
    info = zipfile.ZipInfo(arc, date_time=datetime.datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    digest, size, buf, held = hashlib.sha256(), 0, [], 0
    with z.open(info, "w", force_zip64=True) as out:
        for line in lines:
            data = scrub.line(line).encode("utf-8", "surrogateescape")
            buf.append(data)
            held += len(data)
            if held >= 1 << 20:
                block = b"".join(buf)
                out.write(block)
                digest.update(block)
                size += len(block)
                buf, held = [], 0
        block = b"".join(buf)
        out.write(block)
        digest.update(block)
        size += len(block)
    return size, digest.hexdigest()


def fasta_facts(path: Path) -> dict | None:
    """Name, size, entry count, decoy count and SHA-256 of a FASTA file: what a bundle says instead of holding it."""
    try:
        digest, entries, decoys, size = hashlib.sha256(), 0, 0, 0
        with open(_long(path), "rb") as f:
            for line in f:
                digest.update(line)
                size += len(line)
                if line.startswith(b">"):
                    entries += 1
                    decoys += line[1:6].lower().startswith((b"rev_", b"decoy"))
        return {"name": Path(path).name, "bytes": size, "entries": entries, "decoys": decoys,
                "sha256": digest.hexdigest()}
    except OSError:
        return None


@dataclass
class Result:
    path: Path
    key_path: Path | None
    manifest: dict
    kept_identifiers: list[tuple[str, str, int]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return saved_message(self)


def _job_manifest(plan: Plan, job: JobInfo, cache: dict) -> dict:
    rec = job.record
    run = rec.get("run") if isinstance(rec.get("run"), dict) else {}
    mc = rec.get("method_config") if isinstance(rec.get("method_config"), dict) else {}
    out = {"arc": job.arc, "folder": job.dest.name, "job_id": job.job_id, "status": job.status, "method": job.method,
           "analysis_method": mc.get("analysis_method") or "", "reason": rec.get("reason") or "",
           "exit_code": run.get("exit_code")}
    fasta = run.get("fasta")
    if fasta:
        if fasta not in cache:
            cache[fasta] = fasta_facts(Path(fasta))
        out["fasta"] = cache[fasta] or {"name": Path(str(fasta).replace("\\", "/")).name, "missing": True}
    if plan.opts.level == "validate":
        out["result_tables"] = job.tables
        out["reproducible"] = bool(job.tables) and not job.incomplete
        out["incomplete"] = list(job.incomplete)
    return out


def _readme(m: dict) -> str:
    a = m["anonymised"]
    lines = [
        f"Ionomos bundle ({m['level']})", "=" * 40, "",
        f"Made by {m['build_line']} on {m['created']}.",
        "It was written to a folder on the lab's PC. Ionomos sent it nowhere: a person copied it.", "",
        "For the developer", "-----------------",
        "  ionomos bundle inspect <this zip>            what is in it",
        "  ionomos bundle unpack <this zip> <folder>     one folder per experiment, the rest in _bundle/",
    ]
    if m["level"] == "validate":
        lines += ["  ionomos --config <folder>/config.yaml analyze <folder>/<experiment>",
                  "      repeats the analysis with the lab's settings; compare with the experiment's results/"]
    lines += ["", "Jobs", "----"]
    for j in m["jobs"] or [{"arc": "(none)"}]:
        bits = [str(j.get(k)) for k in ("status", "method") if j.get(k)]
        if j.get("reproducible") is False:
            bits.append("NOT fully reproducible: " + ("; ".join(j.get("incomplete") or []) or "no result table"))
        if j.get("order_preserved") is False:
            bits.append("sample order changed by the pseudonyms: randomly imputed values will differ")
        lines.append(f"  {j['arc']}" + (f"  ({', '.join(bits)})" if bits else ""))
    lines += ["", "Names", "-----"]
    if a["enabled"]:
        lines += [
            "Anonymised. User names, the PC name, experiment / folder / raw-file / sample names, e-mail and IP",
            "addresses were replaced by pseudonyms (user01, exp001, condA, name01, pc01 ...), the same one for",
            "the same original in every file. Protein and gene identifiers and all numbers are as measured.",
            "Condition words: " + ("all kept." if a["conditions"] == "keep" else
                                   "control / role words kept (DMSO, pool ...), the rest replaced."),
            "The key that turns pseudonyms back into names is a separate file that stays in the lab.",
            "Not covered: free text someone typed (notes: fields are removed, the note for this bundle is",
            "not), a name written inside a longer word, a plain word of a name standing alone elsewhere",
            "(words with letters and digits are replaced everywhere), a name that is also a protein or",
            "gene name (kept in the identifier columns of tables), folder names above the users folder,",
            "and pictures or other files that are not text (left out, listed below).",
        ]
    else:
        lines += ["NOT anonymised: this bundle holds the lab's real names. Secrets (webhook addresses,",
                  "passwords) are still replaced by ***."]
    lines += ["", f"Never included: {NEVER_SAID}.", ""]
    for title, rows in (("Capped", m["capped"]), ("Left out", m["skipped"])):
        if rows:
            lines += [title, "-" * len(title)]
            lines += [f"  {r['path']}: {r['why']}" for r in rows]
            lines.append("")
    lines += [f"{len(m['files'])} files, {m['bytes'] / 1e6:.1f} MB before compression. Hashes: {names.BUNDLE_MANIFEST}.", ""]
    return "\n".join(lines)


def _write(plan: Plan, anon: Anonymiser | None, tmp: Path, progress) -> dict:
    from ionomos import notify
    from ionomos.buildinfo import info, one_line

    hide = notify.file_secrets(plan.config_path)  # never in a bundle: webhook addresses, the SMTP password (D58)
    used: set[str] = {names.BUNDLE_MANIFEST.casefold(), names.BUNDLE_README.casefold()}
    files, skipped, total = [], list(plan.skipped), 0
    with zipfile.ZipFile(_long(tmp), "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for it in plan.items:
            if it.src is not None and it.mode not in ("config", "json", "yaml"):
                enc = _sniff(it.src)
                if enc is None and anon is not None:
                    skipped.append({"path": it.arc, "bytes": it.full,
                                    "why": "not a text file: its names cannot be replaced, so it was left out"})
                    continue
                if enc is None:
                    it = Item(**{**it.__dict__, "mode": "binary"})
            arc = _arc(it.arc, anon, used)
            if progress:
                progress(f"{it.what}: {PurePosixPath(arc).name}")
            try:
                if it.mode == "binary":
                    size, sha = _put_binary(z, arc, it.src)
                else:
                    table = it.src is not None and it.src.suffix.lower() in _TABLE_SUFFIXES
                    size, sha = _put(z, arc, _lines(it, anon, hide), _Scrub(anon, hide, table))
            except OSError as exc:
                skipped.append({"path": it.arc, "bytes": it.full, "why": f"could not be read ({type(exc).__name__})"})
                continue
            total += size
            files.append({"path": arc, "bytes": size, "sha256": sha, "what": it.what})
        cache: dict = {}
        jobs = []
        for job in plan.jobs:
            jm = _job_manifest(plan, job, cache)
            if anon is not None:
                jm["order_preserved"] = order_kept(anon, job.names)
            jobs.append(jm)
        manifest = {
            "format": FORMAT, "app": names.APP, "build": info(), "build_line": one_line(),
            "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": plan.opts.level,
            "anonymised": {"enabled": anon is not None,
                           "conditions": "keep" if plan.opts.keep_conditions else "controls",
                           "kept_words": sorted(anon.kept_seen) if anon is not None else [],
                           "replaced": anon.counts() if anon is not None else {},
                           "key_file": "a separate file that stays in the lab" if anon is not None else None},
            "limits": {"max_mb": plan.opts.max_mb, "psm_mb": plan.opts.psm_mb, "log_tail_mb": LOG_TAIL / 1e6},
            "never_included": NEVER_SAID, "jobs": jobs, "capped": plan.capped, "skipped": skipped,
            # a folder that was not found was typed by a person: with names replaced, only that there was one
            "not_found": [p if anon is None or p.startswith("no job ") else "a folder that was not found"
                          for p in plan.problems],
            "files": files, "bytes": total,
        }
        text = json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
        if anon is not None:  # job folders, reasons and left-out paths carry names
            manifest = json.loads(_Scrub(anon, hide, False).line(text))
            for jm in manifest["jobs"]:
                jm["arc"] = "/".join(anon.text(p) for p in jm["arc"].split("/"))
        z.writestr(names.BUNDLE_README, _readme(manifest))
        z.writestr(names.BUNDLE_MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def _put_binary(z: zipfile.ZipFile, arc: str, src: Path) -> tuple[int, str]:
    info = zipfile.ZipInfo(arc, date_time=datetime.datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    digest, size = hashlib.sha256(), 0
    with open(_long(src), "rb") as f, z.open(info, "w", force_zip64=True) as out:
        for block in iter(lambda: f.read(1 << 20), b""):
            out.write(block)
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def verify(zip_path: Path, anon: Anonymiser) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]]:
    """Search a finished zip for every original: (leaks, identifiers kept), each (file, original, count).
    File names inside the zip are searched too. Identifier columns of tables are reported, not failed."""
    import io

    leaks: dict[tuple[str, str], int] = {}
    kept: dict[tuple[str, str], int] = {}

    def note(into: dict, arc: str, found: list[str]):
        for o in found:
            into[(arc, o)] = into.get((arc, o), 0) + 1

    with zipfile.ZipFile(_long(zip_path)) as z:
        for info in z.infolist():
            arc = info.filename
            note(leaks, arc + " (its name)", anon.find(arc))
            table = arc.lower().endswith(_TABLE_SUFFIXES)
            cols: frozenset[int] | None = None
            with z.open(info) as raw:
                for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="\n"):
                    if cols is None:
                        cols = protected_columns(line) if table else frozenset()
                    if cols:
                        cells = line.split("\t")
                        ids = [cells[i] for i in sorted(cols) if i < len(cells)]
                        for i in cols:
                            if i < len(cells):
                                cells[i] = ""
                        note(leaks, arc, anon.find("\t".join(cells)))
                        note(kept, arc, anon.find("\t".join(ids)))
                    else:
                        note(leaks, arc, anon.find(line))
    return ([(a, o, n) for (a, o), n in sorted(leaks.items())], [(a, o, n) for (a, o), n in sorted(kept.items())])


def bundle_name(level: str, when: datetime.datetime | None = None) -> str:
    from ionomos import __version__

    when = when or datetime.datetime.now()
    return f"{names.BUNDLE_PREFIX}-{when:%Y%m%d-%H%M}-{level}-v{__version__}.zip"


def create(config_path: Path, targets=(), opts: Options | None = None, dest: Path | None = None,
           dest_dir: Path | None = None, progress: Callable[[str], None] | None = None) -> Result:
    """Write the bundle (and, when anonymising, its key file next to it). `dest` names the zip itself;
    otherwise it goes into `dest_dir` (default: output_dir()). An existing file is never replaced.
    Raises BundleLeak, leaving no zip, when an original name is still in the finished file."""
    opts = opts or Options()
    config_path = Path(config_path)
    plan = collect(config_path, targets, opts)
    if dest is None:
        folder = Path(dest_dir) if dest_dir is not None else output_dir(config_path)
        dest = folder / bundle_name(opts.level)
    dest = Path(dest)
    try:
        os.makedirs(_long(dest.parent), exist_ok=True)
    except OSError as exc:
        raise BundleError(f"cannot create {dest.parent}: {exc}") from exc
    anon = learn(plan) if opts.anonymise else None
    tmp = unique(dest.with_name(dest.name + ".part"))
    leaks: list = []
    kept: list = []
    try:
        for _attempt in range(2):  # a name first met half-way (a home folder in a log) needs a second pass
            if progress:
                progress("writing the bundle")
            manifest = _write(plan, anon, tmp, progress)
            if anon is None:
                break
            if progress:
                progress("checking that no name is left in it")
            leaks, kept = verify(tmp, anon)
            if not leaks:
                break
        if leaks:
            raise BundleLeak(leaks)
        final = unique(dest)
        os.replace(_long(tmp), _long(final))
    finally:
        try:
            os.remove(_long(tmp))  # only our own unfinished .part file
        except OSError:
            pass
    key_path = None
    if anon is not None:
        key_path = unique(final.with_name(final.stem + names.BUNDLE_KEY_SUFFIX))
        key = {"what": f"The key for {final.name}. KEEP IT IN THE LAB and do not send it with the bundle: it "
                       "turns the pseudonyms in the bundle back into the lab's real names "
                       "(`ionomos bundle translate <this file> <a text file>`).",
               "bundle": final.name, "created": manifest["created"],
               **{g: dict(sorted(v.items())) for g, v in anon.key.items()},
               "kept_in_identifier_columns": [{"file": a, "text": o, "count": n} for a, o, n in kept]}
        with open(_long(key_path), "x", encoding="utf-8") as f:
            json.dump(key, f, indent=2, ensure_ascii=False)
    return Result(path=final, key_path=key_path, manifest=manifest, kept_identifiers=kept, problems=plan.problems)


# ------------------------------------------------------ words for people ----

LEVEL_TEXT = {
    "diagnose": "settings, logs and each job's status and search log: enough to see why a search or an analysis failed",
    "validate": "also the search's result tables and Ionomos' results, so the analysis can be run again elsewhere",
}


def summary_lines(plan: Plan) -> list[str]:
    """What the bundle will hold, in plain lines (the app's dialog and `ionomos bundle --dry-run`)."""
    groups: dict[str, list[Item]] = {}
    for it in plan.items:
        groups.setdefault(it.what, []).append(it)
    lines = [f"Level: {plan.opts.level} ({LEVEL_TEXT[plan.opts.level]})",
             "Names: " + ("replaced by pseudonyms; the key file stays next to the zip" if plan.opts.anonymise
                          else "NOT anonymised (the lab's real names)"),
             f"Jobs: {', '.join(PurePosixPath(j.arc).name for j in plan.jobs) or 'none'}", ""]
    for what, items in groups.items():
        size = sum(i.size for i in items)
        lines.append(f"  {len(items):>3} x {what}  ({_mb(size)})")
    for c in plan.capped:
        lines.append(f"  capped: {PurePosixPath(c['path']).name}: {c['why']}")
    for s in plan.skipped:
        lines.append(f"  LEFT OUT: {PurePosixPath(s['path']).name}: {s['why']}")
    for p in plan.problems:
        lines.append(f"  not found: {p}")
    lines += ["", f"Never included: {NEVER_SAID}.",
              f"Estimated size: {_mb(plan.total)} before compression (the zip is usually several times smaller)."]
    return lines


@dataclass
class Choice:
    """What the app's window has ticked; options() and targets() are what create() takes."""
    validate: bool = False
    anonymise: bool = True
    keep_conditions: bool = False
    jobs: tuple[int, ...] = ()
    note: str = ""

    def options(self) -> Options:
        return Options(level="validate" if self.validate else "diagnose", anonymise=self.anonymise,
                       keep_conditions=self.keep_conditions and self.anonymise, note=self.note)

    def targets(self) -> list[str]:
        return [str(j) for j in self.jobs]


def job_label(job: dict) -> str:
    """One line of the window's job list (recent_jobs() entries)."""
    return f"{job['id']:>4}   {job['status']:<8} {job['user']:<12} {job['method']:<8} {job['name']}"


def preselect(jobs: list[dict], job_id: int | None = None) -> list[int]:
    """Which lines of the job list start selected: the job the window was opened for, else none
    (= the running, waiting and last failed jobs, chosen when the zip is made)."""
    return [i for i, j in enumerate(jobs) if job_id is not None and j["id"] == job_id]


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 100_000 else f"{max(1, round(n / 1e3))} kB"


def saved_message(res: Result) -> str:
    """One sentence on what is in the zip and where the key belongs."""
    m = res.manifest
    what = ("settings, logs and the jobs' search logs" if m["level"] == "diagnose"
            else "settings, logs, the search's result tables and Ionomos' results")
    out = f"Saved {res.path.name} in {res.path.parent}: {what} of {len(m['jobs'])} job(s), never raw data."
    if res.key_path is not None:
        out += (f" Names are replaced by pseudonyms; {res.key_path.name} turns them back and stays in the lab "
                "(do not send it).")
    else:
        out += " It is NOT anonymised: it holds the lab's real names."
    if m["skipped"]:
        out += f" {len(m['skipped'])} file(s) were left out (see README.txt in the zip)."
    return out


# ------------------------------------------------------ the reader's side ----


def inspect(zip_path: Path) -> dict:
    """What a bundle holds: its BUNDLE.json plus the zip's own numbers. An old 'Report a problem' zip
    (no BUNDLE.json) is described from its file list."""
    zip_path = Path(zip_path)
    try:
        with zipfile.ZipFile(_long(zip_path)) as z:
            infos = z.infolist()
            try:
                manifest = json.loads(z.read(names.BUNDLE_MANIFEST).decode("utf-8"))
            except KeyError:
                manifest = None
            build = None
            if manifest is None and "build.json" in z.namelist():
                build = json.loads(z.read("build.json").decode("utf-8"))
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise BundleError(f"{zip_path} is not a readable bundle: {exc}") from exc
    out = {"zip": str(zip_path), "zip_bytes": _size(zip_path), "members": len(infos),
           "bytes": sum(i.file_size for i in infos), "manifest": manifest}
    if manifest is None:
        jobs = sorted({"/".join(i.filename.split("/")[:2]) for i in infos if i.filename.startswith(JOBS_DIR + "/")})
        out["legacy"] = {"build": build, "jobs": jobs, "files": [i.filename for i in infos]}
    return out


def inspect_text(zip_path: Path) -> str:
    d = inspect(zip_path)
    lines = [f"{d['zip']}", f"  {d['members']} files, {_mb(d['bytes'])} unpacked, {_mb(d['zip_bytes'])} as a zip"]
    m = d["manifest"]
    if m is None:
        b = d["legacy"]["build"] or {}
        lines.append(f"  no {names.BUNDLE_MANIFEST}: a report from before bundles (Ionomos {b.get('version', '?')})")
        lines += [f"  job: {j}" for j in d["legacy"]["jobs"]]
        return "\n".join(lines)
    a = m.get("anonymised") or {}
    lines += [f"  level: {m.get('level')}", f"  made by: {m.get('build_line')}  on {m.get('created')}",
              "  names: " + (f"anonymised ({', '.join(f'{n} {g}' for g, n in (a.get('replaced') or {}).items())}); "
                             f"conditions: {a.get('conditions')}; kept words: {', '.join(a.get('kept_words') or []) or '-'}"
                             if a.get("enabled") else "NOT anonymised")]
    lines.append("  jobs:" if m.get("jobs") else "  jobs: none")
    for j in m.get("jobs") or []:
        files = [f for f in m.get("files") or [] if f["path"].startswith(j["arc"] + "/")]
        lines.append(f"    {j['arc']}  status {j.get('status') or '?'}  method {j.get('method') or '?'}"
                     + (f" (analysed as {j['analysis_method']})" if j.get("analysis_method") not in (None, "", j.get("method"))
                        else "") + f"  {len(files)} files, {_mb(sum(f['bytes'] for f in files))}")
        if j.get("reason"):
            lines.append(f"      reason: {j['reason']}")
        if j.get("fasta"):
            f = j["fasta"]
            lines.append("      FASTA (not included): " + (f"{f['name']} is missing on the PC" if f.get("missing") else
                         f"{f['name']}, {_mb(f['bytes'])}, {f['entries']} entries ({f['decoys']} decoys), "
                         f"sha256 {f['sha256'][:16]}…"))
        if "reproducible" in j:
            lines.append("      analysis can be repeated: " + ("yes" if j["reproducible"] else
                         "NOT fully: " + ("; ".join(j.get("incomplete") or []) or "no result table in the bundle")))
        if j.get("order_preserved") is False:
            lines.append("      sample order changed by the pseudonyms: randomly imputed values will differ")
    for title, rows in (("capped", m.get("capped")), ("left out", m.get("skipped")), ("not found", m.get("not_found"))):
        for r in rows or []:
            lines.append(f"  {title}: " + (r if isinstance(r, str) else f"{r['path']}: {r['why']}"))
    lines.append(f"  never included: {m.get('never_included')}")
    return "\n".join(lines)


def _safe_member(name: str) -> PurePosixPath | None:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
        return None
    return p


def unpack(zip_path: Path, dest: Path) -> dict:
    """Lay a bundle out for `ionomos analyze`: <dest>/<experiment>/ per job, everything else in
    <dest>/_bundle/, and <dest>/config.yaml (the lab's settings with folders under <dest>/_lab).
    Nothing existing is replaced. Returns {"experiments", "config", "hash_mismatches", "notes"}."""
    info = inspect(zip_path)
    m = info["manifest"] or {}
    dest = Path(dest)
    roots = {j["arc"]: j.get("folder") or PurePosixPath(j["arc"]).name for j in m.get("jobs") or []}
    if info["manifest"] is None:
        roots = {j: PurePosixPath(j).name for j in info["legacy"]["jobs"]}
    hashes = {f["path"]: f["sha256"] for f in m.get("files") or []}
    targets: list[tuple[zipfile.ZipInfo, Path]] = []
    with zipfile.ZipFile(_long(Path(zip_path))) as z:
        for zi in z.infolist():
            rel = _safe_member(zi.filename)
            if rel is None or zi.is_dir():
                continue
            root = next((r for r in roots if zi.filename.startswith(r + "/")), None)
            if root is not None:
                out = dest / roots[root] / PurePosixPath(zi.filename[len(root) + 1:])
            else:
                out = dest / EXTRAS_DIR / rel
            targets.append((zi, out))
        taken = [str(t) for t in {dest / v for v in roots.values()} | {dest / EXTRAS_DIR, dest / "config.yaml"}
                 if os.path.lexists(_long(t))]
        if taken:
            raise BundleError("unpack never replaces anything, and these exist already: " + ", ".join(sorted(taken))
                              + ". Give an empty folder.")
        bad = []
        for zi, out in targets:
            os.makedirs(_long(out.parent), exist_ok=True)
            digest = hashlib.sha256()
            with z.open(zi) as src, open(_long(out), "xb") as f:
                for block in iter(lambda: src.read(1 << 20), b""):
                    f.write(block)
                    digest.update(block)
            if zi.filename in hashes and hashes[zi.filename] != digest.hexdigest():
                bad.append(zi.filename)
    notes: list[str] = []
    config = _unpacked_config(dest, notes)
    exps = [dest / v for v in roots.values()]
    for j in m.get("jobs") or []:
        if j.get("reproducible") is False:
            notes.append(f"{j.get('folder')}: the analysis cannot be fully repeated: "
                         + ("; ".join(j.get("incomplete") or []) or "no result table in the bundle"))
        if j.get("order_preserved") is False:
            notes.append(f"{j.get('folder')}: the pseudonyms changed the order of the sample names, so randomly "
                         "imputed values (and statistics that use them) will differ from the lab's")
    return {"experiments": exps, "config": config, "hash_mismatches": bad, "notes": notes, "level": m.get("level")}


def _unpacked_config(dest: Path, notes: list[str]) -> Path | None:
    """<dest>/config.yaml: the bundle's settings (methods, analysis defaults) with every folder under
    <dest>/_lab, so analysing here uses the lab's choices and touches nothing else. None if it will not load."""
    import yaml

    src = dest / EXTRAS_DIR / "config.yaml"
    if not src.is_file():
        notes.append("no config.yaml in the bundle: analyse without --config (Ionomos' default settings)")
        return None
    try:
        raw = yaml.safe_load(src.read_text(encoding="utf-8").replace(": ***", ': "***"'))
        if not isinstance(raw, dict):
            raise ValueError("not a mapping")
        lab = dest / LAB_DIR
        for sub in ("inbox", "users", "workflows", "fasta", "logs"):
            (lab / sub).mkdir(parents=True, exist_ok=True)
        raw["paths"] = {"inbox": (lab / "inbox").as_posix(), "users_root": (lab / "users").as_posix(),
                        "fragpipe_exe": (lab / "fragpipe.exe").as_posix(),
                        "workflow_dir": (lab / "workflows").as_posix(), "fasta_dir": (lab / "fasta").as_posix(),
                        "database": (lab / "ionomos.db").as_posix(), "log_dir": (lab / "logs").as_posix()}
        for block in ("notify", "assistant"):  # nothing unpacked may send or ask anything
            raw.pop(block, None)
        raw.setdefault("gui", {})
        if isinstance(raw["gui"], dict):
            raw["gui"]["enabled"] = False
        if isinstance(raw.get("users"), dict):
            raw["users"].pop("learned_aliases_file", None)
        out = dest / "config.yaml"
        with open(out, "x", encoding="utf-8") as f:
            f.write("# Made by `ionomos bundle unpack` from the lab's settings: methods and analysis defaults are\n"
                    "# the lab's, every folder is a stand-in under _lab/.\n")
            yaml.safe_dump(raw, f, sort_keys=False, allow_unicode=True)
        from ionomos import config as config_mod

        config_mod.load(out, check_paths=False)
        return out
    except Exception as exc:  # noqa: BLE001 - the bundle is still usable without it
        notes.append(f"the bundle's config.yaml does not load here ({exc}): analyse without --config "
                     "(Ionomos' default settings; the lab's analysis defaults are not applied)")
        return None


def translate(key_path: Path, text: str) -> str:
    """`text` with the bundle's pseudonyms turned back into the lab's names (the lab's side, with its key file)."""
    try:
        key = json.loads(Path(key_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(f"{key_path} is not a bundle key file: {exc}") from exc
    back: dict[str, str] = {}
    for group in ("experiments", "folders", "users", "computers", "names", "emails", "addresses"):
        for p, orig in (key.get(group) or {}).items():
            back[p.casefold()] = orig
    if not back:
        return text
    alts = sorted(back, key=len, reverse=True)
    rx = re.compile(r"(?<![^\W_])(?:" + "|".join(re.escape(a) for a in alts) + r")(?![^\W_])", re.IGNORECASE)
    return rx.sub(lambda m: back[m.group().casefold()], text)


# ------------------------------------------------------------------ cli ----

CLI_HELP = """\
ionomos bundle [JOB | FOLDER ...] [--level diagnose|validate] [--out DIR] [--no-anonymise] [--keep-conditions]
    Save a zip for troubleshooting (diagnose) or for re-running the analysis elsewhere (validate) on the
    Desktop, or in --out. Nothing is sent anywhere. JOB is a job number, FOLDER an experiment folder; with
    neither, the running, waiting and last failed jobs (validate: also the last finished one).
    Names are replaced by pseudonyms unless --no-anonymise; the key file is saved next to the zip and
    stays in the lab.
ionomos bundle inspect ZIP           what a bundle holds (level, version, jobs, sizes, what was capped)
ionomos bundle unpack ZIP DIR        lay it out: DIR/<experiment>/ per job, DIR/config.yaml, the rest in DIR/_bundle/
                                     then: ionomos --config DIR/config.yaml analyze DIR/<experiment>
ionomos bundle translate KEY [FILE]  the lab's side: turn the pseudonyms in FILE (or stdin) back into names
"""


def add_parser(sub):
    import argparse

    p = sub.add_parser("bundle", help="save a zip for troubleshooting / validation (anonymised; nothing is sent); "
                                      "inspect / unpack one", description=CLI_HELP,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", nargs="*", metavar="JOB|FOLDER",
                   help="job numbers or experiment folders; or: inspect ZIP | unpack ZIP DIR | translate KEY [FILE]")
    p.add_argument("--level", choices=LEVELS, default="diagnose",
                   help="diagnose: settings, logs, job status and search logs (default). validate: also the "
                        "search's result tables and results/, so the analysis can be repeated")
    p.add_argument("--out", metavar="DIR", help="folder for the zip and its key file (default: the Desktop)")
    p.add_argument("--no-anonymise", "--no-anonymize", dest="no_anonymise", action="store_true",
                   help="keep the lab's real names (secrets are still removed)")
    p.add_argument("--keep-conditions", action="store_true",
                   help="keep every condition / sample word readable, not only the control and role words")
    p.add_argument("--max-mb", type=float, default=Options.max_mb, help="size limit of one bundle before "
                   "compression (default %(default)s); what does not fit is left out and listed")
    p.add_argument("--psm-mb", type=float, default=Options.psm_mb,
                   help="a PSM-level table above this size is row-sampled (default %(default)s)")
    p.add_argument("--note", default="", help="a sentence on what happened; goes in as note.txt")
    p.add_argument("--dry-run", action="store_true", help="list what would go in, write nothing")
    return p


def run_cli(args) -> int:
    import sys

    t = list(args.targets)
    try:
        if t[:1] == ["inspect"]:
            if len(t) != 2:
                print("usage: ionomos bundle inspect ZIP", file=sys.stderr)
                return 2
            print(inspect_text(Path(t[1])))
            return 0
        if t[:1] == ["unpack"]:
            if len(t) != 3:
                print("usage: ionomos bundle unpack ZIP DIR", file=sys.stderr)
                return 2
            out = unpack(Path(t[1]), Path(t[2]))
            print(f"unpacked into {Path(t[2])}")
            for bad in out["hash_mismatches"]:
                print(f"  DAMAGED (hash differs from {names.BUNDLE_MANIFEST}): {bad}")
            for n in out["notes"]:
                print(f"  note: {n}")
            cfg = f'--config "{out["config"]}" ' if out["config"] else ""
            for exp in out["experiments"]:
                print(f'  ionomos {cfg}analyze "{exp}"' if out["level"] == "validate" else f"  {exp}")
            if out["level"] != "validate":
                print("  (a diagnose bundle holds no result tables: there is nothing to analyse)")
            return 1 if out["hash_mismatches"] else 0
        if t[:1] == ["translate"]:
            if len(t) not in (2, 3):
                print("usage: ionomos bundle translate KEY [FILE]", file=sys.stderr)
                return 2
            text = Path(t[2]).read_text(encoding="utf-8", errors="replace") if len(t) == 3 else sys.stdin.read()
            sys.stdout.write(translate(Path(t[1]), text))
            return 0
        opts = Options(level=args.level, anonymise=not args.no_anonymise, keep_conditions=args.keep_conditions,
                       max_mb=args.max_mb, psm_mb=args.psm_mb, note=args.note)
        if args.dry_run:
            plan = collect(Path(args.config), t, opts)
            print("\n".join(summary_lines(plan)))
            return 2 if plan.problems else 0
        res = create(Path(args.config), t, opts, dest_dir=Path(args.out) if args.out else None,
                     progress=None)
    except BundleError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(res.message)
    print(f"bundle: {res.path}")
    if res.key_path is not None:
        print(f"key (keep in the lab): {res.key_path}")
        print("leak check: the finished zip was searched for every original name; none found")
    for arc, text, n in res.kept_identifiers:
        print(f"  kept as a protein / gene identifier: {text!r} x{n} in {arc}")
    for s in res.manifest["skipped"]:
        print(f"  left out: {s['path']}: {s['why']}")
    for c in res.manifest["capped"]:
        print(f"  capped: {c['path']}: {c['why']}")
    for prob in res.problems:
        print(f"  not found: {prob}", file=sys.stderr)
    return 2 if res.problems else 0
