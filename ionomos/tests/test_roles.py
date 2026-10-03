"""The roles of the conditions (downstream/roles.py, D61): control, compound, competition; the comparisons that
follow from them; the specific-targets call; and unequal groups (DMSO n=2 against n=4) handled knowingly, on
simulated experiments with known truth (simulate.competition_pg_matrix / competition_tmt).

Not tested on a real lab experiment: the competition keywords and the specific-targets rule are unconfirmed."""
from __future__ import annotations

import csv
import json
import math
import random
import re
from pathlib import Path

import pytest
import yaml

from ionomos import configio, downstream
from ionomos.downstream import analysis, fpa, insights, roles, simulate, stats
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from
from ionomos.downstream.quant import Feature, QuantMatrix

CFG = {"enrichment": False}
UNEQUAL = {"DMSO": 2, "Probe": 4, "Probe_Comp": 4}


def _matrix(sizes: dict[str, int], kind: str = "intensity") -> QuantMatrix:
    samples = [f"{c}_{r}" for c, n in sizes.items() for r in range(1, n + 1)]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    return QuantMatrix(kind, "protein", [Feature("P1", "G1")], samples, [[20.0] * len(samples)], cond)


def _json(out) -> dict:
    return json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))


def _rows(out, name: str) -> list[dict]:
    with open(out.results_dir / name, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _payload(out) -> dict:
    html = out.report.read_text(encoding="utf-8")
    return json.loads(re.search(r"<script id='ionomos-data' type='application/json'>(.*?)</script>", html, re.S).group(1))


def _dia(dest: Path, sizes=None, seed: int = 3, **kw):
    return simulate.competition_pg_matrix(dest / "fragpipe" / "diann-output" / "report.pg_matrix.tsv", sizes,
                                          seed=seed, **kw)


def _tmt(dest: Path, sizes=None, seed: int = 3, **kw):
    return simulate.competition_tmt(dest / "fragpipe" / "tmt-report" / "abundance_gene_MD.tsv", sizes, seed=seed, **kw)


# -------------------------------------------------------------------- roles --

# condition names as the lab writes them: KL6283A ... Comp ... KL6159A is a folder on the lab PC
# (reference/pc-inventory/2026-09-15: D:\Aman\012626_from Cdrive\KL6283A_TMTPD_Comp_KL6159A); the other folders there
# give DMSO / MA25 / Ola, DMSO / FPS / DWI, DMSO / DBG / EGM and wt / KD
ROLE_CASES = [
    (["DMSO", "Probe", "Probe_Comp"], {"DMSO": "control", "Probe": "compound", "Probe_Comp": "competition of Probe"}),
    (["DMSO", "KL6283A", "KL6283A_Comp_KL6159A"],
     {"DMSO": "control", "KL6283A": "compound", "KL6283A_Comp_KL6159A": "competition of KL6283A"}),
    (["DMSO", "Probe", "Probe+Comp"], {"Probe+Comp": "competition of Probe"}),
    (["DMSO", "Probe", "ProbeComp"], {"ProbeComp": "competition of Probe"}),          # one word, as TMT names need
    (["Vehicle", "Cmpd", "Cmpd_comp"], {"Vehicle": "control", "Cmpd_comp": "competition of Cmpd"}),
    (["DMSO", "Probe", "Comp"], {"Comp": "competition of Probe"}),                    # the only compound
    (["DMSO", "Probe", "Probe_Competition"], {"Probe_Competition": "competition of Probe"}),
    (["DMSO", "Probe", "Probe_competitor"], {"Probe_competitor": "competition of Probe"}),
    (["DMSO", "Probe", "Probe_excess"], {"Probe_excess": "competition of Probe"}),
    (["DMSO", "A", "A_Comp", "B", "B_Comp"], {"A_Comp": "competition of A", "B_Comp": "competition of B"}),
    (["WT_DMSO", "WT_Probe", "WT_Probe_Comp"], {"WT_DMSO": "control", "WT_Probe": "compound",
                                                "WT_Probe_Comp": "competition of WT_Probe"}),
    (["DMSO", "Probe", "Probe_Comp", "Pool", "HeLa", "Mock"],
     {"Pool": "reference", "HeLa": "qc", "Mock": "control", "DMSO": "control"}),
    # no competition: nothing but a control and compounds (real names from the inventory)
    (["DMSO", "MA25", "Ola"], {"DMSO": "control", "MA25": "compound", "Ola": "compound"}),
    (["DMSO", "FPS", "DWI"], {"DMSO": "control", "FPS": "compound", "DWI": "compound"}),
    (["DMSO", "DBG", "EGM"], {"DBG": "compound", "EGM": "compound"}),
    (["wt", "KD"], {"wt": "control", "KD": "compound"}),
    (["DMSO", "Compound", "Complete", "Compd"], {"Compound": "compound", "Complete": "compound", "Compd": "compound"}),
    (["Pre", "Post", "DMSO"], {"Pre": "compound", "Post": "compound"}),               # a weak word with nothing to hang on
]


@pytest.mark.parametrize("conds, want", ROLE_CASES)
def test_roles_from_names(conds, want):
    plan = roles.infer(conds, Settings())
    got = {c: r.text() for c, r in plan.roles.items()}
    assert {c: got[c] for c in want} == want
    assert plan.questions == []


def test_tokens_split_words_and_keep_keywords_whole():
    assert roles.tokens("Probe_Comp") == roles.tokens("Probe+Comp") == roles.tokens("ProbeComp") == ["probe", "comp"]
    assert roles.tokens("KL6283A_Comp_KL6159A") == ["kl6283a", "comp", "kl6159a"]
    assert roles.tokens("HeLa", {"hela"}) == ["hela"] and roles.tokens("siNT_Probe", {"sint"}) == ["sint", "probe"]
    assert roles.tokens("DMSO") == ["dmso"] and roles.tokens("Probe (10 uM) | Comp") == ["probe", "10", "u", "m", "comp"]


def test_weak_words_need_the_compounds_own_condition_and_are_asked_about():
    for name in ("Probe_pre", "Probe_block", "Probe_10x", "Probe_cold"):
        plan = roles.infer(["DMSO", "Probe", name], Settings())
        assert plan.roles[name].text() == "competition of Probe" and plan.roles[name].sure is False
        assert len(plan.questions) == 1 and name in plan.questions[0] and "compound" in plan.questions[0]
    # without the compound's own condition they are ordinary conditions (a dose, pre / post)
    for conds in (["DMSO", "Drug_10x"], ["DMSO", "A", "B", "C_10x"], ["Vehicle", "Block", "Drug"]):
        plan = roles.infer(conds, Settings())
        assert plan.competitions == [] and plan.questions == []


def test_a_competition_that_cannot_be_linked_is_a_question():
    plan = roles.infer(["DMSO", "A", "B", "Comp"], Settings())
    assert plan.roles["Comp"].role == "competition" and plan.roles["Comp"].of == "" and not plan.roles["Comp"].sure
    assert "which compound it competes" in plan.questions[0] and "competition of A" in plan.questions[0]


def test_roles_setting_wins_and_is_validated():
    s = settings_from({"roles": {"Mock": "vehicle", "P": "probe", "PX": "competition of P", "DMSO": "compound"}})
    assert s.roles == {"Mock": "control", "P": "compound", "PX": "competition of P", "DMSO": "compound"}
    conds = ["DMSO", "Mock", "P", "PX"]
    assert analysis.find_control(conds, s) == "Mock"          # roles name the control; DMSO was said not to be one
    plan = roles.infer(conds, s)
    assert {c: r.text() for c, r in plan.roles.items()} == s.roles
    assert plan.roles["PX"].source == "analysis.roles" and plan.control == "Mock"
    # other ways to write it; an experiment's roles add to the lab's
    assert settings_from({"roles": {"a": {"role": "competition", "of": "b"}, "c": "Competition (b)", "d": "comp: b"}}
                         ).roles == {"a": "competition of b", "c": "competition of b", "d": "competition of b"}
    assert settings_from({"roles": {"X": "control"}}, {"roles": {"Y": "compound"}}).roles == {"X": "control",
                                                                                            "Y": "compound"}
    for bad in ({"roles": {"X": "bystander"}}, {"roles": "control"}, {"roles": {"X": "control of Y"}}):
        with pytest.raises(AnalysisError, match="roles"):
            settings_from(bad)
    # a name that is not a condition is said, not fatal; a competition of something that is not a compound is asked
    plan = roles.infer(["DMSO", "P"], settings_from({"roles": {"Q": "compound"}}))
    assert "analysis.roles names 'Q'" in plan.notes[0]
    plan = roles.infer(["DMSO", "P", "PX"], settings_from({"roles": {"PX": "competition of Nothing"}}))
    assert "not a compound condition here" in plan.questions[0]


def test_keyword_lists_are_settings():
    s = settings_from({"competition_keywords": "plusinhibitor, chase", "competition_keywords_weak": []})
    assert s.competition_keywords == ("plusinhibitor", "chase") and s.competition_keywords_weak == ()
    plan = roles.infer(["DMSO", "Probe", "Probe_chase", "Probe_Comp", "Probe_pre"], s)
    assert plan.roles["Probe_chase"].text() == "competition of Probe"
    assert plan.roles["Probe_Comp"].role == plan.roles["Probe_pre"].role == "compound"
    assert analysis.as_dict(s)["competition_keywords"] == ["plusinhibitor", "chase"]
    for bad in ({"small_group_min_valid": "third"}, ):
        with pytest.raises(AnalysisError):
            settings_from(bad)


def test_config_writer_round_trips_the_role_settings(tmp_path):
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    assert d["analysis"]["competition_keywords"] == list(roles.DEFAULT_COMPETITION_KEYWORDS)
    d["analysis"].update({"competition_keywords": ["comp", "chase"], "competition_keywords_weak": ["pre"],
                          "role_comparisons": False, "small_group_min_valid": "same",
                          "roles": {"DMSO": "control", "Probe_Comp": "competition of Probe"}})
    text = configio.dump_config(d)
    back = yaml.safe_load(text)["analysis"]
    for k in ("competition_keywords", "competition_keywords_weak", "role_comparisons", "small_group_min_valid", "roles"):
        assert back[k] == d["analysis"][k], k
    s = settings_from({k: v for k, v in back.items() if k != "enabled"})
    assert s.roles == d["analysis"]["roles"] and s.role_comparisons is False and s.small_group_min_valid == "same"
    assert yaml.safe_load(configio.dump_config(configio.defaults()))["analysis"].get("roles") is None


# -------------------------------------------------------------- comparisons --


def _before_d61(conds: list[str], s: Settings) -> list[tuple[str, str]]:
    """The default comparisons up to 0.13 (de_type: control): every condition against the control."""
    ctrl = analysis.find_control(conds, s) or sorted(conds)[0]
    return [(c, ctrl) for c in conds if c != ctrl]


@pytest.mark.parametrize("conds", [c for c, _w in ROLE_CASES if not any("comp" in roles.tokens(x) or "excess" in
                                                                        roles.tokens(x) or "competition" in
                                                                        roles.tokens(x) or "competitor" in
                                                                        roles.tokens(x) for x in c)]
                         + [["A", "B", "C"], ["Drug_1h", "Drug_4h", "DMSO_1h"], ["Cmpd_10nM", "Cmpd_100nM", "DMSO"]])
def test_without_a_competition_the_comparisons_are_as_before(conds):
    m = _matrix({c: 3 for c in conds})
    comps, _notes = analysis.choose_comparisons(m, Settings())
    assert comps == _before_d61(conds, Settings())
    assert roles.plan(m, Settings()).active is False


def test_comparisons_follow_the_design():
    m = _matrix(UNEQUAL)
    comps, notes = analysis.choose_comparisons(m, Settings())
    assert comps == [("Probe", "DMSO"), ("Probe_Comp", "Probe"), ("Probe_Comp", "DMSO")]
    assert "competition experiment: DMSO: control (2 samples); Probe: compound (4); Probe_Comp: competition of " \
           "Probe (4)" in notes[0] and "analysis.roles" in notes[0]
    plan = roles.plan(m, Settings())
    assert [plan.kinds[c] for c in comps] == ["enrichment", "competition", "remaining"]
    assert plan.triples == [("Probe", "Probe_Comp", "DMSO")]
    # two compounds, each with its competition: never the competition of A against compound B
    m2 = _matrix({c: 3 for c in ("DMSO", "A", "A_Comp", "B", "B_Comp")})
    comps2, _ = analysis.choose_comparisons(m2, Settings())
    assert comps2 == [("A", "DMSO"), ("A_Comp", "A"), ("A_Comp", "DMSO"), ("B", "DMSO"), ("B_Comp", "B"),
                      ("B_Comp", "DMSO")]
    # a second control, a pool and a QC standard are not compared by default, and the notes say so
    m3 = _matrix({c: 3 for c in ("DMSO", "Mock", "Probe", "Probe_Comp", "Pool", "HeLa")})
    comps3, notes3 = analysis.choose_comparisons(m3, Settings())
    assert comps3 == comps
    text = " ".join(notes3)
    assert "Mock vs DMSO (both are controls)" in text and "Pool (a pooled reference" in text and "HeLa (a QC" in text
    # probe and probe + competitor only: no control is needed, and none is guessed
    m4 = _matrix({"Probe": 3, "Probe_Comp": 3})
    assert analysis.choose_comparisons(m4, Settings())[0] == [("Probe_Comp", "Probe")]
    # a competition that can't be linked is compared with the control only
    m5 = _matrix({c: 3 for c in ("DMSO", "A", "B", "Comp")})
    assert analysis.choose_comparisons(m5, Settings())[0] == [("A", "DMSO"), ("B", "DMSO"), ("Comp", "DMSO")]


def test_explicit_settings_win_over_the_roles():
    m = _matrix(UNEQUAL)
    conds = m.conditions
    s = settings_from({"comparisons": ["Probe_Comp vs DMSO"]})
    assert analysis.choose_comparisons(m, s)[0] == [("Probe_Comp", "DMSO")]
    assert roles.plan(m, s).active is False and "comparisons" in roles.plan(m, s).inactive
    assert analysis.choose_comparisons(m, settings_from({"de_type": "all"}))[0] == fpa.all_pairs(conds, "DMSO")
    assert analysis.choose_comparisons(m, settings_from({"de_type": "others"}))[0] == [(c, "others") for c in conds]
    off = settings_from({"role_comparisons": False})
    assert analysis.choose_comparisons(m, off)[0] == _before_d61(conds, off)
    assert roles.plan(m, off).roles["Probe_Comp"].role == "competition"       # still shown
    # control: names the control (the Analysis tab always writes it); the design still gives the comparisons
    c = settings_from({"control": "DMSO"})
    assert analysis.choose_comparisons(m, c)[0] == analysis.choose_comparisons(m, Settings())[0]
    # ... and every condition is still compared with a control that was set, as before: nothing is left out
    m6 = _matrix({k: 3 for k in ("DMSO", "Mock", "Probe", "Probe_Comp", "Pool")})
    got = analysis.choose_comparisons(m6, c)[0]
    assert got[:3] == analysis.choose_comparisons(m, Settings())[0] and set(got) >= set(_before_d61(m6.conditions, c))
    assert set(got) - set(_before_d61(m6.conditions, c)) == {("Probe_Comp", "Probe")}
    odd = settings_from({"control": "Probe"})
    assert set(analysis.choose_comparisons(m, odd)[0]) == set(_before_d61(conds, odd))
    with pytest.raises(AnalysisError, match="not one of the conditions"):
        analysis.choose_comparisons(m, settings_from({"control": "Nope"}))
    # roles set by hand
    r = settings_from({"roles": {"Probe_Comp": "compound"}})
    assert analysis.choose_comparisons(m, r)[0] == _before_d61(conds, r)
    ratio = _matrix({"CmpdA": 3, "CmpdB": 3}, "ratio")
    assert analysis.choose_comparisons(ratio, Settings())[0] == [("CmpdA", None), ("CmpdB", None)]


def test_site_ratio_data_is_a_competition_by_construction():
    plan = roles.plan(_matrix({"CmpdA": 3, "CmpdB": 3}, "ratio"), Settings())
    assert plan.by_construction and not plan.active and plan.questions == []
    assert {r.role for r in plan.roles.values()} == {"competition"} and plan.roles["CmpdA"].of == "the probe"
    assert plan.as_dict()["by_construction"] is True


# ---------------------------------------------------------- the whole thing --


def test_competition_experiment_end_to_end(tmp_path):
    dest = tmp_path / "exp"
    truth = _dia(dest)
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG)
    s = _json(out)
    assert s["state"] == "ok"
    assert [(c["name"], c["kind"], c["samples"]) for c in s["comparisons"]] == [
        ("Probe vs DMSO", "enrichment", {"treatment": 4, "control": 2}),
        ("Probe_Comp vs Probe", "competition", {"treatment": 4, "control": 4}),
        ("Probe_Comp vs DMSO", "remaining", {"treatment": 4, "control": 2})]
    assert all(c["confidence"] == "normal" for c in s["comparisons"])            # two controls are not "low confidence"
    r = s["roles"]
    assert r["comparisons_follow_roles"] and r["control"] == "DMSO"
    assert r["conditions"]["Probe_Comp"] == {"role": "competition", "source": "name (comp)", "samples": 4,
                                             "of": "Probe", "linked_by": "name"}
    assert r["conditions"]["DMSO"]["samples"] == 2
    # the doctor says what was read, as a note
    codes = {i["code"]: i for i in s["issues"]}
    note = codes["COMPETITION_DESIGN"]
    assert note["severity"] == "warning" and "Probe_Comp: competition of Probe (4)" in note["message"]
    assert "Probe_Comp vs Probe (what the competitor takes off)" in note["message"]
    assert any("roles:" in f for f in note["fixes"]) and any("role_comparisons: false" in f for f in note["fixes"])
    assert "ROLES_UNSURE" not in codes and "NO_CONTROL" not in codes and "LOW_CONFIDENCE" not in codes
    # specific targets: enriched and competed off; the unspecific binders are enriched and stay
    sp = s["specific_targets"][0]
    assert (sp["compound"], sp["competition"], sp["control"]) == ("Probe", "Probe_Comp", "DMSO")
    rows = _rows(out, "specific_targets.tsv")
    assert list(rows[0]) == roles.SPECIFIC_COLUMNS
    called = {x["label"] for x in rows if x["call"] == "specific"}
    stay = {x["label"] for x in rows if x["call"] == "enriched, not competed"}
    assert called <= truth["specific"] and len(called) >= 0.8 * len(truth["specific"])
    assert stay >= truth["unspecific"] - {"STICKY9"} or len(stay & truth["unspecific"]) >= 18
    assert not called & truth["unspecific"]
    assert sp["specific"] == len(called) and sp["enriched_not_competed"] == len(stay) and sp["tested"] == len(rows)
    one = next(x for x in rows if x["call"] == "specific")
    assert float(one["enrichment_log2fc"]) >= 1 and float(one["enrichment_padj"]) <= 0.05
    assert float(one["competition_log2fc"]) <= -1 and float(one["competition_padj"]) <= 0.05
    assert 50 <= float(one["competed_pct"]) <= 100 and (one["n_control"], one["n_compound"]) == ("2", "4")
    # the report: the section, the roles, the samples on each side, the rule
    html = out.report.read_text(encoding="utf-8")
    assert "<section id='specific'><h2>Specific targets</h2>" in html and "id='navspecific'>Specific targets" in html
    assert "Each condition was given a role (DMSO: control (2 samples); Probe: compound (4); Probe_Comp: competition " \
           "of Probe (4))" in html
    assert "Samples in each comparison: Probe vs DMSO, 4 against 2; Probe_Comp vs Probe, 4 against 4" in html
    assert "all 10 samples of the 3 conditions (7 residual degrees of freedom" in html
    assert "called a specific target of Probe when it was enriched against DMSO (log2FC ≥ 1, adjusted p ≤ 0.05" in html
    data = _payload(out)
    assert data["roles"]["specific"] == [{"compound": "Probe", "competition": "Probe_Comp", "control": "DMSO", "e": 0,
                                          "k": 1, "r": 2, "conf": "", "note": ""}]
    assert [c["size"] for c in data["comps"]] == [[4, 2], [4, 4], [4, 2]]
    assert [c["kind"] for c in data["comps"]] == ["enrichment", "competition", "remaining"]


def test_explicit_comparisons_and_switch_leave_the_analysis_as_before(tmp_path):
    dest = tmp_path / "exp"
    _dia(dest)
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG, overrides={"role_comparisons": False})
    s = _json(out)
    assert [c["name"] for c in s["comparisons"]] == ["Probe vs DMSO", "Probe_Comp vs DMSO"]
    assert s["specific_targets"] == [] and "COMPETITION_DESIGN" not in {i["code"] for i in s["issues"]}
    assert s["roles"]["comparisons_follow_roles"] is False and s["roles"]["why_not"] == "role_comparisons: false"
    assert "<section id='specific' hidden>" in out.report.read_text(encoding="utf-8")
    by_role = {r["id"]: r for r in _rows(downstream.analyze(dest, "DIA", analysis_cfg=CFG), "Probe_vs_DMSO_differential.tsv")}
    plain = _rows(downstream.analyze(dest, "DIA", analysis_cfg=CFG, overrides={"comparisons": ["Probe vs DMSO"]}),
                  "Probe_vs_DMSO_differential.tsv")
    assert all(by_role[r["id"]] == r for r in plain)   # the same numbers whichever way the comparison was chosen


def test_no_competition_means_no_new_issue_and_no_section(tmp_path):
    dest = tmp_path / "exp"
    simulate.dia_pg_matrix(dest / "fragpipe" / "diann-output" / "report.pg_matrix.tsv",
                           [(f"C:\\raw\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)], seed=6,
                           n_proteins=120)
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG)
    s = _json(out)
    assert [c["name"] for c in s["comparisons"]] == ["Drug vs DMSO"] and "kind" not in s["comparisons"][0]
    assert not {"COMPETITION_DESIGN", "ROLES_UNSURE"} & {i["code"] for i in s["issues"]}
    assert s["roles"]["conditions"] == {"DMSO": {"role": "control", "source": "name (control_keywords)", "samples": 3},
                                        "Drug": {"role": "compound", "source": "default (not a control)", "samples": 3}}
    assert s["specific_targets"] == [] and not (out.results_dir / "specific_targets.tsv").exists()
    html = out.report.read_text(encoding="utf-8")
    assert "<section id='specific' hidden>" in html and "Each condition was given a role" not in html
    assert "DMSO: control (3 samples); Drug: compound (3)" in html            # Methods, Settings used: Roles


def test_an_unsure_role_asks(tmp_path):
    dest = tmp_path / "exp"
    _dia(dest, {"DMSO": 3, "Probe": 3, "Probe_10x": 3}, competition="Probe_10x", n=200)
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG)
    s = _json(out)
    ask = next(i for i in s["issues"] if i["code"] == "ROLES_UNSURE")
    assert ask["severity"] == "input" and s["state"] == "needs_input"
    assert "Probe_10x was read as Probe plus a competitor because of '10x'" in ask["message"]
    assert [c["name"] for c in s["comparisons"]][1] == "Probe_10x vs Probe"     # the guess is used meanwhile
    # the answer settles it, either way
    yes = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG, overrides={"roles": {"Probe_10x": "competition of Probe"}}))
    assert "ROLES_UNSURE" not in {i["code"] for i in yes["issues"]} and len(yes["comparisons"]) == 3
    no = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG, overrides={"roles": {"Probe_10x": "compound"}}))
    assert [c["name"] for c in no["comparisons"]] == ["Probe vs DMSO", "Probe_10x vs DMSO"]
    assert not {"ROLES_UNSURE", "COMPETITION_DESIGN"} & {i["code"] for i in no["issues"]}


def test_probe_and_competition_without_a_control(tmp_path):
    dest = tmp_path / "exp"
    _dia(dest, {"Probe": 3, "Probe_Comp": 3}, n=200)
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    assert [c["name"] for c in s["comparisons"]] == ["Probe_Comp vs Probe"]
    assert "NO_CONTROL" not in {i["code"] for i in s["issues"]} and s["specific_targets"] == []


def test_sdrf_role_column_sets_the_roles(tmp_path):
    dest = tmp_path / "exp"
    sizes = {"V": 2, "P": 3, "PX": 3}   # names that say nothing
    _dia(dest, sizes, control="V", compound="P", competition="PX", n=200)
    head = ["source name", "characteristics[biological replicate]", "characteristics[role]", "assay name",
            "comment[data file]", "comment[label]", "factor value[compound]"]
    role = {"V": "control", "P": "compound", "PX": "competition of P"}
    lines = [[f"{c} {r}", r, role[c], f"run {c}{r}", f"{c}_{r}.raw", "AC=MS:1002038;NT=label free sample", c]
             for c, n in sizes.items() for r in range(1, n + 1)]
    (dest / "design.sdrf.tsv").write_text("\t".join(head) + "\n" + "\n".join("\t".join(map(str, x)) for x in lines)
                                          + "\n", encoding="utf-8")
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    assert [c["name"] for c in s["comparisons"]] == ["P vs V", "PX vs P", "PX vs V"]
    assert s["roles"]["conditions"]["PX"]["source"] == "SDRF design.sdrf.tsv" and s["roles"]["control"] == "V"
    assert any("roles from the SDRF design.sdrf.tsv: V = control, P = compound, PX = competition of P" in n
               for n in s["notes"])
    # analysis.roles wins over the SDRF
    s2 = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG, overrides={"roles": {"PX": "compound"}}))
    assert [c["name"] for c in s2["comparisons"]] == ["P vs V", "PX vs V"]


def test_isodtb_roles_and_wording(tmp_path):
    dest = tmp_path / "exp"
    simulate.isodtb_label_quant(dest / "fragpipe" / "combined_modified_peptide_label_quant.tsv",
                                {"CmpdA": [1, 2, 3]}, seed=2, n_sites=120)
    out = downstream.analyze(dest, "isoDTB", analysis_cfg=CFG)
    s = _json(out)
    assert s["roles"]["by_construction"] is True
    assert s["roles"]["conditions"]["CmpdA"]["role"] == "competition" and s["roles"]["conditions"]["CmpdA"]["of"] == "the probe"
    assert not {"COMPETITION_DESIGN", "ROLES_UNSURE"} & {i["code"] for i in s["issues"]}
    assert s["cysteines"]["ran"] and s["specific_targets"] == []
    html = out.report.read_text(encoding="utf-8")
    assert "competition experiment by construction: each condition is a compound competing with the probe" in html


# ----------------------------------------------------------- unequal groups --


def test_the_filter_does_not_drop_features_because_the_control_is_small(tmp_path):
    """filter_condition_pct asks for values in one condition, whichever: a feature missing in both DMSO samples
    but measured in the compound stays, and so does every comparison."""
    samples = ["DMSO_1", "DMSO_2"] + [f"Probe_{k}" for k in range(1, 5)] + [f"Probe_Comp_{k}" for k in range(1, 5)]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    rows = {"all": [20.0] * 10, "no_dmso": [None, None] + [20.0] * 8, "half_dmso": [20.0, None] + [20.0] * 8,
            "one_dmso_only": [20.0, None] + [None] * 8, "one_probe_only": [None, None, 20.0] + [None] * 7,
            "two_probe_only": [None, None, 20.0, 20.0] + [None] * 6}
    m = QuantMatrix("intensity", "protein", [Feature(k, k) for k in rows], samples, list(rows.values()), cond)
    kept, removed = fpa.filter_missing(m, 0, 50)
    assert [f.id for f in kept.features] == ["all", "no_dmso", "half_dmso", "one_dmso_only", "two_probe_only"]
    assert removed == 1   # 1 of 4 is under 50 %; 1 of 2 is 50 %: the percentage means the same in a small group
    dest = tmp_path / "exp"
    _dia(dest)
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    assert all(c["tested"] == s["features"] for c in s["comparisons"]) and len(s["comparisons"]) == 3


def test_group_needs():
    m = _matrix({"DMSO": 2, "Probe": 4, "Probe_Comp": 4, "Three": 3})
    pairs = [("Probe", "DMSO"), ("Probe_Comp", "Probe"), ("DMSO", "Probe"), ("Probe", "Three")]
    assert analysis.group_needs(m, pairs, Settings(), 2) == {("Probe", "DMSO"): (2, 1), ("Probe_Comp", "Probe"): (2, 2),
                                                           ("DMSO", "Probe"): (1, 2), ("Probe", "Three"): (2, 2)}
    same = settings_from({"small_group_min_valid": "same"})
    assert set(analysis.group_needs(m, pairs, same, 2).values()) == {(2, 2)}
    assert set(analysis.group_needs(m, pairs, Settings(), 0).values()) == {(0, 0)}       # imputed: nothing is asked
    assert analysis.group_needs(m, [("Probe", "Three")], settings_from({"min_valid": 3}), 3) == {("Probe", "Three"): (3, 2)}


def test_min_valid_does_not_drop_features_because_the_control_is_small(tmp_path):
    """TMT is not imputed: with DMSO n=2 one missing DMSO value used to leave a feature untested against DMSO.
    Numbers behind D61: 20 seeds x 1,000 genes, 3 % missing: 6.1 % untested with `same`, 0.1 % with `half`."""
    tested, hits, relaxed = {}, {}, {}
    for rule in ("same", "half"):
        dest = tmp_path / rule
        truth = _tmt(dest, n=600)
        out = downstream.analyze(dest, "TMT", analysis_cfg={**CFG, "small_group_min_valid": rule})
        s = _json(out)
        rows = _rows(out, "Probe_vs_DMSO_differential.tsv")
        tested[rule] = [r for r in rows if r["pvalue"] not in ("", "NA")]
        hits[rule] = {r["label"] for r in tested[rule] if r["significant"]}
        relaxed[rule] = s["comparisons"][0].get("tested_below_min_valid", 0)
        both = _rows(out, "ProbeComp_vs_Probe_differential.tsv")          # equal groups: untouched by the rule
        tested[rule + "/equal"] = sum(1 for r in both if r["pvalue"] not in ("", "NA"))
        real = truth["specific"] | truth["unspecific"]
        assert hits[rule] <= real
        if rule == "half":
            assert any(f"Probe vs DMSO: {relaxed[rule]:,} features were tested with fewer than 2 measured values in "
                       "DMSO (2 samples against 4)" in n for n in s["notes"])
            assert "the smaller group of an unequal comparison needed half of its samples" in out.report.read_text(
                encoding="utf-8")
    assert all(int(r["n_control"]) == 2 for r in tested["same"])
    one = [r for r in tested["half"] if int(r["n_control"]) == 1]
    assert len(one) == relaxed["half"] == len(tested["half"]) - len(tested["same"]) > 20
    assert relaxed["same"] == 0 and tested["same/equal"] == tested["half/equal"]
    assert len(tested["half"]) >= 0.99 * 600 > 0.96 * 600 > len(tested["same"])
    assert hits["same"] <= hits["half"] and len(hits["half"]) == 40
    # the features tested on one control value are calibrated: no excess of small p among the unchanged ones
    null = [float(r["pvalue"]) for r in one if r["label"] not in real]
    assert len(null) > 20 and sum(p < 0.05 for p in null) <= max(3, 0.15 * len(null))
    assert all(int(r["n_treatment"]) >= 2 for r in one)


def test_equal_groups_are_not_touched_by_the_small_group_rule(tmp_path):
    out = {}
    for rule in ("same", "half"):
        dest = tmp_path / rule
        _tmt(dest, {"DMSO": 3, "Probe": 3, "ProbeComp": 3}, n=300)
        res = downstream.analyze(dest, "TMT", analysis_cfg={**CFG, "small_group_min_valid": rule})
        out[rule] = [_rows(res, f) for f in ("Probe_vs_DMSO_differential.tsv", "ProbeComp_vs_Probe_differential.tsv")]
    assert out["same"] == out["half"]


def test_limma_pools_the_variance_over_every_group_with_unequal_sizes():
    """~0 + condition on 2 / 4 / 4 samples: the residual variance has 1 + 3 + 3 = 7 df whichever two groups are
    compared, and the standard error is s * sqrt(1/n1 + 1/n2). Checked against the formula here; against R's
    limma itself, with missing values and the small-group rule, in test_unequal_groups_match_r_limma."""
    rng = random.Random(5)
    sizes = {"D": 2, "P": 4, "K": 4}
    samples = [f"{c}{r}" for c, n in sizes.items() for r in range(n)]
    cond = {s: s[0] for s in samples}
    values = []
    for _ in range(300):
        sd = math.exp(rng.gauss(-1.2, 0.5))
        values.append([20 + rng.gauss(0, sd) for _ in samples])
    res = {(r.treatment, r.control): r for r in fpa.limma_contrasts(values, samples, cond, [("P", "D"), ("K", "P")])}
    idx = {c: [j for j, s in enumerate(samples) if cond[s] == c] for c in sizes}
    for (a, b), r in res.items():
        d0, s0 = r.prior
        assert math.isfinite(d0) and d0 > 0
        for i in (0, 7, 150, 299):
            row = values[i]
            mean = {c: sum(row[j] for j in ix) / len(ix) for c, ix in idx.items()}
            rss = sum((row[j] - mean[c]) ** 2 for c, ix in idx.items() for j in ix)
            post = (7 * (rss / 7) + d0 * s0) / (7 + d0)
            t = (mean[a] - mean[b]) / math.sqrt(post * (1 / sizes[a] + 1 / sizes[b]))
            assert r.t[i] == pytest.approx(t, rel=1e-9)
            assert r.p[i] == pytest.approx(stats.t_two_sided_p(t, 7 + d0), rel=1e-9)
            half = stats.qt_upper(0.05, 7 + d0) * math.sqrt(post * (1 / sizes[a] + 1 / sizes[b]))
            assert r.ci_high[i] - r.diff[i] == pytest.approx(half, rel=1e-9)
    # the interval of the 4-against-2 comparison is sqrt(0.75 / 0.5) wider than the 4-against-4 one, feature by feature
    wide, narrow = res[("P", "D")], res[("K", "P")]
    for i in (3, 42):
        assert (wide.ci_high[i] - wide.diff[i]) / (narrow.ci_high[i] - narrow.diff[i]) == pytest.approx(math.sqrt(1.5))


# ------------------------------------------------- unequal groups against R's limma (D66) --

GOLD_UNEQUAL = Path(__file__).parent / "golden" / "unequal"


def _gold_matrix(name: str) -> tuple[list[str], dict[str, list[float | None]]]:
    with open(GOLD_UNEQUAL / name, encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    return rows[0][1:], {r[0].strip(): [None if v.strip() == "NA" else float(v) for v in r[1:]] for r in rows[1:]}


def _gold_limma(name: str) -> dict[tuple[str, str], dict[str, float]]:
    with open(GOLD_UNEQUAL / name, encoding="utf-8") as fh:
        return {(r["comparison"].strip(), r["ID"].strip()): {k: math.nan if v.strip() == "NA" else float(v)
                                                             for k, v in r.items() if k not in ("comparison", "ID")}
                for r in csv.DictReader(fh, delimiter="\t")}


def _unequal_matrix() -> QuantMatrix:
    samples, rows = _gold_matrix("unequal_matrix.tsv")
    m = QuantMatrix("intensity", "protein", [Feature(k, k) for k in rows], samples, [list(v) for v in rows.values()],
                    {s: s.rsplit("_", 1)[0] for s in samples}, "unequal_matrix.tsv", exp="DIA")
    return m


@pytest.mark.parametrize("case, over", [
    ("half", {"imputation": "none", "small_group_min_valid": "half"}),
    ("same", {"imputation": "none", "small_group_min_valid": "same"}),
    ("imputed", {"imputation": "perseus"}),
])
def test_unequal_groups_match_r_limma(case, over):
    """DMSO n=2, Probe n=4, Probe_Comp n=4 with missing values, through the pipeline analyze() runs
    (benchmark.run_pipeline: the filter, median normalisation, imputation, the role comparisons, the
    small-group rule, limma, BH), against limma 3.68.5 on the same steps in R (tests/golden/unequal/,
    run_unequal_reference.R) to 1e-8: fold change, interval, t, p and adjusted p of every feature."""
    from ionomos.downstream import benchmark

    st = settings_from({**CFG, "normalize": "median", **over})
    p, diffs = benchmark.run_pipeline(_unequal_matrix(), st)
    _cols, want = _gold_matrix("unequal_imputed.tsv" if case == "imputed" else "unequal_processed.tsv")
    assert [f.id for f in p.m.features] == list(want) and len(want) == 320 - 16  # the same rows pass the filter
    worst = max(abs(a - b) for f, row in zip(p.m.features, p.m.values, strict=True)
                for a, b in zip(row, want[f.id], strict=True) if a is not None or b is not None)
    assert worst < 1e-9  # R writes 15 significant digits
    assert [d.name for d in diffs] == ["Probe vs DMSO", "Probe_Comp vs Probe", "Probe_Comp vs DMSO"]  # the roles'
    gold = _gold_limma(f"unequal_limma_{case}.tsv")
    with open(GOLD_UNEQUAL / "unequal_priors.tsv", encoding="utf-8") as fh:
        prior = next(r for r in csv.DictReader(fh, delimiter="\t") if r["case"] == case)
    tested = {}
    for d in diffs:
        assert d.prior == pytest.approx((float(prior["df.prior"]), float(prior["s2.prior"])), rel=1e-9)
        assert len(d.rows) == len(want)
        for r in d.rows:
            g = gold[(d.name, r["id"])]
            for ours, key in ((r["log2fc"], "diff"), (r["ci_low"], "CI.L"), (r["ci_high"], "CI.R"), (r["t"], "t"),
                              (r["pvalue"], "p.val"), (r["qvalue"], "p.adj")):
                ours = math.nan if ours is None else ours
                assert (math.isnan(ours) and math.isnan(g[key])) or ours == pytest.approx(g[key], rel=1e-8, abs=1e-12), \
                    (case, d.name, r["id"], key, ours, g[key])
        tested[d.name] = d.tested
    if case == "imputed":
        assert set(tested.values()) == {len(want)}
    else:  # the edges of the small-group rule: one DMSO value of two is tested with half, not with same
        n_vs_dmso = sum(1 for k, g in gold.items() if k[0] == "Probe vs DMSO" and not math.isnan(g["p.val"]))
        assert tested["Probe vs DMSO"] == n_vs_dmso
        if case == "half":
            same = _gold_limma("unequal_limma_same.tsv")
            more = sum(1 for k, g in gold.items() if not math.isnan(g["p.val"]) and math.isnan(same[k]["p.val"]))
            assert more > 20 and all(not math.isnan(g["p.val"]) or math.isnan(same[k]["p.val"])
                                     for k, g in gold.items())
            assert tested["Probe_Comp vs Probe"] == sum(1 for k, g in same.items() if k[0] == "Probe_Comp vs Probe"
                                                       and not math.isnan(g["p.val"]))  # 4 vs 4: untouched


def test_power_is_given_per_comparison_with_its_own_samples():
    rng = random.Random(2)
    samples = ["D1", "D2", "P1", "P2", "P3", "P4", "K1", "K2", "K3", "K4"]
    cond = {s: s[0] for s in samples}
    values = [[20 + rng.gauss(0, 0.3) for _ in samples] for _ in range(200)]
    pw = insights.power(values, samples, cond, None, pairs=[("P vs D", 4, 2), ("K vs P", 4, 4)], pooled=True)
    assert pw["current_n"] == 4 and pw["unbalanced"] is True         # the old single number said 4 per group
    pd_, kp = pw["comparisons"]
    assert pd_["n"] == [4, 2] and pd_["balanced_n"] == pytest.approx(8 / 3) and kp["balanced_n"] == 4
    assert pd_["df"] == kp["df"] == 7                               # one model: samples - conditions
    for a in ("0.05", "0.001"):
        assert pd_["mdfc"][a]["q50"] / kp["mdfc"][a]["q50"] == pytest.approx(math.sqrt(0.75 / 0.5))
    want = (stats.qt_upper(0.05, 7) + stats.qt_upper(0.4, 7)) * math.sqrt(1 / 4 + 1 / 2) * pw["sd"]["q50"]
    assert pd_["mdfc"]["0.05"]["q50"] == pytest.approx(want)
    # the 4-against-4 comparison matches the balanced curve's formula at n = 4 with the model's df, and without
    # pooling (Welch / Student) the df are the two groups' own
    two = insights.power(values, samples, cond, None, pairs=[("P vs D", 4, 2)], pooled=False)["comparisons"][0]
    assert two["df"] == 4 and two["mdfc"]["0.05"]["q50"] > pd_["mdfc"]["0.05"]["q50"]
    assert "comparisons" not in insights.power(values, samples, cond, None)
    assert "comparisons" not in insights.power(values, samples[:3], {s: "X" for s in samples[:3]}, None, "ratio",
                                               pairs=[("X", 3, 0)])


def test_power_per_comparison_reaches_the_report_and_analysis_json(tmp_path):
    dest = tmp_path / "exp"
    _dia(dest)
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG)
    pw = _payload(out)["qc"]["power"]
    assert pw["unbalanced"] is True and [c["n"] for c in pw["comparisons"]] == [[4, 2], [4, 4], [4, 2]]
    assert pw["comparisons"][0]["balanced"] == pytest.approx(2.67, abs=0.01)
    assert pw["comparisons"][0]["mdfc"]["0.05"]["q50"] > pw["comparisons"][1]["mdfc"]["0.05"]["q50"]
    q = _json(out)["quality"]["detectable_log2fc"]
    assert q["Probe vs DMSO"]["samples"] == [4, 2] and q["Probe vs DMSO"]["p0.05"] > q["Probe_Comp vs Probe"]["p0.05"]


def _clean(sizes: dict[str, int], seed: int, n: int = 800, sd: float = 0.3, noisy: tuple[str, float] | None = None):
    rng = random.Random(seed)
    samples = [f"{c}_{r}" for c, k in sizes.items() for r in range(1, k + 1)]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    rows = []
    for _ in range(n):
        base = rng.gauss(22, 2)
        rows.append([base + rng.gauss(0, sd * (noisy[1] if noisy and s == noisy[0] else 1)) for s in samples])
    return rows, samples, cond


def test_the_scorecard_does_not_judge_a_group_for_being_small():
    """A sample with one mate scatters sqrt(2) around it; one with five mates sqrt(1.2) around their mean. Unscaled,
    the two DMSO samples of a clean 2 / 6 / 6 experiment had a z-score over the flag's 3.5 (40 seeds: median 3.25,
    max 4.42); scaled to the usual group size it is about 0."""
    for seed in (1, 2, 3):
        rows, samples, cond = _clean({"DMSO": 2, "Probe": 6, "Comp": 6}, seed)
        card = insights.sample_scorecard(rows, samples, cond)
        assert all(r["status"] == "ok" and r["flags"] == [] for r in card)
        unscaled = insights._robust_z([r["spread"] for r in card], floor=0.02)
        small = [k for k, r in enumerate(card) if r["condition"] == "DMSO"]
        assert all(unscaled[k] > 2.5 for k in small) and all(abs(card[k]["z_spread"]) < 1.5 for k in small)
        assert all(card[k]["spread"] > 1.15 * card[5]["spread"] for k in small)     # the measured value is kept as it is
    # equal groups: the scale is 1, nothing changes
    rows, samples, cond = _clean({"DMSO": 3, "Probe": 3, "Comp": 3}, 4)
    card = insights.sample_scorecard(rows, samples, cond)
    assert [r["z_spread"] for r in card] == insights._robust_z([r["spread"] for r in card], floor=0.02)
    # a group of one has nothing to be judged on, and is not flagged
    rows, samples, cond = _clean({"DMSO": 1, "Probe": 4, "Comp": 4}, 5)
    lone = insights.sample_scorecard(rows, samples, cond)[0]
    assert (lone["spread"], lone["corr_group"], lone["z_spread"], lone["loo_cv"]) == (None, None, None, None)
    assert lone["status"] == "ok"
    # a sample that really is an outlier is still found, in a large group and in a small one
    for who in ("Probe_1", "DMSO_1"):
        rows, samples, cond = _clean({"DMSO": 2, "Probe": 6, "Comp": 6}, 6, noisy=(who, 3.0))
        card = insights.sample_scorecard(rows, samples, cond)
        assert [r["sample"] for r in card if r["status"] != "ok"][0].startswith(who.split("_")[0])


class _D:
    def __init__(self, t, c, rows):
        self.treatment, self.control, self.rows = t, c, rows


def test_imputation_driven_flags_follow_the_group_size():
    """Half of a group imputed makes the hit imputation-driven: 1 of 2 in a small group, 2 of 4 in a large one."""
    samples = ["DMSO_1", "DMSO_2", "P_1", "P_2", "P_3", "P_4"]
    cond = {s: s.split("_")[0] for s in samples}
    f, t = False, True
    imputed = [[f, f, f, f, f, f], [t, f, f, f, f, f], [t, t, f, f, f, f], [f, f, t, f, f, f], [f, f, t, t, f, f],
               [t, f, f, f, f, f]]
    rows = [{"index": i, "significant": "up" if i < 5 else ""} for i in range(6)]
    assert insights.imputation_driven(imputed, samples, cond, _D("P", "DMSO", rows)) == [1, 2, 4]


def test_imputation_driven_hits_in_an_unequal_experiment(tmp_path):
    dest = tmp_path / "exp"
    _dia(dest, seed=5, n=900)
    # median centring on purpose: it shifts this pulldown (D64), which is what makes hits out of features with
    # one DMSO value; the default (auto -> ratio) leaves none of that kind for this seed
    out = downstream.analyze(dest, "DIA", analysis_cfg={**CFG, "normalize": "median"})
    s = _json(out)
    hits = [r for r in _rows(out, "Probe_vs_DMSO_differential.tsv") if r["significant"]]
    want = [r for r in hits if (2 - int(r["n_control"])) / 2 >= 0.5 or (4 - int(r["n_treatment"])) / 4 >= 0.5]
    assert s["quality"]["imputation_driven_hits"]["Probe vs DMSO"] == len(want)
    assert any(int(r["n_control"]) == 1 and int(r["n_treatment"]) == 4 for r in want)   # one of two DMSO values


def test_low_confidence_wording_for_one_and_two_controls(tmp_path):
    one = tmp_path / "one"
    _dia(one, {"DMSO": 1, "Probe": 3, "Probe_Comp": 3}, n=200)
    out = downstream.analyze(one, "DIA", analysis_cfg=CFG)
    s = _json(out)
    by = {c["name"]: c for c in s["comparisons"]}
    assert by["Probe vs DMSO"]["confidence"] == by["Probe_Comp vs DMSO"]["confidence"] == "low"
    assert by["Probe_Comp vs Probe"]["confidence"] == "normal"
    assert by["Probe vs DMSO"]["confidence_note"].startswith("Low confidence: DMSO has 1 sample. The p-values borrow")
    titles = [i["title"] for i in s["issues"] if i["code"] == "LOW_CONFIDENCE"]
    assert titles == ["Probe vs DMSO: low confidence (a group has one sample)",
                      "Probe_Comp vs DMSO: low confidence (a group has one sample)"]
    assert s["specific_targets"][0]["confidence"] == "low" and "Low confidence" in s["specific_targets"][0]["note"]
    assert "LOW CONFIDENCE — a group has one sample" in (out.results_dir / "volcano_Probe_vs_DMSO.svg").read_text(
        encoding="utf-8")
    # two controls: tested normally, nothing flagged
    two = tmp_path / "two"
    _dia(two, n=200)
    s2 = _json(downstream.analyze(two, "DIA", analysis_cfg=CFG))
    assert all(c["confidence"] == "normal" for c in s2["comparisons"])
    assert not {"LOW_CONFIDENCE", "SMALL_GROUP", "SAMPLE_OUTLIER"} & {i["code"] for i in s2["issues"]}
    # ... unless the lab asks for three per group: then the wording says two, not one
    out3 = downstream.analyze(two, "DIA", analysis_cfg={**CFG, "min_valid": 3})
    s3 = _json(out3)
    low = [i for i in s3["issues"] if i["code"] == "LOW_CONFIDENCE"]
    assert [i["title"] for i in low][0] == "Probe vs DMSO: low confidence (a group has 2 samples)"
    assert "DMSO has 2 samples" in low[0]["message"] and "fewer than the 3 the settings ask for" in low[0]["causes"][0]
    assert "LOW CONFIDENCE — a group has 2 samples" in (out3.results_dir / "volcano_Probe_vs_DMSO.svg").read_text(
        encoding="utf-8")


def test_simulated_tables_are_unchanged_without_the_new_arguments(tmp_path):
    a, b = tmp_path / "a.tsv", tmp_path / "b.tsv"
    names = [f"{c}_1_{ch}" for c, ch in zip(["DMSO"] * 3 + ["Drug"] * 3, ["126", "127N", "127C", "128N", "128C", "129N"],
                                            strict=True)]
    simulate.tmt_abundance(a, names, seed=4, n_genes=50)
    simulate.tmt_abundance(b, names, seed=4, n_genes=50, planted={}, missing=0.03)
    assert a.read_text() == b.read_text()
    truth = _tmt(tmp_path / "c", n=100)
    assert len(truth["specific"]) == len(truth["unspecific"]) == 20 and not truth["specific"] & truth["unspecific"]
    sizes, genes, planted, t2 = simulate.competition_design()
    assert sizes == {"DMSO": 2, "Probe": 4, "Probe_Comp": 4} and len(genes) == 600
    assert planted["Probe"]["TGT0"] == 3.0 and planted["Probe_Comp"]["TGT0"] == 0.0 and planted["Probe_Comp"]["STICKY0"] == 3.0
