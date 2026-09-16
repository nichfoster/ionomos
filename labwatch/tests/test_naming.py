"""Table-driven naming tests, using folder and raw names seen on the lab PC."""
from datetime import date

import pytest

from labwatch.naming import (
    NamingError,
    build_user_lookup,
    find_method,
    group_raws,
    parse_folder_name,
    parse_raw_name,
    sanitize,
)

USERS = build_user_lookup(
    ["EJQ", "Isaac", "Chris", "Aman", "Carolyn", "Thang", "Zoe", "Taylor_Elements", "Yun"],
    aliases={"Isaac": ["IJ", "IJD"], "EJQ": ["EJQ_2"], "Thang": ["THB"]},
)


# ------------------------------------------------------------------ sanitize --

@pytest.mark.parametrize(
    "raw, safe",
    [
        ("20260902_EJQ_isoDTB_EJQ-2-027", "20260902_EJQ_isoDTB_EJQ-2-027"),
        ("EJQ-2-027_ER-FLAG_Pulldown_(1uM", "EJQ-2-027_ER-FLAG_Pulldown_1uM"),
        ("EJQ-2-027_ER-FLAG_Pulldown_(500nM_24h)", "EJQ-2-027_ER-FLAG_Pulldown_500nM_24h"),
        ("Again (see this one)", "Again-see-this-one"),
        ("  my exp  ", "my-exp"),
        ("012626_from Cdrive", "012626_from-Cdrive"),
    ],
)
def test_sanitize(raw, safe):
    assert sanitize(raw) == safe
    assert " " not in safe


# -------------------------------------------------------------------- method --

@pytest.mark.parametrize(
    "name, method",
    [
        ("20260902-isoDTB_EJQ-2-027", "isoDTB"),
        ("THB10ISODTB", "isoDTB"),           # glued, substring fallback
        ("thb10isodtb2", "isoDTB"),
        ("KL6159A_159B_9plex_TMT", "TMT"),
        ("EJQ123_DIA", "DIA"),
        ("CS_22rv1_175-12c5uM_24h_DIA", "DIA"),
        ("Fragpipe-DIANN", "DIA"),
        ("HTwt_ZD1186KM_50uM_16h_isoDTB", "isoDTB"),
    ],
)
def test_find_method(name, method):
    assert find_method(name) == method


def test_find_method_none_and_ambiguous():
    with pytest.raises(NamingError, match="no method"):
        find_method("20260914-FLAGPull_IJD05_FLAGAR")
    with pytest.raises(NamingError, match="ambiguous"):
        find_method("EJQ_isoDTB_and_TMT")
    # 'dia' inside another word does not count if a real token exists elsewhere
    assert find_method("media_TMT_run") == "TMT"


# -------------------------------------------------------------------- folder --

@pytest.mark.parametrize(
    "name, user, method, d",
    [
        ("20260902-isoDTB_EJQ-2-027", "EJQ", "isoDTB", date(2026, 9, 2)),
        ("20260804-isoDTB_IJ607061", "Isaac", "isoDTB", date(2026, 8, 4)),  # initials glued to ID
        ("20260914-FLAGPull_IJD05_FLAGAR_DIA", "Isaac", "DIA", date(2026, 9, 14)),  # IJD beats IJ
        ("20260331-IJ-602161-isoDTB", "Isaac", "isoDTB", date(2026, 3, 31)),
        ("08172026-isoDTB-ELK Carolyn", "Carolyn", "isoDTB", date(2026, 8, 17)),
        ("081726-isoDTB-Carolyn", "Carolyn", "isoDTB", date(2026, 8, 17)),
        ("EJQ123_DIA", "EJQ", "DIA", None),  # glued
        ("2026-09-02 EJQ DIA", "EJQ", "DIA", date(2026, 9, 2)),
        ("09-02-2026_EJQ_DIA", "EJQ", "DIA", date(2026, 9, 2)),
        ("EJQ_DIA_090226", "EJQ", "DIA", date(2026, 9, 2)),
        ("EJQ_DIA_20261402", "EJQ", "DIA", None),  # invalid date -> ignored
        ("nobody_here_DIA", None, "DIA", None),
        ("EJQ_123_DIA", "EJQ", "DIA", None),
        ("THB10ISODTB", "Thang", "isoDTB", None),  # glued initials + glued method
        ("THB_10_isoDTB", "Thang", "isoDTB", None),
        ("Taylor Elements TMT run 3", "Taylor_Elements", "TMT", None),
        ("20260126_Aman_TMT_KL6159A-159B_9plex", "Aman", "TMT", date(2026, 1, 26)),
        ("EJQ_2 isoDTB 10uM 2h", "EJQ", "isoDTB", None),  # alias EJQ_2 -> EJQ
    ],
)
def test_parse_folder(name, user, method, d):
    if user is None:
        with pytest.raises(NamingError, match="no known user"):
            parse_folder_name(name, USERS)
        return
    f = parse_folder_name(name, USERS)
    assert f.user == user
    assert f.method == method
    assert f.date == d
    assert " " not in f.safe


def test_method_fallback_from_raw_names():
    f = parse_folder_name("EJQ_2_027_run", USERS, raw_names=["EJQ_isoDTB_1uM_1_1.raw"])
    assert f.method == "isoDTB"
    with pytest.raises(NamingError, match="no method"):
        parse_folder_name("EJQ_2_027_run", USERS, raw_names=["x_1_1.raw"])


def test_parse_folder_ambiguous_user():
    with pytest.raises(NamingError, match="ambiguous user"):
        parse_folder_name("EJQ_Isaac_isoDTB", USERS)


# ---------------------------------------------------------------------- raws --

@pytest.mark.parametrize(
    "fname, method, sample, rep, frac",
    [
        # isoDTB: rep_frac
        ("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_3_7.raw", "isoDTB", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h", 3, 7),
        ("X_R2_F7.raw", "isoDTB", "X", 2, 7),
        ("X_rep2_frac7.RAW", "isoDTB", "X", 2, 7),
        ("X_Rep_2_Fraction_7.raw", "isoDTB", "X", 2, 7),
        ("X_bio2_F7.raw", "isoDTB", "X", 2, 7),
        ("X_biorep2_frac7.raw", "isoDTB", "X", 2, 7),
        ("X_n2_f7.raw", "isoDTB", "X", 2, 7),
        ("X-1-1.raw", "isoDTB", "X", 1, 1),
        ("X_2.raw", "isoDTB", "X", 2, None),
        # TMT: [_TMT]_[F]frac ; rep always 1
        ("KL6159A_TMT_F1.raw", "TMT", "KL6159A", 1, 1),
        ("KL6159A_F8.raw", "TMT", "KL6159A", 1, 8),
        ("KL6159A_TMT_1.raw", "TMT", "KL6159A", 1, 1),
        ("KL6159A_3.raw", "TMT", "KL6159A", 1, 3),
        ("KL6159A.raw", "TMT", "KL6159A", 1, None),
        ("KL6159A_TMT.raw", "TMT", "KL6159A", 1, None),
        # DIA: cond_biorep
        ("DMSO_1.raw", "DIA", "DMSO", 1, None),
        ("Drug_R3.raw", "DIA", "Drug", 3, None),
        ("Drug_bio3.raw", "DIA", "Drug", 3, None),
        ("Drug_Rep_3.raw", "DIA", "Drug", 3, None),
        ("CS_22rv1_175_DIA_2.raw", "DIA", "CS_22rv1_175_DIA", 2, None),
        ("Untitled.raw", "DIA", "Untitled", 1, None),
        # spaces are sanitised, not rejected
        ("my sample 1_2.raw", "isoDTB", "my-sample", 1, 2),
    ],
)
def test_raw_ok(fname, method, sample, rep, frac):
    r = parse_raw_name(fname, method)
    assert (r.sample, r.rep, r.fraction) == (sample, rep, frac)
    assert " " not in r.safe_filename


def test_raw_rejected():
    with pytest.raises(NamingError, match="not a .raw"):
        parse_raw_name("notes.txt", "DIA")
    with pytest.raises(NamingError, match="isoDTB files must end"):
        parse_raw_name("Sample.raw", "isoDTB")
    with pytest.raises(NamingError, match="unknown method"):
        parse_raw_name("S_1.raw", "DDA")


def _iso(reps, fracs, prefix="EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"):
    return [f"{prefix}_{r}_{f}.raw" for r in reps for f in fracs]


def test_group_isodtb_3x7():
    rs = group_raws(_iso(range(1, 4), range(1, 8)), "isoDTB")
    assert rs.samples == ["EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"]
    assert set(rs.layout[rs.samples[0]]) == {1, 2, 3}
    assert rs.layout[rs.samples[0]][2] == list(range(1, 8))


def test_group_dia_two_conditions():
    rs = group_raws(["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"], "DIA")
    assert rs.layout == {"DMSO": {1: [], 2: []}, "Drug": {1: [], 2: []}}


def test_group_tmt_fractions_one_plex():
    rs = group_raws([f"KL_TMT_F{i}.raw" for i in range(1, 9)], "TMT")
    assert rs.layout == {"KL": {1: list(range(1, 9))}}


def test_group_uneven_fractions_is_incomplete():
    files = _iso([1, 2, 3], range(1, 8))
    files.remove("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_3_7.raw")
    with pytest.raises(NamingError, match="incomplete"):
        group_raws(files, "isoDTB")


def test_group_mixed_single_and_fractionated():
    with pytest.raises(NamingError, match="mixes"):
        group_raws(["S_1.raw", "S_1_1.raw"], "isoDTB")


def test_group_duplicate_after_normalisation():
    with pytest.raises(NamingError, match="two files resolve"):
        group_raws(["S_1_1.raw", "S_01_1.raw"], "isoDTB")
    with pytest.raises(NamingError, match="two files resolve"):
        group_raws(["A.raw", "A_TMT.raw"], "TMT")


def test_group_empty():
    with pytest.raises(NamingError, match="no .raw"):
        group_raws([], "DIA")
