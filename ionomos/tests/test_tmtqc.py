"""TMT QC after the search (downstream/tmtqc.py, D91): per plex and channel from FragPipe's psm.tsv ("Intensity
<sample>" reporter columns, Purity, Assigned Modifications), the tmt-report header when there is no psm.tsv, every
doctor issue, the report section and its exported figures, TMT-Integrator's other tables, and the analysis-off path."""
import json
import random
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ionomos import help as helpdoc
from ionomos import names, postprocess, testbed, tmtkit
from ionomos.downstream import analyze, charts, doctor, quant, simulate, slides, tmtqc

TMT10 = tmtkit.LABEL_TYPES["TMT-10"].channels
PROPS = {"tmtintegrator.channel_num": "TMT-10", "tmtintegrator.quant_level": "3", "tmtintegrator.min_purity": "0.5",
         "tmtintegrator.add_Ref": "1", "tmtintegrator.ref_tag": "Bridge", "tmtintegrator.prot_norm": "1",
         "msfragger.table.fix-mods": "57.02146,C (cysteine),true,-1; 229.16293,K (lysine),true,-1"}
SVG = "{http://www.w3.org/2000/svg}"


def _names(plex=1, conds=("Pool", "DMSO", "DMSO", "DMSO", "Drug", "Drug", "Drug")):
    return [(ch, f"{c}_{plex}_{ch}" if c else "NA") for ch, c in zip(TMT10, list(conds) + [""] * 10, strict=False)]


def psm(path: Path, chans, n=300, unlabelled=0.0, purity=0.85, empty=None, ref_shift=0.0, style="Intensity ",
        k_variable_missing=0.0, seed=1, acetyl=0.05):
    """A psm.tsv with FragPipe 24's TMT columns. empty: a channel (index) with almost no signal; ref_shift: log2
    added to channel 0; k_variable_missing: share of K left without TMT (a search with TMT on K variable)."""
    rng = random.Random(seed)
    head = ["Spectrum", "Peptide", "Modified Peptide", "Charge", "Intensity", "Assigned Modifications", "Purity",
            "Is Unique", "Protein", "Gene", *[f"{style}{n}" for _c, n in chans]]
    rows = []
    for i in range(n):
        pep = "".join(rng.choice("ADEFGLMNPSTVWY") for _ in range(8)) + rng.choice(["K", "R", "KAK"])
        mods = []
        r = rng.random()
        if r < acetyl:
            mods.append("N-term(42.0106)")
        elif r >= acetyl + unlabelled:
            mods.append("N-term(229.1629)")
        for j, aa in enumerate(pep, 1):
            if aa == "K" and rng.random() >= k_variable_missing:
                mods.append(f"{j}K(229.1629)")
        base = rng.lognormvariate(13, 1)
        vals = []
        for k in range(len(chans)):
            v = base * 2 ** (rng.gauss(0, 0.2) + (ref_shift if k == 0 else 0))
            if k == empty:
                v = 0 if rng.random() < 0.8 else v / 50
            vals.append(f"{v:.1f}")
        rows.append([f"r.{i}.{i}.2", pep, "", "2", f"{base * 10:.1f}", ", ".join(mods),
                     f"{max(0.05, min(1, rng.gauss(purity, 0.08))):.3f}", "true", f"sp|P{i:05d}|X_HUMAN", f"G{i}",
                     *vals])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join("\t".join(r) for r in [head, *rows]) + "\n", encoding="utf-8")


def _maps(*plexes):
    return {p: dict(_names(k + 1)) for k, p in enumerate(plexes)}


def _codes(res):
    return [c for c, _m in res.problems]


# ---------------------------------------------------------------- reading --


@pytest.mark.parametrize(("mods", "pep", "want"), [
    ("N-term(229.1629), 5K(229.1629)", "PEPTKR", ("label", 1, 1, 0)),
    ("N-term(42.0106), 3K(229.1629)", "AAKR", ("blocked", 1, 1, 0)),
    ("1Q(-17.0265), 4K(229.1629)", "QAAKR", ("blocked", 1, 1, 0)),
    ("4K(229.1629)", "AAAKR", ("free", 1, 1, 0)),
    ("N-term(229.1629)", "AKAKR", ("label", 2, 0, 0)),
    ("N-term(304.2071), 2K(304.2071), 3S(304.2071)", "AKSR", ("label", 1, 1, 1)),
    ("", "AAAR", ("free", 0, 0, 0)),
])
def test_labels_of_one_psm(mods, pep, want):
    assert tmtqc._labels(mods, pep, None) == want


def test_a_good_plex(tmp_path):
    wd = tmp_path / "fragpipe"
    psm(wd / "KL6159A_1" / "psm.tsv", _names())
    res = tmtqc.run(wd, _maps("KL6159A"), PROPS, reference=["126"])
    assert res.found and res.problems == []
    p = res.plexes[0]
    assert (p["name"], p["psms"], p["named"], p["columns"]) == ("KL6159A", 300, 7, "Intensity <sample>")
    ch = {c["channel"]: c for c in p["channels"]}
    assert list(ch) == list(TMT10) and ch["126"]["ref"] and ch["130N"]["na"] and not ch["127N"]["ref"]
    assert ch["127N"]["condition"] == "DMSO" and ch["127N"]["signal_share"] == 1.0
    assert abs(ch["126"]["rel_log2"]) < 0.2 and len(ch["128N"]["log2"]) == 5
    lab = res.labelling
    assert lab["k_fixed"] and lab["blocked"] > 0 and lab["nterm"] == 1.0 and lab["full"] == 1.0
    assert 0.8 < res.purity["median"] < 0.9 and res.purity["below_min"] < 0.02 and res.workflow["quant"] == "MS3"


@pytest.mark.parametrize(("kw", "code"), [
    ({"unlabelled": 0.2}, "TMT_LOW_LABELLING"),
    ({"k_variable_missing": 0.3, "acetyl": 0}, "TMT_LOW_LABELLING"),
    ({"purity": 0.55}, "TMT_LOW_PURITY"),
    ({"empty": 2}, "TMT_CHANNEL_EMPTY"),
    ({"ref_shift": -1.6}, "TMT_REFERENCE_WEAK"),
])
def test_every_issue(tmp_path, kw, code):
    wd = tmp_path / "fragpipe"
    psm(wd / "P_1" / "psm.tsv", _names(), **kw)
    res = tmtqc.run(wd, _maps("P"), PROPS, reference=["126"])
    assert _codes(res) == [code], res.problems
    issue = doctor.check(doctor.Findings(tmt_problems=res.problems))
    assert [i.code for i in issue if i.code == code] == [code] and issue[0].causes and issue[0].fixes


def test_limits_come_from_the_settings(tmp_path):
    wd = tmp_path / "fragpipe"
    psm(wd / "P_1" / "psm.tsv", _names(), unlabelled=0.02)
    assert _codes(tmtqc.run(wd, _maps("P"), PROPS)) == []
    assert _codes(tmtqc.run(wd, _maps("P"), PROPS, limits={"min_labelling": 0.99})) == ["TMT_LOW_LABELLING"]


def test_label_type_mismatch_and_conditions_unknown(tmp_path):
    wd = tmp_path / "fragpipe"
    psm(wd / "P_1" / "psm.tsv", _names())
    res = tmtqc.run(wd, _maps("P"), {**PROPS, "tmtintegrator.channel_num": "TMT-11"})
    assert _codes(res) == ["TMT_LABEL_TYPE_MISMATCH"] and "P's psm.tsv has 10 reporter columns, TMT-11 has 11" in \
        res.problems[0][1]
    other = {"P": {**_maps("P")["P"], "128C": "Other_1_128C"}}
    assert "the map names Other_1_128C that the table does not have" in tmtqc.run(wd, other, PROPS).problems[0][1]
    psm(wd / "KL6161A_1" / "psm.tsv", [(ch, f"KL6161A_{ch}") for ch in TMT10])  # FragPipe's own names
    res = tmtqc.run(wd, {}, PROPS)
    assert "TMT_CONDITIONS_UNKNOWN" in _codes(res) and "KL6161A_126" in dict(res.problems)["TMT_CONDITIONS_UNKNOWN"]


def test_older_tables_without_the_intensity_prefix(tmp_path):
    wd = tmp_path / "fragpipe"
    psm(wd / "P_1" / "psm.tsv", [(c, n) for c, n in _names() if n != "NA"], style="")
    res = tmtqc.run(wd, _maps("P"), PROPS)
    assert res.plexes[0]["columns"].startswith("sample-name columns") and res.plexes[0]["named"] == 7


def test_degraded_paths(tmp_path):
    wd = tmp_path / "fragpipe"
    res = tmtqc.run(wd, {}, {})
    assert not res.found and "no psm.tsv" in res.reason and tmtqc.summary(res)["ran"] is False
    assert tmtqc.report_payload(res) is None and tmtqc.text(res)[0].startswith("no TMT QC")
    # a TMT-Integrator table dropped in by hand: its header is checked
    simulate.tmt_abundance(wd / "tmt-report" / "abundance_gene_MD.tsv", ["S126", "S127N", "DMSO_1_127C"], 1)
    res = tmtqc.run(wd, {}, {})
    assert res.found and res.plexes == [] and res.tables[0]["channels"] == ["S126", "S127N", "DMSO_1_127C"]
    assert "only the tmt-report header was checked" in res.notes[0]
    assert dict(res.problems).get("TMT_CONDITIONS_UNKNOWN")
    # no reporter columns, and a table over the size limit: left unread with a note
    (wd / "X_1").mkdir()
    (wd / "X_1" / "psm.tsv").write_text("Spectrum\tPeptide\nx\ty\n", encoding="utf-8")
    psm(wd / "Y_1" / "psm.tsv", _names())
    res = tmtqc.run(wd, {}, PROPS, max_mb=0.001)
    assert any("no reporter-ion columns" in n for n in res.notes) and any("more than" in n for n in res.notes)


def test_several_plexes_folder_names_and_outputs(tmp_path):
    wd = tmp_path / "e" / "fragpipe"
    psm(wd / "plex_A_1" / "psm.tsv", _names(1), seed=2)    # FragPipe's experiment for plex-A
    psm(wd / "plexB_1" / "psm.tsv", _names(2), seed=3, purity=0.6)
    maps = {"plex-A": dict(_names(1)), "plexB": dict(_names(2))}
    res = tmtqc.run(wd, maps, PROPS)
    assert [p["name"] for p in res.plexes] == ["plex-A", "plexB"] and _codes(res) == ["TMT_LOW_PURITY"]
    assert "plexB (0.6" in res.problems[0][1] and "plex-A" not in res.problems[0][1]
    files = tmtqc.write(res, tmp_path / "e" / "results")
    assert [f.name for f in files] == [names.TMT_QC_FILE, "tmt_qc_plex-A.tsv", "tmt_qc_plexB.tsv"]
    js = json.loads(files[0].read_text(encoding="utf-8"))
    assert js["ran"] and len(js["plexes"]) == 2 and js["plexes_full"][0]["ch"][0]["n"] == "Pool_1_126"
    head = files[1].read_text(encoding="utf-8").splitlines()
    assert head[0].split("\t") == tmtqc.COLUMNS and head[1].startswith("plex-A\t126\tPool_1_126\tPool")
    text = "\n".join(tmtqc.text(res))
    assert "labelling: 100% of 600 PSMs fully labelled" in text and "TMT_LOW_PURITY" in text


def test_run_folder_finds_maps_workflow_and_reference(tmp_path):
    dest = tmp_path / "20260201_Aman_TMT_KL6165-bridge"
    wd = dest / "fragpipe"
    psm(wd / "plexA_1" / "psm.tsv", _names(1))
    (dest / "plexA").mkdir(parents=True)
    (dest / "plexA" / "annotation.txt").write_text(tmtkit.annotation_text(dict(_names(1))), encoding="utf-8")
    (wd / "fragpipe.workflow").write_text("".join(f"{k}={v}\n" for k, v in PROPS.items()), encoding="utf-8")
    rec = {"plan": {"manifest": [{"file": "plexA/KL6165A_TMT_F1.raw", "experiment": "plexA", "bioreplicate": 1}],
                    "overrides": {"tmt": {"reference_channel": "126"}}}}
    (dest / names.STATUS_FILE).write_text(json.dumps(rec), encoding="utf-8")
    res = tmtqc.run_folder(dest)
    assert res.workflow["label"] == "TMT-10" and res.plexes[0]["channels"][0]["ref"]
    maps, src = tmtqc.maps_for(dest, rec, ["plexA"])
    assert src == "annotation files" and maps["plexA"]["126"] == "Pool_1_126"


# --------------------------------------------------- the analysis, report --


@pytest.fixture(scope="module")
def analysed(tmp_path_factory):
    dest = tmp_path_factory.mktemp("tmtqc") / "20260202_Aman_TMT_KL6166"
    wd = dest / "fragpipe"
    psm(wd / "KL6166_1" / "psm.tsv", _names(), unlabelled=0.2)
    sample_names = [n for _c, n in _names() if n != "NA"]
    simulate.tmt_abundance(wd / "tmt-report" / "abundance_gene_MD.tsv", sample_names, 3)
    (wd / "fragpipe.workflow").write_text("".join(f"{k}={v}\n" for k, v in PROPS.items()), encoding="utf-8")
    out = analyze(dest, "TMT", {"enrichment": False}, context={"experiment": "TMT QC"})
    return dest, out, slides.read_payload(out.report)


def test_the_analysis_writes_and_reports_it(analysed):
    dest, out, d = analysed
    a = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert a["tmt_qc"]["ran"] and a["tmt_qc"]["files"][0] == "results/tmt_qc.json"
    assert "TMT_LOW_LABELLING" in [i["code"] for i in a["issues"]]
    assert (dest / "results" / "tmt_qc_KL6166.tsv").is_file()
    html = out.report.read_text(encoding="utf-8")
    assert "<section id='tmt'><h2>TMT plexes and channels</h2>" in html and "id='navtmt'>" in html
    assert "TMT QC: 1 plex(es) read from FragPipe" in html  # the Methods paragraph
    assert d["tmtqc"]["plexes"][0]["ch"][0]["n"] == "Pool_1_126"


def test_the_section_figures_export(analysed):
    _dest, _out, d = analysed
    figs = {n: s for n, _w, s in charts.figures(d, charts.style_from(), ["tmt"])}
    assert list(figs) == ["tmt_channels_KL6166.svg", "tmt_labelling.svg", "tmt_purity.svg"]
    for n, s in figs.items():
        root = ET.fromstring(s.encode("utf-8"))
        assert root.tag == f"{SVG}svg" and not re.search(r"var\(|<style|class=|style=", s), n
        cols = set(re.findall(r'(?:fill|stroke)="([^"]+)"', s)) - {"none"}
        assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in cols), (n, cols)
    texts = [t.text for t in ET.fromstring(figs["tmt_channels_KL6166.svg"]).iter(f"{SVG}text")]
    assert "126 Pool_1_126" in texts and "130N NA" in texts and "TMT channels: plex KL6166" in texts
    assert len([r for r in ET.fromstring(figs["tmt_labelling.svg"]).iter(f"{SVG}rect") if r.get("fill") != "none"]) >= 3
    assert charts.catalog({}, ["tmt"]) == []  # nothing without the section
    # #156: the footer said "hits: dashed: the limit 95% · limma moderated t-test; normalisation …" (cut with "…" on
    # the channel chart), the <title> "TMT labelling efficiency: TMT labelling efficiency"
    for n, s in figs.items():
        root = ET.fromstring(s.encode("utf-8"))
        foot = [t.text or "" for t in root.iter(f"{SVG}text")][-1]
        desc = root.find(f"{SVG}desc").text
        assert "hits:" not in s and "normalisation" not in foot and "Analysis:" not in desc, (n, foot)
        assert not foot.endswith("…"), (n, foot)
    title = {n: ET.fromstring(s.encode("utf-8")).find(f"{SVG}title").text for n, s in figs.items()}
    assert title == {"tmt_channels_KL6166.svg": "TMT channels: plex KL6166", "tmt_labelling.svg":
                     "TMT labelling efficiency", "tmt_purity.svg": "TMT precursor purity"}
    foot = [t.text for t in ET.fromstring(figs["tmt_labelling.svg"]).iter(f"{SVG}text")][-1]
    assert foot.endswith("· dashed: the limit 95%"), foot
    d2 = {**d, "tmtqc": {**d["tmtqc"], "plexes": [{**d["tmtqc"]["plexes"][0], "name": "plexB"}]}}
    figs = {n: s for n, _w, s in charts.figures(d2, charts.style_from(), ["tmt"])}
    texts = [t.text for t in ET.fromstring(figs["tmt_channels_plexB.svg"]).iter(f"{SVG}text")]
    assert "TMT channels: plexB" in texts   # not "plex plexB"


def test_every_tmt_issue_has_help():
    ents = helpdoc.entries()
    assert [c for c in doctor.TMT_ISSUES if f"issue.{c}" not in ents] == []
    assert "report.tmt" in ents and {"glossary.channel", "glossary.purity", "glossary.ms3"} <= set(ents)


# ------------------------------------------------------ TMT-Integrator tables --


@pytest.mark.parametrize(("present", "read", "note"), [
    (["abundance_gene_MD.tsv", "abundance_gene_None.tsv", "ratio_gene_MD.tsv"], "abundance_gene_MD.tsv", ""),
    (["abundance_gene_None.tsv", "ratio_gene_MD.tsv", "abundance_peptide_MD.tsv"], "abundance_gene_None.tsv",
     "did not normalise"),
    (["ratio_gene_MD.tsv", "abundance_peptide_MD.tsv"], "ratio_gene_MD.tsv", "log2 ratios to the reference"),
    (["abundance_protein_GN.tsv"], "abundance_protein_GN.tsv", "variance-scaled"),
    (["abundance_single-site_MD.tsv"], "abundance_single-site_MD.tsv", "no gene or protein table"),
])
def test_tmt_integrator_tables_are_read_in_order(tmp_path, present, read, note):
    wd = tmp_path / "fragpipe"
    for n in present:
        simulate.tmt_abundance(wd / "tmt-report" / n, ["DMSO_1_126", "DMSO_1_127N", "Drug_1_127C", "Drug_1_128N"], 2)
    from ionomos.downstream import _find, load_quantities
    assert _find(wd, *quant.TMT_TABLES).name == read
    m, _files, notes = load_quantities("TMT", wd, tmp_path / "results", None)
    assert m is not None and m.meta["ratio_to_reference"] and len(m.samples) == 4
    assert (note in " ".join(notes)) if note else not any(n.startswith("read ") for n in notes)


def test_the_analysis_off_path_still_writes_the_tmt_qc(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    from ionomos.config import load
    from ionomos.intake import intake
    from ionomos.ledger import Ledger
    from ionomos.worker import Worker

    cfg_path = testbed.init(tmp_path / "bed")
    text = cfg_path.read_text(encoding="utf-8") + "analysis:\n  enabled: false\n"
    cfg_path.write_text(text, encoding="utf-8")
    cfg = load(cfg_path)
    led = Ledger(cfg.database)
    assert intake(testbed.drop(tmp_path / "bed", "tmt_poor_label"), cfg, led).value == "queued"
    Worker(cfg, led).run_once()
    job = led.list()[-1]
    dest = Path(job.dest_dir)
    assert job.status == "done", job.reason
    assert (dest / "results" / names.TMT_QC_FILE).is_file() and not (dest / "results" / "report.html").exists()
    assert "TMT QC: 1 of 1 plex(es) are labelled below 95%" in job.reason
    assert postprocess._tmt_qc_only(tmp_path / "nothing_here", {}, []) == []


@pytest.mark.parametrize(("given", "said"), [
    ({}, "conditions were taken from the text before the first '_'"),
    ({"A1-00004_T": "Tumor", "A1-00005_N": "NAT", "A1-00006_T": "Tumor", "A1-00007_N": "NAT"}, None),
    ({"A1-00004_T": "Tumor", "A1-00005_N": "NAT"}, "the conditions of 2 of 4 samples (e.g. A1-00006_T)"),
])
def test_the_name_note_only_when_a_condition_was_guessed(tmp_path, given, said):
    """#158: "TMT sample names don't follow condition_1_channel ... conditions were taken from the text before the
    first '_'" was printed although experiment.yaml's sample_conditions gave every sample its condition."""
    dest = tmp_path / "e"
    simulate.tmt_abundance(dest / "fragpipe" / "tmt-report" / "abundance_gene_MD.tsv",
                           ["A1-00004_T", "A1-00005_N", "A1-00006_T", "A1-00007_N"], 3, n_genes=60)
    out = analyze(dest, "TMT", {"enrichment": False, "sample_conditions": given})
    a = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    everything = "\n".join(out.warnings) + json.dumps(a)
    if said is None:
        assert "follow condition_1_channel" not in everything
        assert set(a["samples"].values()) == {"Tumor", "NAT"}
    else:
        assert said in everything
