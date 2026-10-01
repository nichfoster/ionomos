"""Search quality per run (downstream/psmqc.py, D55): the numbers read from FragPipe's psm.tsv and DIA-NN's
stats.tsv through qcmetrics.py, the two warnings, and the whole analysis (psm_qc.tsv, analysis.json, the report's
payload, the doctor). The psm.tsv column names come from the FragPipe docs (testbed.PSM_HEADER), not from a real
FragPipe run."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from ionomos import downstream, testbed
from ionomos import help as helpdoc
from ionomos.downstream import psmqc, qcmetrics, simulate
from ionomos.downstream.analysis import settings_from


def _psm(path: Path, run: str, n: int = 300, ppm: float = 2.0, missed: float = 0.1, charges=(2, 3), offset=0.0,
         drop: tuple[str, ...] = ()) -> None:
    """A psm.tsv with FragPipe's columns: n PSMs of one run, a planted mass error (ppm, sd 1), missed-cleavage
    share and charge mix. offset: a mass shift in Da on every 10th PSM (an open-search hit). drop: columns left out."""
    rng = random.Random(run)
    header = [c for c in testbed.PSM_HEADER if c not in drop]
    lines = ["\t".join(header)]
    for k in range(n):
        pep = "PEPTIDEK" + "A" * (k % 9)
        calc = 110.0 * len(pep) + 18.0106
        obs = calc * (1 + rng.gauss(ppm, 1.0) * 1e-6) + (offset if offset and k % 10 == 0 else 0.0)
        row = {"Spectrum": f"{run}.{k + 1:05d}.{k + 1:05d}.2", "Peptide": pep, "Peptide Length": len(pep),
               "Charge": charges[k % len(charges)], "Retention": 600 + k, "Observed Mass": f"{obs:.6f}",
               "Calculated Peptide Mass": f"{calc:.6f}", "Delta Mass": f"{obs - calc:.6f}",
               "Number of Missed Cleavages": (2 if k % 20 == 0 else 1) if k < n * missed else 0,
               "Intensity": 1e6, "Protein": f"sp|P{k % 40:05d}|X_HUMAN"}
        lines.append("\t".join(str(row.get(c, "")) for c in header))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------- the reader --


def test_read_psm_adds_the_detail_and_keeps_the_trend_metrics(tmp_path):
    wd = tmp_path / "fragpipe"
    _psm(wd / "DMSO_1" / "psm.tsv", "DMSO_1", n=300, ppm=3.0, missed=0.2, charges=(2, 2, 3, 4))
    found, notes = qcmetrics.run_metrics(wd, {"DMSO_1": ("DMSO", 1)})
    assert notes == []
    rec = found["DMSO_1"]
    assert rec["metrics"]["psms"] == 300 and 2.5 < rec["metrics"]["ms1_ppm"] < 3.5  # as before
    d = rec["psm"]
    assert d["folder"] == "DMSO_1" and d["ppm_n"] == 300
    q05, q25, med, q75, q95 = d["ppm"]
    assert q05 < q25 < med < q75 < q95 and med == pytest.approx(rec["metrics"]["ms1_ppm"], abs=1e-3)
    assert 1.0 < q75 - q25 < 1.8  # sd 1 planted: IQR ~1.35
    assert d["missed"] == {0: 240, 1: 57, 2: 3} and d["charge"] == {2: 150, 3: 75, 4: 75}
    assert sum(d["length"].values()) == 300 and min(d["length"]) == 8 and max(d["length"]) == 16


def test_quantile():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert [qcmetrics.quantile(xs, q) for q in (0, 0.25, 0.5, 1)] == [1.0, 2.0, 3.0, 5.0]
    assert qcmetrics.quantile([1.0, 2.0], 0.5) == 1.5 and qcmetrics.quantile([7.0], 0.95) == 7.0


def test_search_tables_take_every_run_without_a_manifest(tmp_path):
    wd = tmp_path / "fragpipe"
    testbed.fake_psm(wd / "A_1" / "psm.tsv", [r"C:\x\raw\A_1.raw", r"C:\x\raw\A_1_F2.raw"])
    testbed.fake_diann_stats(wd / "diann-output" / "report.stats.tsv", [r"C:\x\raw\B_1.raw", "/x/B_2_uncalibrated.mzML"])
    (wd / "fragpipe_previous_1" / "A_9").mkdir(parents=True)
    testbed.fake_psm(wd / "fragpipe_previous_1" / "A_9" / "psm.tsv", ["A_9.raw"])  # an earlier attempt: not read
    psm, diann, notes = qcmetrics.search_tables(wd)
    assert notes == [] and sorted(psm) == ["A_1", "A_1_F2"] and sorted(diann) == ["B_1", "B_2"]
    assert psm["A_1"]["rt"] and "precursors" in diann["B_1"]["metrics"]
    assert qcmetrics.search_tables(tmp_path / "nowhere") == ({}, {}, [])


def test_search_tables_skip_what_is_too_big_or_broken(tmp_path):
    wd = tmp_path / "fragpipe"
    testbed.fake_psm(wd / "A_1" / "psm.tsv", ["A_1.raw"])
    (wd / "B_1").mkdir()
    (wd / "B_1" / "psm.tsv").write_text("Nonsense\tColumns\na\tb\n", encoding="utf-8")
    psm, _diann, notes = qcmetrics.search_tables(wd)
    assert sorted(psm) == ["A_1"] and len(notes) == 1 and notes[0].startswith("B_1/psm.tsv: ") and "Spectrum" in notes[0]
    psm, _diann, notes = qcmetrics.search_tables(wd, max_mb=0.001)
    assert psm == {} and any(n.startswith("A_1/psm.tsv is ") and "limit; not read" in n for n in notes)


# -------------------------------------------------------------- the module --


def test_run_gives_a_row_per_run_and_no_flags_on_ordinary_data(tmp_path):
    wd = tmp_path / "fragpipe"
    for s in ("DMSO_1", "DMSO_2", "Drug_1"):
        testbed.fake_psm(wd / s / "psm.tsv", [rf"C:\x\raw\{s}.raw"])
    res = psmqc.run(wd)
    assert res.found and [r["run"] for r in res.runs] == ["DMSO_1", "DMSO_2", "Drug_1"] and res.diann == []
    assert res.problems == [] and res.notes == [] and res.charges == [2, 3]
    r = res.runs[0]
    assert r["sample"] == "DMSO_1" and r["psms"] == 240 and r["peptides"] == 160 and r["flags"] == []
    assert 1.0 < r["ppm"][2] < 3.0 and r["ppm_n"] == 240  # the testbed plants ~2 ppm
    assert sum(r["missed"]) == 240 and r["missed_rate"] == pytest.approx(r["missed"][1] / 240) and 0.02 < r["missed_rate"] < 0.25
    assert r["missed_mean"] == pytest.approx(r["missed_rate"], abs=1e-3)  # no PSM with 2 here
    assert 8 <= r["length"] <= 19
    s = psmqc.summary(res, "results/psm_qc.tsv")
    assert s["ran"] and s["runs"] == 3 and s["psms"] == 720 and s["flagged"] == {} and s["diann_runs"] == 0
    assert 1.0 < s["mass_error_ppm"]["median"] < 3.0 and s["limits"] == {"mass_error_ppm": 10.0,
                                                                        "missed_cleavage_rate": 0.5, "min_psms": 100}


def test_a_psm_tsv_in_the_search_folder_itself_has_no_sample(tmp_path):
    wd = tmp_path / "fragpipe"
    testbed.fake_psm(wd / "psm.tsv", ["A_1.raw", "A_2.raw"])
    res = psmqc.run(wd)
    assert [(r["run"], r["sample"]) for r in res.runs] == [("A_1", ""), ("A_2", "")]


def test_flags_and_problems_on_planted_runs(tmp_path):
    wd = tmp_path / "fragpipe"
    _psm(wd / "ok_1" / "psm.tsv", "ok_1")
    _psm(wd / "off_1" / "psm.tsv", "off_1", ppm=-14.0)                 # calibration off
    _psm(wd / "edge_1" / "psm.tsv", "edge_1", ppm=8.5, missed=0.45)    # inside both limits
    _psm(wd / "dig_1" / "psm.tsv", "dig_1", missed=0.6)                # incomplete digestion
    _psm(wd / "few_1" / "psm.tsv", "few_1", n=60, ppm=20.0, missed=0.9)  # too few PSMs to judge
    res = psmqc.run(wd)
    flags = {r["run"]: r["flags"] for r in res.runs}
    assert flags == {"ok_1": [], "off_1": ["mass error"], "edge_1": [], "dig_1": ["missed cleavages"], "few_1": []}
    assert [c for c, _m in res.problems] == ["PSM_MASS_ERROR", "PSM_MISSED_CLEAVAGES"]
    assert re.search(r"1 of 5 run\(s\).*10 ppm or more: off_1 \(-14\.\d ppm\)\.", res.problems[0][1])
    assert re.search(r"1 of 5 run\(s\), 50% or more.*: dig_1 \(60%\)\.", res.problems[1][1])
    dig = next(r for r in res.runs if r["run"] == "dig_1")
    assert dig["missed"] == [120, 171, 9] and dig["missed_rate"] == pytest.approx(0.6)
    assert psmqc.summary(res)["flagged"] == {"off_1": ["mass error"], "dig_1": ["missed cleavages"]}


def test_many_flagged_runs_are_named_up_to_six(tmp_path):
    wd = tmp_path / "fragpipe"
    for k in range(8):
        _psm(wd / f"r_{k}" / "psm.tsv", f"r_{k}", n=120, ppm=12.0)
    (code, msg), = psmqc.run(wd).problems
    assert code == "PSM_MASS_ERROR" and msg.startswith("8 of 8 run(s)") and msg.count(" ppm)") == 6 and "and 2 more" in msg


def test_mass_offsets_and_missing_columns_do_not_break_or_mislead(tmp_path):
    wd = tmp_path / "fragpipe"
    _psm(wd / "open_1" / "psm.tsv", "open_1", offset=57.02146)  # every 10th PSM carries a +57 Da offset
    _psm(wd / "bare_1" / "psm.tsv", "bare_1", drop=("Observed Mass", "Calculated Peptide Mass", "Delta Mass",
                                                    "Number of Missed Cleavages", "Peptide Length"))
    res = psmqc.run(wd)
    bare, opened = res.runs
    assert opened["ppm_n"] == 270 and 1.5 < opened["ppm"][2] < 2.5 and opened["flags"] == []  # offsets left out
    assert bare["ppm"] is None and bare["missed"] is None and bare["missed_rate"] is None and bare["flags"] == []
    assert bare["length"] == 12.0  # from the Peptide column
    rows = psmqc.table_rows(res)
    assert rows[0]["mass_error_median_ppm"] is None and rows[0]["missed_0"] is None
    assert psmqc.report_payload(res)["runs"][0]["ppm"] is None


def test_nothing_found_says_why(tmp_path):
    (tmp_path / "fragpipe").mkdir()
    res = psmqc.run(tmp_path / "fragpipe")
    assert not res.found and "no psm.tsv" in res.reason
    assert psmqc.summary(res) == {"ran": False, "reason": res.reason} and psmqc.report_payload(res) is None
    (tmp_path / "fragpipe" / "psm.tsv").write_text("Nonsense\na\n", encoding="utf-8")
    assert "Spectrum" in psmqc.run(tmp_path / "fragpipe").reason


def test_table_and_payload_with_diann(tmp_path):
    wd = tmp_path / "fragpipe"
    _psm(wd / "A_1" / "psm.tsv", "A_1", charges=(2, 3, 3, 4))
    testbed.fake_diann_stats(wd / "diann-output" / "report.stats.tsv", [r"C:\x\raw\A_1.raw"])
    res = psmqc.run(wd)
    cols = psmqc.columns(res)
    assert cols[:6] == ["run", "sample", "source", "psms", "peptides", "proteins"] and len(cols) == len(set(cols))
    assert {"charge_2_pct", "charge_3_pct", "charge_4_pct", "mass_error_median_ppm", "ms1_mass_accuracy_ppm"} <= set(cols)
    fp, dn = psmqc.table_rows(res)
    assert set(fp) <= set(cols) and set(dn) <= set(cols)
    assert fp["source"] == "FragPipe psm.tsv" and fp["charge_2_pct"] == 25.0 and fp["charge_3_pct"] == 50.0
    assert dn["source"] == "DIA-NN stats.tsv" and dn["precursors"] > 30000 and "psms" not in dn
    p = psmqc.report_payload(res)
    assert p["z"] == [2, 3, 4] and p["runs"][0]["z"] == [25.0, 50.0, 25.0] and p["runs"][0]["mc"] == [270, 28, 2]
    assert p["len"]["lo"] == 8 and sum(p["len"]["n"]) == 300 and len(p["len"]["n"]) == 9
    assert p["diann"][0]["run"] == "A_1" and p["limits"] == {"ppm": 10.0, "missed": 0.5, "minPsms": 100}
    json.dumps(p, allow_nan=False)


# ------------------------------------------------------------ end to end --


def _experiment(tmp_path: Path, bad: bool = False) -> Path:
    d = tmp_path / "e"
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(d / "fragpipe" / "report.pg_matrix.tsv", runs, seed=6, n_proteins=90)
    for raw, _c in runs:
        stem = Path(raw).stem
        if bad and stem == "Drug_2":
            _psm(d / "fragpipe" / stem / "psm.tsv", stem, ppm=13.0, missed=0.7)
        else:
            testbed.fake_psm(d / "fragpipe" / stem / "psm.tsv", [raw])
    return d


def _payload(out) -> dict:
    html = out.report.read_text(encoding="utf-8")
    return json.loads(re.search(r"<script id='ionomos-data' type='application/json'>(.*?)</script>", html, re.S).group(1))


def test_end_to_end_table_json_report(tmp_path):
    d = _experiment(tmp_path)
    out = downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False})
    q = out.summary["psm_qc"]
    assert q["ran"] and q["table"] == "results/psm_qc.tsv" and q["runs"] == 6 and q["psms"] == 1440 and q["flagged"] == {}
    assert json.loads((d / "results" / "analysis.json").read_text(encoding="utf-8"))["psm_qc"] == q
    lines = (d / "results" / "psm_qc.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t")[:4] == ["run", "sample", "source", "psms"] and len(lines) == 7
    assert lines[1].split("\t")[:4] == ["DMSO_1", "DMSO_1", "FragPipe psm.tsv", "240"]
    data = _payload(out)
    assert [r["run"] for r in data["qc"]["psm"]["runs"]] == ["DMSO_1", "DMSO_2", "DMSO_3", "Drug_1", "Drug_2", "Drug_3"]
    assert "pca" in data["qc"] and "psm_qc.tsv" in data["files"]
    assert not [i for i in out.issues if i.code.startswith("PSM_")]
    assert data["help"]["entries"]["qc.psm"]["t"] == "Search quality"


def test_end_to_end_flagged_run_becomes_two_warnings(tmp_path):
    out = downstream.analyze(_experiment(tmp_path, bad=True), "DIA", analysis_cfg={"enrichment": False})
    issues = {i.code: i for i in out.issues}
    assert issues["PSM_MASS_ERROR"].severity == "warning" and "Drug_2 (+13." in issues["PSM_MASS_ERROR"].message
    assert issues["PSM_MISSED_CLEAVAGES"].severity == "warning" and "Drug_2 (70%)" in issues["PSM_MISSED_CLEAVAGES"].message
    assert out.summary["state"] == "ok"  # warnings: shown, no pop-up
    assert out.summary["psm_qc"]["flagged"] == {"Drug_2": ["mass error", "missed cleavages"]}
    data = _payload(out)
    assert data["qc"]["psm"]["runs"][4]["flags"] == ["mass error", "missed cleavages"]
    ids = {x["id"] for x in data["help"]["issues"]}
    assert {"issue.PSM_MASS_ERROR", "issue.PSM_MISSED_CLEAVAGES"} <= ids


def test_switched_off_absent_and_without_quantities(tmp_path):
    d = _experiment(tmp_path)
    off = downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False, "psm_qc": False})
    assert off.summary["psm_qc"] == {"ran": False, "reason": "search quality is switched off (analysis.psm_qc)"}
    assert "psm" not in _payload(off)["qc"] and not any(p.name == "psm_qc.tsv" for p in off.files)

    none = tmp_path / "none"
    simulate.dia_pg_matrix(none / "fragpipe" / "report.pg_matrix.tsv", [(f"/x/{c}_{r}.raw", c) for c in ("A", "B")
                                                                        for r in (1, 2, 3)], seed=6, n_proteins=90)
    out = downstream.analyze(none, "DIA", analysis_cfg={"enrichment": False})
    assert out.summary["psm_qc"]["ran"] is False and "no psm.tsv" in out.summary["psm_qc"]["reason"]
    assert "psm" not in _payload(out)["qc"] and not (none / "results" / "psm_qc.tsv").exists()

    bare = tmp_path / "bare"  # the search wrote PSMs but no quant table: the tab is still there, with the warning
    _psm(bare / "fragpipe" / "A_1" / "psm.tsv", "A_1", ppm=15.0)
    out = downstream.analyze(bare, "LFQ")
    codes = [i.code for i in out.issues]
    assert "NO_TABLE" in codes and "PSM_MASS_ERROR" in codes
    assert out.summary["psm_qc"]["ran"] and _payload(out)["qc"]["psm"]["runs"][0]["flags"] == ["mass error"]


def test_a_crash_in_the_step_costs_nothing_else(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("psm boom")

    monkeypatch.setattr(psmqc, "run", boom)
    out = downstream.analyze(_experiment(tmp_path), "DIA", analysis_cfg={"enrichment": False})
    assert out.report is not None and out.report.is_file() and out.summary["comparisons"]
    assert out.summary["psm_qc"] == {"ran": False, "reason": "the search-quality step failed (see analysis_error.txt)"}
    crash = next(i for i in out.issues if i.code == "CRASH_PSM_QC")
    assert crash.severity == "warning" and "psm boom" in crash.message


def test_the_analysis_does_not_touch_the_search_output(tmp_path):
    d = _experiment(tmp_path)
    before = {p: p.read_bytes() for p in (d / "fragpipe").rglob("*") if p.is_file()}
    downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False})
    assert {p: p.read_bytes() for p in (d / "fragpipe").rglob("*") if p.is_file()} == before


# ------------------------------------------------- settings, doctor, help --


def test_setting_and_help():
    assert settings_from({}).psm_qc is True and settings_from({"psm_qc": "false"}).psm_qc is False
    assert settings_from({"psm_qc": True}, {"psm_qc": False}).psm_qc is False  # the experiment overrides the lab
    ents = helpdoc.entries()
    assert ents["qc.psm"].title == "Search quality" and "psm_qc.tsv" in ents["qc.psm"].body
    for code in ("PSM_MASS_ERROR", "PSM_MISSED_CLEAVAGES"):
        assert helpdoc.issue_entry(code) == f"issue.{code}"
    for text, limit in ((ents["issue.PSM_MASS_ERROR"].body, f"{psmqc.PPM_WARN:g} ppm"),
                        (ents["qc.psm"].body, f"{psmqc.PPM_WARN:g} ppm"),
                        (ents["qc.psm"].body, f"{psmqc.MISSED_WARN:.0%}"),
                        (ents["issue.PSM_MISSED_CLEAVAGES"].body, f"{psmqc.MISSED_WARN:.0%}")):
        assert limit in text, "the help names the limit the code uses"
