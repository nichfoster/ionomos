"""Adversarial tests of the security / privacy boundaries (docs/SECURITY_NOTES.md).

Each block names the boundary it attacks: a bundle someone else made (unpack, inspect), the YAML loaders, the
notification channels, the command lines Ionomos builds, the assistant's tools, the library scan and the update
path; then the secrets a lab plants in config.yaml and its logs, searched for in everything Ionomos writes or
sends. No test opens a socket to anything but 127.0.0.1."""
import json
import os
import shutil
import warnings
import zipfile
from pathlib import Path

import pytest
import yaml

from ionomos import bundle, names

# ------------------------------------------------- a bundle from someone else ----


def _zip(path: Path, members: dict, jobs=None, level="validate") -> Path:
    """A zip that looks like a bundle: BUNDLE.json (with `jobs`) plus `members` {name: bytes | str}."""
    manifest = {"format": 1, "level": level, "jobs": jobs or [], "files": []}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Duplicate name": the point of one test
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(names.BUNDLE_MANIFEST, json.dumps(manifest))
            for n, data in members.items() if isinstance(members, dict) else members:
                z.writestr(n, data)
    return path


def _files_under(root: Path) -> set[str]:
    """Every file under `root` but the BUNDLE.json that unpack copies into _bundle/."""
    return ({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != names.BUNDLE_MANIFEST}
            if root.exists() else set())


CLIMBING = ["../outside.txt", "..\\outside_win.txt", "/abs.txt", "\\abs_win.txt", "C:/Windows/Temp/drive.txt",
            "C:drive_relative.txt", "\\\\server\\share\\unc.txt", "//server/share/unc2.txt",
            "jobs/../../../up.txt", "_bundle/../../up2.txt"]
ODD = ["_bundle/a.txt:stream", "_bundle/CON", "_bundle/nul.txt", "_bundle/COM1.log", "_bundle/trailing.",
       "_bundle/trailing ", "_bundle/ctl\x01.txt"]


def test_unpack_keeps_every_member_inside_the_folder(tmp_path):
    z = _zip(tmp_path / "evil.zip", {**{n: b"x" for n in CLIMBING + ODD}, "notes/ok.txt": b"fine"})
    dest = tmp_path / "a" / "b" / "un"
    before = _files_under(tmp_path)
    out = bundle.unpack(z, dest)
    assert _files_under(dest) == {"_bundle/notes/ok.txt"}
    assert _files_under(tmp_path) - before == {"a/b/un/_bundle/notes/ok.txt"}  # nothing anywhere else
    said = " ".join(out["notes"])
    assert f"left out {len(CLIMBING + ODD)} file(s)" in said


@pytest.mark.parametrize("folder", ["../../escaped", "..", ".", "C:/Windows/Temp/escaped", "C:escaped",
                                    "\\\\server\\share", "a/b", "_bundle", "_LAB", "config.yaml", "", None, 7])
def test_unpack_refuses_a_job_folder_name_from_the_manifest_that_is_not_one_plain_name(tmp_path, folder):
    """BUNDLE.json names each job's folder; a sender who writes ../../x there must not choose where files go."""
    jobs = [{"arc": "jobs/exp001", "folder": folder}, {"arc": "jobs/exp002", "folder": "exp002"}]
    z = _zip(tmp_path / "evil.zip", {"jobs/exp001/x.tsv": b"x", "jobs/exp002/y.tsv": b"y"}, jobs)
    dest = tmp_path / "w" / "un"
    before = _files_under(tmp_path)
    out = bundle.unpack(z, dest)
    if folder in ("", None):  # no folder: the arc's own last part, which is a plain name
        assert _files_under(dest) == {"exp001/x.tsv", "exp002/y.tsv"}
        return
    assert _files_under(dest) == {"exp002/y.tsv"}  # the bad job is left out whole, not spilled into _bundle
    assert _files_under(tmp_path) - before == {"w/un/exp002/y.tsv"}
    assert out["experiments"] == [dest / "exp002"]
    assert any("left out job 'jobs/exp001'" in n for n in out["notes"])


def test_unpack_refuses_a_job_arc_that_climbs_and_two_jobs_with_one_folder(tmp_path):
    jobs = [{"arc": "jobs/..", "folder": "x"}, {"arc": "jobs/a", "folder": "same"}, {"arc": "jobs/b", "folder": "SAME"}]
    z = _zip(tmp_path / "e.zip", {"jobs/a/1.tsv": b"1", "jobs/b/2.tsv": b"2"}, jobs)
    out = bundle.unpack(z, tmp_path / "un")
    assert _files_under(tmp_path / "un") == {"same/1.tsv"}
    assert sum("left out job" in n for n in out["notes"]) == 2


def test_unpack_two_members_with_one_name_is_a_note_not_a_traceback(tmp_path):
    z = _zip(tmp_path / "dup.zip", [("_bundle_x/a.txt", b"first"), ("_bundle_x/a.txt", b"second"),
                                    ("_bundle_x/A.txt", b"third")])
    out = bundle.unpack(z, tmp_path / "un")
    assert [p.read_bytes() for p in (tmp_path / "un").rglob("*.txt")] == [b"first"]
    assert any("left out 2 file(s)" in n for n in out["notes"])


def _with_declared_size(path: Path, member: str, size: int, data: bytes = b"\0" * 1000) -> Path:
    """A zip whose central directory says `member` unpacks to `size` bytes (a 10 GB bomb without writing one):
    zipfile writes the directory from its ZipInfo list at close, so the size is changed just before."""
    with zipfile.ZipFile(path, "a" if path.exists() else "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, data)
        z.filelist[-1].file_size = size
    return path


def test_unpack_refuses_a_10_gb_zip_bomb_before_writing_anything(tmp_path):
    z = _zip(tmp_path / "bomb.zip", {"_bundle_x/ok.txt": b"ok"})
    _with_declared_size(z, "_bundle_x/bomb.tsv", 10 << 30)
    with zipfile.ZipFile(z) as zz:
        assert zz.getinfo("_bundle_x/bomb.tsv").file_size == 10 << 30
    dest = tmp_path / "un"
    with pytest.raises(bundle.BundleError, match="zip bomb"):
        bundle.unpack(z, dest)
    assert not dest.exists()
    from ionomos import cli

    assert cli.main(["bundle", "unpack", str(z), str(tmp_path / "un2")]) == 1


def test_unpack_refuses_more_than_the_disk_has_free(tmp_path, monkeypatch):
    z = _zip(tmp_path / "big.zip", {"_bundle_x/t.tsv": b"a\tb\n" * 1000})
    real = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda p: real(p)._replace(free=bundle.FREE_MARGIN_BYTES + 100))
    with pytest.raises(bundle.BundleError, match="free"):
        bundle.unpack(z, tmp_path / "un")
    assert not (tmp_path / "un").exists()


def test_inspect_refuses_a_manifest_that_says_it_is_gigabytes(tmp_path):
    z = tmp_path / "m.zip"
    _with_declared_size(z, names.BUNDLE_MANIFEST, 10 << 30, b"{}")
    with pytest.raises(bundle.BundleError, match="not a bundle Ionomos made"):
        bundle.inspect(z)
    with zipfile.ZipFile(tmp_path / "list.zip", "w") as zz:
        zz.writestr(names.BUNDLE_MANIFEST, "[1, 2]")
    with pytest.raises(bundle.BundleError, match="not a JSON object"):
        bundle.inspect(tmp_path / "list.zip")


def test_unpack_a_damaged_member_is_a_bundle_error(tmp_path):
    z = _zip(tmp_path / "bad.zip", {"_bundle_x/t.tsv": os.urandom(4000)})
    raw = bytearray(z.read_bytes())
    at = raw.find(b"_bundle_x/t.tsv") + len("_bundle_x/t.tsv") + 50  # inside the deflated data
    raw[at:at + 64] = bytes(64)
    z.write_bytes(bytes(raw))
    with pytest.raises(bundle.BundleError, match="damaged"):
        bundle.unpack(z, tmp_path / "un")


PWNED = "IONOMOS_PWNED.txt"


def _bundle_config(tmp_path: Path, text: str) -> dict:
    z = _zip(tmp_path / "cfg.zip", {"config.yaml": text})
    return bundle.unpack(z, tmp_path / "un")


def test_unpacked_config_with_a_python_tag_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = _bundle_config(tmp_path, f"paths: {{}}\nx: !!python/object/apply:os.system ['echo x > {PWNED}']\n")
    assert out["config"] is None and any("does not load" in n for n in out["notes"])
    assert not list(tmp_path.rglob(PWNED))


def test_unpacked_config_keeps_no_program_the_sender_named(tmp_path):
    cfg = {"paths": {"inbox": "x"}, "methods": {
        "DIA": {"engine": "diann", "data_type": "DIA", "workflow": "DIA.workflow", "fasta": "h.fas",
                "diann_exe": "C:/evil/diann.exe"},
        "LFQ": {"engine": "maxquant", "data_type": "DDA", "workflow": "x.xml", "fasta": "h.fas",
                "maxquant_exe": "C:/evil/MaxQuantCmd.exe"}},
        "fragpipe": {"config_tools_folder": "\\\\evil\\tools", "config_python": "C:/evil/python"},
        "library": {"roots": ["\\\\evil\\share"]},
        "notify": {"enabled": True, "slack": "https://hooks.slack.com/services/T0/B0/x"},
        "assistant": {"enabled": True, "base_url": "http://127.0.0.1:1/v1"}}
    out = _bundle_config(tmp_path, yaml.safe_dump(cfg))
    written = (tmp_path / "un" / "config.yaml").read_text(encoding="utf-8")
    for word in ("evil", "notify", "assistant", "library", "hooks.slack"):
        assert word not in written, word
    assert out["config"] == tmp_path / "un" / "config.yaml" or any("does not load" in n for n in out["notes"])
