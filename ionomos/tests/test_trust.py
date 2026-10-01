"""'How far to trust this' (downstream/trust.py, D60): plain statements with their numbers in analysis.json and
at the top of report.html, built from checks the analysis already makes. No score."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from ionomos import downstream
from ionomos.downstream import analysis, simulate, trust

CFG = {"enrichment": False}


def _runs(*groups):
    return [(f"C:\\d\\{c}_{r}.raw", c) for c, n in groups for r in range(1, n + 1)]


def _analyze(tmp_path, groups=(("DMSO", 4), ("Drug", 4)), seed=6, n=500, edit=None, **settings):
    table = tmp_path / "e/fragpipe/report.pg_matrix.tsv"
    simulate.dia_pg_matrix(table, _runs(*groups), seed=seed, n_proteins=n, noise_spread=0.4)
    if edit:
        edit(table)
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg={**CFG, **settings})
    t = json.loads((tmp_path / "e/results/analysis.json").read_text(encoding="utf-8"))["trust"]
    return out, t, {s["key"]: s for s in t.get("statements", [])}


def _block(out) -> str:
    html = out.report.read_text(encoding="utf-8")
    m = re.search(r"<div class='card findings' id='trust'>.*?</div></div>", html, re.S)
    return m.group(0) if m else ""


def test_a_clean_experiment_states_its_numbers_and_flags_nothing(tmp_path):
    out, t, by = _analyze(tmp_path)
    assert [s["key"] for s in t["statements"]] == ["replicates", "agreement", "missing", "comparison", "power"]
    assert {s["level"] for s in t["statements"]} == {"ok"}
    assert by["replicates"]["text"] == "Samples per group: DMSO 4, Drug 4."
    assert by["replicates"]["numbers"] == {"groups": {"DMSO": 4, "Drug": 4}, "smallest": 4, "largest": 4}
    a = by["agreement"]["numbers"]
    assert 0.8 < a["lowest_r"] <= a["median_r"] <= 1 and set(a["median_cv"]) == {"DMSO", "Drug"} and a["flagged"] == a["warned"] == []
    m = by["missing"]["numbers"]
    assert 0 < m["missing_share"] < 0.1 and m["imputed_share"] == m["missing_share"] and m["imputation"] == "perseus"
    c = by["comparison"]
    d = out.summary["comparisons"][0]
    assert c["numbers"]["tested"] == d["tested"] and (c["numbers"]["up"], c["numbers"]["down"]) == (d["up"], d["down"])
    assert c["text"].startswith(f"Drug vs DMSO: {d['tested']:,} features tested, {d['up']:,} up and {d['down']:,} down.")
    assert c["numbers"]["p_value_shape"] == "signal" and "Hits resting on imputed values" in c["text"]
    p = by["power"]["numbers"]
    assert p["n"] == 4 and 0 < p["detectable_p05"] < p["detectable_p001"] and p["log2fc_cutoff"] == 1.0
    assert "found 80% of the time at p 0.05" in by["power"]["text"]
    # no score anywhere, and the basis is said
    assert not any("score" in k for k in t) and "no overall score" in t["basis"]
    assert t["compare"] is None and t["benchmark"] is None and t["benchmark_simulated"] is None


def test_the_report_carries_the_list_under_the_key_findings(tmp_path):
    out, t, _by = _analyze(tmp_path)
    html = out.report.read_text(encoding="utf-8")
    block = _block(out)
    assert block.startswith("<div class='card findings' id='trust'><h3>How far to trust this</h3><ul><li")
    assert block.count("<li data-key=") == len(t["statements"]) and "pill warn" not in block
    assert "There is no overall score" in block and "ionomos compare" in block
    assert html.index("id='findings'") < html.index("id='trust'") < html.index("<section id='differential'>")
    assert "How far to trust this" in json.dumps(json.loads(re.search(
        r"<script id='ionomos-data' type='application/json'>(.*?)</script>", html, re.S).group(1))["help"]["entries"]
        ["report.trust"]), "its help entry is in the report"


def test_small_and_uneven_groups_are_marked(tmp_path):
    _out, _t, by = _analyze(tmp_path / "two", groups=(("DMSO", 2), ("Drug", 2)))
    assert by["replicates"]["level"] == "check" and "With 2 replicates" in by["replicates"]["text"]
    _out, _t, by = _analyze(tmp_path / "uneven", groups=(("DMSO", 3), ("Drug", 6)))
    assert by["replicates"]["level"] == "check" and "DMSO 3, Drug 6 (uneven)" in by["replicates"]["text"]
    assert by["replicates"]["numbers"]["smallest"] == 3 and by["replicates"]["numbers"]["largest"] == 6
    out, _t, by = _analyze(tmp_path / "one", groups=(("DMSO", 1), ("Drug", 4)))
    assert "A group of one has no replicate spread" in by["replicates"]["text"]
    assert by["comparison"]["level"] == "check" and "Low confidence" in by["comparison"]["text"]
    assert "<span class='pill warn'>check</span> Samples per group: DMSO 1, Drug 4." in _block(out)
    out, t, by = _analyze(tmp_path / "none", groups=(("DMSO", 1), ("Drug", 1)))
    assert by["comparison"]["level"] == "check" and "fold change only" in by["comparison"]["text"]
    assert "no p-values exist" in by["comparison"]["text"] and "power" not in by


def test_a_flagged_sample_marks_replicate_agreement(tmp_path):
    def spoil(table: Path):  # one replicate with noise of its own
        rng = random.Random(1)
        lines = table.read_text(encoding="utf-8").split("\n")
        out = [lines[0]]
        for ln in lines[1:]:
            c = ln.split("\t")
            if len(c) > 9 and c[9]:
                c[9] = f"{float(c[9]) * 2 ** rng.gauss(0, 1.5):.1f}"
            out.append("\t".join(c))
        table.write_text("\n".join(out), encoding="utf-8")

    out, _t, by = _analyze(tmp_path, edit=spoil)
    a = by["agreement"]
    assert a["level"] == "check" and a["numbers"]["flagged"] == ["DMSO_3"]
    assert "The sample scorecard fails 1: DMSO_3." in a["text"]
    assert "DMSO_3" in out.summary["quality"]["samples_flagged"] and "SAMPLE_OUTLIER" in {i.code for i in out.issues}


def test_imputation_is_stated_and_marked_when_hits_rest_on_it(tmp_path):
    def thin(table: Path):  # 60 proteins never measured in DMSO: with Perseus imputation they become hits
        lines = table.read_text(encoding="utf-8").split("\n")
        out = [lines[0]]
        for k, ln in enumerate(lines[1:]):
            c = ln.split("\t")
            if len(c) > 10 and k < 60:
                c[7:11] = [""] * 4
            out.append("\t".join(c))
        table.write_text("\n".join(out), encoding="utf-8")

    out, _t, by = _analyze(tmp_path / "imp", edit=thin)
    c = by["comparison"]
    assert c["level"] == "check" and c["numbers"]["imputation_driven_hits"] >= 40
    assert f"imputed): {c['numbers']['imputation_driven_hits']:,} of " in c["text"]
    assert "IMPUTATION_DRIVEN" in {i.code for i in out.issues}, "the same threshold as the doctor's"
    _out, _t, by = _analyze(tmp_path / "none", edit=thin, imputation="none")
    assert "Nothing was imputed" in by["missing"]["text"] and by["missing"]["numbers"]["imputed_share"] == 0
    assert "imputation_driven_hits" not in by["comparison"]["numbers"]


def test_power_is_marked_when_the_design_cannot_see_the_cutoff(tmp_path):
    _out, _t, by = _analyze(tmp_path, groups=(("DMSO", 2), ("Drug", 2)), log2fc=0.3)
    p = by["power"]
    assert p["level"] == "check" and p["numbers"]["detectable_p05"] > 0.3
    assert "That is above the cut-off of 0.3" in p["text"]


def test_guard_findings_are_statements_too(tmp_path):
    def copy(table: Path):  # DMSO_2 is DMSO_1 again
        out = []
        for ln in table.read_text(encoding="utf-8").split("\n"):
            c = ln.split("\t")
            if len(c) > 8 and not ln.startswith("Protein.Group"):
                c[8] = c[7]
            out.append("\t".join(c))
        table.write_text("\n".join(out), encoding="utf-8")

    out, t, _by = _analyze(tmp_path, edit=copy)
    s = [x for x in t["statements"] if x["key"] == "statistics"]
    assert len(s) == 1 and s[0]["level"] == "check" and "DMSO_1 = DMSO_2" in s[0]["text"]
    assert s[0]["numbers"]["code"] == "IDENTICAL_SAMPLES"


def test_a_results_table_states_only_what_it_holds(tmp_path):
    rng = random.Random(2)
    rows = [f"P{i}\t{rng.gauss(0, 1):.3f}\t{rng.random():.5f}" for i in range(400)]
    table = tmp_path / "limma.tsv"
    table.write_text("ID\tlogFC\tP.Value\n" + "\n".join(rows) + "\n", encoding="utf-8")
    ws = tmp_path / "limma_ionomos"
    ws.mkdir()
    out = downstream.analyze(ws, analysis_cfg=CFG, table=table)
    t = out.summary["trust"]
    assert [s["key"] for s in t["statements"]] == ["comparison"]
    assert "p-value histogram: flat: no sign of differences" in t["statements"][0]["text"]


def test_nothing_analysed_gives_an_empty_list_and_no_block(tmp_path):
    (tmp_path / "e").mkdir()
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg=CFG)
    assert out.summary["trust"]["statements"] == [] and "id='trust'" not in out.report.read_text(encoding="utf-8")
    assert trust.html(None) == "" and trust.html({"statements": []}) == ""


def test_the_block_escapes_what_comes_from_the_data():
    t = {"statements": [{"key": "comparison", "level": "check", "text": "<img src=x> vs \"a\" & b", "numbers": {}}],
         "compare": {"file": "compare.json", "page": "compare.html", "generated_at": "<now>", "reference": "<b>ref</b>",
                     "same_settings": False, "lines": ["<script>x</script>: agrees"]}, "basis": "b"}
    html = trust.html(t)
    assert "<img" not in html and "<script" not in html and "<b>ref" not in html
    assert "&lt;img src=x&gt; vs &quot;a&quot; &amp; b" in html and "&lt;script&gt;x&lt;/script&gt;: agrees" in html
    assert "<span class='pill warn'>other settings</span>" in html


def test_the_settings_digest_is_the_same_from_settings_and_from_analysis_json(tmp_path):
    out, t, _by = _analyze(tmp_path, alpha=0.01, sample_conditions={"Drug_4": "Drug"})
    stored = json.loads((tmp_path / "e/results/analysis.json").read_text(encoding="utf-8"))["settings"]
    assert trust.settings_digest(stored) == t["settings_digest"]
    s = analysis.settings_from({**CFG, "alpha": 0.01, "sample_conditions": {"Drug_4": "Drug"}})
    assert trust.settings_digest(analysis.as_dict(s)) == t["settings_digest"]
    assert trust.settings_digest(analysis.as_dict(analysis.settings_from(CFG))) != t["settings_digest"]


def test_a_crashing_trust_step_never_costs_the_report(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("trust broke")

    monkeypatch.setattr(trust, "build", boom)
    out, t, _by = _analyze(tmp_path)
    assert t == {} and out.report.is_file() and out.summary["comparisons"][0]["tested"] > 400
    assert any("analysis step trust failed" in w for w in out.warnings)
