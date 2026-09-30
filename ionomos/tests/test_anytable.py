"""Any protein / results table -> volcano plots (downstream/anytable.py): the formats labs actually export,
the ways such files go wrong, and the promise that analysing a table never touches files beside it."""
import json
import math
import random
import zipfile
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import anytable
from ionomos.downstream.tables import read_tsv

GENES = [f"G{i}" for i in range(300)]
HITS = set(GENES[:25])
SAMPLES = [(c, r) for c in ("DMSO", "Drug") for r in (1, 2, 3)]


def _intensity(rng, cond, gene):
    base = 20 + (int(gene[1:]) * 37 % 700) / 100
    return 2 ** (base + (2.0 if cond != "DMSO" and gene in HITS else 0.0) + rng.gauss(0, 0.25))


def _write(p: Path, header, rows, sep="\t", quote=False) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    cell = (lambda c: f'"{c}"') if quote else str
    p.write_text("\n".join(sep.join(cell(c) for c in r) for r in [header, *rows]) + "\n", encoding="utf-8")
    return p


def _xlsx(p: Path, header, rows) -> Path:
    """A genuine minimal .xlsx (shared strings + numbers), stored under a non-default sheet path."""
    strings, idx, xml_rows = [], {}, []
    for i, r in enumerate([header, *rows], 1):
        cells = []
        for j, v in enumerate(r):
            col = chr(65 + j) if j < 26 else "A" + chr(65 + j - 26)
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{col}{i}"><v>{v}</v></c>')
            elif v != "":
                if v not in idx:
                    idx[v] = len(strings)
                    strings.append(v)
                cells.append(f'<c r="{col}{i}" t="s"><v>{idx[v]}</v></c>')
        xml_rows.append(f'<row r="{i}">{"".join(cells)}</row>')
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{m}" xmlns:r="{r_ns}"><sheets>'
                                      '<sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Type="x" Target="worksheets/data.xml"/></Relationships>')
        z.writestr("xl/worksheets/data.xml", f'<worksheet xmlns="{m}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{m}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    return p


def _analyze(table: Path):
    from ionomos.postprocess import table_workspace

    ws = table_workspace(table)
    return ws, downstream.analyze(ws, analysis_cfg={"enrichment": False}, table=table)


def _hits_found(out) -> set[str]:
    comp = out.summary["comparisons"][0]
    rows = read_tsv(out.results_dir.parent / comp["table"])[1]
    return {r["label"] for r in rows if r["significant"] == "up"}


def _recovers_planted_hits(out) -> None:
    found = _hits_found(out)
    assert len(found & HITS) >= 0.5 * len(HITS) and len(found - HITS) <= 3, (sorted(found & HITS), sorted(found - HITS))


def _volcanos_ok(ws: Path, out) -> None:
    assert out.summary["comparisons"]
    for c in out.summary["comparisons"]:
        assert (ws / c["volcano"]).stat().st_size > 200


def test_maxquant_protein_groups(tmp_path):
    rng = random.Random(1)
    h = ["Protein IDs", "Protein names", "Gene names", "Peptides", "Intensity", *[f"Intensity {c}_{r}" for c, r in SAMPLES],
         *[f"LFQ intensity {c}_{r}" for c, r in SAMPLES], "Reverse", "Potential contaminant"]
    rows = []
    for k, g in enumerate(GENES):
        lfq = [0 if rng.random() < 0.05 else round(_intensity(rng, c, g)) for c, _ in SAMPLES]
        rows.append([f"P{k}", f"{g} protein", g, 4, sum(lfq), *lfq, *lfq, "+" if k == 299 else "", ""])
    ws, out = _analyze(_write(tmp_path / "txt" / "proteinGroups.txt", h, rows))
    # recognised as MaxQuant (engines.py, D36), read by the same any-table loader
    assert out.summary["method"] == "MaxQuant" and len(out.summary["samples"]) == 6
    assert out.summary["engine"]["engine"] == "MaxQuant"
    assert set(out.summary["samples"].values()) == {"DMSO", "Drug"}
    assert out.summary["features"] == 299  # the Reverse '+' row is left out
    assert any("LFQ intensity" in n for n in out.warnings) and any("log2-transformed" in n for n in out.warnings)
    _recovers_planted_hits(out)
    _volcanos_ok(ws, out)


def test_semicolon_csv_with_decimal_commas_and_quotes(tmp_path):
    rng = random.Random(2)
    h = ["Accession", "Gene", *[f"{c}_{r}" for c, r in SAMPLES]]
    rows = [[f"P{k}", g, *[f"{math.log2(_intensity(rng, c, g)):.3f}".replace(".", ",") for c, _ in SAMPLES]]
            for k, g in enumerate(GENES)]
    ws, out = _analyze(_write(tmp_path / "export.csv", h, rows, sep=";", quote=True))
    assert any("log2 already" in n for n in out.warnings)
    _recovers_planted_hits(out)


def test_excel_sheet(tmp_path):
    rng = random.Random(3)
    h = ["Notes", "Protein", "Gene", *[f"{c} {r}" for c, r in SAMPLES]]
    rows = [["", f"P{k}", g, *[round(_intensity(rng, c, g)) for c, _ in SAMPLES]] for k, g in enumerate(GENES)]
    ws, out = _analyze(_xlsx(tmp_path / "sheet.xlsx", h, rows))
    assert set(out.summary["samples"].values()) == {"DMSO", "Drug"}
    _recovers_planted_hits(out)
    _volcanos_ok(ws, out)


@pytest.mark.parametrize(("name", "header", "row", "sep"), [
    ("limma.csv", ["", "logFC", "AveExpr", "t", "P.Value", "adj.P.Val", "B"],
     lambda g, hit: [g, 2.5 if hit else 0.1, 20, 1, 1e-6 if hit else 0.5, 1e-4 if hit else 0.9, 0], ","),
    ("deseq.csv", ["gene", "baseMean", "FoldChange", "pvalue", "padj"],  # linear fold change
     lambda g, hit: [g, 100, 4.0 if hit else 1.05, 1e-6 if hit else 0.6, 1e-4 if hit else 0.9], ","),
    ("perseus.txt", ["Gene names", "Student's T-test Difference Drug_DMSO", "-Log Student's T-test p-value Drug_DMSO",
                     "Student's T-test q-value Drug_DMSO"],  # -log10 p, and a log2 difference that is all positive
     lambda g, hit: [g, 3 if hit else 0.1, 6 if hit else 0.3, 0.001 if hit else 0.9], "\t"),
])
def test_results_tables_are_plotted_as_given(tmp_path, name, header, row, sep):
    ws, out = _analyze(_write(tmp_path / name, header, [row(g, g in HITS) for g in GENES], sep=sep))
    comp = out.summary["comparisons"][0]
    assert comp["up"] == len(HITS) and comp["down"] == 0 and comp["tested"] == len(GENES)
    assert any("plotted as given" in n for n in out.warnings)
    _volcanos_ok(ws, out)


def test_fragpipe_analyst_export_has_one_volcano_per_comparison(tmp_path):
    h = ["Protein ID", "Gene Name", "DrugA_vs_DMSO_log2 fold change", "DrugA_vs_DMSO_p.val", "DrugA_vs_DMSO_p.adj",
         "DrugB_vs_DMSO_log2 fold change", "DrugB_vs_DMSO_p.val", "DrugB_vs_DMSO_p.adj"]
    rows = [[f"P{k}", g, 2.5 if g in HITS else 0.1, 1e-5 if g in HITS else 0.5, 1e-3 if g in HITS else 0.9,
             -1.5 if g in HITS else 0.0, 1e-4 if g in HITS else 0.7, 1e-2 if g in HITS else 0.95]
            for k, g in enumerate(GENES)]
    ws, out = _analyze(_write(tmp_path / "fpa.tsv", h, rows))
    by = {c["name"]: c for c in out.summary["comparisons"]}
    assert by["DrugA_vs_DMSO"]["up"] == len(HITS) and by["DrugB_vs_DMSO"]["down"] == len(HITS)
    _volcanos_ok(ws, out)


def test_fold_changes_alone_still_give_a_labelled_plot(tmp_path):
    ws, out = _analyze(_write(tmp_path / "fc.tsv", ["Gene", "log2FC"], [[g, 2 if g in HITS else 0.1] for g in GENES]))
    comp = out.summary["comparisons"][0]
    assert comp["confidence"] == "none" and comp["up"] == len(HITS)
    assert "FOLD CHANGE ONLY" in (ws / comp["volcano"]).read_text(encoding="utf-8")


def test_letter_replicates_get_a_suggested_grouping(tmp_path):
    rng = random.Random(4)
    h = ["Gene", "ctrl_a", "ctrl_b", "treated_a", "treated_b"]
    rows = [[g, *[f"{_intensity(rng, c, g):.1f}" for c in ("DMSO", "DMSO", "Drug", "Drug")]] for g in GENES]
    ws, out = _analyze(_write(tmp_path / "letters.tsv", h, rows))
    issue = {i.code: i for i in out.issues}["EACH_OWN_CONDITION"]
    assert issue.severity == "input" and set(issue.data["suggested"].values()) == {"ctrl", "treated"}
    # the person's answer gives real statistics
    out = downstream.analyze(ws, analysis_cfg={"enrichment": False}, table=tmp_path / "letters.tsv",
                             overrides={"sample_conditions": issue.data["suggested"]})
    comp = out.summary["comparisons"][0]
    assert comp["name"] == "treated vs ctrl" and comp["confidence"] == "normal" and comp["tested"] > 0


@pytest.mark.parametrize(("name", "content"), [
    ("notes.txt", b"this is not a table\njust words\n"),
    ("empty.csv", b""),
    ("broken.xlsx", b"PK\x03\x04 not a zip"),
    ("hdr.tsv", b"Gene\tDMSO_1\tDrug_1\n"),
])
def test_unusable_files_explain_themselves(tmp_path, name, content):
    (tmp_path / name).write_bytes(content)
    ws, out = _analyze(tmp_path / name)
    assert out.summary["state"] == "failed" and "UNUSABLE_TABLE" in {i.code for i in out.issues}
    assert out.report and out.report.is_file()
    assert not (out.results_dir / "analysis_error.txt").exists()


def test_folder_scan_finds_the_matrix_and_skips_long_tables(tmp_path):
    rng = random.Random(5)
    _write(tmp_path / "psm.tsv", ["Spectrum", "Intensity", "Hyperscore"], [[f"s{i}", i, 2] for i in range(900)])
    (tmp_path / "log_2026.txt").write_text("log", encoding="utf-8")
    _write(tmp_path / "combined" / "txt" / "proteinGroups.txt", ["Gene names", *[f"LFQ intensity {c}_{r}" for c, r in SAMPLES]],
           [[g, *[round(_intensity(rng, c, g)) for c, _ in SAMPLES]] for g in GENES])
    assert anytable.find_table(tmp_path).name == "proteinGroups.txt"
    out = downstream.analyze(tmp_path, analysis_cfg={"enrichment": False})
    assert out.summary["method"] == "MaxQuant" and len(out.summary["samples"]) == 6


def test_analysing_a_table_never_touches_files_beside_it(tmp_path):
    table = _write(tmp_path / "fc.tsv", ["Gene", "logFC", "P.Value"], [[g, 2 if g in HITS else 0, 0.5] for g in GENES])
    mine = tmp_path / "results" / "report.html"  # the user's own results folder next to the table
    mine.parent.mkdir()
    mine.write_text("my own report", encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    from ionomos.cli import main

    assert main(["analyze", str(table), "--quiet", "--no-enrichment"]) == 0
    assert all(p.read_bytes() == b for p, b in before.items())
    ws = tmp_path / "fc_ionomos"
    assert (ws / "results" / "report.html").is_file()
    assert json.loads((ws / "results" / "analysis.json").read_text(encoding="utf-8"))["method"] == "table"
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - set(before)
    assert all(ws in p.parents for p in new)
