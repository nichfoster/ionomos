import json
import shutil

import pytest

from labwatch.intake import IntakeError, IntakeResult, Kind, draft, intake, plan
from labwatch.ledger import Ledger
from labwatch.manifest import FileOverride, Overrides, load_overrides, save_overrides
from tests.conftest import iso_raws, make_drop


@pytest.fixture
def ledger(lab):
    return Ledger(lab["cfg"].database)


def test_isodtb_happy_path(lab, ledger):
    d = make_drop(lab["inbox"], "20260902-isoDTB_EJQ-2-027", iso_raws(), others=["notes.xlsx"])
    assert intake(d, lab["cfg"], ledger) == IntakeResult.QUEUED
    job = ledger.next_queued()
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
    assert intake(d, lab["cfg"], ledger) == IntakeResult.QUEUED
    job = ledger.next_queued()
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
    assert intake(d, lab["cfg"], ledger) == IntakeResult.QUEUED
    m = ledger.next_queued().parsed["plan"]["manifest"]
    assert {x["bioreplicate"] for x in m} == {1} and {x["experiment"] for x in m} == {"KL6159A"}


@pytest.mark.parametrize(
    "name, raws, reason",
    [
        ("nobody_isoDTB", ["S_1_1.raw"], "no known user"),
        ("EJQ_2027_run", ["S_1_1.raw"], "no method"),
        ("EJQ_isoDTB_x", [], "no .raw"),
        ("EJQ_isoDTB_x", ["S_1_1.raw", "S_1_2.raw", "S_2_1.raw"], "incomplete"),
        ("EJQ_isoDTB_x", ["Sample.raw"], "must end"),
    ],
)
def test_rejections_leave_folder_and_write_note(lab, ledger, name, raws, reason):
    d = make_drop(lab["inbox"], name, raws)
    assert intake(d, lab["cfg"], ledger) == IntakeResult.REJECTED
    assert d.is_dir()
    note = lab["inbox"] / f"{name}.REJECTED.txt"
    assert note.is_file() and reason in note.read_text()
    assert ledger.list() == []


def test_never_overwrite_existing_destination(lab, ledger):
    (lab["general"] / "EJQ" / "EJQ_isoDTB_x").mkdir()
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    assert intake(d, lab["cfg"], ledger) == IntakeResult.REJECTED
    assert "already exists" in (lab["inbox"] / "EJQ_isoDTB_x.REJECTED.txt").read_text()
    assert d.is_dir()


def test_same_name_twice_is_rejected_by_ledger(lab, ledger):
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    assert intake(d, lab["cfg"], ledger) == IntakeResult.QUEUED
    shutil.rmtree(lab["general"] / "EJQ" / "EJQ_isoDTB_x")  # user deleted results
    d2 = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    assert intake(d2, lab["cfg"], ledger) == IntakeResult.REJECTED
    assert "already processed" in (lab["inbox"] / "EJQ_isoDTB_x.REJECTED.txt").read_text()


def test_default_user_fallback(lab, ledger):
    import yaml

    from labwatch.config import load
    dd = lab["cfg_dict"]
    dd["users"]["default"] = "_unsorted"
    lab["cfg_path"].write_text(yaml.safe_dump(dd))
    cfg = load(lab["cfg_path"])
    d = make_drop(lab["inbox"], "mystery_isoDTB", ["S_1_1.raw"])
    assert intake(d, cfg, ledger) == IntakeResult.QUEUED
    assert ledger.next_queued().user == "_unsorted"
    assert (lab["general"] / "_unsorted" / "mystery_isoDTB").is_dir()


def test_plan_is_read_only(lab, ledger):
    d = make_drop(lab["inbox"], "20260902-isoDTB_EJQ-2-027", iso_raws())
    p = plan(d, lab["cfg"], ledger)
    assert p.dest.endswith("20260902-isoDTB_EJQ-2-027")
    assert d.is_dir() and ledger.list() == []
    with pytest.raises(IntakeError):
        plan(lab["inbox"] / "nope", lab["cfg"], ledger)


# ------------------------------------------------------------- overrides --

def test_experiment_yaml_overrides_everything(lab, ledger):
    d = make_drop(lab["inbox"], "mystery_run", ["a.raw", "b.raw", "c.raw"])
    save_overrides(d, Overrides(
        user="Chris", method="DIA", files={
            "a.raw": FileOverride(experiment="DMSO", bioreplicate=1),
            "b.raw": FileOverride(experiment="DMSO", bioreplicate=2),
            "c.raw": FileOverride(experiment="Drug", bioreplicate=1),
        }, notes="hand-made"))
    p = plan(d, lab["cfg"], ledger)
    assert p.folder.user == "Chris" and p.folder.method == "DIA"
    assert p.layout == {"DMSO": {1: [], 2: []}, "Drug": {1: []}}
    assert p.overrides["notes"] == "hand-made"
    assert intake(d, lab["cfg"], ledger) == IntakeResult.QUEUED
    dest = lab["general"] / "Chris" / "mystery_run"
    assert (dest / "experiment.yaml").is_file()
    assert json.loads((dest / "labwatch.json").read_text())["plan"]["date_source"] == "drop"


def test_overrides_allow_uneven_and_bad_tail(lab, ledger):
    files = iso_raws([1, 2], range(1, 4)) + ["EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra.raw"]
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", files)
    with pytest.raises(IntakeError) as e:
        plan(d, lab["cfg"], ledger)
    assert e.value.kind == Kind.RAWS
    save_overrides(d, Overrides(allow_uneven_fractions=True, files={
        "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra.raw": FileOverride(experiment="EJQ_PK_EJQ-2-027_isoDTB_1uM_3h", bioreplicate=2, fraction=4)}))
    p = plan(d, lab["cfg"], ledger)
    assert p.layout["EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"] == {1: [1, 2, 3], 2: [1, 2, 3, 4]}


def test_malformed_overrides_is_not_resolvable(lab, ledger):
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    (d / "experiment.yaml").write_text("bogus_key: 1\n")
    with pytest.raises(IntakeError) as e:
        plan(d, lab["cfg"], ledger)
    assert e.value.kind == Kind.OVERRIDES
    assert intake(d, lab["cfg"], ledger, resolver=_Never()) == IntakeResult.REJECTED


# -------------------------------------------------------------- resolver --

class _Never:
    def resolve(self, d):
        raise AssertionError("resolver must not be called")


class _Answer:
    def __init__(self, ov):
        self.ov, self.drafts = ov, []

    def resolve(self, d):
        self.drafts.append(d)
        return self.ov


def test_draft_for_unknown_user(lab):
    d = make_drop(lab["inbox"], "XYZ99_isoDTB_run", iso_raws([1, 2], [1, 2]))
    with pytest.raises(IntakeError) as e:
        plan(d, lab["cfg"])
    dr = draft(d, lab["cfg"], e.value)
    assert dr.kind == Kind.USER and dr.user == "" and dr.method == "isoDTB"
    assert [f.bioreplicate for f in dr.files] == ["1", "1", "2", "2"]
    assert dr.known_users and "EJQ" in dr.known_users


def test_resolver_answer_is_persisted_and_used(lab, ledger):
    d = make_drop(lab["inbox"], "XYZ99_isoDTB_run", iso_raws([1, 2], [1, 2]))
    r = _Answer(Overrides(user="Isaac"))
    assert intake(d, lab["cfg"], ledger, resolver=r) == IntakeResult.QUEUED
    assert r.drafts[0].kind == Kind.USER
    dest = lab["general"] / "Isaac" / "XYZ99_isoDTB_run"
    ov = load_overrides(dest)
    assert ov.user == "Isaac" and ov.resolved_by == "gui"


def test_resolver_can_name_a_new_user(lab, ledger):
    d = make_drop(lab["inbox"], "XYZ99_isoDTB_run", ["S_1_1.raw"])
    assert intake(d, lab["cfg"], ledger, resolver=_Answer(Overrides(user="NewPerson"))) == IntakeResult.QUEUED
    assert (lab["general"] / "NewPerson" / "XYZ99_isoDTB_run").is_dir()


def test_resolver_skip_rejects(lab, ledger):
    d = make_drop(lab["inbox"], "XYZ99_isoDTB_run", ["S_1_1.raw"])
    assert intake(d, lab["cfg"], ledger, resolver=_Answer(None)) == IntakeResult.REJECTED
    assert "skipped" in (lab["inbox"] / "XYZ99_isoDTB_run.REJECTED.txt").read_text()


def test_resolver_not_used_for_dest_conflicts(lab, ledger):
    (lab["general"] / "EJQ" / "EJQ_isoDTB_x").mkdir()
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    assert intake(d, lab["cfg"], ledger, resolver=_Never()) == IntakeResult.REJECTED


def test_successful_intake_clears_old_note(lab, ledger):
    d = make_drop(lab["inbox"], "EJQ_isoDTB_x", ["S_1_1.raw"])
    note = lab["inbox"] / "EJQ_isoDTB_x.REJECTED.txt"
    note.write_text("old")
    assert intake(d, lab["cfg"], ledger) == IntakeResult.QUEUED
    assert not note.exists()
