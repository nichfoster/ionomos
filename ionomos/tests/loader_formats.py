"""One simulated experiment written in every format Ionomos reads (tests/test_loader_formats.py, round 4).

    exp = experiment(tmp_path)                    # proteins, their linear values per run, the planted truth
    spec = FORMATS["maxquant"](exp)               # the files as grids: [(relative path, header, rows, delimiter)]
    main = emit(folder, spec, bom=True, ...)      # written, with the damage asked for; the main table's path

Every format holds the same numbers, so every format must give the same hits (D36: the analysis must not depend on
which engine's file carried them). The writers use the engines' real column names, as tests/test_engines.py does.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ionomos.downstream import simulate

CONDS = ("DMSO", "Drug")
RUNS = [f"{c}_{r}" for c in CONDS for r in (1, 2, 3)]


@dataclass
class Experiment:
    prots: list[tuple[str, str, str, int]]       # (protein id, gene, description, peptides)
    vals: dict[str, dict[str, float | None]]     # run -> protein id -> linear intensity
    truth: dict[str, int]                        # gene -> +1 / -1 (Drug vs DMSO)


def experiment(tmp: Path, n: int = 150, seed: int = 11) -> Experiment:
    pg = tmp / "_sim" / "report.pg_matrix.tsv"
    truth = simulate.dia_pg_matrix(pg, [(f"C:\\raw\\{r}.raw", r.split("_")[0]) for r in RUNS], seed=seed, n_proteins=n)
    header, *lines = pg.read_text(encoding="utf-8").splitlines()
    head = header.split("\t")
    prots, vals = [], {r: {} for r in RUNS}
    for line in lines:
        v = line.split("\t")
        prots.append((v[0], v[3], v[4], int(v[5])))
        for k, h in enumerate(head):
            if h.endswith(".raw"):
                vals[h.rsplit("\\", 1)[-1][:-4]][v[0]] = float(v[k]) if v[k] else None
    return Experiment(prots, vals, truth["Drug"])


Grid = tuple[str, list[str], list[list], str]   # (relative path, header, rows, delimiter)


def _v(x):
    return "" if x is None else x


# ------------------------------------------------------------------ formats --


def fragpipe_dia(e: Experiment) -> list[Grid]:
    head = ["Protein.Group", "Protein.Ids", "Protein.Names", "Genes", "First.Protein.Description", "N.Sequences",
            "N.Proteotypic.Sequences", *[f"C:\\raw\\{r}.raw" for r in RUNS]]
    rows = [[p, p, f"{g}_HUMAN", g, d, n, n, *[_v(e.vals[r][p]) for r in RUNS]] for p, g, d, n in e.prots]
    return [("fragpipe/diann-output/report.pg_matrix.tsv", head, rows, "\t")]


def fragpipe_lfq(e: Experiment) -> list[Grid]:
    head = ["Protein", "Protein ID", "Entry Name", "Gene", "Description", "Combined Total Peptides",
            *[f"{r} Intensity" for r in RUNS], *[f"{r} MaxLFQ Intensity" for r in RUNS]]
    rows = [[f"sp|{p}|{g}_HUMAN", p, f"{g}_HUMAN", g, d, n, *[_v(e.vals[r][p]) for r in RUNS],
             *[_v(e.vals[r][p]) for r in RUNS]] for p, g, d, n in e.prots]
    return [("fragpipe/combined_protein.tsv", head, rows, "\t")]


DIANN_LONG = ["Run", "Protein.Group", "Protein.Ids", "Genes", "Protein.Names", "First.Protein.Description",
              "Precursor.Id", "Stripped.Sequence", "Q.Value", "PG.Q.Value", "PG.MaxLFQ"]


def _diann_rows(e: Experiment) -> list[list]:
    rows = []
    for p, g, d, _n in e.prots:
        for r in RUNS:
            v = e.vals[r][p]
            if v is not None:
                for k in range(2):
                    rows.append([f"D:\\data\\{r}.raw", p, p, g, f"{g}_HUMAN", d, f"PEP{k}{p}K2", f"PEP{k}{p}K",
                                 0.001, 0.002, v])
        rows.append([f"D:\\data\\{RUNS[0]}.raw", p, p, g, "", d, "BADK2", "BADK", 0.2, 0.3, 1e12])  # above 1 %
    return rows


def diann_long(e: Experiment) -> list[Grid]:
    return [("report.tsv", DIANN_LONG, _diann_rows(e), "\t")]


def maxquant(e: Experiment) -> list[Grid]:
    head = ["Protein IDs", "Majority protein IDs", "Gene names", "Protein names", "Peptides", "Razor + unique peptides",
            "Reverse", "Potential contaminant", "Only identified by site", "Intensity", "iBAQ",
            *[f"Intensity {r}" for r in RUNS], *[f"LFQ intensity {r}" for r in RUNS]]
    rows = []
    for p, g, d, n in e.prots:
        lin = [e.vals[r][p] or 0 for r in RUNS]
        rows.append([p, p, g, d, n + 1, n, "", "", "", sum(lin), 1, *[x * 1.1 for x in lin], *lin])
    rows.append(["REV__P99999", "REV__P99999", "", "", 1, 1, "+", "", "", *[1e9] * 14])
    rows.append(["CON__P00761", "CON__P00761", "TRYP", "Trypsin", 9, 9, "", "+", "", *[1e9] * 14])
    return [("combined/txt/proteinGroups.txt", head, rows, "\t"),
            ("combined/txt/parameters.txt", ["Parameter", "Value"], [["Version", "2.4.9.0"], ["Protein FDR", "0.01"]],
             "\t")]


def spectronaut_long(e: Experiment) -> list[Grid]:
    head = ["R.Condition", "R.FileName", "R.Replicate", "PG.ProteinGroups", "PG.Genes", "PG.ProteinDescriptions",
            "PG.Quantity", "PG.Qvalue", "EG.PrecursorId", "EG.Qvalue"]
    rows = [[r.split("_")[0], f"20260930_{r}_SN", r.split("_")[1], p, g, d, e.vals[r][p], 0.001, f"_PEP{p}K_.2", 0.001]
            for p, g, d, _n in e.prots for r in RUNS if e.vals[r][p] is not None]
    return [("Report_BGS.tsv", head, rows, "\t")]


def spectronaut_pivot(e: Experiment) -> list[Grid]:
    head = ["PG.ProteinGroups", "PG.Genes", "PG.ProteinDescriptions", *[f"[{k}] {r}.PG.Quantity" for k, r in
                                                                        enumerate(RUNS, 1)]]
    return [("pivot.tsv", head, [[p, g, d, *[e.vals[r][p] if e.vals[r][p] is not None else "Filtered" for r in RUNS]]
                                 for p, g, d, _n in e.prots], "\t")]


def alphadia(e: Experiment) -> list[Grid]:
    return [("pg.matrix.tsv", ["pg", *RUNS], [[p, *[_v(e.vals[r][p]) for r in RUNS]] for p, *_ in e.prots], "\t")]


def msstats(e: Experiment) -> list[Grid]:
    head = ["ProteinName", "PeptideSequence", "PrecursorCharge", "FragmentIon", "ProductCharge", "IsotopeLabelType",
            "Condition", "BioReplicate", "Run", "Intensity"]
    rows = []
    for p, _g, _d, _n in e.prots:
        for r in RUNS:
            v = e.vals[r][p]
            c, k = r.split("_")
            for j, share in enumerate((0.5, 0.3, 0.2)):
                rows.append([p, f"PEP{j}K", 2, "NA", "NA", "L", c, k, f"{r}.mzML", v * share if v else "NA"])
    return [("msstats.csv", head, rows, ",")]


def pd(e: Experiment) -> list[Grid]:
    head = ["Checked", "Master", "Accession", "Description", "# Unique Peptides",
            *[f"Abundances (Normalized): F{k}: Sample, {r.split('_')[0]}" for k, r in enumerate(RUNS, 1)]]
    return [("Proteins.txt", head, [["FALSE", "IsMasterProtein", p, d, n, *[_v(e.vals[r][p]) for r in RUNS]]
                                    for p, _g, d, n in e.prots], "\t")]


def sage(e: Experiment) -> list[Grid]:
    head = ["peptide", "charge", "proteins", "q_value", "score", "spectral_angle", *[f"{r}.mzML" for r in RUNS]]
    rows = [[f"PEPTIDE{p}{'A' * k}K", 2, f"sp|{p}|{g}_HUMAN", 0.001, 1.2, 0.9,
             *[(e.vals[r][p] or 0) * f for r in RUNS]]
            for p, g, _d, _n in e.prots for k, f in enumerate((1.0, 0.5, 0.25))]
    return [("sage/lfq.tsv", head, rows, "\t")]


def table(e: Experiment, name: str = "table.tsv", sep: str = "\t") -> list[Grid]:
    return [(name, ["Protein", "Gene", *RUNS], [[p, g, *[_v(e.vals[r][p]) for r in RUNS]] for p, g, _d, _n in e.prots],
             sep)]


FORMATS = {
    "fragpipe-dia": fragpipe_dia,
    "fragpipe-lfq": fragpipe_lfq,
    "diann-long": diann_long,
    "maxquant": maxquant,
    "spectronaut-long": spectronaut_long,
    "spectronaut-pivot": spectronaut_pivot,
    "alphadia": alphadia,
    "msstats": msstats,
    "pd": pd,
    "sage": sage,
    "table-tsv": table,
    "table-csv": lambda e: table(e, "table.csv", ","),
    "table-semicolon": lambda e: table(e, "table.csv", ";"),
}
# the method each format is analysed as (downstream.detect_method)
METHOD = {"fragpipe-dia": "DIA", "fragpipe-lfq": "LFQ", "diann-long": "DIA-NN", "diann-parquet": "DIA-NN",
          "maxquant": "MaxQuant", "spectronaut-long": "Spectronaut", "spectronaut-pivot": "Spectronaut",
          "alphadia": "AlphaDIA", "msstats": "MSstats", "pd": "PD", "sage": "Sage", "table-tsv": "table",
          "table-csv": "table", "table-semicolon": "table", "table-xlsx": "table"}


# --------------------------------------------------------------------- TMT --

TMT10 = ["126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131"]
POOL, DMSO, DRUG = ("126", "131"), ("127N", "127C", "128N", "128C"), ("129N", "129C", "130N", "130C")


@dataclass
class Plexes:
    prots: list[str]
    sim: dict[str, dict[str, dict[str, float]]]   # plex -> channel -> protein -> linear
    hits: set[str]


def plexes(n: int = 80, k: int = 2, seed: int = 5, hits: int = 10) -> Plexes:
    """k TMT10 plexes with a pool (126, 131), 4 DMSO and 4 Drug channels and a plex effect per protein; the first
    `hits` proteins go up 2.3- to 3.5-fold in Drug (as tests/test_plexes.py)."""
    import math
    import random

    rng = random.Random(seed)
    prots = [f"P{j:05d}" for j in range(n)]
    base = {p: rng.uniform(16, 24) for p in prots}
    up = {p: (rng.uniform(1.2, 1.8) if j < hits else 0.0) for j, p in enumerate(prots)}
    sim = {}
    for x in range(1, k + 1):
        shift = {p: rng.gauss(0, 1.2) for p in prots}
        chans: dict[str, dict[str, float]] = {c: {} for c in TMT10}
        for p in prots:
            truth = {c: base[p] + (up[p] if c in DRUG else 0.0) + rng.gauss(0, 0.25) for c in DMSO + DRUG}
            pool = math.log2(sum(2 ** v for v in truth.values()) / len(truth))
            for c in TMT10:
                chans[c][p] = 2 ** ((pool if c in POOL else truth[c]) + shift[p] + rng.gauss(0, 0.1))
        sim[f"Exp{x}"] = chans
    return Plexes(prots, sim, {p for p in prots if up[p]})


def _tmt_cond(ch: str) -> str:
    return "Pool" if ch in POOL else "DMSO" if ch in DMSO else "Drug"


def maxquant_tmt(t: Plexes) -> list[Grid]:
    exps = list(t.sim)
    head = ["Protein IDs", "Majority protein IDs", "Gene names", "Peptides", "Reverse", "Potential contaminant",
            *[f"Reporter intensity corrected {j} {e}" for e in exps for j in range(1, 11)]]
    rows = [[p, p, f"G{p[1:]}", 5, "", "", *[t.sim[e][c][p] for e in exps for c in TMT10]] for p in t.prots]
    return [("combined/txt/proteinGroups.txt", head, rows, "\t")]


def pd_tmt(t: Plexes) -> list[Grid]:
    head = ["Accession", "Description", *[f"Abundance: F{x}: {c}, Sample, {_tmt_cond(c)}" for x in range(1, len(t.sim) + 1)
                                          for c in TMT10]]
    rows = [[p, f"G{p[1:]} protein", *[t.sim[f"Exp{x}"][c][p] for x in range(1, len(t.sim) + 1) for c in TMT10]]
            for p in t.prots]
    return [("proteins.txt", head, rows, "\t")]


def msstats_tmt(t: Plexes) -> list[Grid]:
    head = ["ProteinName", "PeptideSequence", "Charge", "PSM", "Mixture", "TechRepMixture", "Run", "Channel",
            "Condition", "BioReplicate", "Intensity"]
    rows = []
    for mix, chans in t.sim.items():
        for p in t.prots:
            for j, share in enumerate((1.0, 0.5, 0.25)):
                pep = f"PEP{p[1:]}{'ACD'[j]}K"
                for c in TMT10:
                    cond = "Norm" if c in POOL else _tmt_cond(c)
                    rows.append([p, pep, 2, f"{pep}_2_{j}", mix, 1, f"{mix}_F1.mzML", c, cond, f"{mix}_{c}",
                                 chans[c][p] * share])
    return [("msstatstmt.csv", head, rows, ",")]


def sage_tmt(t: Plexes) -> list[Grid]:
    psm_head = ["psm_id", "peptide", "proteins", "filename", "scannr", "rank", "label", "charge", "spectrum_q",
                "peptide_q", "protein_q"]
    tmt_head = ["filename", "scannr", "ion_injection_time", *[f"tmt_{j}" for j in range(1, 11)]]
    psm, tmt = [], []
    for mix, chans in t.sim.items():
        for p in t.prots:
            for j, share in enumerate((1.0, 0.5, 0.25)):
                scan = f"scan={len(psm) + 1}"
                psm.append([len(psm) + 1, f"PEP{p[1:]}{'ACD'[j]}K", f"sp|{p}|G{p[1:]}_HUMAN", f"{mix}_F1.mzML", scan, 1,
                            1, 2, 0.001, 0.001, 0.001])
                tmt.append([f"{mix}_F1.mzML", scan, 50.0, *[round(chans[c][p] * share, 3) for c in TMT10]])
    return [("sage/tmt.tsv", tmt_head, tmt, "\t"), ("sage/results.sage.tsv", psm_head, psm, "\t")]


TMT_FORMATS = {"maxquant-tmt": maxquant_tmt, "pd-tmt": pd_tmt, "msstatstmt": msstats_tmt, "sage-tmt": sage_tmt}


# ------------------------------------------------------------------ writing --


def text(header: list[str], rows: list[list], sep: str, quote: bool = False) -> str:
    """The grid as text. quote: every cell in double quotes (Proteome Discoverer's and Excel's habit); otherwise a
    cell that holds the delimiter, a quote or a line break is quoted as csv does it."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\n", quoting=csv.QUOTE_ALL if quote else csv.QUOTE_MINIMAL)
    w.writerow(header)
    w.writerows([["" if c is None else c for c in r] for r in rows])
    return buf.getvalue()


def emit(folder: Path, grids: list[Grid], *, bom: bool = False, crlf: bool = False, encoding: str = "utf-8",
         quote: bool = False) -> Path:
    """Write every grid under folder; returns the first (the main table)."""
    out = []
    for rel, header, rows, sep in grids:
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        t = text(header, rows, sep, quote)
        if crlf:
            t = t.replace("\n", "\r\n")
        data = t.encode(encoding)
        if bom and encoding == "utf-8":
            data = b"\xef\xbb\xbf" + data
        p.write_bytes(data)
        out.append(p)
    if (folder / "sage" / "lfq.tsv") in out:
        (folder / "sage" / "results.json").write_text(json.dumps({"version": "0.14.7", "database": {
            "fasta": "", "decoy_tag": "rev_"}}), encoding="utf-8")
    return out[0]


def parquet(folder: Path, e: Experiment) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = _diann_rows(e)
    folder.mkdir(parents=True, exist_ok=True)
    cols = {h: [r[k] for r in rows] for k, h in enumerate(DIANN_LONG)}
    pq.write_table(pa.table(cols), folder / "report.parquet")
    return folder / "report.parquet"


def xlsx(path: Path, header: list[str], rows: list[list]) -> Path:
    """A minimal real .xlsx (shared strings and numbers)."""
    strings: list[str] = []
    idx: dict[str, int] = {}
    xml_rows = []
    for i, r in enumerate([header, *rows], 1):
        cells = []
        for j, v in enumerate(r):
            col = chr(65 + j) if j < 26 else "A" + chr(65 + j - 26)
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{col}{i}"><v>{v}</v></c>')
            elif v not in ("", None):
                idx.setdefault(v, len(strings))
                if idx[v] == len(strings):
                    strings.append(v)
                cells.append(f'<c r="{col}{i}" t="s"><v>{idx[v]}</v></c>')
        xml_rows.append(f'<row r="{i}">{"".join(cells)}</row>')
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{m}"><sheetData>{"".join(xml_rows)}</sheetData>'
                                               "</worksheet>")
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{m}">' + "".join(f"<si><t>{s}</t></si>" for s in strings)
                   + "</sst>")
    return path
