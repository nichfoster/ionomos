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


@pytest.mark.parametrize(
    "data, msg",
    [
        ({"nope": 1}, "unknown key"),
        ({"date": "yesterday"}, "YYYY-MM-DD"),
        ({"files": {"a.raw": {"bioreplicate": "two"}}}, "integer"),
        ({"files": {"a.raw": {"what": 1}}}, "unknown key"),
        ({"files": "a.raw"}, "mapping"),
        ({"tmt": {"tag": "TMT-10"}}, "channels"),
        ({"tmt": {"plexes": {"p1": {}}}}, "channels"),
        ({"user": "../../x"}, "not a path"),
        ({"user": "a\\\\b"}, "not a path"),
        ({"user": "EJQ\\nX"}, "not a path"),  # control character
        ({"user": ".."}, "no usable characters"),
        ({"files": {"a.raw": {"experiment": "../evil"}}}, "not a path"),
    ],
)
def test_bad_overrides(data, msg):
    with pytest.raises(OverridesError, match=msg):
        parse_overrides(data)


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
