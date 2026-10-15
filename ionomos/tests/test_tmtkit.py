"""TMT before the search (tmtkit.py, D91): label types, channel names, FragPipe's rules and Ionomos' own flags, the
plan for a drop (experiment.yaml, the user's own annotation.txt, <plex>/*.raw folders), suggestions, the CLI, and
the pre-search path through fragpipe.prepare and intake. Names are the lab's (reference/pc-inventory: KL6159A
9plex TMT, KL6283A_TMTPD_Comp, the R script's 22Rv1_AM2012_PD)."""
import json
import re
from pathlib import Path

import pytest
import yaml

from ionomos import cli, fragpipe, names, testbed, tmtkit
from ionomos.config import load
from ionomos.intake import intake
from ionomos.ledger import Job, Ledger
from ionomos.manifest import OverridesError, parse_overrides
from ionomos.worker import Worker

TMT10 = tmtkit.LABEL_TYPES["TMT-10"].channels
TMT16 = tmtkit.LABEL_TYPES["TMT-16"].channels
PROPS10 = {"tmtintegrator.channel_num": "TMT-10", "tmtintegrator.add_Ref": "1", "tmtintegrator.ref_tag": "Bridge"}


def _map(conds, channels=TMT10, plex=1):
    """{channel: name} from a condition per channel ('' = NA)."""
    return {ch: (f"{c}_{plex}_{ch}" if c else "NA") for ch, c in zip(channels, conds, strict=False)} | \
        {ch: "NA" for ch in channels[len(conds):]}


GOOD = _map(["DMSO", "DMSO", "DMSO", "Drug", "Drug", "Drug"])


# ------------------------------------------------------------- label types --


@pytest.mark.parametrize(("text", "name"), [
    ("TMT-10", "TMT-10"), ("TMT10", "TMT-10"), ("tmt10plex", "TMT-10"), ("Tmt16", "TMT-16"), ("TMTpro", "TMT-16"),
    ("TMTpro-18", "TMT-18"), ("tmtpro 16", "TMT-16"), ("11", "TMT-11"), ("TMT-6", "TMT-6"), ("TMT-35", "TMT-35"),
    ("iTRAQ-8", "iTRAQ-8"), ("", None), (None, None), ("TMT-12", None), ("iTRAQ4plex", "iTRAQ-4"),
    ("itraq-8", "iTRAQ-8"), ("iodoTMT-6", "iodoTMT-6"), ("TMT2", "TMT-2"), ("DiLeu-12", None), ("iTRAQ-6", None),
])
def test_label_types_any_spelling(text, name):
    lt = tmtkit.label_type(text)
    assert (lt.name if lt else None) == name


def test_label_types_match_fragpipe():
    """QuantLabel.java (FragPipe 24.0, LABELS and labelModMap): the channels in the kit's order, the masses."""
    lts = tmtkit.LABEL_TYPES
    assert [lt.n for lt in lts.values()][:5] == [6, 10, 11, 16, 18]   # the lab's kits first
    assert lts["TMT-6"].channels == ("126", "127N", "128C", "129N", "130C", "131N")
    assert lts["TMT-18"].channels[-3:] == ("134N", "134C", "135N") and lts["TMT-16"].channels == lts["TMT-18"].channels[:16]
    assert lts["TMT-35"].n == 35 and lts["TMT-35"].channels[:18] == lts["TMT-18"].channels
    assert lts["TMT-35"].channels[18:21] == ("127D", "128ND", "128CD") and lts["TMT-35"].channels[-1] == "135CD"
    assert lts["iTRAQ-4"].channels == ("114", "115", "116", "117")
    assert lts["iTRAQ-8"].channels == ("113", "114", "115", "116", "117", "118", "119", "121")
    assert lts["TMT-2"].channels == ("126", "127C") and lts["TMT-0"].channels == ("126",)
    assert {k: v.mass for k, v in lts.items()} == {
        "TMT-6": 229.1629, "TMT-10": 229.1629, "TMT-11": 229.1629, "TMT-16": 304.2071, "TMT-18": 304.2071,
        "TMT-35": 304.2071, "TMT-2": 225.1558, "TMT-0": 224.1525, "iTRAQ-4": 144.1021, "iTRAQ-8": 304.2054,
        "iodoTMT-6": 329.2266}
    assert [lt.reagent for lt in lts.values()][:5] == ["TMT", "TMT", "TMT", "TMTpro", "TMTpro"]
    assert set(tmtkit.TESTED) <= set(lts) and not set(tmtkit.OTHER_FRAGPIPE_LABELS) & set(lts)
    from ionomos import fake_fragpipe
    assert fake_fragpipe.TMT_CHANNELS == {k: v.channels for k, v in lts.items()}  # one source


@pytest.mark.parametrize(("ch", "key"), [
    ("126", "126"), ("127n", "127N"), ("TMT127N", "127N"), ("128nd", "128ND"), ("135CD", "135CD"),
    ("127D", "127D"), ("114", "114"), ("115a", ""), ("x", ""),
])
def test_channel_keys(ch, key):
    assert tmtkit.channel_key(ch) == key


@pytest.mark.parametrize("text", [
    "tmt:\n  channels:\n    '126': DMSO_1_126\n    '126': Drug_1_126\n",
    "tmt:\n  channels: {126: DMSO_1_126, '126': Drug_1_126}\n",
    "tmt:\n  plexes:\n    a:\n      channels: {126: A_1_126, 126: B_1_126}\n",
])
def test_a_channel_written_twice_in_experiment_yaml_is_refused(tmp_path, text):
    """#156: YAML kept the last of two equal keys, so the first name vanished and TMT_CHANNEL_TWICE never fired."""
    from ionomos.manifest import load_overrides

    (tmp_path / "experiment.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(OverridesError, match="126 is written twice"):
        load_overrides(tmp_path)


def test_two_spellings_of_one_channel_are_refused():
    assert tmtkit.check_block({"channels": {"127n": "A_1_127N", "127N": "B_1_127N"}}) == [
        "tmt: channel 127N is written twice (as '127n', '127N'); list every channel once"]
    assert tmtkit.repeated_keys("tmt: [unclosed") == [] and tmtkit.repeated_keys("files: {a: 1, a: 2}") == []


def test_a_reference_that_is_not_a_channel_of_the_kit_is_said(tmp_path):
    """#156: reference_channel 135N on TMT-10 gave no flag with one plex and a misleading one with two; a reference
    on an NA channel of a single plex was taken silently; the plex line still named a pool as the reference."""
    one = tmtkit.validate({"p": GOOD}, "TMT-10", reference=["135N"])
    assert _codes(one) == ["TMT_REFERENCE_UNKNOWN"]
    assert one[0].text.startswith("reference 135N is not a channel of TMT-10 (126, 127N")
    two = tmtkit.validate({"a": _map(["Pool", "DMSO", "Drug"]), "b": _map(["Pool", "DMSO", "Drug"], plex=2)},
                          "TMT-10", reference=["135N"])
    assert "TMT_REFERENCE_UNKNOWN" in _codes(two) and "TMT_REFERENCE_MISSING" not in _codes(two)
    na = tmtkit.validate({"p": GOOD}, "TMT-10", reference=["131N"])
    assert _codes(na) == ["TMT_REFERENCE_MISSING"] and "131N is NA" in na[0].text
    assert tmtkit.validate({"p": GOOD}, "TMT-10", reference=["126"]) == []
    assert tmtkit.validate({"p": GOOD}, "TMT-10", reference=["Pool_1_126"]) == []  # a name: not a channel
    dest = tmp_path / "x"
    dest.mkdir()
    pooled = _map(["Pool", "DMSO", "Drug"])
    tp = tmtkit.plan(dest, {"tmt": {"tag": "TMT-10", "channels": pooled, "reference_channel": "135N"}}, PROPS10,
                     [(dest / "A_F1.raw", "A", 1, "DDA")])
    assert tp.plexes[0].reference == [] and "reference" not in tp.summary()[1]


def test_a_name_twice_in_one_plex_says_so():
    """#156: TMT_DUPLICATE_NAME for a name repeated inside one plex said "(plex KLX and KLX)"."""
    f = [x for x in tmtkit.validate({"KLX": {**GOOD, "131N": "DMSO_1_126"}}, "TMT-10")
         if x.code == "TMT_DUPLICATE_NAME"]
    assert f[0].text == "sample name DMSO_1_126 is used twice in plex KLX; FragPipe needs every name once"


def test_a_plex_of_na_only_stops_before_the_search():
    """#156: TMT_ALL_NA was a warning, so the job was searched and the fake FragPipe crashed (the wrong diagnosis
    followed); a plex with no sample can't be right."""
    from ionomos.downstream import simulate

    f = [x for x in tmtkit.validate({"p": {ch: "NA" for ch in TMT10}}, "TMT-10") if x.code == "TMT_ALL_NA"]
    assert f[0].severity == "error"
    assert simulate._control([]) == ""


def test_tmt_plan_says_a_not_utf8_experiment_yaml_once(tmp_path, caplog, capsys):
    """#156: `tmt plan` on an experiment.yaml saved as Windows-1252 said "is not UTF-8 ... Save it as UTF-8" twice."""
    drop = tmp_path / "20260126_Aman_TMT_KL6159A-9plex"
    drop.mkdir()
    (drop / "KL6159A_TMT_F1.raw").write_bytes(b"\0" * 8)
    text = yaml.safe_dump({"notes": "24 h \u2013 1 \u00b5M", "tmt": {"tag": "TMT-10", "channels": GOOD}},
                          allow_unicode=True)
    (drop / "experiment.yaml").write_bytes(text.encode("cp1252"))
    cfg = testbed.init(tmp_path / "bed")
    with caplog.at_level("WARNING"):
        assert cli.main(["--config", str(cfg), "tmt", "plan", str(drop)]) == 0
    capsys.readouterr()
    said = [r for r in caplog.records if "is not UTF-8" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in said]


def test_fragpipes_other_label_types_are_passed_on(tmp_path):
    """#154: a tag FragPipe knows but Ionomos has no channels for is accepted and checked by FragPipe's rules only;
    the tag is compared with the workflow's label by name."""
    assert tmtkit.fragpipe_label("dileu12") == "DiLeu-12" and tmtkit.fragpipe_label("TMT-12") == ""
    assert tmtkit.check_block({"tag": "DiLeu-12", "channels": {"115a": "A_1_115a"}}) == []
    dest = tmp_path / "x"
    dest.mkdir()
    lines = [(dest / "A_F1.raw", "A", 1, "DDA")]
    ch = {c: f"A_1_{c}" for c in ("115a", "115b", "116a", "116b", "116c", "117a", "117b", "117c", "118a", "118b",
                                  "118c", "118d")}
    tp = tmtkit.plan(dest, {"tmt": {"tag": "DiLeu-12", "channels": ch}}, {"tmtintegrator.channel_num": "DiLeu-12"},
                     lines)
    assert tp.errors() == [] and tp.label == "DiLeu-12" and "TMT_LABEL_UNKNOWN" in _codes(tp.flags)
    assert tp.annotations["annotation.txt"].splitlines()[0] == "115a\tA_1_115a"
    tp = tmtkit.plan(dest, {"tmt": {"tag": "DiLeu-12", "channels": ch}}, PROPS10, lines)
    assert [f.code for f in tp.errors()][0] == "TMT_TAG_MISMATCH"
    assert "change tag: to TMT-10; if they are DiLeu-12, name a DiLeu-12 workflow" in tp.error_text()
    tp = tmtkit.plan(dest, {"tmt": {"tag": "DiLeu-12", "channels": dict(list(ch.items())[:3])}},
                     {"tmtintegrator.channel_num": "DiLeu-12"}, lines)
    assert "TMT_CHANNEL_COUNT" in _codes(tp.errors())   # FragPipe's own rule: the count from the name


@pytest.mark.parametrize(("tag", "channels", "wf"), [
    ("iTRAQ-4", {"114": "A_1_114", "115": "A_1_115", "116": "B_1_116", "117": "B_1_117"}, "iTRAQ-4"),
    ("TMT-35", None, "TMT-35"),
    ("TMT-2", {"126": "A_1_126", "127C": "B_1_127C"}, "TMT-2"),
])
def test_stock_itraq_and_tmt35_maps_are_planned(tmp_path, tag, channels, wf):
    """#154: FragPipe 24's stock iTRAQ4.workflow (channel_num=iTRAQ-4) and TMT35.workflow were refused at intake."""
    channels = channels or tmtkit.suggest_channels(tag, ["A", "B"], 10, "126")
    assert parse_overrides({"tmt": {"tag": tag, "channels": channels}}).tmt["tag"] == tag
    dest = tmp_path / "x"
    dest.mkdir()
    tp = tmtkit.plan(dest, {"tmt": {"tag": tag, "channels": channels}}, {"tmtintegrator.channel_num": wf},
                     [(dest / "A_F1.raw", "A", 1, "DDA")])
    assert tp.errors() == [] and tp.label == wf and "TMT_LABEL_UNKNOWN" not in _codes(tp.flags)
    assert tp.annotations["annotation.txt"] == tmtkit.annotation_text(channels)
    tp = tmtkit.plan(dest, {"tmt": {"tag": tag, "channels": channels}}, PROPS10, [(dest / "A_F1.raw", "A", 1, "DDA")])
    assert "TMT_TAG_MISMATCH" in _codes(tp.errors())
    bad = dict(channels) | {"131C": "C_1_131C"} if wf != "TMT-35" else {**channels, "999": "C_1_999"}
    tp = tmtkit.plan(dest, {"tmt": {"tag": tag, "channels": bad}}, {"tmtintegrator.channel_num": wf},
                     [(dest / "A_F1.raw", "A", 1, "DDA")])
    assert "TMT_UNKNOWN_CHANNEL" in _codes(tp.errors())


def test_suggest_and_block_keep_fragpipes_label_names(capsys):
    """#154: `tmt suggest --tag iTRAQ-4` / `TMT-35` were refused; the editor turned an iTRAQ map into TMT-10."""
    m = tmtkit.suggest_channels("itraq4plex", ["DMSO", "Drug"], 2)
    assert m == {"114": "DMSO_1_114", "115": "DMSO_1_115", "116": "Drug_1_116", "117": "Drug_1_117"}
    assert tmtkit.suggest_channels("TMT-35", ["A"], 2, "128ND")["128ND"] == "Pool_1_128ND"
    assert tmtkit.to_overrides_block(m, "iTRAQ4")["tag"] == "iTRAQ-4"
    assert tmtkit.to_overrides_block({"115a": "x"}, "DiLeu-12")["tag"] == "DiLeu-12"
    assert cli.main(["tmt", "suggest", "--tag", "iTRAQ-4", "--conditions", "DMSO,Drug", "--replicates", "2"]) == 0
    block = yaml.safe_load(capsys.readouterr().out.split("\n", 1)[1])["tmt"]
    assert block["tag"] == "iTRAQ-4" and list(block["channels"]) == ["114", "115", "116", "117"]
    assert cli.main(["tmt", "suggest", "--tag", "TMT-35", "--conditions", "A,B", "--replicates", "17"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize(("name", "cond", "plex", "ch", "lab"), [
    ("DMSO_1_126", "DMSO", 1, "126", True), ("Drug_1_128N", "Drug", 1, "128N", True),
    ("22Rv1_1_131C", "22Rv1", 1, "131C", True), ("DMSO_126", "DMSO", None, "126", False),
    ("DMSO-1-126", "DMSO", 1, "126", False), ("126_DMSO", "DMSO", None, "126", False),
    ("Pool_2_126", "Pool", 2, "126", False), ("KL6159A_127n", "KL6159A", None, "127N", False),
    ("ProbeComp_1_128C", "ProbeComp", 1, "128C", True), ("Probe_Comp_1_129N", "Probe", 1, "129N", False),
    ("DMSO", "DMSO", None, "", False),
])
def test_sample_names(name, cond, plex, ch, lab):
    p = tmtkit.parse_sample_name(name)
    assert (p.condition, p.plex, p.channel, p.lab_pattern) == (cond, plex, ch, lab)


def test_na_and_empty_names_are_no_sample():
    assert tmtkit.parse_sample_name("NA") is None and tmtkit.parse_sample_name("na") is None
    assert tmtkit.parse_sample_name("") is None and tmtkit.parse_sample_name(None) is None


# ---------------------------------------------------------- annotation files --


def test_annotation_text_and_read(tmp_path):
    text = tmtkit.annotation_text(GOOD)
    assert text.splitlines()[0] == "126\tDMSO_1_126" and text.count("\tNA\n") == 4
    p = tmp_path / "plex1_annotation.txt"
    p.write_bytes(("﻿" + text.replace("\t", "  ") + "\n\n").encode("utf-8"))  # BOM, spaces, blank lines
    assert tmtkit.read_annotation(p) == list(GOOD.items())
    p.write_text("126 DMSO rep 1\n", encoding="utf-8")
    with pytest.raises(tmtkit.TmtError, match="Invalid line in annotation file plex1_annotation.txt: 126 DMSO rep 1"):
        tmtkit.read_annotation(p)
    p.write_bytes("126\tDMSO_1_126\n127N\tDrug_1_127N\n".encode("utf-16"))
    assert tmtkit.read_annotation(p) == [("126", "DMSO_1_126"), ("127N", "Drug_1_127N")]


def test_channel_map_from_folder_reads_and_never_writes(tmp_path):
    d = tmp_path / "20260127_Aman_TMT_KL6160-2plex"
    (d / "plexA").mkdir(parents=True)
    (d / "plexB").mkdir()
    (d / "plexA" / "annotation.txt").write_text(tmtkit.annotation_text(GOOD), encoding="utf-8")
    (d / "plexB" / "a_annotation.txt").write_text("126 x\n", encoding="utf-8")
    (d / "plexB" / "b_annotation.txt").write_text("126 y\n", encoding="utf-8")
    (d / "annotation.txt").write_text("126 two words here\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in d.rglob("*") if p.is_file()}
    got = tmtkit.channel_map_from_folder(d)
    assert got["plexA"].file == "annotation.txt" and got["plexA"].channels == GOOD
    assert got["plexB"].file == "" and got["plexB"].files == ["a_annotation.txt", "b_annotation.txt"]
    assert got[""].problem.startswith("Invalid line")
    assert {p: p.read_bytes() for p in d.rglob("*") if p.is_file()} == before


# ----------------------------------------------------------------- validate --


def _codes(flags):
    return sorted({f.code for f in flags})


@pytest.mark.parametrize(("maps", "label", "kw", "codes"), [
    ({"p1": GOOD}, "TMT-10", {}, []),
    ({"p1": dict(list(GOOD.items())[:6])}, "TMT-10", {}, ["TMT_CHANNEL_COUNT"]),
    ({"p1": {**GOOD, "126": "DMSO rep 1"}}, "TMT-10", {}, ["TMT_BAD_LINE"]),
    ({"p1": {**GOOD, "131N": ""}}, "TMT-10", {}, ["TMT_BAD_LINE"]),
    ({"p1": GOOD, "p2": GOOD}, "TMT-10", {}, ["TMT_DUPLICATE_NAME", "TMT_NO_BRIDGE"]),
    ({"p1": GOOD}, "TMT-10", {"ref_tag": "Pool", "add_ref": "-1"}, ["TMT_NO_REFERENCE"]),
    ({"p1": {**GOOD, "129C": "Pool_1_129C"}}, "TMT-10", {"ref_tag": "Pool", "add_ref": "-1"}, []),
    ({"p1": GOOD}, "TMT-10", {"ref_tag": "", "add_ref": "-1"}, ["TMT_REF_TAG_EMPTY"]),
    ({"p1": {**{k: v for k, v in GOOD.items() if k != "131N"}, "131C": "NA"}}, "TMT-10", {}, ["TMT_UNKNOWN_CHANNEL"]),
    ({"p1": dict(reversed(list(GOOD.items())))}, "TMT-10", {}, ["TMT_CHANNEL_ORDER"]),
    ({"p1": {**GOOD, "127N": "DMSO_1_126"}}, "TMT-10", {}, ["TMT_DUPLICATE_NAME", "TMT_NAME_CHANNEL"]),
    ({"p1": {**GOOD, "127N": "DMSO_1_128N"}}, "TMT-10", {}, ["TMT_NAME_CHANNEL"]),
    ({"p1": _map(["DMSO", "DMSO", "Drug"])}, "TMT-10", {}, ["TMT_ONE_CHANNEL"]),
    ({"p1": {ch: "NA" for ch in TMT10}}, "TMT-10", {}, ["TMT_ALL_NA"]),
    ({"p1": _map(["DMSO", "DMSO", "Drug", "Drug"], TMT16)}, "TMT-16", {}, []),
    ({"p1": {ch: f"KL6159A_{ch}" for ch in TMT10}}, "TMT-10", {}, ["TMT_NAME_PATTERN"]),
    ({"p1": _map(["Pool", "DMSO", "Drug", "DMSO", "Drug"], plex=1),
      "p2": _map(["DMSO", "Drug", "Pool", "DMSO", "Drug"], plex=2)}, "TMT-10", {"reference": ["126"]},
     ["TMT_REFERENCE_MISSING"]),  # #155: with several plexes condition_2_channel is kept (no pattern note)
    ({"p1": _map(["Pool", "DMSO", "Drug", "DMSO", "Drug"], plex=1),
      "p2": _map(["", "Drug", "Pool", "DMSO", "Drug"], plex=2)}, "TMT-10", {"reference": ["126"]},
     ["TMT_REFERENCE_MISSING"]),
    ({"p1": _map(["Pool", "DMSO", "Drug", "DMSO", "Drug"], plex=1),
      "p2": _map(["Pool", "DMSO", "Drug", "DMSO", "Drug"], plex=2)}, "TMT-10", {"reference": ["126"]},
     []),
])
def test_validate(maps, label, kw, codes):
    assert _codes(tmtkit.validate(maps, label, **kw)) == codes


def test_fragpipes_rules_keep_their_words():
    """The JobError texts tests and people search for (D59 item 4): unchanged since 0.12."""
    rows = {"PLEX": tmtkit.annotation_rows("126\tDMSO_1\n127N\tDrug_1\n")}
    assert [f.text for f in tmtkit.fragpipe_problems(rows, "TMT-10")] == [
        "plex PLEX lists 2 channel(s) but the workflow's label type is TMT-10 (10); list every channel, with NA for "
        "the unused ones"]
    assert fragpipe.annotation_problems({"PLEX": "126\tDMSO rep 1\n"}, "TMT-10")[0].startswith(
        "plex PLEX: a channel needs one sample name without spaces")


# -------------------------------------------------------------------- plan --


def _plan(tmp_path, tmt=None, props=PROPS10, layout=None, user=None, workflow="TMT10-MS3.workflow"):
    """A filed experiment folder: layout {relative raw path: experiment}; user {relative path: text}."""
    dest = tmp_path / "20260126_Aman_TMT_KL6159A-9plex"
    layout = layout or {"KL6159A_TMT_F1.raw": "KL6159A", "KL6159A_TMT_F2.raw": "KL6159A"}
    for f in layout:
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        (dest / f).write_bytes(b"\0" * 8)
    for f, text in (user or {}).items():
        (dest / f).write_text(text, encoding="utf-8")
    lines = [{"file": f, "experiment": e, "bioreplicate": 1} for f, e in layout.items()]
    return dest, tmtkit.plan(dest, {"tmt": tmt or {}}, props, lines, None, workflow)


def test_plan_one_plex_from_experiment_yaml(tmp_path):
    shuffled = dict(sorted(GOOD.items()))  # YAML dumped with sorted keys
    dest, tp = _plan(tmp_path, {"tag": "TMT-10", "channels": shuffled})
    assert tp.errors() == [] and tp.warnings() == []
    assert list(tp.annotations) == ["annotation.txt"]
    assert tp.annotations["annotation.txt"] == tmtkit.annotation_text(GOOD)  # the kit's order
    p = tp.plexes[0]
    assert (p.name, p.files, p.source, p.target, p.na) == ("KL6159A", 2, "experiment.yaml", "annotation.txt",
                                                            ["129C", "130N", "130C", "131N"])
    assert p.conditions == {"DMSO": 3, "Drug": 3}
    text = "\n".join(tp.summary())
    assert "TMT-10 (TMT, 10 channels), from the workflow" in text and "Ionomos writes annotation.txt" in text
    assert not list(dest.glob("*annotation.txt"))  # a plan writes nothing


def test_plan_tag_must_be_the_workflows_label_type(tmp_path):
    _dest, tp = _plan(tmp_path, {"tag": "TMT-16", "channels": GOOD})
    assert [f.code for f in tp.errors()] == ["TMT_TAG_MISMATCH"]
    msg = tp.error_text()
    assert "experiment.yaml says tag: TMT-16 but workflow TMT10-MS3.workflow is set to TMT-10" in msg
    assert "change tag: to TMT-10" in msg and "name a TMT-16 workflow with workflow:" in msg


def test_plan_without_any_map(tmp_path):
    _dest, tp = _plan(tmp_path)
    assert [f.code for f in tp.warnings()] == ["TMT_NO_MAP"] and tp.annotations == {}
    assert "the report will not know your conditions" in tp.warnings()[0].text


def test_plan_reads_the_users_own_annotation_and_never_writes_it(tmp_path):
    own = tmtkit.annotation_text(_map(["Veh", "Veh", "Cmpd", "Cmpd"]))
    dest, tp = _plan(tmp_path, user={"KL6159A_annotation.txt": own})
    assert tp.annotations == {} and tp.errors() == [] and tp.warnings() == []
    assert tp.plexes[0].source == "KL6159A_annotation.txt" and tp.plexes[0].conditions == {"Veh": 2, "Cmpd": 2}
    # beside experiment.yaml's map it wins (FragPipe reads it) and is said
    _dest, tp = _plan(tmp_path, {"channels": GOOD}, user={"KL6159A_annotation.txt": own})
    assert [f.code for f in tp.warnings()] == ["TMT_ANNOTATION_DIFFERS"] and tp.annotations == {}
    # a user's file FragPipe would stop on fails the job, named
    _dest, tp = _plan(tmp_path, user={"KL6159A_annotation.txt": "126 DMSO rep 1\n"})
    assert tp.error_text().startswith("KL6159A_annotation.txt: plex KL6159A: a channel needs one sample name")
    assert (dest / "KL6159A_annotation.txt").read_text(encoding="utf-8") == "126 DMSO rep 1\n"


def test_plan_plex_folders_and_a_bridge(tmp_path):
    layout = {"plexA/KL6160A_TMT_F1.raw": "plexA", "plexA/KL6160A_TMT_F2.raw": "plexA",
              "plexB/KL6160B_TMT_F1.raw": "plexB"}
    maps = {"plexA": {"channels": _map(["Pool", "DMSO", "DMSO", "Drug", "Drug"], plex=1)},
            "plexB": {"channels": _map(["DMSO", "DMSO", "Drug", "Drug"], plex=2)}}
    dest, tp = _plan(tmp_path, {"tag": "TMT-10", "reference_channel": 126, "plexes": maps}, layout=layout)
    assert sorted(tp.annotations) == sorted(str(dest / p / "annotation.txt") for p in ("plexA", "plexB"))
    assert _codes(tp.warnings()) == ["TMT_FRACTIONS_DIFFER", "TMT_REFERENCE_MISSING"]
    assert tp.plexes[0].reference == ["126 (Pool_1_126)"] and tp.plexes[1].reference == ["126 (DMSO_2_126)"]
    # a plex's own reference wins over reference_channel
    maps["plexB"]["reference"] = "127N"
    maps["plexB"]["channels"]["127N"] = "Pool_2_127N"
    _dest, tp = _plan(tmp_path, {"tag": "TMT-10", "reference_channel": 126, "plexes": maps}, layout=layout)
    assert "TMT_REFERENCE_MISSING" not in _codes(tp.flags)


def test_plan_plexes_sharing_a_folder(tmp_path):
    layout = {"KL6161A_TMT_F1.raw": "KL6161A", "KL6161B_TMT_F1.raw": "KL6161B"}
    _dest, tp = _plan(tmp_path, layout=layout)
    assert [f.code for f in tp.warnings()] == ["TMT_NO_MAP", "TMT_SHARED_FOLDER"]
    _dest, tp = _plan(tmp_path, layout=layout, user={"annotation.txt": tmtkit.annotation_text(GOOD)})
    assert [f.code for f in tp.errors()] == ["TMT_SHARED_FILE"] and "repeated sample names" in tp.error_text()


def test_plan_bioreplicate_and_unknown_label(tmp_path):
    dest = tmp_path / "x"
    dest.mkdir()
    lines = [(dest / "A_F1.raw", "A", 2, "DDA")]
    tp = tmtkit.plan(dest, {"tmt": {"channels": GOOD}}, {"tmtintegrator.channel_num": "DiLeu-12"}, lines)
    assert {"TMT_BIOREPLICATE", "TMT_LABEL_UNKNOWN"} <= set(_codes(tp.flags))
    tp = tmtkit.plan(dest, {"tmt": {"channels": GOOD}}, {"x": "1"}, lines)
    assert "TMT_LABEL_UNSET" in _codes(tp.flags)


@pytest.mark.parametrize(("block", "match"), [
    ({"tag": "TMT-12", "channels": GOOD}, "tag 'TMT-12' is not a label type FragPipe knows; use one of TMT-6"),
    ({"channels": GOOD, "plex": 1}, r"unknown key\(s\) plex"),
    ({"channels": GOOD, "plexes": {"a": {"channels": GOOD}}}, "either channels"),
    ({"plexes": {"a": {"channels": {}}}}, "plex 'a' needs a non-empty channels mapping"),
    ({"plexes": {"a": {"channels": GOOD, "refrence": 126}}}, r"plex 'a': unknown key\(s\) refrence"),
    ({"tag": "TMT-10"}, "needs either channels"),
])
def test_experiment_yaml_tmt_block_is_checked(block, match):
    with pytest.raises(OverridesError, match=match):
        parse_overrides({"tmt": block})


def test_experiment_yaml_tmt_block_forms_that_are_fine():
    for block in ({"tag": "TMTpro-16", "channels": _map(["A", "B"], TMT16)}, {"reference_channel": [126, "127N"]},
                  {"plexes": {"a": {"channels": GOOD, "reference": "126"}}, "reference_channel": 126}):
        assert parse_overrides({"tmt": block}).tmt == block


# ----------------------------------------------------------------- suggest --


def test_suggest_channels_and_the_block():
    m = tmtkit.suggest_channels("TMT-16", ["DMSO", "Drug"], 3, reference="126")
    assert list(m) == list(TMT16) and m["126"] == "Pool_1_126" and m["127N"] == "DMSO_1_127N"
    assert [n for n in m.values() if n != "NA"][-1] == "Drug_1_129C" and list(m.values()).count("NA") == 9
    assert tmtkit.validate({"p": m}, "TMT-16") == []
    m2 = tmtkit.suggest_channels("TMT-10", ["DMSO", "Drug"], {"DMSO": 2, "Drug": 4}, plex=2)
    assert list(m2.values())[:6] == ["DMSO_2_126", "DMSO_2_127N", "Drug_2_127C", "Drug_2_128N", "Drug_2_128C",
                                     "Drug_2_129N"]
    block = tmtkit.to_overrides_block({"p": m}, "TMTpro", "126")
    assert block["tag"] == "TMT-16" and block["reference_channel"] == "126" and block["channels"] == m
    assert parse_overrides({"tmt": block}).tmt == block
    two = tmtkit.to_overrides_block({"a": m, "b": m2}, "TMT-10")
    assert set(two["plexes"]) == {"a", "b"}
    with pytest.raises(tmtkit.TmtError, match="TMT-10 has 10 channels; 4 \\+ 4 \\+ 4 replicates \\+ 1 reference need 13"):
        tmtkit.suggest_channels("TMT-10", ["A", "B", "C"], 4, reference="126")
    with pytest.raises(tmtkit.TmtError, match="letters and digits only"):
        tmtkit.suggest_channels("TMT-10", ["Probe_Comp"], 2)
    with pytest.raises(tmtkit.TmtError, match="131C is not a channel of TMT-10"):
        tmtkit.suggest_channels("TMT-10", ["A"], 2, reference="131C")


# --------------------------------------------------------------------- cli --


def test_cli_suggest_plan_annotation(tmp_path, capsys):
    assert cli.main(["tmt", "suggest", "--tag", "TMT-10", "--conditions", "DMSO,Drug", "--replicates", "3",
                     "--reference", "126"]) == 0
    out = capsys.readouterr().out
    block = yaml.safe_load(out)["tmt"]
    assert block["tag"] == "TMT-10" and block["channels"]["127N"] == "DMSO_1_127N"
    assert cli.main(["tmt", "suggest", "--tag", "TMT-10", "--conditions", "A,B,C", "--replicates", "4"]) == 2
    assert "need 12" in capsys.readouterr().out

    drop = tmp_path / "20260126_Aman_TMT_KL6159A-9plex"
    drop.mkdir()
    for i in (1, 2):
        (drop / f"KL6159A_TMT_F{i}.raw").write_bytes(b"\0" * 8)
    (drop / "experiment.yaml").write_text(yaml.safe_dump({"tmt": {"tag": "TMT-10", "channels": GOOD}}),
                                          encoding="utf-8")
    cfg = testbed.init(tmp_path / "bed")
    assert cli.main(["--config", str(cfg), "tmt", "plan", str(drop)]) == 0
    out = capsys.readouterr().out
    assert "plex KL6159A (top level, 2 files): 6 channels named (DMSO 3, Drug 3), 4 NA" in out
    assert cli.main(["--config", str(cfg), "tmt", "annotation", str(drop)]) == 0
    assert "126\tDMSO_1_126" in capsys.readouterr().out and not (drop / "annotation.txt").exists()
    assert cli.main(["--config", str(cfg), "tmt", "annotation", str(drop), "--write"]) == 0
    assert (drop / "annotation.txt").read_text(encoding="utf-8") == tmtkit.annotation_text(GOOD)
    (drop / "annotation.txt").write_text("mine\n", encoding="utf-8")
    assert cli.main(["--config", str(cfg), "tmt", "annotation", str(drop), "--write"]) == 1  # FragPipe would stop
    assert (drop / "annotation.txt").read_text(encoding="utf-8") == "mine\n"
    capsys.readouterr()
    assert cli.main(["--config", str(cfg), "tmt", "plan", str(drop), "--json"]) == 1
    got = json.loads(capsys.readouterr().out)
    assert [f["code"] for f in got["flags"] if f["severity"] == "error"] == ["TMT_BAD_LINE"]
    assert got["plexes"][0]["source"] == "annotation.txt"


@pytest.mark.parametrize(("args", "words"), [
    ((["DMSO", "Drug"], 0), "replicates: at least 1 per condition (DMSO has 0)"),
    ((["DMSO", "Drug"], {"DMSO": 3}), "at least 1 per condition (Drug has 0)"),
    ((["DMSO", "Drug"], [2, 2, 2]), "replicates: 3 numbers for 2 conditions; give one number, or one per condition"),
    ((["DMSO", "Drug"], "x"), "replicates: 'x' is not a whole number"),
    ((["DMSO", "DMSO"], 3), "condition DMSO is named twice"),
    ((["DMSO", "dmso"], 3), "condition dmso is named twice (as DMSO and dmso)"),
    (([" ", ""], 3), "name at least one condition"),
    ((["A"], 2, ["126", "126"]), "reference 126 is named twice"),
    ((["A"], 2, "126,126"), "reference 126 is named twice"),
    ((["A"], 2, "abc"), "reference abc is not a channel of TMT-10 (126 127N"),
    ((["A"], 2, "126,131C"), "reference 131C is not a channel of TMT-10"),
    ((["A", "B"], 6), "TMT-10 has 10 channels; 6 + 6 replicates need 12, and 10 samples fit (5 per condition for 2 "
                      "conditions)"),
    ((["A"], 10, "126"), "need 11, and 9 samples fit beside the reference"),
])
def test_suggest_refuses_answers_that_make_no_map(args, words):
    """A tester's finds: replicates 0 gave an all-NA map, a repeated condition or reference channel was taken."""
    with pytest.raises(tmtkit.TmtError, match=re.escape(words)):
        tmtkit.suggest_channels("TMT-10", *args)
    assert tmtkit.suggest_channels("TMT-10", ["A"], 2, "126,127N")["127N"] == "Pool_1_127N"  # two references


@pytest.mark.parametrize(("args", "words"), [
    (["--replicates", "0"], "at least 1 per condition"),
    (["--reference", "126,126"], "reference 126 is named twice"),
    (["--conditions", "DMSO,DMSO"], "condition DMSO is named twice"),
    (["--conditions", ","], "name at least one condition"),
    (["--replicates", "x"], "--replicates x: give a whole number"),
    (["--replicates", ","], "--replicates is empty"),
    (["--tag", "TMT-12"], "'TMT-12' is not a label type FragPipe knows"),
    (["--tag", "DiLeu-12"], "Ionomos does not know the channels of DiLeu-12, so it can't suggest a map"),
    (["--plexes", "a,a"], "plex a is named twice in --plexes"),
    (["--replicates", "6"], "and 10 samples fit"),
])
def test_cli_suggest_says_why_and_exits_2(args, words, capsys):
    base = {"--tag": "TMT-10", "--conditions": "DMSO,Drug"}
    base.update(dict(zip(args[::2], args[1::2], strict=True)))
    assert cli.main(["tmt", "suggest", *[x for kv in base.items() for x in kv]]) == 2
    out = capsys.readouterr().out
    assert out.startswith("error: ") and words in out and "tmt:" not in out


def test_tmt_commands_refuse_a_job_that_is_not_tmt(tmp_path, capsys):
    """A tester's find: `ionomos tmt plan` on a DIA drop read it as TMT-6 with plexes DMSO and Drug."""
    cfg = testbed.init(tmp_path / "bed")
    dia = testbed.drop(tmp_path / "bed", "dia_good")
    for cmd in ("plan", "annotation"):
        assert cli.main(["--config", str(cfg), "tmt", cmd, str(dia)]) == 2
        out = capsys.readouterr().out
        assert "error: this is a DIA job, not TMT (method DIA" in out and "TMT-6" not in out and "plex " not in out
    with pytest.raises(tmtkit.NotTmt, match="this is a DIA job, not TMT"):
        tmtkit.plan_for_folder(dia, load(cfg))
    # --method wins: the person says it is TMT; a method the lab does not have is refused, not replaced
    assert cli.main(["--config", str(cfg), "tmt", "plan", str(dia), "--method", "TMT"]) == 0
    capsys.readouterr()
    assert cli.main(["--config", str(cfg), "tmt", "plan", str(dia), "--method", "Foo"]) == 2
    assert "--method Foo is not one of the lab's methods (isoDTB, TMT, DIA)" in capsys.readouterr().out

    # a filed job: ionomos.json says what it was filed as
    filed = tmp_path / "20260914_Isaac_DIA_FLAG-AR-pulldown"
    (filed / "raw").mkdir(parents=True)
    (filed / "raw" / "DMSO_1.raw").write_bytes(b"\0")
    rec = {"plan": {"folder": {"method": "DIA"},
                    "manifest": [{"file": "raw/DMSO_1.raw", "experiment": "DMSO", "bioreplicate": 1}]}}
    names.status_path(filed).write_text(json.dumps(rec), encoding="utf-8")
    for cmd in ("plan", "annotation", "qc"):
        assert cli.main(["--config", str(cfg), "tmt", cmd, str(filed)]) == 2
        assert f"this is a DIA job, not TMT (method DIA, from {names.STATUS_FILE})" in capsys.readouterr().out
    with pytest.raises(tmtkit.NotTmt, match="this is a DIA job"):
        tmtkit.plan_for_folder(filed)  # no lab config: the built-in kinds

    # nothing says what it is: say so, and how to tell Ionomos
    bare = tmp_path / "some_folder"
    bare.mkdir()
    (bare / "KL6159A_TMT_F1.raw").write_bytes(b"\0")
    assert cli.main(["--config", str(cfg), "tmt", "qc", str(bare), "--json"]) == 2
    out = capsys.readouterr().out
    assert "can't tell whether some_folder is a TMT job" in out and "say so with --method TMT" in out
    assert cli.main(["--config", str(cfg), "tmt", "qc", str(bare), "--method", "TMT", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ran"] is False
    with pytest.raises(tmtkit.NotTmt, match="no lab config to read its name with; if it is TMT, say so with "
                                             "--method TMT"):
        tmtkit.plan_for_folder(bare, None, None, unknown_ok=False)
    assert tmtkit.plan_for_folder(bare).plexes[0].name == "KL6159A"  # the editor's way: read as TMT
    assert tmtkit.plan_for_folder(bare, None, "TMT", unknown_ok=False).plexes


# -------------------------------------------------------- the search path --


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    monkeypatch.setattr(fragpipe, "POLL_SECONDS", 0.05)
    cfg = load(testbed.init(tmp_path / "bed"))
    return {"root": tmp_path / "bed", "cfg": cfg, "ledger": Ledger(cfg.database)}


def _run(bed, sample: str) -> tuple[Path, Job, dict]:
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    job = bed["ledger"].list()[-1]
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(job.id)
    dest = Path(job.dest_dir)
    return dest, job, json.loads((dest / names.STATUS_FILE).read_text(encoding="utf-8"))


def test_tmtpro_16_with_na_channels_is_searched(bed):
    dest, job, rec = _run(bed, "tmt16_na")
    assert job.status == "done", job.reason
    ann = (dest / "annotation.txt").read_text(encoding="utf-8")
    assert ann.splitlines() == [f"{ch}\t{n}" for ch, n in testbed.SAMPLES["tmt16_na"]["yaml"]["tmt"]["channels"].items()]
    assert ann.count("\tNA\n") == 5 and rec["plan"]["tmt"]["label"] == "TMT-16"
    psm = (dest / "fragpipe" / "KL6162_1" / "psm.tsv").read_text(encoding="utf-8").splitlines()
    head = psm[0].split("\t")
    assert [h for h in head if h.startswith("Intensity ")][:2] == ["Intensity DMSO_1_126", "Intensity DMSO_1_127N"]
    assert head.count("Intensity NA") == 5 and "(304.2071)" in psm[1]
    a = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert set(a["samples"].values()) == {"DMSO", "Drug", "Drug2"} and len(a["samples"]) == 11
    assert a["tmt_qc"]["ran"] and a["tmt_qc"]["workflow"]["quant"] == "MS2"


def test_a_tag_that_is_not_the_workflows_fails_before_the_search(bed):
    dest, job, rec = _run(bed, "tmt_tag_mismatch")
    assert job.status == "failed"
    assert "experiment.yaml says tag: TMT-16 but workflow TMT10-MS3.workflow is set to TMT-10" in job.reason
    assert not (dest / "fragpipe" / "fragpipe.workflow").exists()  # FragPipe never started
    assert any("the search will stop" in w for w in rec["plan"]["warnings"])  # said at intake already


def test_the_users_own_annotation_is_used_read_only(bed):
    src = testbed.SAMPLES["tmt_own_annotation"]["texts"]["annotation.txt"]
    dest, job, rec = _run(bed, "tmt_own_annotation")
    assert job.status == "done", job.reason
    assert (dest / "annotation.txt").read_text(encoding="utf-8") == src
    assert rec["plan"]["tmt"]["plexes"][0]["source"] == "annotation.txt"
    a = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert set(a["samples"].values()) == {"Veh", "Cmpd"}


def test_two_plexes_with_a_bridge(bed):
    dest, job, rec = _run(bed, "tmt_bridge")
    assert job.status == "done", job.reason
    for plex, k in (("plexA", 1), ("plexB", 2)):
        assert (dest / plex / "annotation.txt").read_text(encoding="utf-8").startswith(f"126\tPool_{k}_126\n")
    q = json.loads((dest / "results" / names.TMT_QC_FILE).read_text(encoding="utf-8"))
    refs = [c for p in q["plexes_full"] for c in p["ch"] if c["ref"]]
    assert [c["n"] for c in refs] == ["Pool_1_126", "Pool_2_126"] and all(abs(c["rel"]) < 1 for c in refs)
    assert [w for w in rec["plan"]["warnings"]] == []
    # the pool is the reference role: used for the plexes, never a comparison (D91, roles.kept_apart)
    a = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    compared = [c["name"] for c in a["comparisons"]]
    assert compared == ["Drug vs DMSO"], compared


def test_two_plexes_annotation_files_keep_the_plex(bed):
    """#155: the FragPipe-Analyst annotation said plex 1 with an empty channel for every sample and repeated
    replicate numbers; the lab's annotation file (the R script keeps `_1_` only) dropped plex 2."""
    from ionomos.downstream.tables import read_tsv

    dest, job, _rec = _run(bed, "tmt_bridge")
    assert job.status == "done", job.reason
    head, rows = read_tsv(dest / "results" / "fragpipe-analyst" / "experiment_annotation.tsv")
    assert head == ["plex", "channel", "sample", "sample_name", "condition", "replicate"]
    got = {r["sample"]: (r["plex"], r["channel"]) for r in rows}
    assert got["Pool_1_126"] == ("1", "126") and got["DMSO_2_127N"] == ("2", "127N"), got
    assert all(r["sample"].split("_")[1] == r["plex"] and r["sample"].endswith("_" + r["channel"]) for r in rows)
    pairs = [(r["condition"], r["replicate"]) for r in rows]
    assert len(pairs) == len(set(pairs)), pairs   # a replicate number once per condition
    head, lab = read_tsv(dest / "results" / "experimental_annotation.tsv")
    assert {r["plex"] for r in lab} == {"1", "2"} and len(lab) == len(rows)
    assert {r["sample_name"] for r in lab} == {r["sample"] for r in lab}   # unique: the plex is kept in the name
    drug = [r["replicate"] for r in lab if r["condition"] == "Drug"]
    assert drug == [str(k) for k in range(1, len(drug) + 1)]


def test_the_lab_annotation_port_multi_plex_extension():
    """#155: one plex -> the R script's file byte for byte (tests/test_downstream.py, the golden); several plexes ->
    condition_<k>_channel kept for every k, plex = k, the whole name as sample_name, replicates running on."""
    from ionomos.downstream import tmt

    header = ["Index", "Pool_1_126", "DMSO_1_127N", "DMSO_1_127C", "Pool_2_126", "DMSO_2_127N", "odd-name_2_128N"]
    one = tmt.annotation_rows(header)
    assert [r["sample"] for r in one] == ["Pool_1_126", "DMSO_1_127N", "DMSO_1_127C"]
    assert one[1]["sample_name"] == "DMSO_127N"
    many = tmt.annotation_rows(header, multiplex=True)
    assert [(r["plex"], r["sample_name"], r["replicate"]) for r in many] == [
        ("1", "Pool_1_126", 1), ("1", "DMSO_1_127N", 1), ("1", "DMSO_1_127C", 2), ("2", "Pool_2_126", 2),
        ("2", "DMSO_2_127N", 3)]
    plex, ch = tmt.plexes_and_channels(["Pool_1_126", "X", "C3L-00004_T"],
                                       {"plexA": {"126": "Pool_1_126"}, "plexB": {"127N": "C3L-00004_T"}})
    assert plex == {"Pool_1_126": "plexA", "C3L-00004_T": "plexB"} and ch["C3L-00004_T"] == "127N"


def test_the_fake_writes_tmt_integrators_tables(bed):
    dest, job, _rec = _run(bed, "tmt_good")
    rep = dest / "fragpipe" / "tmt-report"
    assert {p.name for p in rep.glob("*.tsv")} == {
        "abundance_gene_MD.tsv", "abundance_gene_None.tsv", "ratio_gene_MD.tsv", "abundance_protein_MD.tsv",
        "ratio_protein_MD.tsv", "abundance_peptide_MD.tsv"}
    head = (rep / "abundance_protein_MD.tsv").read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert head[:5] == ["Index", "NumberPSM", "Gene", "MaxPepProb", "ReferenceIntensity"]


def test_a_fake_ref_tag_rule_is_fragpipes(bed, tmp_path):
    """The fake FragPipe stops as FragPipe does when a reference sample is asked for and a file has none."""
    wf = bed["cfg"].workflow_dir / "TMT10-MS3.workflow"
    wf.write_text(wf.read_text(encoding="utf-8").replace("tmtintegrator.add_Ref=1", "tmtintegrator.add_Ref=-1"),
                  encoding="utf-8")
    folder = testbed.drop(bed["root"], "tmt_good")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    job = bed["ledger"].list()[-1]
    with pytest.raises(fragpipe.JobError, match=re.escape("has no sample name containing the workflow's Ref sample "
                                                          "tag 'Bridge'")):
        fragpipe.prepare(job, bed["cfg"])


def test_an_itraq4_drop_is_taken_in_and_searched(bed):
    """#154: a drop with tag: iTRAQ-4 and FragPipe's stock iTRAQ4.workflow was REJECTED at intake. Now intake takes
    it, the annotation is written in iTRAQ's channels and the fake FragPipe searches it (TESTED)."""
    (bed["cfg"].workflow_dir / "iTRAQ4.workflow").write_text(testbed.workflow_text("TMT", "iTRAQ-4"),
                                                              encoding="utf-8", newline="\n")
    drop = bed["root"] / "Fragpipe_Auto" / "inbox" / "20260301_Aman_TMT_KL7001-itraq"
    drop.mkdir()
    for i in (1, 2):
        (drop / f"KL7001_TMT_F{i}.raw").write_bytes(b"RAW" * 400)
    ch = {"114": "DMSO_1_114", "115": "DMSO_1_115", "116": "Drug_1_116", "117": "Drug_1_117"}
    (drop / "experiment.yaml").write_text(yaml.safe_dump({"workflow": "iTRAQ4", "tmt": {"tag": "iTRAQ-4",
                                                                                        "channels": ch}}),
                                          encoding="utf-8")
    assert intake(drop, bed["cfg"], bed["ledger"]).value == "queued"
    job = bed["ledger"].list()[-1]
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(job.id)
    assert job.status == "done", job.reason
    dest = Path(job.dest_dir)
    assert (dest / "annotation.txt").read_text(encoding="utf-8") == tmtkit.annotation_text(ch)
    rec = json.loads((dest / names.STATUS_FILE).read_text(encoding="utf-8"))
    assert rec["plan"]["tmt"]["label"] == "iTRAQ-4" and rec["run"]["tmt_map"]["tmt"]["tag"] == "iTRAQ-4"
    a = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert set(a["samples"].values()) == {"DMSO", "Drug"}


def test_a_retry_uses_the_fixed_experiment_yaml_not_intakes_map(bed):
    """#147: a TMT job failed on its channel map; the person fixed tmt: in experiment.yaml and pressed Retry. The
    retry planned the search from the map frozen in the job list at intake and failed again, word for word."""
    from ionomos.worker import request_retry

    dest, job, rec = _run(bed, "tmt_tag_mismatch")
    assert job.status == "failed" and "tag: TMT-16" in job.reason and "(channel map from experiment.yaml)" in job.reason
    assert rec["plan"]["overrides"]["tmt"]["tag"] == "TMT-16"   # what intake saw: kept as it was
    block = tmtkit.to_overrides_block(GOOD, "TMT-10")
    (dest / "experiment.yaml").write_text(yaml.safe_dump({"tmt": block}), encoding="utf-8")
    assert request_retry(bed["ledger"], job.id)[0]
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(job.id)
    assert job.status == "done", job.reason
    assert (dest / "annotation.txt").read_text(encoding="utf-8") == tmtkit.annotation_text(GOOD)
    rec = json.loads((dest / names.STATUS_FILE).read_text(encoding="utf-8"))
    assert rec["run"]["tmt_map"]["from"] == "experiment.yaml (changed since intake)"
    assert rec["run"]["tmt_map"]["tmt"]["tag"] == "TMT-10"
    assert rec["plan"]["overrides"]["tmt"]["tag"] == "TMT-16"   # intake's record is history, never rewritten
    assert "TMT: the channel map is from experiment.yaml (changed since intake)" in rec["run"]["notes"]


def test_the_retry_map_when_experiment_yaml_is_broken_or_gone(bed):
    """#147: the folder's experiment.yaml is the map; a file that can't be read fails the job saying so (no Hold,
    D91), and only a file that is gone falls back to the map saved at intake."""
    dest, job, _rec = _run(bed, "tmt_good")
    assert job.status == "done", job.reason
    yml = dest / "experiment.yaml"
    now = yaml.safe_load(yml.read_text(encoding="utf-8"))["tmt"]
    filed = tmtkit._as_json(now)
    assert tmtkit.current_block(dest, filed) == (now, "experiment.yaml")   # 126: vs "126": is no change
    yml.write_text("tmt: [unclosed\n", encoding="utf-8")
    with pytest.raises(tmtkit.TmtError, match="experiment.yaml can't be read, so its tmt: map can't be used"):
        tmtkit.current_block(dest, filed)
    bed["ledger"].requeue(job.id, "test")
    with pytest.raises(fragpipe.JobError, match=r"^TMT: experiment.yaml can't be read.*fix the file, then Retry"):
        fragpipe.prepare(bed["ledger"].get(job.id), bed["cfg"])
    yml.unlink()
    block, src = tmtkit.current_block(dest, filed)
    assert block == filed and src == "the map saved at intake (experiment.yaml is gone)"
    spec = fragpipe.prepare(bed["ledger"].get(job.id), bed["cfg"])
    assert spec.tmt_map["from"] == src and "TMT: the channel map is from " + src in spec.notes
    yml.write_text("notes: no map any more\n", encoding="utf-8")
    assert tmtkit.current_block(dest, filed) == ({}, "experiment.yaml (its tmt: block was removed after intake)")
    assert tmtkit.current_block(dest, {}) == ({}, "")
