"""An SDRF in the experiment folder as the design (downstream/design.py, D47).

The SDRFs here use the spec's real layout, copied from the quantms test datasets (bigbio/quantms-test-datasets:
testdata/lfq_ci/PXD026600, testdata-aws/tmt_full/PXD005486): mixed-case headers ("Source Name",
"Characteristics[...]", "Factor Value[...]"), repeated comment[modification parameters] columns,
"AC=MS:1002038;NT=label free sample" labels, TMT labels TMT126 ... TMT131 and fraction identifiers."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import design, engines, plex, simulate

CFG = {"enrichment": False}
LFQ_HEAD = ["Source Name", "Characteristics[organism]", "Characteristics[organism part]", "Characteristics[disease]",
            "Characteristics[biological replicate]", "Material Type", "assay name", "technology type",
            "comment[data file]", "comment[file uri]", "comment[technical replicate]", "comment[fraction identifier]",
            "comment[proteomics data acquisition method]", "comment[label]", "comment[instrument]",
            "comment[modification parameters]", "comment[modification parameters]", "comment[cleavage agent details]",
            "Factor Value[compound]", "Factor Value[dose]"]
LABEL_FREE = "AC=MS:1002038;NT=label free sample"
RUNS = [f"20260930_EXP_{k:02d}" for k in range(1, 7)]  # names that don't say the condition
TRUE = {r: ("DMSO" if k < 3 else "Drug") for k, r in enumerate(RUNS)}


def _write(path: Path, head: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\t".join(head) + "\n" + "\n".join("\t".join(str(x) for x in r) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def _lfq_rows(runs=RUNS, cond=TRUE, dose=None) -> list[list]:
    rows = []
    for k, run in enumerate(runs):
        c = cond[run]
        rep = sum(1 for r in runs[: k + 1] if cond[r] == c)
        rows.append([f"Sample {k + 1}", "Homo sapiens", "not available", "not available", rep, "cell",
                     f"run {k + 1}", "proteomic profiling by mass spectrometry", f"{run}.raw",
                     f"ftp://example.org/{run}.raw", 1, 1, "NT=Data-Independent Acquisition;AC=NCIT:C161786",
                     LABEL_FREE, "NT=Orbitrap Eclipse;AC=MS:1003029", "NT=Carbamidomethyl;TA=C;MT=fixed;AC=UNIMOD:4",
                     "NT=Oxidation;MT=Variable;TA=M;AC=Unimod:35", "AC=MS:1001251;NT=Trypsin", c,
                     (dose or {}).get(run, "not applicable")])
    return rows


def _dia(dest: Path, seed: int = 4):
    truth = simulate.dia_pg_matrix(dest / "fragpipe" / "diann-output" / "report.pg_matrix.tsv",
                                   [(f"C:\\Fragpipe_General\\X\\exp\\raw\\{r}.raw", TRUE[r]) for r in RUNS],
                                   seed=seed, n_proteins=200)
    return truth["Drug"]


def _json(out) -> dict:
    return json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))


def _hits(out) -> set[str]:
    with open(out.results_dir / "Drug_vs_DMSO_differential.tsv", encoding="utf-8") as fh:
        return {r["label"] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}


# ------------------------------------------------------------------- reading --


def test_read_real_layout_and_factors(tmp_path):
    dose = {r: ("0 uM" if TRUE[r] == "DMSO" else "1 uM") for r in RUNS}
    p = _write(tmp_path / "PXD999999.sdrf.tsv", LFQ_HEAD, _lfq_rows(dose=dose))
    d = design.read(p)
    assert d.factors == ["compound", "dose"] and not d.labelled
    r = d.rows[3]
    assert (r.stem, r.condition, r.biorep, r.channel, r.techrep, r.fraction) == (RUNS[3], "Drug | 1 uM", 1, "", 1, 1)
    assert design.read(p, factor="Factor Value[compound]").rows[3].condition == "Drug"
    d2 = design.read(p, factor=["cell line"])  # not a factor column: say so, use them all
    assert d2.rows[3].condition == "Drug | 1 uM" and "not a factor value column" in d2.notes[0]
    with pytest.raises(design.DesignError, match="not an SDRF"):
        design.read(_write(tmp_path / "x.sdrf.tsv", ["a", "b"], [[1, 2]]))


def test_tmt_sdrf_plexes_labels_and_pools(tmp_path):
    """Two plexes x two fractions; TMT131 of a 10-plex; the pool marked as the spec says (biological replicate
    'pooled')."""
    head = ["Source Name", "Characteristics[organism]", "Characteristics[biological replicate]", "assay name",
            "comment[data file]", "comment[technical replicate]", "comment[fraction identifier]", "comment[label]",
            "Factor Value[treatment]"]
    rows = []
    for x in (1, 2):
        for f in (1, 2):
            for k, ch in enumerate(plex.TMT_ORDERS[10]):
                pooled = ch in ("126", "131")
                cond = "pool" if pooled else ("control" if k < 5 else "treatment")
                rows.append([f"plex{x} pool" if pooled else f"S{x}_{k}", "Homo sapiens", "pooled" if pooled else k,
                             f"run {x}{f}", f"P{x}_Fr{f}.raw", 1, f, f"TMT{ch}", cond])
    d = design.read(_write(tmp_path / "t.sdrf.tsv", head, rows))
    assert d.labelled and d.plex_of == {"P1_Fr1": "plex1", "P1_Fr2": "plex1", "P2_Fr1": "plex2", "P2_Fr2": "plex2"}
    assert d.rows[9].channel == "131" and d.rows[0].pooled and not d.rows[1].pooled
    assert d.rows[3].fraction == 1 and d.rows[13].fraction == 2


def test_find_skips_results_and_ionomos_folders(tmp_path):
    dest = tmp_path / "exp"
    _write(dest / "results" / "sdrf.tsv", LFQ_HEAD, _lfq_rows())  # Ionomos' own output
    _write(dest / "old_ionomos" / "a.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    _write(dest / "fragpipe_previous_2026" / "b.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    assert design.find(dest, dest / "fragpipe")[0] is None
    _write(dest / "fragpipe" / "deep.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    top = _write(dest / "design.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    path, notes = design.find(dest, dest / "fragpipe")
    assert path == top and "several SDRF files" in notes[0]
    table = _write(tmp_path / "tables" / "matrix.tsv", ["id", "a"], [["p", 1]])
    beside = _write(tmp_path / "tables" / "m.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    assert design.find(tmp_path / "tables" / "matrix_ionomos", None, table)[0] == beside


# ------------------------------------------------------------------ analysis --


def test_sdrf_sets_the_design_of_a_dia_experiment(tmp_path):
    dest = tmp_path / "exp"
    truth = _dia(dest)
    _write(dest / "PXD999999.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG)
    s = _json(out)
    assert s["samples"] == {r: TRUE[r] for r in RUNS}  # names kept; conditions from the SDRF
    assert s["design"]["conditions_from"] == "SDRF PXD999999.sdrf.tsv"
    assert s["design"]["sdrf"]["matched"] == 6 and s["design"]["sdrf"]["factors"] == ["compound", "dose"]
    assert any("from the SDRF PXD999999.sdrf.tsv for 6 of 6 samples" in n for n in s["notes"])
    hits = _hits(out)
    assert len(hits & set(truth)) >= 0.7 * len(truth)
    assert "taken from the SDRF-Proteomics file PXD999999.sdrf.tsv" in out.report.read_text(encoding="utf-8")
    # the SDRF Ionomos wrote is output: it is never read back as a design
    assert (out.results_dir / "sdrf.tsv").is_file()
    (dest / "PXD999999.sdrf.tsv").unlink()
    s2 = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    assert "sdrf" not in s2["design"] and s2["design"]["conditions_from"] == "sample names"


def test_the_analysis_tab_sees_the_sdrf_conditions(tmp_path):
    from ionomos import postprocess

    dest = tmp_path / "exp"
    _dia(dest)
    _write(dest / "d.sdrf.tsv", LFQ_HEAD, _lfq_rows(dose={r: "1 uM" for r in RUNS}))
    (dest / "experiment.yaml").write_text("analysis:\n  sdrf_factor: [compound]\n", encoding="utf-8")
    info = postprocess.inspect_folder(dest, None, "DIA")
    assert {x["sample"]: x["condition"] for x in info["samples"]} == TRUE


def test_precedence_sample_conditions_then_sdrf_then_manifest(tmp_path):
    dest = tmp_path / "exp"
    _dia(dest)
    manifest = [{"file": f"raw/{r}.raw", "experiment": "wrong", "bioreplicate": k + 1} for k, r in enumerate(RUNS)]
    record = {"plan": {"manifest": manifest}}
    _write(dest / "d.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG, record=record)
    s = _json(out)
    assert set(s["samples"].values()) == {"DMSO", "Drug"}  # the SDRF beats the manifest
    sample = next(x for x in s["samples"] if s["samples"][x] == "Drug")
    out2 = downstream.analyze(dest, "DIA", analysis_cfg=CFG, record=record,
                              overrides={"sample_conditions": {sample: "DMSO"}})
    s2 = _json(out2)
    assert s2["samples"][sample] == "DMSO"  # sample_conditions beat the SDRF
    assert s2["design"]["overridden_by_sample_conditions"] == [sample]


def test_unmatched_runs_and_an_sdrf_for_something_else(tmp_path):
    dest = tmp_path / "exp"
    _dia(dest)
    _write(dest / "d.sdrf.tsv", LFQ_HEAD, _lfq_rows(RUNS[:5], TRUE))
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    issue = next(i for i in s["issues"] if i["code"] == "SDRF_UNMATCHED_RUNS")
    assert issue["severity"] == "input" and issue["data"]["runs"] == [RUNS[5]]
    other = [f"OTHER_{k}" for k in range(6)]
    _write(dest / "d.sdrf.tsv", LFQ_HEAD, _lfq_rows(other, {r: "A" for r in other}))
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    issue = next(i for i in s["issues"] if i["code"] == "SDRF_UNMATCHED_RUNS")
    assert "names none of the 6 runs" in issue["message"] and s["design"]["sdrf"]["used"] is False


def test_fragpipe_calibrated_names_match_the_sdrf(tmp_path):
    """DIA-NN inside FragPipe reports the converted <run>_uncalibrated.mzML; the SDRF names <run>.raw."""
    dest = tmp_path / "exp"
    simulate.dia_pg_matrix(dest / "fragpipe" / "diann-output" / "report.pg_matrix.tsv",
                           [(f"/data/{r}_uncalibrated.mzML", TRUE[r]) for r in RUNS], seed=4, n_proteins=60)
    _write(dest / "d.sdrf.tsv", LFQ_HEAD, _lfq_rows())
    m, _f, notes = downstream.load_quantities("DIA", dest / "fragpipe", dest / "results", None)
    assert {m.condition[s] for s in m.samples} == {"DMSO", "Drug"} and not m.meta["sdrf"]["unmatched"]


def test_sdrf_with_maxquant_plexes_gives_conditions_and_the_irs_reference(tmp_path):
    """MaxQuant TMT across three experiments, its summary.txt naming each experiment's raw files, and an SDRF
    marking channels 126 / 131 pooled: conditions from the SDRF, IRS on the pools, the pools left out."""
    from tests.test_plexes import POOL, TMT10, _cond, _maxquant, _sim

    prots, sim, hits = _sim(n=150, hits=15)
    mq = tmp_path / "mq"
    _maxquant(mq, prots, sim)
    head = ["Source Name", "Characteristics[organism]", "Characteristics[biological replicate]", "assay name",
            "technology type", "comment[data file]", "comment[technical replicate]", "comment[fraction identifier]",
            "comment[label]", "Factor Value[compound]"]
    rows = []
    for e in sim:
        for f in (1, 2):
            for ch in TMT10:
                pooled = ch in POOL
                rows.append([f"{e} pool {ch}" if pooled else f"{e}_{ch}", "Homo sapiens", "pooled" if pooled else 1,
                             f"{e} F{f}", "proteomic profiling by mass spectrometry", f"{e}_F{f}.raw", 1, f,
                             f"TMT{ch}", "not applicable" if pooled else _cond(ch)])
    _write(mq / "design.sdrf.tsv", head, rows)
    out = downstream.analyze(mq, analysis_cfg=CFG)
    s = _json(out)
    assert s["method"] == "MaxQuant" and s["design"]["sdrf"]["matched"] == 30
    assert s["tmt"]["applied"] and s["tmt"]["reference_from"].startswith("SDRF design.sdrf.tsv")
    assert set(s["samples"].values()) == {"DMSO", "Drug"} and len(s["samples"]) == 24
    with open(out.results_dir / "Drug_vs_DMSO_differential.tsv", encoding="utf-8") as fh:
        sig = {r["id"] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}
    assert len(sig & hits) >= 0.8 * len(hits)


def test_sdrf_beats_the_engines_own_conditions(tmp_path):
    """MSstatsTMT's Condition column says A / B; the SDRF says what the channels are."""
    from tests.test_plexes import TMT10, _sim, _write_msstats_tmt

    prots, sim, _ = _sim(n=40, plexes=2)
    f = _write_msstats_tmt(tmp_path / "q" / "msstatstmt.csv", prots, sim)
    text = f.read_text(encoding="utf-8").replace(",DMSO,", ",A,").replace(",Drug,", ",B,")
    f.write_text(text, encoding="utf-8")
    head = ["source name", "characteristics[biological replicate]", "comment[data file]", "comment[label]",
            "factor value[compound]"]
    rows = [[f"{e}_{ch}", 1, f"{e}_T1.raw", f"TMT{ch}", "DMSO" if ch < "129" else "Drug"]
            for e in sim for ch in TMT10]
    _write(tmp_path / "q" / "x.sdrf.tsv", head, rows)
    m, _files, _n = downstream.load_quantities("MSstatsTMT", tmp_path / "q", tmp_path / "q" / "results", None)
    assert {m.condition[s] for s in m.samples} == {"DMSO", "Drug"} and m.meta["conditions_from"] == "SDRF x.sdrf.tsv"
    assert engines.load_msstats_tmt(f).condition["Exp1_127N"] == "A"  # the engine alone
