"""The analysis on messy input (D60): malformed but plausible tables through downstream.analyze() and the loaders.

Three promises are checked for every case here: analyze() does not raise, a report and a strictly valid
analysis.json are written, and no stage crashes (a CRASH_* issue is a bug, not an answer). A messy input must
also be talked about: an issue or a note says what was done with it.

    MESSY            hand-made tables, one per kind of mess, each with what the notes / issues must say
    seeded fuzz      FragPipe tables (DIA, TMT, isoDTB) from simulate.py with random damage, random settings
    regressions      the bugs the fuzzing found, one test each
    guards           statistics that run but may not mean what they say (guards.py): each is reported
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import anytable, fpa, guards, quant, simulate

CFG = {"enrichment": False}
CONDS = (("DMSO", 3), ("Drug", 3))


def base(rng, n=120, conds=CONDS, log2=False):
    """A plain table: Protein, Gene, one intensity column per sample; every 10th protein 4-fold up in Drug."""
    samples = [f"{c}_{r}" for c, k in conds for r in range(1, k + 1)]
    rows = []
    for i in range(n):
        b = rng.gauss(22, 2)
        vals = []
        for s in samples:
            v = b + (2.0 if i % 10 == 0 and not s.startswith("DMSO") else 0.0) + rng.gauss(0, 0.3)
            vals.append("" if rng.random() < 0.05 else (f"{v:.4f}" if log2 else f"{2 ** v:.1f}"))
        rows.append([f"P{10000 + i}", f"GENE{i}", *vals])
    return ["Protein", "Gene", *samples], rows


def write(path: Path, header, rows, delim="\t") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(delim.join(header) + "\n")
        for r in rows:
            fh.write(delim.join(r) + "\n")
    return path


def _strict_json(path: Path) -> dict:
    def refuse(token):
        raise ValueError(f"{token} is not JSON")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=refuse)


def check_outcome(out, results: Path) -> dict:
    """What every analysis must leave behind, however bad the input."""
    assert out.report is not None and out.report.is_file() and out.report.stat().st_size > 500, "a report is written"
    html = out.report.read_text(encoding="utf-8")
    assert "Ionomos" in html
    summary = _strict_json(results / "analysis.json")  # no NaN / Infinity tokens
    crashed = [i.code for i in out.issues if i.code.startswith("CRASH")]
    err = results / "analysis_error.txt"
    assert not crashed, f"{crashed}: {err.read_text(encoding='utf-8')[-1500:] if err.is_file() else ''}"
    assert isinstance(summary["trust"], dict)
    for c in summary["comparisons"]:
        assert (results / Path(c["table"]).name).is_file()
    return summary


def run_table(tmp_path: Path, header, rows, delim="\t", **settings):
    table = write(tmp_path / ("t.csv" if delim != "\t" else "t.tsv"), header, rows, delim)
    ws = tmp_path / "t_ionomos"
    ws.mkdir(exist_ok=True)
    out = downstream.analyze(ws, analysis_cfg={**CFG, **settings}, table=table)
    return out, check_outcome(out, ws / "results")


def said(out) -> str:
    return " | ".join([*out.warnings, *(f"{i.code}: {i.title} {i.message}" for i in out.issues)])


# ------------------------------------------------------------- hand-made mess --


def _dup_ids(rng):
    h, rows = base(rng)
    for r in rows[:30]:
        r[0], r[1] = "P1", "DUP"
    return h, rows


def _dup_sample_names(rng):
    h, rows = base(rng)
    h[3], h[6] = h[2], h[5]
    return h, rows


def _all_one_name(rng):
    h, rows = base(rng)
    h[2:] = ["Intensity"] * 6
    return h, rows


def _empty_names(rng):
    h, rows = base(rng)
    h[2], h[5] = "", " "
    return h, rows


def _unicode_names(rng):
    h, rows = base(rng)
    h[2:] = ["Kontrolle_ä_1", "Kontrolle_ä_2", "Kontrolle_ä_3", "薬_1", "薬_2", "薬_3"]
    return h, rows


def _blank(cols):
    def make(rng):
        h, rows = base(rng)
        for r in rows:
            for j in cols:
                r[j] = ""
        return h, rows
    return make


def _missing_rows(rng):
    h, rows = base(rng)
    for r in rows[:40]:
        r[2:] = [""] * 6
    return h, rows


def _all(value):
    def make(rng):
        h, rows = base(rng)
        for r in rows:
            r[2:] = [value] * 6
        return h, rows
    return make


def _zero_variance(rng):
    h, rows = base(rng)
    for i, r in enumerate(rows):
        r[2:] = [str(1000 * (i + 1))] * 3 + [str((4000 if i % 10 == 0 else 1000) * (i + 1))] * 3
    return h, rows


def _infinities(rng):
    h, rows = base(rng)
    for r in rows[::3]:
        r[2], r[5], r[6], r[7] = "Inf", "-Inf", "inf", "NaN"
    return h, rows


def _negative(rng):
    h, rows = base(rng)
    for r in rows[::4]:
        r[2], r[3] = "0", "-1500.5"
    return h, rows


def _text_cells(rng):
    h, rows = base(rng)
    junk = ["n.d.", "#DIV/0!", "<LOD", "Filtered", "#VALUE!", "1.2.3", "--", "?"]
    for r in rows:
        if rng.random() < 0.3:
            r[rng.randrange(2, 8)] = rng.choice(junk)
    return h, rows


def _text_column(rng):
    h, rows = base(rng)
    for r in rows:
        r[4] = "Filtered"
    return h, rows


def _decimal_commas(rng):
    h, rows = base(rng)
    for r in rows:
        r[2:] = [v.replace(".", ",") for v in r[2:]]
    return h, rows


def _thousands(rng):
    h, rows = base(rng)
    for r in rows:
        r[2:] = [f"{float(v):,.1f}" if v else "" for v in r[2:]]
    return h, rows


def _huge(rng):
    h, rows = base(rng)
    for r in rows[::5]:
        r[2], r[3], r[5], r[6] = "1e300", "1e308", "1e400", "9" * 400
    return h, rows


def _tiny(rng):
    h, rows = base(rng)
    for r in rows[::5]:
        r[2], r[3] = "1e-300", "5e-324"
    return h, rows


def _ragged(rng):
    h, rows = base(rng)
    for r in rows[::7]:
        del r[5:]
    for r in rows[1::7]:
        r += ["extra", "cells"]
    return h, rows


def _blank_ids(rng):
    h, rows = base(rng)
    for r in rows[::2]:
        r[0] = r[1] = ""
    return h, rows


def _identical_samples(rng):
    h, rows = base(rng)
    for r in rows:
        r[3] = r[4] = r[2]
    return h, rows


def _hostile(rng):
    h, rows = base(rng)
    h[2:] = ["<b>c</b>_1", "<b>c</b>_2", "<b>c</b>_3", "x&\"'_1", "x&\"'_2", "x&\"'_3"]
    rows[0][1] = "</script><script>alert(1)</script>"
    return h, rows


def _spaces(rng):
    h, rows = base(rng)
    return [f"  {x} " for x in h], [[f" {c}  " for c in r] for r in rows]


# name -> (maker, text the notes / issues must hold; None = a clean table, nothing to say)
MESSY = {
    "plain": (base, None),
    "log2 values": (lambda rng: base(rng, log2=True), "look like log2 already"),
    "duplicate IDs": (_dup_ids, "30 rows share their ID"),
    "duplicate sample names": (_dup_sample_names, "column name(s) used more than once: DMSO_1, Drug_1"),
    "every column one name": (_all_one_name, "ONE_CONDITION"),
    "empty sample names": (_empty_names, "2 numeric column(s) without a name"),
    "unicode sample names": (_unicode_names, "NO_CONTROL"),
    "hostile names": (_hostile, "NO_CONTROL"),
    "all-missing rows": (_missing_rows, None),
    "an all-missing column": (_blank([4]), "no values at all were left out: DMSO_3"),
    "an all-missing condition": (_blank([5, 6, 7]), "ONE_CONDITION"),
    "everything missing": (_all("NA"), "UNUSABLE_TABLE"),
    "one replicate": (lambda rng: base(rng, conds=(("DMSO", 1), ("Drug", 1))), "FOLD_CHANGE_ONLY"),
    "one condition": (lambda rng: base(rng, conds=(("DMSO", 4),)), "ONE_CONDITION"),
    "one sample": (lambda rng: base(rng, conds=(("DMSO", 1),)), "ONE_SAMPLE"),
    "1 vs 6": (lambda rng: base(rng, conds=(("DMSO", 1), ("Drug", 6))), "LOW_CONFIDENCE"),
    "2 vs 4": (lambda rng: base(rng, conds=(("DMSO", 2), ("Drug", 4))), None),
    "3 + 3 + 1": (lambda rng: base(rng, conds=(("DMSO", 3), ("Drug", 3), ("Other", 1))), "LOW_CONFIDENCE"),
    "constant values": (_all("1000000"), "ZERO_VARIANCE"),
    "zero variance in every group": (_zero_variance, "ZERO_VARIANCE"),
    "all zero": (_all("0"), "ZERO_VARIANCE"),
    "infinities": (_infinities, "infinite or too large"),
    "negative and zero intensities": (_negative, "30 negative intensities"),
    "text in numeric cells": (_text_cells, "are not numbers"),
    "a column of text": (_text_column, "no values at all were left out: DMSO_3"),
    "decimal commas in a tab file": (_decimal_commas, "use a decimal comma"),
    "thousands separators": (_thousands, "thousands separators"),
    "huge values": (_huge, "infinite or too large"),
    "tiny values": (_tiny, "outside any measurable range"),
    "1 feature": (lambda rng: base(rng, n=1), "only 1 feature(s): median normalisation"),
    "2 features": (lambda rng: base(rng, n=2), "FEW_FEATURES"),
    "a header and no rows": (lambda rng: (base(rng)[0], []), "UNUSABLE_TABLE"),
    "ragged rows": (_ragged, "fewer cells than the header"),
    "blank IDs": (_blank_ids, "60 row(s) have no ID"),
    "identical samples": (_identical_samples, "IDENTICAL_SAMPLES"),
    "spaces around every cell": (_spaces, None),
    "one numeric column": (lambda rng: (base(rng)[0][:3], [r[:3] for r in base(rng)[1]]), "ONE_SAMPLE"),
}


@pytest.mark.parametrize("name", list(MESSY))
def test_a_messy_table_is_analysed_and_talked_about(tmp_path, name):
    make, expect = MESSY[name]
    header, rows = make(random.Random(7))
    out, summary = run_table(tmp_path, header, rows)
    if expect:
        assert expect in said(out), said(out)
    assert out.warnings or out.issues  # even a clean table says how it was read


SETTINGS = [{"imputation": imp, "normalize": norm} for imp in ("none", "min", "zero", "mindet", "minprob", "knn")
            for norm in ("median", "gn", "none")] + \
           [{"test": t, "de_type": d} for t in ("welch", "student") for d in ("control", "all", "others")] + \
           [{"de_type": "others"}, {"block": "replicate"}, {"variance_prior": "deqms"}, {"filter_condition_pct": 100},
            {"filter_global_pct": 100}, {"min_valid": 5}, {"use_adjusted": False, "log2fc": 0}]


def test_messy_tables_with_every_kind_of_setting(tmp_path):
    """Each messy table with three settings drawn from the list (seeded): 111 analyses, none may crash."""
    rng = random.Random(2026)
    for k, (name, (make, _expect)) in enumerate(MESSY.items()):
        header, rows = make(random.Random(7))
        for j, s in enumerate(rng.sample(SETTINGS, 3)):
            d = tmp_path / f"{k}_{j}"
            try:
                run_table(d, header, rows, **s)
            except AssertionError as exc:
                raise AssertionError(f"{name} with {s}: {exc}") from exc


def test_semicolon_file_with_decimal_commas_still_reads(tmp_path):
    header, rows = _decimal_commas(random.Random(7))
    out, summary = run_table(tmp_path, header, rows, delim=";")
    assert summary["comparisons"][0]["tested"] == 120 and summary["comparisons"][0]["up"] >= 8


# ---------------------------------------------------------------- seeded fuzz --

JUNK = ["", "NA", "NaN", "Inf", "-Inf", "0", "-5", "n.d.", "#DIV/0!", "1,5", "1e400", "1e-320", "Filtered", " ", "ü",
        "1.2.3", "TRUE", "0.0", "-0", "1e308", "\"", "'", "<x>", "1 000", "１２", "5E", "+", "--3"]
OPS = ["cells", "dupcol", "blankhead", "delcol", "duprows", "blankcol", "trunc", "bom", "crlf", "nul", "empty",
       "headeronly", "samehead", "unicodehead", "constcol", "constall", "shuffle_header", "ragged", "dropmeta",
       "blankrow", "dupid", "quote"]


def make_fragpipe_table(method: str, root: Path, rng) -> Path:
    if method == "DIA":
        runs = [(f"C:\\d\\DMSO_{r}.raw", "DMSO") for r in range(1, rng.choice([1, 2, 3, 6]) + 1)] + \
               [(f"C:\\d\\Drug_{r}.raw", "Drug") for r in range(1, rng.choice([1, 2, 3, 6]) + 1)]
        p = root / "fragpipe" / "diann-output" / "report.pg_matrix.tsv"
        simulate.dia_pg_matrix(p, runs, seed=rng.randrange(99), n_proteins=rng.choice([1, 3, 40, 150]))
    elif method == "TMT":
        p = root / "fragpipe" / "tmt-report" / "abundance_gene_MD.tsv"
        ch = ["126", "127N", "127C", "128N", "128C", "129N"]
        simulate.tmt_abundance(p, [f"{c}_1_{ch[i]}" for i, c in enumerate(["DMSO"] * 3 + ["Drug"] * 3)],
                               seed=rng.randrange(99), n_genes=rng.choice([1, 3, 40, 150]))
    else:
        p = root / "fragpipe" / "combined_modified_peptide_label_quant.tsv"
        simulate.isodtb_label_quant(p, {"EXP": [1, 2, 3][: rng.choice([1, 2, 3])]}, seed=rng.randrange(99),
                                    n_sites=rng.choice([1, 3, 40, 150]))
    return p


def damage(path: Path, rng) -> list[str]:
    """One to four random kinds of damage to a tab-separated table, in place. Returns what was done."""
    grid = [ln.split("\t") for ln in path.read_text(encoding="utf-8").split("\n") if ln]
    ops = [rng.choice(OPS) for _ in range(rng.randint(1, 4))]
    for op in ops:
        ncol = len(grid[0]) if grid else 0
        if op == "cells" and len(grid) > 1:
            for _ in range(rng.randint(1, max(2, len(grid) * ncol // 5))):
                r = grid[rng.randrange(1, len(grid))]
                if r:
                    r[rng.randrange(len(r))] = rng.choice(JUNK)
        elif op == "dupcol" and ncol:
            j = rng.randrange(ncol)
            for r in grid:
                r.append(r[j] if j < len(r) else "")
        elif op == "blankhead" and ncol:
            grid[0][rng.randrange(ncol)] = ""
        elif op == "delcol" and ncol > 1:
            j = rng.randrange(ncol)
            for r in grid:
                if j < len(r):
                    del r[j]
        elif op == "duprows" and len(grid) > 1:
            grid += [list(r) for r in grid[1: 1 + rng.randint(1, 20)]]
        elif op in ("blankcol", "constcol") and ncol:
            j = rng.randrange(ncol)
            for r in grid[1:]:
                if j < len(r):
                    r[j] = "" if op == "blankcol" else "1000"
        elif op == "trunc" and len(grid) > 1:
            grid[-1] = grid[-1][: rng.randrange(len(grid[-1]) + 1)]
        elif op == "empty":
            grid = []
        elif op == "headeronly":
            grid = grid[:1]
        elif op == "samehead" and ncol:
            grid[0][rng.randrange(ncol)] = grid[0][rng.randrange(ncol)]
        elif op == "unicodehead" and ncol:
            grid[0][rng.randrange(ncol)] = rng.choice(["Ünï_1", "薬_2", "a b_1", "💊_3", "x\u200b_1", "ctrl 1"])
        elif op == "constall":
            for r in grid[1:]:
                for j, c in enumerate(r):
                    if c.replace(".", "").replace("-", "").isdigit():
                        r[j] = "7"
        elif op == "shuffle_header" and ncol:
            rng.shuffle(grid[0])
        elif op == "ragged":
            for r in grid[1:]:
                if rng.random() < 0.2:
                    del r[rng.randrange(len(r) + 1):]
        elif op == "dropmeta" and ncol > 3:
            for r in grid:
                del r[:3]
        elif op == "blankrow":
            grid.insert(rng.randrange(len(grid) + 1), [""] * ncol)
        elif op == "dupid" and len(grid) > 2:
            for r in grid[1:]:
                if r and rng.random() < 0.5:
                    r[0] = grid[1][0]
        elif op == "quote" and len(grid) > 1:
            r = grid[rng.randrange(1, len(grid))]
            if r:
                r[0] = '"unterminated'
    text = "\n".join("\t".join(r) for r in grid) + ("\n" if grid else "")
    if "crlf" in ops:
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if "bom" in ops:
        data = b"\xef\xbb\xbf" + data
    if "nul" in ops:
        data = data[: len(data) // 2] + b"\x00\x00" + data[len(data) // 2:]
    path.write_bytes(data)
    return ops


def fuzz_once(seed: int, root: Path):
    rng = random.Random(seed)
    method = rng.choice(["DIA", "TMT", "isoDTB", "DIA", "auto"])
    table = make_fragpipe_table("DIA" if method == "auto" else method, root, rng)
    ops = damage(table, rng)
    s = dict(CFG)
    if rng.random() < 0.5:
        s["imputation"] = rng.choice(["none", "min", "zero", "mindet", "minprob", "knn", "perseus"])
    if rng.random() < 0.3:
        s["normalize"] = rng.choice(["gn", "none"])
    if rng.random() < 0.3:
        s["test"] = rng.choice(["welch", "student"])
    if rng.random() < 0.3:
        s["de_type"] = rng.choice(["all", "others"])
    before = table.read_bytes()
    out = downstream.analyze(root, None if method == "auto" else method, analysis_cfg=s)
    assert table.read_bytes() == before, "the input table is never rewritten"
    return method, ops, s, out


FUZZ_SEEDS = 160


def test_seeded_fuzz_of_the_fragpipe_loaders(tmp_path):
    """160 damaged DIA / TMT / isoDTB tables with random settings. To look at one: fuzz_once(seed, folder).
    6,000 further seeds were run once while this was written (2026-10-01) and passed; this is the part kept
    in the suite. Damage can be harmless (a BOM, a renamed annotation column), so nothing is asserted about
    notes here: the MESSY table above does that case by case."""
    for seed in range(FUZZ_SEEDS):
        root = tmp_path / str(seed)
        method, ops, s, out = fuzz_once(seed, root)
        try:
            check_outcome(out, root / "results")
        except AssertionError as exc:
            raise AssertionError(f"seed {seed} ({method}, {ops}, {s}): {exc}") from exc


# ---------------------------------------------------------------- regressions --


def test_a_few_negative_cells_do_not_turn_intensities_into_log2(tmp_path):
    """Found by the fuzz: one negative cell made the loader read raw intensities as log2 values, and the
    statistics ran on numbers around a million."""
    header, rows = _negative(random.Random(7))
    out, summary = run_table(tmp_path, header, rows)
    m = anytable.load(tmp_path / "t.tsv")
    vals = [v for row in m.values for v in row if v is not None]
    assert 10 < min(vals) and max(vals) < 40, "log2 intensities"
    assert "look like raw intensities" in said(out) and "30 negative intensities" in said(out)
    assert summary["comparisons"][0]["up"] >= 6 and summary["quality"], "QC and insights ran"


def test_two_columns_with_one_name_keep_their_own_values(tmp_path):
    """Found by the fuzz: both columns named DMSO_1 were read from the same (last) column."""
    header, rows = _dup_sample_names(random.Random(7))
    write(tmp_path / "t.tsv", header, rows)
    m = anytable.load(tmp_path / "t.tsv")
    assert m.samples == ["DMSO_1", "DMSO_1.2", "DMSO_3", "Drug_1", "Drug_1.2", "Drug_3"]
    assert m.column("DMSO_1") != m.column("DMSO_1.2") and m.column("Drug_1") != m.column("Drug_1.2")
    assert [m.condition[s] for s in m.samples] == ["DMSO"] * 3 + ["Drug"] * 3
    assert guards.identical_samples(m.values, m.samples) == []


def test_columns_without_a_name_are_left_out_not_named_dot_2(tmp_path):
    header, rows = _empty_names(random.Random(7))
    write(tmp_path / "t.tsv", header, rows)
    m = anytable.load(tmp_path / "t.tsv")
    assert m.samples == ["DMSO_2", "DMSO_3", "Drug_2", "Drug_3"]
    assert any("without a name" in n for n in m.notes)


@pytest.mark.parametrize("cells, want, note", [
    (["1234,5", "0,25", "12,75"], [1234.5, 0.25, 12.75], "decimal comma"),
    (["1,234,567.8", "12,345.0", "999.5"], [1234567.8, 12345.0, 999.5], "thousands separators"),
    (["1,234", "12,345", "7"], [1234.0, 12345.0, 7.0], "If these are decimal commas"),
    (["1234,5", "0.25", "3"], [None, 0.25, 3.0], "one number format"),
])
def test_number_formats_in_a_tab_file(tmp_path, cells, want, note):
    write(tmp_path / "t.tsv", ["Protein", "A_1"], [[f"P{i}", c] for i, c in enumerate(cells)])
    notes: list[str] = []
    _header, rows = anytable.read_table(tmp_path / "t.tsv", notes)
    from ionomos.downstream.tables import num

    assert [num(r[1]) for r in rows] == want
    assert note in " ".join(notes)
    _h, plain = anytable.read_table(tmp_path / "t.tsv")  # the engines' reader is as it was
    assert [r[1] for r in plain] == cells


def test_values_no_instrument_produces_count_as_missing(tmp_path):
    """Found by the fuzz: 1e300 as an intensity (log2 996), or 1e308 in a log2 table, overflowed the QC."""
    header, rows = _huge(random.Random(7))
    out, summary = run_table(tmp_path, header, rows)
    assert summary["quality"] and "sample_qc.tsv" in {p.name for p in out.files}
    m = quant.QuantMatrix("intensity", "protein", [quant.Feature("a", "a"), quant.Feature("b", "b")], ["x", "y"],
                          [[1e308, 22.0], [math.inf, math.nan]], {"x": "c", "y": "c"})
    notes = guards.check_input(m)
    assert m.values == [[None, 22.0], [None, None]] and "3 value(s) are outside any measurable range" in notes[0]


def test_a_repeated_sample_column_in_a_fragpipe_table_is_dropped_with_a_note(tmp_path):
    """read_tsv keeps one column per name, so a TMT table with two columns named alike would count one
    channel twice."""
    p = tmp_path / "e/fragpipe/tmt-report/abundance_gene_MD.tsv"
    cols = ["DMSO_1_126", "DMSO_1_127N", "DMSO_1_127C", "Drug_1_128N", "Drug_1_128C", "Drug_1_129N"]
    simulate.tmt_abundance(p, cols, seed=3, n_genes=120)
    lines = p.read_text(encoding="utf-8").split("\n")
    lines[0] = lines[0].replace("DMSO_1_127N", "DMSO_1_126")
    p.write_text("\n".join(lines), encoding="utf-8")
    out = downstream.analyze(tmp_path / "e", "TMT", analysis_cfg=CFG)
    summary = check_outcome(out, tmp_path / "e/results")
    assert list(summary["samples"]).count("DMSO_1_126") == 1 and len(summary["samples"]) == 5
    assert "more than one column named DMSO_1_126" in said(out)


def test_a_sample_name_no_file_name_can_hold_still_gets_its_site_table(tmp_path):
    """Found by the fuzz: a NUL byte (or : / ?) in an isoDTB ratio column's sample name went straight into the
    name of <sample>_sites.tsv, and the read stage crashed."""
    p = tmp_path / "e/fragpipe/combined_modified_peptide_label_quant.tsv"
    simulate.isodtb_label_quant(p, {"EXP": [1, 2, 3]}, seed=2, n_sites=80)
    p.write_text(p.read_text(encoding="utf-8").replace("EXP_", "E:X\x00P?_"), encoding="utf-8")
    out = downstream.analyze(tmp_path / "e", "isoDTB", analysis_cfg=CFG)
    summary = check_outcome(out, tmp_path / "e/results")
    assert (tmp_path / "e/results/E_X_P__sites.tsv").is_file()
    assert summary["features"] > 50 and summary["comparisons"][0]["tested"] > 50


def test_fifty_thousand_features(tmp_path):
    runs = [(f"C:\\d\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=1, n_proteins=50_000)
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg=CFG)
    summary = check_outcome(out, tmp_path / "e/results")
    assert summary["features_loaded"] == 50_000 and summary["comparisons"][0]["tested"] > 45_000
    assert summary["comparisons"][0]["up"] > 1500


# --------------------------------------------------------------------- guards --


def _analyze_matrix(tmp_path, header, rows, **settings):
    out, summary = run_table(tmp_path, header, rows, **settings)
    return out, summary, {i.code: i for i in out.issues}


def test_identical_samples_are_reported(tmp_path):
    header, rows = _identical_samples(random.Random(7))
    out, summary, issues = _analyze_matrix(tmp_path, header, rows)
    assert issues["IDENTICAL_SAMPLES"].data["groups"] == [["DMSO_1", "DMSO_2", "DMSO_3"]]
    assert any(s["key"] == "statistics" and "DMSO_1 = DMSO_2" in s["text"] for s in summary["trust"]["statements"])


def test_zero_variance_is_reported_for_limma_and_for_welch(tmp_path):
    rng = random.Random(3)
    header, rows = base(rng, n=200)
    for r in rows[:40]:  # rounded to the same number in every replicate; 4-fold apart between the groups
        r[2:] = ["1000000"] * 3 + ["4000000"] * 3
    out, summary, issues = _analyze_matrix(tmp_path / "limma", header, rows, normalize="none")
    z = issues["ZERO_VARIANCE"]
    assert z.data["features"] == 40 and z.data["tested"] == 200 and "limma replaces their variance" in z.message
    out, summary, issues = _analyze_matrix(tmp_path / "welch", header, rows, normalize="none", test="welch",
                                           imputation="none")
    z = issues["ZERO_VARIANCE"]
    assert z.data["p_zero"] == 40 and z.data["hits"] == 40 and "divides by zero" in z.message


def test_features_tested_without_residual_df_are_counted(tmp_path):
    """1 control against 1 treated sample, with a third, replicated condition lending the variance: a feature
    missing in that third condition has no residual df, and its p-value is the prior's alone."""
    rng = random.Random(5)
    header, rows = base(rng, n=150, conds=(("DMSO", 1), ("Drug", 1), ("Other", 4)))
    for r in rows[:25]:
        r[4:] = [""] * 4
    out, summary, issues = _analyze_matrix(tmp_path, header, rows, imputation="none", filter_condition_pct=0,
                                           comparisons=["Drug vs DMSO"])
    g = issues["NO_RESIDUAL_DF"]
    assert g.data["features"] >= 20 and g.data["comparison"] == "Drug vs DMSO"
    assert "LOW_CONFIDENCE" in issues  # the group-of-one label is still there
    assert any("one value per group" in s["text"] for s in summary["trust"]["statements"])


def test_no_variance_prior_with_two_features_is_said(tmp_path):
    header, rows = base(random.Random(7), n=2)
    out, summary, issues = _analyze_matrix(tmp_path, header, rows, normalize="none")
    assert issues["VARIANCE_PRIOR"].data["what"] == "none" and "ordinary t-tests" in issues["VARIANCE_PRIOR"].message


def test_a_prior_that_does_not_converge_is_said():
    """Variances spread over many orders of magnitude, with unequal df: limma's ML fit of the prior df stops at
    the lower edge of its search range (2). The guard reports it; the numbers are left as limma gives them."""
    rng = random.Random(11)
    samples = [f"{c}_{r}" for c in ("A", "B") for r in (1, 2, 3, 4)]
    cond = {s: s[0] for s in samples}
    values = []
    for i in range(300):
        sd = math.exp(rng.gauss(0, 4))
        row = [20 + rng.gauss(0, sd) for _ in samples]
        if i % 3 == 0:
            row[rng.randrange(8)] = None
        values.append(row)
    feats = [quant.Feature(f"P{i}", f"G{i}") for i in range(300)]
    m = quant.QuantMatrix("intensity", "protein", feats, samples, values, cond, exp="TMT")
    from ionomos.downstream import analysis

    s = analysis.settings_from({"imputation": "none", "normalize": "none", "filter_condition_pct": 0})
    p, _ = fpa.process(m, imputation="none")
    res = analysis.run_contrasts(p, [("B", "A")], s)
    d = analysis.to_diff(p, res[0], "A", s)
    assert d.prior[0] <= guards.PRIOR_DF_FLOOR
    found = guards.statistics(p, [d], s)
    assert [g["what"] for g in found if g["code"] == "VARIANCE_PRIOR"] == ["floor"]
    assert "did not converge" in guards.issues(found)[0].message


def test_homogeneous_variances_are_not_an_issue(tmp_path):
    """The simulation's proteins share one SD, so limma's prior df is infinite (or at the top of the ML
    search). That is limma working as designed: stated under 'How far to trust this', never an issue."""
    runs = [(f"C:\\d\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=3, n_proteins=400)
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg={**CFG, "imputation": "none"})
    summary = check_outcome(out, tmp_path / "e/results")
    assert not {"VARIANCE_PRIOR", "ZERO_VARIANCE", "NO_RESIDUAL_DF", "IDENTICAL_SAMPLES"} & {i.code for i in out.issues}
    comp = next(s for s in summary["trust"]["statements"] if s["key"] == "comparison")
    assert "one pooled variance" in comp["text"]


def test_a_clean_experiment_gets_no_guard_issue_and_no_new_note(tmp_path):
    """The default output is unchanged where nothing is wrong: the same notes as before these checks."""
    runs = [(f"C:\\d\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3, 4)]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=9, n_proteins=500,
                           noise_spread=0.4)
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg=CFG)
    check_outcome(out, tmp_path / "e/results")
    assert out.warnings == [] and not [i for i in out.issues if i.severity != "warning"]
    assert not {"VARIANCE_PRIOR", "ZERO_VARIANCE", "NO_RESIDUAL_DF", "IDENTICAL_SAMPLES"} & {i.code for i in out.issues}
