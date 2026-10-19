from datetime import date

import pytest

from ionomos.manifest import (
    FileOverride,
    Overrides,
    OverridesError,
    apply_file_overrides,
    fp_manifest_text,
    load_overrides,
    parse_overrides,
    save_overrides,
    tmt_annotation_files,
)
from ionomos.naming import group_raws


def test_roundtrip(tmp_path):
    ov = Overrides(user="EJQ", method="isoDTB", date=date(2026, 9, 2), allow_uneven_fractions=True,
                   files={"a_1_1.raw": FileOverride(experiment="X", bioreplicate=2, fraction=-1)},
                   tmt={"channels": {"126": "DMSO_126"}}, notes="n")
    save_overrides(tmp_path, ov)
    back = load_overrides(tmp_path)
    assert back.user == "EJQ" and back.method == "isoDTB" and back.date == date(2026, 9, 2)
    assert back.allow_uneven_fractions and back.notes == "n"
    assert back.files["a_1_1.raw"].fraction == -1
    assert back.tmt == {"channels": {"126": "DMSO_126"}}


def test_merge_keeps_existing_keys(tmp_path):
    save_overrides(tmp_path, Overrides(notes="keep me", files={"a.raw": FileOverride(experiment="A")}))
    save_overrides(tmp_path, Overrides(user="EJQ", files={"b.raw": FileOverride(experiment="B")}))
    back = load_overrides(tmp_path)
    assert back.notes == "keep me" and back.user == "EJQ"
    assert set(back.files) == {"a.raw", "b.raw"}


def test_the_editor_replaces_the_analysis_block_and_the_review_merges_into_it(tmp_path):
    """The experiment editor writes the whole analysis: block, so a choice taken back (a role back to automatic,
    a sample used again) leaves the file; the review window's control is merged in and keeps saved comparisons."""
    save_overrides(tmp_path, Overrides(notes="keep me", analysis={"exclude_samples": ["A_1"], "log2fc": 0.5,
                                                                  "roles": {"P_pre": "compound"}}))
    save_overrides(tmp_path, Overrides(analysis={"control": "DMSO"}))
    assert load_overrides(tmp_path).analysis == {"exclude_samples": ["A_1"], "log2fc": 0.5,
                                                 "roles": {"P_pre": "compound"}, "control": "DMSO"}
    save_overrides(tmp_path, Overrides(analysis={"log2fc": 0.5}), replace_analysis=True)
    back = load_overrides(tmp_path)
    assert back.analysis == {"log2fc": 0.5} and back.notes == "keep me"
    save_overrides(tmp_path, Overrides(), replace_analysis=True)
    assert load_overrides(tmp_path).analysis == {} and load_overrides(tmp_path).notes == "keep me"


@pytest.mark.parametrize(
    "data, msg",
    [
        ({"nope": 1}, "unknown key"),
        ({"date": "yesterday"}, "YYYY-MM-DD"),
        ({"files": {"a.raw": {"bioreplicate": "two"}}}, "integer"),
        ({"files": {"a.raw": {"what": 1}}}, "unknown key"),
        # replicate / fraction numbers are bounded like a file name's, 1-999 (D85): job 1 on 0.5.1 wrote these two
        ({"files": {"CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1_20260508180610.raw": {"bioreplicate": 20260508180610}}},
         r"files\.CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1_20260508180610\.raw\.bioreplicate is 20260508180610, but it "
         r"must be 1-999: .*DIA-NN drops that run"),
        ({"files": {"DMSO_2_20260508204737.raw": {"bioreplicate": "20260508204737"}}}, "must be 1-999"),
        ({"files": {"a.raw": {"bioreplicate": 2 ** 31}}}, "must be 1-999"),
        ({"files": {"a.raw": {"bioreplicate": 1000}}}, "must be 1-999"),
        ({"files": {"a.raw": {"bioreplicate": 0}}}, "must be 1-999"),
        ({"files": {"a.raw": {"bioreplicate": -1}}}, "must be 1-999"),  # -1 only means "no fraction"
        ({"files": {"a.raw": {"fraction": 0}}}, "1-999, or -1 for a single-shot file"),
        ({"files": {"a.raw": {"fraction": -2}}}, "1-999, or -1"),
        ({"files": {"a.raw": {"fraction": 1000}}}, "1-999, or -1"),
        ({"files": "a.raw"}, "mapping"),
        ({"tmt": {"tag": "TMT-10"}}, "channels"),
        ({"tmt": {"plexes": {"p1": {}}}}, "channels"),
        ({"user": "../../x"}, "not a path"),
        ({"user": "a\\\\b"}, "not a path"),
        ({"user": "EJQ\\nX"}, "not a path"),  # control character
        ({"user": ".."}, "no usable characters"),
        ({"files": {"a.raw": {"experiment": "../evil"}}}, "not a path"),
        # workflow: / fasta: are file names in workflow_dir / fasta_dir: a drop never picks a file elsewhere (#168)
        ({"workflow": "C:/Users/elsewhere/planted.workflow"}, "name of a file in the lab's workflow_dir, not a path"),
        ({"workflow": "//server/share/x.workflow"}, "workflow_dir, not a path"),
        ({"workflow": r"\\server\share\x.workflow"}, "workflow_dir, not a path"),
        ({"workflow": "../TMT10-MS3.workflow"}, "workflow_dir, not a path"),
        ({"workflow": ".."}, "workflow_dir, not a path"),
        ({"workflow": "C:isoDTB.workflow"}, "workflow_dir, not a path"),  # drive-relative
        ({"fasta": "/home/x/human_reviewed_decoys.fas"}, "fasta_dir, not a path"),
        ({"fasta": r"\\?\UNC\server\share\db.fas"}, "fasta_dir, not a path"),
        ({"fasta": "db.fas\n"}, "fasta_dir, not a path"),
    ],
)
def test_bad_overrides(data, msg):
    with pytest.raises(OverridesError, match=msg):
        parse_overrides(data)


@pytest.mark.parametrize(("spec", "rep", "frac"), [
    ({"bioreplicate": 1}, 1, None), ({"bioreplicate": 999}, 999, None), ({"bioreplicate": "3"}, 3, None),
    ({"fraction": 1}, None, 1), ({"fraction": 999}, None, 999), ({"fraction": -1}, None, -1),
    ({"bioreplicate": "", "fraction": ""}, None, None),
])
def test_replicate_and_fraction_bounds_accept(spec, rep, frac):
    fo = parse_overrides({"files": {"a.raw": spec}}).files["a.raw"]
    assert (fo.bioreplicate, fo.fraction) == (rep, frac)


def test_the_naming_window_answer_replaces_the_files_block(tmp_path):
    """The naming window shows every raw, so its answer is the whole files: block: an entry it was opened for (a
    replicate outside 1-999 written by 0.5.1) is not merged back in. Other keys are kept (D85)."""
    bad = "CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1_20260508180610.raw"
    (tmp_path / "experiment.yaml").write_text(
        f"notes: keep me\nfiles:\n  {bad}: {{experiment: DMSO, bioreplicate: 20260508180610}}\n", encoding="utf-8")
    with pytest.raises(OverridesError, match="must be 1-999"):
        load_overrides(tmp_path)
    save_overrides(tmp_path, Overrides(user="Chris", files={"x.raw": FileOverride(experiment="X")}), replace_files=True)
    back = load_overrides(tmp_path)
    assert set(back.files) == {"x.raw"} and back.notes == "keep me" and back.user == "Chris"
    save_overrides(tmp_path, Overrides(user="Chris"), replace_files=True)
    assert load_overrides(tmp_path).files == {}


@pytest.mark.parametrize("key,name", [
    ("workflow", "TMT10-MS3-phospho"), ("workflow", "isoDTB.workflow"), ("workflow", "LFQ MBR v2.1.workflow"),
    ("fasta", "human_reviewed_2025-01_decoys.fas"), ("fasta", "2026-09-15-decoys-reviewed-contam-UP000005640.fas"),
])
def test_workflow_and_fasta_file_names_are_kept_as_written(key, name):
    assert getattr(parse_overrides({key: name}), key) == name


def test_a_site_annotation_on_a_network_path_is_never_opened(tmp_path, monkeypatch):
    from ionomos.downstream import cys

    opened = []
    monkeypatch.setattr(cys.Path, "is_file", lambda self: opened.append(str(self)) or True)
    for p in (r"\\server\share\cysdb.csv", "//server/share/cysdb.csv", r"\\?\UNC\server\share\cysdb.csv"):
        assert cys.find_annotation(p, tmp_path) is None
    assert opened == []
    assert cys.find_annotation("cysdb.csv", tmp_path) == tmp_path / "cysdb.csv"


def test_absent_file_is_empty(tmp_path):
    assert load_overrides(tmp_path).is_empty()


def test_path_fields_are_sanitized():
    ov = parse_overrides({"user": "Mary Jane", "files": {"a.raw": {"experiment": "plex 1"}}})
    assert ov.user == "Mary-Jane"
    assert ov.files["a.raw"].experiment == "plex-1"


@pytest.mark.parametrize("value", ["EJQ", "Taylor_Elements", "2026-09-02-run", "v1.2.3_build"])
def test_gui_era_values_round_trip(value):
    assert parse_overrides({"user": value}).user == value
    assert parse_overrides({"files": {"a.raw": {"experiment": value}}}).files["a.raw"].experiment == value


def test_apply_file_overrides_rebuilds_layout():
    rs = group_raws(["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw"], "DIA")
    ov = Overrides(files={"Drug_1.raw": FileOverride(experiment="DMSO", bioreplicate=3)})
    out = apply_file_overrides(rs, ov)
    assert out.layout == {"DMSO": {1: [], 2: [], 3: []}}
    with pytest.raises(OverridesError, match="not in folder"):
        apply_file_overrides(rs, Overrides(files={"ghost.raw": FileOverride(experiment="x")}))
    with pytest.raises(OverridesError, match="two files resolve"):
        apply_file_overrides(rs, Overrides(files={"Drug_1.raw": FileOverride(experiment="DMSO", bioreplicate=1)}))


@pytest.mark.parametrize(("rep", "frac"), [(20260508180610, None), (20260508204737, None), (1000, None), (0, None),
                                         (1, 1000)])
def test_no_override_puts_a_number_outside_1_to_999_in_the_manifest(rep, frac):
    """However a FileOverride is made (experiment.yaml, the window, the naming history), the layout refuses a
    number outside 1-999 before it can reach FragPipe's manifest (D85)."""
    rs = group_raws(["CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1_20260508180610.raw", "CS_22rv1_FLAG-AR_MA25-10uM_DMSO_3.raw"],
                    "DIA")
    ov = Overrides(files={"CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1_20260508180610.raw":
                          FileOverride(bioreplicate=rep, fraction=frac)})
    with pytest.raises(OverridesError, match="out of range .*expected 1–999"):
        apply_file_overrides(rs, ov)


def test_fp_manifest_text():
    t = fp_manifest_text([("C:\\x\\a_1.raw", "DMSO", 1, "DIA"), ("/u/b.raw", "Drug", 2, "DDA")])
    assert t == "C:/x/a_1.raw\tDMSO\t1\tDIA\n/u/b.raw\tDrug\t2\tDDA\n"


def test_tmt_annotations():
    ov = parse_overrides({"tmt": {"channels": {"126": "DMSO_126", "127N": "Drug_127N"}}})
    a = tmt_annotation_files(ov, ["plexA", "plexB"])
    assert a["plexA"] == "126\tDMSO_126\n127N\tDrug_127N\n" and a["plexB"] == a["plexA"]
    ov = parse_overrides({"tmt": {"plexes": {"plexA": {"channels": {"126": "x"}}}}})
    assert tmt_annotation_files(ov, ["plexA"]) == {"plexA": "126\tx\n"}
    with pytest.raises(OverridesError, match="no entry"):
        tmt_annotation_files(ov, ["plexA", "plexB"])
    assert tmt_annotation_files(Overrides(), ["p"]) == {}


def test_tmt_block_is_checked_with_tmtkit():
    """D91: a tag Ionomos does not know, or a typo in a key, is said at intake with the known values."""
    with pytest.raises(OverridesError, match="tag 'TMT10plexx' is not a label type FragPipe knows; use one of TMT-6"):
        parse_overrides({"tmt": {"tag": "TMT10plexx", "channels": {"126": "x"}}})
    with pytest.raises(OverridesError, match=r"unknown key\(s\) reference \(known: tag, channels, plexes"):
        parse_overrides({"tmt": {"channels": {"126": "x"}, "reference": 126}})
    ov = parse_overrides({"tmt": {"tag": "TMTpro", "channels": {126: "DMSO_1_126", "127N": None}}})
    assert tmt_annotation_files(ov, ["p"]) == {"p": "126\tDMSO_1_126\n127N\t\n"}  # empty: FragPipe refuses it
