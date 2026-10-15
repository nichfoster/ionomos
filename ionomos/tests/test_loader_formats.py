"""Every format Ionomos reads, against the ways real files arrive (round 4, the loaders' audit).

One simulated experiment is written in every format (tests/loader_formats.py) and then damaged:

    the same hits    every format gives the same hit list (D36: the engine that carried the numbers must not matter)
    harmless         a BOM, CRLF, UTF-16 (Excel's "Unicode text"), Windows' cp1252, every cell in quotes, commas
                     instead of tabs, the delimiter inside a quoted cell: read to the same numbers as the original
    damage           a cut-off file, 0 bytes, a header only, NUL bytes, a column twice, rows twice, NA / #N/A /
                     Filtered cells, thousands separators, a 200,000-character cell, a quote never closed: the file is
                     read with a note or refused with a TableError naming it, and analyze() never crashes (CRASH_*)
    size             100,000 rows of every format
    regressions      one test per defect this audit fixed
"""
from __future__ import annotations

import csv
import json
import math
import random
import time
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import anytable, engines, quant, sdrfdesign, tables

from . import loader_formats as lf

CFG = {"enrichment": False}


@pytest.fixture(scope="module")
def exp(tmp_path_factory) -> lf.Experiment:
    e = lf.experiment(tmp_path_factory.mktemp("sim"), n=60)
    # descriptions with characters outside ASCII, so encodings are tested on real text
    e.prots = [(p, g, d + (" é µ ß" if k % 5 == 0 else ""), n) for k, (p, g, d, n) in enumerate(e.prots)]
    return e


def _load(folder: Path):
    method = downstream.detect_method(folder)
    m, _files, notes = downstream.load_quantities(method, folder, folder / "_res", None)
    return method, m, notes


def _snapshot(m) -> tuple[dict, dict]:
    return ({f.id: dict(zip(m.samples, row, strict=True)) for f, row in zip(m.features, m.values, strict=True)},
            dict(m.condition))


def _same(a, b) -> str:
    (va, ca), (vb, cb) = a, b
    if ca != cb:
        return f"conditions {ca} != {cb}"
    if set(va) != set(vb):
        return f"features differ: {sorted(set(va) ^ set(vb))[:4]}"
    for k, row in va.items():
        for s, x in row.items():
            y = vb[k].get(s)
            if (x is None) != (y is None) or (x is not None and abs(x - y) > 1e-6):
                return f"{k} {s}: {x} != {y}"
    return ""


def _crashes(out) -> list[str]:
    return [f"{i.code}: {i.message}" for i in out.issues if i.code.startswith("CRASH")]


def _hits(out) -> set[str]:
    d = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    comp = next(c for c in d["comparisons"] if c["name"] == "Drug vs DMSO")
    with open(out.results_dir.parent / comp["table"], encoding="utf-8") as fh:
        return {r["id"].split(";")[0] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}


# ---------------------------------------------------------------- same hits --


def test_one_experiment_in_every_format_gives_the_same_hits(tmp_path):
    """DIA-NN (matrix, long report, Parquet), FragPipe (DIA, IonQuant), MaxQuant, Spectronaut (long, pivot),
    AlphaDIA, MSstats, Proteome Discoverer, Sage, and any table as tab, comma, semicolon and .xlsx: one hit list."""
    e = lf.experiment(tmp_path / "sim", n=300)
    folders = {}
    for name, fn in lf.FORMATS.items():
        lf.emit(tmp_path / name, fn(e))
        folders[name] = tmp_path / name
    if _pyarrow():
        lf.parquet(tmp_path / "diann-parquet", e)
        folders["diann-parquet"] = tmp_path / "diann-parquet"
    _rel, h, rows, _sep = lf.table(e)[0]
    lf.xlsx(tmp_path / "table-xlsx" / "table.xlsx", h, rows)
    folders["table-xlsx"] = tmp_path / "table-xlsx"
    hits = {}
    for name, folder in folders.items():
        out = downstream.analyze(folder, analysis_cfg=CFG)
        assert out.method == lf.METHOD[name], (name, out.method)
        assert not _crashes(out), (name, _crashes(out))
        hits[name] = _hits(out)
    ids = {g: p for p, g, _d, _n in e.prots}
    truth = {ids[g] for g in e.truth}
    first = hits["fragpipe-dia"]
    assert len(first & truth) >= 0.7 * len(truth) and len(first - truth) <= 3
    differ = {k: sorted(v ^ first) for k, v in hits.items() if v != first}
    assert not differ, differ


def _pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def test_tmt_formats_find_the_planted_hits(tmp_path):
    """Proteome Discoverer (files as plexes), MSstatsTMT (Norm channels) and Sage TMT (with the tmt: channel map)
    read from the same two simulated TMT10 plexes find the planted proteins."""
    t = lf.plexes(n=120, hits=12)
    names = {c: ("Pool_" + c if c in lf.POOL else f"{lf._tmt_cond(c)}_{c}") for c in lf.TMT10}
    for name in ("pd-tmt", "msstatstmt", "sage-tmt"):
        lf.emit(tmp_path / name, lf.TMT_FORMATS[name](t))
        overrides = {"tmt": {"channels": names}} if name == "sage-tmt" else None
        out = downstream.analyze(tmp_path / name, analysis_cfg={**CFG, "tmt_reference": ["126", "131"]},
                                 record={"plan": {"overrides": overrides}} if overrides else None)
        assert not _crashes(out), (name, _crashes(out))
        found = _hits(out)
        assert len(found & t.hits) >= 0.8 * len(t.hits) and len(found - t.hits) <= 2, (name, sorted(found))


# ----------------------------------------------------------------- harmless --

HARMLESS = ["bom", "crlf", "bom+crlf", "utf16", "cp1252", "quote-all", "swap-delimiter", "delimiter-in-text",
            "latin1-byte"]


def _write_changed(folder: Path, name: str, op: str, e: lf.Experiment) -> Path:
    grids = lf.FORMATS[name](e)
    rel, head, rows, sep = grids[0]
    rows = [list(r) for r in rows]
    kw = {"bom": op.startswith("bom"), "crlf": "crlf" in op, "quote": op == "quote-all",
          "encoding": {"utf16": "utf-16", "cp1252": "cp1252"}.get(op, "utf-8")}
    if op == "swap-delimiter":
        sep = "," if sep == "\t" else "\t"
    if op == "delimiter-in-text":  # csv quotes these cells; a tab or comma inside must not shift the columns
        for r in rows[::3]:
            r[:] = [c.replace(" protein", ", kinase\tdomain; protein") if isinstance(c, str) else c for c in r]
    main = lf.emit(folder, [(rel, head, rows, sep), *grids[1:]], **kw)
    if op == "latin1-byte":  # one byte of another encoding in a UTF-8 file
        main.write_bytes(main.read_bytes().replace("é".encode(), b"\xe9", 1))
    return main


@pytest.mark.parametrize("op", HARMLESS)
@pytest.mark.parametrize("name", list(lf.FORMATS))
def test_harmless_differences_read_the_same_numbers(tmp_path, exp, name, op):
    lf.emit(tmp_path / "base", lf.FORMATS[name](exp))
    method, m, _ = _load(tmp_path / "base")
    _write_changed(tmp_path / "x", name, op, exp)
    method2, m2, _ = _load(tmp_path / "x")
    assert method2 == method
    assert _same(_snapshot(m), _snapshot(m2)) == ""


# ------------------------------------------------------------------- damage --

DAMAGE = ["cut-off", "zero-bytes", "header-only", "nul-bytes", "column-twice", "rows-twice", "na-cells",
          "thousands", "long-cell", "unclosed-quote"]


def _damage(folder: Path, name: str, op: str, e: lf.Experiment) -> Path:
    rng = random.Random(3)
    grids = lf.FORMATS[name](e)
    rel, head, rows, sep = grids[0]
    head, rows = list(head), [list(r) for r in rows]
    if op == "header-only":
        rows = []
    elif op == "column-twice":
        head.append(head[-1])
        rows = [r + [r[-1]] for r in rows]
    elif op == "rows-twice":
        rows += [list(r) for r in rows[:10]]
    elif op == "na-cells":
        for r in rows:
            r[:] = [rng.choice(["NA", "#N/A", "Filtered", "n.d.", "NaN"]) if isinstance(c, float) and rng.random() < 0.1
                    else c for c in r]
    elif op == "thousands":
        for r in rows:
            r[:] = [f"{c:,.1f}" if isinstance(c, float) and c > 1000 else c for c in r]
    elif op in ("long-cell", "unclosed-quote"):  # in a description, else in the protein column (a long ID list)
        ids = ("Protein.Group", "PG.ProteinGroups", "pg", "ProteinName", "proteins", "Protein", "Accession", "Protein IDs")
        for r in rows[2:3]:
            j = next((j for j, c in enumerate(r) if isinstance(c, str) and " protein" in c),
                     next(head.index(h) for h in ids if h in head))
            r[j] = r[j] + ";" * 200_000 if op == "long-cell" else '"' + r[j]
    main = lf.emit(folder, [(rel, head, rows, sep), *grids[1:]])
    data = main.read_bytes()
    if op == "cut-off":
        main.write_bytes(data[: int(len(data) * 0.7)])
    elif op == "zero-bytes":
        main.write_bytes(b"")
    elif op == "nul-bytes":
        main.write_bytes(data[: len(data) // 2] + b"\x00\x00" + data[len(data) // 2:])
    return main


@pytest.mark.parametrize("op", DAMAGE)
@pytest.mark.parametrize("name", list(lf.FORMATS))
def test_damage_is_read_with_a_note_or_refused_in_words(tmp_path, exp, name, op):
    main = _damage(tmp_path / "x", name, op, exp)
    before = main.read_bytes()
    out = downstream.analyze(tmp_path / "x", analysis_cfg=CFG)
    assert not _crashes(out), _crashes(out)
    assert out.report is not None and out.report.is_file()
    assert main.read_bytes() == before, "the input is never rewritten"
    try:
        _method, m, notes = _load(tmp_path / "x")
    except anytable.TableError as exc:
        assert main.name in str(exc) or "table" in str(exc), str(exc)
        return
    said = " | ".join(notes + (m.notes if m is not None else []))
    if op == "long-cell" and m is not None:   # a long ID list is ordinary: the numbers are all there
        assert len(m.features) >= 60 and len(m.samples) == 6
    if op == "nul-bytes" and lf.METHOD[name] not in ("DIA", "LFQ"):  # FragPipe's reader has no notes channel
        assert "NUL byte" in said
    if op == "cut-off" and name in ("maxquant", "alphadia", "pd", "spectronaut-pivot", "msstats", "table-tsv"):
        assert "looks cut off" in said, said


@pytest.mark.parametrize("op", ["bom", "crlf", "quote", "utf16", "cut-off", "zero-bytes", "header-only", "nul-bytes",
                                "long-cell", "rows-twice", "column-twice"])
@pytest.mark.parametrize("name", list(lf.TMT_FORMATS))
def test_tmt_formats_take_the_same_changes(tmp_path, name, op):
    t = lf.plexes(n=30)
    grids = lf.TMT_FORMATS[name](t)
    lf.emit(tmp_path / "base", grids)
    _method, m0, _ = _load(tmp_path / "base")
    rel, head, rows, sep = grids[0]
    head, rows = list(head), [list(r) for r in rows]
    if op == "header-only":
        rows = []
    elif op == "long-cell":
        rows[1][1] = f"{rows[1][1]}{'x' * 200_000}"
    elif op == "rows-twice":
        rows += [list(r) for r in rows[:10]]
    elif op == "column-twice":
        head.append(head[-1])
        rows = [r + [r[-1]] for r in rows]
    kw = {"bom": op == "bom", "crlf": op == "crlf", "quote": op == "quote",
          "encoding": "utf-16" if op == "utf16" else "utf-8"}
    main = lf.emit(tmp_path / "x", [(rel, head, rows, sep), *grids[1:]], **kw)
    data = main.read_bytes()
    if op == "cut-off":
        main.write_bytes(data[: int(len(data) * 0.7)])
    elif op == "zero-bytes":
        main.write_bytes(b"")
    elif op == "nul-bytes":
        main.write_bytes(data[: len(data) // 2] + b"\x00\x00" + data[len(data) // 2:])
    out = downstream.analyze(tmp_path / "x", analysis_cfg=CFG)
    assert not _crashes(out), _crashes(out)
    try:
        _method, m, _ = _load(tmp_path / "x")
    except anytable.TableError:
        assert op in ("cut-off", "zero-bytes", "header-only", "nul-bytes")
        return
    if op in ("bom", "crlf", "quote", "utf16"):
        assert m is not None and _same(_snapshot(m), _snapshot(m0)) == ""
    if op == "cut-off" and m is not None:
        assert any("cut off" in n for n in m.notes), m.notes


# --------------------------------------------------------------------- size --

ROWS_PER_PROTEIN = {"diann-long": 12.5, "msstats": 18, "spectronaut-long": 5.8, "sage": 3}


@pytest.mark.parametrize("name", list(lf.FORMATS))
def test_a_hundred_thousand_rows(tmp_path, name):
    n = int(100_000 / ROWS_PER_PROTEIN.get(name, 1))
    e = lf.experiment(tmp_path / "sim", n=n, seed=3)
    lf.emit(tmp_path / "x", lf.FORMATS[name](e))
    t0 = time.perf_counter()
    _method, m, _ = _load(tmp_path / "x")
    took = time.perf_counter() - t0
    assert len(m.samples) == 6 and len(m.features) >= 0.99 * n
    assert took < 120, f"{name}: {took:.1f} s for 100,000 rows"   # about 1-15 s on the development PC


# -------------------------------------------------------------- regressions --


def test_a_cell_longer_than_csvs_limit_is_read(tmp_path, exp):
    """MaxQuant's 'Evidence IDs' of an abundant protein in a big experiment passes csv's 131,072-character default:
    every csv-based reader crashed (CRASH_READ)."""
    g = lf.maxquant(exp)
    rel, h, rows, sep = g[0]
    lf.emit(tmp_path, [(rel, [*h, "Evidence IDs"], [[*r, ";".join(map(str, range(30_000)))] for r in rows], sep), *g[1:]])
    out = downstream.analyze(tmp_path, analysis_cfg=CFG)
    assert not _crashes(out) and len(out.summary["samples"]) == 6
    p = tmp_path / "pg.tsv"
    p.write_text("Protein.Group\tGenes\tA_1\tB_1\nP1\t" + "G;" * 100_000 + "\t5\t6\n", encoding="utf-8")
    assert tables.read_tsv(p)[1][0]["B_1"] == "6"


def test_a_utf16_report_is_still_recognised(tmp_path, exp):
    """Excel's 'Unicode text' is UTF-16: the engines' header reader saw nothing, so a Spectronaut report was read as
    an unknown table (two 'samples', R.Replicate and PG.Quantity) and a pg_matrix had no runs."""
    lf.emit(tmp_path / "sn", lf.spectronaut_long(exp), encoding="utf-16")
    assert engines.detect(tmp_path / "sn").method == "Spectronaut"
    lf.emit(tmp_path / "dia", lf.fragpipe_dia(exp), encoding="utf-16")
    assert len(quant.from_pg_matrix(tmp_path / "dia" / "fragpipe" / "diann-output" / "report.pg_matrix.tsv").samples) == 6


@pytest.mark.parametrize("name", ["spectronaut-long", "pd", "msstats"])
def test_an_engine_report_saved_as_xlsx_is_still_that_engine(tmp_path, exp, name):
    """Proteome Discoverer exports to Excel, and a report opened in Excel gets saved as .xlsx: the engines' header
    reader skipped .xlsx, so a Spectronaut long report was analysed as a plain table with 'R.Replicate' and
    'PG.Quantity' as its two samples."""
    lf.emit(tmp_path / "txt", lf.FORMATS[name](exp))
    _method, m0, _ = _load(tmp_path / "txt")
    rel, head, rows, _sep = lf.FORMATS[name](exp)[0]
    lf.xlsx(tmp_path / "x" / (Path(rel).stem + ".xlsx"), head, rows)
    method, m, _ = _load(tmp_path / "x")
    assert method == lf.METHOD[name] and _same(_snapshot(m0), _snapshot(m)) == ""


def test_a_diann_report_named_by_out_is_found(tmp_path, exp):
    """DIA-NN names its main report after --out (exp1.tsv, or exp1.parquet in 2.x): only report.tsv was recognised,
    so without the matrices the long report was read as a plain table (Q.Value, PG.MaxLFQ ... as samples)."""
    lf.emit(tmp_path / "a", [("exp1.tsv", lf.DIANN_LONG, lf._diann_rows(exp), "\t")])
    (tmp_path / "a" / "exp1.stats.tsv").write_text("File.Name\tPrecursors.Identified\nx\t5\n", encoding="utf-8")
    found = engines.detect(tmp_path / "a")
    assert (found.method, found.path.name) == ("DIA-NN", "exp1.tsv")
    assert engines.detect(tmp_path / "a" / "exp1.tsv").method == "DIA-NN"
    if _pyarrow():
        p = lf.parquet(tmp_path / "b", exp)
        p.replace(tmp_path / "b" / "exp1.parquet")
        (tmp_path / "b" / "exp1.stats.tsv").write_text("File.Name\n", encoding="utf-8")
        (tmp_path / "b" / "exp1-lib.parquet").write_bytes(b"PAR1 a library PAR1")
        found = engines.detect(tmp_path / "b")
        assert (found.method, found.path.name) == ("DIA-NN", "exp1.parquet")
        assert len(engines.load_diann_long(found.path).features) == len(exp.prots)


def test_cp1252_sample_names_keep_their_letters(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_bytes("Protein\tKontrolle_ä_1\tKontrolle_ä_2\tBehandelt_1\tBehandelt_2\nP1\t10\t11\t20\t21\n"
                  .encode("cp1252"))
    notes: list[str] = []
    head, _rows = anytable.read_table(p, notes)
    assert head[1] == "Kontrolle_ä_1" and "cp1252" in notes[0]
    assert tables.read_header(p)[1] == "Kontrolle_ä_1"


def test_quoted_excel_saves_of_fragpipe_tables(tmp_path, exp):
    """A pg_matrix saved again from Excel has its cells in quotes: no run was found (0 samples). A quoted cell
    holding a tab shifted every later column of its row (silently wrong numbers)."""
    lf.emit(tmp_path / "q", lf.fragpipe_dia(exp), quote=True)
    m = quant.from_pg_matrix(tmp_path / "q" / "fragpipe" / "diann-output" / "report.pg_matrix.tsv")
    assert m.samples == lf.RUNS and m.features[0].id == exp.prots[0][0]
    _write_changed(tmp_path / "t", "fragpipe-dia", "delimiter-in-text", exp)
    m2 = quant.from_pg_matrix(tmp_path / "t" / "fragpipe" / "diann-output" / "report.pg_matrix.tsv")
    assert _same(_snapshot(m), _snapshot(m2)) == ""
    assert "kinase\tdomain" in m2.features[0].description


def test_proteome_discoverer_saved_as_csv_keeps_its_conditions(tmp_path, exp):
    """'Abundances (Normalized): F1: Sample, DMSO' in a comma file is quoted; the header reader split it at its
    commas, so the conditions were lost."""
    g = lf.pd(exp)
    lf.emit(tmp_path, [("Proteins.csv", g[0][1], g[0][2], ",")])
    m = engines.load_pd(tmp_path / "Proteins.csv")
    assert m.samples == lf.RUNS and m.condition["Drug_2"] == "Drug" and m.meta["conditions_from"] == "engine"


def test_a_spectronaut_report_with_decimal_commas(tmp_path, exp):
    """Spectronaut on a German or French Windows writes 1234,56 in its tab-separated report: every quantity was
    read as missing (and every q-value as missing, so nothing was filtered)."""
    lf.emit(tmp_path / "a", lf.spectronaut_long(exp))
    rel, h, rows, sep = lf.spectronaut_long(exp)[0]
    rows = [[str(c).replace(".", ",") if isinstance(c, float) else c for c in r] for r in rows]
    lf.emit(tmp_path / "b", [(rel, h, rows, sep)])
    a = engines.load_spectronaut(tmp_path / "a" / rel)
    b = engines.load_spectronaut(tmp_path / "b" / rel)
    assert _same(_snapshot(a), _snapshot(b)) == ""
    assert any("decimal comma" in n for n in b.notes)


def test_a_semicolon_file_with_dots_between_the_thousands(tmp_path):
    """Excel on a German Windows writes 1.234.567,8 in a semicolon file when the cells show thousands: those were
    not numbers, so a column was left out as 'too many cells are not numbers'."""
    p = tmp_path / "t.csv"
    p.write_text("Protein;A_1;B_1\nP1;1.234.567,8;12,5\nP2;2.000,0;1.234\n", encoding="utf-8")
    _h, rows = anytable.read_table(p)
    assert [tables.num(r[1]) for r in rows] == [1234567.8, 2000.0] and rows[0][2] == "12.5"
    assert rows[1][2] == "1.234"   # ambiguous on its own: left as it is


def test_rows_without_a_q_value_do_not_pass_the_filter(tmp_path, exp):
    rows = lf._diann_rows(exp)
    for r in rows:
        if r[6] == "BADK2":   # a precursor at 1e12 whose q-values are blank: it must not be the protein's value
            r[8] = r[9] = ""
    lf.emit(tmp_path, [("report.tsv", lf.DIANN_LONG, rows, "\t")])
    m = engines.load_diann_long(tmp_path / "report.tsv")
    assert max(v for row in m.values for v in row if v is not None) < 30
    assert any("blank or unreadable q-value" in n for n in m.notes)
    for r in rows:   # a q-value column with no number at all is not a filter: every row would go
        r[9] = "NaN"
    lf.emit(tmp_path / "n", [("report.tsv", lf.DIANN_LONG, rows, "\t")])
    m2 = engines.load_diann_long(tmp_path / "n" / "report.tsv")
    assert len(m2.features) == len(exp.prots) and any("PG.Q.Value holds no numbers" in n for n in m2.notes)


def test_sage_column_twice_and_repeated_ions_are_not_added(tmp_path, exp):
    """lfq.tsv with a file's column twice doubled that file's intensities (it was read as two fractions); a
    repeated peptide-ion row was added to the first."""
    lf.emit(tmp_path / "a", lf.sage(exp))
    rel, h, rows, sep = lf.sage(exp)[0]
    lf.emit(tmp_path / "b", [(rel, [*h, h[-1]], [[*r, r[-1]] for r in rows] + rows[:6], sep)])
    a = engines.load_sage(tmp_path / "a" / rel)
    b = engines.load_sage(tmp_path / "b" / rel)
    assert _same(_snapshot(a), _snapshot(b)) == ""
    text = " | ".join(b.notes)
    assert "appear more than once" in text and "6 row(s) repeat a peptide ion" in text


def test_a_damaged_sage_results_json_never_crashes(tmp_path, exp):
    lf.emit(tmp_path, lf.sage(exp))
    (tmp_path / "sage" / "results.json").write_text(json.dumps({"version": ["0.14"], "database": "human.fasta"}),
                                                     encoding="utf-8")
    out = downstream.analyze(tmp_path, analysis_cfg=CFG)
    assert not _crashes(out) and len(out.summary["samples"]) == 6
    assert engines.provenance(tmp_path, "Sage")["engine"] == "Sage"


def test_a_damaged_parquet_report_is_refused_in_words(tmp_path, exp):
    """A cut or still-being-written report.parquet raised pyarrow's own error (CRASH_READ)."""
    pytest.importorskip("pyarrow")
    p = lf.parquet(tmp_path / "ok", exp)
    for name, data in (("cut", p.read_bytes()[:500]), ("empty", b""), ("text", b"Run\tProtein.Group\n")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "report.parquet").write_bytes(data)
        with pytest.raises(anytable.TableError, match="report.parquet"):
            engines.load_diann_long(tmp_path / name / "report.parquet")
        out = downstream.analyze(tmp_path / name, analysis_cfg=CFG)
        assert not _crashes(out) and "UNUSABLE_TABLE" in {i.code for i in out.issues}


def test_a_parquet_report_with_nulls(tmp_path, exp):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    rows = lf._diann_rows(exp)
    cols = {h: [r[k] for r in rows] for k, h in enumerate(lf.DIANN_LONG)}
    cols["PG.MaxLFQ"] = [None if k % 7 == 0 else v for k, v in enumerate(cols["PG.MaxLFQ"])]
    cols["Q.Value"] = [None if k % 9 == 0 else v for k, v in enumerate(cols["Q.Value"])]
    pq.write_table(pa.table(cols), tmp_path / "report.parquet")
    m = engines.load_diann_long(tmp_path / "report.parquet")
    assert len(m.features) == len(exp.prots) and any("blank or unreadable q-value" in n for n in m.notes)


def test_a_sample_named_with_a_superscript_digit(tmp_path):
    """'Ctrl_²' looks like a replicate number to str.isdigit() but int() refuses it: the read crashed."""
    p = tmp_path / "t.tsv"
    p.write_text("Protein\tCtrl_²\tDrug_²\tCtrl_1\tDrug_1\n" + "".join(
        f"P{k}\t{100 + k}\t{200 + k}\t{110 + k}\t{190 + k}\n" for k in range(30)), encoding="utf-8")
    m = anytable.load(p)
    assert "Ctrl_²" in m.samples and "Ctrl_²" not in m.replicate


def test_an_engines_empty_file_says_it_is_empty(tmp_path):
    """A 0-byte proteinGroups.txt or report.tsv (the engine stopped, or the copy did not finish) was 'no result
    table found' in a FragPipe folder; now the table is named and called empty."""
    for rel in ("combined/txt/proteinGroups.txt", "report.tsv", "sage/lfq.tsv", "pg.matrix.tsv"):
        d = tmp_path / rel.replace("/", "_")
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(b"")
        out = downstream.analyze(d, analysis_cfg=CFG)
        issue = {i.code: i for i in out.issues}.get("UNUSABLE_TABLE")
        assert issue is not None and "0 bytes" in issue.message, [(i.code, i.message) for i in out.issues]


def test_an_unclosed_quote_does_not_swallow_the_file(tmp_path):
    rows = [f'P{k},"G{k},{100 + k},{200 + k},{110 + k},{190 + k}' if k == 3 else f"P{k},G{k},{100 + k},{200 + k},"
            f"{110 + k},{190 + k}" for k in range(40)]
    p = tmp_path / "t.csv"
    p.write_text("Protein,Gene,Ctrl_1,Drug_1,Ctrl_2,Drug_2\n" + "\n".join(rows) + "\n", encoding="utf-8")
    notes: list[str] = []
    _head, got = anytable.read_table(p, notes)
    assert len(got) == 40 and got[3][1] == '"G3' and "never closed" in notes[0]


def test_a_cut_off_last_row_is_left_out_with_a_note(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_text("Protein\tA_1\tA_2\tB_1\tB_2\nP1\t1\t2\t3\t4\nP2\t5\t6\t7\t8\nP3\t9\t1", encoding="utf-8")
    notes: list[str] = []
    _h, rows = anytable.read_table(p, notes)
    assert [r[0] for r in rows] == ["P1", "P2"] and "looks cut off" in notes[0]
    p.write_text("Protein\tA_1\nP1\t1\nP2\t5", encoding="utf-8")   # complete, only without a final line break
    assert len(anytable.read_table(p, [])[1]) == 2


def test_a_run_column_twice_in_a_pg_matrix_is_read_once(tmp_path, exp):
    rel, h, rows, sep = lf.fragpipe_dia(exp)[0]
    lf.emit(tmp_path, [(rel, [*h, h[-1]], [[*r, r[-1]] for r in rows], sep)])
    m = quant.from_pg_matrix(tmp_path / rel)
    assert m.samples == lf.RUNS and any("more than one column named Drug_3" in n for n in m.notes)


def test_an_sdrf_saved_from_excel_is_still_the_design(tmp_path):
    rows = [["source name", "characteristics[biological replicate]", "assay name", "comment[data file]",
             "factor value[compound]"]] + [[f"s{k}", str(k % 3 + 1), f"run{k}", f"{r}.raw", r.split("_")[0]]
                                           for k, r in enumerate(lf.RUNS)]
    for enc, quote in (("utf-16", False), ("utf-8", True)):
        p = tmp_path / enc / "design.sdrf.tsv"
        p.parent.mkdir()
        p.write_bytes(lf.text(rows[0], rows[1:], "\t", quote).encode(enc))
        d = sdrfdesign.read(p)
        assert d.factors == ["compound"] and [r.condition for r in d.rows][:4] == ["DMSO"] * 3 + ["Drug"]
        assert sdrfdesign.find(p.parent)[0] == p


@pytest.mark.parametrize("method", [None, "isoDTB", "TMT", "LFQ", "DIA", "DIA-NN", "MaxQuant", "Sage", "AlphaDIA",
                                    "Spectronaut", "PD", "MSstats", "MSstatsTMT", "table"])
def test_provenance_never_crashes_on_odd_files(tmp_path, method):
    """Logs, parameter files and Sage's record that are binary, empty, of the wrong shape, or folders by those
    names: provenance gives what it can and never raises."""
    d = tmp_path / "x"
    (d / "sage").mkdir(parents=True)
    (d / "log_2026-10-15.txt").write_bytes(b"\xff\xfe\x00F\x00r\x00\x00\x00" * 50)
    (d / "fragpipe.workflow").write_bytes(b"database.db-path=\xe9\\:\\x\n")
    (d / "report.log.txt").write_bytes(b"")
    (d / "parameters.txt").write_bytes(b"Version\t\xff\xfe\n")
    (d / "mqpar.xml").mkdir()
    (d / "frozen_config.yaml").write_text("version: [1, 2]\n", encoding="utf-8")
    (d / "sage" / "results.json").write_text("[1, 2, 3]", encoding="utf-8")
    (d / "sage" / "lfq.tsv").write_bytes(b"")
    for target in (d, d / "sage" / "lfq.tsv", tmp_path / "missing"):
        prov = engines.provenance(target, method, source=str(d / "sage" / "lfq.tsv"), meta={"engine": 5})
        assert isinstance(prov, dict)
    (d / "sage" / "results.json").write_text(json.dumps({"version": None, "database": [1]}), encoding="utf-8")
    assert isinstance(engines.provenance(d, method), dict)


def test_numbers_like_excel_writes_them_in_an_engine_table(tmp_path, exp):
    """#N/A, Filtered and NaN in a long report's quantity are missing values, never numbers or crashes."""
    rows = lf._diann_rows(exp)
    for k, r in enumerate(rows):
        if k % 11 == 0:
            r[10] = ["#N/A", "Filtered", "NaN", "", "1e999"][k % 5]
    lf.emit(tmp_path, [("report.tsv", lf.DIANN_LONG, rows, "\t")])
    m = engines.load_diann_long(tmp_path / "report.tsv")
    assert all(v is None or (math.isfinite(v) and v < 30) for row in m.values for v in row)
