"""Tab-separated tables without pandas: read, write, number parsing that matches readr's NA rules.

Text is read the way the lab's files arrive (round 4): UTF-8 with or without a byte-order mark, UTF-16 with one
(Excel's "Unicode text"), and Windows' own code page (cp1252) when the bytes are not UTF-8 at all. A cell may be as
long as it likes: MaxQuant's ID lists ("Evidence IDs") pass csv's default limit of 131,072 characters on big
experiments, so the limit is raised for the whole process."""
from __future__ import annotations

import csv
import itertools
import math
import re
from pathlib import Path

NA_STRINGS = {"", "NA", "NaN", "nan", "NULL", "null", "N/A", "#N/A", "-", "Inf", "-Inf", "inf", "-inf"}

csv.field_size_limit(2**31 - 1)  # the largest a C long holds on Windows; csv's default stops at 131,072 characters

_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_UTF8_MULTIBYTE = re.compile(rb"[\xc2-\xf4][\x80-\xbf]")


def decode(raw: bytes) -> tuple[str, str]:
    """(text, encoding used). UTF-16 when the bytes start with its byte-order mark; UTF-8 (a BOM dropped) when they
    are UTF-8, or hold UTF-8 characters next to a few damaged bytes (those become U+FFFD); otherwise cp1252, the
    code page Windows programs write with (an 'ä' from a Java or Excel export is then still an 'ä')."""
    if raw.startswith(_UTF16_BOMS):
        return raw.decode("utf-16", errors="replace"), "utf-16"
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        if _UTF8_MULTIBYTE.search(raw):
            return raw.decode("utf-8-sig", errors="replace"), "utf-8"
        return raw.decode("cp1252", errors="replace"), "cp1252"


def open_text(path: str | Path, sniff: int = 1 << 16):
    """A text file opened for reading line by line, in the encoding decode() would pick from its first 64 KB."""
    with open(path, "rb") as fh:
        head = fh.read(sniff)
    if head.startswith(_UTF16_BOMS):
        enc = "utf-16"
    else:
        try:
            head.decode("utf-8-sig")
            enc = "utf-8-sig"
        except UnicodeDecodeError as exc:
            # a multibyte character cut by the 64 KB boundary is still UTF-8
            ok = exc.start >= len(head) - 3 or bool(_UTF8_MULTIBYTE.search(head))
            enc = "utf-8-sig" if ok else "cp1252"
    return open(path, encoding=enc, errors="replace", newline="")


def unquote(cell: str) -> str:
    """'"Protein.Group"' -> 'Protein.Group': a cell wrapped in double quotes, as Excel and Proteome Discoverer
    write them, with "" inside read as one quote. Anything else is left as it is."""
    if len(cell) >= 2 and cell[0] == '"' and cell[-1] == '"':
        return cell[1:-1].replace('""', '"')
    return cell


def reader(lines, delim: str = "\t"):
    """csv rows of an engine's own text table, line by line: a quote never spans cells or lines (the engines don't
    quote, and one stray quote must not swallow the file), but a cell wrapped in quotes (the table saved again from
    Excel) loses them."""
    for row in csv.reader(lines, delimiter=delim, quoting=csv.QUOTE_NONE):
        yield [unquote(c) for c in row] if any(c[:1] == '"' for c in row) else row


def delimiter(line: str) -> str:
    """The delimiter of a header line: a tab whenever there is one, else a comma or a semicolon (more of them wins)."""
    if "\t" in line or not (line.count(",") or line.count(";")):
        return "\t"
    return "," if line.count(",") >= line.count(";") else ";"


def num(x) -> float | None:
    """'1.5' -> 1.5; '', 'NA', 'NaN', non-numbers -> None. Infinities count as missing."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if (isinstance(x, float) and not math.isfinite(x)) else float(x)
    s = str(x).strip()
    if s in NA_STRINGS:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def read_tsv(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    """(header, rows as dicts of strings). Handles a BOM, UTF-16 and cp1252 (decode()), ragged lines, cells in
    double quotes (a table saved again from Excel), and a table saved with commas or semicolons instead of tabs.
    A quote is never taken to span cells or lines: FragPipe's and DIA-NN's tables have no quoting, and one stray
    quote must not swallow the rest of the file."""
    with open_text(path) as fh:
        first = fh.readline()
        delim = delimiter(first)
        reader = csv.reader(itertools.chain([first], fh), delimiter=delim, quoting=csv.QUOTE_NONE)
        try:
            header = [unquote(h) for h in next(reader)]
        except StopIteration:
            return [], []
        rows = []
        for line in reader:
            if not line or all(not c for c in line):
                continue
            if any(c[:1] == '"' for c in line):
                if len(line) > len(header):  # a quoted cell holding the delimiter: read this line with quotes
                    quoted = next(csv.reader([delim.join(line)], delimiter=delim), line)
                    line = quoted if len(quoted) == len(header) else line
                line = [unquote(c) for c in line]
            line = line + [""] * (len(header) - len(line))
            rows.append(dict(zip(header, line, strict=False)))
    return header, rows


def read_header(path: str | Path) -> list[str]:
    with open_text(path) as fh:
        first = fh.readline().rstrip("\r\n")
    return [unquote(h) for h in first.split(delimiter(first))] if first else []


def fmt(v) -> str:
    """Format like readr::write_tsv: None and NaN -> 'NA', integral doubles without '.0', shortest repr."""
    if v is None:
        return "NA"
    if isinstance(v, float):
        if math.isnan(v):
            return "NA"
        if math.isinf(v):
            return "Inf" if v > 0 else "-Inf"
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return repr(v)
    return str(v)


def write_tsv(path: str | Path, header: list[str], rows: list[dict | list]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            vals = [r.get(h) for h in header] if isinstance(r, dict) else list(r)
            fh.write("\t".join(fmt(v).replace("\t", " ").replace("\n", " ") for v in vals) + "\n")
    tmp.replace(p)
    return p
