"""A pull-down's negative control (roles.py "background", D88): read from the name together with the data or the
experiment's name, never from the name alone; left out of the global missing-value rule; normalised apart from
the pull-downs; every pull-down compared against it. On simulated pull-downs with known truth
(simulate.pulldown_pg_matrix). The real case that asked for it (a FLAG IP with an empty-vector control,
docs/REAL_RUNS.md) is not a fixture: the data is unpublished."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ionomos import configio, downstream
from ionomos.downstream import fpa, roles, simulate
from ionomos.downstream.analysis import Settings, settings_from
from ionomos.downstream.quant import Feature, QuantMatrix

CFG = {"enrichment": False}


def _pg(dest: Path) -> Path:
    return dest / "fragpipe" / "diann-output" / "report.pg_matrix.tsv"


def _json(out) -> dict:
    return json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))


def _hits(out, name: str) -> dict[str, str]:
    import csv

    path = out.results_dir / f"{name.replace(' ', '_')}_differential.tsv"
    with open(path, encoding="utf-8") as fh:
        return {r["label"]: r["significant"] for r in csv.DictReader(fh, delimiter="\t") if r["significant"]}


def _plain(dest: Path, conds=("DMSO", "Drug", "EV"), seed: int = 6) -> None:
    """No condition misses more than another: an empty vector as the plain control of an over-expression."""
    simulate.dia_pg_matrix(_pg(dest), [(f"C:\\raw\\{c}_{r}.raw", c) for c in conds for r in (1, 2, 3)], seed=seed,
                           n_proteins=300)


# ------------------------------------------------------------- read as one --


def test_a_pulldown_with_an_empty_vector_end_to_end(tmp_path):
    dest = tmp_path / "20261009_HEK-FLAG-BAIT-IP"
    truth = simulate.pulldown_pg_matrix(_pg(dest), seed=4)
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG)
    s = _json(out)
    ev = s["roles"]["conditions"]["EV"]
    assert ev["role"] == "background" and ev["source"] == "name ('ev') and the data" and "sure" not in ev
    assert [c["name"] for c in s["comparisons"]] == ["Drug vs DMSO", "DMSO vs EV", "Drug vs EV"]
    assert s["roles"]["comparisons_follow_roles"] is True
    assert {"what": "EV vs DMSO", "why": "a background (negative control): the pull-downs are compared against it "
                                         "instead"} in s["roles"]["not_compared"]
    codes = {i["code"] for i in s["issues"]}
    assert "PULLDOWN_DESIGN" in codes and "COMPETITION_DESIGN" not in codes
    # the interactors read as up over the background; the bead binders don't
    up = _hits(out, "DMSO vs EV")
    found = {g for g, d in up.items() if d == "up"}
    assert len(found & truth["interactors"]) >= 0.75 * len(truth["interactors"])   # low-abundance ones sit near
    assert len({g for g in up if g.startswith("BEAD")}) <= 0.01 * 540              # the imputed level
    # the compound's changes are found within the pull-downs
    drug = _hits(out, "Drug vs DMSO")
    assert {g for g, d in drug.items() if (d == "up") == (truth["drug"].get(g, 0) > 0) and g in truth["drug"]}
    assert len({g for g in drug if g.startswith("BEAD")}) <= 0.01 * 540
    norm = [x for x in s["processing"] if x["step"] == "normalisation"]
    assert norm and norm[0]["method"].endswith("; the background (EV) apart")


def test_the_experiments_name_is_enough_with_the_word_but_is_marked_unsure(tmp_path):
    dest = tmp_path / "20261009_FLAG-IP"
    _plain(dest)
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    ev = s["roles"]["conditions"]["EV"]
    assert ev["role"] == "background" and ev["sure"] is False
    assert ev["source"] == "name ('ev') in a pull-down ('ip' in the experiment's name)"
    issue = next(i for i in s["issues"] if i["code"] == "PULLDOWN_DESIGN")
    assert ("EV: read from the names alone (the data didn't show it), so compared against but filtered and "
            "normalised with the others; check it") in issue["message"]
    assert [c["name"] for c in s["comparisons"]] == ["Drug vs DMSO", "DMSO vs EV", "Drug vs EV"]
    norm = [x for x in s["processing"] if x["step"] == "normalisation"]
    assert norm and "apart" not in norm[0]["method"]                       # not sure: processed with the others


@pytest.mark.parametrize("folder", ["20261009_CMZ_WX48A_SiHa_EV", "20261009_HA-KRAS_OE", "20261009_GFP-X_PD"])
def test_a_tag_or_a_cell_line_in_the_name_is_not_a_pulldown(tmp_path, folder):
    dest = tmp_path / folder                         # SiHa splits into si + ha; a tag is on over-expressions too
    _plain(dest, ("KRAS", "EV"))
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    assert s["roles"]["conditions"]["EV"]["role"] == "control"
    assert [c["name"] for c in s["comparisons"]] == ["KRAS vs EV"]


def test_an_empty_vector_without_the_evidence_stays_the_control(tmp_path):
    dest = tmp_path / "20261009_OE"   # an over-expression: nothing goes missing in EV
    _plain(dest, ("OE", "EV"))
    out = downstream.analyze(dest, "DIA", analysis_cfg=CFG)
    s = _json(out)
    assert s["roles"]["conditions"]["EV"]["role"] == "control"
    assert [c["name"] for c in s["comparisons"]] == ["OE vs EV"]
    assert "PULLDOWN_DESIGN" not in {i["code"] for i in s["issues"]}
    assert not any("background" in n for n in s["notes"])       # a plain over-expression: nothing to say


def test_a_low_loaded_control_of_a_proteome_is_not_a_background():
    """Its samples miss more of the faint features, but there is no set measured everywhere else."""
    samples = [f"{c}_{r}" for c in ("OE", "EV") for r in (1, 2, 3)]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    feats, vals = [], []
    for i in range(200):
        feats.append(Feature(f"P{i}", f"G{i}"))
        lvl = 16 + i * 0.05
        vals.append([None if (cond[s] == "EV" and lvl < 18 and (i + int(s[-1])) % 2) else lvl for s in samples])
    m = QuantMatrix("intensity", "protein", feats, samples, vals, cond)
    ev = roles.background_evidence(m, Settings(), "20261009_OE")["conditions"]["EV"]
    assert not ev["use"] and ev["data"] == "" and ev["why_not"].startswith("only part of the data says so (its samples")


def test_the_data_alone_is_only_a_suggestion(tmp_path):
    dest = tmp_path / "20261009_FLAG-IP"
    simulate.pulldown_pg_matrix(_pg(dest), seed=4, background="Neg")
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg=CFG))
    assert s["roles"]["conditions"]["Neg"]["role"] == "compound"
    assert any(n.startswith("Neg looks like the background (negative control) of this pull-down") for n in s["notes"])


def test_the_settings_win(tmp_path):
    dest = tmp_path / "20261009_HEK-FLAG-BAIT-IP"
    simulate.pulldown_pg_matrix(_pg(dest), seed=4)
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg={**CFG, "roles": {"EV": "control"}}))
    assert s["roles"]["conditions"]["EV"]["role"] == "control"
    s = _json(downstream.analyze(dest, "DIA", analysis_cfg={**CFG, "control": "EV"}))
    assert s["roles"]["conditions"]["EV"]["role"] == "control"
    dest2 = tmp_path / "plain"
    _plain(dest2, ("DMSO", "Drug", "Mock"))
    s = _json(downstream.analyze(dest2, "DIA", analysis_cfg={**CFG, "roles": {"Mock": "negative control"}}))
    assert s["roles"]["conditions"]["Mock"]["role"] == "background"
    assert [c["name"] for c in s["comparisons"]] == ["Drug vs DMSO", "DMSO vs Mock", "Drug vs Mock"]


@pytest.mark.parametrize("name,word", [("EV", "ev"), ("empty", "empty"), ("EmptyVector", "emptyvector"),
                                       ("beads", "beads"), ("IgG", "igg"), ("untagged", "untagged")])
def test_background_words(name, word):
    m = _missing_matrix(name)
    ev = roles.background_evidence(m, Settings(), "x")["conditions"][name]
    assert ev["use"] and ev["sure"] and ev["word"] == word
    assert roles.infer(m.conditions, Settings(), evidence={"conditions": {name: ev}}).roles[name].role == "background"


def test_background_keywords_are_a_setting(tmp_path):
    s = settings_from({"background_keywords": "neg, nobead"})
    assert s.background_keywords == ("neg", "nobead")
    ev = roles.background_evidence(_missing_matrix("Neg"), s, "x")
    assert ev["conditions"]["Neg"]["use"]
    assert "EV" not in roles.background_evidence(_missing_matrix("EV"), s, "x")["conditions"]
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    d["analysis"]["background_keywords"] = ["neg", "nobead"]
    assert yaml.safe_load(configio.dump_config(d))["analysis"]["background_keywords"] == ["neg", "nobead"]


def test_the_bait_is_named_when_the_experiments_name_names_it():
    m = _missing_matrix("EV", bait="FOXA1")
    data = roles.background_evidence(m, Settings(), "20261006_HEK-FLAG-FOXA1-IP")["conditions"]["EV"]["data"]
    assert "the bait FOXA1 is not measured there" in data


def _missing_matrix(bg: str, bait: str = "") -> QuantMatrix:
    """Three pull-downs of DMSO and Drug, three of `bg` missing a quarter of the features (the interactors)."""
    samples = [f"{c}_{r}" for c in ("DMSO", "Drug", bg) for r in (1, 2, 3)]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    feats, vals = [], []
    for i in range(80):
        feats.append(Feature(f"P{i}", bait if (i == 0 and bait) else f"G{i}"))
        vals.append([None if (cond[s] == bg and i < 20) else 20.0 + (i % 7) * 0.1 for s in samples])
    return QuantMatrix("intensity", "protein", feats, samples, vals, cond)


# ---------------------------------------------------------------- the steps --


def test_the_global_filter_does_not_count_the_background():
    samples = [f"{c}_{r}" for c in ("DMSO", "Drug", "EV") for r in (1, 2, 3)]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    in_ip = [20.0] * 6 + [None] * 3                       # every pull-down, no background: 6 of 9
    four = [20.0, 20.0, None, 20.0, 20.0, None, None, None, None]   # 4 of the 6 pull-downs
    m = QuantMatrix("intensity", "protein", [Feature("A", "A"), Feature("B", "B")], samples, [in_ip, four], cond)
    kept, removed = fpa.filter_missing(m, 66, 0)
    assert [f.id for f in kept.features] == ["A"] and removed == 1
    kept, removed = fpa.filter_missing(m, 66, 0, background=["EV"])
    assert [f.id for f in kept.features] == ["A", "B"] and removed == 0


def test_the_background_is_normalised_apart():
    samples = ["DMSO_1", "DMSO_2", "Drug_1", "Drug_2", "EV_1", "EV_2"]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    loading = {"DMSO_1": 0.0, "DMSO_2": 0.4, "Drug_1": -0.2, "Drug_2": 0.1, "EV_1": -4.0, "EV_2": -3.5}
    vals = [[20.0 + i * 0.05 + loading[s] for s in samples] for i in range(40)]
    m = QuantMatrix("intensity", "protein", [Feature(f"P{i}", f"G{i}") for i in range(40)], samples, vals, cond)
    out, info = fpa.normalize_apart(m, "median", ["EV"])
    med = {s: sorted(r[j] for r in out.values)[20] for j, s in enumerate(samples)}
    ip = [med[s] for s in samples[:4]]
    assert max(ip) - min(ip) < 1e-9 and abs(med["EV_1"] - med["EV_2"]) < 1e-9
    assert med["DMSO_1"] - med["EV_1"] > 3.5      # the pull-downs stay above the background
    assert info["background"] == {"conditions": ["EV"], "samples": ["EV_1", "EV_2"], "used": "median"}
    together, _ = fpa.normalize_info(m, "median")
    assert abs(sorted(r[0] for r in together.values)[20] - sorted(r[4] for r in together.values)[20]) < 1e-9
    same, info2 = fpa.normalize_apart(m, "median", [])
    assert same.values == together.values and "background" not in info2


# ----------------------------------------------------------------- the editor --


def test_the_editor_offers_background_and_hints_at_it():
    sizes = {"DMSO": 3, "Drug": 3, "EV": 3}
    view = roles.preview(sizes, Settings())
    ev = next(r for r in view.rows if r.condition == "EV")
    assert ev.role in ("compound", "control") and not ev.confirm          # a hint, not a question
    assert ("EV: 'ev' in the name: if EV is the negative control of a pull-down (empty vector, beads, IgG), choose "
            "background") in view.notes
    named = roles.preview(sizes, Settings(), name="20261009_FLAG-BAIT-IP")    # the review window: the folder's name
    row = next(r for r in named.rows if r.condition == "EV")
    assert row.label == "background (negative control)" and row.confirm and row.confirmable
    assert named.pairs == [("Drug", "DMSO"), ("DMSO", "EV"), ("Drug", "EV")]
    assert ("background (negative control)", "background") in roles.role_choices(view, "EV")
    chosen = roles.preview(sizes, Settings(), roles.set_role({}, "EV", "background"))
    assert chosen.pairs == [("Drug", "DMSO"), ("DMSO", "EV"), ("Drug", "EV")] and chosen.follows_roles
    row = next(r for r in chosen.rows if r.condition == "EV")
    assert row.label == "background (negative control)" and not row.confirm
    assert any("enrichment over background: what DMSO pulls down above the negative control" in x
               for x in chosen.comparisons)
    assert "Not compared: EV vs DMSO (a background (negative control): the pull-downs are compared against it " \
           "instead)" in chosen.notes
