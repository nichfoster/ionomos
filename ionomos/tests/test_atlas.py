"""The hand-off to the lab's cysteine atlas (atlas.py, docs/ATLAS.md, D120-D125, D127), contract
"ionomos-atlas-handoff/2".

The selection rule is harvest_fragpipe.ps1's (Nomura-Lab, Lab Informatics/tools), so its cases are table-driven
from the script's constants; the layout is its Get-RelativeDestination, checked against the atlas side's reader
(ingest_fragpipe.local_run_dir, copied below). Every test writes under tmp_path only: never the real Dropbox,
nomura-data, C:/Fragpipe_Auto or C:/Fragpipe_General.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ionomos import atlas, attention, names
from ionomos.config import ConfigError, load
from ionomos.ledger import Job, Ledger

# ------------------------------------------------------------------------------------------- contract --

CONTRACT_COLUMNS = ["Run", "Owner", "RunName", "LastModified", "FragPipeVersion", "ProbeClass", "Database", "Enzyme",
                    "VarMods", "HasWorkflow", "HasManifest", "PsmFiles", "RawFiles", "RawMissing", "Experiments",
                    "RawPaths", "FilesSelected", "MBSelected", "FilesTooLarge", "Destination",
                    "IonomosJob", "IonomosUser", "IonomosExperiment", "IonomosMethod", "IonomosVersion",
                    "HandoffSha256"]


def test_the_contract_names_and_columns():
    assert atlas.CONTRACT == "ionomos-atlas-handoff/2"
    assert atlas.LOOKUP_CONTRACT == "ionomos-atlas-lookup/1"
    assert list(atlas.CSV_COLUMNS) == CONTRACT_COLUMNS
    assert names.ATLAS_HANDOFF_FILE == "ionomos-handoff.json"
    assert names.ATLAS_LOG_DIR == "_harvest-logs"
    assert names.ATLAS_RUNS_CSV.format(stamp="20261019-140211", job=12) == "runs-ionomos-20261019-140211-job12.csv"
    assert (names.ATLAS_RETURN_DIR, names.ATLAS_LOOKUP_TSV, names.ATLAS_LOOKUP_JSON) == \
        ("_atlas", "atlas_sites_lookup.tsv", "atlas_sites_lookup.json")
    assert atlas.MAX_FILE_MB == 500
    assert atlas.MAX_PIN_MB == 100 and names.ATLAS_GZIP_SUFFIX == ".gz"
    assert atlas.KINDS == ("isoDTB", "TMT")
    assert atlas.DESIGN_FILES == ("experiment.yaml", "results/fragpipe-analyst/experiment_annotation.tsv")


# ------------------------------------------------------------------------------------------- settings --


@pytest.mark.parametrize(("raw", "want"), [
    (None, atlas.DEFAULTS),
    ({}, atlas.DEFAULTS),
    ({"enabled": True, "handoff_dir": r"C:\Users\me\TNRG Dropbox\Daniel Nomura\Nicholas Foster Data\fragpipe-runs"},
     {**atlas.DEFAULTS, "enabled": True,
      "handoff_dir": r"C:\Users\me\TNRG Dropbox\Daniel Nomura\Nicholas Foster Data\fragpipe-runs"}),
    ({"handoff_dir": r"\\server\share\fragpipe-runs"}, {**atlas.DEFAULTS, "handoff_dir": r"\\server\share\fragpipe-runs"}),
    ({"methods": "isoDTB"}, atlas.DEFAULTS),
    ({"max_tries": 3, "retry_minutes": 2.5}, {**atlas.DEFAULTS, "max_tries": 3, "retry_minutes": 2.5}),
    ({"pin_max_mb": 0}, {**atlas.DEFAULTS, "pin_max_mb": 0}),
    ({"methods": ["isoDTB", "TMT"], "pin_max_mb": 250}, {**atlas.DEFAULTS, "methods": ["isoDTB", "TMT"],
                                                        "pin_max_mb": 250}),
])
def test_settings_read(raw, want):
    assert atlas.settings_from(raw) == want


@pytest.mark.parametrize(("raw", "words"), [
    ([], "must be a mapping"),
    ({"enable": True}, "enable: unknown setting"),
    ({"enabled": "yes"}, "true or false"),
    ({"handoff_dir": 3}, "folder path"),
    ({"handoff_dir": "fragpipe-runs"}, "full folder path"),
    ({"methods": [""]}, "list of method names"),
    ({"max_tries": 0}, "max_tries must be"),
    ({"max_tries": 2.5}, "max_tries must be"),
    ({"retry_minutes": True}, "retry_minutes must be"),
    ({"pin_max_mb": -1}, "pin_max_mb must be a whole number from 0 to 4096"),
    ({"pin_max_mb": 2.5}, "pin_max_mb must be"),
])
def test_settings_refuse(raw, words):
    with pytest.raises(atlas.AtlasError, match=re.escape(words)):
        atlas.settings_from(raw)


def _write_cfg(lab, **atlas_block):
    d = dict(lab["cfg_dict"])
    d["atlas"] = atlas_block
    lab["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    return load(lab["cfg_path"])


def test_config_has_the_section_off_by_default(lab):
    assert lab["cfg"].atlas == atlas.DEFAULTS
    assert atlas.handoff_dir(lab["cfg"]) is None and atlas.lookup_dir(lab["cfg"]) is None


@pytest.mark.parametrize(("methods", "words"), [
    (["DIA"], "DIA is DIA; only FragPipe isoDTB and TMT methods"),
    (["Nope"], "there is no methods.Nope"),
])
def test_config_lets_only_fragpipe_isodtb_and_tmt_methods_in(lab, methods, words):
    with pytest.raises(ConfigError, match=re.escape(words)) as exc:
        _write_cfg(lab, enabled=True, handoff_dir=str(lab["root"] / "dest"), methods=methods)
    assert "line" in str(exc.value)  # the file and the line of the key (D102)


def test_config_refuses_an_isodtb_method_another_engine_searches(lab):
    d = dict(lab["cfg_dict"])
    d["methods"] = {**d["methods"], "isoSage": {"engine": "sage", "fasta": "h.fas", "data_type": "DDA"}}
    d["naming"] = {"methods": {"isoSage": {"like": "isoDTB"}}}
    d["atlas"] = {"enabled": True, "handoff_dir": str(lab["root"] / "dest"), "methods": ["isoSage"]}
    lab["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    with pytest.raises(ConfigError, match="searched by sage"):
        load(lab["cfg_path"])


def test_a_lab_without_an_isodtb_method_loads_while_the_hand_off_is_off(lab):
    """Every Save writes the default methods: [isoDTB]; a lab with no isoDTB method (or a MaxQuant one) must load."""
    d = dict(lab["cfg_dict"])
    d["methods"] = {"DIA": d["methods"]["DIA"]}
    d["atlas"] = {"enabled": False, "methods": ["isoDTB"]}
    lab["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    assert load(lab["cfg_path"]).atlas["methods"] == ["isoDTB"]
    d["atlas"]["enabled"] = True  # turned on: now it is named
    lab["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    with pytest.raises(ConfigError, match="there is no methods.isoDTB"):
        load(lab["cfg_path"])


def test_tmt_may_go_while_the_hand_off_is_on(lab):
    cfg = _write_cfg(lab, enabled=True, handoff_dir=str(lab["root"] / "dest"), methods=["isoDTB", "TMT"])
    assert cfg.atlas["methods"] == ["isoDTB", "TMT"] and atlas.kind_of(cfg, "TMT") == "TMT"


def test_a_method_like_isodtb_may_go(lab):
    d = dict(lab["cfg_dict"])
    d["methods"] = {**d["methods"], "isoDTB2": {"workflow": "isoDTB.workflow", "fasta": "h.fas", "data_type": "DDA"}}
    d["naming"] = {"methods": {"isoDTB2": {"like": "isoDTB"}}}
    d["atlas"] = {"methods": ["isoDTB", "isoDTB2"]}
    lab["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    assert load(lab["cfg_path"]).atlas["methods"] == ["isoDTB", "isoDTB2"]


def test_the_dropbox_folder_with_spaces_survives_a_save(tmp_path):
    from ionomos import configio

    d = configio.defaults("C:/Fragpipe_Auto", "C:/Fragpipe_General")
    d["atlas"].update(enabled=True, handoff_dir=r"C:\Users\me\TNRG Dropbox\Daniel Nomura\Nicholas Foster Data\fragpipe-runs")
    p = tmp_path / "config.yaml"
    configio.write_config(p, d, backup=False)
    assert configio.read_config(p)["atlas"] == d["atlas"]
    assert " " not in configio.defaults()["atlas"]["handoff_dir"]  # no default path with a space (CLAUDE.md)


# ------------------------------------------------------------------------------------ the selection rule --

MB = 1024 * 1024

# (path in the run folder, taken?, reason): harvest_fragpipe.ps1's $RootAllow, $ReplicateAllow, $NeverCopy,
# $SkipDirNames and the size cap; PowerShell's -like and -contains ignore case
SELECTION = [
    ("fragpipe.workflow", True, "selected"),
    ("fragpipe-files.fp-manifest", True, "selected"),
    ("fragpipe.job", True, "selected"),
    ("combined_protein.tsv", True, "selected"),
    ("combined_modified_peptide_label_quant.tsv", True, "selected"),
    ("experiment_annotation.tsv", True, "selected"),
    ("log_2026-10-19_14-02-11.txt", True, "selected"),
    ("filelist_ionquant.txt", True, "selected"),
    ("notes.csv", True, "selected"),
    ("philosopher.log", True, "selected"),
    ("human_reviewed_decoys.fas", True, "selected"),
    ("2026-10-19-decoys-reviewed.fasta", True, "selected"),
    ("fragger.params", True, "selected"),
    ("tmt-integrator-conf.yml", True, "selected"),
    ("x.yaml", True, "selected"),
    ("run_fingerprint.json", True, "selected"),
    ("dataset.manifest", True, "selected"),
    ("sdrf.XML", True, "selected"),
    (".hidden.tsv", True, "selected"),               # Get-ChildItem -Force lists hidden files
    ("COMBINED_ION.TSV", True, "selected"),
    ("interact.prot.xml", False, "never-copy pattern"),  # *.xml, but *.prot.xml is never copied
    ("x.pep.xml", False, "never-copy pattern"),
    ("a.pepXML", False, "not needed"),
    ("a.pin", True, "selected (gzip)"),              # the decoys (D127): its own rule, before $NeverCopy
    ("b.pin.gz", False, "not needed"),
    ("a.raw", False, "not needed"),
    ("a.mzML", False, "not needed"),
    ("lib.speclib", False, "not needed"),
    ("results.zip", False, "not needed"),
    ("psm.tsv.partial", False, "not needed"),
    ("figure.png", False, "not needed"),
    ("exp_1/psm.tsv", True, "selected"),
    ("exp_1/peptide.tsv", True, "selected"),
    ("exp_1/protein.tsv", True, "selected"),
    ("exp_3/Psm.TSV", True, "selected"),
    ("exp_2/msfragger.log", True, "selected"),
    ("exp_2/log_1.txt", True, "selected"),
    ("exp_2/ion.tsv", False, "not needed"),           # only with harvest's -IncludeIonTsv
    ("exp_2/other.txt", False, "not needed"),
    ("exp_2/interact-a.pep.xml", False, "not needed"),
    ("exp_2/a_calibrated.mgf", False, "not needed"),
    ("exp_2/S_2.PIN", True, "selected (gzip)"),
    ("exp_2/S_3.pin", False, "its _edited.pin is taken"),  # MSBooster's twin holds all of it
    ("exp_2/S_3_edited.pin", True, "selected (gzip)"),
    ("exp_2/S_4_edited.pin", True, "selected (gzip)"),
]
NOT_LISTED = ["exp_1/deeper/psm.tsv", "MSBooster/psm.tsv", "msbooster/x.log", "tmp/psm.tsv", "TEMP/x.log",
              "__pycache__/x.log", ".git/x.log"]


def _run_folder(base: Path, files: dict[str, bytes | int]) -> Path:
    for rel, data in files.items():
        p = base.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else b"x" * data)
    return base


def test_the_selection_is_harvests(tmp_path):
    run = _run_folder(tmp_path / "fragpipe", {rel: rel.encode() for rel, _t, _r in SELECTION + [
        (n, None, None) for n in NOT_LISTED]})
    got = {p.rel: (p.take, p.reason) for p in atlas.select(run)}
    want = {rel: (take, reason) for rel, take, reason in SELECTION}
    assert {k: v for k, v in got.items() if k in want} == want
    assert not set(got) & set(NOT_LISTED), "a skipped or second-level folder was listed"
    assert all(p.level == ("replicate" if "/" in p.rel else "root") for p in atlas.select(run))


def test_the_size_cap_is_harvests(tmp_path, monkeypatch):
    run = _run_folder(tmp_path / "fragpipe", {"big.tsv": MB + 1, "edge.tsv": MB, "exp_1/psm.tsv": MB + 1})
    got = {p.rel: p.reason for p in atlas.select(run, max_mb=1)}
    assert got == {"big.tsv": "larger than 1 MB", "edge.tsv": "selected", "exp_1/psm.tsv": "larger than 1 MB"}
    monkeypatch.setattr(atlas, "MAX_FILE_MB", 1)  # the default is read at call time
    assert {p.rel for p in atlas.select(run) if p.take} == {"edge.tsv"}


def test_the_pin_cap(tmp_path):
    run = _run_folder(tmp_path / "fragpipe", {"exp_1/a.pin": MB + 1, "exp_1/b.pin": MB, "big.tsv": MB + 1})
    got = {p.rel: (p.take, p.reason, p.gzip, p.dest_rel) for p in atlas.select(run, max_mb=1, pin_max_mb=1)}
    assert got == {"exp_1/a.pin": (False, "pin larger than 1 MB", False, "exp_1/a.pin"),
                   "exp_1/b.pin": (True, "selected (gzip)", True, "exp_1/b.pin.gz"),
                   "big.tsv": (False, "larger than 1 MB", False, "big.tsv")}
    assert {p.reason for p in atlas.select(run, pin_max_mb=0) if p.rel.endswith(".pin")} == {"pins off"}
    assert {p.rel for p in atlas.select(run, max_mb=0) if p.take} == {"exp_1/a.pin", "exp_1/b.pin"}  # their own cap


def test_a_linked_folder_is_not_followed(tmp_path):
    outside = _run_folder(tmp_path / "elsewhere", {"psm.tsv": b"not this run"})
    run = _run_folder(tmp_path / "fragpipe", {"fragpipe.workflow": b"x"})
    try:
        os.symlink(outside, run / "exp_1", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account can't make a folder link")
    assert [p.rel for p in atlas.select(run)] == ["fragpipe.workflow"]


def test_an_unreadable_run_folder_raises(tmp_path):
    with pytest.raises(OSError):
        atlas.select(tmp_path / "not-there")


# ----------------------------------------------------------------------------------------------- layout --


def local_run_dir(run_path, harvest_root):
    """ingest_fragpipe.local_run_dir (Nomura-Lab, the atlas side), as it is: how the atlas finds a run's copy."""
    m = re.match(r"^([A-Za-z]):\\(.*)$", run_path)
    return os.path.join(harvest_root, m.group(1).upper(), m.group(2)) if m else run_path


@pytest.mark.parametrize(("source", "parts"), [
    (r"C:\Fragpipe_General\EJQ\run", ["C", "Fragpipe_General", "EJQ", "run"]),
    (r"d:\EJQ\x", ["D", "EJQ", "x"]),
    ("C:/Fragpipe_General/EJQ/run", ["C", "Fragpipe_General", "EJQ", "run"]),
    (r"C:\Fragpipe_General\EJQ\run\exp_1\psm.tsv", ["C", "Fragpipe_General", "EJQ", "run", "exp_1", "psm.tsv"]),
    (r"\\server\share\x\y", ["server_share", "x", "y"]),
    (r"\\?\C:\a\b", ["C", "a", "b"]),
    (r"\\?\UNC\server\share\x", ["server_share", "x"]),
    ("/tmp/a/b", ["_", "tmp", "a", "b"]),
])
def test_the_layout_is_get_relative_destination(source, parts):
    assert atlas.layout_parts(source) == parts


@pytest.mark.parametrize("run", [r"C:\Fragpipe_General\Nicholas\2026-10-19_isoDTB_x\fragpipe", r"D:\EJQ\run 2\fragpipe"])
def test_the_atlas_finds_the_copy_where_it_lands(run):
    root = r"C:\Users\me\TNRG Dropbox\Daniel Nomura\Nicholas Foster Data\fragpipe-runs"
    ours = "\\".join([root, *atlas.layout_parts(run)])
    assert local_run_dir(run, root).replace("/", "\\") == ours


# ------------------------------------------------------------------------------------ harvest's facts --


def test_workflow_info_reads_like_harvest(tmp_path):
    wf = tmp_path / "fragpipe.workflow"
    wf.write_text("# FragPipe (24.0) runtime properties\n"
                  "Database.DB-Path=C\\:\\\\Fragpipe_Auto\\\\fasta\\\\h.fas\n"
                  "msfragger.search_enzyme_name_1=stricttrypsin\n"
                  "msfragger.table.var-mods=15.9949,M,true,3; 561.33,C,true,1\n"
                  "msfragger.table.fix-mods=0.0,C-Term Peptide,true,-1\n"
                  "msfragger.search_enzyme_name_1=trypsin\n", encoding="utf-8")
    info = atlas.read_workflow_info(wf)
    assert info["Version"] == "24.0"
    assert info["Database"] == r"C:\Fragpipe_Auto\fasta\h.fas"
    assert info["Enzyme"] == "trypsin"  # the last line wins, as in harvest's loop
    assert info["VarMods"] == "15.9949,M,true,3; 561.33,C,true,1"
    assert info["ProbeClass"] == "isoDTB"


@pytest.mark.parametrize(("mods", "cls"), [
    ("567.34,C,true,1", "isoDTB"), ("464.28,C,true,1", "isoTOP"), ("229.1629,K,true,1", "TMT"),
    ("561.33,C,true,1; 229.16,K,true,1", "isoDTB+TMT"), ("15.9949,M,true,3", "other"),
])
def test_probe_class_from_masses(tmp_path, mods, cls):
    wf = tmp_path / "w.workflow"
    wf.write_text(f"msfragger.table.var-mods={mods}\n", encoding="utf-8")
    assert atlas.read_workflow_info(wf)["ProbeClass"] == cls


def test_manifest_info_reads_like_harvest(tmp_path):
    there = tmp_path / "a.raw"
    there.write_bytes(b"r")
    mf = tmp_path / "fragpipe-files.fp-manifest"
    mf.write_text(f"{there}\tEXP_A\t1\tDDA\n\n{tmp_path / 'gone.raw'}\tEXP_A\t2\tDDA\n"
                  f"{tmp_path / 'b.raw'}\tEXP_B\t\tDDA\n", encoding="utf-8")
    info = atlas.read_manifest_info(mf)
    assert info["RawCount"] == 3 and info["RawMissing"] == 2
    assert info["Experiments"] == "EXP_A | EXP_B"
    assert info["RawPaths"] == f"{there} | {tmp_path / 'gone.raw'} | {tmp_path / 'b.raw'}"
    assert info["rows"][2] == {"raw": str(tmp_path / "b.raw"), "experiment": "EXP_B", "bioreplicate": "",
                               "data_type": "DDA"}


@pytest.mark.parametrize(("mb", "text"), [(12.34, "12.3"), (12.0, "12"), (0, "0"), (0.04, "0"), (1234.56, "1234.6")])
def test_mb_as_powershell_writes_it(mb, text):
    assert atlas._ps_number(mb) == text


def test_the_runs_csv_is_export_csvs():
    blob = atlas.runs_csv_bytes({"Run": 'C:\\a "b"', "HasWorkflow": "True", "MBSelected": "1.5"})
    assert blob.startswith(b"\xef\xbb\xbf")
    text = blob[3:].decode("utf-8")
    assert text.count("\r\n") == 2 and text.endswith("\r\n")
    head, row = text.split("\r\n")[:2]
    assert head == ",".join(f'"{c}"' for c in CONTRACT_COLUMNS)
    assert row.startswith('"C:\\a ""b""",')
    assert next(csv.DictReader(io.StringIO(text)))["Run"] == 'C:\\a "b"'


# ---------------------------------------------------------------------------------------- one hand-off --

WORKFLOW = ("# FragPipe (24.0) runtime properties\ndatabase.db-path=C\\:\\\\Fragpipe_Auto\\\\fasta\\\\h.fas\n"
            "msfragger.search_enzyme_name_1=stricttrypsin\nmsfragger.table.var-mods=561.3387,C,true,1\n")


@pytest.fixture
def handoff(lab):
    """A done isoDTB job with a FragPipe output folder, and the hand-off on, into a folder whose path has spaces."""
    dropbox = lab["root"] / "TNRG Dropbox" / "Nicholas Foster Data"
    dropbox.mkdir(parents=True)
    dest_root = dropbox / "fragpipe-runs"
    cfg = _write_cfg(lab, enabled=True, handoff_dir=str(dest_root), max_tries=3, retry_minutes=1)
    exp = lab["general"] / "EJQ" / "20261019_isoDTB_EJQ-2-027"
    raws = [exp / "raw" / f"S_{r}.raw" for r in (1, 2)]
    for r in raws:
        r.parent.mkdir(parents=True, exist_ok=True)
        r.write_bytes(b"raw")
    run = _run_folder(exp / "fragpipe", {
        "fragpipe.workflow": WORKFLOW.encode(), "fragpipe.job": b"job",
        "fragpipe-files.fp-manifest": "".join(f"{r}\tEXP\t{i}\tDDA\n" for i, r in enumerate(raws, 1)).encode(),
        "combined_modified_peptide_label_quant.tsv": b"Protein\tx\nsp|P1|A_HUMAN\t1\n",
        "log_2026-10-19_14-02-11.txt": b"done", "exp_1/psm.tsv": b"Peptide\n" * 50, "exp_2/psm.tsv": b"Peptide\n" * 40,
        "exp_1/interact-a.pep.xml": b"<x/>", "MSBooster/psm.tsv": b"no", "a.raw": b"raw"})
    led = Ledger(cfg.database)
    jid = led.insert(Job(inbox_name=exp.name, user="EJQ", method="isoDTB", dest_dir=str(exp)))
    led.set_status(jid, "done")
    (exp / names.STATUS_FILE).write_text(json.dumps({
        "status": "done", "ionomos_version": "0.18.1",
        "run": {"workdir": str(run), "workflow": str(exp / "ionomos_run" / "isoDTB.workflow"),
                "workflow_source": "C:/Fragpipe_Auto/workflows/isoDTB.workflow", "fasta": "C:/x/h.fas",
                "finished_at": "2026-10-19T14:02:11-07:00", "exit_code": 0}}), encoding="utf-8")
    return {"cfg": cfg, "ledger": led, "job": led.get(jid), "exp": exp, "run": run, "root": dest_root, "lab": lab}


def _snapshot(folder: Path) -> dict:
    """Every file of the experiment folder but its status file: path, size, modified time, content hash."""
    out = {}
    for p in folder.rglob("*"):
        if p.is_file() and p.name != names.STATUS_FILE:
            out[p.relative_to(folder).as_posix()] = (p.stat().st_size, p.stat().st_mtime_ns,
                                                     hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def _long(p) -> str:
    """A path the file functions open past Windows' 260 characters, as atlas._fs does: the destination repeats the
    test's long tmp folder (C/Users/.../pytest-N/.../fragpipe), so its paths pass the limit here, not on the PC."""
    s = os.path.abspath(str(p))
    return "\\\\?\\" + s if os.name == "nt" and not s.startswith("\\\\") else s


def _files(root: Path) -> dict[str, str]:
    """Every file under root: relative path with '/' -> a path that opens."""
    out = {}
    base = _long(root)
    for d, _dirs, files in os.walk(base):
        for f in files:
            out[os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/")] = os.path.join(d, f)
    return out


def _read(p) -> bytes:
    with open(_long(p), "rb") as fh:
        return fh.read()


def _write(p, data: bytes) -> None:
    os.makedirs(_long(Path(p).parent), exist_ok=True)
    with open(_long(p), "wb") as fh:
        fh.write(data)


def _records(root: Path) -> list[Path]:
    return sorted((root / names.ATLAS_LOG_DIR).glob("runs-ionomos-*.csv"))


def test_a_hand_off_copies_verifies_and_records(handoff):
    before = _snapshot(handoff["exp"])
    res = atlas.send(handoff["cfg"], handoff["job"])
    assert res.ok, res.message
    assert res.state == atlas.SENT and res.copied == 7 and res.already == 0

    run_dest = atlas.destination(handoff["run"], handoff["root"])
    assert run_dest == handoff["root"].joinpath(*atlas.layout_parts(atlas.windows_path(handoff["run"])))
    copied = sorted(_files(run_dest))
    assert copied == sorted(["fragpipe.workflow", "fragpipe.job", "fragpipe-files.fp-manifest",
                             "combined_modified_peptide_label_quant.tsv", "log_2026-10-19_14-02-11.txt",
                             "exp_1/psm.tsv", "exp_2/psm.tsv", names.ATLAS_HANDOFF_FILE])
    for rel in copied:
        if rel != names.ATLAS_HANDOFF_FILE:
            assert _read(run_dest / rel) == (handoff["run"] / rel).read_bytes()
    assert not [r for r in _files(handoff["root"]) if r.endswith(".partial")]

    # the completion record
    data = _read(run_dest / names.ATLAS_HANDOFF_FILE)
    rec = json.loads(data)
    assert rec["contract"] == atlas.CONTRACT and rec["run"] == atlas.windows_path(handoff["run"])
    files = {f["path"]: f for f in rec["files"]}
    assert set(files) == set(copied) - {names.ATLAS_HANDOFF_FILE}
    assert files["exp_1/psm.tsv"] == {"path": "exp_1/psm.tsv", "size": 400,
                                      "sha256": hashlib.sha256(b"Peptide\n" * 50).hexdigest()}
    job = rec["job"]
    assert (job["number"], job["user"], job["experiment"], job["method"]) == (handoff["job"].id, "EJQ",
                                                                           handoff["exp"].name, "isoDTB")
    assert job["fragpipe_version"] == "24.0" and job["fasta"] == "C:/x/h.fas"
    assert job["workflow"] == "C:/Fragpipe_Auto/workflows/isoDTB.workflow"
    assert len(job["raw_files"]) == 2 and job["replicate_plan"][1]["bioreplicate"] == "2"
    assert job["ionomos_version"] and job["search_finished_at"] == "2026-10-19T14:02:11-07:00"

    # the runs CSV: one row, harvest's columns then Ionomos'
    (csv_path,) = _records(handoff["root"])
    assert re.fullmatch(r"runs-ionomos-\d{8}-\d{6}-job1\.csv", csv_path.name) and str(csv_path) == res.record
    blob = csv_path.read_bytes()
    assert blob.startswith(b"\xef\xbb\xbf")
    (row,) = list(csv.DictReader(io.StringIO(blob[3:].decode("utf-8"))))
    assert list(row) == CONTRACT_COLUMNS
    assert row["Run"] == atlas.windows_path(handoff["run"]) and row["Owner"] == "EJQ" and row["RunName"] == "fragpipe"
    assert row["ProbeClass"] == "isoDTB" and row["FragPipeVersion"] == "24.0"
    assert row["Database"] == r"C:\Fragpipe_Auto\fasta\h.fas" and row["Enzyme"] == "stricttrypsin"
    assert (row["HasWorkflow"], row["HasManifest"], row["PsmFiles"]) == ("True", "True", "2")
    assert (row["RawFiles"], row["RawMissing"], row["Experiments"]) == ("2", "0", "EXP")
    assert (row["FilesSelected"], row["MBSelected"], row["FilesTooLarge"]) == ("7", "0", "0")
    assert row["Destination"] == str(run_dest)
    assert row["HandoffSha256"] == hashlib.sha256(data).hexdigest() == res.handoff_sha256
    assert (row["IonomosJob"], row["IonomosUser"], row["IonomosExperiment"], row["IonomosMethod"]) == \
        ("1", "EJQ", handoff["exp"].name, "isoDTB")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", row["LastModified"])

    # the experiment folder: read only; ionomos.json says how it went
    assert _snapshot(handoff["exp"]) == before
    st = atlas.state(handoff["exp"])
    assert st["state"] == "sent" and st["record"] == res.record and st["handoff_sha256"] == res.handoff_sha256
    assert json.loads((handoff["exp"] / names.STATUS_FILE).read_text(encoding="utf-8"))["status"] == "done"
    assert not list(handoff["cfg"].log_dir.glob("atlas-send-*.lock"))


def test_sending_again_copies_nothing_and_writes_a_new_record_with_the_same_hash(handoff):
    first = atlas.send(handoff["cfg"], handoff["job"])
    run_dest = Path(first.dest_dir)
    stamp = {r: os.stat(f).st_mtime_ns for r, f in _files(run_dest).items()}
    again = atlas.send(handoff["cfg"], handoff["job"], trigger="app")
    assert again.ok and again.copied == 0 and again.already == 7
    assert again.handoff_sha256 == first.handoff_sha256  # nothing in the record changes: no double count
    assert {r: os.stat(f).st_mtime_ns for r, f in _files(run_dest).items()} == stamp  # not even the record
    assert len(_records(handoff["root"])) == 2 and again.record != first.record
    hist = atlas.state(handoff["exp"])["history"]
    assert hist == [{"sent_at": hist[0]["sent_at"], "record": first.record, "handoff_sha256": first.handoff_sha256}]


def test_a_different_file_at_the_destination_is_a_conflict_and_nothing_is_copied(handoff):
    run_dest = atlas.destination(handoff["run"], handoff["root"])
    theirs = run_dest / "exp_1" / "psm.tsv"
    _write(theirs, b"Peptide\n" * 49 + b"Peptidx\n")  # same size, other content
    res = atlas.send(handoff["cfg"], handoff["job"])
    assert not res.ok and res.state == atlas.CONFLICT and res.copied == 0
    assert "exp_1" in res.conflicts[0] and "other content" in res.conflicts[0]
    assert _read(theirs) == b"Peptide\n" * 49 + b"Peptidx\n"
    assert list(_files(run_dest)) == ["exp_1/psm.tsv"]  # nothing else, no record
    assert not _records(handoff["root"])
    st = atlas.state(handoff["exp"])
    assert st["state"] == "conflict" and not atlas.is_due(st)  # a person decides
    (item,) = [i for i in attention.items(handoff["cfg"].log_dir) if i.kind == atlas.ATTENTION_KIND]
    assert item.job_id == 1 and "Send to atlas" in " ".join(item.fixes) and str(theirs) in item.details


def test_a_file_of_another_size_is_a_conflict_without_reading_it(handoff, monkeypatch):
    run_dest = atlas.destination(handoff["run"], handoff["root"])
    _write(run_dest / "exp_2" / "psm.tsv", b"short")
    res = atlas.send(handoff["cfg"], handoff["job"])
    assert res.state == atlas.CONFLICT and "5 bytes" in res.conflicts[0]


def test_an_unreachable_destination_waits_backs_off_and_gives_up(handoff, monkeypatch):
    cfg = _write_cfg(handoff["lab"], enabled=True, handoff_dir=str(handoff["lab"]["root"] / "Offline Dropbox" / "x"),
                     max_tries=3, retry_minutes=1)
    for n in (1, 2):
        res = atlas.send(cfg, handoff["job"])
        assert res.state == atlas.FAILED and "Dropbox" in res.message
        st = atlas.state(handoff["exp"])
        assert st["tries"] == n
        nxt = datetime.fromisoformat(st["next_try"])
        assert timedelta(minutes=2 ** (n - 1)) - timedelta(seconds=5) < nxt - datetime.now().astimezone() \
               <= timedelta(minutes=2 ** (n - 1))
        assert not atlas.is_due(st) and atlas.is_due(st, now=nxt)
    res = atlas.send(cfg, handoff["job"])
    assert res.state == atlas.GAVE_UP and atlas.state(handoff["exp"])["state"] == "gave_up"
    assert not atlas.is_due(atlas.state(handoff["exp"]), now=datetime.now().astimezone() + timedelta(days=9))
    (item,) = [i for i in attention.items(cfg.log_dir) if i.kind == atlas.ATTENTION_KIND]
    assert "3 times" in item.causes[0]
    # fixed, and sent by hand: the item closes
    res = atlas.send(handoff["cfg"], handoff["job"], trigger="cli")
    assert res.ok and atlas.state(handoff["exp"])["tries"] == 0
    assert not [i for i in attention.items(cfg.log_dir) if i.kind == atlas.ATTENTION_KIND]


def test_the_destination_is_made_only_when_its_parent_is_there(handoff):
    assert not handoff["root"].exists()  # Nicholas Foster Data is there, fragpipe-runs not yet
    assert atlas.send(handoff["cfg"], handoff["job"]).ok
    assert handoff["root"].is_dir()


def test_a_copy_that_does_not_verify_is_never_renamed(handoff, monkeypatch):
    real = atlas._sha256

    def bad(path):
        return "0" * 64 if str(path).endswith(".partial") else real(path)

    monkeypatch.setattr(atlas, "_sha256", bad)
    res = atlas.send(handoff["cfg"], handoff["job"])
    assert res.state == atlas.FAILED and "does not match" in res.message
    run_dest = atlas.destination(handoff["run"], handoff["root"])
    assert [os.path.splitext(r)[1] for r in _files(run_dest)] == [".partial"]
    assert not _records(handoff["root"])
    monkeypatch.setattr(atlas, "_sha256", real)
    assert atlas.send(handoff["cfg"], handoff["job"]).ok  # the leftover .partial is our own: written over


def test_a_file_too_large_is_left_and_counted(handoff, monkeypatch):
    monkeypatch.setattr(atlas, "MAX_FILE_MB", 0)
    (handoff["run"] / "empty.tsv").write_bytes(b"")
    res = atlas.send(handoff["cfg"], handoff["job"])
    assert res.ok and res.files == 1 and len(res.too_large) == 7
    (row,) = list(csv.DictReader(io.StringIO(_records(handoff["root"])[0].read_bytes()[3:].decode("utf-8"))))
    assert (row["FilesSelected"], row["FilesTooLarge"], row["PsmFiles"]) == ("1", "7", "0")


def test_the_record_name_never_replaces_one(handoff, monkeypatch):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 19, 14, 2, 11, tzinfo=tz)

    monkeypatch.setattr(atlas, "datetime", Fixed)
    a = atlas.send(handoff["cfg"], handoff["job"])
    b = atlas.send(handoff["cfg"], handoff["job"])
    assert Path(a.record).name == "runs-ionomos-20261019-140211-job1.csv"
    assert Path(b.record).name == "runs-ionomos-20261019-140212-job1.csv"


@pytest.mark.parametrize(("change", "words"), [
    ({"status": "failed"}, "only a finished search"),
    ({"method": "TMT"}, "atlas.methods sends isoDTB"),
])
def test_only_a_done_job_of_an_atlas_method_goes(handoff, change, words):
    from dataclasses import replace

    res = atlas.send(handoff["cfg"], replace(handoff["job"], **change))
    assert not res.ok and words in res.message and not res.setup
    assert atlas.state(handoff["exp"]) == {} and not handoff["root"].exists()


@pytest.mark.parametrize(("block", "words"), [
    ({"enabled": False}, "is off"),
    ({"enabled": True}, "handoff_dir is not set"),
])
def test_nothing_is_tried_without_the_setup(handoff, block, words):
    cfg = _write_cfg(handoff["lab"], **block)
    res = atlas.send(cfg, handoff["job"])
    assert res.setup and words in res.message and atlas.state(handoff["exp"]) == {}


def test_a_missing_run_folder_fails_and_waits(handoff):
    st = json.loads((handoff["exp"] / names.STATUS_FILE).read_text(encoding="utf-8"))
    st["run"]["workdir"] = str(handoff["exp"] / "fragpipe_gone")
    (handoff["exp"] / names.STATUS_FILE).write_text(json.dumps(st), encoding="utf-8")
    res = atlas.send(handoff["cfg"], handoff["job"])
    assert res.state == atlas.FAILED and "is not there" in res.message


def test_another_process_sending_the_job_is_left_to_it(handoff):
    lock = handoff["cfg"].log_dir / names.ATLAS_SEND_LOCK.format(job=1)
    lock.write_text("1234 now", encoding="utf-8")
    res = atlas.send(handoff["cfg"], handoff["job"])
    assert not res.ok and "another Ionomos process" in res.message and not handoff["root"].exists()
    assert lock.is_file()
    old = lock.stat().st_mtime - atlas.LOCK_STALE_S - 60
    os.utime(lock, (old, old))  # its process died hours ago
    assert atlas.send(handoff["cfg"], handoff["job"]).ok and not lock.exists()


def test_rename_never_replaces(tmp_path):
    (tmp_path / "a.partial").write_bytes(b"new")
    (tmp_path / "a").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        atlas._rename_new(tmp_path / "a.partial", tmp_path / "a")
    assert (tmp_path / "a").read_bytes() == b"old"


# --------------------------------------------------------------- the decoys and the sample design (D127) --

PIN_HEAD = b"SpecId\tLabel\tScanNr\thyperscore\tPeptide\tProteins\n"


def _pin(n: int, extra: str = "") -> bytes:
    rows = [f"S_{i}.{i}.{i}.2\t{1 if i % 3 else -1}\t{i}\t{20 + i % 7}{extra}\tK.PEPC{i}K.R\tsp|P{i}|X_HUMAN\n"
            for i in range(n)]
    return PIN_HEAD + "".join(rows).encode()


@pytest.fixture
def design(handoff):
    """The handoff job, with its .pin files and its sample design: experiment.yaml and the analysis' annotation in the
    experiment folder, a TMT annotation beside the raw files."""
    run, exp = handoff["run"], handoff["exp"]
    pins = {"exp_1/S_1.pin": _pin(400), "exp_1/S_1_edited.pin": _pin(400, "\t0.5"), "exp_2/S_2.pin": _pin(300)}
    _run_folder(run, pins)
    _run_folder(exp, {"experiment.yaml": b"analysis:\n  control: DMSO\ntmt:\n  126: DMSO_1\n",
                      "raw/annotation.txt": b"126 DMSO_1\n127N KB02_1\n",
                      "raw/other.txt": b"not design",
                      "results/fragpipe-analyst/experiment_annotation.tsv": b"file\tsample\tcondition\n"})
    return {**handoff, "pins": pins}


def test_the_pins_and_the_sample_design_go_too(design):
    before = _snapshot(design["exp"])
    res = atlas.send(design["cfg"], design["job"])
    assert res.ok, res.message
    assert (res.copied, res.pins, res.design) == (12, 2, 3) and ".pin gzip-compressed" in res.message
    run_dest = Path(res.dest_dir)
    got = _files(run_dest)
    assert {r for r in got if ".pin" in r} == {"exp_1/S_1_edited.pin.gz", "exp_2/S_2.pin.gz"}  # S_1.pin: its twin
    for rel in ("exp_1/S_1_edited.pin", "exp_2/S_2.pin"):
        with gzip.open(_long(run_dest / (rel + ".gz"))) as fh:
            assert fh.read() == design["pins"][rel]
    rec = json.loads(_read(run_dest / names.ATLAS_HANDOFF_FILE))
    assert rec["contract"] == "ionomos-atlas-handoff/2" and rec["probe_class"] == "isoDTB"
    files = {f["path"]: f for f in rec["files"]}
    gz = files["exp_2/S_2.pin.gz"]
    assert gz["sha256"] == hashlib.sha256(_read(run_dest / "exp_2" / "S_2.pin.gz")).hexdigest()
    assert gz["gzip_of"] == {"path": "exp_2/S_2.pin", "size": len(design["pins"]["exp_2/S_2.pin"]),
                             "sha256": hashlib.sha256(design["pins"]["exp_2/S_2.pin"]).hexdigest()}
    assert gz["size"] < len(design["pins"]["exp_2/S_2.pin"]) / 2
    assert rec["left"] == [{"path": "exp_1/S_1.pin", "size": len(design["pins"]["exp_1/S_1.pin"]),
                            "reason": "its _edited.pin is taken"}]
    # the design: each at its own path under the destination (harvest's layout), relative to it in the record
    kinds = {d["kind"]: d for d in rec["design"]}
    assert set(kinds) == {"experiment.yaml", "experiment_annotation.tsv", "annotation.txt"}
    for d in rec["design"]:
        src = Path(d["source"])
        assert d["path"] == "/".join(atlas.layout_parts(atlas.windows_path(src)))
        assert _read(design["root"].joinpath(*d["path"].split("/"))) == src.read_bytes()
        assert d["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest() and d["size"] == src.stat().st_size
    assert kinds["annotation.txt"]["source"] == atlas.windows_path(design["exp"] / "raw" / "annotation.txt")
    (row,) = list(csv.DictReader(io.StringIO(_records(design["root"])[0].read_bytes()[3:].decode("utf-8"))))
    assert (row["FilesSelected"], row["PsmFiles"], row["ProbeClass"]) == ("12", "2", "isoDTB")
    assert row["HandoffSha256"] == res.handoff_sha256
    assert _snapshot(design["exp"]) == before  # read only
    # sent again: nothing copied, the same record
    again = atlas.send(design["cfg"], design["job"])
    assert again.ok and again.copied == 0 and again.already == 12 and again.handoff_sha256 == res.handoff_sha256


def test_a_pin_gz_another_writer_compressed_counts_as_there(design):
    """harvest_fragpipe.ps1 compresses with .NET's GZipStream: other bytes, the same content. Compared by content."""
    run_dest = atlas.destination(design["run"], design["root"])
    theirs = gzip.compress(design["pins"]["exp_2/S_2.pin"], compresslevel=1, mtime=1_700_000_000)
    _write(run_dest / "exp_2" / "S_2.pin.gz", theirs)
    res = atlas.send(design["cfg"], design["job"])
    assert res.ok and res.already == 1 and res.copied == 11
    assert _read(run_dest / "exp_2" / "S_2.pin.gz") == theirs  # left as it was
    rec = json.loads(_read(run_dest / names.ATLAS_HANDOFF_FILE))
    gz = {f["path"]: f for f in rec["files"]}["exp_2/S_2.pin.gz"]
    assert gz["sha256"] == hashlib.sha256(theirs).hexdigest() and gz["size"] == len(theirs)


@pytest.mark.parametrize(("there", "words"), [
    (gzip.compress(b"another run's pin"), "holds other content"),
    (b"not gzip at all", "not a whole gzip file"),
    (gzip.compress(b"x" * 5000)[:-9], "not a whole gzip file"),  # cut short
])
def test_a_pin_gz_with_other_content_is_a_conflict(design, there, words):
    run_dest = atlas.destination(design["run"], design["root"])
    _write(run_dest / "exp_2" / "S_2.pin.gz", there)
    res = atlas.send(design["cfg"], design["job"])
    assert res.state == atlas.CONFLICT and res.copied == 0 and words in res.conflicts[0]
    assert _read(run_dest / "exp_2" / "S_2.pin.gz") == there


def test_compression_is_the_same_every_time_and_verified(tmp_path, monkeypatch):
    src = tmp_path / "a.pin"
    src.write_bytes(_pin(2000))
    a = atlas._gzip_verified(src, tmp_path / "a1.pin.gz")
    b = atlas._gzip_verified(src, tmp_path / "a2.pin.gz")
    assert a == b and (tmp_path / "a1.pin.gz").read_bytes() == (tmp_path / "a2.pin.gz").read_bytes()
    assert a[2] == hashlib.sha256(src.read_bytes()).hexdigest() and a[1] == (tmp_path / "a1.pin.gz").stat().st_size
    assert os.stat(tmp_path / "a1.pin.gz").st_mtime_ns == src.stat().st_mtime_ns
    monkeypatch.setattr(atlas, "gunzip_sha256", lambda p: ("0" * 64, 0))
    with pytest.raises(OSError, match="compressed copy"):
        atlas._gzip_verified(src, tmp_path / "a3.pin.gz")
    assert not (tmp_path / "a3.pin.gz").exists() and (tmp_path / "a3.pin.gz.partial").exists()


def test_no_pins_when_the_cap_is_zero(design):
    cfg = _write_cfg(design["lab"], enabled=True, handoff_dir=str(design["root"]), pin_max_mb=0)
    res = atlas.send(cfg, design["job"])
    assert res.ok and res.pins == 0
    assert not [r for r in _files(Path(res.dest_dir)) if ".pin" in r]
    rec = json.loads(_read(Path(res.dest_dir) / names.ATLAS_HANDOFF_FILE))
    assert {(x["path"], x["reason"]) for x in rec["left"]} == {("exp_1/S_1.pin", "its _edited.pin is taken"),
                                                               ("exp_1/S_1_edited.pin", "pins off"),
                                                               ("exp_2/S_2.pin", "pins off")}


def test_a_changed_design_file_goes_beside_the_earlier_copy(design):
    first = atlas.send(design["cfg"], design["job"])
    yml = design["exp"] / "experiment.yaml"
    yml.write_bytes(b"analysis:\n  control: Vehicle\n")  # the analysis' choices edited, then sent again
    second = atlas.send(design["cfg"], design["job"])
    assert second.ok and second.copied == 1 and second.handoff_sha256 != first.handoff_sha256
    dest = atlas.destination(yml, design["root"])
    sha = hashlib.sha256(yml.read_bytes()).hexdigest()
    assert _read(dest) == b"analysis:\n  control: DMSO\ntmt:\n  126: DMSO_1\n"  # the earlier copy stays
    assert _read(dest.with_name(f"experiment.{sha[:12]}.yaml")) == yml.read_bytes()
    rec = json.loads(_read(Path(second.dest_dir) / names.ATLAS_HANDOFF_FILE))
    (d,) = [x for x in rec["design"] if x["kind"] == "experiment.yaml"]
    assert d["path"].endswith(f"/experiment.{sha[:12]}.yaml") and d["sha256"] == sha
    third = atlas.send(design["cfg"], design["job"])
    assert third.ok and third.copied == 0 and third.handoff_sha256 == second.handoff_sha256


def test_a_tmt_search_goes_as_tmt_with_each_plexs_annotation(design):
    from dataclasses import replace

    exp = design["exp"]
    plexes = [exp / "plex1" / "P1.raw", exp / "plex2" / "P2.raw"]
    for i, r in enumerate(plexes, 1):
        _run_folder(r.parent, {r.name: b"raw", f"plex{i}_annotation.txt": f"126 DMSO_{i}\n".encode()})
    (design["run"] / "fragpipe-files.fp-manifest").write_text(
        "".join(f"{r}\tplex{i}\t\tDDA\n" for i, r in enumerate(plexes, 1)), encoding="utf-8")
    (design["run"] / "fragpipe.workflow").write_text("msfragger.table.var-mods=229.1629,K,true,1\n", encoding="utf-8")
    cfg = _write_cfg(design["lab"], enabled=True, handoff_dir=str(design["root"]), methods=["isoDTB", "TMT"])
    res = atlas.send(cfg, replace(design["job"], method="TMT"))
    assert res.ok, res.message
    rec = json.loads(_read(Path(res.dest_dir) / names.ATLAS_HANDOFF_FILE))
    assert rec["probe_class"] == "TMT" and rec["job"]["method"] == "TMT"
    anns = sorted(Path(d["source"]).name for d in rec["design"] if d["kind"] == "annotation.txt")
    assert anns == ["plex1_annotation.txt", "plex2_annotation.txt"]  # raw/annotation.txt: no raw file of this run
    (row,) = list(csv.DictReader(io.StringIO(_records(design["root"])[0].read_bytes()[3:].decode("utf-8"))))
    assert (row["ProbeClass"], row["IonomosMethod"]) == ("TMT", "TMT")


def test_only_isodtb_and_tmt_go_even_by_hand(design):
    from dataclasses import replace

    res = atlas.send(design["cfg"], replace(design["job"], method="DIA"), force=True)
    assert not res.ok and "only isoDTB and TMT searches" in res.message and not design["root"].exists()


def test_the_experiment_folder_harvest_finds(tmp_path):
    run = _run_folder(tmp_path / "exp" / "fragpipe", {"fragpipe.workflow": b"x"})
    assert atlas.experiment_folder(run) is None  # a FragPipe run of someone's own: nothing of Ionomos above it
    for marker in ("experiment.yaml", "ionomos.json", "labwatch.json"):
        (tmp_path / "exp" / marker).write_text("{}\n", encoding="utf-8")
        assert atlas.experiment_folder(run) == tmp_path / "exp"
        (tmp_path / "exp" / marker).unlink()
    assert atlas.EXPERIMENT_MARKERS == ("experiment.yaml", names.STATUS_FILE, *names.LEGACY_STATUS_FILES)


def test_design_files_skip_what_is_not_there(tmp_path):
    rows = [{"raw": str(tmp_path / "gone" / "a.raw")}, {"raw": ""}]
    assert atlas.design_files(tmp_path / "no-exp", rows) == []
    _run_folder(tmp_path / "raw", {"a.raw": b"r", "b.raw": b"r", "Annotation.TXT": b"126 x\n", "big_annotation.txt": 3})
    rows = [{"raw": str(tmp_path / "raw" / "a.raw")}, {"raw": str(tmp_path / "raw" / "b.raw")}]
    got = [(p.rel, p.take, p.reason) for p in atlas.design_files(None, rows, max_mb=0)]
    assert got == [("Annotation.TXT", False, "larger than 0 MB"), ("big_annotation.txt", False, "larger than 0 MB")]
    assert [p.rel for p in atlas.design_files(None, rows) if p.take] == ["Annotation.TXT", "big_annotation.txt"]


@pytest.mark.parametrize(("extra", "want"), [
    ({}, ["TMT"]),
    ({"naming": {"methods": {"TMT16": {"like": "TMT"}, "TMTsage": {"like": "TMT"}}}}, ["TMT", "TMT16"]),
    ({"naming": {"methods": {"TMT16": "{sample}_{rep}"}}}, ["TMT"]),  # a template only: its own kind
])
def test_the_labs_tmt_methods(extra, want):
    from ionomos import configio

    d = configio.defaults()
    d["methods"]["TMT16"] = {"workflow": "TMT16.workflow", "fasta": "h.fas", "data_type": "DDA"}
    d["methods"]["TMTsage"] = {"engine": "sage", "fasta": "h.fas", "data_type": "DDA"}  # another engine: never
    d.update(extra)
    assert atlas.methods_of_kind(d, "TMT") == want
    assert atlas.methods_of_kind(d, "isoDTB") == ["isoDTB"]


def test_the_tmt_switch_puts_the_labs_tmt_methods_in_and_takes_them_out():
    from ionomos import configio

    d = configio.defaults()
    assert atlas.methods_listed(d) == ["isoDTB"] and not atlas.sends_kind(d, "TMT")
    assert atlas.methods_with(d, "TMT", True) == ["isoDTB", "TMT"]
    d["atlas"]["methods"] = ["TMT", "isoDTB2"]
    assert atlas.sends_kind(d, "TMT") and atlas.methods_with(d, "TMT", False) == ["isoDTB2"]
    del d["atlas"]["methods"]
    assert atlas.methods_listed(d) == ["isoDTB"]


# --------------------------------------------------------------------------------- pending and the loop --


def test_a_finished_job_is_marked_pending_once(handoff):
    assert atlas.mark_pending(handoff["cfg"], handoff["job"])
    st = atlas.state(handoff["exp"])
    assert st["state"] == "pending" and st["search_finished_at"] == "2026-10-19T14:02:11-07:00" and atlas.is_due(st)
    assert not atlas.mark_pending(handoff["cfg"], handoff["job"])  # the same search: already on the list


@pytest.mark.parametrize("why", ["off", "tmt", "failed"])
def test_what_is_not_marked(handoff, why):
    from dataclasses import replace

    cfg, job = handoff["cfg"], handoff["job"]
    if why == "off":
        cfg = _write_cfg(handoff["lab"], enabled=False, handoff_dir=str(handoff["root"]))
    job = replace(job, method="TMT") if why == "tmt" else replace(job, status="failed") if why == "failed" else job
    assert not atlas.mark_pending(cfg, job) and atlas.state(handoff["exp"]) == {}


@pytest.mark.parametrize(("tries", "minutes"), [(1, 15), (2, 30), (3, 60), (6, 480), (7, 720), (40, 720)])
def test_the_back_off_doubles_up_to_twelve_hours(tries, minutes):
    assert atlas.backoff(tries, 15) == timedelta(minutes=minutes)


def test_the_loop_sends_what_is_due_and_leaves_the_rest(handoff):
    loop = atlas.HandoffLoop(lambda: handoff["cfg"])
    assert loop.run_once() == []  # done before the hand-off was asked for: only by hand (Send to atlas)
    atlas.mark_pending(handoff["cfg"], handoff["job"])
    (res,) = loop.run_once()
    assert res.ok and atlas.state(handoff["exp"])["state"] == "sent"
    assert loop.run_once() == []


def test_the_loop_retries_a_failed_hand_off_when_it_is_due(handoff):
    offline = _write_cfg(handoff["lab"], enabled=True, handoff_dir=str(handoff["lab"]["root"] / "Off" / "x"),
                         max_tries=3, retry_minutes=1)
    atlas.mark_pending(offline, handoff["job"])
    cfg = [offline]
    loop = atlas.HandoffLoop(lambda: cfg[0])
    (res,) = loop.run_once()
    assert res.state == atlas.FAILED
    assert loop.run_once() == []  # not due yet
    cfg[0] = _write_cfg(handoff["lab"], enabled=True, handoff_dir=str(handoff["root"]), max_tries=3, retry_minutes=1)
    later = datetime.now().astimezone() + timedelta(minutes=2)
    (res,) = loop.run_once(now=later)
    assert res.ok


def test_the_loop_does_nothing_while_the_hand_off_is_off(handoff, monkeypatch):
    cfg = _write_cfg(handoff["lab"], enabled=False)
    monkeypatch.setattr(atlas, "send", lambda *a, **k: pytest.fail("sent while off"))
    atlas._set_state(handoff["exp"], {"state": "pending"})
    assert atlas.HandoffLoop(lambda: cfg).run_once() == []


def test_the_workers_hook_marks_a_done_job_and_never_raises(handoff, monkeypatch):
    from ionomos import worker

    stale = handoff["cfg"]  # the config as the watcher started
    worker._atlas_queue(stale, handoff["ledger"], handoff["job"].id)
    assert atlas.state(handoff["exp"])["state"] == "pending"

    class Broken:
        def get(self, _):
            raise RuntimeError("ledger gone")

    worker._atlas_queue(stale, Broken(), 1)  # logged, not raised


def test_the_workers_hook_reads_the_config_again(handoff):
    from ionomos import worker

    started_off = load(_write_cfg(handoff["lab"], enabled=False).config_path)
    _write_cfg(handoff["lab"], enabled=True, handoff_dir=str(handoff["root"]))  # turned on in the app since
    worker._atlas_queue(started_off, handoff["ledger"], handoff["job"].id)
    assert atlas.state(handoff["exp"])["state"] == "pending"


def test_a_search_that_ends_done_is_handed_off_end_to_end(tmp_path, monkeypatch):
    """The testbed's fake FragPipe runs an isoDTB drop to done; the worker marks it, the loop hands it off."""
    from ionomos import testbed
    from ionomos.intake import intake
    from ionomos.worker import Worker

    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    cfg_path = testbed.init(tmp_path / "bed")
    d = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    (tmp_path / "Lab Dropbox").mkdir()
    d["atlas"] = {"enabled": True, "handoff_dir": str(tmp_path / "Lab Dropbox" / "fragpipe-runs")}
    cfg_path.write_text(yaml.safe_dump(d), encoding="utf-8")
    cfg = load(cfg_path)
    led = Ledger(cfg.database)
    assert intake(testbed.drop(tmp_path / "bed", "iso_good"), cfg, led).value == "queued"
    assert Worker(cfg, led).run_once()
    job = led.list()[-1]
    assert job.status == "done", job.reason
    assert atlas.state(job.dest_dir)["state"] == "pending"
    (res,) = atlas.HandoffLoop(lambda: cfg).run_once()
    assert res.ok, res.message
    run_dest = atlas.destination(Path(job.dest_dir) / "fragpipe", Path(d["atlas"]["handoff_dir"]))
    got = _files(run_dest)
    assert "fragpipe.workflow" in got and [r for r in got if r.endswith("/psm.tsv")]
    assert json.loads(_read(run_dest / names.ATLAS_HANDOFF_FILE))["job"]["method"] == "isoDTB"


# ---------------------------------------------------------------------------------------- the commands --


def test_the_commands_on_a_lab_with_a_finished_job(handoff, capsys):
    from ionomos.cli import main

    cfg = ["--config", str(handoff["cfg"].config_path)]
    assert main([*cfg, "atlas", "status"]) == 0
    assert "not handed off" in capsys.readouterr().out
    assert main([*cfg, "atlas", "send", "1"]) == 0
    out = capsys.readouterr().out
    assert "handed to the atlas" in out and "record:" in out
    assert main([*cfg, "atlas", "status", "1"]) == 0
    assert "sent 20" in capsys.readouterr().out
    assert main([*cfg, "atlas", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["1"]["state"] == "sent"
    assert main([*cfg, "atlas", "check"]) == 0
    out = capsys.readouterr().out
    assert "this account can write to it" in out
    assert not [p for p in handoff["root"].iterdir() if p.name.startswith(".ionomos-write-test")]
    # a conflict is exit 1 and names the file
    victim = Path(atlas.state(handoff["exp"])["dest"]) / "fragpipe.job"
    _write(victim, b"edited")
    assert main([*cfg, "atlas", "send", "1"]) == 1
    assert "differs:" in capsys.readouterr().err


def test_send_while_off_is_exit_2(handoff, capsys):
    from ionomos.cli import main

    cfg = _write_cfg(handoff["lab"], enabled=False)
    assert main(["--config", str(cfg.config_path), "atlas", "send", "1"]) == 2
    assert "is off" in capsys.readouterr().err


def test_check_finds_an_unset_or_unreachable_folder(handoff, monkeypatch):
    monkeypatch.setattr(atlas, "dropbox_suggestion", lambda *a, **k: "")  # never this PC's own Dropbox
    cfg = _write_cfg(handoff["lab"], enabled=True)
    assert [f.ok for f in atlas.check(cfg)] == [True, False]
    cfg = _write_cfg(handoff["lab"], enabled=True, handoff_dir=str(handoff["lab"]["root"] / "No Dropbox" / "x"))
    assert not all(f.ok for f in atlas.check(cfg))
    assert all(f.ok for f in atlas.check(handoff["cfg"]))  # its parent is there: the first hand-off makes it


# ------------------------------------------------------------------------------------- the Dropbox folder --


def _dropbox(tmp_path, info: dict) -> dict:
    local = tmp_path / "Local"
    (local / "Dropbox").mkdir(parents=True)
    (local / "Dropbox" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    return {"LOCALAPPDATA": str(local), "APPDATA": str(tmp_path / "Roaming")}


def test_the_dropbox_folder_is_found_as_harvest_finds_it(tmp_path):
    team = tmp_path / "TNRG Dropbox"
    (team / "Daniel Nomura" / "Nicholas Foster Data").mkdir(parents=True)
    env = _dropbox(tmp_path, {"business": {"path": str(team), "root_path": str(team)},
                              "personal": {"path": str(tmp_path / "Dropbox")}})
    assert atlas.dropbox_roots(env) == [str(team), str(tmp_path / "Dropbox")]
    assert atlas.dropbox_suggestion(env) == str(team / "Daniel Nomura" / "Nicholas Foster Data" / "fragpipe-runs")


def test_no_dropbox_no_suggestion(tmp_path):
    assert atlas.dropbox_suggestion({"LOCALAPPDATA": str(tmp_path)}) == ""
    deep = tmp_path / "Dropbox" / "a" / "b" / "c"
    (deep / "Nicholas Foster Data").mkdir(parents=True)  # three levels down: harvest doesn't look that far
    assert atlas.dropbox_suggestion(roots=[str(tmp_path / "Dropbox")]) == ""
