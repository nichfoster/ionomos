"""
FASTA files from outside the PC: download a proteome from UniProt, make it FragPipe-ready (decoys + contaminants),
say how old a FASTA is against UniProt's current release, and fetch FragPipe's stock workflows from its source.

No Tk; standard library only (urllib, gzip, zipfile, subprocess). docs/FASTA.md is the spec; it says which facts
below were checked against UniProt's, Philosopher's and FragPipe's own documentation or source, and which are assumed.

    search_proteomes("human")                    -> SearchResult(rows=[{id, organism, taxon, proteins}], notes, error)
    download_proteome("UP000005640", dest_dir)   -> DownloadResult(path=<dest>/UP000005640_9606.fasta, release, ...)
    prepare_for_fragpipe(fasta, dest_dir)        -> PrepareResult(path=<dest>/2026-10-14-decoys-reviewed-contam-
                                                    UP000005640_9606.fasta.fas, method, decoys, contaminants, ...)
    uniprot_release()                            -> {'release': '2026_03', 'date': '2026-09-02'} | None (offline)
    staleness("2026-01-30-decoys-…fas", rel)     -> "made 2026-01-30; UniProt is at 2026_03 (2026-09-02), …"
    stock_workflows("24.0")                      -> StockList(names=['Basic-Search', …, 'TMT10-MS3', …], notes, error)
    fetch_stock_workflow("24.0", "TMT10-MS3", d) -> FetchResult(path=<d>/TMT10-MS3.workflow, url, notes, error)
    find_philosopher([fragpipe_root])            -> <root>/tools/Philosopher/philosopher-v….exe | None
    fragpipe_fasta_name(src_name, ...)           -> the name FragPipe's Database tab (Philosopher) gives the result

Rules every function keeps (D90):
- Network calls are made only when a function is called (never at import time), always with a timeout, and never
  when the environment variable IONOMOS_OFFLINE is set (the tests set it). A network problem becomes a plain-words
  `error` / note in the result; nothing here raises to the caller for a network problem.
- Long work takes `progress(message, done, total)` (total None when unknown) and `stop` (a threading.Event): a
  stopped call returns with error "stopped" and leaves no partial file behind.
- Nothing is overwritten. A file is written as `<name>.part`, then renamed; an existing name gets `-2`, `-3`, … .
- The tests replace `urlopen` in this module (`fastasource.urlopen`); there is no real network in a test.
"""
from __future__ import annotations

import gzip
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import date as Date
from datetime import datetime
from pathlib import Path

from ionomos import __version__

urlopen = urllib.request.urlopen  # the one way out to the network; tests replace it

UNIPROT = "https://rest.uniprot.org"
FRAGPIPE_API = "https://api.github.com/repos/Nesvilab/FragPipe/contents"
FRAGPIPE_RAW = "https://raw.githubusercontent.com/Nesvilab/FragPipe"
# FragPipe 23.0 and later keep the stock workflows in workflows/; 22.0 and earlier in MSFragger-GUI/workflows/
WORKFLOW_FOLDERS = ("workflows", "MSFragger-GUI/workflows")
# Philosopher's contaminant list as a zip in its source; `philosopher database --contam` adds crap-gpmdb.fas from it
PHILOSOPHER_CONTAMINANTS_URL = "https://raw.githubusercontent.com/Nesvilab/philosopher/master/lib/dat/cRAPS.zip"
PHILOSOPHER_CONTAMINANTS_MEMBER = "crap-gpmdb.fas"
CRAP_URL = "https://ftp.thegpm.org/fasta/cRAP/crap.fasta"  # the GPM's cRAP; its server failed TLS on 2026-10-14
CONTAM_PREFIX = "contam_"  # what `philosopher database --contamprefix` puts before a contaminant's header
USER_AGENT = f"Ionomos/{__version__} (FASTA download; https://github.com/nichfoster/ionomos)"
OFFLINE_ENV = "IONOMOS_OFFLINE"
CHUNK = 1 << 16
PHILOSOPHER_TIMEOUT_S = 900  # a human proteome takes Philosopher well under a minute; isoforms of a big one, minutes

Progress = Callable[[str, int, "int | None"], None]
_MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"), 1)}
_PROTEOME_RE = re.compile(r"^UP\d{9,}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-]{0,120}$")
_DATE_IN_NAME = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


class Stopped(Exception):
    """Raised inside this module when `stop` is set; turned into error "stopped" before it reaches a caller."""


class _TooLong(Exception):
    """A download ran past its time limit."""


# ------------------------------------------------------------------ results --


@dataclass
class DownloadResult:
    proteome: str
    path: Path | None = None          # the plain FASTA as UniProt sent it (no decoys yet)
    taxon: str = ""
    organism: str = ""
    reviewed: bool = True
    isoforms: bool = False
    entries: int = 0
    expected: int | None = None       # UniProt's count for the same query (X-Total-Results)
    release: str = ""                 # X-UniProt-Release, e.g. 2026_03
    release_date: str = ""            # X-UniProt-Release-Date as YYYY-MM-DD
    url: str = ""
    bytes: int = 0
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.path is not None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path) if self.path else ""
        return d


@dataclass
class PrepareResult:
    source: Path
    path: Path | None = None          # the FragPipe-ready FASTA
    method: str = ""                  # "philosopher" | "python"
    decoy_tag: str = "rev_"
    entries: int = 0
    targets: int = 0                  # entries that are not decoys (contaminants included)
    decoys: int = 0
    contaminants: int = 0             # target entries starting with contam_
    contaminant_source: str = ""      # where the contaminants came from, in words
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.path is not None

    @property
    def decoy_share(self) -> float:
        return self.decoys / self.entries if self.entries else 0.0

    def summary(self) -> str:
        if not self.ok:
            return f"not prepared: {self.error}"
        return (f"{self.path.name}: {self.entries:,} entries = {self.targets - self.contaminants:,} proteins + "
                f"{self.contaminants:,} contaminants ({CONTAM_PREFIX}) + {self.decoys:,} decoys ({self.decoy_tag}, "
                f"{100 * self.decoy_share:.1f} %); made with {self.method}")

    def as_dict(self) -> dict:
        d = asdict(self)
        d["source"], d["path"] = str(self.source), str(self.path) if self.path else ""
        d["decoy_share"] = round(self.decoy_share, 4)
        return d


@dataclass
class SearchResult:
    query: str
    rows: list[dict] = field(default_factory=list)  # {id, organism, taxon, proteins}
    total: int | None = None
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class StockList:
    version: str
    tag: str = ""
    folder: str = ""
    names: list[str] = field(default_factory=list)  # without ".workflow"
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FetchResult:
    name: str
    path: Path | None = None
    url: str = ""
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.path is not None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path) if self.path else ""
        return d


# ------------------------------------------------------------------ network --


def offline_reason() -> str:
    """Why no network call may be made now ('' when it may)."""
    return "network use is off on this computer (IONOMOS_OFFLINE is set)" if os.environ.get(OFFLINE_ENV) else ""


def _request(url: str, accept: str = "") -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if accept:
        headers["Accept"] = accept
    return urllib.request.Request(url, headers=headers)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def _net_error(exc: BaseException, url: str) -> str:
    """A network failure in plain words, with what to do."""
    host = _host(url)
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            detail = exc.read(400).decode("utf-8", "replace").strip().splitlines()[0][:200]
        except Exception:  # noqa: BLE001 - the body is only a hint
            pass
        if exc.code == 404:
            return f"{host} has no such file (HTTP 404)"
        if exc.code in (403, 429):
            return (f"{host} refused for now (HTTP {exc.code}: too many requests from this network); "
                    f"try again in an hour")
        return f"{host} answered HTTP {exc.code}" + (f": {detail}" if detail else "")
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, TimeoutError) or "timed out" in str(reason):
        return f"{host} did not answer in time; check the internet connection and try again"
    return (f"could not reach {host} ({reason}); check this computer's internet connection, or download the file "
            f"on another computer and use `ionomos fasta prepare` on it")


def _check_stop(stop: threading.Event | None) -> None:
    if stop is not None and stop.is_set():
        raise Stopped()


def _say(progress: Progress | None, message: str, done: int = 0, total: int | None = None) -> None:
    if progress is not None:
        try:
            progress(message, done, total)
        except Exception:  # noqa: BLE001 - a broken progress display must not break a download
            pass


def _uniprot_date(text: str) -> str:
    """'02-September-2026' (X-UniProt-Release-Date) -> '2026-09-02'; '' when it can't be read. No locale."""
    m = re.match(r"^\s*(\d{1,2})-([A-Za-z]+)-(\d{4})\s*$", text or "")
    if not m or m.group(2).lower() not in _MONTHS:
        m2 = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", text or "")
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}" if m2 else ""
    return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _release_from(headers) -> dict:
    rel = (headers.get("X-UniProt-Release") or "").strip()
    raw = (headers.get("X-UniProt-Release-Date") or "").strip()
    return {"release": rel, "date": _uniprot_date(raw), "date_text": raw} if rel else {}


def _total_from(headers) -> int | None:
    try:
        return int((headers.get("X-Total-Results") or "").strip())
    except ValueError:
        return None


def _get_json(url: str, timeout: float, accept: str = "application/json"):
    with urlopen(_request(url, accept), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")), r.headers


def _uniprot_query(proteome: str, reviewed: bool) -> str:
    return f"(proteome:{proteome})" + (" AND (reviewed:true)" if reviewed else "")


def _uniprot_url(kind: str, query: str, **params) -> str:
    return f"{UNIPROT}/uniprotkb/{kind}?" + urllib.parse.urlencode({"query": query, **params})


# ------------------------------------------------------------------ release --


def uniprot_release(timeout: float = 10, *, notes: list[str] | None = None) -> dict | None:
    """UniProt's current release: {'release': '2026_03', 'date': '2026-09-02', 'date_text': '02-September-2026'}.

    One small request (no entries are sent: size=0); the release is in UniProt's response headers. Offline, or
    any other failure: None, and the reason is appended to `notes` when given. Never raises."""
    why = offline_reason()
    url = _uniprot_url("search", _uniprot_query("UP000005640", True), format="list", size="0")
    if not why:
        try:
            with urlopen(_request(url), timeout=timeout) as r:
                rel = _release_from(r.headers)
            if rel:
                return rel
            why = "UniProt did not say which release it is at (no X-UniProt-Release header)"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            why = _net_error(exc, url)
    if notes is not None:
        notes.append(f"UniProt's current release is unknown: {why}")
    return None


def name_date(value) -> Date | None:
    """The date in a FragPipe FASTA name ('2026-01-30-decoys-contam-…' -> 2026-01-30), a 'YYYY-MM-DD' text, a
    date or datetime, or a path; None when there is none."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    if value is None:
        return None
    m = _DATE_IN_NAME.search(Path(str(value)).name)
    if not m:
        return None
    try:
        return Date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _months(a: Date, b: Date) -> int:
    return max(0, (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0))


def staleness(fasta_name_date, release: dict | None, *, today: Date | None = None) -> str:
    """How old a FASTA is, in one sentence: its date (from the FragPipe-style name, or given) against UniProt's
    current release (`uniprot_release()`), or against today when the release is unknown.

    UniProt publishes no list of past release dates in its API, so this never counts releases: it says whether the
    current one came out after the file was made, and how many months later."""
    today = today or Date.today()
    made = name_date(fasta_name_date)
    if made is None:
        return ("the file name has no date (FragPipe names its FASTAs YYYY-MM-DD-decoys-…), so its age is unknown; "
                "check the file's date in Explorer")
    age = _months(made, today)
    ago = "this month" if age == 0 else f"{age} month{'s' if age != 1 else ''} ago"
    rel_date = name_date((release or {}).get("date"))
    if not release or rel_date is None:
        return f"made {made.isoformat()} ({ago}); UniProt's current release is unknown (no connection)"
    rel = f"{release.get('release', '?')} ({rel_date.isoformat()})"
    if made >= rel_date:
        return f"made {made.isoformat()} ({ago}); UniProt is at {rel}, released before it: up to date"
    gap = _months(made, rel_date)
    later = f"{gap} month{'s' if gap != 1 else ''} after it" if gap else "after it"
    return (f"made {made.isoformat()} ({ago}); UniProt is at {rel}, released {later}: at least one release behind. "
            f"For a new project, download it again; keep this file for experiments already searched with it")


# ------------------------------------------------------------------ search --


def search_proteomes(organism_text: str, *, limit: int = 5, reference_only: bool = True, timeout: float = 15
                     ) -> SearchResult:
    """A few UniProt proteomes for what a lab member types: "human", "mouse", "Mus musculus", "10090",
    "UP000005640". Rows: {'id', 'organism', 'taxon', 'proteins'} (proteins = every UniProt entry of the
    proteome, reviewed or not). Reference proteomes only unless `reference_only=False`."""
    text = " ".join(str(organism_text or "").split())
    res = SearchResult(query=text)
    if not text:
        res.error = "type an organism (human, mouse, yeast …), a taxon number (9606) or a proteome id (UP000005640)"
        return res
    if _PROTEOME_RE.match(text.upper()):
        q = f"upid:{text.upper()}"
    elif text.isdigit():
        q = f"organism_id:{text}"
    else:
        q = "(" + text.replace('"', " ") + ")"
    if reference_only and not q.startswith("upid:"):
        q += " AND (proteome_type:REFERENCE)"
    url = f"{UNIPROT}/proteomes/search?" + urllib.parse.urlencode(
        {"query": q, "format": "tsv", "size": str(max(1, min(int(limit), 25))),
         "fields": "upid,organism,organism_id,protein_count"})
    why = offline_reason()
    if why:
        res.error = why
        return res
    try:
        with urlopen(_request(url, "text/plain"), timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            res.total = _total_from(r.headers)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        res.error = _net_error(exc, url)
        return res
    lines = [ln for ln in body.splitlines() if ln.strip()]
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        try:
            n = int(cols[3])
        except ValueError:
            n = 0
        res.rows.append({"id": cols[0].strip(), "organism": cols[1].strip(), "taxon": cols[2].strip(),
                         "proteins": n})
    # "mouse" -> Mus musculus (Mouse) before Mus caroli (Ryukyu mouse): UniProt's own order otherwise
    want = f"({text.lower()})"
    res.rows.sort(key=lambda row: want not in row["organism"].lower())
    if not res.rows:
        res.notes.append(f"UniProt has no {'reference ' if reference_only else ''}proteome for '{text}'"
                         + ("; try the Latin name, or all proteomes" if reference_only else ""))
    return res


# ---------------------------------------------------------------- download --


def unique_path(path: Path) -> Path:
    """`path` when free, else the same name with -2, -3, … before the extension(s) (.fasta.fas counts as one)."""
    path = Path(path)
    if not path.exists() and not path.with_name(path.name + ".part").exists():
        return path
    name = path.name
    for ext in (".fasta.fas", ".fasta.gz", ".fasta", ".fas", ".fa", ".workflow"):
        if name.lower().endswith(ext) and len(name) > len(ext):
            stem, suffix = name[: -len(ext)], name[-len(ext):]
            break
    else:
        stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        cand = path.with_name(f"{stem}-{n}{suffix}")
        if not cand.exists() and not cand.with_name(cand.name + ".part").exists():
            return cand
        n += 1


def _commit(part: Path, final: Path) -> Path:
    """Give a finished .part file its name, never over an existing file."""
    if final.exists():
        final = unique_path(final)
    os.replace(part, final)
    return final


def download_proteome(proteome_id: str, dest_dir: Path, *, reviewed: bool = True, isoforms: bool = False,
                      taxon: str | None = None, progress: Progress | None = None,
                      stop: threading.Event | None = None, timeout: float = 60, time_limit_s: float = 3600,
                      fasta_dir: Path | None = None) -> DownloadResult:
    """Download a UniProt proteome as a plain FASTA, the way Philosopher's `database --id` asks UniProt for it.

    Two requests: UniProt's count for the query (size=0: the release and X-Total-Results), then the entries
    from the stream endpoint, gzip-compressed, written to `<dest_dir>/<UP…>_<taxon>.fasta` (the name UniProt's
    FTP gives a reference proteome, and the base of the lab's FragPipe FASTA). The number of entries is
    checked against UniProt's count. The result has no decoys: `prepare_for_fragpipe` adds them."""
    pid = str(proteome_id or "").strip().upper()
    res = DownloadResult(proteome=pid, reviewed=reviewed, isoforms=isoforms, taxon=str(taxon or ""))
    if not _PROTEOME_RE.match(pid):
        res.error = (f"'{proteome_id}' is not a UniProt proteome id (they look like UP000005640); "
                     f"`ionomos fasta search human` finds one")
        return res
    why = offline_reason()
    if why:
        res.error = why
        return res
    dest_dir = Path(dest_dir)
    if " " in str(dest_dir):
        res.notes.append(f"{dest_dir} has a space in it; FragPipe can't use a FASTA there. Pick "
                         f"{_folder_hint(fasta_dir)}")
    query = _uniprot_query(pid, reviewed)
    iso = "true" if isoforms else "false"
    count_url = _uniprot_url("search", query, format="fasta", includeIsoform=iso, size="0")
    res.url = _uniprot_url("stream", query, format="fasta", compressed="true", includeIsoform=iso)
    started = time.monotonic()
    gz_part: Path | None = None
    fa_part: Path | None = None
    try:
        _say(progress, f"asking UniProt about {pid}")
        try:
            with urlopen(_request(count_url), timeout=timeout) as r:
                res.expected = _total_from(r.headers)
                rel = _release_from(r.headers)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            res.error = _net_error(exc, count_url)
            return res
        res.release, res.release_date = rel.get("release", ""), rel.get("date", "")
        if res.expected == 0:
            res.error = (f"UniProt has no {'reviewed ' if reviewed else ''}entries for proteome {pid}"
                         + ("; try without 'reviewed only' (--unreviewed)" if reviewed else ""))
            return res
        _check_stop(stop)
        dest_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{pid}-", suffix=".fasta.gz.part", dir=dest_dir)
        os.close(fd)
        gz_part = Path(tmp)
        _say(progress, f"downloading {pid} from UniProt ({res.expected or '?'} entries)", 0, None)
        try:
            with urlopen(_request(res.url), timeout=timeout) as r, open(gz_part, "wb") as out:
                rel = _release_from(r.headers) or rel
                res.release, res.release_date = rel.get("release", res.release), rel.get("date", res.release_date)
                while True:
                    _check_stop(stop)
                    if time.monotonic() - started > time_limit_s:
                        raise _TooLong(f"the download took longer than {time_limit_s:.0f} s; try again, or "
                                       f"later (UniProt may be busy)")
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    res.bytes += len(chunk)
                    _say(progress, f"downloading {pid}", res.bytes, None)
        except _TooLong as exc:
            res.error = str(exc)
            return res
        except (urllib.error.URLError, OSError, ValueError) as exc:
            res.error = _net_error(exc, res.url)
            return res
        _say(progress, "unpacking", res.bytes, res.bytes)
        fd, tmp = tempfile.mkstemp(prefix=f".{pid}-", suffix=".fasta.part", dir=dest_dir)
        os.close(fd)
        fa_part = Path(tmp)
        taxa: Counter = Counter()
        organisms: dict[str, str] = {}
        try:
            with open(gz_part, "rb") as raw:
                head = raw.read(2)
            opener = gzip.open if head == b"\x1f\x8b" else open  # a server may also send it unpacked
            with opener(gz_part, "rb") as src, open(fa_part, "wb") as out:
                for line in src:
                    _check_stop(stop)
                    if line.startswith(b">"):
                        res.entries += 1
                        if res.entries <= 2000:
                            m = re.search(rb" OX=(\d+)", line)
                            if m:
                                tx = m.group(1).decode()
                                taxa[tx] += 1
                                if tx not in organisms:
                                    o = re.search(rb" OS=(.+?)(?: OX=| GN=| PE=| SV=|$)", line)
                                    organisms[tx] = o.group(1).decode("utf-8", "replace").strip() if o else ""
                    out.write(line)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            res.error = f"UniProt's file could not be unpacked ({exc}); the download was probably cut off: try again"
            return res
        if res.entries == 0:
            res.error = f"UniProt sent no entries for {pid}"
            return res
        if res.expected is not None and res.entries != res.expected:
            res.error = (f"got {res.entries:,} entries but UniProt counts {res.expected:,}; the download is "
                         f"incomplete or UniProt changed release meanwhile: try again")
            return res
        if res.expected is None:
            res.notes.append("UniProt did not give a count to check the download against")
        if taxa:
            top = taxa.most_common(1)[0][0]
            res.taxon = res.taxon or top
            res.organism = organisms.get(top, "")
        if not res.taxon:
            res.notes.append("no taxon (OX=) in the headers; the file is named after the proteome only")
        final = dest_dir / (f"{pid}_{res.taxon}.fasta" if res.taxon else f"{pid}.fasta")
        res.path = _commit(fa_part, final)
        fa_part = None
        if res.path.name != final.name:
            res.notes.append(f"{final.name} already exists; the new file is {res.path.name}")
        _say(progress, f"saved {res.path.name}", res.bytes, res.bytes)
        return res
    except Stopped:
        res.error = "stopped"
        res.path = None
        return res
    finally:
        for p in (gz_part, fa_part):
            if p is not None:
                try:
                    p.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------- prepare --


def fragpipe_fasta_name(src_name: str, *, day: Date | None = None, decoys: bool = True, contam: bool = True,
                        tags: Iterable[str] = ()) -> str:
    """The name Philosopher (`database`, as FragPipe's Database tab runs it) gives its output:
    `<YYYY-MM-DD>[-decoys][-<tags>…][-contam]-<base>.fas`, base = the input file's name. Tags are Philosopher's
    `reviewed` / `isoforms` (it adds them when it downloads itself). The lab's file
    `2026-01-30-decoys-contam-UP000005640_9606.fasta.fas` is this with base `UP000005640_9606.fasta`."""
    day = day or Date.today()
    parts = [day.isoformat()]
    if decoys:
        parts.append("decoys")
    parts += [t for t in tags if t]
    if contam:
        parts.append("contam")
    return "-".join(parts) + f"-{Path(src_name).name}.fas"


BOM = b"\xef\xbb\xbf"  # UTF-8 byte-order mark: Notepad's and Excel's "UTF-8 with BOM" put it before the first '>'
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
HEADER_MAX = 1 << 16  # a header is read up to this many bytes (a real one is under 1 kB): bounded memory on any file


def fasta_headers(fh, stop: threading.Event | None = None, deadline: float | None = None, *, chunk: int = CHUNK,
                  max_header: int = HEADER_MAX):
    """Every header line (without '>', without its line end) of a FASTA opened in binary mode, read in chunks of
    `chunk` bytes: never split into lines or decoded, a leading UTF-8 byte-order mark skipped, linear time and
    bounded memory on any file (one with no line break at all, a header of a gigabyte: at most `HEADER_MAX` bytes of
    a header are kept). Raises TimeoutError when `stop` is set or `deadline` (time.monotonic()) has passed.
    The one header reader of `_scan`, `library.describe_fasta` and `fragpipe.fasta_info`."""
    head: bytearray | None = None  # a header that runs on into the next chunk
    at_start = True                # the next byte read begins a line
    first = True
    while True:
        if (stop is not None and stop.is_set()) or (deadline is not None and time.monotonic() > deadline):
            raise TimeoutError
        buf = fh.read(max(chunk, len(BOM)) if first else chunk)
        if not buf:
            break
        pos, n = 0, len(buf)
        if first:
            first = False
            if buf.startswith(BOM):
                pos = len(BOM)
        if head is not None:
            j = buf.find(b"\n")
            if len(head) < max_header:
                head += buf[:min(n if j < 0 else j, max_header - len(head))]
            if j < 0:
                continue
            yield bytes(head).rstrip(b"\r")
            head, pos, at_start = None, j + 1, True
        while pos < n:
            if at_start and buf[pos] == 0x3E:  # '>' at the start of a line
                start = pos + 1
            else:
                i = buf.find(b"\n>", pos)
                if i < 0:
                    break
                start = i + 2
            j = buf.find(b"\n", start)
            if j < 0:
                head = bytearray(buf[start:start + max_header])
                break
            yield buf[start:min(j, start + HEADER_MAX)].rstrip(b"\r")
            pos, at_start = j + 1, True
        if head is None and pos < n:
            at_start = buf.endswith(b"\n")  # only one byte is carried over, never the chunk
    if head is not None:
        yield bytes(head).rstrip(b"\r")


def _read_entries(fh, keep: Callable[[bytes], bool] | None = None) -> list[tuple[bytes, bytes]]:
    """[(header without '>', sequence)] the way Philosopher reads a FASTA (fas.ParseFile2): tabs in a header become
    spaces, sequence lines are stripped and joined, line ends may be \\n or \\r\\n; a leading UTF-8 byte-order mark is
    skipped. Read in pieces of at most CHUNK bytes: text that belongs to no kept entry (before the first header, or
    in an entry turned down by `keep(header)`, which sees at most the first HEADER_MAX bytes of the header) is never
    held in memory, so a file without line breaks costs no more than its reading."""
    out: list[tuple[bytes, bytearray]] = []
    seq: bytearray | None = None   # the sequence of the entry being kept
    line: bytearray | None = None  # the line being gathered: a header, or a sequence line of a kept entry
    header = False
    decided = True                 # whether `keep` has had its say on the header being gathered
    at_start = True
    first = fh.readline(max(CHUNK, len(BOM)))
    first = first[len(BOM):] if first.startswith(BOM) else first
    for piece in itertools.chain([first] if first else [], iter(lambda: fh.readline(CHUNK), b"")):
        if at_start:
            header = piece.startswith(b">")
            decided = keep is None or not header
            if header:
                seq = None
            line = bytearray() if header or seq is not None else None
        if line is not None:
            line += piece
        at_start = piece.endswith(b"\n")
        if not decided and (at_start or len(line) > HEADER_MAX):
            decided = True
            if not keep(bytes(line[1:HEADER_MAX + 1]).rstrip(b"\r\n")):
                line = None
        if at_start and line is not None:
            seq = _end_line(out, seq, line, header)
            line = None
    if line is not None and not decided:
        line = line if keep(bytes(line[1:HEADER_MAX + 1]).rstrip(b"\r\n")) else None
    if line is not None:
        _end_line(out, seq, line, header)
    return [(h, bytes(s)) for h, s in out]


def _end_line(out: list, seq: bytearray | None, line: bytearray, header: bool) -> bytearray | None:
    if header:
        out.append((bytes(line).rstrip(b"\r\n")[1:].replace(b"\t", b" "), bytearray()))
        return out[-1][1]
    seq.extend(line.strip())
    return seq


def read_fasta(path: Path) -> list[tuple[bytes, bytes]]:
    """[(header without '>', sequence)] the way Philosopher reads a FASTA (`_read_entries`). Text before the first
    header belongs to no entry and is not returned: `prepare_for_fragpipe` refuses such a file first
    (`text_before_first_header`)."""
    with open(path, "rb") as fh:
        return _read_entries(fh)


def text_before_first_header(path: Path) -> tuple[int, str]:
    """(line number, its start) of the first text that comes before the file's first header, or (0, '') when the
    first text (after a UTF-8 byte-order mark and blank lines) is a '>' header or the file has no text. Such text
    belongs to no entry; Philosopher and a plain reader drop it, and with it the first protein when it is that
    protein's header behind a stray character. Reads only up to that first text."""
    lines = 0
    indent = b""  # blanks already on the current line (" >sp|…" is not a header)
    with open(path, "rb") as fh:
        first = True
        while True:
            buf = fh.read(CHUNK)
            if not buf:
                return 0, ""
            if first:
                first = False
                buf = buf[len(BOM):] if buf.startswith(BOM) else buf
            text = buf.lstrip()
            blank = buf[:len(buf) - len(text)]
            lines += blank.count(b"\n")
            nl = blank.rfind(b"\n")
            indent = blank[nl + 1:] if nl >= 0 else indent + blank[:80]
            if text:
                if text.startswith(b">") and not indent:
                    return 0, ""
                start = (indent + text[:80]).split(b"\n")[0].rstrip()
                return lines + 1, start.decode("utf-8", "replace")


def _parse_fasta_bytes(data: bytes) -> list[tuple[bytes, bytes]]:
    if data.startswith(BOM):
        data = data[len(BOM):]
    out: list[tuple[bytes, bytearray]] = []
    for line in data.splitlines():
        if line.startswith(b">"):
            out.append((line[1:].replace(b"\t", b" "), bytearray()))
        elif out:
            out[-1][1].extend(line.strip())
    return [(h, bytes(s)) for h, s in out]


def reverse_like_philosopher(seq: bytes) -> bytes:
    """A decoy sequence exactly as Philosopher's default decoy mode makes it (dat.reverseSeq): reversed, a first M
    kept in place. Its loop stops at len/2, so with a first M and an odd length the middle pair is not swapped;
    copied as it is, so the two methods give the same file."""
    r = bytearray(seq)
    i, j = (1 if seq.startswith(b"M") else 0), len(r) - 1
    while i < len(r) // 2:
        r[i], r[j] = r[j], r[i]
        i, j = i + 1, j - 1
    return bytes(r)


def _protein_id(header: bytes) -> bytes:
    """Philosopher's extractProteinID: the text between the first two '|', else the whole header."""
    first = header.find(b"|")
    if first >= 0:
        second = header.find(b"|", first + 1)
        if second >= 0:
            return header[first + 1:second]
    return header


def target_decoy(targets: list[tuple[bytes, bytes]], contaminants: list[tuple[bytes, bytes]], decoy_tag: str = "rev_",
                 contam_prefix: str = CONTAM_PREFIX) -> list[tuple[bytes, bytes]]:
    """Philosopher's `database --custom … [--contam --contamprefix]` in Python (dat.Create + dat.Save):

    - entries are keyed by header (a header twice: the later sequence wins);
    - a contaminant is added as `contam_<header>` unless its accession (between the first two '|') is already a
      target's, or an earlier contaminant's;
    - every entry gets a decoy `<tag><header>` with the sequence reversed (`reverse_like_philosopher`);
    - the result is sorted by '>' + header, byte-wise, each sequence on one line."""
    db: dict[bytes, bytes] = {}
    for h, s in targets:
        db[h] = s
    ids = {_protein_id(h) for h in db}
    for h, s in contaminants:
        pid = _protein_id(h)
        if pid in ids:
            continue
        db[contam_prefix.encode() + h] = s
        ids.add(pid)
    tag = decoy_tag.encode()
    out = {b">" + h: s for h, s in db.items()}
    out.update({b">" + tag + h: reverse_like_philosopher(s) for h, s in db.items()})
    return [(h[1:], out[h]) for h in sorted(out)]


def _write_fasta(entries: list[tuple[bytes, bytes]], part: Path) -> None:
    with open(part, "wb") as out:
        for h, s in entries:
            out.write(b">" + h + b"\n" + s + b"\n")


def _scan(path: Path, decoy_tag: str) -> dict:
    """Entries, decoys and contaminants of a FASTA, reading only the headers (`fasta_headers`)."""
    entries = decoys = contam = 0
    tag, con = decoy_tag.encode(), CONTAM_PREFIX.encode()
    with open(path, "rb") as fh:
        for h in fasta_headers(fh):
            entries += 1
            if h.startswith(tag):
                decoys += 1
            elif h.startswith(con):
                contam += 1
    return {"entries": entries, "decoys": decoys, "contaminants": contam}


def contaminants_from_fasta(path: Path, prefix: str = CONTAM_PREFIX, decoy_tag: str = "rev_"
                            ) -> list[tuple[bytes, bytes]]:
    """The `contam_` entries of an existing FragPipe FASTA, without the prefix (decoys of them are skipped)."""
    p = prefix.encode()
    with open(path, "rb") as fh:  # streamed: only the contam_ entries of a big FASTA are kept
        return [(h[len(p):], s) for h, s in _read_entries(fh, keep=lambda start: start.startswith(p))]


def _download_bytes(url: str, timeout: float, stop: threading.Event | None, limit: int = 50 << 20) -> bytes:
    buf = io.BytesIO()
    with urlopen(_request(url), timeout=timeout) as r:
        while True:
            _check_stop(stop)
            chunk = r.read(CHUNK)
            if not chunk:
                break
            buf.write(chunk)
            if buf.tell() > limit:
                raise ValueError(f"{_host(url)} sent more than {limit >> 20} MB")
    return buf.getvalue()


def fetch_contaminants(*, timeout: float = 30, stop: threading.Event | None = None, notes: list[str] | None = None
                       ) -> tuple[list[tuple[bytes, bytes]], str]:
    """Philosopher's contaminant list from its source on GitHub (crap-gpmdb.fas in lib/dat/cRAPS.zip, ~3 MB),
    else the GPM's cRAP. ([], '') offline, the reason in `notes`."""
    notes = notes if notes is not None else []
    why = offline_reason()
    if why:
        notes.append(f"contaminant list not downloaded: {why}")
        return [], ""
    try:
        data = _download_bytes(PHILOSOPHER_CONTAMINANTS_URL, timeout, stop)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = _parse_fasta_bytes(z.read(PHILOSOPHER_CONTAMINANTS_MEMBER))
        if entries:
            return entries, f"Philosopher's contaminant list ({PHILOSOPHER_CONTAMINANTS_MEMBER}, from GitHub)"
        notes.append(f"Philosopher's {PHILOSOPHER_CONTAMINANTS_MEMBER} had no entries")
    except (urllib.error.URLError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        notes.append("Philosopher's contaminant list could not be downloaded: " + (
            _net_error(exc, PHILOSOPHER_CONTAMINANTS_URL) if isinstance(exc, (urllib.error.URLError, OSError))
            else str(exc)))
    try:
        entries = _parse_fasta_bytes(_download_bytes(CRAP_URL, timeout, stop))
        if entries:
            return entries, "the GPM's cRAP list (thegpm.org)"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        notes.append("cRAP could not be downloaded: " + _net_error(exc, CRAP_URL))
    return [], ""


def find_philosopher(roots: Iterable[Path] = ()) -> Path | None:
    """FragPipe's bundled Philosopher: `<root>/tools/Philosopher/philosopher-v*` (an .exe on Windows), newest first.
    `roots` are FragPipe install folders or launchers; without any, every FragPipe this PC has
    (`fragpipe.launcher_candidates()`)."""
    from ionomos import fragpipe

    folders: list[Path] = []
    given = [Path(r) for r in roots]
    if not given:
        given = [p for p in (fragpipe.fragpipe_root(c) for c in fragpipe.launcher_candidates()) if p]
    for r in given:
        if r.is_file() or r.suffix.lower() in (".bat", ".exe"):
            r = fragpipe.fragpipe_root(r) or r.parent
        folders.append(r / "tools" / "Philosopher")
    for d in folders:
        if not d.is_dir():
            continue
        # `philosopher-v5.1.10` has the pathlib suffix ".10": decide on the whole name, not the suffix
        hits = [p for p in d.iterdir() if p.is_file() and _philosopher_name(p.name)]
        if hits:
            return max(hits, key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p.name)])
    return None


def _philosopher_name(name: str) -> bool:
    """`philosopher-v5.1.10.exe` on Windows; `philosopher-v5.1.10`, `philosopher` or a `.py` stand-in elsewhere."""
    n = name.lower()
    if not n.startswith("philosopher"):
        return False
    if os.name == "nt":
        return n.endswith(".exe")
    return n.endswith(".py") or re.fullmatch(r"philosopher(-v[\d.]+)?", n) is not None


def _tool_cmd(exe: Path) -> list[str]:
    # a .py stand-in (the tests' fake Philosopher) runs with this Python; the real one is a program
    return [sys.executable, str(exe)] if exe.suffix.lower() == ".py" else [str(exe)]


def _run_tool(cmd: list[str], cwd: Path, log: Path, timeout_s: float, stop: threading.Event | None) -> int:
    """Run one step with no window, output to `log`; killed (with its children) on stop or timeout."""
    from ionomos import fragpipe

    with open(log, "ab") as out:
        proc = fragpipe._start(cmd, "Philosopher", cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL)
        try:
            end = time.monotonic() + timeout_s
            while True:
                try:
                    return proc.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
                if (stop is not None and stop.is_set()) or time.monotonic() > end:
                    fragpipe.kill_tree(proc)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                    if stop is not None and stop.is_set():
                        raise Stopped()
                    raise TimeoutError(f"Philosopher took longer than {timeout_s:.0f} s")
        finally:
            fragpipe._release(proc)  # Windows: what it left running ends with its job (D126)


# a line of a Go crash report: "goroutine 1 [running]:", "main.main()", "github.com/x/y.(*T).F(...)", "…/f.go:23 +0x85"
_GO_FRAME = re.compile(r"\.go:\d+|^goroutine \d+|^[\w.\-/*()]+\(.*\)$|^\.\.\.|^exit status \d+$")


def _last_lines(path: Path, n: int = 6) -> str:
    """The last `n` lines Philosopher printed, without the frames of a Go crash report (they name Philosopher's own
    source files, which say nothing to a lab member); the crash itself is said in words."""
    try:
        text = path.read_bytes()[-16000:].decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    kept = [ln for ln in lines if not _GO_FRAME.search(ln)]
    out = " | ".join(kept[-n:])
    if len(kept) < len(lines):
        out += (" | " if out else "") + "then Philosopher crashed (its stack trace is left out)"
    return out


def _with_philosopher(exe: Path, src: Path, work: Path, decoy_tag: str, contam: bool,
                      stop: threading.Event | None, timeout_s: float) -> Path:
    """FragPipe's Database tab, "Add decoys" (DownloadDbHelper.updateDb + CmdDatabaseUpdate): in a fresh folder,
    `workspace --clean --nocheck`, `workspace --init --nocheck --temp <dir>`, `database --custom <fasta>
    [--contam --contamprefix]` (plus `--prefix` when the tag isn't rev_), `workspace --clean --nocheck`.
    Philosopher writes its .fas into the folder it runs in; that file is returned."""
    temp = work / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    log = work / "philosopher.log"
    base = _tool_cmd(exe)
    steps = [["workspace", "--clean", "--nocheck"], ["workspace", "--init", "--nocheck", "--temp", str(temp)],
             ["database", "--custom", str(src.resolve())] + (["--contam", "--contamprefix"] if contam else [])
             + (["--prefix", decoy_tag] if decoy_tag != "rev_" else [])]
    for step in steps:
        code = _run_tool(base + step, work, log, timeout_s, stop)
        if code != 0:
            raise RuntimeError(f"Philosopher {' '.join(step[:2])} ended with exit code {code}: "
                               f"{_last_lines(log) or 'no output'}")
    try:
        _run_tool(base + ["workspace", "--clean", "--nocheck"], work, log, 120, stop)
    except (TimeoutError, OSError):
        pass  # only tidies its .meta folder, which goes with the work folder anyway
    made = sorted((p for p in work.glob("*.fas") if p.is_file()), key=lambda p: p.stat().st_mtime)
    if not made:
        raise RuntimeError(f"Philosopher wrote no .fas file: {_last_lines(log) or 'no output'}")
    return made[-1]


def prepare_for_fragpipe(src: Path, dest_dir: Path | None = None, *, decoy_tag: str = "rev_",
                         contaminants: Path | str | None = "auto", contaminant_candidates: Iterable[Path] = (),
                         philosopher: Path | None = None, date: Date | None = None, tags: Iterable[str] = (),
                         progress: Progress | None = None, stop: threading.Event | None = None,
                         timeout: float = 30, philosopher_timeout_s: float = PHILOSOPHER_TIMEOUT_S,
                         fasta_dir: Path | None = None) -> PrepareResult:
    """A FASTA without decoys -> a new FragPipe-ready one beside it (or in `dest_dir`): the targets, contaminants
    as `contam_…` entries, and a reversed `rev_…` decoy of each (half the entries), named as FragPipe names it.

    `contaminants`:
      'auto'   Philosopher's own list. With `philosopher` (its program, `find_philosopher()`) it is run as FragPipe's
               Database tab runs it. Without it, the same steps in Python, the list taken from (b) the contam_
               entries of an existing FragPipe FASTA among `contaminant_candidates`, else (c) Philosopher's list from
               GitHub, else the GPM's cRAP, else (d) none, with a note.
      a path   a contaminant FASTA of your own (Python; Philosopher can only add its own list).
      None     no contaminants.
    The source is never changed; nothing is overwritten. The result passes `fragpipe.decoy_problem` (40-60 %
    decoys) or carries an error saying why not.

    A leading UTF-8 byte-order mark is skipped by both methods (Philosopher is given a copy without it), so the
    result is the one the file saved without it gives. Text before the first header is refused (it would cost the
    first protein). Spaces in the source's name become '_' in the result's (FragPipe refuses them); `fasta_dir`
    (config.yaml's) is the folder a note suggests when `dest_dir` has a space."""
    from ionomos import fragpipe

    src = Path(src)
    res = PrepareResult(source=src, decoy_tag=decoy_tag)
    if not decoy_tag or any(c.isspace() for c in decoy_tag):
        res.error = f"decoy tag '{decoy_tag}' can't be used; FragPipe's default is rev_"
        return res
    if not src.is_file():
        res.error = f"{src} not found"
        return res
    dest_dir = Path(dest_dir) if dest_dir else src.parent
    if " " in str(dest_dir):
        res.notes.append(f"{dest_dir} has a space in it; FragPipe can't use a FASTA there. Move the result to "
                         f"{_folder_hint(fasta_dir)}")
    try:
        before = _scan(src, decoy_tag)
        with open(src, "rb") as fh:
            start = fh.read(len(BOM))
        stray_line, stray = text_before_first_header(src)
    except OSError as exc:
        res.error = f"{src.name} can't be read ({exc})"
        return res
    if before["entries"] == 0:
        res.error = f"{src.name} has no FASTA entries (no line starts with '>')"
        if start.startswith(_UTF16_BOMS):
            res.error += (": it is saved as UTF-16 ('Unicode' in Notepad). Open it in Notepad, choose Save As, "
                          "Encoding UTF-8, and prepare that file")
        return res
    if stray:
        res.error = (f"{src.name} has text before its first protein header (line {stray_line}: '{stray}'), so the "
                     f"first protein would be lost. Open it in a text editor, make the first line the first '>' "
                     f"header, save, and prepare it again")
        return res
    has_bom = start == BOM
    if has_bom:
        res.notes.append(f"{src.name} starts with a UTF-8 byte-order mark (saved as 'UTF-8 with BOM'); it was "
                         f"skipped, so the result is the same as for the file saved without one")
    if before["decoys"]:
        res.error = (f"{src.name} already has decoys ({before['decoys']:,} entries start with '{decoy_tag}'): "
                     f"adding more would make it unusable. Use it as it is, or start from the file without decoys")
        return res
    if src.stat().st_size >= fragpipe.BIG_FASTA_BYTES:
        res.notes.append(f"{src.name} is 1 GB or more: FragPipe does not check the decoys of a file this big, and "
                         f"making it may take a while")
    contam_file: Path | None = None
    if isinstance(contaminants, (str, Path)) and str(contaminants) != "auto":
        contam_file = Path(contaminants)
        if not contam_file.is_file():
            res.error = f"contaminant file {contam_file} not found"
            return res
    want_contam = contaminants is not None
    tags = list(tags)
    base = src.name
    if any(c.isspace() for c in base):
        base = re.sub(r"\s+", "_", base.strip())
        res.notes.append(f"'{src.name}' has spaces in its name, which FragPipe refuses in a FASTA path: the new "
                         f"file is named after '{base}' instead")

    def name_for(contam: bool) -> str:
        return fragpipe_fasta_name(base, day=date, decoys=True, contam=contam, tags=tags)

    final_name = name_for(want_contam)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        made: Path | None = None
        if philosopher is not None and contam_file is None:
            exe = Path(philosopher)
            if not exe.is_file():
                res.notes.append(f"Philosopher not found at {exe}; made in Python instead")
            else:
                _say(progress, f"running Philosopher on {src.name}")
                work = Path(tempfile.mkdtemp(prefix=".ionomos-philosopher-", dir=dest_dir))
                try:
                    given = _without_bom(src, work / "input") if has_bom else src
                    out = _with_philosopher(exe, given, work, decoy_tag, want_contam, stop, philosopher_timeout_s)
                    part = dest_dir / (final_name + ".part")
                    part = unique_path(part) if part.exists() else part
                    shutil.move(str(out), str(part))
                    made = _commit(part, dest_dir / final_name)
                    res.method = "philosopher"
                    if want_contam:
                        res.contaminant_source = f"Philosopher's built-in list ({exe.name})"
                except (RuntimeError, TimeoutError, OSError) as exc:
                    res.notes.append(f"Philosopher could not make it ({exc}); made in Python instead, the same way")
                finally:
                    shutil.rmtree(work, ignore_errors=True)  # the folder this call made, nothing else
        if made is None:
            made = _prepare_python(src, dest_dir, name_for, res, decoy_tag, contaminants, contam_file,
                                   contaminant_candidates, progress, stop, timeout)
        res.path = made
    except Stopped:
        res.error = "stopped"
        return res
    if res.path is None:
        return res
    wanted = name_for(want_contam and (res.method == "philosopher" or bool(res.contaminant_source)))
    if res.path.name != wanted:
        res.notes.append(f"{wanted} already exists; the new file is {res.path.name}")
    after = _scan(res.path, decoy_tag)
    res.entries, res.decoys, res.contaminants = after["entries"], after["decoys"], after["contaminants"]
    res.targets = res.entries - res.decoys
    if want_contam and res.contaminants == 0 and res.method == "philosopher":
        res.notes.append("no contaminants were added: FragPipe-Analyst and Ionomos can't remove keratins, trypsin "
                         "or BSA from the results without them (D87)")
    problem = fragpipe.decoy_problem({"percolator.run-percolator": "true", "database.decoy-tag": decoy_tag}, res.path)
    if problem:
        res.error = f"the new file would not pass FragPipe's check: {problem}"
    _say(progress, f"saved {res.path.name}")
    return res


def _folder_hint(fasta_dir: Path | None) -> str:
    if fasta_dir is not None and " " not in str(fasta_dir):
        return f"fasta_dir ({fasta_dir})"
    return "a folder without spaces, like C:/Fragpipe_Auto/fasta"


def _without_bom(src: Path, folder: Path) -> Path:
    """A copy of `src` without its leading UTF-8 byte-order mark, same name, in `folder` (a folder of its own, so it
    is never taken for Philosopher's .fas). The bundled Philosopher crashes on a BOM; given this copy it makes
    exactly what it makes from the file saved without one (its output depends on the entries, not the file)."""
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / src.name
    with open(src, "rb") as fin, open(copy, "wb") as fout:
        if fin.read(len(BOM)) != BOM:
            fin.seek(0)
        shutil.copyfileobj(fin, fout, CHUNK)
    return copy


def _prepare_python(src: Path, dest_dir: Path, name_for: Callable[[bool], str], res: PrepareResult, decoy_tag: str,
                    contaminants, contam_file: Path | None, candidates: Iterable[Path], progress, stop, timeout
                    ) -> Path:
    _say(progress, f"reading {src.name}")
    targets = read_fasta(src)
    _check_stop(stop)
    contam: list[tuple[bytes, bytes]] = []
    if contaminants is None:
        pass
    elif contam_file is not None:
        contam = read_fasta(contam_file)
        res.contaminant_source = f"your file {contam_file.name}"
    else:
        for cand in candidates:
            try:
                if Path(cand).resolve() == src.resolve():
                    continue
                contam = contaminants_from_fasta(Path(cand), decoy_tag=decoy_tag)
            except OSError:
                continue
            if contam:
                res.contaminant_source = f"the contam_ entries of {Path(cand).name} (made by FragPipe on this PC)"
                break
        if not contam:
            _say(progress, "downloading the contaminant list")
            contam, res.contaminant_source = fetch_contaminants(timeout=timeout, stop=stop, notes=res.notes)
        if not contam:
            res.notes.append("no contaminant list was found (offline, and no FragPipe FASTA with contam_ entries "
                             "given): the file has decoys but no contaminants, and its name says so. FragPipe-"
                             "Analyst and Ionomos can't remove keratins, trypsin or BSA from the results without "
                             "them (D87)")
    if not contam:
        res.contaminant_source = ""
    final_name = name_for(bool(contam))
    _check_stop(stop)
    _say(progress, "adding decoys")
    entries = target_decoy(targets, contam, decoy_tag)
    part = dest_dir / (final_name + ".part")
    part = unique_path(part) if part.exists() else part
    try:
        _write_fasta(entries, part)
        made = _commit(part, dest_dir / final_name)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    res.method = "python"
    return made



# ------------------------------------------------------------ stock workflows --


def fragpipe_tag(version: str) -> str:
    """FragPipe's release tags on GitHub: '24.0', '23.1' (no 'v'). 'v24.0', 'FragPipe-24.0', '24' -> '24.0'."""
    v = re.sub(r"^(?:fragpipe[-_ ]?)?v?", "", str(version or "").strip(), flags=re.IGNORECASE)
    if re.fullmatch(r"\d+", v):
        v += ".0"
    return v


def stock_workflows(version: str, *, timeout: float = 15) -> StockList:
    """The names of FragPipe's stock workflows for a release, from its GitHub source (contents API: 60 requests an
    hour per network without a login). Tries `workflows/` (23.0 on), then `MSFragger-GUI/workflows/` (22.0)."""
    tag = fragpipe_tag(version)
    res = StockList(version=str(version), tag=tag)
    if not re.fullmatch(r"\d+(?:\.\d+)+(?:-[A-Za-z0-9.]+)?", tag):
        res.error = f"'{version}' is not a FragPipe version (like 24.0)"
        return res
    why = offline_reason()
    if why:
        res.error = why
        return res
    last = ""
    for folder in WORKFLOW_FOLDERS:
        url = f"{FRAGPIPE_API}/{folder}?ref={urllib.parse.quote(tag)}"
        try:
            data, _h = _get_json(url, timeout, "application/vnd.github+json")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                last = f"no {folder}/ at FragPipe {tag}"
                continue
            res.error = _net_error(exc, url)
            return res
        except (urllib.error.URLError, OSError, ValueError) as exc:
            res.error = _net_error(exc, url)
            return res
        if not isinstance(data, list):
            last = f"GitHub's answer for {folder}/ was not a folder listing"
            continue
        res.folder = folder
        res.names = sorted(str(x.get("name"))[: -len(".workflow")] for x in data
                           if isinstance(x, dict) and x.get("type") == "file"
                           and str(x.get("name", "")).endswith(".workflow"))
        if not res.names:
            res.notes.append(f"{folder}/ at FragPipe {tag} has no .workflow files")
        return res
    res.error = (f"FragPipe {tag} not found on GitHub ({last}); its versions look like 24.0 or 23.1"
                 if last else f"FragPipe {tag} not found on GitHub")
    return res


def fetch_stock_workflow(version: str, name: str, dest_dir: Path, *, timeout: float = 15,
                         folder: str | None = None) -> FetchResult:
    """One stock workflow, `<dest_dir>/<name>.workflow` (-2, -3 … when taken), from FragPipe's source at that
    release tag (raw.githubusercontent.com: no API limit). A source only: nothing is pinned or adopted here."""
    stem = str(name or "").strip()
    stem = stem[: -len(".workflow")] if stem.lower().endswith(".workflow") else stem
    res = FetchResult(name=stem)
    tag = fragpipe_tag(version)
    if not _SAFE_NAME.match(stem) or ".." in stem:
        res.error = f"'{name}' is not a workflow name (like TMT10-MS3); `ionomos fasta stock-workflows {tag}` lists them"
        return res
    why = offline_reason()
    if why:
        res.error = why
        return res
    dest_dir = Path(dest_dir)
    folders = (folder,) if folder else WORKFLOW_FOLDERS
    text = b""
    for f in folders:
        url = f"{FRAGPIPE_RAW}/{urllib.parse.quote(tag)}/{f}/{urllib.parse.quote(stem)}.workflow"
        try:
            text = _download_bytes(url, timeout, None, limit=5 << 20)
            res.url = url
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            res.error = _net_error(exc, url)
            return res
        except (urllib.error.URLError, OSError, ValueError) as exc:
            res.error = _net_error(exc, url)
            return res
    if not res.url:
        res.error = (f"FragPipe {tag} has no stock workflow {stem}; `ionomos fasta stock-workflows {tag}` lists "
                     f"the ones it has")
        return res
    body = text.decode("utf-8", "replace")
    if body.lstrip().startswith("<") or not re.search(r"^[A-Za-z][\w.\-]*\s*[=:]", body, re.MULTILINE):
        res.error = f"{res.url} did not return a workflow (key=value lines)"
        return res
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"{stem}.workflow"
    part = unique_path(final.with_name(final.name + ".part"))
    part.write_bytes(text)
    res.path = _commit(part, final)
    if res.path.name != final.name:
        res.notes.append(f"{final.name} already exists; the new file is {res.path.name}")
    res.notes.append(f"FragPipe {tag}'s stock {stem}: it has no FASTA set (database.db-path); Ionomos sets the "
                     f"method's FASTA when it runs a search")
    return res


# --------------------------------------------------------------------- CLI --

CLI_HELP = """\
FASTA files from UniProt, made ready for FragPipe (docs/FASTA.md).

  ionomos fasta search human                     find the proteome id (UP…) of an organism
  ionomos fasta download UP000005640             download it (reviewed only), add decoys and contaminants
         [--unreviewed] [--isoforms] [--dest DIR] [--no-contam | --contam FILE] [--no-philosopher]
  ionomos fasta prepare FILE.fasta [--dest DIR]  add decoys and contaminants to a FASTA you have
  ionomos fasta release [FASTA ...]              UniProt's current release; how old the lab's FASTAs are
  ionomos fasta stock-workflows 24.0 [--get TMT10-MS3 --dest DIR]   FragPipe's stock workflows of a release

Nothing is overwritten: a name that is taken gets -2, -3, …. Downloads need the internet; the rest does not.
"""


def add_parser(sub):
    import argparse

    p = sub.add_parser("fasta", help="download a proteome from UniProt, add decoys + contaminants for FragPipe, "
                                     "check how old a FASTA is, get FragPipe's stock workflows",
                       description=CLI_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    fs = p.add_subparsers(dest="fasta_cmd", required=True)
    s = fs.add_parser("search", help="find a UniProt proteome id by organism")
    s.add_argument("organism", nargs="+", help="human, mouse, 'Mus musculus', 10090 or UP000005640")
    s.add_argument("--all", action="store_true", help="not only reference proteomes")
    s.add_argument("--limit", type=int, default=5, help="list at most this many proteomes (default 5)")
    d = fs.add_parser("download", help="download a proteome and make it FragPipe-ready")
    d.add_argument("proteome", help="UniProt proteome id, e.g. UP000005640 (human)")
    d.add_argument("--unreviewed", action="store_true", help="all entries, not only reviewed (Swiss-Prot)")
    d.add_argument("--isoforms", action="store_true", help="add isoform sequences")
    d.add_argument("--dest", metavar="DIR", help="folder for the files (default: fasta_dir from config.yaml)")
    d.add_argument("--no-philosopher", action="store_true", help="add the decoys in Python, not with FragPipe's "
                                                                 "Philosopher")
    d.add_argument("--raw-only", action="store_true", help="download only; no decoys or contaminants")
    _contam_args(d)
    pr = fs.add_parser("prepare", help="add decoys and contaminants to a FASTA (a new file; the old one is kept)")
    pr.add_argument("fasta", help="the FASTA file to prepare")
    pr.add_argument("--dest", metavar="DIR", help="folder for the new file (default: the FASTA's folder)")
    pr.add_argument("--no-philosopher", action="store_true", help="add the decoys in Python, not with "
                                                                   "FragPipe's Philosopher")
    pr.add_argument("--decoy-tag", default="rev_", help="decoy prefix (default rev_, as FragPipe)")
    _contam_args(pr)
    r = fs.add_parser("release", help="UniProt's current release, and how old FASTAs are against it")
    r.add_argument("fastas", nargs="*", help="FASTA files or folders of them (default: those in fasta_dir)")
    w = fs.add_parser("stock-workflows", help="list (or --get one of) FragPipe's stock workflows of a release")
    w.add_argument("version", help="FragPipe version, e.g. 24.0")
    w.add_argument("--get", metavar="NAME", help="download this workflow (never over an existing file)")
    w.add_argument("--dest", metavar="DIR", help="folder for --get (required with it)")
    for q in (s, d, pr, r, w):
        q.add_argument("--json", action="store_true", help="print the result as JSON")
    p.set_defaults(fn=run_cli)
    return p


def _contam_args(parser) -> None:
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--no-contam", action="store_true", help="no contaminants")
    g.add_argument("--contam", metavar="FILE", help="your own contaminant FASTA (default: Philosopher's list)")


def _config(args):
    try:
        from ionomos.config import load

        return load(args.config, check_paths=False)
    except Exception:  # noqa: BLE001 - every command works without a config
        return None


def _cli_progress():
    state = {"msg": "", "t": 0.0}

    def show(message: str, done: int, total: int | None) -> None:
        now = time.monotonic()
        if message != state["msg"] or now - state["t"] > 2:
            extra = f" ({done / 1e6:.1f} MB)" if done and message.startswith("downloading") else ""
            print(f"  {message}{extra}", file=sys.stderr, flush=True)
            state["msg"], state["t"] = message, now

    return show


def _fasta_files(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in Path(folder).iterdir()
                      if p.is_file() and p.name.lower().endswith((".fas", ".fasta", ".fa")))
    except OSError:
        return []


def _release_files(given: list[str], cfg) -> tuple[list[Path], list[Path], list[Path]]:
    """(FASTA files, paths that don't exist, folders without FASTAs) for `fasta release`: a folder stands for its
    FASTAs; nothing given, the FASTAs in fasta_dir."""
    if not given:
        return (_fasta_files(cfg.fasta_dir) if cfg else []), [], []
    files: list[Path] = []
    missing: list[Path] = []
    empty: list[Path] = []
    for g in given:
        f = Path(g)
        if f.is_dir():
            found = _fasta_files(f)
            files += found
            if not found:
                empty.append(f)
        elif f.is_file():
            files.append(f)
        else:
            missing.append(f)
    return files, missing, empty


def _philosopher_for(cfg) -> Path | None:
    roots = []
    if cfg is not None:
        from ionomos import fragpipe

        root = fragpipe.fragpipe_root(cfg.fragpipe_exe)
        if root:
            roots.append(root)
    return find_philosopher(roots) or (find_philosopher() if roots else None)


def _print_notes(notes: list[str]) -> None:
    for n in notes:
        print(f"  note: {n}")


def run_cli(args) -> int:
    cmd = args.fasta_cmd
    as_json = getattr(args, "json", False)
    if cmd == "search":
        res = search_proteomes(" ".join(args.organism), limit=args.limit, reference_only=not args.all)
        if as_json:
            print(json.dumps(res.as_dict(), indent=2))
            return 1 if res.error else 0
        if res.error:
            print(f"could not search: {res.error}", file=sys.stderr)
            return 1
        for row in res.rows:
            print(f"{row['id']}  {row['organism']}  (taxon {row['taxon']}, {row['proteins']:,} UniProt entries)")
        _print_notes(res.notes)
        if res.rows:
            print(f"\nthen: ionomos fasta download {res.rows[0]['id']}")
        return 0
    if cmd == "release":
        notes: list[str] = []
        rel = uniprot_release(notes=notes)
        cfg = _config(args)
        files, missing, empty = _release_files(args.fastas, cfg)
        ok = rel is not None and not missing
        if as_json:
            print(json.dumps({"release": rel, "notes": notes, "missing": [str(f) for f in missing],
                              "files": {str(f): staleness(f.name, rel) for f in files}}, indent=2))
            return 0 if ok else 1
        print(f"UniProt: {rel['release']} ({rel['date']})" if rel else f"UniProt: unknown. {notes[0] if notes else ''}")
        for f in files:
            print(f"{f.name}: {staleness(f.name, rel)}")
        for f in missing:
            print(f"{f}: no such file or folder; check the path (with no FASTA given, the FASTAs in fasta_dir are "
                  f"checked)")
        for d in empty:
            print(f"{d}: no FASTA files (.fas, .fasta, .fa) in this folder")
        if not args.fastas and not files:
            print("(no FASTA files given, and no fasta_dir in config.yaml)")
        return 0 if ok else 1
    if cmd == "stock-workflows":
        if args.get:
            if not args.dest:
                print("say where to save it: --dest DIR", file=sys.stderr)
                return 2
            got = fetch_stock_workflow(args.version, args.get, Path(args.dest))
            if as_json:
                print(json.dumps(got.as_dict(), indent=2))
            elif got.ok:
                print(f"saved {got.path}  (from {got.url})")
                _print_notes(got.notes)
            else:
                print(f"not saved: {got.error}", file=sys.stderr)
            return 0 if got.ok else 1
        lst = stock_workflows(args.version)
        if as_json:
            print(json.dumps(lst.as_dict(), indent=2))
        elif lst.error:
            print(f"could not list them: {lst.error}", file=sys.stderr)
        else:
            print(f"FragPipe {lst.tag} stock workflows ({lst.folder}/):")
            for n in lst.names:
                print(f"  {n}")
            _print_notes(lst.notes)
        return 1 if lst.error else 0
    cfg = _config(args)
    contaminants: Path | str | None = None if args.no_contam else (Path(args.contam) if args.contam else "auto")
    phil = None if args.no_philosopher else _philosopher_for(cfg)
    cands = _fasta_files(cfg.fasta_dir) if cfg is not None else []
    if cmd == "prepare":
        src = Path(args.fasta)
        res = prepare_for_fragpipe(src, Path(args.dest) if args.dest else None, decoy_tag=args.decoy_tag,
                                   contaminants=contaminants, contaminant_candidates=cands, philosopher=phil,
                                   progress=None if as_json else _cli_progress(),
                                   fasta_dir=cfg.fasta_dir if cfg is not None else None)
        return _print_prepared(res, as_json=as_json)
    # download
    if args.dest:
        dest = Path(args.dest)
    elif cfg is not None:
        dest = cfg.fasta_dir
    else:
        print("no config.yaml to take fasta_dir from: say where with --dest DIR (a folder without spaces)",
              file=sys.stderr)
        return 2
    fasta_dir = cfg.fasta_dir if cfg is not None else None
    got = download_proteome(args.proteome, dest, reviewed=not args.unreviewed, isoforms=args.isoforms,
                            progress=_cli_progress(), fasta_dir=fasta_dir)
    if not got.ok:
        if as_json:
            print(json.dumps({"download": got.as_dict()}, indent=2))
        else:
            print(f"not downloaded: {got.error}", file=sys.stderr)
            _print_notes(got.notes)
        return 1
    if not as_json:
        print(f"downloaded {got.path}: {got.entries:,} entries of {got.organism or got.proteome}, UniProt "
              f"{got.release or '?'} ({got.release_date or '?'})")
        _print_notes(got.notes)
    if args.raw_only:
        if as_json:
            print(json.dumps({"download": got.as_dict()}, indent=2))
        return 0
    tags = (["reviewed"] if got.reviewed else []) + (["isoforms"] if got.isoforms else [])
    res = prepare_for_fragpipe(got.path, dest, contaminants=contaminants, contaminant_candidates=cands,
                               philosopher=phil, tags=tags, progress=_cli_progress(), fasta_dir=fasta_dir)
    if as_json:
        print(json.dumps({"download": got.as_dict(), "prepared": res.as_dict()}, indent=2))
        return 0 if res.ok else 1
    return _print_prepared(res, as_json=False)


def _print_prepared(res: PrepareResult, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res.as_dict(), indent=2))
        return 0 if res.ok else 1
    if not res.ok:
        print(f"not prepared: {res.error}", file=sys.stderr)
        _print_notes(res.notes)
        return 1
    print(res.summary())
    print(f"  {res.path}")
    if res.contaminant_source:
        print(f"  contaminants from {res.contaminant_source}")
    _print_notes(res.notes)
    print("  To search with it: put it in fasta_dir and choose it for the method (app, Methods tab), or set "
          "database.db-path in FragPipe")
    return 0
