"""Table-driven tests for the naming convention, using names seen on the lab PC."""
from datetime import date

import pytest

from labwatch.naming import NamingError, group_raws, parse_folder_name, parse_raw_name

METHODS = {"isodtb": "isoDTB", "tmt": "TMT", "dia": "DIA", "dda": "DDA"}


# ---------------------------------------------------------------- folders --

@pytest.mark.parametrize(
    "name, user, method, exp_id, desc",
    [
        ("20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h", "EJQ", "isoDTB", "EJQ-2-027", "1uM-3h"),
        ("20260902_EJQ_isoDTB_EJQ-2-027", "EJQ", "isoDTB", "EJQ-2-027", None),
        ("20260914_Isaac_DIA_IJD05_FLAG-AR-pulldown", "Isaac", "DIA", "IJD05", "FLAG-AR-pulldown"),
        ("20260126_Aman_TMT_KL6159A-159B_9plex", "Aman", "TMT", "KL6159A-159B", "9plex"),
        # method token is case-insensitive and canonicalised
        ("20260902_ejq_isodtb_EJQ-2-027", "ejq", "isoDTB", "EJQ-2-027", None),
        # description may itself contain underscores (everything after EXPID)
        ("20260902_EJQ_isoDTB_EJQ-2-027_1uM_3h_redo", "EJQ", "isoDTB", "EJQ-2-027", "1uM_3h_redo"),
    ],
)
def test_folder_ok(name, user, method, exp_id, desc):
    f = parse_folder_name(name, METHODS)
    assert f.user == user
    assert f.method == method
    assert f.exp_id == exp_id
    assert f.description == desc
    assert f.date == date(int(name[:4]), int(name[4:6]), int(name[6:8]))


@pytest.mark.parametrize(
    "name, reason",
    [
        # real names from the inventory that should be rejected
        ("EJQ123_isoDTB", "must be"),
        ("20260902-isoDTB_EJQ-2-027", "must be"),
        ("THB10ISODTB", "must be"),
        ("HTwt_ZD1186KM_50uM_16h_isoDTB", "must be"),  # no date
        ("EJQ-2-027_ER-FLAG_Pulldown_(1uM", "disallowed"),
        ("20260902_EJQ_isoDTB_EJQ-2-027 (1uM 3h)", "spaces"),
        ("20260902_EJQ_FOO_EJQ-2-027", "unknown METHOD"),
        ("20261402_EJQ_isoDTB_EJQ-2-027", "valid YYYYMMDD"),
        ("20260902_EJQ_isoDTB", "must be"),  # missing EXPID
        (" 20260902_EJQ_isoDTB_X", "whitespace"),
    ],
)
def test_folder_rejected(name, reason):
    with pytest.raises(NamingError, match=reason):
        parse_folder_name(name, METHODS)


def test_folder_without_method_table_returns_token_as_typed():
    assert parse_folder_name("20260902_EJQ_IsoDtb_X").method == "IsoDtb"


# ------------------------------------------------------------------- raws --

@pytest.mark.parametrize(
    "fname, sample, rep, frac",
    [
        ("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_3_7.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h", 3, 7),
        ("EJQ_EJQ2027_isoDTB_10uM_2h_1_1.raw", "EJQ_EJQ2027_isoDTB_10uM_2h", 1, 1),
        ("DMSO_2.raw", "DMSO", 2, None),
        ("Sample_01_03.RAW", "Sample", 1, 3),
    ],
)
def test_raw_ok(fname, sample, rep, frac):
    r = parse_raw_name(fname)
    assert (r.sample, r.rep, r.fraction) == (sample, rep, frac)


@pytest.mark.parametrize(
    "fname",
    ["notes.txt", "Sample.raw", "Sample_a.raw", "Sample 1.raw", "Sample_1_2_3x.raw"],
)
def test_raw_rejected(fname):
    with pytest.raises(NamingError):
        parse_raw_name(fname)


def _iso(reps, fracs, prefix="EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"):
    return [f"{prefix}_{r}_{f}.raw" for r in reps for f in fracs]


def test_group_fractionated_3x7():
    rs = group_raws(_iso(range(1, 4), range(1, 8)))
    assert rs.samples == ["EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"]
    assert rs.layout["EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"] == {1: list(range(1, 8)), 2: list(range(1, 8)), 3: list(range(1, 8))}


def test_group_two_conditions_single_shot():
    rs = group_raws(["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"])
    assert rs.layout == {"DMSO": {1: [], 2: []}, "Drug": {1: [], 2: []}}


def test_group_uneven_fractions_is_incomplete():
    files = _iso([1, 2, 3], range(1, 8))
    files.remove("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_3_7.raw")
    with pytest.raises(NamingError, match="same fractions"):
        group_raws(files)


def test_group_mixed_single_and_fractionated():
    with pytest.raises(NamingError, match="mixes"):
        group_raws(["S_1.raw", "S_1_1.raw"])


def test_group_duplicate():
    with pytest.raises(NamingError, match="duplicate"):
        group_raws(["S_1_1.raw", "S_01_1.raw"])


def test_group_empty():
    with pytest.raises(NamingError, match="no .raw"):
        group_raws([])
