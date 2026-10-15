"""The TMT channel editor (tmt_editor.py, D91): the no-Tk ChannelMap (state <-> experiment.yaml tmt:, the lab's
<condition>_<plex>_<channel> names, tmtkit's validation passed through word for word, Suggest…, Read
annotation.txt from the folder) and, under the GUI rule of test_app.py, the frame in a hidden window."""
from __future__ import annotations

import json
import re

import pytest

from ionomos import names, tmtkit
from ionomos.tmt_editor import (
    NA,
    REFERENCE,
    SAMPLE,
    ChannelMap,
    convention,
    for_folder,
    roles_with_references,
)
from tests.conftest import gui_tests, make_tk_root

TMT10 = tmtkit.LABEL_TYPES["TMT-10"].channels


def _bridge() -> ChannelMap:
    cm = ChannelMap.empty(["plexA", "plexB"], workflow_label="TMT-10")
    cm.suggest(["DMSO", "Drug"], 3, "126")
    return cm


# ---------------------------------------------------------------- building --


def test_an_empty_map_takes_the_workflows_label_type_and_writes_nothing():
    cm = ChannelMap.empty(["KL6164"], workflow_label="TMTpro-16")
    assert cm.label == "TMT-16" and [r.channel for r in cm.plexes["KL6164"]] == list(tmtkit.LABEL_TYPES["TMT-16"].channels)
    assert cm.is_empty() and cm.block() == {} and cm.flags() == [] and cm.errors() == []
    assert "no channel named yet" in cm.summary()[1]
    assert ChannelMap.empty().label == "TMT-10" and list(ChannelMap.empty().plexes) == ["plex"]
    assert ChannelMap.empty(["p"], "TMT-6", "TMT-10").label == "TMT-6"  # experiment.yaml's tag wins, and is checked


def test_suggest_is_tmtkits_and_the_names_follow_the_convention():
    cm = _bridge()
    want = {p: tmtkit.suggest_channels("TMT-10", ["DMSO", "Drug"], 3, "126", plex=k) for k, p in
            ((1, "plexA"), (2, "plexB"))}
    assert cm.maps() == want
    assert cm.block() == tmtkit.to_overrides_block(want, "TMT-10", "126")
    a = {r.channel: r for r in cm.plexes["plexA"]}
    assert (a["126"].role, a["126"].condition, a["127N"].role, a["130N"].role) == (REFERENCE, "Pool", SAMPLE, NA)
    assert cm.name("plexB", a["127N"]) == "DMSO_2_127N" == convention("DMSO", 2, "127N")
    reps = cm.replicates()
    assert [reps[("plexA", c)] for c in ("127N", "127C", "128N")] == [1, 2, 3]
    assert reps[("plexB", "127N")] == 4 and ("plexA", "126") not in reps  # replicates are counted, not typed
    assert cm.reference_conditions() == ["Pool"] and cm.references() == {"plexA": ["126"], "plexB": ["126"]}
    assert cm.summary()[1] == "plex plexA: 7 channels named (DMSO 3, Drug 3), 3 NA, reference 126 (Pool_1_126)"


def test_suggest_from_the_dialogs_words():
    cm = ChannelMap.empty(["p"], "TMT-6")
    cm.suggest_from_text("Veh, Cmpd", "2, 3", "", "")
    assert cm.maps()["p"] == {"126": "Veh_1_126", "127N": "Veh_1_127N", "128C": "Cmpd_1_128C",
                              "129N": "Cmpd_1_129N", "130C": "Cmpd_1_130C", "131N": "NA"}
    cm.suggest_from_text("A B", "2", "131N", "Bridge")
    assert cm.maps()["p"]["131N"] == "Bridge_1_131N" and cm.references() == {"p": ["131N"]}
    for args, words in ((("", "3"), "name at least one condition"),
                        (("A", "x"), "replicates: a whole number"),
                        (("A", "0"), "at least 1"),
                        (("A,B", "4", "126"), re.escape("TMT-6 has 6 channels; 4 + 4 replicates + 1 reference need 9")),
                        (("A_1", "2"), "letters and digits only"),
                        (("A", "2", "999"), "reference 999 is not a channel of TMT-6")):
        with pytest.raises(tmtkit.TmtError, match=words):
            cm.suggest_from_text(*args)


def test_block_round_trip_and_changed():
    b = _bridge().block()
    cm = ChannelMap.from_block(b, ["plexA", "plexB"], workflow_label="TMT-10")
    assert cm.block() == b and not cm.changed() and cm.sources == {"plexA": "experiment.yaml",
                                                                   "plexB": "experiment.yaml"}
    cm.set_condition("plexA", "127N", "Veh")
    assert cm.changed() and cm.maps()["plexA"]["127N"] == "Veh_1_127N"
    # one channels: block applies to every plex (tmtkit.maps_from_block); a single plex is written that way back
    one = {"tag": "TMT-6", "channels": {"126": "DMSO_1_126", "127N": "NA"}}
    two = ChannelMap.from_block(one, ["a", "b"])
    assert two.label == "TMT-6" and two.maps()["b"]["126"] == "DMSO_1_126"
    single = ChannelMap.from_block(one, ["a"])
    assert set(single.block()) == {"tag", "channels"} and single.block()["channels"]["126"] == "DMSO_1_126"
    # a hand-typed name is kept; a name with a pool word or the ref tag is a reference
    odd = ChannelMap.from_block({"plexes": {"p1": {"channels": {"126": "Mix_A", "127N": "ctrl-rep1",
                                                                "127C": "NA"}, "reference": "126"}}},
                                ["p1"], "TMT-10")
    r = {x.channel: x for x in odd.plexes["p1"]}
    assert (r["126"].role, r["126"].name, r["127N"].condition, r["127N"].name) == (REFERENCE, "Mix_A", "ctrl-rep1",
                                                                                  "ctrl-rep1")
    assert odd.block()["reference_channel"] == "126"
    assert ChannelMap.from_block({"channels": {"126": "Bridge_1_126"}}, ["p"]).plexes["p"][0].role == REFERENCE


def test_references_differing_between_plexes_are_written_per_plex():
    cm = _bridge()
    cm.set_condition("plexB", "126", "DMSO")  # a channel named Pool is read as a reference: rename it
    cm.set_role("plexB", "126", SAMPLE)
    cm.set_role("plexB", "131N", "reference (bridge, pooled)")
    b = cm.block()
    assert "reference_channel" not in b
    assert b["plexes"]["plexA"]["reference"] == "126" and b["plexes"]["plexB"]["reference"] == "131N"
    assert b["plexes"]["plexB"]["channels"]["131N"] == "Pool_2_131N"
    assert tmtkit.check_block(b) == [] and tmtkit.references_from_block(b, "plexB") == ["131N"]
    back = ChannelMap.from_block(b, ["plexA", "plexB"])
    assert back.references() == {"plexA": ["126"], "plexB": ["131N"]}
    with pytest.raises(ValueError):
        cm.set_role("plexA", "126", "bystander")


def test_edits_names_conditions_roles_and_clear():
    cm = ChannelMap.empty(["p"], "TMT-10")
    cm.set_name("p", "126", "DMSO_1_126")
    r = cm.row("p", "126")
    assert (r.condition, r.role, r.name) == ("DMSO", SAMPLE, "")       # the convention: nothing kept by hand
    cm.set_name("p", "127N", "Pooled-mix")
    assert cm.row("p", "127n").role == REFERENCE and cm.row("p", "127N").condition == "Pooled-mix"
    cm.set_condition("p", "127N", "Pool")
    assert cm.name("p", cm.row("p", "127N")) == "Pool_1_127N"         # a new condition: the convention again
    cm.set_condition("p", "126", "")
    assert cm.row("p", "126").role == NA and cm.maps()["p"]["126"] == "NA"
    cm.set_name("p", "128N", "na")
    assert cm.row("p", "128N").role == NA
    cm.set_role("p", "129N", REFERENCE)
    assert cm.maps()["p"]["129N"] == "Pool_1_129N"
    cm.set_role("p", "129N", NA)
    assert cm.maps()["p"]["129N"] == "NA"
    cm.clear()
    assert cm.is_empty() and cm.block() == {}


def test_label_type_change_keeps_shared_channels_and_warns_against_the_workflow():
    cm = _bridge()
    cm.workflow = "TMT10-MS3"
    cm.set_label("TMT-6")
    assert [r.channel for r in cm.plexes["plexA"]] == list(tmtkit.LABEL_TYPES["TMT-6"].channels)
    assert cm.maps()["plexA"]["127N"] == "DMSO_1_127N" and cm.maps()["plexA"]["128C"] == "Drug_1_128C"
    assert cm.label_warning().startswith("workflow TMT10-MS3 is set to TMT-10 (tmtintegrator.channel_num): a TMT-6 "
                                         "map fails the job before the search")
    assert cm.errors()[-1] == cm.label_warning() and "the workflow says TMT-10" in cm.summary()[0]
    cm.set_label("nonsense")
    assert cm.label == "TMT-6"
    cm.set_label("TMT10")
    # #156: put aside by TMT-6 (said in the summary) and brought back by TMT-10
    assert cm.label_warning() == "" and cm.maps()["plexA"]["127C"] == "DMSO_1_127C"


def test_a_label_switch_says_which_names_it_put_aside():
    """#156: TMT-16 -> TMT-10 dropped the names on 131C-134N silently and for good."""
    cm = ChannelMap.empty(["p"], workflow_label="TMT-16")
    cm.suggest(["DMSO", "Drug"], 7)
    assert cm.maps()["p"]["133N"] == "Drug_1_133N"
    assert cm.set_label("TMT-10") == ["131C", "132N", "132C", "133N"]
    assert "131C" not in cm.maps()["p"]
    assert cm.summary()[1] == ("the names on 131C, 132N, 132C, 133N were put aside: TMT-10 has no such channels "
                               "(pick TMT-16 again to get them back)")
    assert cm.set_label("TMT-16") == [] and cm.maps()["p"]["133N"] == "Drug_1_133N"
    assert not any("put aside" in line for line in cm.summary())
    cm.set_label("TMT-10")
    cm.clear()
    cm.set_label("TMT-16")
    assert cm.is_empty()   # Clear empties what was put aside too


def test_an_itraq_map_stays_itraq():
    """#154: the editor turned an iTRAQ-4 map into TMT-10 (its label type was unknown, so the default won)."""
    block = {"tag": "iTRAQ-4", "channels": {114: "DMSO_1_114", 115: "DMSO_1_115", 116: "Drug_1_116", 117: "NA"}}
    cm = ChannelMap.from_block(block, ["KL7001"], workflow_label="iTRAQ-4")
    assert cm.label == "iTRAQ-4" and [r.channel for r in cm.plexes["KL7001"]] == ["114", "115", "116", "117"]
    assert cm.block()["tag"] == "iTRAQ-4" and not cm.changed() and cm.errors() == []
    assert cm.summary()[0] == "iTRAQ-4 (iTRAQ, 4 channels), as the workflow"
    other = ChannelMap.from_block({"tag": "DiLeu-12", "channels": {"115a": "A_1_115a"}}, ["p"])
    assert other.label == "DiLeu-12" and other.block()["tag"] == "DiLeu-12"   # FragPipe's, passed on


def test_validation_is_tmtkits_word_for_word():
    cm = _bridge()
    cm.set_name("plexB", "127N", "DMSO_1_127N")  # the same name in two plexes
    cm.ref_tag, cm.add_ref = "Bridge", "-1"
    flags = tmtkit.validate(cm.maps(), "TMT-10", "Bridge", "-1", cm.references(), source="the channel editor")
    assert [f.as_dict() for f in cm.flags()] == [f.as_dict() for f in flags]
    assert cm.errors() == [f.line() for f in flags if f.severity == "error"]
    assert cm.warnings() == [f.line() for f in flags if f.severity != "error"]
    assert {"TMT_DUPLICATE_NAME", "TMT_NO_REFERENCE"} <= {f.code for f in cm.flags()}
    spaced = _bridge()
    spaced.set_name("plexA", "128C", "Drug 1")   # a space: FragPipe's 'Invalid line', in tmtkit's words
    assert any(e.startswith("plex plexA: a channel needs one sample name without spaces") for e in spaced.errors())
    assert any("sample name DMSO_1_127N is used twice (plex plexA and plexB)" in e for e in cm.errors())


def test_reading_the_folder_never_changes_it(tmp_path):
    drop = tmp_path / "20260201_Aman_TMT_x"
    (drop / "plexA").mkdir(parents=True)
    (drop / "plexB").mkdir()
    a = "".join(f"{ch} {'Pool' if ch == '126' else 'Veh'}_1_{ch}\n" for ch in TMT10[:4]) + \
        "".join(f"{ch} NA\n" for ch in TMT10[4:])
    (drop / "plexA" / "annotation.txt").write_text(a, encoding="utf-8")
    (drop / "plexB" / "my_annotation.txt").write_text("126 Pool_2_126\n127N two words here\n", encoding="utf-8")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in drop.rglob("*.txt")}
    cm = ChannelMap.empty(["plexA", "plexB"], "TMT-10")
    said = cm.read_folder(drop)
    assert "read plexA/annotation.txt for plex plexA" in said and "Invalid line in annotation file" in said
    assert cm.maps()["plexA"]["126"] == "Pool_1_126" and cm.row("plexA", "126").role == REFERENCE
    assert cm.row("plexA", "127N").condition == "Veh" and cm.plexes["plexB"][0].role == NA
    assert "your file: FragPipe uses it, it is never changed" in cm.sources["plexA"]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in drop.rglob("*.txt")} == before
    # one plex at the top level; a TMT-6 file switches the label type; two files are not guessed between
    one = tmp_path / "one"
    one.mkdir()
    (one / "annotation.txt").write_text("".join(f"{c}\tA_1_{c}\n" for c in tmtkit.LABEL_TYPES["TMT-6"].channels),
                                        encoding="utf-8")
    cm1 = ChannelMap.from_folder(one, ["KL1"], "TMT-10")
    assert cm1.label == "TMT-6" and cm1.maps()["KL1"]["131N"] == "A_1_131N" and cm1.changed()
    (one / "b_annotation.txt").write_text("126 X\n", encoding="utf-8")
    assert "2 annotation files there" in ChannelMap.empty(["KL1"]).read_folder(one)
    assert ChannelMap.empty().read_folder(tmp_path / "gone") == "no folder to read from"
    (tmp_path / "bare").mkdir()
    assert "no file ending in annotation.txt" in ChannelMap.empty().read_folder(tmp_path / "bare")


def test_plexes_follow_the_file_grid():
    cm = _bridge()
    cm.set_plexes(["plexA", "plexC"])  # plexB renamed while typing: its rows move with it
    assert list(cm.plexes) == ["plexA", "plexC"] and cm.maps()["plexC"]["127N"] == "DMSO_2_127N"
    cm.set_plexes(["plexA", "plexC", "plexD"])
    assert cm.plexes["plexD"][0].role == NA
    cm.set_plexes([])
    assert list(cm.plexes) == ["plex"]


def test_reference_channels_become_the_reference_role():
    b = _bridge().block()
    assert roles_with_references({}, b) == {"Pool": "reference"}
    assert roles_with_references({"Drug": "compound"}, b) == {"Drug": "compound", "Pool": "reference"}
    assert roles_with_references({"pool": "qc"}, b) == {"pool": "qc"}        # a role a person gave wins
    # a condition the map holds on sample channels only loses a reference role; others are kept as written
    assert roles_with_references({"Drug": "reference", "X": "control", "Gone": "reference"}, b) == \
        {"X": "control", "Gone": "reference", "Pool": "reference"}
    # tmtkit: a reference_channel holding a name without a pool word; a condition also on sample channels is not one
    blk = {"channels": {"126": "Mix_1_126", "127N": "DMSO_1_127N"}, "reference_channel": "126"}
    assert tmtkit.reference_conditions(blk) == ["Mix"]
    blk2 = {"plexes": {"a": {"channels": {"126": "DMSO_1_126", "127N": "DMSO_1_127N"}}}, "reference_channel": 126}
    assert tmtkit.reference_conditions(blk2) == []
    assert tmtkit.reference_conditions({"channels": {"126": "X_1_126"}}, ref_tag="X") == ["X"]
    assert tmtkit.reference_conditions({}) == [] and tmtkit.reference_conditions({"plexes": "bad"}) == []


def test_for_folder_reads_a_filed_tmt_experiment(tmp_path):
    dest = tmp_path / "20260201_Aman_TMT_KL6165-bridge"
    for p in ("plexA", "plexB"):
        (dest / p).mkdir(parents=True)
        (dest / p / f"{p}_TMT_F1.raw").write_bytes(b"\0")
    b = _bridge().block()
    import yaml

    (dest / "experiment.yaml").write_text(yaml.safe_dump({"tmt": b}), encoding="utf-8")
    cm = for_folder(dest)
    assert cm is not None and list(cm.plexes) == ["plexA", "plexB"] and cm.block() == b and not cm.changed()
    # without a tmt: block, the map the search used (ionomos.json plan.tmt) is shown
    (dest / "experiment.yaml").unlink()
    rec = {"plan": {"manifest": [{"file": f"{p}/{p}_TMT_F1.raw", "experiment": p, "bioreplicate": 1}
                                 for p in ("plexA", "plexB")]}}
    names.status_path(dest).write_text(json.dumps(rec), encoding="utf-8")
    (dest / "plexA" / "annotation.txt").write_text("".join(f"{c} Veh_1_{c}\n" for c in TMT10), encoding="utf-8")
    cm2 = for_folder(dest)
    assert cm2.maps()["plexA"]["126"] == "Veh_1_126" and cm2.plexes["plexB"][0].role == NA and cm2.changed()


# ----------------------------------------------------------------------- tk --

_ok, _why = gui_tests()


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_the_frame_in_a_hidden_window(tmp_path):
    from tkinter import ttk

    from ionomos.tmt_editor import TmtEditorFrame

    root = make_tk_root()
    root.withdraw()
    try:
        changes = []
        cm = ChannelMap.empty(["plexA", "plexB"], workflow_label="TMT-10")
        ed = TmtEditorFrame(root, cm, folder=lambda: tmp_path, on_change=lambda: changes.append(1))
        ed.frame.pack()
        root.update()
        assert "start here" in str(ed.suggest_btn.cget("text")) and len(ed.book.tabs()) == 2
        assert ed.suggest("DMSO, Drug", "3", "126") == ""
        assert cm.block() == _bridge().block() and str(ed.suggest_btn.cget("text")) == "Suggest…"
        name_v, cond_v, rep, role = ed._cells[("plexA", "127N")]
        assert name_v.get() == "DMSO_1_127N" and str(rep.cget("text")) == "1"
        cond_v.set("Veh")  # typing a condition renames the channel
        assert name_v.get() == "Veh_1_127N" and cm.maps()["plexA"]["127N"] == "Veh_1_127N"
        name_v.set("Drug_1_128C")  # a name used on 128C already: tmtkit's error, in red
        assert "sample name Drug_1_128C is used twice" in str(ed.errors_l.cget("text"))
        role.set("NA (unused)")
        role.event_generate("<<ComboboxSelected>>")
        root.update()
        assert cm.maps()["plexA"]["127N"] == "NA" and name_v.get() == ""
        ed.label_v.set("TMT-6")
        ed._label_chosen()
        assert "fails the job before the search" in str(ed.label_note.cget("text"))
        assert ed.suggest("A", "9") .startswith("TMT-6 has 6 channels")
        ed.clear()
        assert cm.is_empty() and changes
        assert all(isinstance(w, ttk.Frame) for w in ed.book.winfo_children())
    finally:
        root.destroy()
