"""
When each raw file was acquired (D78). Files are only opened for reading; nothing is ever written to them.

    info = read(path, look_in=[dest / "sage_mzml"])   # {"time", "utc", "source", "approximate", ...}
    files = for_manifest(dest, ["raw/DMSO_1.raw", ...]) # intake: ionomos.json "acquisition" {file: info}
    header(path)                                      # a Thermo .raw file's own header, or None

Sources, best first:

    raw header           Thermo .raw: the FileHeader at offset 0 (1356 bytes; magic 0xA101, "Finnigan" in
                         UTF-16LE, the format version at 0x24) holds two audit tags, acquisition start at 0x28
                         and end at 0x98, each starting with a Windows FILETIME (100 ns since 1601-01-01 UTC).
                         The layout is the one unfinnigan documented and OpenTFRaw (validated on six real files
                         from LTQ to Fusion Lumos and Q Exactive HF) and Philosopher's `fin` package read; it is
                         what Thermo's RawFileReader calls FileHeader.CreationDate, which ThermoRawFileParser
                         writes as "Creation date". Only the first 264 bytes are read. A header is used only when
                         the magic, the signature, a known version and a plausible time all check out.
    ThermoRawFileParser  what it already wrote for this file: an mzML's <run startTimeStamp="..."> (Sage's
                         conversion, sage_mzml/), or <name>-metadata.json / -metadata.txt ("Creation date")
    file name            the Xcalibur stamp at the end of the name (..._20260930143015.raw), local time
    file time            the raw file's modification time: the instrument writes the file until the run ends
                         and copying keeps the time. Marked approximate.

"time" is local wall-clock time without a zone (as the QC trend has always stored it), "utc" the same moment
in UTC when the source says which moment it is. A header also gives "end" (acquisition end) and "matches_file",
whether that end is within MATCH_MINUTES of the file's modification time: the check to look at on the lab PC.
"""
from __future__ import annotations

import json
import re
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

HEADER_BYTES = 0x108          # magic .. end of the second audit tag
MAGIC = 0xA101
SIGNATURE = "Finnigan"
KNOWN_VERSIONS = (8, 47, 57, 60, 62, 63, 64, 66)
NEWER_VERSIONS_UP_TO = 99     # later Xcalibur / Foundation versions keep the header; still checked for a sane time
FILETIME_EPOCH = 11644473600  # seconds from 1601-01-01 to 1970-01-01
EARLIEST = datetime(1995, 1, 1, tzinfo=UTC)
MATCH_MINUTES = 10
MZML_HEAD = 256 * 1024        # an mzML's <run> element is near the top

SOURCES = ("raw header", "ThermoRawFileParser", "file name", "file time")
APPROXIMATE = ("file time",)


def _local(dt_utc: datetime) -> str:
    return dt_utc.astimezone().replace(tzinfo=None, microsecond=0).isoformat()


def _utc(dt_utc: datetime) -> str:
    return dt_utc.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _filetime(v: int) -> datetime | None:
    if v <= 0:
        return None
    try:
        return datetime.fromtimestamp(v / 1e7 - FILETIME_EPOCH, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _plausible(dt: datetime | None) -> bool:
    return dt is not None and EARLIEST <= dt <= datetime.now(UTC) + timedelta(days=2)


def parse_header(data: bytes) -> dict | None:
    """The Thermo FileHeader fields this needs, from a file's first bytes, or None when they are not a
    Thermo raw file header (or not one this can trust)."""
    if len(data) < HEADER_BYTES:
        return None
    magic, = struct.unpack_from("<H", data, 0)
    if magic != MAGIC:
        return None
    try:
        sig = data[2:20].decode("utf-16-le").split("\x00", 1)[0]
    except UnicodeDecodeError:
        return None
    if sig != SIGNATURE:
        return None
    version, = struct.unpack_from("<I", data, 0x24)
    if version not in KNOWN_VERSIONS and not 66 < version <= NEWER_VERSIONS_UP_TO:
        return None
    start = _filetime(struct.unpack_from("<Q", data, 0x28)[0])
    end = _filetime(struct.unpack_from("<Q", data, 0x98)[0])
    if not _plausible(start):
        return None
    # The audit tags' text (Xcalibur_System, the instrument, or a Windows account) is not kept: an account name
    # in ionomos.json would be a name a bundle's anonymiser does not know (D63).
    return {"start": start, "end": end if _plausible(end) else None, "version": version}


def header(path: Path) -> dict | None:
    """A Thermo .raw file's header (parse_header), read-only; None for anything else or on any error."""
    try:
        with open(path, "rb") as fh:
            return parse_header(fh.read(HEADER_BYTES))
    except OSError:
        return None


# ------------------------------------------------------------ ThermoRawFileParser --

_NET_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y %H:%M:%S")


def parse_time(text: str) -> tuple[datetime | None, bool]:
    """A time as ThermoRawFileParser writes it: ISO 8601 (mzML, with a zone) or .NET's DateTime.ToString() in
    the PC's culture (metadata files, no zone). Returns (time, has_zone); a time without a zone is local."""
    s = (text or "").strip()
    if not s:
        return None, False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt, dt.tzinfo is not None
    except ValueError:
        pass
    for fmt in _NET_FORMATS:  # US order first: the lab PC's culture is en-US
        try:
            return datetime.strptime(s, fmt), False
        except ValueError:
            continue
    return None, False


def _mzml_start(path: Path) -> str | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(MZML_HEAD).decode("utf-8", "replace")
    except OSError:
        return None
    m = re.search(r"<run\b[^>]*\bstartTimeStamp=\"([^\"]+)\"", head)
    return m.group(1) if m else None


def _metadata_creation(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    if path.suffix.lower() == ".json":
        try:
            props = (json.loads(text) or {}).get("FileProperties") or []
        except (ValueError, AttributeError):
            return None
        for p in props if isinstance(props, list) else []:
            if isinstance(p, dict) and str(p.get("name", "")).lower() == "content creation date":
                return str(p.get("value") or "") or None
        return None
    m = re.search(r"^Creation date=(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def from_trfp(stem: str, look_in) -> tuple[datetime, str] | None:
    """(time, file it came from) from what ThermoRawFileParser wrote for the raw file `stem` in these folders."""
    for d in look_in or []:
        d = Path(d)
        for name, reader in ((f"{stem}.mzML", _mzml_start), (f"{stem}-metadata.json", _metadata_creation),
                             (f"{stem}-metadata.txt", _metadata_creation)):
            p = d / name
            if not p.is_file():
                continue
            dt, zoned = parse_time(reader(p) or "")
            if dt is None:
                continue
            if not zoned:
                dt = dt.astimezone()  # no zone: the computer's local time
            if _plausible(dt.astimezone(UTC)):
                return dt.astimezone(UTC), p.name
    return None


# ------------------------------------------------------------------ the answer --


def from_name(stem: str) -> datetime | None:
    from ionomos.naming import ACQ_STAMP

    m = ACQ_STAMP.search(stem)
    if not m:
        return None
    try:
        return datetime.strptime(re.sub(r"\D", "", m.group(0)), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def read(path: Path, look_in=None, stem: str | None = None) -> dict:
    """When the run in `path` was acquired, from the best source there is (see the module doc). Always returns
    a dict; "time" is None when nothing at all is known. Read-only."""
    path = Path(path)
    stem = stem if stem is not None else path.stem
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except (OSError, OverflowError, ValueError):
        mtime = None
    info: dict = {"time": None, "utc": None, "source": None, "approximate": False}
    if mtime is not None:
        info["file_time"] = _local(mtime)
    h = header(path) if path.suffix.lower() == ".raw" else None
    if h is not None and mtime is not None and h["start"] > mtime + timedelta(days=1):
        info["note"] = "the raw header's time is after the file was last written; not used"
        h = None
    if h is not None:
        info.update(time=_local(h["start"]), utc=_utc(h["start"]), source="raw header", version=h["version"])
        if h["end"] is not None:
            info["end"] = _local(h["end"])
            if mtime is not None:
                info["matches_file"] = abs((h["end"] - mtime).total_seconds()) <= MATCH_MINUTES * 60
        return info
    got = from_trfp(stem, look_in)
    if got is not None:
        info.update(time=_local(got[0]), utc=_utc(got[0]), source="ThermoRawFileParser", from_file=got[1])
        return info
    named = from_name(stem)
    if named is not None:
        info.update(time=named.isoformat(), source="file name")
        return info
    if mtime is not None:
        info.update(time=_local(mtime), utc=_utc(mtime), source="file time", approximate=True)
    return info


def for_manifest(dest: Path, files, look_in=None) -> dict[str, dict]:
    """{manifest file (relative to dest): read()} for every raw file of a filed experiment (intake)."""
    dest = Path(dest)
    out = {}
    for f in files:
        f = str(f)
        out[f] = read(dest / f, look_in)
    return out
