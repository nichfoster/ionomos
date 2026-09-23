"""Tab-separated tables without pandas: read, write, number parsing that matches readr's NA rules."""
from __future__ import annotations

import csv
import math
from pathlib import Path

NA_STRINGS = {"", "NA", "NaN", "nan", "NULL", "null", "N/A", "#N/A", "-", "Inf", "-Inf", "inf", "-inf"}


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
    """(header, rows as dicts of strings). Handles a UTF-8 BOM and ragged lines."""
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            return [], []
        rows = []
        for line in reader:
            if not line or all(not c for c in line):
                continue
            line = line + [""] * (len(header) - len(line))
            rows.append(dict(zip(header, line, strict=False)))
    return header, rows


def read_header(path: str | Path) -> list[str]:
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        first = fh.readline().rstrip("\r\n")
    return first.split("\t") if first else []


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
