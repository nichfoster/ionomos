"""Contaminants from the search's FASTA (D87).

DIA-NN writes bare accessions in Protein.Group (P02769, not contam_sp|P02769|ALBU_BOVIN), so FragPipe-Analyst's
"contam" rule removes nothing from a FragPipe DIA search (docs/REAL_RUNS.md, 2026-10-06). The accessions of the
FASTA's contam_ entries are dropped as well; a group goes when any of its proteins is one.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import fpa, simulate
from ionomos.downstream.quant import Feature, QuantMatrix

# the lab's FASTA is 2026-01-30-decoys-contam-UP000005640_9606.fasta.fas: targets, rev_ decoys, contam_ entries
FASTA = """\
>sp|P02768|ALBU_HUMAN Albumin OS=Homo sapiens OX=9606 GN=ALB PE=1 SV=2
MKWVTFISLLFLFSSAYS
>sp|P60709|ACTB_HUMAN Actin, cytoplasmic 1 OS=Homo sapiens OX=9606 GN=ACTB PE=1 SV=1
MDDDIAALVVDNGSGMCK
>contam_sp|P02769|ALBU_BOVIN Albumin OS=Bos taurus OX=9913 GN=ALB PE=1 SV=4
MKWVTFISLLLLFSSAYS
>contam_sp|P00761|TRYP_PIG Trypsin OS=Sus scrofa OX=9823 PE=1 SV=1
FPTDDDDKIVGGYTCAAN
>contam_Q99999 a contaminant without a UniProt header
MKTAYIAK
>rev_sp|P02768|ALBU_HUMAN
SYASSFLFLLSIFTVWKM
>rev_contam_sp|P11111|NOT_A_CONTAMINANT
SYASSFLLLFSIFTVWKM
"""
FASTA_NAME = "2026-01-30-decoys-contam-test.fasta.fas"
CONTAMS = frozenset({"P02769", "P00761", "Q99999"})


def _fasta(folder: Path) -> Path:
    p = folder / "fasta" / FASTA_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FASTA, encoding="utf-8")
    return p


def test_fasta_contaminants_are_the_contam_entries_only(tmp_path):
    assert fpa.fasta_contaminants(_fasta(tmp_path)) == CONTAMS  # not targets, not decoys (rev_contam_ too)


def test_fasta_contaminants_rereads_a_changed_file(tmp_path):
    p = _fasta(tmp_path)
    assert fpa.fasta_contaminants(p) == CONTAMS
    p.write_text(FASTA + ">contam_sp|P04264|K2C1_HUMAN Keratin\nMSRQ\n", encoding="utf-8")
    assert fpa.fasta_contaminants(p) == CONTAMS | {"P04264"}


@pytest.mark.parametrize("protein, label, accessions, removed", [
    # DIA-NN's Protein.Group: bare accessions (the 2026-10-06 run)
    ("P02769", "ALB", CONTAMS, True),                     # BSA, labelled with the human gene name
    ("P00761", "", CONTAMS, True),                        # porcine trypsin
    ("P02768", "ALB", CONTAMS, False),                    # human albumin stays
    ("P02768;P02769", "ALB", CONTAMS, True),              # any contaminant in the group: removed (D87)
    ("P02769;P02768", "ALB", CONTAMS, True),
    ("P60709", "ACTB", CONTAMS, False),
    ("P02769-2", "ALB", CONTAMS, False),                  # an isoform is not the FASTA's entry
    ("Q99999", "", CONTAMS, True),
    # without the FASTA: FragPipe-Analyst's rule, unchanged
    ("P02769", "ALB", frozenset(), False),
    ("contam_sp|P00761|TRYP_PIG", "", frozenset(), True),  # FragPipe's own tables keep the prefix
    ("sp|P02768|ALBU_HUMAN", "ALB", frozenset(), False),
    ("CON__P02769", "", frozenset(), True),              # MaxQuant
    ("P1", "contam_TRYP", frozenset(), True),
    # FragPipe's own tables, with the FASTA too
    ("sp|P02769|ALBU_BOVIN", "ALB", CONTAMS, True),
    ("sp|P02768|ALBU_HUMAN", "ALB", CONTAMS, False),
])
def test_contaminant_rule(protein, label, accessions, removed):
    assert fpa.is_contaminant(Feature(protein, label), accessions) is removed
    m = QuantMatrix("intensity", "protein", [Feature("P60709", "ACTB"), Feature(protein, label)], ["x_1"],
                    [[1.0], [2.0]], {"x_1": "x"})
    out, n = fpa.remove_contaminants(m, accessions)
    assert n == int(removed) and len(out.features) == 2 - int(removed)


def test_process_reports_the_rule_and_mixed_groups():
    m = QuantMatrix("intensity", "protein",
                    [Feature("P60709", "ACTB"), Feature("P02769", "ALB"), Feature("P02768;P02769", "ALB")],
                    ["x_1", "x_2"], [[20.0, 21.0], [25.0, 25.5], [24.0, 24.2]], {"x_1": "x", "x_2": "x"})
    p, notes = fpa.process(m, imputation="none", contaminant_fasta=(FASTA_NAME, CONTAMS))
    step = next(s for s in p.steps if s["step"] == "contaminants removed")
    assert step["removed"] == 2 and step["features"] == 1
    assert step["rule"] == f"the 3 contam_ entries of {FASTA_NAME}, or names with contam_ or CON__"
    assert any("1 protein group(s) with a contaminant and another protein" in n and "P02768;P02769" in n
               for n in notes)
    # without the FASTA nothing has the prefix: no step, as before
    p, _ = fpa.process(m, imputation="none")
    assert not any(s["step"] == "contaminants removed" for s in p.steps)


# ------------------------------------------------------------------ end to end --

GROUPS = {0: ("P02769", "ALB"), 1: ("P00761", ""), 2: ("P02768;P02769", "ALB"), 3: ("P02768", "ALB")}
RUNS = [(f"C:\\Fragpipe_Auto_Users\\Sheena\\exp\\{c}_{r}_uncalibrated.mzML", c)
        for c in ("Vehicle", "Compound") for r in (1, 2, 3)]


def _dest(tmp_path: Path, db_path: str | None) -> Path:
    """A FragPipe 24 DIA output folder: dia-quant-output/report.pg_matrix.tsv, and fragpipe.workflow when db_path."""
    dest = tmp_path / "20261006_SH_DIA_pulldown"
    pg = dest / "fragpipe" / "dia-quant-output" / "report.pg_matrix.tsv"
    simulate.dia_pg_matrix(pg, RUNS, seed=5, n_proteins=40, missing=0)
    with open(pg, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    for i, (group, gene) in GROUPS.items():  # Protein.Group, Protein.Ids, Protein.Names, Genes
        rows[1 + i][0:4] = [group, group, "", gene]
    with open(pg, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, delimiter="\t", lineterminator="\n").writerows(rows)
    if db_path is not None:
        esc = db_path.replace("\\", "\\\\").replace(":", "\\:")  # Java properties, as FragPipe writes it
        (dest / "fragpipe" / "fragpipe.workflow").write_text(
            f"# FragPipe (24.0)\ndatabase.db-path={esc}\ndatabase.decoy-tag=rev_\n", encoding="utf-8")
    return dest


MISSING = "C:\\Fragpipe_Auto\\fasta\\2026-01-30-decoys-contam-UP000005640_9606.fasta.fas"


@pytest.mark.parametrize("where, removed, note", [
    ("workflow", 3, None),            # a FragPipe GUI result: the workflow FragPipe left in its folder
    ("record", 3, None),              # a job Ionomos ran: ionomos.json run.fasta
    ("workflow-missing+record", 3, None),
    ("workflow-missing", 0, "2026-01-30-decoys-contam-UP000005640_9606.fasta.fas was not found"),
    ("nothing", 0, None),
    ("workflow-but-off", 0, None),
])
def test_analyze_removes_the_fastas_contaminants(tmp_path, where, removed, note):
    fasta = _fasta(tmp_path)
    db = {"workflow": str(fasta), "workflow-but-off": str(fasta)}.get(where, MISSING if "missing" in where else None)
    dest = _dest(tmp_path, db)
    record = {"run": {"fasta": str(fasta)}} if "record" in where else None
    cfg = {"enrichment": False, "remove_contaminants": where != "workflow-but-off"}
    downstream.analyze(dest, "DIA", cfg, record=record)
    summary = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    steps = [s for s in summary["processing"] if s["step"] == "contaminants removed"]
    assert [s["removed"] for s in steps] == ([removed] if removed else [])
    if removed:
        assert FASTA_NAME in steps[0]["rule"]
        assert any("P02768;P02769" in n for n in summary["notes"])
    matrix = (dest / "results" / "protein_matrix_log2.tsv").read_text(encoding="utf-8")
    assert "P02769\t" in matrix  # the loaded matrix is written before processing, with every row
    tested = (dest / "results" / "Compound_vs_Vehicle_differential.tsv")
    ids = {r.split("\t", 1)[0] for r in tested.read_text(encoding="utf-8").splitlines()[1:]}
    assert ("P02768" in ids) and (("P02769" in ids) is (removed == 0))
    assert any(note in n for n in summary["notes"]) if note else not any("was not found" in n for n in summary["notes"])
