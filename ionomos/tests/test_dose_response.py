"""Dose-response curves (downstream/doseresponse.py): the CurveCurator port against CurveCurator 0.6.0 itself
(tests/golden/dose_response/, made by run_curvecurator.py; CurveCurator is not needed here), recovery of planted
EC50s, dose parsing, the stage in analyze() (skipped with a note, ratio data, a crash leaves the report)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import doseresponse as dr
from ionomos.downstream import fpa, quant, simulate
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from, settings_lenient
from ionomos.downstream.tables import num, read_tsv

GOLDEN = Path(__file__).parent / "golden" / "dose_response"


# ------------------------------------------------------------------- doses --


@pytest.mark.parametrize("value, unit, molar, label", [
    ("10 nM", "", 1e-8, "nM"), ("10nM", "", 1e-8, "nM"), ("0.1uM", "", 1e-7, "µM"), ("0.1 µM", "", 1e-7, "µM"),
    ("2 μM", "", 2e-6, "µM"), ("1mM", "", 1e-3, "mM"), ("5 M", "", 5.0, "M"), ("250 pM", "", 2.5e-10, "pM"),
    ("1e3 nM", "", 1e-6, "nM"), (0, "", 0.0, ""), ("0", "", 0.0, ""), (10, "nM", 1e-8, "nM"), ("3", "uM", 3e-6, "µM"),
    ("10 NM", "", 1e-8, "nM"),
])
def test_parse_dose(value, unit, molar, label):
    d, u = dr.parse_dose(value, unit)
    assert d == pytest.approx(molar, rel=1e-12) and u == label


@pytest.mark.parametrize("value", ["10", 10, "ten nM", "10 mg", "-1 nM", True, "10 nM 20 nM"])
def test_parse_dose_refuses_what_it_cannot_read(value):
    with pytest.raises(dr.DoseError):
        dr.parse_dose(value)


@pytest.mark.parametrize("name, molar, unit, series", [
    ("Cmpd_10nM", 1e-8, "nM", "Cmpd"), ("10nM_Cmpd", 1e-8, "nM", "Cmpd"), ("Cmpd-0p1uM", 1e-7, "µM", "Cmpd"),
    ("Cmpd10nM", 1e-8, "nM", "Cmpd"), ("KRAS_G12C_1uM", 1e-6, "µM", "KRAS_G12C"), ("1uM", 1e-6, "µM", ""),
    ("Ibrutinib_2.5uM", 2.5e-6, "µM", "Ibrutinib"), ("Cmpd_10 µM", 1e-5, "µM", "Cmpd"), ("X_1mM", 1e-3, "mM", "X"),
])
def test_dose_in_name(name, molar, unit, series):
    d, u, s = dr.dose_in_name(name)
    assert d == pytest.approx(molar) and u == unit and s == series


@pytest.mark.parametrize("name", ["DMSO", "Cmpd_10min", "EJQ_2_027", "Cmpd_3M4", "CV35", "NT"])
def test_names_without_a_dose(name):
    assert dr.dose_in_name(name) is None


def test_two_doses_in_one_name_are_ambiguous():
    with pytest.raises(dr.DoseError, match="2 doses"):
        dr.dose_in_name("A_1uM_B_10nM")


def test_fmt_dose_picks_a_readable_unit():
    assert [dr.fmt_dose(x) for x in (1e-9, 3e-6, 1e-5, 2.5e-10, 0.0)] == ["1 nM", "3 µM", "10 µM", "250 pM", "0"]


def _conds(doses=("1nM", "10nM", "100nM", "1uM", "10uM"), compound="Cmpd"):
    return ["DMSO", *[f"{compound}_{d}" for d in doses]]


def test_plan_reads_the_names_and_the_control():
    plan = dr.plan_series(_conds(), Settings(), "DMSO")
    assert [s.name for s in plan.series] == ["Cmpd"] and not plan.problems
    sr = plan.series[0]
    assert sr.controls == ["DMSO"] and len(sr.doses) == 5 and sr.unit == "nM"
    assert sr.dose_of["Cmpd_1uM"] == pytest.approx(1e-6)


def test_plan_two_compounds_share_the_control():
    conds = ["DMSO", *_conds(compound="A")[1:], *_conds(("3nM", "30nM", "300nM", "3uM"), "B")[1:]]
    plan = dr.plan_series(conds, Settings(), "DMSO")
    assert {s.name: len(s.doses) for s in plan.series} == {"A": 5, "B": 4}
    assert all(s.controls == ["DMSO"] for s in plan.series)


def test_plan_fewer_doses_than_needed_is_a_note_not_a_problem():
    plan = dr.plan_series(_conds(("1uM", "10uM", "100uM")), Settings(), "DMSO")
    assert not plan.series and not plan.problems
    assert plan.skipped and "3 doses above zero" in plan.notes[0] and "at least 4" in plan.notes[0]
    assert "fewer than 4 doses" in plan.reason
    ok = dr.plan_series(_conds(("1uM", "10uM", "100uM")), Settings(dose_min_doses=3), "DMSO")
    assert len(ok.series) == 1


def test_plan_without_doses_says_so_quietly():
    plan = dr.plan_series(["DMSO", "Drug"], Settings(), "DMSO")
    assert not plan.series and not plan.notes and not plan.problems and "No doses were found" in plan.reason


def test_plan_intensity_without_a_control_is_a_warning():
    plan = dr.plan_series(_conds()[1:], Settings(), None)
    assert not plan.series and plan.problems[0][0] == "warning" and "no control" in plan.problems[0][1]
    ratio = dr.plan_series(_conds()[1:], Settings(), None, kind="ratio")  # isoDTB ratios are already to DMSO
    assert len(ratio.series) == 1 and ratio.series[0].controls == []


def test_plan_ambiguous_name_is_left_out_with_a_warning():
    plan = dr.plan_series([*_conds(), "Cmpd_1uM_Other_10nM"], Settings(), "DMSO")
    assert len(plan.series) == 1 and "Cmpd_1uM_Other_10nM" not in plan.series[0].dose_of
    assert plan.problems[0][0] == "warning" and "analysis.doses" in plan.problems[0][1]


def test_plan_explicit_doses_win_and_bad_ones_need_a_person():
    s = settings_from({"doses": {"Veh": 0, "D1": "1 nM", "D2": "10 nM", "D3": "100 nM", "D4": "1 uM"}})
    plan = dr.plan_series(["Veh", "D1", "D2", "D3", "D4", "Other"], s, None)
    assert len(plan.series) == 1 and plan.series[0].controls == ["Veh"] and len(plan.series[0].doses) == 4
    assert any("Other" in n for n in plan.notes)
    bad = settings_from({"doses": {"DMSO": 0, "Nope": "1 nM"}})
    plan = dr.plan_series(["DMSO", "Drug"], bad, None)
    assert plan.problems and plan.problems[0][0] == "input" and "'Nope'" in plan.problems[0][1]


def test_dose_settings_are_validated():
    s = settings_from({"doses": {"DMSO": 0, "A": 10}, "dose_unit": "uM"})
    assert s.dose_unit == "µM" and s.doses == {"DMSO": 0, "A": 10}
    with pytest.raises(AnalysisError, match="has no unit"):
        settings_from({"doses": {"DMSO": 0, "A": 10}})
    with pytest.raises(AnalysisError, match="dose_unit"):
        settings_from({"dose_unit": "mg"})
    with pytest.raises(AnalysisError, match="map a condition"):
        settings_from({"doses": ["1 nM"]})
    for bad in ({"dose_alpha": 1.5}, {"dose_fc_lim": -1}, {"dose_min_doses": 2}):
        with pytest.raises(AnalysisError):
            settings_from(bad)
    # lenient: a bare number is read with the dose_unit written after it in the same block
    s, notes = settings_lenient({"doses": {"DMSO": 0, "A": 10}, "dose_unit": "nM"})
    assert not notes and s.doses["A"] == 10


@pytest.mark.parametrize("raw, dose", [("Cmpd_0p1uM_1.raw", 1e-7), ("Cmpd10nM_2.raw", 1e-8),
                                       ("10nM_Cmpd_3.raw", 1e-8), ("Cmpd_1mM_1.raw", 1e-3)])
def test_dia_raw_names_keep_the_dose_in_the_condition(raw, dose):
    from ionomos.naming import parse_raw_name

    d, _u, series = dr.dose_in_name(parse_raw_name(raw, "DIA").sample)
    assert d == pytest.approx(dose) and series == "Cmpd"


def test_the_apps_config_writer_keeps_the_dose_settings():
    import yaml

    from ionomos import configio

    d = configio.defaults()
    d["analysis"].update({"dose_min_doses": 5, "dose_unit": "µM", "dose_response": False,
                          "doses": {"DMSO": 0, "A": "10 nM"}})
    back = yaml.safe_load(configio.dump_config(d))["analysis"]
    assert {k: back[k] for k in ("dose_min_doses", "dose_unit", "dose_response", "doses")} == {
        "dose_min_doses": 5, "dose_unit": "µM", "dose_response": False, "doses": {"DMSO": 0, "A": "10 nM"}}
    settings_from(back)  # and it is valid


# ----------------------------------------------------- F distribution, fit --


@pytest.mark.parametrize("f, d2, sf, logsf", [  # scipy.stats.f(5, d2, loc=0.12)
    (3.0, 10.3, 0.07078677859187722, -2.6480830387736995), (0.5, 4.1, 0.8416963079687997, -0.17233600917223144),
    (40.0, 7.25, 4.1184855465563816e-05, -10.097439954972295), (400.0, 20.0, 2.541026506227057e-19, -42.81654863118764),
    (0.1, 6.0, 1.0, 0.0),
])
def test_f_tail_matches_scipy(f, d2, sf, logsf):
    assert dr.f_sf(f, 5, d2, 0.12) == pytest.approx(sf, rel=1e-9)
    assert dr.f_logsf(f, 5, d2, 0.12) == pytest.approx(logsf, rel=1e-9, abs=1e-12)


@pytest.mark.parametrize("q, d2, x", [(0.95, 6.3, 4.2437736050383945), (0.95, 12.7, 3.0480125173270465),
                                      (0.99, 4.5, 12.783181539739175)])
def test_f_quantile_matches_scipy(q, d2, x):
    assert dr.f_ppf(q, 5, d2) == pytest.approx(x, rel=1e-9)


def test_exact_curve_is_recovered():
    xs = [math.log10(d * 1e-9) for d in (1, 3, 10, 30, 100, 300, 1000, 3000, 10000)]
    true = (7.3, 1.4, 1.0, 0.2)
    fit = dr.fit_curve(xs, [dr.curve(x, *true) for x in xs])
    assert fit["pec50"] == pytest.approx(7.3, abs=1e-4) and fit["slope"] == pytest.approx(1.4, abs=1e-3)
    assert fit["back"] == pytest.approx(0.2, abs=1e-4) and fit["r2"] > 0.9999
    assert fit["fold_change"] == pytest.approx(math.log2(dr.curve(xs[-1], *true) / dr.curve(xs[0], *true)), abs=1e-4)
    s0, score = dr.relevance(fit)
    assert dr.classify(fit, score) == "down"


def test_too_few_points_is_not_fitted():
    assert dr.fit_curve([-8.0, -7.0, -6.0], [1.0, 0.8, 0.3]) is None


def test_flat_quiet_curve_is_not_regulated():
    xs = [math.log10(d * 1e-9) for d in (1, 10, 100, 1000, 10000)]
    fit = dr.fit_curve(xs, [1.01, 0.98, 1.02, 0.99, 1.0])
    _s0, score = dr.relevance(fit)
    assert dr.classify(fit, score) == "not"


def _curves_from_titration(**kw):
    samples, cond, dose, rows, truth = simulate.dose_titration(**kw)
    feats = [quant.Feature(pid, g) for pid, g, _ in rows]
    kind = "ratio" if kw.get("ratio") else "intensity"
    m = quant.QuantMatrix(kind, "site" if kind == "ratio" else "protein", feats, samples, [v for _, _, v in rows], cond)
    p, _ = fpa.process(m, imputation="none")
    res, plan = dr.run(p, Settings(), "DMSO" if kind == "intensity" else None)
    return res, plan, truth


def test_planted_ec50s_are_recovered_and_flat_features_are_not_called():
    res, plan, truth = _curves_from_titration(doses_nm=[1, 3, 10, 30, 100, 300, 1000, 3000, 10000], replicates=2,
                                              n=240, seed=3, curve_fraction=0.4, noise=0.12)
    assert res is not None and len(res.curves) == 1
    rows = res.curves[0].rows
    err, called, planted, false = [], 0, 0, 0
    for r in rows:
        t = truth[r["id"]]
        if t["class"] in ("up", "down"):
            planted += 1
            if r["class"] == t["class"]:
                called += 1
                err.append(abs(r["pec50"] - t["pec50"]))
                assert r["pec50_ci_low"] < r["pec50"] < r["pec50_ci_high"]
        elif r["class"] in ("up", "down"):
            false += 1
    assert called >= 0.85 * planted, (called, planted)
    assert sorted(err)[len(err) // 2] < 0.1 and sum(e < 0.3 for e in err) >= 0.9 * len(err)
    assert false <= 0.03 * (len(rows) - planted), false
    covered = sum(r["pec50_ci_low"] <= truth[r["id"]]["pec50"] <= r["pec50_ci_high"] for r in rows
                  if r["class"] in ("up", "down") and truth[r["id"]]["class"] == r["class"])
    assert covered >= 0.8 * called  # the Jacobian interval is approximate, but should mostly cover the truth


def test_ratio_data_uses_one_as_the_control():
    res, plan, truth = _curves_from_titration(doses_nm=[10, 100, 1000, 10000, 100000], replicates=3, n=120,
                                              seed=5, curve_fraction=0.5, ratio=True, compound="Frag")
    assert res is not None and res.curves[0].series.name == "Frag" and res.curves[0].series.controls == []
    right = sum(r["class"] == truth[r["id"]]["class"] for r in res.curves[0].rows
                if truth[r["id"]]["class"] in ("up", "down"))
    planted = sum(t["class"] in ("up", "down") for t in truth.values())
    assert right >= 0.8 * planted


# ------------------------------------------------ against CurveCurator 0.6.0 --


def _golden(design: str):
    _h, des = read_tsv(GOLDEN / f"{design}_design.tsv")
    _h, mx = read_tsv(GOLDEN / f"{design}_matrix.tsv")
    _h, cc = read_tsv(GOLDEN / f"{design}_curvecurator.tsv")
    return des, mx, {r["id"]: r for r in cc}


@pytest.mark.parametrize("design", ["A", "B"])
def test_port_agrees_with_curvecurator(design):
    des, mx, cc = _golden(design)
    dose = {r["sample"]: float(r["dose_nM"]) for r in des}
    ctrl = [s for s in dose if dose[s] == 0]
    order = sorted((s for s in dose if dose[s] > 0), key=lambda s: dose[s])
    all_x = sorted({math.log10(dose[s] * 1e-9) for s in order})
    same, regulated, close, f_rel, p_log = 0, 0, 0, [], []
    for r in mx:
        cv = [2 ** num(r[s]) for s in ctrl if num(r[s]) is not None]
        ref = sum(cv) / len(cv)
        pts = [(math.log10(dose[s] * 1e-9), 2 ** num(r[s]) / ref) for s in order if num(r[s]) is not None]
        fit = dr.fit_curve([a for a, _ in pts], [b for _, b in pts], all_x)
        _s0, score = dr.relevance(fit)
        c = cc[r["id"]]
        same += dr.classify(fit, score) == c["class"]
        f_rel.append(abs(fit["f"] - float(c["f_value"])) / max(1.0, float(c["f_value"])))
        p_log.append(abs(math.log10(fit["p"]) - math.log10(float(c["pvalue"]))))
        if c["class"] in ("up", "down"):
            regulated += 1
            close += abs(fit["pec50"] - float(c["pec50"])) <= 0.05
            if abs(fit["pec50"] - float(c["pec50"])) > 0.05:  # a different minimum: never a worse one
                x = [-math.inf, *(a for a, _ in pts)]
                y = [1.0, *(b for _, b in pts)]
                par = [float(c[k]) for k in ("pec50", "slope", "front", "back")]
                assert fit["sse"] <= sum((dr.curve(a, *par) - b) ** 2 for a, b in zip(x, y, strict=True)) * (1 + 1e-6)
    n = len(mx)
    assert same >= 0.99 * n, f"{same}/{n} classes agree"
    assert regulated > 100 and close >= 0.97 * regulated, f"{close}/{regulated} pEC50 within 0.05"
    assert sorted(f_rel)[n // 2] < 1e-3 and max(f_rel) < 0.1
    assert sorted(p_log)[n // 2] < 1e-3 and max(p_log) < 0.1


# -------------------------------------------------------- the stage in analyze --


def _dose_experiment(tmp_path: Path, doses, **kw) -> Path:
    dest = tmp_path / "exp"
    simulate.dose_pg_matrix(dest / "fragpipe/diann-output/report.pg_matrix.tsv", doses, n=kw.pop("n", 150), **kw)
    return dest


def test_analyze_fits_curves_and_reports_them(tmp_path):
    dest = _dose_experiment(tmp_path, [1, 10, 100, 1000, 10000], seed=4, curve_fraction=0.3)
    out = downstream.analyze(dest, "DIA", context={"experiment": "Dose"})
    info = json.loads((dest / "results/analysis.json").read_text(encoding="utf-8"))["dose_response"]
    assert info["ran"] and info["table"] == "results/dose_response.tsv"
    ser = info["series"][0]
    assert ser["name"] == "Cmpd" and ser["doses"] == ["1 nM", "10 nM", "100 nM", "1 µM", "10 µM"]
    assert ser["up"] + ser["down"] > 20 and ser["curves"] == 150
    header, rows = read_tsv(dest / "results/dose_response.tsv")
    assert header == dr.COLUMNS and len(rows) == 150 and {r["unit"] for r in rows} == {"nM"}
    assert not any(i.code.startswith("CRASH") or i.code == "DOSES" for i in out.issues)
    html = out.report.read_text(encoding="utf-8")
    assert "id='dose'>" in html and "Dose-response curves (Cmpd) were fitted" in html
    data = json.loads(html.split("<script id='ionomos-data' type='application/json'>")[1].split("</script>")[0])
    assert data["dose"]["ran"] and len(data["dose"]["series"][0]["y"][0]) == len(data["dose"]["series"][0]["samples"])


def test_fewer_than_four_doses_skips_the_stage_with_a_note(tmp_path):
    dest = _dose_experiment(tmp_path, [10, 100, 1000], seed=2)
    out = downstream.analyze(dest, "DIA")
    info = out.summary["dose_response"]
    assert not info["ran"] and "fewer than 4 doses" in info["reason"]
    assert any("dose-response Cmpd: skipped: 3 doses" in n for n in out.warnings)
    assert not (dest / "results/dose_response.tsv").exists()
    assert "id='dose'>" in out.report.read_text(encoding="utf-8")  # shown, with the reason


def test_two_condition_experiment_has_no_dose_section(tmp_path):
    dest = tmp_path / "exp"
    runs = [(f"C:\\raw\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe/diann-output/report.pg_matrix.tsv", runs, n_proteins=60)
    out = downstream.analyze(dest, "DIA")
    assert not out.summary["dose_response"]["ran"] and not any("dose" in n for n in out.warnings)
    assert "<section id='dose' hidden>" in out.report.read_text(encoding="utf-8")


def test_a_crash_in_the_stage_still_makes_the_report(tmp_path, monkeypatch):
    dest = _dose_experiment(tmp_path, [1, 10, 100, 1000, 10000], seed=4)

    def boom(*a, **k):
        raise RuntimeError("curve fitting exploded")

    monkeypatch.setattr(dr, "run", boom)
    out = downstream.analyze(dest, "DIA")
    assert out.report is not None and out.report.is_file()
    assert out.summary["dose_response"] == {"ran": False,
                                            "reason": "the dose-response step failed (see analysis_error.txt)"}
    crash = [i for i in out.issues if i.code == "CRASH_DOSE_RESPONSE"]
    assert crash and crash[0].severity == "warning"
    assert out.summary["comparisons"] and all(c["volcano"] for c in out.summary["comparisons"])


def test_bad_explicit_doses_become_an_issue(tmp_path):
    dest = _dose_experiment(tmp_path, [1, 10, 100, 1000, 10000], seed=4, n=60)
    out = downstream.analyze(dest, "DIA", overrides={"doses": {"DMSO": 0, "Cmpd_1nM": "1 nM", "Missing": "5 nM"}})
    issue = [i for i in out.issues if i.code == "DOSES"]
    assert issue and issue[0].severity == "input" and "'Missing'" in issue[0].message


def test_dose_response_can_be_switched_off(tmp_path):
    dest = _dose_experiment(tmp_path, [1, 10, 100, 1000, 10000], seed=4, n=40)
    out = downstream.analyze(dest, "DIA", overrides={"dose_response": False})
    assert out.summary["dose_response"]["ran"] is False and "switched off" in out.summary["dose_response"]["reason"]
