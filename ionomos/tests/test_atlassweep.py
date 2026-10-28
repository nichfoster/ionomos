"""The drive sweep (atlassweep.py, docs/ATLAS.md "The drive sweep", D128): the FragPipe runs on the PC's disks that
Ionomos did not search, handed to the cysteine atlas with their decoys and sample design.

The walk is harvest_fragpipe.ps1's pass 1 (the atlas's own repository, which is private); the copying is the
hand-off's (atlas.transfer, tests/test_atlas.py). Every test writes under tmp_path only: never the real Dropbox, the
atlas's data folder, C:/Fragpipe_Auto or C:/Fragpipe_General.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ionomos import atlas, atlassweep, attention, configio, names
from ionomos.atlas import _free_bytes as real_free_bytes  # before conftest's fixture replaces it
from ionomos.config import load
from ionomos.ledger import Job, Ledger
from tests.test_atlas import WORKFLOW, _files, _long, _pin, _read, _records, _run_folder, _snapshot, _write, _write_cfg

TMT_WORKFLOW = ("# FragPipe (23.1) runtime properties\ndatabase.db-path=C\\:\\\\fasta\\\\h.fas\n"
                "msfragger.table.fix-mods=229.16293,K,true,-1; 229.16293,n^,true,-1\n")
ISOTOP_WORKFLOW = "# FragPipe (22.0)\nmsfragger.table.var-mods=464.28596,C,true,1\n"
# D:\Kosuke\A549_nucleus_TMT-check's five runs (the lab's Dropbox export of 2026-10-27): TMT2 (duplex, 225.1558) on K
# and the peptide N-term, TMT6's 229.16293 on S turned off; classed other before D135. FragPipe's fix-mods table lists
# every site, most at 0.0
A549_FIX = ("0.0,C-Term Peptide,true,-1; 0.0,N-Term Peptide,true,-1; 0.0,C-Term Protein,true,-1; "
            "0.0,N-Term Protein,true,-1; 0.0,G (glycine),true,-1; 0.0,A (alanine),true,-1; 0.0,S (serine),true,-1; "
            "0.0,P (proline),true,-1; 57.02146,C (cysteine),true,-1; 225.1558,K (lysine),true,-1; "
            "0.0,R (arginine),true,-1")
A549_VAR = "225.1558,n^,true,1; 229.16293,S,false,1"
A549_WORKFLOW = (f"# FragPipe (23.1) runtime properties\nmsfragger.table.fix-mods={A549_FIX}\n"
                 f"msfragger.table.var-mods={A549_VAR}\n")
HOURS_AGO = 3


def _age(folder: Path, hours: float = HOURS_AGO) -> None:
    """Every file under folder modified `hours` ago: older than SETTLE_S, so no run waits."""
    t = time.time() - hours * 3600
    for p in Path(folder).rglob("*"):
        if p.is_file():
            os.utime(p, (t, t))


def _manifest(raws: list[Path]) -> bytes:
    return "".join(f"{r}\tEXP\t{i}\tDDA\n" for i, r in enumerate(raws, 1)).encode()


def _raws(folder: Path, n: int = 2) -> list[Path]:
    out = [folder / f"S_{i}.raw" for i in range(1, n + 1)]
    for r in out:
        r.parent.mkdir(parents=True, exist_ok=True)
        r.write_bytes(b"raw")
    return out


@pytest.fixture
def drives(lab, monkeypatch):
    """A lab PC's disks under tmp_path: an isoDTB run and a TMT run in Fragpipe_General (not Ionomos jobs), an
    isoTOP run, a folder of only psm.tsv files and the test bed's fake run on a second "drive"; the hand-off on with the
    sweep, into a Dropbox folder whose path has spaces."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    # _age sets modified times only; a test can't set a file's creation time, which Windows reports (atlas._born)
    monkeypatch.setattr(atlas, "_born", lambda st: 0.0)
    dropbox = lab["root"] / "Team Dropbox" / "Nicholas Foster Data"
    dropbox.mkdir(parents=True)
    dest = dropbox / "fragpipe-runs"
    disk = lab["root"] / "D"
    iso = lab["general"] / "EJQ" / "2025-03-04_isoDTB_EJQ-1-100"
    iso_raws = _raws(iso / "raw")
    _run_folder(iso / "fragpipe", {
        "fragpipe.workflow": WORKFLOW.encode(), "fragpipe-files.fp-manifest": _manifest(iso_raws),
        "fragpipe.job": b"job", "combined_modified_peptide_label_quant.tsv": b"Protein\tx\nsp|P1|A_HUMAN\t1\n",
        "log_2025-03-04_10-00-00.txt": b"MSFragger\ndone", "exp_1/psm.tsv": b"Peptide\n" * 30,
        "exp_2/psm.tsv": b"Peptide\n" * 20, "exp_1/S_1.pin": _pin(200), "exp_1/S_1_edited.pin": _pin(200, "\t0.5"),
        "exp_2/S_2.pin": _pin(150), "exp_1/interact-S_1.pep.xml": b"<x/>"})
    tmt = lab["general"] / "Chris" / "2025-05-06_TMT_comp"
    tmt_raws = _raws(tmt / "plex1")
    _run_folder(tmt, {"plex1/annotation.txt": b"126 DMSO_1\n127N KB02_1\n"})
    _run_folder(tmt / "fragpipe", {
        "fragpipe.workflow": TMT_WORKFLOW.encode(), "fragpipe-files.fp-manifest": _manifest(tmt_raws),
        "plex1/psm.tsv": b"Peptide\n" * 10, "plex1/S_1.pin": _pin(80)})
    _run_folder(disk / "Aman" / "isotop", {"fragpipe.workflow": ISOTOP_WORKFLOW.encode(), "x/psm.tsv": b"P\n"})
    _run_folder(disk / "old" / "loose", {"a/psm.tsv": b"P\n", "b/psm.tsv": b"P\n"})
    _run_folder(disk / "bed" / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n",
                                            "fragger.params": b"# FAKE MSFragger parameters\n"})
    _age(lab["root"])
    cfg = _write_cfg(lab, enabled=True, handoff_dir=str(dest), sweep=True,
                     sweep_roots=[str(lab["general"]), str(disk)])
    return {"cfg": cfg, "lab": lab, "dest": dest, "disk": disk, "iso": iso, "tmt": tmt, "general": lab["general"]}


def _tmt_on(drives, **more):
    return _write_cfg(drives["lab"], enabled=True, handoff_dir=str(drives["dest"]), sweep=True,
                      sweep_roots=[str(drives["general"]), str(drives["disk"])], methods=["isoDTB", "TMT"], **more)


def _sweep_items(cfg) -> list[attention.Item]:
    return [i for i in attention.items(cfg.log_dir) if i.kind == atlassweep.ATTENTION_KIND]


def _outcome(p, folder: Path) -> atlassweep.RunResult:
    want = atlas.windows_path(folder)
    (r,) = [r for r in p.runs if r.run == want]
    return r


# ------------------------------------------------------------------------------------------- settings --


@pytest.mark.parametrize(("raw", "want"), [
    ({"sweep": True}, {"sweep": True}),
    ({"sweep_roots": "D:\\"}, {"sweep_roots": ["D:\\"]}),
    ({"sweep_roots": ["C:\\Fragpipe_General", "D:\\Old Runs"]}, {"sweep_roots": ["C:\\Fragpipe_General",
                                                                                 "D:\\Old Runs"]}),
    ({"sweep_exclude": ["D:\\Backups"]}, {"sweep_exclude": ["D:\\Backups"]}),
    ({"sweep_hours": 6, "sweep_min_free_gb": 25.5}, {"sweep_hours": 6.0, "sweep_min_free_gb": 25.5}),
])
def test_sweep_settings_read(raw, want):
    assert atlas.settings_from(raw) == {**atlas.DEFAULTS, **want}


def test_the_sweep_is_off_by_default_and_searches_every_fixed_drive():
    s = atlas.settings_from(None)
    assert (s["sweep"], s["sweep_roots"], s["sweep_exclude"], s["sweep_hours"], s["sweep_min_free_gb"]) == \
        (False, [], [], 24, 40)
    s["sweep_roots"].append("X:\\")
    assert atlas.DEFAULTS["sweep_roots"] == []  # each read gets its own list


@pytest.mark.parametrize(("raw", "words"), [
    ({"sweep": "yes"}, "sweep must be true or false"),
    ({"sweep_roots": [3]}, "sweep_roots must be a list of folders"),
    ({"sweep_roots": ["Fragpipe_General"]}, "sweep_roots must hold full folder paths"),
    ({"sweep_exclude": ["backups"]}, "sweep_exclude must hold full folder paths"),
    ({"sweep_hours": 0}, "sweep_hours must be a number from 1 to 720"),
    ({"sweep_min_free_gb": -1}, "sweep_min_free_gb must be a number"),
])
def test_sweep_settings_refuse(raw, words):
    with pytest.raises(atlas.AtlasError, match=re.escape(words)):
        atlas.settings_from(raw)


def test_the_sweep_settings_survive_a_save(tmp_path):
    d = configio.defaults(str(tmp_path))
    d["atlas"] = {**d["atlas"], "enabled": True, "sweep": True,
                  "handoff_dir": r"C:\Users\Lab Account\Team Dropbox\Nicholas Foster Data\fragpipe-runs",
                  "sweep_roots": ["C:\\", "D:\\Old Runs"], "sweep_exclude": [r"D:\Backups"], "sweep_hours": 12}
    p = tmp_path / "config.yaml"
    p.write_text(configio.dump_config(d), encoding="utf-8")
    back = configio.read_config(p)["atlas"]
    assert atlas.settings_from(back) == {**atlas.DEFAULTS, "enabled": True, "sweep": True,
                                         "handoff_dir": d["atlas"]["handoff_dir"], "sweep_roots": ["C:\\",
                                                                                                  "D:\\Old Runs"],
                                         "sweep_exclude": [r"D:\Backups"], "sweep_hours": 12}


# ------------------------------------------------------------------------------------------- the walk --


def test_the_walk_finds_runs_as_harvest_does(drives, tmp_path):
    w = atlassweep.walk([str(drives["general"]), str(drives["disk"])])
    got = {Path(r).relative_to(drives["lab"]["root"]).as_posix() for r in w.runs}
    assert got == {"Fragpipe_General/EJQ/2025-03-04_isoDTB_EJQ-1-100/fragpipe",
                   "Fragpipe_General/Chris/2025-05-06_TMT_comp/fragpipe",
                   "D/Aman/isotop",            # a marker; its x/psm.tsv is the run's (one level down)
                   "D/old/loose/a", "D/old/loose/b",  # psm.tsv with no run folder above: a run each
                   "D/bed/fragpipe"}
    assert w.folders > 10 and not w.errors and not w.stopped


def test_the_walk_skips_harvests_names_links_and_the_excluded(tmp_path):
    root = tmp_path / "C"
    for rel in ("AppData/Local/x/fragpipe.workflow", "Windows/y/psm.tsv", "Users/me/MSBooster/psm.tsv",
                "Backups/old/fragpipe.workflow", "keep/run/fragpipe.workflow", "keep/run/exp_1/sub/psm.tsv"):
        _run_folder(root, {rel: b"x"})
    try:
        (root / "link").symlink_to(root / "keep", target_is_directory=True)
    except (OSError, NotImplementedError):
        pass  # this account can't make a folder link (Windows without the privilege): the rest still holds
    w = atlassweep.walk([str(root)], exclude=[str(root / "backups")])  # case is ignored, as harvest's -ieq
    assert [Path(r).relative_to(root).as_posix() for r in w.runs] == ["keep/run"]  # exp_1/sub: two levels down
    assert w.skipped >= 4


def test_a_dropbox_folder_is_never_searched_even_unnamed(tmp_path):
    root = tmp_path / "C"
    _run_folder(root, {"Users/x/Other Dropbox/.dropbox": b"{}", "Users/x/Other Dropbox/copy/fragpipe.workflow": b"x",
                       "Fragpipe_General/EJQ/run/fragpipe.workflow": b"x"})
    w = atlassweep.walk([str(root)])  # no info.json names it (another Windows account's Dropbox)
    assert [Path(r).relative_to(root).as_posix() for r in w.runs] == ["Fragpipe_General/EJQ/run"]


def test_an_unreadable_folder_is_noted_and_passed_over(tmp_path, monkeypatch):
    root = tmp_path / "C"
    _run_folder(root, {"a/fragpipe.workflow": b"x", "locked/b/fragpipe.workflow": b"x"})
    real = os.scandir

    def scandir(p):
        if str(p).endswith("locked"):
            raise PermissionError(13, "Access is denied")
        return real(p)

    monkeypatch.setattr(atlassweep.os, "scandir", scandir)
    w = atlassweep.walk([str(root)])
    assert [Path(r).name for r in w.runs] == ["a"]
    assert w.errors == [[str(root / "locked"), "Access is denied"]]


def test_the_walk_stops_when_asked(tmp_path):
    root = tmp_path / "C"
    for i in range(600):
        (root / f"d{i}").mkdir(parents=True)
    w = atlassweep.walk([str(root)], stop=lambda: True)
    assert w.stopped and w.folders == 500


@pytest.mark.parametrize(("path", "owner"), [
    (r"C:\Fragpipe_General\EJQ\20261019_isoDTB\fragpipe", "EJQ"),
    (r"C:\Users\Lab Account\Desktop\member\run", "Lab Account"),
    (r"D:\Taylor_Elements\2024\TMT\fragpipe", "Taylor_Elements"),
    (r"D:\Fragpipe_General\member2\x", "member2"),
    (r"\\server\share\x", "server"),
])
def test_the_owner_is_harvests_guess(path, owner):
    assert atlas.owner_guess(path) == owner


def test_exclusions_hold_the_atlas_and_dropbox_folders(tmp_path):
    info = tmp_path / "local" / "Dropbox" / "info.json"
    info.parent.mkdir(parents=True)
    info.write_text(json.dumps({"business": {"path": str(tmp_path / "Team Dropbox")}}), encoding="utf-8")
    s = atlas.settings_from({"handoff_dir": str(tmp_path / "Team Dropbox" / "x" / "fragpipe-runs"),
                             "sweep_exclude": [str(tmp_path / "Backups")]})
    assert atlassweep.exclusions(s, env={"LOCALAPPDATA": str(tmp_path / "local")}) == \
        [s["handoff_dir"], str(tmp_path / "Team Dropbox"), str(tmp_path / "Backups")]


@pytest.mark.skipif(os.name != "nt", reason="Windows' drives")
def test_the_fixed_drives_hold_c():
    assert "C:\\" in atlassweep.fixed_drives()


def test_a_files_birth_is_its_creation_time_on_windows_only(monkeypatch):
    from types import SimpleNamespace

    st = SimpleNamespace(st_birthtime=5.0, st_ctime=3.0)
    monkeypatch.setattr(atlas, "os", SimpleNamespace(name="posix"))  # the module's os, never os.name itself
    assert atlas._born(st) == 0.0  # st_ctime is the inode's change time there
    monkeypatch.setattr(atlas, "os", SimpleNamespace(name="nt"))
    assert atlas._born(st) == 5.0
    assert atlas._born(SimpleNamespace(st_ctime=3.0)) == 3.0  # before 3.12: st_ctime is the creation time


def test_off_windows_there_are_no_fixed_drives(monkeypatch):
    monkeypatch.setattr(atlassweep, "os", type("os", (), {"name": "posix"}))
    assert atlassweep.fixed_drives() == []


# ------------------------------------------------------------------------------------------- a pass --


def test_a_pass_sends_the_isodtb_run_with_its_decoys_and_design(drives):
    before = _snapshot(drives["general"]), _snapshot(drives["disk"])
    p = atlassweep.sweep(drives["cfg"])
    assert p.ok, p.message
    iso = _outcome(p, drives["iso"] / "fragpipe")
    assert iso.outcome == atlassweep.SENT and iso.kind == "isoDTB" and iso.owner == "EJQ"
    assert (iso.pins, iso.design) == (2, 0)  # no experiment.yaml, no annotation beside the raws
    run_dest = atlas.destination(drives["iso"] / "fragpipe", drives["dest"])
    got = _files(run_dest)
    assert {"exp_1/psm.tsv", "exp_2/psm.tsv", "exp_1/S_1_edited.pin.gz", "exp_2/S_2.pin.gz", "fragpipe.workflow",
            names.ATLAS_HANDOFF_FILE} <= set(got)
    assert not any(r.endswith((".pep.xml", "S_1.pin.gz", ".raw")) for r in got)
    with gzip.open(_long(run_dest / "exp_2" / "S_2.pin.gz")) as fh:
        assert fh.read() == _pin(150)
    rec = json.loads(_read(run_dest / names.ATLAS_HANDOFF_FILE))
    assert rec["contract"] == "ionomos-atlas-handoff/2" and rec["probe_class"] == "isoDTB" and "job" not in rec
    f = rec["found"]
    assert (f["owner"], f["experiment"], f["experiment_folder"]) == ("EJQ", "fragpipe", "")
    assert f["fragpipe_version"] == "24.0" and f["raw_files"] == [str(r) for r in sorted((drives["iso"] / "raw")
                                                                                          .glob("*.raw"))]
    (csv_path,) = [r for r in _records(drives["dest"]) if atlas.run_id(iso.run) in r.name]
    assert re.fullmatch(r"runs-ionomos-\d{8}-\d{6}-found-[0-9a-f]{12}\.csv", csv_path.name)
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    (row,) = list(csv.DictReader(io.StringIO(raw[3:].decode("utf-8"))))
    assert list(row) == list(atlas.CSV_COLUMNS)
    assert (row["Run"], row["Owner"], row["ProbeClass"], row["IonomosJob"], row["IonomosMethod"]) == \
        (iso.run, "EJQ", "isoDTB", "", "")
    assert row["HandoffSha256"] == iso.handoff_sha256 and row["PsmFiles"] == "2"
    assert (_snapshot(drives["general"]), _snapshot(drives["disk"])) == before  # everything searched is only read


def test_what_a_pass_leaves(drives):
    p = atlassweep.sweep(drives["cfg"])
    whys = {Path(r.run).relative_to(drives["lab"]["root"]).as_posix(): (r.outcome, r.why) for r in p.runs}
    assert whys["Fragpipe_General/Chris/2025-05-06_TMT_comp/fragpipe"] == \
        ("left", "probe class TMT (the TMT switch is off)")
    assert whys["D/Aman/isotop"] == ("left", "probe class isoTOP")
    assert whys["D/old/loose/a"] == ("left", "probe class unknown")  # no workflow
    assert whys["D/bed/fragpipe"] == ("left", "made by the test bed's fake FragPipe")
    assert p.summary()["sent"] == 1 and p.summary()["left"] == 5


def test_with_the_tmt_switch_on_a_tmt_run_goes_as_tmt_with_its_annotation(drives):
    cfg = _tmt_on(drives)
    p = atlassweep.sweep(cfg)
    assert p.ok, p.message
    tmt = _outcome(p, drives["tmt"] / "fragpipe")
    assert (tmt.outcome, tmt.kind, tmt.owner, tmt.pins, tmt.design) == ("sent", "TMT", "Chris", 1, 1)
    rec = json.loads(_read(atlas.destination(drives["tmt"] / "fragpipe", drives["dest"]) / names.ATLAS_HANDOFF_FILE))
    assert rec["probe_class"] == "TMT"
    (d,) = rec["design"]
    assert d["kind"] == "annotation.txt" and d["source"] == atlas.windows_path(drives["tmt"] / "plex1" /
                                                                               "annotation.txt")
    assert _read(drives["dest"].joinpath(*d["path"].split("/"))) == b"126 DMSO_1\n127N KB02_1\n"


def test_an_unchanged_run_is_not_read_again(drives, monkeypatch):
    first = atlassweep.sweep(drives["cfg"])
    assert first.ok
    monkeypatch.setattr(atlas, "transfer", lambda *a, **k: pytest.fail("an unchanged run was read again"))
    again = atlassweep.sweep(drives["cfg"])
    assert again.ok and _outcome(again, drives["iso"] / "fragpipe").outcome == atlassweep.UNCHANGED
    assert len(_records(drives["dest"])) == 1


def test_a_run_that_changed_is_sent_again(drives):
    atlassweep.sweep(drives["cfg"])
    _run_folder(drives["iso"] / "fragpipe", {"exp_3/psm.tsv": b"Peptide\n" * 5})
    _age(drives["iso"])
    p = atlassweep.sweep(drives["cfg"])
    iso = _outcome(p, drives["iso"] / "fragpipe")
    assert iso.outcome == atlassweep.SENT and iso.copied == 1  # only the new file; the rest is there already
    assert len(_records(drives["dest"])) == 2


def test_a_run_still_being_written_waits(drives):
    (drives["iso"] / "fragpipe" / "exp_2" / "psm.tsv").write_bytes(b"Peptide\n" * 21)  # just now
    p = atlassweep.sweep(drives["cfg"])
    iso = _outcome(p, drives["iso"] / "fragpipe")
    assert iso.outcome == atlassweep.WAITING and "may still be being written" in iso.why
    assert not _records(drives["dest"]) and p.ok


def test_a_conflict_waits_for_a_person_and_a_manual_pass_tries_again(drives):
    run_dest = atlas.destination(drives["iso"] / "fragpipe", drives["dest"])
    _write(run_dest / "exp_1" / "psm.tsv", b"someone else's copy\n")
    p = atlassweep.sweep(drives["cfg"])
    iso = _outcome(p, drives["iso"] / "fragpipe")
    assert iso.outcome == atlassweep.CONFLICT and not p.ok
    assert set(_files(run_dest)) == {"exp_1/psm.tsv"}  # nothing copied
    (item,) = _sweep_items(drives["cfg"])
    assert item.severity == "warning" and "1 conflict" in item.message
    # the watcher's next pass leaves it (nothing changed); a person moves the old copy aside and asks again
    auto = atlassweep.sweep(drives["cfg"])
    assert _outcome(auto, drives["iso"] / "fragpipe").outcome == atlassweep.CONFLICT
    os.rename(_long(run_dest / "exp_1" / "psm.tsv"), _long(run_dest / "exp_1" / "psm.tsv.old"))
    again = atlassweep.sweep(drives["cfg"], trigger="app")
    assert again.ok and _outcome(again, drives["iso"] / "fragpipe").outcome == atlassweep.SENT
    assert _sweep_items(drives["cfg"]) == []


def test_an_ionomos_jobs_run_is_queued_as_the_job(drives):
    exp = drives["general"] / "EJQ" / "20261019_isoDTB_EJQ-2-027"
    run = _run_folder(exp / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n"})
    led = Ledger(drives["cfg"].database)
    jid = led.insert(Job(inbox_name=exp.name, user="EJQ", method="isoDTB", dest_dir=str(exp)))
    led.set_status(jid, "done")
    (exp / names.STATUS_FILE).write_text(json.dumps({"status": "done", "run": {"workdir": str(run)}}),
                                         encoding="utf-8")
    _age(exp)
    p = atlassweep.sweep(drives["cfg"])
    r = _outcome(p, run)
    assert (r.outcome, r.job_id) == (atlassweep.JOB, jid) and "queued as job" in r.why
    assert atlas.state(exp)["state"] == atlas.PENDING  # the hand-off loop sends it, with its job record
    assert not any(atlas.run_id(r.run) in c.name for c in _records(drives["dest"]))
    again = atlassweep.sweep(drives["cfg"])
    assert "waiting to be handed off" in _outcome(again, run).why  # queued once


def test_an_ionomos_folder_of_no_job_here_sends_its_latest_done_search_only(drives):
    exp = drives["general"] / "EJQ" / "20261019_isoDTB_EJQ-2-027"
    _run_folder(exp, {"experiment.yaml": b"analysis:\n  control: DMSO\n"})
    (exp / names.STATUS_FILE).write_text(json.dumps({"status": "done"}), encoding="utf-8")  # another PC's ledger
    run = _run_folder(exp / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n"})
    old = _run_folder(exp / "fragpipe_previous_20261019-120000",
                      {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"half\n"})
    _age(exp)
    p = atlassweep.sweep(drives["cfg"])
    r = _outcome(p, run)
    assert r.outcome == atlassweep.SENT and r.design == 1  # experiment.yaml, from its experiment folder
    rec = json.loads(_read(atlas.destination(run, drives["dest"]) / names.ATLAS_HANDOFF_FILE))
    assert rec["found"]["experiment"] == exp.name and rec["found"]["experiment_folder"] == atlas.windows_path(exp)
    assert _outcome(p, old).outcome == atlassweep.LEFT and "earlier attempt" in _outcome(p, old).why


@pytest.mark.parametrize(("status", "outcome"), [("failed", "left"), ("running", "waiting"), ("queued", "waiting")])
def test_an_ionomos_folder_not_done_sends_nothing(drives, status, outcome):
    exp = drives["general"] / "EJQ" / "20261019_isoDTB_EJQ-2-028"
    exp.mkdir(parents=True)
    (exp / names.STATUS_FILE).write_text(json.dumps({"status": status}), encoding="utf-8")
    run = _run_folder(exp / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n"})
    _age(exp)
    assert _outcome(atlassweep.sweep(drives["cfg"]), run).outcome == outcome


def test_an_earlier_attempt_of_a_job_is_left(drives):
    exp = drives["general"] / "EJQ" / "20261019_isoDTB_EJQ-2-027"
    run = _run_folder(exp / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n"})
    old = _run_folder(exp / "fragpipe_previous_x", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n"})
    led = Ledger(drives["cfg"].database)
    jid = led.insert(Job(inbox_name=exp.name, user="EJQ", method="isoDTB", dest_dir=str(exp)))
    led.set_status(jid, "done")
    (exp / names.STATUS_FILE).write_text(json.dumps({"status": "done", "run": {"workdir": str(run)}}),
                                         encoding="utf-8")
    _age(exp)
    p = atlassweep.sweep(drives["cfg"])
    assert _outcome(p, run).outcome == atlassweep.JOB
    r = _outcome(p, old)
    assert (r.outcome, r.job_id) == (atlassweep.LEFT, jid) and "only its latest search goes" in r.why


def test_ionomos_own_folders_are_not_searched(drives):
    """FragPipe's preflight dry runs (the setup check) are real workflows in the log folder; drops in the inbox."""
    logs, inbox = drives["cfg"].log_dir, drives["cfg"].inbox
    _run_folder(logs / "preflight" / "20261026-0900" / "isoDTB" / "fragpipe",
                {"fragpipe.workflow": WORKFLOW.encode(), "fragpipe.job": b"job", "exp_1/psm.tsv": b"P\n"})
    _run_folder(inbox / "drop" / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n"})
    _age(drives["lab"]["root"])
    cfg = _write_cfg(drives["lab"], enabled=True, handoff_dir=str(drives["dest"]), sweep=True,
                     sweep_roots=[str(drives["lab"]["root"])])
    p = atlassweep.sweep(cfg)
    assert not [r for r in p.runs if str(logs) in r.run or str(inbox) in r.run]
    assert atlassweep.exclusions(atlas.settings(cfg), env={}, cfg=cfg)[1:3] == [str(logs), str(inbox)]


def test_a_run_with_no_psm_tsv_is_left(drives):
    run = _run_folder(drives["disk"] / "dry" / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(),
                                                             "fragpipe-files.fp-manifest": b"x.raw\tEXP\t1\tDDA\n",
                                                             "fragpipe.job": b"job"})
    _age(drives["disk"])
    r = _outcome(atlassweep.sweep(drives["cfg"]), run)
    assert r.outcome == atlassweep.LEFT and "no psm.tsv" in r.why


@pytest.mark.parametrize(("var", "fix", "want"), [
    ("561.3387,C,true,1", "", "isoDTB"),
    ("561.3387,C,false,1; 15.9949,M,true,3", "", "other"),          # a label-free search copied from isoDTB
    ("561.3387,C,true,1; 229.16293,K,false,-1", "", "isoDTB"),      # its TMT row off
    ("", "229.16293,K,true,-1; 229.16293,n^,true,-1", "TMT"),
    ("561.3387", "", "isoDTB"),                                      # no enabled field: counts
    (A549_VAR, A549_FIX, "TMT"),                                     # TMT2 (D135); its 229.16293 row is off
    ("229.16293,S,false,1", A549_FIX.replace("225.1558", "0.0"), "other"),
    ("", "304.20715,K,true,-1; 304.20715,n^,true,-1", "TMT"),       # TMTpro
    ("", "295.18959,K,true,-1", "TMT"),                              # TMTpro zero
    ("224.1525,n^,true,1", "", "TMT"),                               # TMT0
])
def test_only_the_modifications_turned_on_count(var, fix, want):
    assert atlassweep.enabled_class({"VarMods": var, "FixMods": fix}) == want


def test_a_tmt2_run_goes_as_tmt_with_tmt_integrators_tables(drives):
    """One of Kosuke's A549_nucleus_TMT-check runs (D135): TMT by its TMT2 mass, so it goes with the TMT switch on,
    and its tmt-report tables go with it, to tmt-report/<name> under the run's destination."""
    run = _run_folder(drives["disk"] / "Kosuke" / "A549_nucleus_TMT-check" / "fragpipe", {
        "fragpipe.workflow": A549_WORKFLOW.encode(), "nucleus/psm.tsv": b"Peptide\n" * 4,
        "tmt-report/abundance_protein_MD.tsv": b"Index\tNumberPSM\n", "tmt-report/ratio_gene_None.tsv": b"Index\n",
        "tmt-report/notes.txt": b"not a table"})
    _age(drives["disk"])
    off = _outcome(atlassweep.sweep(drives["cfg"]), run)
    assert (off.outcome, off.kind, off.why) == ("left", "TMT", "probe class TMT (the TMT switch is off)")
    on = _outcome(atlassweep.sweep(_tmt_on(drives)), run)
    assert (on.outcome, on.kind) == ("sent", "TMT"), on.why
    run_dest = atlas.destination(run, drives["dest"])
    assert _read(run_dest / "tmt-report" / "abundance_protein_MD.tsv") == b"Index\tNumberPSM\n"
    assert "tmt-report/notes.txt" not in _files(run_dest)
    rec = json.loads(_read(run_dest / names.ATLAS_HANDOFF_FILE))
    assert rec["probe_class"] == "TMT" and rec["found"]["probe_class_by_masses"] == "TMT"
    assert {"tmt-report/abundance_protein_MD.tsv", "tmt-report/ratio_gene_None.tsv"} <= {f["path"] for f in rec["files"]}


def test_a_run_sent_by_the_earlier_rule_goes_again_and_nothing_there_is_touched(drives, monkeypatch):
    """D135 changed what is taken. A found run sent before (its tmt-report tables left behind, an earlier search's
    .pin taken) has another fingerprint now, so the next pass sends it again: the new files are copied, the rest
    compared and kept; nothing at the destination is removed or overwritten but the run's ionomos-handoff.json, whose
    new version lists the stale .pin in left[] and not in files[] (the receiver goes by files[]). The .pin.gz copied
    earlier stays where it is."""
    cfg = _tmt_on(drives)
    run = drives["tmt"] / "fragpipe"
    stale = "plex1/Old_search_F1_edited.pin"
    _run_folder(run, {"tmt-report/abundance_gene_MD.tsv": b"Index\tDMSO_1\n", "tmt-report/ratio_gene_MD.tsv": b"I\n",
                      stale: _pin(40)})
    _age(drives["tmt"])
    with monkeypatch.context() as m:  # the rule before D135: no tmt-report tables, every .pin
        m.setattr(atlas, "TMT_REPORT_ALLOW", ())
        m.setattr(atlas, "_manifest_groups", lambda run_dir: {})
        first = atlassweep.sweep(cfg)
    assert _outcome(first, run).outcome == atlassweep.SENT and first.ok
    run_dest = atlas.destination(run, drives["dest"])
    assert stale + ".gz" in _files(run_dest) and "tmt-report/abundance_gene_MD.tsv" not in _files(run_dest)

    def there() -> dict:
        return {r: (os.stat(f).st_size, os.stat(f).st_mtime_ns, hashlib.sha256(_read(f)).hexdigest())
                for r, f in _files(drives["dest"]).items()}

    before = there()
    second = atlassweep.sweep(cfg)
    assert second.ok, second.message
    r = _outcome(second, run)
    assert (r.outcome, r.copied) == (atlassweep.SENT, 2)  # only the two tables; the rest is there already
    assert _outcome(second, drives["iso"] / "fragpipe").outcome == atlassweep.UNCHANGED  # its takes are the same
    after = there()
    prefix = "/".join(atlas.layout_parts(atlas.windows_path(run))) + "/"
    assert set(before) <= set(after)  # nothing removed
    assert {k for k in before if after[k] != before[k]} == {prefix + names.ATLAS_HANDOFF_FILE}  # nor overwritten
    new = set(after) - set(before)
    assert {k[len(prefix):] for k in new if k.startswith(prefix)} == {"tmt-report/abundance_gene_MD.tsv",
                                                                       "tmt-report/ratio_gene_MD.tsv"}
    assert len([k for k in new if k.startswith(names.ATLAS_LOG_DIR + "/")]) == 1  # a second runs CSV
    rec = json.loads(_read(run_dest / names.ATLAS_HANDOFF_FILE))
    assert {"path": stale, "size": len(_pin(40)), "reason": "its raw file is not in this run's manifest"} in rec["left"]
    assert stale + ".gz" not in {f["path"] for f in rec["files"]} and stale + ".gz" in _files(run_dest)
    assert {"tmt-report/abundance_gene_MD.tsv", "tmt-report/ratio_gene_MD.tsv"} <= {f["path"] for f in rec["files"]}
    assert _outcome(atlassweep.sweep(cfg), run).outcome == atlassweep.UNCHANGED  # and once only


def test_a_label_free_workflow_copied_from_isodtb_is_left(drives):
    wf = WORKFLOW.replace("561.3387,C,true,1", "561.3387,C,false,1")
    run = _run_folder(drives["disk"] / "lfq" / "fragpipe", {"fragpipe.workflow": wf.encode(), "e/psm.tsv": b"P\n"})
    _age(drives["disk"])
    r = _outcome(atlassweep.sweep(drives["cfg"]), run)
    assert (r.outcome, r.kind) == ("left", "other") and "harvest reads isoDTB" in r.why


def test_a_run_copied_in_just_now_waits_though_its_files_are_old(drives, monkeypatch):
    born = time.time() - 60  # a copy keeps the modified time; on Windows the creation time says when it came
    real = atlas._born
    monkeypatch.setattr(atlas, "_born", lambda st: born if st.st_size == len(b"Peptide\n" * 30) else real(st))
    r = _outcome(atlassweep.sweep(drives["cfg"]), drives["iso"] / "fragpipe")
    assert r.outcome == atlassweep.WAITING and "made or changed 1 min ago" in r.why


def test_the_free_space_probe_reads_the_drive(tmp_path):
    assert real_free_bytes(tmp_path) > 0  # the tests' fixture turns it off (conftest); the real one reads the disk


def test_a_lock_left_by_a_process_that_is_gone_is_taken_over(drives, monkeypatch):
    from ionomos import health

    lock = drives["cfg"].log_dir / names.ATLAS_SWEEP_LOCK
    lock.write_text(f"99999999 {atlas._iso()}\n")
    monkeypatch.setattr(health, "process_started", lambda pid: None)
    p = atlassweep.sweep(drives["cfg"])
    assert p.ok and not lock.exists()


def test_the_saved_pass_says_how_it_ended(drives):
    p = atlassweep.sweep(drives["cfg"])
    last = atlassweep.last_pass(drives["cfg"])
    assert last["ok"] is True and last["message"] == p.message and last["stopped"] == "" and last["sent"] == 1


def test_a_search_starting_is_a_soft_stop_with_no_attention_item(drives):
    p = atlassweep.sweep(drives["cfg"], stop=lambda: True)
    assert p.soft and p.stopped.startswith("a search started") and _sweep_items(drives["cfg"]) == []
    for i in range(520):  # the walk looks every 500 folders
        (drives["disk"] / "many" / f"d{i}").mkdir(parents=True)
    walked = atlassweep.sweep(drives["cfg"], walk_stop=lambda: True)
    assert walked.soft and "stopped while the drives were being searched" in walked.stopped


def test_overlapping_roots_are_walked_once(tmp_path):
    root = tmp_path / "C"
    _run_folder(root, {"Fragpipe_General/EJQ/run/fragpipe.workflow": b"x"})
    w = atlassweep.walk([str(root), str(root / "Fragpipe_General"), str(root)])
    assert len(w.runs) == 1


def test_a_dry_run_writes_nothing_anywhere(drives):
    cfg = _write_cfg(drives["lab"], enabled=False, handoff_dir=str(drives["dest"]),
                     sweep_roots=[str(drives["general"]), str(drives["disk"])])  # works while the hand-off is off
    before = _snapshot(drives["lab"]["root"])
    p = atlassweep.sweep(cfg, trigger="cli", dry_run=True)
    assert p.ok and not p.setup
    iso = _outcome(p, drives["iso"] / "fragpipe")
    assert iso.outcome == atlassweep.SENT and iso.to_copy == 9 and iso.mb_to_copy >= 0
    assert (iso.pins, iso.design, iso.fragpipe_version, iso.var_mods) == (2, 0, "24.0", "561.3387,C,true,1")
    assert "1 would go to the atlas (9 files" in p.message
    assert _snapshot(drives["lab"]["root"]) == before
    assert not drives["dest"].exists() and not (cfg.log_dir / names.ATLAS_SWEEP_STATE).exists()


def test_the_copying_stops_before_the_drive_runs_low(drives, monkeypatch):
    monkeypatch.setattr(atlas, "_free_bytes", lambda path: 30 * 1024 ** 3)
    p = atlassweep.sweep(drives["cfg"])
    assert not p.ok and not p.soft and "copying stops above 40 GB free (atlas.sweep_min_free_gb)" in p.stopped
    assert not _records(drives["dest"]) and set(_files(drives["dest"])) <= set()  # nothing copied at all
    (item,) = _sweep_items(drives["cfg"])
    assert "stops above 40 GB free" in " ".join(item.causes)
    loop = atlassweep.SweepLoop(lambda: drives["cfg"])  # a hard stop is tried again in an hour, not in 10 minutes
    now = datetime.now().astimezone()
    assert loop.due(drives["cfg"], now + timedelta(seconds=atlassweep.RESUME_S + 1)) == ""
    assert loop.due(drives["cfg"], now + timedelta(seconds=atlassweep.HARD_RETRY_S + 1)) == "retry"


def test_a_jobs_hand_off_keeps_fragpipes_own_floor(drives, monkeypatch):
    """A job's hand-off is small: it keeps fragpipe.min_free_gb (20), not the sweep's 40."""
    d = dict(drives["lab"]["cfg_dict"])
    d["fragpipe"] = {**d["fragpipe"], "min_free_gb": 20}
    d["atlas"] = {"enabled": True, "handoff_dir": str(drives["dest"])}
    drives["lab"]["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    cfg = load(drives["lab"]["cfg_path"])
    exp = drives["general"] / "EJQ" / "20261019_isoDTB_EJQ-2-027"
    run = _run_folder(exp / "fragpipe", {"fragpipe.workflow": WORKFLOW.encode(), "exp_1/psm.tsv": b"P\n"})
    led = Ledger(cfg.database)
    jid = led.insert(Job(inbox_name=exp.name, user="EJQ", method="isoDTB", dest_dir=str(exp)))
    led.set_status(jid, "done")
    (exp / names.STATUS_FILE).write_text(json.dumps({"status": "done", "run": {"workdir": str(run)}}),
                                         encoding="utf-8")
    monkeypatch.setattr(atlas, "_free_bytes", lambda path: 15 * 1024 ** 3)
    res = atlas.send(cfg, led.get(jid))
    assert not res.ok and res.low_space and res.state == atlas.FAILED and "(fragpipe.min_free_gb)" in res.message
    assert not _files(drives["dest"])  # nothing copied
    assert atlas.state(exp)["state"] == atlas.FAILED and atlas.state(exp)["next_try"]  # tried again later
    monkeypatch.setattr(atlas, "_free_bytes", lambda path: 30 * 1024 ** 3)
    assert atlas.send(cfg, led.get(jid)).ok  # above FragPipe's floor, though under the sweep's 40


def test_no_dropbox_stops_the_pass(drives):
    cfg = _write_cfg(drives["lab"], enabled=True, handoff_dir=str(drives["lab"]["root"] / "nope" / "fragpipe-runs"),
                     sweep=True, sweep_roots=[str(drives["general"])])
    p = atlassweep.sweep(cfg)
    assert not p.ok and "can't be reached" in p.stopped
    assert not (drives["lab"]["root"] / "nope").exists()


@pytest.mark.parametrize(("block", "words"), [
    ({"enabled": False}, "the atlas hand-off is off"),
    ({"enabled": True}, "atlas.handoff_dir is not set"),
])
def test_nothing_is_tried_without_the_setup(drives, block, words):
    p = atlassweep.sweep(_write_cfg(drives["lab"], sweep=True, sweep_roots=[str(drives["general"])], **block))
    assert p.setup and words in p.message and not p.runs


def test_no_roots_off_windows_is_the_setup(drives, monkeypatch):
    monkeypatch.setattr(atlassweep, "fixed_drives", lambda: [])  # on Windows: never the runner's own C:
    cfg = _write_cfg(drives["lab"], enabled=True, handoff_dir=str(drives["dest"]), sweep=True)
    p = atlassweep.sweep(cfg)
    assert p.setup and "no folders to search" in p.message


def test_a_pass_stops_between_runs_and_the_next_goes_on(drives):
    cfg = _tmt_on(drives)
    calls = []
    p = atlassweep.sweep(cfg, stop=lambda: calls.append(1) or len(calls) > 1)
    assert p.stopped.startswith("a search started") and len(p.runs) == 1
    st = atlassweep.load_state(cfg)
    assert st["passes"][-1]["stopped"] and st["walk"]["runs"]
    again = atlassweep.sweep(cfg, reuse_walk=True)
    assert again.ok and {r.outcome for r in again.runs} >= {"sent"} and len(again.runs) == 6


def test_another_process_sweeping_is_left_to_it(drives):
    lock = drives["cfg"].log_dir / names.ATLAS_SWEEP_LOCK
    lock.write_text("123 now\n")
    p = atlassweep.sweep(drives["cfg"])
    assert not p.ok and "another Ionomos process" in p.message and not p.runs
    assert lock.exists()


# -------------------------------------------------------------------------------------------- the loop --


def test_when_a_pass_is_due(drives):
    loop = atlassweep.SweepLoop(lambda: drives["cfg"])
    now = datetime.now().astimezone()
    assert loop.due(drives["cfg"], now) == "first"
    atlassweep.sweep(drives["cfg"])
    assert loop.due(drives["cfg"], now) == ""
    assert loop.due(drives["cfg"], now + timedelta(hours=25)) == "daily"
    daily_off = _write_cfg(drives["lab"], enabled=True, handoff_dir=str(drives["dest"]), sweep=False)
    assert loop.due(daily_off, now + timedelta(hours=25)) == ""
    atlassweep.request_now(drives["cfg"].log_dir)
    assert loop.due(drives["cfg"], now) == "asked"
    assert loop.due(daily_off, now) == "asked"  # Search now, while the daily sweep is off
    assert loop.due(_write_cfg(drives["lab"], enabled=False, sweep=True), now) == ""


def test_the_loop_runs_a_pass_when_asked_and_takes_the_request(drives):
    loop = atlassweep.SweepLoop(lambda: drives["cfg"])
    atlassweep.sweep(drives["cfg"])
    atlassweep.request_now(drives["cfg"].log_dir)
    p = loop.run_once()
    assert p is not None and p.trigger == "app"
    assert not (drives["cfg"].log_dir / names.ATLAS_SWEEP_NOW).exists()
    assert loop.run_once() is None  # nothing due now


def test_the_loop_waits_while_a_search_runs(drives):
    led = Ledger(drives["cfg"].database)
    jid = led.insert(Job(inbox_name="x", user="EJQ", method="isoDTB", dest_dir=str(drives["general"] / "EJQ" / "x")))
    led.set_status(jid, "running")
    loop = atlassweep.SweepLoop(lambda: drives["cfg"])
    assert loop.run_once() is None and not _records(drives["dest"])
    led.set_status(jid, "done")
    assert loop.run_once() is not None


def test_a_resumed_pass_goes_on_after_a_while(drives):
    cfg = _tmt_on(drives)
    atlassweep.sweep(cfg, stop=lambda: True)
    loop = atlassweep.SweepLoop(lambda: cfg)
    now = datetime.now().astimezone()
    assert loop.due(cfg, now) == ""
    assert loop.due(cfg, now + timedelta(seconds=atlassweep.RESUME_S + 1)) == "resume"


# ----------------------------------------------------------------------------------------- the commands --


def test_the_sweep_command(drives, capsys):
    from ionomos.cli import main

    cfg_path = str(drives["lab"]["cfg_path"])
    assert main(["--config", cfg_path, "atlas", "sweep", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would go (probe class, owner: runs):" in out and "isoDTB  EJQ" in out
    assert not drives["dest"].exists()
    assert main(["--config", cfg_path, "atlas", "sweep", "--root", str(drives["general"])]) == 0
    out = capsys.readouterr().out
    assert "1 sent" in out and len(_records(drives["dest"])) == 1
    assert main(["--config", cfg_path, "atlas", "sweep", "--dry-run", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["unchanged"] == 0 and {r["outcome"] for r in data["runs"]} >= {"sent", "left"}
    assert main(["--config", cfg_path, "atlas", "status"]) == 0
    assert "drive sweep on: last search" in capsys.readouterr().out


def test_the_sweep_command_while_off_is_exit_2(drives, capsys):
    from ionomos.cli import main

    _write_cfg(drives["lab"], enabled=False, sweep_roots=[str(drives["general"])])
    assert main(["--config", str(drives["lab"]["cfg_path"]), "atlas", "sweep"]) == 2
    assert "hand-off is off" in capsys.readouterr().err
