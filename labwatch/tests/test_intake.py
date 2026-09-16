import json
import shutil

import pytest

from labwatch.intake import IntakeError, intake, plan
from labwatch.ledger import Ledger
from tests.conftest import iso_raws, make_drop


@pytest.fixture
def ledger(lab):
    return Ledger(lab["cfg"].database)


def test_isodtb_happy_path(lab, ledger):
    d = make_drop(lab["inbox"], "20260902-isoDTB_EJQ-2-027", iso_raws(), others=["notes.xlsx"])
    job = intake(d, lab["cfg"], ledger)
    assert job is not None and job.status == "queued"
    dest = lab["general"] / "EJQ" / "20260902-isoDTB_EJQ-2-027"
    assert dest.is_dir() and not d.exists()
    assert len(list(dest.glob("*.raw"))) == 21
    assert (dest / "notes.xlsx").is_file()
    rec = json.loads((dest / "labwatch.json").read_text())
    assert rec["status"] == "queued"
    assert rec["plan"]["folder"]["user"] == "EJQ"
    assert rec["plan"]["folder"]["date"] == "2026-09-02"
    assert rec["plan"]["layout"] == {"EJQ_PK_EJQ-2-027_isoDTB_1uM_3h": {"1": list(range(1, 8)), "2": list(range(1, 8)), "3": list(range(1, 8))}}
    assert rec["method_config"]["postprocess"] == ["isodtb_sites"]
    m = rec["plan"]["manifest"]
    assert len(m) == 21 and m[0]["data_type"] == "DDA" and {x["bioreplicate"] for x in m} == {1, 2, 3}
    assert ledger.next_queued().id == job.id


def test_dia_with_raw_subfolder_and_spaces(lab, ledger):
    d = make_drop(lab["inbox"], "Isaac DIA FLAG pulldown (test)",
                  ["DMSO 1.raw", "DMSO 2.raw", "Drug_1.raw", "Drug_2.raw"], raw_sub=True)
    job = intake(d, lab["cfg"], ledger)
    dest = lab["general"] / "Isaac" / "Isaac-DIA-FLAG-pulldown-test"
    assert job.dest_dir == str(dest)
    names = sorted(p.name for p in (dest / "raw").iterdir())
    assert names == ["DMSO-1.raw", "DMSO-2.raw", "Drug_1.raw", "Drug_2.raw"]
    rec = json.loads((dest / "labwatch.json").read_text())
    assert rec["plan"]["renames"] == {"DMSO 1.raw": "DMSO-1.raw", "DMSO 2.raw": "DMSO-2.raw"}
    assert rec["plan"]["manifest"][0]["file"].startswith("raw/")
    assert {x["experiment"] for x in rec["plan"]["manifest"]} == {"DMSO", "Drug"}
    assert rec["plan"]["manifest"][0]["data_type"] == "DIA"


def test_tmt_all_rep1(lab, ledger):
    d = make_drop(lab["inbox"], "Aman_TMT_KL6159A", [f"KL6159A_TMT_F{i}.raw" for i in range(1, 9)])
    job = intake(d, lab["cfg"], ledger)
    m = job.parsed["plan"]["manifest"]
    assert {x["bioreplicate"] for x in m} == {1} and {x["experiment"] for x in m} == {"KL6159A"}


@pytest.mark.parametrize(
    "name, raws, reason",
    [
        ("EJQ123_isoDTB", ["S_1_1.raw"], "no known user"),
        ("EJQ_2027_run", ["S_1_1.raw"], "no method"),
        ("EJQ_isoDTB_x", [], "no .raw"),
        ("EJQ_isoDTB_x", ["S_1_1.raw", "S_1_2.raw", "S_2_1.raw"], "incomplete"),
        ("EJQ_isoDTB_x", ["Sample.raw"], "must end"),
    ],
)
def test_rejections_leave_folder_and_write_note(lab, ledger, name, raws, reason):
    d = make_drop(lab["inbox"], name, raws)
    assert intake(d, lab["cfg"], ledger) is None
    assert d.is_dir()
    note = lab["inbox"] / f"{name}.REJECTED.txt"
    assert note.is_file() and reason in note.read_text()
    assert ledger.list() == []


def test_never_overwrite_existing_destination(lab, ledger):
    (lab["general"] / "EJQ" / "EJQ_isoDTB_x").mkdir()
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    assert intake(d, lab["cfg"], ledger) is None
    assert "already exists" in (lab["inbox"] / "EJQ_isoDTB_x.REJECTED.txt").read_text()
    assert d.is_dir()


def test_same_name_twice_is_rejected_by_ledger(lab, ledger):
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    assert intake(d, lab["cfg"], ledger) is not None
    shutil.rmtree(lab["general"] / "EJQ" / "EJQ_isoDTB_x")  # user deleted results
    d2 = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    assert intake(d2, lab["cfg"], ledger) is None
    assert "already exists in the ledger" in (lab["inbox"] / "EJQ_isoDTB_x.REJECTED.txt").read_text()


def test_default_user_fallback(lab, ledger):
    import yaml

    from labwatch.config import load
    dd = lab["cfg_dict"]; dd["users"]["default"] = "_unsorted"
    lab["cfg_path"].write_text(yaml.safe_dump(dd))
    cfg = load(lab["cfg_path"])
    d = make_drop(lab["inbox"], "mystery_isoDTB", ["S_1_1.raw"])
    job = intake(d, cfg, ledger)
    assert job.user == "_unsorted"
    assert (lab["general"] / "_unsorted" / "mystery_isoDTB").is_dir()


def test_plan_is_read_only(lab, ledger):
    d = make_drop(lab["inbox"], "20260902-isoDTB_EJQ-2-027", iso_raws())
    p = plan(d, lab["cfg"], ledger)
    assert p.dest.endswith("20260902-isoDTB_EJQ-2-027")
    assert d.is_dir() and ledger.list() == []
    with pytest.raises(IntakeError):
        plan(lab["inbox"] / "nope", lab["cfg"], ledger)
