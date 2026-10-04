"""Time courses (downstream/timecourse.py, D53): reading times, planning series, the tests against limma itself
(tests/golden/timecourse/, limma 3.68.5), patterns, and the whole analysis on a simulated experiment."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, fpa, simulate, splines
from ionomos.downstream import timecourse as tc
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from
from ionomos.downstream.quant import Feature, QuantMatrix

GOLD = Path(__file__).parent / "golden" / "timecourse"


# -------------------------------------------------------------------- times --


@pytest.mark.parametrize("name, want", [
    ("Drug_0h", (0.0, "h", "Drug")), ("Drug_30min", (0.5, "min", "Drug")), ("Drug_4h", (4.0, "h", "Drug")),
    ("T24h", (24.0, "h", "")), ("2d_KO", (48.0, "d", "KO")), ("Cmpd-0p5h", (0.5, "h", "Cmpd")),
    ("WT 90 s", (0.025, "s", "WT")), ("Drug_1uM_3hr", (3.0, "h", "Drug_1uM")), ("x_45mins", (0.75, "min", "x")),
])
def test_time_in_name(name, want):
    t, u, rest = tc.time_in_name(name)
    assert (pytest.approx(t), u, rest) == want


@pytest.mark.parametrize("name", ["DMSO", "Cmpd_10mM", "Drug_1", "Cmpd2h", "KO3d7", "sample_h", "10M", "day_one"])
def test_names_without_a_time(name):
    assert tc.time_in_name(name) is None


def test_two_times_in_a_name_and_parse_time():
    with pytest.raises(tc.TimeError, match="2 times"):
        tc.time_in_name("Drug_1h_then_4h")
    assert tc.parse_time("30 min") == (0.5, "min") and tc.parse_time(0) == (0.0, "")
    assert tc.parse_time(4, "h") == (4.0, "h") and tc.parse_time("2d") == (48.0, "d")
    for bad in ("soon", -1, "4", True):
        with pytest.raises(tc.TimeError):
            tc.parse_time(bad)
    assert [tc.fmt_time(x) for x in (0, 0.5, 4, 48, 30)] == ["0", "30 min", "4 h", "2 d", "30 h"]
    assert tc.fmt_time(0.5, "h") == "0.5 h" and tc.fmt_time(0, "h") == "0 h"


def test_plan_series_from_names():
    conds = ["Drug_0h", "Drug_1h", "Drug_4h", "DMSO_0h", "DMSO_1h", "DMSO_4h", "Other_1h", "Other_4h"]
    plan = tc.plan_series(conds, Settings(), None)
    assert [(s.name, s.conditions) for s in plan.series] == [("Drug", conds[:3]), ("DMSO", conds[3:6])]
    assert [s.name for s in plan.skipped] == ["Other"] and "Other: skipped: 2 time points (1 h, 4 h)" in plan.notes[0]
    # a control without a time is time 0 of the series that have none
    plan = tc.plan_series(["DMSO", "Drug_1h", "Drug_4h", "KO_0h", "KO_2h", "KO_8h"], Settings(), "DMSO")
    drug, ko = plan.series
    assert drug.conditions == ["DMSO", "Drug_1h", "Drug_4h"] and drug.baseline_shared and drug.times == [0.0, 1.0, 4.0]
    assert ko.conditions == ["KO_0h", "KO_2h", "KO_8h"] and not ko.baseline_shared
    none = tc.plan_series(["DMSO", "Drug"], Settings(), "DMSO")
    assert not none.series and "No times were found" in none.reason
    more = tc.plan_series(conds[:3], Settings(time_min_points=4), None)
    assert not more.series and "needs at least 4" in more.reason


def test_plan_series_explicit_times_and_problems():
    s = settings_from({"times": {"early": 0, "mid": "30 min", "late": 2, "ghost": 1}, "time_unit": "h"})
    plan = tc.plan_series(["early", "mid", "late", "extra"], s, None)
    assert plan.series[0].conditions == ["early", "mid", "late"] and plan.series[0].times == [0.0, 0.5, 2.0]
    assert plan.problems == [("input", "analysis.times names 'ghost', which is not a condition here "
                                       "(conditions: early, mid, late, extra)")]
    assert "left out: extra" in plan.notes[0]
    clash = tc.plan_series(["A_1h", "A_60min", "A_2h", "A_4h"], Settings(), None)
    assert not clash.series and "A_1h and A_60min are both at 1 h" in clash.problems[0][1]
    two = tc.plan_series(["A_1h_2h", "A_0h", "A_4h", "A_8h"], Settings(), None)
    assert two.series and "2 times in its name" in two.problems[0][1]
    for bad in ({"times": {"a": "soon"}}, {"times": {"a": 3}}, {"time_unit": "fortnight"}, {"time_min_points": 2},
                {"times": [1, 2]}):
        with pytest.raises(AnalysisError):
            settings_from(bad)
    assert settings_from({"time_unit": "hours", "times": {"a": 3}}).time_unit == "h"


# ------------------------------------------------------------ against limma --


def _gold(name: str) -> dict[str, dict[str, float]]:
    with open(GOLD / name, encoding="utf-8") as fh:
        return {r["ID"]: {k: (math.nan if v.strip() == "NA" else float(v)) for k, v in r.items() if k != "ID"}
                for r in csv.DictReader(fh, delimiter="\t")}


def _matrix(name: str) -> QuantMatrix:
    with open(GOLD / "tc_samples.tsv", encoding="utf-8") as fh:
        sd = list(csv.DictReader(fh, delimiter="\t"))
    with open(GOLD / name, encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    samples = rows[0][1:]
    assert samples == [x["sample"] for x in sd]
    feats = [Feature(id=r[0], label=r[0]) for r in rows[1:]]
    vals = [[None if v == "NA" else float(v) for v in r[1:]] for r in rows[1:]]
    return QuantMatrix("intensity", "protein", feats, samples, vals, {x["sample"]: x["condition"] for x in sd},
                       name, replicate={x["sample"]: int(x["replicate"]) for x in sd})


def _run(matrix: str, **settings):
    m = _matrix(matrix)
    p = fpa.Processed(m=m, measured=[list(r) for r in m.values], imputed=[[False] * len(m.samples) for _ in m.values],
                      imputation="given")   # as loaded: no filter, nothing imputed, no min_valid rule
    s = Settings(log2fc=1.0, **settings)
    model = analysis.make_model(m, s, [])
    assert not model.problem
    res, plan = tc.run(p, s, None, model)
    return res


def _close(got, want, rel=1e-8):
    if want is None or (isinstance(want, float) and math.isnan(want)):
        return got is None or math.isnan(got)
    return got is not None and math.isclose(got, want, rel_tol=rel, abs_tol=1e-10)


@pytest.mark.parametrize("matrix, gold, settings", [
    ("tc_matrix.tsv", "tc_plain.tsv", {}),
    ("tc_matrix.tsv", "tc_block.tsv", {"block": "replicate"}),
    ("tc_matrix_missing.tsv", "tc_plain_missing.tsv", {}),
])
def test_f_trend_and_interaction_match_limma(matrix, gold, settings):
    res = _run(matrix, **settings)
    want = _gold(gold)
    assert [c.series.name for c in res.courses] == ["Drug", "DMSO"]
    assert res.formula == ("~0 + condition + replicate" if settings else "~0 + condition")
    checked = 0
    for c in res.courses:
        s = c.series.name
        got = {r["id"]: r for r in c.rows}
        tested = [i for i, w in want.items() if not math.isnan(w[f"{s}_P"])]
        assert sorted(got) == sorted(tested) and c.untested == len(want) - len(tested)
        for fid, r in got.items():
            w = want[fid]
            pairs = [(r["F"], w[f"{s}_F"]), (r["pvalue"], w[f"{s}_P"]), (r["qvalue"], w[f"{s}_adjP"]),
                     (r["trend_t"], w[f"{s}_trend_t"]), (r["trend_pvalue"], w[f"{s}_trend_P"]),
                     *[(r["fc"][k], w[f"{s}_fc{k}"]) for k in (1, 2, 3)]]
            if s == "Drug":
                assert r["interaction_vs"] == "DMSO"
                pairs += [(r["interaction_F"], w["inter_F"]), (r["interaction_pvalue"], w["inter_P"])]
                if "missing" not in matrix:
                    pairs += [(r["interaction_qvalue"], w["inter_adjP"]), (r["trend_qvalue"], w[f"{s}_trend_adjP"])]
            else:
                assert r["interaction_vs"] == ""
            bad = [(a, b) for a, b in pairs if not _close(a, b)]
            assert not bad, (s, fid, bad)
            checked += len(pairs)
    assert checked > 2500
    drug = res.courses[0]
    planted = {f"F{i}" for i in range(40)}
    called = {r["id"] for r in drug.rows if r["class"] != "not"}
    assert len(called & planted) >= (30 if "missing" not in matrix else 20) and len(called - planted) <= 2
    assert not [r for r in res.courses[1].rows if r["class"] != "not"]


# ------------------------------------------------------------------ patterns --


def test_patterns_are_deterministic_and_separate_shapes():
    up = [[0, 0.5 + 0.01 * k, 1.0, 2.0] for k in range(10)]
    down = [[0, -0.6, -1.2 - 0.01 * k, -2.0] for k in range(10)]
    pulse = [[0, 2.0, 1.9 + 0.01 * k, 0.1] for k in range(10)]
    a = tc.patterns(up + down + pulse)
    assert a == tc.patterns(up + down + pulse)
    groups = [set(a[0:10]), set(a[10:20]), set(a[20:30])]
    assert all(len(g) <= 2 for g in groups) and not (groups[0] & groups[1]) and not (groups[0] & groups[2])
    assert tc.patterns([]) == [] and tc.patterns([[0, 1, 2]]) == [0]
    assert len(set(tc.patterns([[0, 1, 2]] * 40))) == 1          # identical profiles: one pattern, no empty ones


# ------------------------------------------------------------- whole analysis --


def _experiment(tmp_path: Path, series=None, **cfg):
    d = tmp_path / "exp"
    series = series or {"Drug": ["0h", "1h", "4h", "24h"], "DMSO": ["0h", "1h", "4h", "24h"]}
    truth = simulate.time_course_pg_matrix(d / "report.pg_matrix.tsv", series, seed=2)
    out = downstream.analyze(d, analysis_cfg={"enrichment": False, **cfg})
    return d, truth, out, json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))


def test_time_course_end_to_end(tmp_path):
    d, truth, out, s = _experiment(tmp_path)
    t = s["time_course"]
    assert t["ran"] and t["table"] == "results/time_course.tsv" and t["model"] == "~0 + condition"
    drug, dmso = t["series"]
    assert drug["times"] == ["0 h", "1 h", "4 h", "24 h"] and drug["tested"] == 300
    assert drug["differs_from"]["series"] == "DMSO" and drug["differs_from"]["features"] > 25
    assert dmso["up"] + dmso["down"] + dmso["mixed"] <= 2 and "differs_from" not in dmso
    assert sum(drug["patterns"]) == drug["up"] + drug["down"] + drug["mixed"]
    with open(d / "results" / "time_course.tsv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert len(rows) == 600 and list(rows[0]) == tc.COLUMNS
    called = {r["label"]: r for r in rows if r["series"] == "Drug" and r["class"] != "not"}
    assert len(set(called) & set(truth["Drug"])) / len(truth["Drug"]) > 0.7 and len(set(called) - set(truth["Drug"])) <= 2
    for g, r in called.items():   # the class follows the planted shape; a pulse peaks in the middle
        if g in truth["Drug"]:
            assert r["class"] == {"up": "up", "down": "down", "pulse": "up"}[truth["Drug"][g]], (g, r)
            assert (r["peak_time"] in ("1 h", "4 h")) == (truth["Drug"][g] == "pulse"), (g, r)
    html = out.report.read_text(encoding="utf-8")
    assert "<a href='#time' id='navtime'>Time course</a>" in html and "<section id='time'>" in html
    assert "Time courses: Drug (0 h, 1 h, 4 h, 24 h), DMSO (0 h, 1 h, 4 h, 24 h)." in html
    assert "responds differently from DMSO" in html
    data = json.loads(html.split("<script id='ionomos-data' type='application/json'>")[1].split("</script>")[0])
    series = data["time"]["series"][0]
    assert data["time"]["ran"] and len(series["i"]) == 300 and len(series["fc"][0]) == 4
    assert len(series["samples"]) == 12 and series["stime"] == [0.0] * 3 + [1.0] * 3 + [4.0] * 3 + [24.0] * 3
    assert [data["cond"][j] for j in series["samples"][:3]] == ["Drug_0h"] * 3
    assert "time_course.tsv" in data["files"] and not [i for i in out.issues if i.code == "TIMES"]


def test_shared_control_baseline_too_few_points_and_off(tmp_path):
    d = tmp_path / "exp"
    runs = [(f"{c}_{r}.raw", c) for c in ("DMSO", "Drug_1h", "Drug_4h", "Drug_24h") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(d / "report.pg_matrix.tsv", runs, n_proteins=80)
    out = downstream.analyze(d, analysis_cfg={"enrichment": False})
    t = out.summary["time_course"]
    assert t["ran"] and t["series"][0]["conditions"] == ["DMSO", "Drug_1h", "Drug_4h", "Drug_24h"]
    assert any("DMSO (no time in its name) is used as time 0" in n for n in out.warnings)
    off = downstream.analyze(d, analysis_cfg={"enrichment": False, "time_course": False})
    assert off.summary["time_course"] == {"ran": False, "reason": "the time-course tests are switched off "
                                                                  "(analysis.time_course)"}
    welch = downstream.analyze(d, analysis_cfg={"enrichment": False, "test": "welch"})
    assert not welch.summary["time_course"]["ran"] and "limma model" in welch.summary["time_course"]["reason"]
    e2 = tmp_path / "two"
    simulate.dia_pg_matrix(e2 / "report.pg_matrix.tsv", [(f"{c}_{r}.raw", c) for c in ("Drug_0h", "Drug_4h")
                                                          for r in (1, 2, 3)], n_proteins=60)
    two = downstream.analyze(e2, analysis_cfg={"enrichment": False})
    assert not two.summary["time_course"]["ran"] and "needs at least 3" in two.summary["time_course"]["reason"]
    assert "<section id='time'>" in two.report.read_text(encoding="utf-8")       # shown, with the reason
    plain = tmp_path / "plain"
    simulate.dia_pg_matrix(plain / "report.pg_matrix.tsv", [(f"{c}_{r}.raw", c) for c in ("DMSO", "Drug")
                                                             for r in (1, 2, 3)], n_proteins=60)
    none = downstream.analyze(plain, analysis_cfg={"enrichment": False})
    assert "No times were found" in none.summary["time_course"]["reason"]
    assert "<section id='time' hidden>" in none.report.read_text(encoding="utf-8")


def test_unreadable_times_become_an_issue(tmp_path):
    _d, _truth, out, s = _experiment(tmp_path, times={"Drug_0h": 0, "Drug_1h": "1 h", "Drug_4h": "4 h", "Nope": "2 h"})
    issue = next(i for i in out.issues if i.code == "TIMES")
    assert issue.severity == "input" and "'Nope', which is not a condition here" in issue.message
    assert s["time_course"]["ran"] and [x["name"] for x in s["time_course"]["series"]] == ["Drug"]


# ------------------------------------------------------- splines (D77) --


def _ns_gold() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(GOLD / "ns_basis.tsv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            c = out.setdefault(r["case"].strip(), {"basis": {}, "predict": {}, "knot": {}})
            x = None if r["x"].strip() == "NA" else float(r["x"])
            c[r["what"].strip()][(int(r["row"]), int(r["col"]))] = (x, float(r["value"]))
    return out


def test_ns_basis_matches_r():
    """splines.ns against R's splines::ns and predict(): the columns themselves, not only their span."""
    gold = _ns_gold()
    assert len(gold) == 8
    for case, g in gold.items():
        rows = max(r for r, _c in g["basis"])
        df = max(c for _r, c in g["basis"])
        x = [g["basis"][(r, 1)][0] for r in range(1, rows + 1)]
        b = splines.ns(x, df)
        assert b.knots == pytest.approx([g["knot"][(k, 0)][1] for k in range(1, len(g["knot"]) + 1)], abs=1e-14), case
        bad = [(rc, b.basis[rc[0] - 1][rc[1] - 1], v) for rc, (_x, v) in g["basis"].items()
               if abs(b.basis[rc[0] - 1][rc[1] - 1] - v) > 1e-12]
        assert not bad, (case, bad[:3])
        grid = [g["predict"][(r, 1)][0] for r in range(1, max(r for r, _c in g["predict"]) + 1)]
        p = b.predict(grid)
        bad = [(rc, v) for rc, (_x, v) in g["predict"].items() if abs(p[rc[0] - 1][rc[1] - 1] - v) > 1e-12]
        assert not bad, (case, bad[:3])
    with pytest.raises(splines.SplineError):
        splines.ns([1, 1, 1], 2)
    with pytest.raises(splines.SplineError):
        splines.ns([0, 1, 2], 0)


def _sp_matrix(name: str) -> QuantMatrix:
    with open(GOLD / "sp_samples.tsv", encoding="utf-8") as fh:
        sd = list(csv.DictReader(fh, delimiter="\t"))
    with open(GOLD / name, encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    assert rows[0][1:] == [x["sample"] for x in sd]
    vals = [[None if v == "NA" else float(v) for v in r[1:]] for r in rows[1:]]
    return QuantMatrix("intensity", "protein", [Feature(id=r[0], label=r[0]) for r in rows[1:]], rows[0][1:], vals,
                       {x["sample"]: x["condition"] for x in sd}, name,
                       replicate={x["sample"]: int(x["replicate"]) for x in sd})


@pytest.mark.parametrize("matrix, gold, settings", [
    ("sp_matrix.tsv", "sp_df4.tsv", {}),                                   # auto: 8 time points -> a 4-df spline
    ("sp_matrix.tsv", "sp_df4_block.tsv", {"block": "replicate", "time_model": "spline"}),
    ("sp_matrix_missing.tsv", "sp_df3_missing.tsv", {"time_spline_df": 3}),
])
def test_spline_f_fit_and_interaction_match_limma(matrix, gold, settings):
    m = _sp_matrix(matrix)
    p = fpa.Processed(m=m, measured=[list(r) for r in m.values], imputed=[[False] * len(m.samples) for _ in m.values],
                      imputation="given")
    s = Settings(log2fc=1.0, **settings)
    model = analysis.make_model(m, s, [])
    assert not model.problem
    res, plan = tc.run(p, s, None, model)
    assert not plan.spline_problems
    want = _gold(gold)
    df = settings.get("time_spline_df", 4)
    assert [(c.series.name, c.model_label, c.interaction_vs) for c in res.courses] == \
        [("Drug", f"spline ({df} df)", "DMSO"), ("DMSO", f"spline ({df} df)", "")]
    assert res.courses[0].series.times == [0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 24.0, 48.0]
    checked = 0
    for c in res.courses:
        name = c.series.name
        got = {r["id"]: r for r in c.rows}
        tested = [i for i, w in want.items() if not math.isnan(w[f"{name}_P"])]
        assert sorted(got) == sorted(tested) and c.untested == len(want) - len(tested)
        for fid, r in got.items():
            w = want[fid]
            pairs = [(r["F"], w[f"{name}_F"]), (r["pvalue"], w[f"{name}_P"]), (r["qvalue"], w[f"{name}_adjP"]),
                     *[(r["fc"][k], w[f"{name}_fc{k}"]) for k in range(1, 8)]]
            if name == "Drug":
                pairs += [(r["interaction_F"], w["inter_F"]), (r["interaction_pvalue"], w["inter_P"]),
                          (r["interaction_qvalue"], w["inter_adjP"])]
            assert r["model"] == f"spline ({df} df)"
            bad = [(a, b) for a, b in pairs if not _close(a, b)]
            assert not bad, (name, fid, bad)
            checked += len(pairs)
    assert checked > 3000
    planted = {f"F{i}" for i in range(40)}
    called = {r["id"] for r in res.courses[0].rows if r["class"] != "not"}
    assert len(called & planted) >= 25 and len(called - planted) <= 2
    assert not [r for r in res.courses[1].rows if r["class"] != "not"]


def test_spline_df_and_model_choice():
    s = Settings()
    assert [tc.spline_df(n, s)[0] for n in (3, 6, 7, 8, 20)] == [0, 0, 4, 4, 4]      # auto: factor up to 6 points
    sp = Settings(time_model="spline")
    assert [tc.spline_df(n, sp)[0] for n in (3, 4, 5, 6, 7)] == [1, 2, 3, 4, 4]       # at most points - 2
    assert tc.spline_df(12, Settings(time_model="factor"))[0] == 0
    assert tc.spline_df(9, Settings(time_spline_df=6)) == (6, "")
    df, why = tc.spline_df(7, Settings(time_spline_df=6), "Drug: ")
    assert df == 5 and "time_spline_df is 6, too high for 7 time points" in why and "every time point's mean" in why
    df, why = tc.spline_df(7, Settings(time_spline_df=9))
    assert df == 5 and "can't be fitted" in why
    assert settings_from({"time_model": "Splines", "time_spline_df": "auto"}).time_model == "spline"
    assert settings_from({"time_spline_df": 3}).time_spline_df == 3
    for bad in ({"time_model": "curve"}, {"time_spline_df": 0}, {"time_spline_df": 2.5}, {"time_spline_df": "x"},
                {"time_spline_df": True}):
        with pytest.raises(AnalysisError):
            settings_from(bad)


LONG = ["0h", "1h", "2h", "4h", "6h", "8h", "12h", "24h"]


def test_spline_end_to_end_report_and_figures(tmp_path):
    from ionomos.downstream import charts, sectionfigs

    d, truth, out, s = _experiment(tmp_path, series={"Drug": LONG, "DMSO": LONG})
    t = s["time_course"]
    drug, dmso = t["series"]
    assert drug["time_model"] == dmso["time_model"] == "spline" and drug["spline"]["df"] == 4
    assert drug["spline"]["boundary_h"] == [0.0, 24.0] and len(drug["spline"]["knots_h"]) == 3
    assert drug["differs_from"]["series"] == "DMSO" and drug["differs_from"]["features"] > 20
    with open(d / "results" / "time_course.tsv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert list(rows[0]) == tc.COLUMNS and {r["model"] for r in rows} == {"spline (4 df)"}
    called = {r["label"] for r in rows if r["series"] == "Drug" and r["class"] != "not"}
    assert len(called & set(truth["Drug"])) / len(truth["Drug"]) > 0.7 and len(called - set(truth["Drug"])) <= 3
    html = out.report.read_text(encoding="utf-8")
    assert "natural cubic spline" in html
    data = json.loads(html.split("<script id='ionomos-data' type='application/json'>")[1].split("</script>")[0])
    S = data["time"]["series"][0]
    assert S["model"] == "spline" and S["df"] == 4 and len(S["grid"]) == tc.GRID_POINTS and len(S["gb"][0]) == 4
    assert S["grid"][0] == 0 and S["grid"][-1] == 24 and len(S["cf"]) == len(S["lv"]) == len(S["i"])
    # the curve runs through the replicates, and its change from the start at each time point is the table's fc
    curve = tc.curve_at(S, 0)
    first = [data["v"][S["i"][0]][j] for j in S["samples"][:3]]
    assert abs(curve[0] - sum(first) / 3) < 1.0
    for a, hours in enumerate(S["times"]):
        if hours in S["grid"]:
            assert curve[S["grid"].index(hours)] - curve[0] == pytest.approx(S["fc"][0][a], abs=2e-3)
    svg = sectionfigs.figure_time_profiles(data, 0, [0, 1], charts.style_from())
    assert "fitted spline (4 df)" in svg
    assert not [i for i in out.issues if i.code in ("TIMES", "TIME_SPLINE")]


def test_spline_df_too_high_is_an_issue(tmp_path):
    _d, _truth, out, s = _experiment(tmp_path, time_model="spline", time_spline_df=5)
    issue = next(i for i in out.issues if i.code == "TIME_SPLINE")
    assert issue.severity == "warning" and "too high for 4 time points" in issue.message
    drug = s["time_course"]["series"][0]
    assert drug["time_model"] == "spline" and drug["spline"]["df"] == 2
    _d, _t, plain, s2 = _experiment(tmp_path / "auto")                  # 4 time points, auto: as before
    assert {x["time_model"] for x in s2["time_course"]["series"]} == {"factor"}
    assert not [i for i in plain.issues if i.code == "TIME_SPLINE"]
