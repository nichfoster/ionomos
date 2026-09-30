"""Other engines' outputs (downstream/engines.py): detection, loading, provenance, and a full analysis from
each. The fixtures are small files with the engines' real column names, built from one simulated
experiment with planted changes, so every engine should find the same hits."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import anytable, engines, simulate

CONDS = ("DMSO", "Drug")
RUNS = [f"{c}_{r}" for c in CONDS for r in (1, 2, 3)]


def _truth(tmp_path: Path, n: int = 300):
    """(proteins [(id, gene, desc, peptides)], {run: {id: linear intensity or None}}, planted truth)."""
    pg = tmp_path / "sim" / "report.pg_matrix.tsv"
    truth = simulate.dia_pg_matrix(pg, [(f"C:\\raw\\{r}.raw", r.split("_")[0]) for r in RUNS], seed=11, n_proteins=n)
    header, *lines = pg.read_text(encoding="utf-8").splitlines()
    head = header.split("\t")
    prots, vals = [], {r: {} for r in RUNS}
    for line in lines:
        v = line.split("\t")
        pid, gene, desc, npep = v[0], v[3], v[4], int(v[5])
        prots.append((pid, gene, desc, npep))
        for k, h in enumerate(head):
            if h.endswith(".raw"):
                run = h.rsplit("\\", 1)[-1][:-4]
                vals[run][pid] = float(v[k]) if v[k] else None
    return prots, vals, truth["Drug"]


def _write(path: Path, header: list[str], rows: list[list], sep: str = "\t") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sep.join(header) + "\n" + "\n".join(sep.join("" if x is None else str(x) for x in r) for r in rows)
                    + "\n", encoding="utf-8")
    return path


def _hits(out) -> set[str]:
    d = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert d["comparisons"] and d["comparisons"][0]["name"] == "Drug vs DMSO", d["comparisons"]
    import csv

    with open(out.results_dir / "Drug_vs_DMSO_differential.tsv", encoding="utf-8") as fh:
        return {r["label"] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}


def _recall(hits: set[str], truth: dict) -> float:
    return len(hits & set(truth)) / len(truth)


CFG = {"enrichment": False}


# ------------------------------------------------------------------- DIA-NN --


def test_diann_standalone_pg_matrix_is_read_as_dia_with_diann_provenance(tmp_path):
    prots, vals, truth = _truth(tmp_path)
    d = tmp_path / "exp"
    (d / "out").mkdir(parents=True)
    (tmp_path / "sim" / "report.pg_matrix.tsv").replace(d / "out" / "report.pg_matrix.tsv")
    (d / "out" / "report.log.txt").write_text("DIA-NN 1.9.2 (Data-Independent Acquisition by Neural Networks)\n"
                                              "Compiled on Oct 29 2024\n", encoding="utf-8")
    assert downstream.detect_method(d) == "DIA"
    out = downstream.analyze(d, analysis_cfg=CFG)
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["engine"]["engine"] == "DIA-NN" and s["engine"]["version"] == "1.9.2"
    assert _recall(_hits(out), truth) > 0.7
    html = out.report.read_text(encoding="utf-8")
    assert "Quantities from DIA-NN 1.9.2" in html and "Data source" in html


def _diann_long_rows(prots, vals, bad_q: bool = True):
    rows = []
    for pid, gene, desc, _npep in prots:
        for run in RUNS:
            v = vals[run][pid]
            if v is None:
                continue
            for k in range(2):  # two precursors per protein and run, the same protein quantity
                rows.append([f"D:\\data\\{run}.raw", pid, pid, gene, f"{gene}_HUMAN",
                             desc, f"PEPTIDE{k}K2", f"PEPTIDE{k}K", 0.001, 0.002, v])
        if bad_q:  # a precursor above 1% FDR with a crazy quantity: must be ignored
            rows.append([f"D:\\data\\{RUNS[0]}.raw", pid, pid, gene, "", desc, "BADK2", "BADK", 0.2, 0.3, 1e12])
    return rows


LONG_HEAD = ["Run", "Protein.Group", "Protein.Ids", "Genes", "Protein.Names", "First.Protein.Description",
             "Precursor.Id", "Stripped.Sequence", "Q.Value", "PG.Q.Value", "PG.MaxLFQ"]


def test_diann_long_report_is_summarised_with_the_fdr_filter(tmp_path):
    prots, vals, truth = _truth(tmp_path)
    rep = _write(tmp_path / "exp" / "report.tsv", LONG_HEAD, _diann_long_rows(prots, vals))
    found = engines.detect(tmp_path / "exp")
    assert (found.engine, found.method) == ("DIA-NN", "DIA-NN")
    m = engines.load_diann_long(rep)
    first = prots[0][0]
    i = [f.id for f in m.features].index(first)
    j = m.samples.index("DMSO_1")
    assert m.values[i][j] == pytest.approx(math.log2(vals["DMSO_1"][first]))  # the 1e12 row was filtered out
    assert m.features[i].peptides == 2 and m.exp == "DIA"
    assert set(m.conditions) == {"DMSO", "Drug"}
    out = downstream.analyze(tmp_path / "exp", analysis_cfg=CFG)
    assert out.method == "DIA-NN" and _recall(_hits(out), truth) > 0.7
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["engine"]["fdr"].startswith("precursor and protein-group q")


def test_diann_parquet_without_pyarrow_says_what_to_do(tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__

    def no_pyarrow(name, *a, **k):
        if name.startswith("pyarrow"):
            raise ImportError("no pyarrow here")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    (tmp_path / "exp").mkdir()
    (tmp_path / "exp" / "report.parquet").write_bytes(b"PAR1....PAR1")
    assert downstream.detect_method(tmp_path / "exp") == "DIA-NN"
    with pytest.raises(anytable.TableError, match="pyarrow"):
        engines.load_diann_long(tmp_path / "exp" / "report.parquet")
    out = downstream.analyze(tmp_path / "exp", analysis_cfg=CFG)
    assert any("pyarrow" in w for w in out.warnings)
    assert out.report.is_file()


def test_diann_parquet_with_pyarrow(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    prots, vals, truth = _truth(tmp_path, n=120)
    rows = _diann_long_rows(prots, vals)
    cols = {h: [r[k] for r in rows] for k, h in enumerate(LONG_HEAD)}
    pq.write_table(pa.table(cols), tmp_path / "report.parquet")
    m = engines.load_diann_long(tmp_path / "report.parquet")
    assert len(m.features) == 120 and m.samples[0] == "DMSO_1"


# ---------------------------------------------------------------- MaxQuant --


def _maxquant(tmp_path, prots, vals, tmt=False) -> Path:
    txt = tmp_path / "mq" / "combined" / "txt"
    if tmt:
        head = ["Protein IDs", "Majority protein IDs", "Gene names", "Protein names", "Peptides",
                "Razor + unique peptides", "Reverse", "Potential contaminant", "Only identified by site"] + \
            [f"Reporter intensity corrected {k} TMTexp" for k in range(1, 7)]
    else:
        head = ["Protein IDs", "Majority protein IDs", "Gene names", "Protein names", "Peptides",
                "Razor + unique peptides", "Reverse", "Potential contaminant", "Only identified by site",
                "Intensity", "iBAQ"] + [f"Intensity {r}" for r in RUNS] + [f"LFQ intensity {r}" for r in RUNS]
    rows = []
    for pid, gene, desc, npep in prots:
        lin = [vals[r][pid] or 0 for r in RUNS]
        base = [pid, pid, gene, desc, npep + 1, npep, "", "", ""]
        rows.append(base + (lin if tmt else [sum(lin), 1, *[x * 1.1 for x in lin], *lin]))
    rows.append(["REV__P99999", "REV__P99999", "", "", 1, 1, "+", "", ""] + [1e9] * (6 if tmt else 14))
    rows.append(["CON__P00761", "CON__P00761", "TRYP", "Trypsin", 9, 9, "", "+", ""] + [1e9] * (6 if tmt else 14))
    _write(txt / "proteinGroups.txt", head, rows)
    (txt / "parameters.txt").write_text("Parameter\tValue\nVersion\t2.4.9.0\nProtein FDR\t0.01\n", encoding="utf-8")
    return txt / "proteinGroups.txt"


def test_maxquant_proteingroups_lfq(tmp_path):
    prots, vals, truth = _truth(tmp_path)
    pg = _maxquant(tmp_path, prots, vals)
    assert downstream.detect_method(tmp_path / "mq") == "MaxQuant"
    m = engines.load_maxquant(pg)
    ids = {f.id for f in m.features}
    assert "REV__P99999" not in ids and "CON__P00761" not in ids  # flagged '+' rows left out
    assert m.meta["quantity"] == "LFQ intensity" and m.exp == "LFQ"
    assert m.samples == RUNS and m.features[0].peptides == prots[0][3]
    out = downstream.analyze(tmp_path / "mq", analysis_cfg=CFG)
    assert out.method == "MaxQuant" and _recall(_hits(out), truth) > 0.7
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["engine"] == {"engine": "MaxQuant", "version": "2.4.9.0", "files": ["combined/txt/parameters.txt"],
                           "quantity": "LFQ intensity", "fdr": "protein FDR 0.01",
                           "table": "combined/txt/proteinGroups.txt"}
    html = out.report.read_text(encoding="utf-8")
    assert "Quantities from MaxQuant 2.4.9.0 (combined/txt/proteinGroups.txt) were read by Ionomos" in html


def test_maxquant_file_given_directly_and_tmt_reporters(tmp_path):
    prots, vals, _ = _truth(tmp_path, n=80)
    pg = _maxquant(tmp_path, prots, vals, tmt=True)
    out = downstream.analyze(tmp_path / "ws", analysis_cfg=CFG, table=pg)
    assert out.method == "MaxQuant"
    m = engines.load_maxquant(pg)
    assert m.exp == "TMT" and m.meta["quantity"] == "Reporter intensity corrected"


# ------------------------------------------------------------- Spectronaut --


def test_spectronaut_long_report_uses_condition_and_replicate(tmp_path):
    prots, vals, truth = _truth(tmp_path)
    head = ["R.Condition", "R.FileName", "R.Replicate", "PG.ProteinGroups", "PG.Genes", "PG.ProteinDescriptions",
            "PG.Quantity", "PG.Qvalue", "EG.PrecursorId", "EG.Qvalue"]
    rows = []
    for pid, gene, desc, _n in prots:
        for run in RUNS:
            v = vals[run][pid]
            if v is not None:
                c, r = run.split("_")
                rows.append([c, f"20260930_{run}_SN", r, pid, gene, desc, v, 0.001, "_PEPK_.2", 0.001])
    _write(tmp_path / "sn" / "Report_BGS.tsv", head, rows)
    found = engines.detect(tmp_path / "sn")
    assert found.method == "Spectronaut"
    m = engines.load_spectronaut(found.path)
    assert sorted(m.samples) == sorted(RUNS) and m.condition["Drug_2"] == "Drug" and m.replicate["Drug_2"] == 2
    out = downstream.analyze(tmp_path / "sn", analysis_cfg=CFG)
    assert _recall(_hits(out), truth) > 0.7


def test_spectronaut_pivot_report(tmp_path):
    prots, vals, _ = _truth(tmp_path, n=60)
    head = ["PG.ProteinGroups", "PG.Genes"] + [f"[{k}] {r}.PG.Quantity" for k, r in enumerate(RUNS, 1)]
    _write(tmp_path / "piv" / "pivot.tsv", head, [[p, g, *[vals[r][p] for r in RUNS]] for p, g, _d, _n in prots])
    found = engines.detect(tmp_path / "piv")
    assert found.method == "Spectronaut"
    m = engines.load_spectronaut(found.path)
    assert m.samples == RUNS and m.meta["engine"] == "Spectronaut"


# ---------------------------------------------------------------- AlphaDIA --


def test_alphadia_pg_matrix_and_version(tmp_path):
    prots, vals, truth = _truth(tmp_path)
    _write(tmp_path / "ad" / "pg.matrix.tsv", ["pg", *RUNS], [[p, *[vals[r][p] for r in RUNS]] for p, *_ in prots])
    (tmp_path / "ad" / "frozen_config.yaml").write_text("version: 1.10.2\nfdr:\n  fdr: 0.01\n", encoding="utf-8")
    assert downstream.detect_method(tmp_path / "ad") == "AlphaDIA"
    out = downstream.analyze(tmp_path / "ad", analysis_cfg=CFG)
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["engine"]["engine"] == "AlphaDIA" and s["engine"]["version"] == "1.10.2"
    assert s["comparisons"][0]["up"] + s["comparisons"][0]["down"] > 0


# ------------------------------------------------------------ MSstats format --


def test_median_polish_matches_r():
    # R: medpolish(matrix(c(1,3,2,4), 2)) -> overall 2.5, col effects -0.5 / 0.5
    assert engines.median_polish([[1.0, 2.0], [3.0, 4.0]]) == pytest.approx([2.0, 3.0])
    # a missing value doesn't shift the other runs; a run with no values stays missing
    got = engines.median_polish([[10.0, 11.0, None], [12.0, None, None], [14.0, 15.0, None]])
    assert got[2] is None and got[1] - got[0] == pytest.approx(1.0)
    assert engines.median_polish([]) == []


def test_msstats_long_format(tmp_path):
    prots, vals, truth = _truth(tmp_path, n=200)
    head = ["ProteinName", "PeptideSequence", "PrecursorCharge", "FragmentIon", "ProductCharge", "IsotopeLabelType",
            "Condition", "BioReplicate", "Run", "Intensity"]
    rows = []
    for pid, _g, _d, _n in prots:
        for run in RUNS:
            v = vals[run][pid]
            c, r = run.split("_")
            for k, share in enumerate((0.5, 0.3, 0.2)):  # three features, each a fixed share of the protein
                rows.append([pid, f"PEP{k}K", 2, "NA", "NA", "L", c, r, f"{run}.mzML", v * share if v else "NA"])
            rows.append([pid, "PEP0K", 2, "NA", "NA", "H", c, r, f"{run}.mzML", 1e9])  # heavy reference: ignored
    f = _write(tmp_path / "q" / "out_msstats.csv", head, rows, sep=",")
    assert engines.detect(tmp_path / "q").method == "MSstats"
    m = engines.load_msstats(f)
    i = [x.id for x in m.features].index(prots[0][0])
    j = m.samples.index("DMSO_1")
    v = vals["DMSO_1"][prots[0][0]]
    if v:  # median polish of log2(v*share) over a constant-share design recovers log2(v) + a constant
        k = m.samples.index("DMSO_2")
        v2 = vals["DMSO_2"][prots[0][0]]
        if v2:
            assert m.values[i][j] - m.values[i][k] == pytest.approx(math.log2(v) - math.log2(v2), abs=1e-9)
    assert any("heavy" in n for n in m.notes)
    out = downstream.analyze(tmp_path / "q", analysis_cfg=CFG)
    by_gene = {g: pid for pid, g, _d, _n in prots}  # MSstats input has protein names only
    assert _recall(_hits(out), {by_gene[g]: v for g, v in truth.items()}) > 0.7


def test_msstats_tmt_is_refused_clearly(tmp_path):
    _write(tmp_path / "t.csv", ["ProteinName", "PeptideSequence", "Charge", "PSM", "Mixture", "TechRepMixture", "Run",
                                "Channel", "Condition", "BioReplicate", "Intensity"], [["P1"] + ["x"] * 10], sep=",")
    with pytest.raises(anytable.TableError, match="MSstatsTMT"):
        engines.load_msstats(tmp_path / "t.csv")


# ------------------------------------------------------- Proteome Discoverer --


def test_proteome_discoverer_export_names_conditions(tmp_path):
    prots, vals, truth = _truth(tmp_path)
    head = ["Checked", "Master", "Accession", "Description", "# Unique Peptides"] + \
        [f"Abundances (Normalized): F{k}: Sample, {r.split('_')[0]}" for k, r in enumerate(RUNS, 1)]
    _write(tmp_path / "pd" / "Proteins.txt", head,
           [["FALSE", "IsMasterProtein", p, d, n, *[vals[r][p] for r in RUNS]] for p, _g, d, n in prots])
    assert downstream.detect_method(tmp_path / "pd") == "PD"
    m = engines.load_pd(tmp_path / "pd" / "Proteins.txt")
    assert m.samples == ["DMSO_1", "DMSO_2", "DMSO_3", "Drug_1", "Drug_2", "Drug_3"]
    assert m.condition["Drug_3"] == "Drug" and m.meta["quantity"] == "Abundances (Normalized)"
    out = downstream.analyze(tmp_path / "pd", analysis_cfg=CFG)
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["engine"]["engine"] == "Proteome Discoverer" and "version" not in s["engine"]


# ----------------------------------------------------------- FragPipe / misc --


def test_fragpipe_provenance_reads_versions_tools_and_fasta(tmp_path):
    runs = [(f"C:\\x\\{r}.raw", r.split("_")[0]) for r in RUNS]
    fp = tmp_path / "e" / "fragpipe"
    simulate.dia_pg_matrix(fp / "diann-output" / "report.pg_matrix.tsv", runs, seed=3, n_proteins=80)
    (fp / "log_2026-09-30_10-00-00.txt").write_text(
        "FragPipe version 24.0\nMSFragger version MSFragger-4.3\nIonQuant version IonQuant-1.11.11\n"
        "DIA-NN 1.8.2 beta 8\n", encoding="utf-8")
    (fp / "fragpipe.workflow").write_text("# FragPipe (24.0) runtime properties\n"
                                          "database.db-path=C\\:\\\\Fragpipe_Auto\\\\fasta\\\\human.fas\n",
                                          encoding="utf-8")
    prov = engines.provenance(fp, "DIA")
    assert prov["engine"] == "FragPipe" and prov["version"] == "24.0"
    assert prov["tools"] == {"MSFragger": "4.3", "IonQuant": "1.11.11", "DIA-NN": "1.8.2"}
    assert prov["fasta"] == "C:\\Fragpipe_Auto\\fasta\\human.fas" and prov["files"] == ["fragpipe.workflow"]
    out = downstream.analyze(tmp_path / "e", "DIA", CFG, context={"fragpipe": "workflow DIA.workflow"})
    html = out.report.read_text(encoding="utf-8")
    assert "Raw files were searched with FragPipe 24.0 (workflow DIA.workflow; MSFragger 4.3" in html


def test_detection_prefers_fragpipe_tables_and_ignores_results_folder(tmp_path):
    prots, vals, _ = _truth(tmp_path, n=30)
    d = tmp_path / "mixed"
    (d / "fragpipe").mkdir(parents=True)
    (tmp_path / "sim" / "report.pg_matrix.tsv").replace(d / "fragpipe" / "report.pg_matrix.tsv")
    _maxquant(d / "results", prots, vals)  # an old copy inside results/ must not be picked
    assert downstream.detect_method(d) == "DIA"
    assert all("results" not in x.path.parts for x in engines.detect_all(d))


def test_unknown_folder_and_empty_provenance(tmp_path):
    assert engines.detect(tmp_path) is None
    assert engines.provenance(tmp_path, None) == {}


def test_every_engine_finds_the_same_hits_in_the_same_data(tmp_path):
    """One experiment written as DIA-NN, MaxQuant and Spectronaut output: the analysis must not depend on
    which engine's file format carried the numbers."""
    prots, vals, _ = _truth(tmp_path)
    _write(tmp_path / "a" / "report.tsv", LONG_HEAD, _diann_long_rows(prots, vals))
    _maxquant(tmp_path / "b", prots, vals)
    head = ["R.Condition", "R.FileName", "R.Replicate", "PG.ProteinGroups", "PG.Genes", "PG.Quantity"]
    _write(tmp_path / "c" / "Report.tsv", head, [[r.split("_")[0], r, r.split("_")[1], p, g, vals[r][p]]
                                                  for p, g, _d, _n in prots for r in RUNS if vals[r][p]])
    hits = [_hits(downstream.analyze(tmp_path / x, analysis_cfg=CFG)) for x in ("a", "b", "c")]
    assert hits[0] and hits[0] == hits[1] == hits[2]

