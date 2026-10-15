"""`ionomos example` (docs/EXAMPLE.md, D95): real published data, downloaded, checked, written as experiment folders
and analysed. No real network here: the download is stubbed (example.urlopen) with a zip built from the excerpt in
tests/golden/example/ (CC BY 4.0, see its README.txt), and example.ARCHIVE is pointed at that zip's size and hashes."""
import csv
import hashlib
import io
import os
import subprocess
import sys
import textwrap
import urllib.error
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from ionomos import cli, example
from ionomos.downstream import quant

GOLDEN = Path(__file__).parent / "golden" / "example"


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


@pytest.fixture
def excerpt(tmp_path_factory, monkeypatch):
    """A zip laid out like the published one, holding the excerpt; ARCHIVE describes it. Returns its bytes."""
    d = tmp_path_factory.mktemp("zip")
    z = d / example.ARCHIVE.name
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(GOLDEN.rglob("*.tsv")):
            zf.write(f, example.ARCHIVE.prefix + f.relative_to(GOLDEN).as_posix())
    data = z.read_bytes()
    monkeypatch.setattr(example, "ARCHIVE", replace(example.ARCHIVE, size=len(data),
                                                    md5=hashlib.md5(data).hexdigest(),
                                                    sha256=hashlib.sha256(data).hexdigest()))
    return data


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _serve(monkeypatch, data: bytes, calls: list | None = None):
    def fake(req, timeout=None):
        assert timeout, "every network call has a timeout"
        if calls is not None:
            calls.append(req.full_url)
        return _Response(data)

    monkeypatch.delenv("IONOMOS_OFFLINE", raising=False)
    monkeypatch.setattr(example, "urlopen", fake)


def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("no network call may be made here")

    monkeypatch.setattr(example, "urlopen", refuse)


# ------------------------------------------------------------- the data --


def test_the_published_archive_is_described_exactly():
    a = example.ARCHIVE
    assert a.url.startswith("https://zenodo.org/records/13147961/files/" + a.name)
    assert a.size == 20_856_553 and len(a.md5) == 32 and len(a.sha256) == 64
    assert "CC BY 4.0" in a.licence and a.doi == "10.5281/zenodo.13147961"
    assert "10.1021/acs.jproteome.4c00294" in a.citation
    assert set(example.DATASETS) == {"apms", "dia", "tmt"}
    for d in example.DATASETS.values():  # every table the example unpacks is in the excerpt the tests use
        assert (GOLDEN / d.table[0]).is_file() and (GOLDEN / d.annotation[0]).is_file()
        assert " " not in d.table[1] and d.comparison.endswith(" vs " + d.control)


def test_the_excerpt_is_real_rows_and_carries_its_licence():
    assert "Attribution 4.0 International" in (GOLDEN / "LICENSE-CC-BY-4.0.txt").read_text(encoding="utf-8")
    readme = (GOLDEN / "README.txt").read_text(encoding="utf-8")
    assert "zenodo.13147961" in readme and "CC BY 4.0" in readme and "Changes made" in readme
    for member in ("AP-MS/combined_protein.tsv", "DIA_4plex/diann-output.pg_matrix.tsv",
                   "TMT_4plex/ratio_protein_MD.tsv"):
        assert len((GOLDEN / member).read_bytes().splitlines()) == 241  # header + 240 rows: an excerpt, not the data


def test_list_says_what_and_where_and_downloads_nothing(tmp_path, monkeypatch, capsys):
    _no_network(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["example", "--list"]) == 0
    out = capsys.readouterr().out
    for word in ("zenodo.org/records/13147961", "CC BY 4.0", "apms:", "dia:", "tmt:", "Swantek", "Clark"):
        assert word in out, word
    assert not list(tmp_path.iterdir())


# ----------------------------------------------------------- end to end --


def test_example_end_to_end_from_a_stubbed_download(tmp_path, monkeypatch, excerpt, capsys):
    calls: list = []
    _serve(monkeypatch, excerpt, calls)
    folder = tmp_path / "ex"
    assert cli.main(["example", str(folder), "--quiet", "--no-enrichment"]) == 0
    printed = capsys.readouterr().out
    assert calls == [example.ARCHIVE.url]
    kept = folder / "download" / example.ARCHIVE.name
    assert kept.read_bytes() == excerpt
    assert not [p for p in folder.rglob("*.part")], "no temporary file is left behind"
    readme = (folder / "README.txt").read_text(encoding="utf-8")
    assert "CC BY 4.0" in readme and example.ARCHIVE.record in readme and "Changes made" in readme

    for key, ds in example.DATASETS.items():
        exp = folder / key
        # the tables are the authors', byte for byte
        assert (exp / ds.table[1]).read_bytes() == (GOLDEN / ds.table[0]).read_bytes()
        assert (exp / ds.annotation[1]).read_bytes() == (GOLDEN / ds.annotation[0]).read_bytes()
        assert (exp / "README.txt").is_file()
        report = exp / "results" / "report.html"
        assert report.is_file() and f"report: {report}" in printed

    # the design: what the names say (AP-MS), or the authors' annotation (DIA, TMT), with their exclusions
    import yaml

    dia = yaml.safe_load((folder / "dia" / "experiment.yaml").read_text(encoding="utf-8"))["analysis"]
    assert dia["control"] == "NAT" and dia["comparisons"] == ["Tumor vs NAT"]
    assert dia["exclude_samples"] == ["CPTAC_CCRCC_W_JHU_20190112_LUMOS_C3N-00832_T"]
    assert len(dia["sample_conditions"]) == 32 and set(dia["sample_conditions"].values()) == {"Tumor", "NAT"}
    tmt = yaml.safe_load((folder / "tmt" / "experiment.yaml").read_text(encoding="utf-8"))["analysis"]
    assert tmt["exclude_samples"] == ["QC6", "QC7", "QC8", "CPT0078800003"]
    assert not any(s.startswith("pool") for s in tmt["sample_conditions"])
    apms = yaml.safe_load((folder / "apms" / "experiment.yaml").read_text(encoding="utf-8"))["analysis"]
    assert apms == {"control": "CONTROL", "comparisons": ["CCND1 vs CONTROL"]}

    def hits(key):
        t = next((folder / key / "results").glob("*_differential.tsv"))
        return {r["label"]: r["significant"] for r in _rows(t)}

    # the biology the README promises, found again in a 240-row excerpt
    assert {hits("apms")[g] for g in ("CCND1", "CDK4", "CDK6", "CDKN1A")} == {"up"}
    for key in ("dia", "tmt"):
        h = hits(key)
        assert (h["CA9"], h["AHNAK2"], h["NDUFV2"], h["SFRP1"], h["PIGR"]) == ("up", "up", "down", "down", "down")
    onoff = {(r["only_in"], r["label"]) for r in _rows(folder / "apms" / "results" / "presence_absence.tsv")}
    assert {("CCND1", "CCND1"), ("CCND1", "CDK4")} <= onoff


def test_tmt_protein_tables_are_labelled_by_gene():
    """TMT-Integrator's protein tables: Index is the accession, Gene the symbol (the label, and enrichment's key);
    its gene tables have no Gene column and keep Index."""
    m = quant.from_tmt_abundance(GOLDEN / "TMT_4plex" / "ratio_protein_MD.tsv")
    f = next(x for x in m.features if x.id == "Q16790")
    assert f.label == "CA9"
    g = quant.from_tmt_abundance(Path(__file__).parent / "golden" / "tmt_abundance_gene_MD.tsv")
    assert all(x.label == x.id for x in g.features)


def test_a_hand_downloaded_zip_is_used_and_never_changed(tmp_path, monkeypatch, excerpt, capsys):
    _no_network(monkeypatch)
    z = tmp_path / "elsewhere" / example.ARCHIVE.name
    z.parent.mkdir()
    z.write_bytes(excerpt)
    before = z.stat().st_mtime_ns
    folder = tmp_path / "ex"
    assert cli.main(["example", str(folder), "--zip", str(z), "--dataset", "apms", "--quiet",
                     "--no-enrichment"]) == 0
    assert z.read_bytes() == excerpt and z.stat().st_mtime_ns == before
    assert sorted(p.name for p in folder.iterdir()) == ["README.txt", "apms"]  # no download/, no dia/ or tmt/
    assert "checked: size, MD5 and SHA-256 match" in capsys.readouterr().out


def test_a_copy_already_here_is_found_and_checked(tmp_path, monkeypatch, excerpt, capsys):
    _no_network(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / example.ARCHIVE.name).write_bytes(excerpt)  # downloaded by hand into the folder it runs from
    assert cli.main(["example", "--dataset", "apms", "--quiet", "--no-enrichment"]) == 0
    assert (tmp_path / "ionomos_example" / "apms" / "results" / "report.html").is_file()
    assert "using the copy already here" in capsys.readouterr().out
    (tmp_path / example.ARCHIVE.name).unlink()
    earlier = tmp_path / "ionomos_example" / "download"
    earlier.mkdir()
    (earlier / example.ARCHIVE.name).write_bytes(excerpt)  # kept by an earlier run: re-runs need no network
    assert example.find_local(tmp_path) == earlier / example.ARCHIVE.name
    assert cli.main(["example", "--dataset", "tmt", "--quiet", "--no-enrichment"]) == 0
    assert (tmp_path / "ionomos_example_2" / "tmt" / "results" / "report.html").is_file()


# --------------------------------------------------------------- rules --


def test_a_download_that_does_not_match_is_not_kept(tmp_path, monkeypatch, excerpt, capsys):
    bad = bytearray(excerpt)
    bad[len(bad) // 2] ^= 0xFF  # same size, other bytes
    _serve(monkeypatch, bytes(bad))
    folder = tmp_path / "ex"
    assert cli.main(["example", str(folder), "--quiet"]) == 2
    err = capsys.readouterr().err
    assert "is not the published one" in err and "MD5" in err and example.ARCHIVE.url in err
    assert not folder.exists(), "the folder this run made, empty again, is gone; nothing half-done is left"

    _serve(monkeypatch, excerpt + b"more")  # larger than published: stopped while reading
    assert cli.main(["example", str(folder), "--quiet"]) == 2
    assert "larger than the published" in capsys.readouterr().err and not folder.exists()


def test_a_bad_zip_given_by_hand_is_refused_and_left_alone(tmp_path, monkeypatch, excerpt, capsys):
    _no_network(monkeypatch)
    z = tmp_path / "mine.zip"
    z.write_bytes(excerpt[:-10])
    assert cli.main(["example", str(tmp_path / "ex"), "--zip", str(z)]) == 2
    assert "is not the published" in capsys.readouterr().err
    assert z.read_bytes() == excerpt[:-10] and not (tmp_path / "ex").exists()


def test_nothing_that_exists_is_written_over(tmp_path, monkeypatch, excerpt, capsys):
    _no_network(monkeypatch)
    full = tmp_path / "full"
    full.mkdir()
    (full / "mine.txt").write_text("keep", encoding="utf-8")
    assert cli.main(["example", str(full), "--quiet"]) == 2
    assert "not an empty folder" in capsys.readouterr().err
    assert [p.name for p in full.iterdir()] == ["mine.txt"]
    # the default name moves on
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ionomos_example").mkdir()
    assert example.pick_folder(None) == tmp_path / "ionomos_example_2"
    # the download never replaces a file of its name
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / example.ARCHIVE.name).write_bytes(b"someone's file")
    _serve(monkeypatch, excerpt)
    with pytest.raises(example.ExampleError, match="already exists"):
        example.download(dl)
    assert (dl / example.ARCHIVE.name).read_bytes() == b"someone's file"
    # each file is created new: a name that exists is an error, not a replacement
    with pytest.raises(FileExistsError):
        example._write_new(full / "mine.txt", b"other")
    assert (full / "mine.txt").read_text(encoding="utf-8") == "keep"
    # a user's empty folder that existed before stays after a failed start
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    assert cli.main(["example", str(empty), "--quiet"]) == 2
    assert empty.is_dir()


def test_offline_says_where_to_fetch_it_and_what_to_run(tmp_path, monkeypatch, capsys):
    _no_network(monkeypatch)
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["example"]) == 2
    err = capsys.readouterr().err
    assert example.ARCHIVE.url in err and "ionomos example --zip" in err and "IONOMOS_OFFLINE" in err
    assert not list(tmp_path.iterdir()), "a failed start leaves no empty ionomos_example behind"


def test_a_network_failure_is_a_sentence_and_leaves_no_part_file(tmp_path, monkeypatch, capsys):
    def unreachable(req, timeout=None):
        raise urllib.error.URLError("getaddrinfo failed")

    monkeypatch.delenv("IONOMOS_OFFLINE", raising=False)
    monkeypatch.setattr(example, "urlopen", unreachable)
    dl = tmp_path / "dl"
    with pytest.raises(example.ExampleError) as e:
        example.download(dl)
    msg = str(e.value)
    assert "could not reach zenodo.org" in msg and example.ARCHIVE.url in msg and "--zip" in msg
    assert not list(dl.iterdir())


def test_progress_lines_without_a_terminal():
    out = io.StringIO()
    show = example._progress_printer(out)
    for done in range(0, 101, 5):
        show(done, 100)
    lines = out.getvalue().splitlines()
    assert len(lines) == 5 and lines[-1].endswith("(100%)") and "\r" not in out.getvalue()


# ----------------------------------------------------------------- CLI --


def test_cli_help_and_module_docstring_name_the_command(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["example", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--dataset" in out and "--zip" in out and "--list" in out
    assert "ionomos example" in (cli.__doc__ or "")


NO_TK = """
import sys
sys.modules["tkinter"] = None
sys.modules["_tkinter"] = None
from ionomos.cli import main
rc = main(["example", "--list"])
gui = sorted(m for m in sys.modules if m in ("ionomos.app", "ionomos.tkutil", "ionomos.popups"))
print("RESULT", rc, gui)
"""


def test_example_needs_no_tkinter(tmp_path):
    env = {**os.environ, "IONOMOS_OFFLINE": "1", "IONOMOS_NO_GUI": "1",
           "IONOMOS_CONFIG": str(tmp_path / "no-config.yaml")}
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(NO_TK)], cwd=tmp_path, env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr
    assert "RESULT 0 []" in r.stdout, r.stdout + r.stderr
