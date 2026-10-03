"""Liganded cysteines (downstream/cys.py, D52): the calls, selectivity, the protein view, a site annotation, and
the whole analysis on a simulated isoDTB experiment."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import cys, fpa, simulate
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from
from ionomos.downstream.quant import Feature, QuantMatrix

L4 = math.log2(4)


def _processed(sites: dict[str, dict[str, list[float | None]]], reps: int = 3):
    """sites: {'P1|C10': {'A': [log2 R per replicate], 'B': [...]}} -> fpa.Processed of a site ratio matrix."""
    conds = list(next(iter(sites.values())))
    samples = [f"{c}_{r}" for c in conds for r in range(1, reps + 1)]
    feats, vals = [], []
    for sid, per in sites.items():
        prot, site = sid.split("|")
        feats.append(Feature(id=f"sp|{prot}|G{prot}_HUMAN|{site}", label=f"G{prot} {site}", description=f"{prot} protein"))
        vals.append([v for c in conds for v in (per[c] + [None] * reps)[:reps]])
    m = QuantMatrix("ratio", "site", feats, samples, vals, {s: s.rsplit("_", 1)[0] for s in samples}, "sites.tsv",
                    exp="isoDTB")
    p, _notes = fpa.process(m)
    return p


def test_classify_rule():
    assert cys.classify([2.5, 2.1, 0.3], L4, 2) == ("liganded", 3, 2)
    assert cys.classify([2.0, 2.0, None], L4, 2) == ("liganded", 2, 2)         # exactly R = 4 counts
    assert cys.classify([2.5, 0.2, 0.3], L4, 2) == ("inconsistent", 3, 1)
    assert cys.classify([0.1, 0.2, 0.3], L4, 2) == ("not liganded", 3, 0)
    assert cys.classify([3.0, None, None], L4, 2) == ("too few", 1, 1)
    assert cys.classify([None, None, None], L4, 2) == ("too few", 0, 0)
    assert cys.classify([3.0], L4, 1) == ("liganded", 1, 1)


def test_site_key():
    assert cys.site_key("sp|P04406|G3P_HUMAN|C152") == ("P04406", "C", 152)
    assert cys.site_key("tr|A0A024|X_HUMAN|C7") == ("A0A024", "C", 7)
    assert cys.site_key("MYPROT|C12") == ("MYPROT", "C", 12)
    assert cys.site_key("no-site-here") == ("no-site-here", "", None)


SITES = {
    "P1|C10": {"A": [2.5, 2.2, 2.4], "B": [0.1, 0.0, -0.2]},     # selective for A
    "P1|C20": {"A": [2.5, 3.0, None], "B": [2.1, 2.6, 2.2]},     # shared
    "P1|C30": {"A": [2.6, 0.1, 0.2], "B": [0.0, 0.1, 0.2]},      # inconsistent in A
    "P2|C5": {"A": [0.1, 0.2, 0.0], "B": [3.1, 2.9, 3.3]},       # selective for B
    "P3|C7": {"A": [2.2, 2.3, 2.1], "B": [0.3, None, None]},     # liganded by A, B not measured enough: unresolved
    "P4|C9": {"A": [None, None, 1.0], "B": [None, None, None]},  # too few
}


def test_calls_selectivity_and_fractions():
    res = cys.run(_processed(SITES), Settings())
    by = {r["id"].split("|", 1)[1].split("|", 1)[1].replace("_HUMAN|", "|"): r for r in res.rows}
    cls = {k.replace("GP", "P"): (r["per"]["A"]["class"], r["per"]["B"]["class"], r["selectivity"]) for k, r in by.items()}
    assert cls == {
        "P1|C10": ("liganded", "not liganded", "selective"),
        "P1|C20": ("liganded", "liganded", "shared"),
        "P1|C30": ("inconsistent", "not liganded", ""),
        "P2|C5": ("not liganded", "liganded", "selective"),
        "P3|C7": ("liganded", "too few", "unresolved"),
        "P4|C9": ("too few", "too few", ""),
    }
    a, b = res.compounds
    assert a.counts == {"liganded": 3, "inconsistent": 1, "not liganded": 1, "too few": 1}
    assert a.assessed == 5 and a.fraction == pytest.approx(3 / 5)
    assert b.counts["liganded"] == 2 and b.assessed == 4 and b.fraction == pytest.approx(0.5)
    assert res.selectivity == {"selective": 2, "shared": 1, "unresolved": 1}
    first = res.rows[0]["per"]["A"]
    assert first["log2"] == pytest.approx(2.4) and first["engagement"] == pytest.approx(1 - 2 ** -2.4)
    assert (first["n"], first["over"]) == (3, 3)
    assert not res.problems and not res.notes
    s = cys.summary(res, table="results/cysteine_sites.tsv")
    assert s["rule"] == "R ≥ 4 (heavy / light) in at least 2 replicates, on the ratios as measured (not centred)"
    assert s["sites_liganded"] == 4 and s["centred"] is False
    assert s["compounds"][0]["liganded_fraction"] == 0.6 and s["selectivity"]["selective"] == 2


def test_thresholds_are_settings():
    p = _processed(SITES)
    strict = cys.run(p, Settings(liganded_ratio=5.5, liganded_min_replicates=3))
    assert [c.counts["liganded"] for c in strict.compounds] == [0, 1]              # only P2|C5 in B: R > 5.5 x 3
    loose = cys.run(p, Settings(liganded_ratio=2, liganded_min_replicates=1))
    assert loose.compounds[0].counts["liganded"] == 5 and loose.compounds[0].counts["too few"] == 0   # one R = 2 too
    for bad in ({"liganded_ratio": 1}, {"liganded_min_replicates": 0}, {"liganded_direction": "sideways"}):
        with pytest.raises(AnalysisError):
            settings_from(bad)
    assert settings_from({"liganded_direction": "L/H"}).liganded_direction == "low"
    assert settings_from({"liganded": "no", "liganded_ratio": "3"}).liganded is False


def test_direction_low_flips_the_ratio_and_a_wrong_direction_is_flagged():
    sites = {f"P{k}|C{k}": {"A": [-2.5, -2.2, -2.4]} for k in range(1, 15)}
    sites["P99|C1"] = {"A": [0.0, 0.1, -0.1]}
    p = _processed(sites)
    high = cys.run(p, Settings())
    assert high.compounds[0].counts["liganded"] == 0 and high.compounds[0].other_side == 14
    assert len(high.problems) == 1 and "the other way round (liganded_direction: low)" in high.problems[0][1]
    low = cys.run(p, Settings(liganded_direction="low"))
    assert low.compounds[0].counts["liganded"] == 14 and not low.problems
    assert low.rows[0]["per"]["A"]["log2"] == pytest.approx(2.4)
    assert "light / heavy" in cys.rule_text(low)


def test_fewer_replicates_than_the_rule_use_what_there_is():
    res = cys.run(_processed({"P1|C1": {"A": [2.5]}, "P1|C2": {"A": [0.5]}}, reps=1), Settings())
    assert res.compounds[0].min_replicates == 1 and res.compounds[0].counts["liganded"] == 1
    assert "A has 1 replicate" in res.notes[0] and res.selectivity["selective"] == 0
    assert res.rows[0]["selectivity"] == ""       # one compound: nothing to be selective against


def test_proteins_with_most_sites_liganded_are_marked():
    sites = {f"P1|C{k}": {"A": [2.5, 2.5, 2.5]} for k in (1, 2)}
    sites["P1|C3"] = {"A": [0.0, 0.0, 0.0]}
    sites["P1|C4"] = {"A": [0.0, 0.0, 0.0]}
    sites["P2|C1"] = {"A": [2.5, 2.5, 2.5]}
    sites["P2|C2"] = {"A": [0.0, 0.0, 0.0]}
    sites["P3|C1"] = {"A": [0.0, 0.0, 0.0]}
    res = cys.run(_processed(sites), Settings())
    prot = {e["protein"]: e["per"]["A"] for e in res.proteins}
    assert prot == {"P1": {"assessed": 4, "liganded": 2, "most": True}, "P2": {"assessed": 2, "liganded": 1, "most": False}}
    rows = cys.protein_rows(res)
    assert rows[0]["A pattern"] == "most sites" and rows[1]["A pattern"] == "site-specific"
    assert res.proteins[0]["idx"] == [0, 1, 2, 3]


# ---------------------------------------------------------------- annotation --


def test_cysdb_style_annotation(tmp_path):
    f = tmp_path / "cysdb.csv"
    f.write_text("cysteineid,proteinid,identified,hyperreactive,ligandable,note\n"
                 "P1_C10,P1,yes,no,yes,first\nP1_C20,P1,yes,yes,no,x\nP2_C5,P2,yes,no,no,y\n"
                 + "".join(f"P9_C{k},P9,yes,no,no,z\n" for k in range(1, 9)) + "garbage,P9,yes,no,no,z\n",
                 encoding="utf-8")
    ann = cys.load_annotation(f)
    assert ann["flags"] == ["identified", "hyperreactive", "ligandable"] and len(ann["sites"]) == 11
    assert ann["sites"][("P1", 10)] == {"identified": True, "hyperreactive": False, "ligandable": True}
    assert "1 rows without a readable site left out" in ann["notes"][0]
    res = cys.run(_processed(SITES), Settings(), ann)
    status = {r["site"]: r["status"] for r in res.rows}
    assert status == {"GP1 C10": "known liganded", "GP1 C20": "known hyperreactive", "GP1 C30": "new",
                      "GP2 C5": "seen before", "GP3 C7": "new", "GP4 C9": "new"}
    assert res.annotation == {"file": "cysdb.csv", "sites_in_file": 11, "flags": ann["flags"], "matched": 3,
                              "liganded_known": 3, "liganded_new": 1}
    cols = cys.columns(res)
    assert cols[-4:] == ["annotation", "identified", "hyperreactive", "ligandable"]
    row = next(r for r in cys.table_rows(res) if r["site"] == "GP1 C10")
    assert (row["annotation"], row["ligandable"], row["hyperreactive"]) == ("known liganded", "yes", "no")
    assert next(r for r in cys.table_rows(res) if r["site"] == "GP3 C7")["ligandable"] == ""


def test_annotation_with_accession_and_position_columns_and_other_key_spellings(tmp_path):
    f = tmp_path / "sites.tsv"
    f.write_text("Protein ID\tPosition\tLigandable\nsp|P1|GP1_HUMAN\tC10\t1\nP2-2\t5\t0\n", encoding="utf-8")
    ann = cys.load_annotation(f)
    assert ann["sites"] == {("P1", 10): {"ligandable": True}, ("P2", 5): {"ligandable": False}}
    g = tmp_path / "keys.txt"
    g.write_text("site\nP04406_CYS152\nQ9Y3A3-2_C7\nO15111_23\n", encoding="utf-8")
    assert set(cys.load_annotation(g)["sites"]) == {("P04406", 152), ("Q9Y3A3", 7), ("O15111", 23)}
    bad = tmp_path / "bad.csv"
    bad.write_text("gene,value\nA,1\nB,2\n", encoding="utf-8")
    with pytest.raises(cys.AnnotationError, match="no site column"):
        cys.load_annotation(bad)
    with pytest.raises(cys.AnnotationError, match="can't be read"):
        cys.load_annotation(tmp_path / "missing.csv")


# ------------------------------------------------------------- whole analysis --


def _experiment(tmp_path: Path, **analysis):
    d = tmp_path / "exp"
    hits = simulate.isodtb_label_quant(d / "fragpipe" / "combined_modified_peptide_label_quant.tsv",
                                       {"CmpdA": [1, 2, 3], "CmpdB": [1, 2, 3]}, seed=3)
    out = downstream.analyze(d, "isoDTB", {"enrichment": False, **analysis})
    return d, hits, out, json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))


def test_isodtb_analysis_calls_liganded_sites(tmp_path):
    d, hits, out, s = _experiment(tmp_path)
    c = s["cysteines"]
    assert c["ran"] and c["table"] == "results/cysteine_sites.tsv" and c["proteins_table"]
    assert [x["name"] for x in c["compounds"]] == ["CmpdA", "CmpdB"]
    import csv

    with open(d / "results" / "cysteine_sites.tsv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert len(rows) == s["features"]
    called = {r["id"] for r in rows if r["CmpdA class"] == "liganded"}
    assert len(called & hits) / len(hits) > 0.7 and len(called - hits) <= 2    # planted sites sit near R = 5.3
    assert rows[0]["n_liganded"] == "2" and rows[0]["selectivity"] == "shared"  # liganded sites first
    assert float(rows[0]["CmpdA R"]) == pytest.approx(2 ** float(rows[0]["CmpdA log2_R"]), rel=1e-2)
    assert (d / "results" / "cysteine_proteins.tsv").read_text(encoding="utf-8").startswith("protein\tgene\tsites\t")
    html = out.report.read_text(encoding="utf-8")
    assert "<a href='#cys' id='navcys'>Liganded sites</a>" in html and "<section id='cys'>" in html
    assert "called liganded by a compound when its competition ratio reached R ≥ 4 (heavy / light)" in html
    data = json.loads(html.split("<script id='ionomos-data' type='application/json'>")[1].split("</script>")[0])
    cy = data["cys"]
    assert cy["ran"] and len(cy["i"]) == len(data["f"]["id"]) == len(cy["compounds"][0]["r"])
    assert cy["compounds"][0]["counts"]["liganded"] == c["compounds"][0]["liganded"]
    assert "cysteine_sites.tsv" in data["files"]
    assert not [i for i in out.issues if i.code in ("LIGANDED_DIRECTION", "SITE_ANNOTATION")]


def test_settings_reach_the_analysis_and_it_can_be_switched_off(tmp_path):
    _d, _hits, _out, s = _experiment(tmp_path, liganded_ratio=8, liganded_min_replicates=3)
    assert s["cysteines"]["rule"] == ("R ≥ 8 (heavy / light) in at least 3 replicates, on the ratios as measured "
                                      "(not centred)")
    assert s["cysteines"]["compounds"][0]["liganded"] <= 2
    d, _hits, out, s = _experiment(tmp_path / "off", liganded=False)
    assert s["cysteines"] == {"ran": False, "reason": "liganded-site calls are switched off (analysis.liganded)"}
    assert not (d / "results" / "cysteine_sites.tsv").exists()
    assert "<section id='cys' hidden>" in out.report.read_text(encoding="utf-8")


def test_wrong_direction_and_a_missing_annotation_become_issues(tmp_path):
    d, _hits, out, s = _experiment(tmp_path, liganded_direction="low", site_annotation="cysdb.csv")
    codes = {i.code: i for i in out.issues}
    assert codes["LIGANDED_DIRECTION"].severity == "warning" and "liganded_direction: high" in codes["LIGANDED_DIRECTION"].message
    assert "'cysdb.csv' was not found" in codes["SITE_ANNOTATION"].message
    assert s["cysteines"]["ran"] and "annotation" not in s["cysteines"]        # the calls are still made
    (d / "cysdb.csv").write_text("cysteineid,ligandable\nP10051_C388,yes\n", encoding="utf-8")
    out = downstream.analyze(d, "isoDTB", {"enrichment": False, "site_annotation": "cysdb.csv"})
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["cysteines"]["annotation"]["matched"] == 1 and s["cysteines"]["annotation"]["liganded_known"] == 1
    assert not [i for i in out.issues if i.code == "SITE_ANNOTATION"]
    assert any("site annotation cysdb.csv: 1 sites with ligandable" in n for n in out.warnings)


def test_intensity_data_gets_no_cysteine_section(tmp_path):
    d = tmp_path / "dia"
    simulate.dia_pg_matrix(d / "report.pg_matrix.tsv", [(f"{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)],
                           n_proteins=60)
    out = downstream.analyze(d, analysis_cfg={"enrichment": False})
    assert out.summary["cysteines"] == {"ran": False, "reason": "not site ratio data (isoDTB)"}
    assert "<section id='cys' hidden>" in out.report.read_text(encoding="utf-8")
