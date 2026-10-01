"""Any protein/peptide table -> the same QuantMatrix the FragPipe loaders make.

Used when a folder has no FragPipe result table Ionomos knows, or when someone points `ionomos analyze`
at a file. Two shapes are recognised, in this order:

  differential  already-analysed results: a fold-change column and a p-value column per comparison
                (limma topTable, Perseus, DESeq2, a FragPipe-Analyst export, a hand-made sheet).
                These are plotted as they are; nothing is recomputed.
  quantities    an ID column plus one numeric column per sample (MaxQuant proteinGroups, Spectronaut,
                Proteome Discoverer, DIA-NN / FragPipe matrices, any hand-made sheet). These go through
                the normal pipeline: normalisation, imputation, statistics, volcano plots.

Formats: tab / comma / semicolon separated text (a semicolon file may use decimal commas) and Excel
.xlsx (first sheet), read without extra packages. Nothing here writes to the input.
"""
from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ionomos.downstream.quant import Feature, QuantMatrix, run_stem
from ionomos.downstream.tables import NA_STRINGS, num

TABLE_SUFFIXES = (".tsv", ".csv", ".txt", ".xlsx", ".tab")
SKIP_DIRS = {"results", "ionomos_run", "__MACOSX"}
# FragPipe / DIA-NN long-format intermediates: one row per PSM/ion/precursor, never a sample matrix
SKIP_NAMES = {"psm.tsv", "ion.tsv", "peptide.tsv", "protein.tsv", "modified_peptide.tsv", "combined_ion.tsv",
              "report.tsv", "report.pr_matrix.tsv", "filelist_ionquant.txt", "filelist_proteinprophet.txt",
              "fragpipe-files.fp-manifest", "sdrf.tsv", "experiment_annotation.tsv", "experimental_annotation.tsv"}
MAX_BYTES = 500 * 1024 * 1024
SCAN_BYTES = 100 * 1024 * 1024  # find_table() skips bigger files; name them explicitly to load them


class TableError(ValueError):
    """The file can't be read as a table, or holds neither quantities nor results."""


# ------------------------------------------------------------------ reading --


def _xlsx(path: Path) -> list[list[str]]:
    """First worksheet of an .xlsx as rows of strings (shared strings, inline strings, numbers)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise TableError(f"{path.name} is not a readable .xlsx file ({exc})") from exc
    with z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheet = "xl/worksheets/sheet1.xml"
        try:  # the workbook's first sheet, wherever it is stored
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            first = wb.find("m:sheets/m:sheet", ns)
            rid = first.get(f"{{{ns['r']}}}id") if first is not None else None
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                if rel.get("Id") == rid:
                    target = rel.get("Target", "").lstrip("/")
                    sheet = target if target.startswith("xl/") else "xl/" + target
        except (KeyError, ET.ParseError):
            pass
        if sheet not in names:
            raise TableError(f"{path.name}: no worksheet found")
        rows: list[list[str]] = []
        for row in ET.fromstring(z.read(sheet)).iter(f"{{{ns['m']}}}row"):
            cells: dict[int, str] = {}
            for c in row.findall("m:c", ns):
                ref = re.match(r"([A-Z]+)", c.get("r", "") or "")
                col = 0
                for ch in ref.group(1) if ref else "":
                    col = col * 26 + ord(ch) - 64
                col = col - 1 if ref else len(cells)
                t, v = c.get("t"), c.find("m:v", ns)
                if t == "s" and v is not None and v.text is not None:
                    val = shared[int(v.text)] if int(v.text) < len(shared) else ""
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
                else:
                    val = v.text if v is not None and v.text is not None else ""
                cells[col] = val
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(j, "") for j in range(width)])
        return rows


_DEC_COMMA = re.compile(r"^[+-]?\d+,\d+(?:[eE][+-]?\d+)?$")           # 1234,5
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")        # 1,234,567.8
_DOT_DECIMAL = re.compile(r"^[+-]?\d*\.\d+(?:[eE][+-]?\d+)?$")


def _number_format(grid: list[list[str]], notes: list[str], name: str) -> None:
    """Decimal commas (1234,5) or thousands separators (1,234,567.8) in a tab / comma file, in place. One
    reading for the whole file, from the cells that can only be read one way; "1,234" alone is ambiguous
    and is read as 1234, with a note."""
    comma = [(i, j, c.strip()) for i, r in enumerate(grid[1:], 1) for j, c in enumerate(r) if "," in c]
    dec = [x for x in comma if _DEC_COMMA.match(x[2])]
    thou = [x for x in comma if _THOUSANDS.match(x[2])]
    if not dec and not thou:
        return
    dec_sure = [x for x in dec if not _THOUSANDS.match(x[2])]
    thou_sure = [x for x in thou if not _DEC_COMMA.match(x[2])]
    dots = any(_DOT_DECIMAL.match(c.strip()) for r in grid[1:] for c in r if "." in c)
    if dec_sure and not thou_sure and not dots:
        for i, j, c in dec:
            grid[i][j] = c.replace(",", ".")
        notes.append(f"{name}: {len(dec):,} numbers use a decimal comma (e.g. {dec_sure[0][2]}); read as decimals")
    elif thou_sure or not dec_sure:
        for i, j, c in thou:
            grid[i][j] = c.replace(",", "")
        notes.append(f"{name}: {len(thou):,} numbers use commas as thousands separators (e.g. {thou[0][2]}); the "
                     "commas were dropped" + ("" if thou_sure or dots else
                                              ". If these are decimal commas, export the table with decimal points"))
    else:
        notes.append(f"{name}: numbers are written both with decimal commas (e.g. {dec_sure[0][2]}) and with decimal "
                     "points; the ones with commas were not read. Export the table with one number format")


def read_table(path: str | Path, notes: list[str] | None = None) -> tuple[list[str], list[list[str]]]:
    """(header, rows) from a text or .xlsx table. Header cells are stripped; rows are padded to the header.
    notes: a list to add to. With it, decimal commas / thousands separators are also read in tab and comma
    files, and rows that are shorter or longer than the header are counted."""
    path = Path(path)
    delim = ""
    if path.stat().st_size > MAX_BYTES:
        raise TableError(f"{path.name} is larger than {MAX_BYTES // 2**20} MB")
    if path.suffix.lower() == ".xlsx":
        grid = _xlsx(path)
    else:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace") if not raw.startswith((b"\xff\xfe", b"\xfe\xff")) \
            else raw.decode("utf-16")
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        delim = max(("\t", ",", ";"), key=first.count) if first else "\t"
        if "\t" in first and path.suffix.lower() in (".tsv", ".txt", ".tab"):  # a tab file whose headers hold
            delim = "\t"  # commas: Proteome Discoverer's 'Abundance: F1: 126, Sample, DMSO'
        grid = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
        if delim == ";":  # European exports: 1,5 means 1.5
            grid = [grid[0]] + [[re.sub(r"^(-?\d+),(\d+)$", r"\1.\2", c.strip()) for c in r] for r in grid[1:]]
    grid = [r for r in grid if any(c.strip() for c in r)]
    if not grid:
        raise TableError(f"{path.name} is empty")
    header = [h.strip() for h in grid[0]]
    if notes is not None:
        if delim and delim != ";":
            _number_format(grid, notes, path.name)
        short = sum(1 for r in grid[1:] if len(r) < len(header))
        long = sum(1 for r in grid[1:] if any(c.strip() for c in r[len(header):]))
        if short:
            notes.append(f"{path.name}: {short:,} row(s) have fewer cells than the header; the missing cells count "
                         "as blank")
        if long:
            notes.append(f"{path.name}: {long:,} row(s) have more cells than the header; the extra cells were ignored")
    rows = [(r + [""] * (len(header) - len(r)))[: len(header)] for r in grid[1:]]
    return header, rows


# -------------------------------------------------------------- recognising --

_ID_NAMES = ["protein.group", "protein group", "protein id", "protein ids", "majority protein ids", "pg.proteingroups",
             "pg.proteinaccessions", "protein accession", "accession", "accessions", "uniprot", "uniprot id",
             "protein", "proteins", "id", "gene", "genes", "gene names", "gene name", "pg.genes", "gene symbol",
             "symbol", "name", "site", "peptide", "sequence"]
_LABEL_NAMES = ["genes", "gene", "gene names", "gene name", "pg.genes", "gene symbol", "gene.names", "symbol",
                "gene_symbol", "genesymbol"]
_DESC_NAMES = ["description", "protein names", "protein description", "first.protein.description",
               "pg.proteindescriptions", "fasta headers", "protein name"]
# numeric columns that describe a feature, never a sample
_NOT_SAMPLE = re.compile(
    r"(^|[ ._])(n\.|#|count|counts|coverage|mw|mol\.? weight|mass|length|score|q[ ._-]?value|pep|posterior|"
    r"peptides?|razor|unique|msms|ms/ms|fdr|p[ ._-]?val(ue)?|pval|adj|fold|log2fc|logfc|t$|position|start|end|"
    r"index|rank|charge|rt|retention|seq|sequence|best|number|numberpsm|maxpepprob|referenceintensity|"
    r"combined|total|spectral|ibaq)([ ._]|$)", re.I)
# sample-column families, most specific first: (regex on the header, group 1 = sample name)
_FAMILIES = [
    re.compile(r"^LFQ intensity (.+)$", re.I),                    # MaxQuant label-free
    re.compile(r"^(.+) MaxLFQ Intensity$", re.I),                 # FragPipe / IonQuant
    re.compile(r"^Reporter intensity corrected (.+)$", re.I),     # MaxQuant TMT
    re.compile(r"^(?:\[\d+\]\s*)?(.+?)\.PG\.Quantity$", re.I),    # Spectronaut
    re.compile(r"^Abundances? \(Normalized\):\s*(.+)$", re.I),    # Proteome Discoverer
    re.compile(r"^Abundances?:\s*(.+)$", re.I),
    re.compile(r"^Intensity (.+)$", re.I),                        # MaxQuant raw intensity
    re.compile(r"^(.+) Intensity$", re.I),                        # FragPipe combined_protein
]
_FC = re.compile(r"(log2[ _.-]?fold[ _.-]?change|log2foldchange|log2[ _.-]?fc|logfc|log2[ _.-]?ratio|"
                 r"fold[ _.-]?change|difference|(^|[ _.:-])fc($|[ _.:-]))", re.I)
_P = re.compile(r"((-?log(10)?)[ _.-]?)?(p[ _.-]?val(ue)?|pvalue|p\.value|^p$|(^|[ _.:-])p($|[ _.:-]))", re.I)
_Q = re.compile(r"(adj[ _.-]?p|p[ _.-]?adj|padj|q[ _.-]?val(ue)?|qvalue|fdr|bh|adjusted)", re.I)
_MQ_FLAGS = ("Reverse", "Potential contaminant", "Only identified by site", "Contaminant")


# what other programs write in a cell without a value (Spectronaut "Filtered", Excel errors, "n.d.")
_NO_VALUE = {"filtered", "n.d.", "nd", "n.a.", "#div/0!", "#value!", "#num!", "#ref!", "#name?", "missing", "none",
             "<lod", "bdl", "--", "?"}


def _blank(c: str) -> bool:
    return c in NA_STRINGS or c.lower() in _NO_VALUE


def _number_like(c: str) -> bool:
    """A cell that is a number, including one too large for a float or written as infinity (missing later)."""
    if num(c) is not None:
        return True
    try:
        float(c)
    except ValueError:
        return False
    return True


def _unique(header: list[str], notes: list[str]) -> list[str]:
    """Column names made unique ("DMSO_1", "DMSO_1.2"), so two columns with one name are two columns."""
    seen: dict[str, int] = {}
    out, dups = [], []
    for h in header:
        seen[h] = seen.get(h, 0) + 1
        if seen[h] > 1 and h:
            new = f"{h}.{seen[h]}"
            while new in seen:
                new += "'"
            seen[new] = 1
            dups.append(h)
            out.append(new)
        else:
            out.append(h)
    if dups:
        names = list(dict.fromkeys(dups))
        notes.append("column name(s) used more than once: " + ", ".join(names[:6]) + ("…" if len(names) > 6 else "")
                     + "; each later column was read as its own column (name.2, name.3)")
    return out


def _pick(header: list[str], names: list[str]) -> str | None:
    low = {h.lower(): h for h in header}
    return next((low[n] for n in names if n in low), None)


def _numeric_share(rows: list[list[str]], j: int, limit: int = 400) -> tuple[int, int]:
    """(numbers, non-blank cells) in column j over the first `limit` rows."""
    ok = nonblank = 0
    for r in rows[:limit]:
        c = r[j].strip()
        if _blank(c):
            continue
        nonblank += 1
        ok += _number_like(c)
    return ok, nonblank


def _is_numeric(rows, j) -> bool:
    ok, nb = _numeric_share(rows, j)
    return nb == 0 or ok >= 0.9 * nb


_TEST_WORDS = re.compile(r"-?\blog(10)?\b|student'?s|welch'?s|t-?test|\btest\b|\bval\b|\bvalue\b", re.I)


def _stem(h: str) -> str:
    """The comparison a result column belongs to: 'DrugA_vs_DMSO_p.adj' and
    '-Log Student's T-test p-value DrugA_DMSO' -> the same key as their fold-change column."""
    s = h
    for rx in (_FC, _Q, _P):
        s = rx.sub(" ", s)
    s = _TEST_WORDS.sub(" ", s)
    return re.sub(r"[\s_.:\-]+", " ", s).strip()


def _comparisons(header: list[str], rows) -> list[dict]:
    """Already-analysed results: [{name, fc, p, q, neglog}] — one per fold-change column with a matching p."""
    out = []
    ps = [h for h in header if _P.search(h) and not _Q.search(h) and _is_numeric(rows, header.index(h))]
    qs = [h for h in header if _Q.search(h) and _is_numeric(rows, header.index(h))]
    for h in header:
        if not _FC.search(h) or _P.search(h) or _Q.search(h) or not _is_numeric(rows, header.index(h)):
            continue
        key = _stem(h).lower()
        same = [p for p in ps if _stem(p).lower() == key] or (ps if len(ps) == 1 else [])
        if not same:
            continue
        p = same[0]
        q = next((x for x in qs if _stem(x).lower() == key), None) or (qs[0] if len(qs) == 1 else None)
        name = re.sub(r"\s+", " ", _TEST_WORDS.sub(" ", _FC.sub(" ", h))).strip(" _.:-")
        out.append({"name": name, "fc": h, "p": p, "q": q, "neglog": bool(re.search(r"-\s*log|^log", p, re.I))})
    if not out and not ps:  # fold changes and nothing else: still plotted, as fold change only
        for h in header:
            if _FC.search(h) and not _Q.search(h) and _is_numeric(rows, header.index(h)) \
                    and _numeric_share(rows, header.index(h))[0]:
                name = re.sub(r"\s+", " ", _TEST_WORDS.sub(" ", _FC.sub(" ", h))).strip(" _.:-")
                out.append({"name": name, "fc": h, "p": None, "q": None, "neglog": False})
    return out


def _samples(header: list[str], rows, used: set[str]) -> tuple[list[str], list[str], str]:
    """(sample columns, sample names, how they were found)."""
    summary = {"total", "unique", "razor", "combined", "summed", "sum", "max", "mean", "average", "median"}
    for fam in _FAMILIES:
        cols = [h for h in header if fam.match(h) and h not in used and _is_numeric(rows, header.index(h))
                and fam.match(h).group(1).strip().lower() not in summary]
        if len(cols) >= 2:
            names = [fam.match(h).group(1).strip() for h in cols]
            return cols, [run_stem(n) for n in names], fam.pattern
    cols = [h for h in header if h and h not in used and not _NOT_SAMPLE.search(h)
            and _is_numeric(rows, header.index(h)) and _numeric_share(rows, header.index(h))[0] > 0]
    return cols, [run_stem(h) for h in cols], "numeric columns"


def _left_out(header: list[str], rows, used: set[str], cols: list[str]) -> list[str]:
    """Notes on columns that could have been samples and were not read as one."""
    blank, unnamed, partly = [], 0, []
    for j, h in enumerate(header):
        if h in used or h in cols or (h and _NOT_SAMPLE.search(h)):
            continue
        ok, nb = _numeric_share(rows, j)
        if not h:
            unnamed += bool(ok) and ok >= 0.9 * nb
        elif nb == 0:
            blank.append(h)
        elif 0.5 * nb <= ok < 0.9 * nb:
            partly.append(f"{h} ({100 * ok / nb:.0f}% numbers)")
    notes = []
    if unnamed:
        notes.append(f"{unnamed} numeric column(s) without a name in the header were left out; name them to use them "
                     "as samples")
    if blank and cols:
        notes.append("column(s) with no values at all were left out: " + ", ".join(blank[:8]) +
                     ("…" if len(blank) > 8 else ""))
    if partly:
        notes.append("column(s) left out because too many of their cells are not numbers: " + ", ".join(partly[:8]) +
                     ("…" if len(partly) > 8 else ""))
    return notes


def _cell_notes(rows, cols: list[int]) -> list[str]:
    """What the sample columns held besides numbers and blanks: text, infinities, numbers too large to hold."""
    text: dict[str, int] = {}
    infinite = 0
    for r in rows:
        for j in cols:
            c = r[j].strip()
            if c in NA_STRINGS:
                infinite += c.lower().lstrip("+-") == "inf"
            elif num(c) is None:
                if _number_like(c):
                    infinite += 1
                else:
                    text[c] = text.get(c, 0) + 1
    notes = []
    if text:
        top = sorted(text, key=lambda k: -text[k])[:4]
        notes.append(f"{sum(text.values()):,} cell(s) in the sample columns are not numbers (e.g. " +
                     ", ".join(repr(t[:20]) for t in top) + ") and count as missing")
    if infinite:
        notes.append(f"{infinite:,} cell(s) in the sample columns are infinite or too large for a number and count as "
                     "missing")
    return notes


def _id_column(header: list[str], rows) -> str:
    named = _pick(header, _ID_NAMES)
    if named:
        return named
    for j, h in enumerate(header):  # the first mostly-text column
        ok, nb = _numeric_share(rows, j)
        if nb and ok < 0.5 * nb:
            return h
    return header[0]


def describe(path: str | Path) -> dict:
    """What a table holds, without loading it all: {"kind": "differential"|"quantities"|None, ...}."""
    header, rows = read_table(path, [])
    comps = [c for c in _comparisons(header, rows) if c["p"]] or (
        _comparisons(header, rows) if not _samples(header, rows, {_id_column(header, rows)})[0] else [])
    if comps:
        return {"kind": "differential", "comparisons": [c["name"] or Path(path).stem for c in comps],
                "rows": len(rows)}
    idc = _id_column(header, rows)
    cols, names, how = _samples(header, rows, {idc})
    if len(cols) >= 1:
        return {"kind": "quantities", "samples": names, "found_by": how, "rows": len(rows)}
    return {"kind": None, "rows": len(rows)}


# ------------------------------------------------------------------ loading --


def load(path: str | Path) -> QuantMatrix:
    """A QuantMatrix from any table. Already-analysed tables come back with no samples and
    meta["precomputed"] = [{name, fc, p, q}] (lists aligned with the features)."""
    path = Path(path)
    notes: list[str] = []
    header, rows = read_table(path, notes)
    if not rows:
        raise TableError(f"{path.name} has a header but no rows")
    header = _unique(header, notes)
    idc = _id_column(header, rows)
    labc = _pick(header, _LABEL_NAMES) or idc
    descc = _pick(header, _DESC_NAMES)
    ix = {h: j for j, h in enumerate(header)}

    flags = [f for f in _MQ_FLAGS if f in ix]
    if flags:
        before = len(rows)
        rows = [r for r in rows if not any(r[ix[f]].strip() == "+" for f in flags)]
        if before - len(rows):
            notes.append(f"{before - len(rows)} row(s) flagged {' / '.join(flags)} were left out (MaxQuant '+')")

    def feature(r) -> Feature:
        fid = r[ix[idc]].strip()
        lab = r[ix[labc]].strip().split(";")[0] or fid.split(";")[0]
        return Feature(id=fid, label=lab, description=r[ix[descc]].strip() if descc else "")

    comps = _comparisons(header, rows)
    if comps:
        feats = [feature(r) for r in rows]
        pre = []
        for c in comps:
            fc = [num(r[ix[c["fc"]]]) for r in rows]
            got = [v for v in fc if v is not None]
            linear_name = re.search(r"fold[ _.-]?change|foldchange|(^|[^a-z])fc([^a-z]|$)|ratio", c["fc"], re.I)
            if (got and linear_name and not re.search(r"log|diff", c["fc"], re.I)
                    and min(got) > 0):  # a linear fold change (2.5, 0.4)
                fc = [math.log2(v) if v is not None else None for v in fc]
                notes.append(f"'{c['fc']}' looks like a linear fold change; log2-transformed")
            p = [num(r[ix[c["p"]]]) for r in rows] if c["p"] else [None] * len(rows)
            if c["neglog"]:  # Perseus "-Log p-value"
                p = [10 ** -v if v is not None else None for v in p]
            p = [v if v is not None and 0 <= v <= 1 else None for v in p]
            q = [num(r[ix[c["q"]]]) for r in rows] if c["q"] else None
            if q is not None:
                q = [v if v is not None and 0 <= v <= 1 else None for v in q]
            pre.append({"name": c["name"] or path.stem, "fc": fc, "p": p, "q": q, "columns": [c["fc"], c["p"], c["q"]]})
        notes.append(f"{path.name} already holds results ({', '.join(x['name'] for x in pre)}): plotted as given, "
                     "not recomputed")
        return QuantMatrix("intensity", "protein", feats, [], [], {}, str(path), notes=notes, exp="table",
                           meta={"precomputed": pre, "table": path.name})

    cols, names, how = _samples(header, rows, {idc, labc, descc} - {None})
    if not cols:
        raise TableError(f"{path.name}: no numeric sample columns and no fold-change / p-value columns found")
    raw = [[num(r[ix[c]]) for c in cols] for r in rows]
    notes += _left_out(header, rows, {idc, labc, descc} - {None}, cols)
    notes += _cell_notes(rows, [ix[c] for c in cols])
    vals = [v for row in raw for v in row if v is not None]
    if not vals:
        values = raw
    elif sorted(vals)[len(vals) // 2] < 100:  # the median decides: a few negative cells don't make intensities log2
        values = raw
        notes.append(f"values in {path.name} look like log2 already; used as they are")
    else:
        values = [[math.log2(v) if v is not None and v > 0 else None for v in row] for row in raw]
        notes.append(f"values in {path.name} look like raw intensities; log2-transformed (zeros count as missing)")
        negative = sum(1 for v in vals if v < 0)
        if negative:
            notes.append(f"{negative:,} negative intensities in {path.name} count as missing (an intensity can't be "
                         "below zero; were the values background-subtracted?)")
    from ionomos.downstream.doctor import suggest_conditions

    samples, cond, reps = [], {}, {}
    for n in names:
        s, k = n or "sample", 2
        while s in cond:
            s, k = f"{n}.{k}", k + 1
        samples.append(s)
        cond[s] = s
        tail = s.rsplit("_", 1)[-1]
        if tail.isdigit():
            reps[s] = int(tail)
    cond = suggest_conditions(samples)  # 20260101_DMSO_1 / 20260101_Drug_2 -> DMSO / Drug
    notes.append(f"{len(samples)} sample column(s) found by {how}: " + ", ".join(cols[:8]) + ("…" if len(cols) > 8 else ""))
    return QuantMatrix("intensity", "protein", [feature(r) for r in rows], samples, values, cond, str(path), notes=notes,
                       exp="LFQ", replicate=reps, columns=dict(zip(samples, cols, strict=True)),
                       meta={"table": path.name})


def find_table(folder: str | Path) -> Path | None:
    """The most promising table under folder (3 levels deep, results/ and old runs skipped): one with results
    beats one with quantities; more rows win."""
    folder = Path(folder)
    best, best_key = None, None
    cands = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).parts
        if (len(rel) > 4 or not p.is_file() or p.suffix.lower() not in TABLE_SUFFIXES or p.name.startswith((".", "~$"))
                or p.name.lower() in SKIP_NAMES or re.match(r"(?i)^log[_-]", p.name)
                or p.name.lower().endswith((".sdrf.tsv", "_sdrf.tsv", "-sdrf.tsv", ".sdrf"))  # a design (sdrfdesign.py)
                or any(x in SKIP_DIRS or "_previous_" in x for x in rel[:-1])):
            continue
        try:
            if p.stat().st_size > SCAN_BYTES:
                continue
        except OSError:
            continue
        cands.append(p)
    for p in cands[:200]:
        try:
            d = describe(p)
        except (TableError, OSError, UnicodeError, csv.Error, ValueError, ET.ParseError):
            continue
        if not d["kind"]:
            continue
        key = (d["kind"] == "differential", d["rows"])
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best
