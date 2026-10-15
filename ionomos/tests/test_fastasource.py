"""fastasource.py: UniProt downloads, FragPipe-ready FASTAs (Philosopher or the Python twin), the release check and
FragPipe's stock workflows (docs/FASTA.md, D90).

No real network: every test that reaches the network replaces `fastasource.urlopen` with `FakeNet`, which answers
from canned responses (headers as UniProt / GitHub send them, checked on 2026-10-14) and records each request.
Philosopher is a small Python stand-in run as a real subprocess."""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
import threading
import urllib.error
import zipfile
from datetime import date
from pathlib import Path

import pytest

from ionomos import cli, fastasource, fragpipe

UNIPROT_HEADERS = {"X-UniProt-Release": "2026_03", "X-UniProt-Release-Date": "02-September-2026"}

HUMAN = (b">sp|P68871|HBB_HUMAN Hemoglobin subunit beta OS=Homo sapiens OX=9606 GN=HBB PE=1 SV=2\n"
         b"MVHLTPEEKSAVTALWGKVNVDEVGGEALGRLLVVYPWTQRFFESFGDLSTPDAVMGNPK\nVKAHGKKVLGAFSDGLAHLDNLKGTFATLSELHCDKLHVDPENFR\n"
         b">sp|P69905|HBA_HUMAN Hemoglobin subunit alpha OS=Homo sapiens OX=9606 GN=HBA1 PE=1 SV=2\n"
         b"MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSHGSAQVKGHGKKVADALTNAVAHV\n"
         b">sp|P02768|ALBU_HUMAN Albumin OS=Homo sapiens OX=9606 GN=ALB PE=1 SV=2\n"
         b"MKWVTFISLLFLFSSAYSRGVFRRDAHKSEVAHRFKDLGEENFKALVLIAFAQYLQQCPFEDH\n")

CRAP = (b">sp|P02769|ALBU_BOVIN Albumin OS=Bos taurus OX=9913 GN=ALB PE=1 SV=4\nMKWVTFISLLLLFSSAYSRGVFRR\n"
        b">sp|P00761|TRYP_PIG Trypsin OS=Sus scrofa OX=9823 PE=1 SV=1\nFPTDDDDKIVGGYTCAANSIPYQVSLNS\n"
        b">sp|P02768|ALBU_HUMAN Albumin (also a target) OS=Homo sapiens OX=9606\nMKWVTF\n")


class Resp:
    def __init__(self, body: bytes, headers: dict):
        self._body, self.headers, self.status = io.BytesIO(body), headers, 200

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNet:
    """urlopen stand-in: routes are (substring of the URL, answer); an answer is (body, headers), an exception
    instance (raised), or an int HTTP status (an HTTPError). The first route whose substring is in the URL wins."""

    def __init__(self, *routes):
        self.routes = list(routes)
        self.calls: list[tuple[str, float | None]] = []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append((url, timeout))
        for key, answer in self.routes:
            if key in url:
                if isinstance(answer, BaseException):
                    raise answer
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(url, answer, "error", {}, io.BytesIO(b"nope"))
                body, headers = answer
                return Resp(body, headers)
        raise AssertionError(f"unexpected request {url}")

    def urls(self) -> list[str]:
        return [u for u, _ in self.calls]


@pytest.fixture
def net(monkeypatch):
    """Install a FakeNet (online: IONOMOS_OFFLINE removed); returns a function that sets its routes (and forgets
    the requests so far)."""
    monkeypatch.delenv("IONOMOS_OFFLINE", raising=False)
    fake = FakeNet()
    monkeypatch.setattr(fastasource, "urlopen", fake)

    def routes(*r):
        fake.routes, fake.calls = list(r), []
        return fake

    return routes


def _uniprot(total: int, fasta: bytes = HUMAN, compress: bool = True):
    count = (b"", {**UNIPROT_HEADERS, "X-Total-Results": str(total)})
    stream = (gzip.compress(fasta) if compress else fasta, dict(UNIPROT_HEADERS))
    return ("uniprotkb/search", count), ("uniprotkb/stream", stream)


def _files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


# ---------------------------------------------------------------- download --


def test_download_writes_the_proteome_named_like_uniprots_ftp_and_checks_the_count(tmp_path, net):
    fake = net(*_uniprot(3))
    seen = []
    res = fastasource.download_proteome("up000005640", tmp_path, progress=lambda m, d, t: seen.append(m))
    assert res.ok, res.error
    assert res.path == tmp_path / "UP000005640_9606.fasta"
    assert res.path.read_bytes() == HUMAN
    assert (res.entries, res.expected, res.taxon, res.organism) == (3, 3, "9606", "Homo sapiens")
    assert (res.release, res.release_date) == ("2026_03", "2026-09-02")
    assert _files(tmp_path) == ["UP000005640_9606.fasta"], "no .part file is left behind"
    count_url, stream_url = fake.urls()
    for url in (count_url, stream_url):
        assert "proteome%3AUP000005640" in url and "reviewed%3Atrue" in url and "includeIsoform=false" in url
    assert "size=0" in count_url and "/stream?" in stream_url and "compressed=true" in stream_url
    assert all(t for _u, t in fake.calls), "every request has a timeout"
    assert seen and seen[-1].startswith("saved")


def test_download_all_entries_with_isoforms_and_an_unpacked_answer(tmp_path, net):
    fake = net(*_uniprot(3, compress=False))
    res = fastasource.download_proteome("UP000005640", tmp_path, reviewed=False, isoforms=True)
    assert res.ok and res.path.read_bytes() == HUMAN
    assert all("reviewed" not in u and "includeIsoform=true" in u for u in fake.urls())


@pytest.mark.parametrize("routes, words", [
    (lambda: _uniprot(5), "got 3 entries but UniProt counts 5"),
    (lambda: _uniprot(0), "--unreviewed"),
    (lambda: [("uniprotkb", urllib.error.URLError("getaddrinfo failed"))], "could not reach rest.uniprot.org"),
    (lambda: [("uniprotkb", TimeoutError("timed out"))], "did not answer in time"),
    (lambda: [("uniprotkb/search", 400)], "HTTP 400"),
    (lambda: [("uniprotkb/search", (b"", {**UNIPROT_HEADERS, "X-Total-Results": "3"})),
              ("uniprotkb/stream", (b"\x1f\x8b" + b"broken", {}))], "could not be unpacked"),
])
def test_download_problems_are_errors_in_words_and_leave_nothing(tmp_path, net, routes, words):
    net(*routes())
    res = fastasource.download_proteome("UP000005640", tmp_path)
    assert not res.ok and words in res.error
    assert _files(tmp_path) == []


def test_download_never_overwrites(tmp_path, net):
    old = tmp_path / "UP000005640_9606.fasta"
    old.write_bytes(b">old\nAAA\n")
    net(*_uniprot(3))
    res = fastasource.download_proteome("UP000005640", tmp_path)
    assert res.path.name == "UP000005640_9606-2.fasta"
    assert old.read_bytes() == b">old\nAAA\n"
    assert any("already exists" in n for n in res.notes)


def test_download_stops_when_asked(tmp_path, net):
    net(*_uniprot(3))
    stop = threading.Event()

    def progress(message, done, total):
        if message.startswith("downloading"):
            stop.set()

    res = fastasource.download_proteome("UP000005640", tmp_path, progress=progress, stop=stop)
    assert res.error == "stopped" and res.path is None
    assert _files(tmp_path) == []


def test_download_refuses_a_bad_id_and_offline_without_any_request(tmp_path, monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(fastasource, "urlopen", fake)
    monkeypatch.delenv("IONOMOS_OFFLINE", raising=False)
    assert "not a UniProt proteome id" in fastasource.download_proteome("human", tmp_path).error
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    assert "IONOMOS_OFFLINE" in fastasource.download_proteome("UP000005640", tmp_path).error
    assert fastasource.search_proteomes("human").error
    assert fastasource.uniprot_release() is None
    assert fastasource.stock_workflows("24.0").error
    assert fastasource.fetch_stock_workflow("24.0", "TMT10", tmp_path).error
    assert fake.calls == []


# ------------------------------------------------------------------ search --

PROTEOMES_TSV = (b"Proteome Id\tOrganism\tOrganism Id\tProtein count\n"
                 b"UP000515126\tMus caroli (Ryukyu mouse) (Ricefield mouse)\t10089\t38135\n"
                 b"UP000000589\tMus musculus (Mouse)\t10090\t54855\n")


def test_search_proteomes_reads_uniprots_table_and_puts_the_common_name_first(net):
    fake = net(("proteomes/search", (PROTEOMES_TSV, {**UNIPROT_HEADERS, "X-Total-Results": "2"})))
    res = fastasource.search_proteomes("  mouse ")
    assert [r["id"] for r in res.rows] == ["UP000000589", "UP000515126"]
    assert res.rows[0] == {"id": "UP000000589", "organism": "Mus musculus (Mouse)", "taxon": "10090",
                           "proteins": 54855}
    assert "proteome_type%3AREFERENCE" in fake.urls()[0] and "format=tsv" in fake.urls()[0]
    fastasource.search_proteomes("UP000005640")
    fastasource.search_proteomes("9606", reference_only=False)
    assert "upid%3AUP000005640" in fake.urls()[1] and "organism_id%3A9606" in fake.urls()[2]
    assert "proteome_type" not in fake.urls()[2]


def test_search_proteomes_nothing_found_and_empty_text(net):
    net(("proteomes/search", (b"Proteome Id\tOrganism\tOrganism Id\tProtein count\n", {})))
    res = fastasource.search_proteomes("unicorn")
    assert res.rows == [] and "no reference proteome" in res.notes[0]
    assert "type an organism" in fastasource.search_proteomes(" ").error


# ------------------------------------------------------------------ release --


def test_uniprot_release_from_the_headers_and_offline(net):
    net(("uniprotkb/search", (b"", UNIPROT_HEADERS)))
    assert fastasource.uniprot_release() == {"release": "2026_03", "date": "2026-09-02",
                                             "date_text": "02-September-2026"}
    net(("uniprotkb/search", urllib.error.URLError("no route")))
    notes: list[str] = []
    assert fastasource.uniprot_release(notes=notes) is None
    assert "could not reach rest.uniprot.org" in notes[0]
    net(("uniprotkb/search", (b"", {})))
    notes = []
    assert fastasource.uniprot_release(notes=notes) is None and "X-UniProt-Release" in notes[0]


@pytest.mark.parametrize("text, iso", [("02-September-2026", "2026-09-02"), ("1-january-2025", "2025-01-01"),
                                       ("2026-09-02", "2026-09-02"), ("Sept 2", ""), ("", "")])
def test_uniprot_dates_are_read_without_the_locale(text, iso):
    assert fastasource._uniprot_date(text) == iso


REL = {"release": "2026_03", "date": "2026-09-02"}


@pytest.mark.parametrize("made, release, words", [
    ("2026-01-30-decoys-contam-UP000005640_9606.fasta.fas", REL,
     "made 2026-01-30 (8 months ago); UniProt is at 2026_03 (2026-09-02), released 7 months after it: at least one "
     "release behind"),
    ("2026-09-20-decoys-contam-UP000005640_9606.fasta.fas", REL, "released before it: up to date"),
    (date(2026, 9, 2), REL, "up to date"),
    ("2026-01-30-decoys-contam-x.fas", None, "UniProt's current release is unknown"),
    ("human.fasta", REL, "has no date"),
    ("C:/Fragpipe_Auto/fasta/2025-10-14-decoys-y.fas", REL, "(12 months ago)"),
])
def test_staleness_in_words(made, release, words):
    assert words in fastasource.staleness(made, release, today=date(2026, 10, 14))


# ---------------------------------------------------------------- prepare --


def test_the_name_is_philosophers_and_the_labs():
    assert fastasource.fragpipe_fasta_name("UP000005640_9606.fasta", day=date(2026, 1, 30)) == \
        "2026-01-30-decoys-contam-UP000005640_9606.fasta.fas"  # the PC's file (PROTEOMICS_PC.md)
    assert fastasource.fragpipe_fasta_name("UP000005640_9606.fasta", day=date(2026, 1, 30), contam=False,
                                           tags=["reviewed", "isoforms"]) == \
        "2026-01-30-decoys-reviewed-isoforms-UP000005640_9606.fasta.fas"


@pytest.mark.parametrize("seq, decoy", [
    (b"MABCDE", b"MEDCBA"),     # a first M stays
    (b"ABCDE", b"EDCBA"),
    (b"MABCD", b"MDBCA"),       # Philosopher's loop stops at len/2: odd length with M leaves the middle pair
    (b"M", b"M"), (b"", b""), (b"KR", b"RK"),
])
def test_decoys_are_reversed_as_philosopher_reverses_them(seq, decoy):
    assert fastasource.reverse_like_philosopher(seq) == decoy


def test_target_decoy_layout_and_contaminant_dedup():
    targets = [(b"sp|P1|A_HUMAN a", b"MAAK"), (b"sp|P2|B_HUMAN b", b"KLM"), (b"sp|P1|A_HUMAN a", b"MAAKR")]
    contam = [(b"sp|P2|B_HUMAN dup of a target", b"XX"), (b"sp|P9|C_BOVIN c", b"MCC"), (b"sp|P9|C_BOVIN again", b"Z")]
    out = fastasource.target_decoy(targets, contam)
    assert out == [(b"contam_sp|P9|C_BOVIN c", b"MCC"), (b"rev_contam_sp|P9|C_BOVIN c", b"MCC"),
                   (b"rev_sp|P1|A_HUMAN a", b"MRAKA"), (b"rev_sp|P2|B_HUMAN b", b"MLK"),  # odd + M: quirk
                   (b"sp|P1|A_HUMAN a", b"MAAKR"), (b"sp|P2|B_HUMAN b", b"KLM")]


def _src(tmp_path: Path, data: bytes = HUMAN, name: str = "UP000005640_9606.fasta") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _passes_fragpipe(path: Path, tag: str = "rev_") -> bool:
    return fragpipe.decoy_problem({"percolator.run-percolator": "true", "database.decoy-tag": tag}, path) == ""


def test_prepare_in_python_with_the_contaminants_of_the_labs_old_fasta(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    src = _src(tmp_path, HUMAN.replace(b"\n", b"\r\n").replace(b"OX=9606 GN=HBB", b"OX=9606\tGN=HBB"))
    old = tmp_path / "2026-01-30-decoys-contam-UP000005640_9606.fasta.fas"
    old_entries = fastasource.target_decoy([], fastasource._parse_fasta_bytes(CRAP))  # contam_ + rev_contam_ only
    old.write_bytes(b"".join(b">" + h + b"\n" + s + b"\n" for h, s in old_entries))
    before = src.read_bytes()
    res = fastasource.prepare_for_fragpipe(src, contaminant_candidates=[old, src], date=date(2026, 10, 14),
                                           tags=["reviewed"])
    assert res.ok, res.error
    assert res.path.name == "2026-10-14-decoys-reviewed-contam-UP000005640_9606.fasta.fas"
    assert (res.method, res.entries, res.decoys, res.contaminants, res.targets) == ("python", 10, 5, 2, 5)
    assert "contam_ entries of 2026-01-30" in res.contaminant_source
    assert _passes_fragpipe(res.path)
    assert src.read_bytes() == before, "the source is never changed"
    text = res.path.read_text()
    assert ">contam_sp|P02769|ALBU_BOVIN" in text and ">rev_contam_sp|P00761|TRYP_PIG" in text
    assert "contam_sp|P02768" not in text, "human albumin is a target, not a contaminant"
    assert "\r" not in text and "\t" not in text and "OX=9606 GN=HBB" in text
    again = fastasource.prepare_for_fragpipe(src, contaminant_candidates=[old], date=date(2026, 10, 14),
                                             tags=["reviewed"])
    assert again.path.name == "2026-10-14-decoys-reviewed-contam-UP000005640_9606-2.fasta.fas"
    assert res.path.read_text() == text, "the first file is untouched"
    assert "already exists" in " ".join(again.notes)


def test_prepare_downloads_philosophers_list_or_crap_or_says_there_is_none(tmp_path, net):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("crap-gpmdb.fas", CRAP)
        z.writestr("proteomes.csv", "Proteome Id,Organism,Organism Id\n")
    src = _src(tmp_path)
    net(("philosopher/master/lib/dat/cRAPS.zip", (buf.getvalue(), {})))
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "a")
    assert res.ok and res.contaminants == 2 and "Philosopher's contaminant list" in res.contaminant_source
    net(("cRAPS.zip", 404), ("thegpm.org", (CRAP, {})))
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "b")
    assert res.ok and res.contaminants == 2 and "cRAP" in res.contaminant_source
    assert any("HTTP 404" in n for n in res.notes)
    net(("cRAPS.zip", urllib.error.URLError("offline")), ("thegpm.org", urllib.error.URLError("offline")))
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "c", date=date(2026, 10, 14))
    assert res.ok and res.contaminants == 0 and res.contaminant_source == ""
    assert res.path.name == "2026-10-14-decoys-UP000005640_9606.fasta.fas", "the name doesn't claim contaminants"
    assert any("no contaminant list" in n for n in res.notes) and _passes_fragpipe(res.path)


def test_prepare_with_your_own_contaminants_or_none(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    src = _src(tmp_path)
    mine = tmp_path / "my_contaminants.fasta"
    mine.write_bytes(CRAP)
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "out", contaminants=mine)
    assert res.ok and res.contaminants == 2 and res.contaminant_source == "your file my_contaminants.fasta"
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "out", contaminants=None, date=date(2026, 1, 2))
    assert res.ok and res.contaminants == 0 and res.notes == []
    assert res.path.name == "2026-01-02-decoys-UP000005640_9606.fasta.fas" and res.decoy_share == 0.5
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "out", contaminants=None, decoy_tag="DECOY_")
    assert res.ok and _passes_fragpipe(res.path, "DECOY_")


@pytest.mark.parametrize("data, kw, words", [
    (HUMAN + b">rev_sp|P1|X\nKAAM\n", {}, "already has decoys"),
    (b"just text\n", {}, "no FASTA entries"),
    (HUMAN, {"contaminants": "C:/nowhere/crap.fasta"}, "contaminant file"),
    (HUMAN, {"decoy_tag": "rev "}, "can't be used"),
])
def test_prepare_refuses_what_fragpipe_could_not_use(tmp_path, monkeypatch, data, kw, words):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    src = _src(tmp_path, data)
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "out", **kw)
    assert not res.ok and words in res.error
    assert not (tmp_path / "out").exists() or _files(tmp_path / "out") == []
    assert "not found" in fastasource.prepare_for_fragpipe(tmp_path / "missing.fasta").error


FAKE_PHILOSOPHER = r'''
import datetime, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["FAKE_PHIL_LOG"], "a") as fh:
    fh.write(os.getcwd() + " :: " + " ".join(args) + "\n")
if args[0] == "workspace" and "--init" in args:
    os.makedirs(".meta", exist_ok=True)
if args[0] == "database":
    if os.environ.get("FAKE_PHIL_FAIL"):
        print("ERRO[0000] something went wrong")
        sys.exit(3)
    if not os.path.isdir(".meta"):
        print("workspace not initialised")
        sys.exit(1)
    src = pathlib.Path(args[args.index("--custom") + 1])
    db, cur = {}, None
    for line in src.read_text().splitlines():
        if line.startswith(">"):
            cur = line[1:]
            db[cur] = ""
        elif cur:
            db[cur] += line.strip()
    contam = "--contam" in args
    if contam:
        db["contam_sp|P00761|TRYP_PIG Trypsin"] = "FPTDDDDK"
    out = dict(db)
    out.update({"rev_" + h: s[::-1] for h, s in db.items()})
    name = f"{datetime.date.today():%Y-%m-%d}-decoys{'-contam' if contam else ''}-{src.name}.fas"
    with open(name, "w", newline="\n") as fh:
        for h in sorted(out):
            fh.write(f">{h}\n{out[h]}\n")
'''


@pytest.fixture
def philosopher(tmp_path, monkeypatch):
    exe = tmp_path / "tools" / "philosopher-fake.py"
    exe.parent.mkdir(parents=True)
    exe.write_text(FAKE_PHILOSOPHER)
    log = tmp_path / "phil.log"
    monkeypatch.setenv("FAKE_PHIL_LOG", str(log))
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    return exe, log


def test_prepare_runs_philosopher_as_fragpipes_database_tab_does(tmp_path, philosopher):
    exe, log = philosopher
    src = _src(tmp_path)
    dest = tmp_path / "fasta"
    res = fastasource.prepare_for_fragpipe(src, dest, philosopher=exe, date=date(2026, 1, 30))
    assert res.ok, (res.error, res.notes)
    assert res.method == "philosopher" and "built-in list" in res.contaminant_source
    assert res.path == dest / "2026-01-30-decoys-contam-UP000005640_9606.fasta.fas"
    assert (res.entries, res.decoys, res.contaminants) == (8, 4, 1) and _passes_fragpipe(res.path)
    steps = [line.split(" :: ")[1] for line in log.read_text().splitlines()]
    assert steps[0] == "workspace --clean --nocheck"
    assert steps[1].startswith("workspace --init --nocheck --temp ")
    assert steps[2] == f"database --custom {src.resolve()} --contam --contamprefix"
    assert steps[3] == "workspace --clean --nocheck"
    workdir = Path(log.read_text().splitlines()[2].split(" :: ")[0])
    assert workdir.parent == dest and not workdir.exists(), "Philosopher ran in its own folder, removed after"
    assert _files(dest) == [res.path.name]


def test_a_failing_philosopher_falls_back_to_python(tmp_path, philosopher, monkeypatch):
    exe, _log = philosopher
    monkeypatch.setenv("FAKE_PHIL_FAIL", "1")
    res = fastasource.prepare_for_fragpipe(_src(tmp_path), tmp_path / "out", philosopher=exe, contaminants=None)
    assert res.ok and res.method == "python"
    assert any("exit code 3" in n and "something went wrong" in n for n in res.notes)
    assert _files(tmp_path / "out") == [res.path.name]
    res = fastasource.prepare_for_fragpipe(_src(tmp_path), tmp_path / "out2", philosopher=tmp_path / "nope.exe",
                                           contaminants=None)
    assert res.ok and res.method == "python" and "Philosopher not found" in res.notes[0]


def test_find_philosopher_in_a_fragpipe_install(tmp_path):
    ext = ".exe" if os.name == "nt" else ""
    root = tmp_path / "FragPipe-24.0"
    tools = root / "tools" / "Philosopher"
    tools.mkdir(parents=True)
    for v in ("5.1.2", "5.1.10", "5.0.0"):
        (tools / f"philosopher-v{v}{ext}").write_bytes(b"")
    (root / "bin").mkdir()
    (root / "bin" / "fragpipe.bat").write_text("@echo off\n")
    assert fastasource.find_philosopher([root]).name == f"philosopher-v5.1.10{ext}"
    assert fastasource.find_philosopher([root / "bin" / "fragpipe.bat"]).name == f"philosopher-v5.1.10{ext}"
    assert fastasource.find_philosopher([tmp_path / "elsewhere"]) is None


# ------------------------------------------------------------ stock workflows --


def _listing(*names):
    return json.dumps([{"name": n, "type": "file"} for n in names] + [{"name": "sub", "type": "dir"}]).encode()


@pytest.mark.parametrize("given, tag", [("24.0", "24.0"), ("v23.1", "23.1"), ("FragPipe-22.0", "22.0"), ("24", "24.0")])
def test_fragpipe_tags(given, tag):
    assert fastasource.fragpipe_tag(given) == tag


def test_stock_workflows_from_the_new_and_the_old_folder(net):
    fake = net(("contents/workflows?ref=24.0", (_listing("TMT10-MS3.workflow", "LFQ-MBR.workflow", "x.txt"), {})))
    res = fastasource.stock_workflows("24.0")
    assert (res.tag, res.folder, res.names, res.error) == ("24.0", "workflows", ["LFQ-MBR", "TMT10-MS3"], "")
    assert all(t for _u, t in fake.calls)
    net(("contents/workflows?ref=22.0", 404),
        ("contents/MSFragger-GUI/workflows?ref=22.0", (_listing("isoDTB-ABPP.workflow"), {})))
    res = fastasource.stock_workflows("22.0")
    assert (res.folder, res.names) == ("MSFragger-GUI/workflows", ["isoDTB-ABPP"])
    net(("contents/", 404))
    assert "FragPipe 99.0 not found" in fastasource.stock_workflows("99").error
    net(("contents/", 403))
    assert "try again in an hour" in fastasource.stock_workflows("24.0").error
    assert "not a FragPipe version" in fastasource.stock_workflows("latest").error


WF = b"# FragPipe 24.0\nworkflow.description=TMT\ndatabase.db-path=\ntmtintegrator.channel_num=TMT-10\n"


def test_fetch_stock_workflow_never_overwrites_and_checks_what_came(tmp_path, net):
    fake = net(("/24.0/workflows/TMT10-MS3.workflow", (WF, {})))
    res = fastasource.fetch_stock_workflow("24.0", "TMT10-MS3.workflow", tmp_path)
    assert res.ok and res.path == tmp_path / "TMT10-MS3.workflow" and res.path.read_bytes() == WF
    assert res.url == "https://raw.githubusercontent.com/Nesvilab/FragPipe/24.0/workflows/TMT10-MS3.workflow"
    again = fastasource.fetch_stock_workflow("24.0", "TMT10-MS3", tmp_path)
    assert again.path.name == "TMT10-MS3-2.workflow" and res.path.read_bytes() == WF
    assert all(t for _u, t in fake.calls)
    net(("/22.0/workflows/", 404), ("/22.0/MSFragger-GUI/workflows/isoDTB-ABPP.workflow", (WF, {})))
    assert fastasource.fetch_stock_workflow("22.0", "isoDTB-ABPP", tmp_path).url.endswith(
        "/22.0/MSFragger-GUI/workflows/isoDTB-ABPP.workflow")
    net(("raw.githubusercontent", 404))
    assert "has no stock workflow" in fastasource.fetch_stock_workflow("24.0", "Nope", tmp_path).error
    net(("raw.githubusercontent", (b"<html>login</html>", {})))
    assert "did not return a workflow" in fastasource.fetch_stock_workflow("24.0", "TMT10", tmp_path).error
    fake = net()
    assert "not a workflow name" in fastasource.fetch_stock_workflow("24.0", "../../x", tmp_path).error
    assert fake.calls == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["TMT10-MS3-2.workflow", "TMT10-MS3.workflow",
                                                         "isoDTB-ABPP.workflow"]


# --------------------------------------------------------------------- CLI --


def test_cli_search_download_release_and_stock(tmp_path, net, capsys):
    nocfg = ["--config", str(tmp_path / "no-config.yaml")]
    net(("proteomes/search", (PROTEOMES_TSV, {})))
    assert cli.main(nocfg + ["fasta", "search", "mouse"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("UP000000589  Mus musculus (Mouse)") and "fasta download UP000000589" in out

    assert cli.main(nocfg + ["fasta", "download", "UP000005640"]) == 2, "no config and no --dest"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("crap-gpmdb.fas", CRAP)
    net(*_uniprot(3), ("cRAPS.zip", (buf.getvalue(), {})))
    dest = tmp_path / "fasta"
    assert cli.main(nocfg + ["fasta", "download", "UP000005640", "--dest", str(dest), "--no-philosopher"]) == 0
    out = capsys.readouterr().out
    made = [p.name for p in dest.iterdir()]
    final = [n for n in made if n.endswith(".fas")]
    assert sorted(made) == sorted(["UP000005640_9606.fasta", final[0]])
    assert final[0].endswith("-decoys-reviewed-contam-UP000005640_9606.fasta.fas")
    assert "UniProt 2026_03 (2026-09-02)" in out and "2 contaminants" in out and "50.0 %" in out

    net(("uniprotkb/search", (b"", UNIPROT_HEADERS)))
    assert cli.main(nocfg + ["fasta", "release", str(dest / final[0])]) == 0
    assert "UniProt: 2026_03 (2026-09-02)" in capsys.readouterr().out

    net(("contents/workflows?ref=24.0", (_listing("TMT10-MS3.workflow"), {})), ("/24.0/workflows/", (WF, {})))
    assert cli.main(nocfg + ["fasta", "stock-workflows", "24.0"]) == 0
    assert "TMT10-MS3" in capsys.readouterr().out
    assert cli.main(nocfg + ["fasta", "stock-workflows", "24.0", "--get", "TMT10-MS3"]) == 2
    assert cli.main(nocfg + ["fasta", "stock-workflows", "24.0", "--get", "TMT10-MS3", "--dest", str(tmp_path / "w"),
                             "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["path"].endswith("TMT10-MS3.workflow")


def test_cli_prepare_and_release_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    nocfg = ["--config", str(tmp_path / "no-config.yaml")]
    src = _src(tmp_path)
    assert cli.main(nocfg + ["fasta", "prepare", str(src), "--no-contam", "--no-philosopher"]) == 0
    out = capsys.readouterr().out
    assert "-decoys-UP000005640_9606.fasta.fas: 6 entries = 3 proteins + 0 contaminants" in out
    assert cli.main(nocfg + ["fasta", "prepare", str(src), "--no-contam", "--no-philosopher", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decoy_share"] == 0.5
    assert cli.main(nocfg + ["fasta", "release", "2026-01-30-decoys-contam-x.fas"]) == 1
    out = capsys.readouterr().out
    assert "UniProt: unknown" in out and "made 2026-01-30" in out


def test_no_network_at_import(monkeypatch):
    """Importing the module makes no request: it only binds urlopen."""
    import importlib

    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append(a))
    try:
        importlib.reload(fastasource)
        assert calls == []
    finally:
        monkeypatch.undo()
        importlib.reload(fastasource)
    assert fastasource.urlopen is __import__("urllib.request").request.urlopen
    assert sys.modules["ionomos.fastasource"] is fastasource


# --- fix-fasta (round 4)

BOM = b"\xef\xbb\xbf"


def _ref_headers(data: bytes) -> list[bytes]:
    """The headers by the plainest reading there is (split on \\n): what `fasta_headers` must give in any chunks."""
    data = data[len(BOM):] if data.startswith(BOM) else data
    return [ln[1:].rstrip(b"\r") for ln in data.split(b"\n") if ln.startswith(b">")]


def _ref_entries(data: bytes) -> list[tuple[bytes, bytes]]:
    """The reader before round 4 (whole lines), with the BOM skipped: what `_read_entries` must give in any pieces."""
    data = data[len(BOM):] if data.startswith(BOM) else data
    out: list[tuple[bytes, bytearray]] = []
    for line in io.BytesIO(data):
        line = line.rstrip(b"\r\n")
        if line.startswith(b">"):
            out.append((line[1:].replace(b"\t", b" "), bytearray()))
        elif out:
            out[-1][1].extend(line.strip())
    return [(h, bytes(s)) for h, s in out]


def _random_fastas(n: int = 250):
    import random

    rnd = random.Random(152156)
    atoms = [b">", b"\n", b"\r\n", b"\r", b"MKT", b"A", b" ", b"\t", b"sp|P1|X", b"contam_", b"\n>", b"\n\n"]
    cases = [b"", BOM, BOM + b">", b">", b"\n", BOM + b"\n>a\nMK", b">a", b">a\r\n", HUMAN, BOM + HUMAN,
             HUMAN.replace(b"\n", b"\r\n"), HUMAN.rstrip(b"\n"), b"junk\n" + HUMAN, b"MK>notaheader\n>h\nMK\n"]
    for _ in range(n):
        body = b"".join(rnd.choice(atoms) for _ in range(rnd.randint(0, 40)))
        cases.append((BOM if rnd.random() < 0.3 else b"") + body)
    return cases


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 8, 64, 1 << 16])
def test_fasta_headers_match_a_line_by_line_reading_in_any_chunks(chunk):
    for data in _random_fastas():
        assert list(fastasource.fasta_headers(io.BytesIO(data), chunk=chunk)) == _ref_headers(data), (chunk, data)


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 8, 64])
def test_read_entries_match_the_whole_line_reader_in_any_pieces(chunk, monkeypatch):
    monkeypatch.setattr(fastasource, "CHUNK", chunk)
    for data in _random_fastas():
        assert fastasource._read_entries(io.BytesIO(data)) == _ref_entries(data), (chunk, data)
    keep = fastasource._read_entries(io.BytesIO(b">contam_a x\nMK\nTT\n>b\nQQ\n>contam_c\nW"),
                                     keep=lambda start: start.startswith(b"contam_"))
    assert keep == [(b"contam_a x", b"MKTT"), (b"contam_c", b"W")]


def test_a_bom_fasta_keeps_its_first_protein_in_python(tmp_path, monkeypatch):
    """#152: the Python way read b'\\xef\\xbb\\xbf>sp|P68871…' as text before the first header and dropped it."""
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = fastasource.prepare_for_fragpipe(_src(tmp_path / "a"), contaminants=None, date=date(2026, 10, 15))
    src = _src(tmp_path / "b", BOM + HUMAN)
    res = fastasource.prepare_for_fragpipe(src, contaminants=None, date=date(2026, 10, 15))
    assert res.ok and res.method == "python"
    assert (res.entries, res.targets, res.decoys) == (6, 3, 3)
    assert res.path.read_bytes() == plain.path.read_bytes(), "the same file as from the file saved without a BOM"
    assert b"P68871" in res.path.read_bytes()
    assert any("byte-order mark" in n for n in res.notes)
    assert src.read_bytes().startswith(BOM), "the source is never changed"
    assert fastasource.read_fasta(src)[0][0].startswith(b"sp|P68871")
    assert fastasource._scan(src, "rev_")["entries"] == 3
    mine = _src(tmp_path, BOM + CRAP, name="my_contaminants.fasta")  # a contaminant file of your own, with a BOM
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "c", contaminants=mine)
    assert res.ok and res.contaminants == 2, "ALBU_BOVIN (the first entry) is kept"


def test_a_bom_fasta_reaches_philosopher_without_the_bom(tmp_path, philosopher):
    """#152: the bundled Philosopher crashes on the BOM. It is given a copy without it, so its result is the one of
    the file saved without a BOM (the stand-in, like a plain reader, would otherwise lose the first protein)."""
    exe, log = philosopher
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = fastasource.prepare_for_fragpipe(_src(tmp_path / "a"), tmp_path / "out-a", philosopher=exe,
                                             date=date(2026, 10, 15))
    src = _src(tmp_path / "b", BOM + HUMAN)
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "out-b", philosopher=exe, date=date(2026, 10, 15))
    assert res.ok and res.method == "philosopher", (res.error, res.notes)
    assert res.path.name == plain.path.name and res.path.read_bytes() == plain.path.read_bytes()
    assert (res.entries, res.contaminants) == (8, 1)
    custom = [ln for ln in log.read_text().splitlines() if "database --custom" in ln][-1]
    assert str(src.resolve()) not in custom and custom.split("--custom ")[1].startswith(str(tmp_path / "out-b"))
    assert _files(tmp_path / "out-b") == [res.path.name], "the copy went with Philosopher's folder"
    assert src.read_bytes() == BOM + HUMAN


@pytest.mark.parametrize("data, line, words", [
    (b"x>sp|P68871|HBB_HUMAN Hemoglobin\nMVHL\n>sp|P2|B\nMK\n", 1, "x>sp|P68871"),
    (b"\r\n\r\nMVHLTPEEK\n>sp|P2|B\nMK\n", 3, "MVHLTPEEK"),
    (BOM + b" >sp|P1|A\nMK\n>sp|P2|B\nMK\n", 1, ">sp|P1|A"),
])
def test_text_before_the_first_header_is_refused_not_dropped(tmp_path, monkeypatch, data, line, words):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    src = _src(tmp_path, data)
    assert fastasource.text_before_first_header(src)[0] == line
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "out", contaminants=None)
    assert not res.ok and f"line {line}" in res.error and words in res.error and "first protein" in res.error
    assert not (tmp_path / "out").exists() or _files(tmp_path / "out") == []
    for fine in (HUMAN, b"\n\n" + HUMAN, BOM + HUMAN, b""):
        assert fastasource.text_before_first_header(_src(tmp_path, fine, name="ok.fasta")) == (0, "")


def test_a_utf16_fasta_is_refused_with_how_to_save_it(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    src = _src(tmp_path, HUMAN.decode().encode("utf-16"))
    res = fastasource.prepare_for_fragpipe(src, tmp_path / "out")
    assert not res.ok and "UTF-16" in res.error and "UTF-8" in res.error


def test_bom_in_fragpipe_fasta_info(tmp_path):
    p = _src(tmp_path, BOM + b">rev_a\nKM\n>b\nMK\n", name="x.fas")
    assert fragpipe.fasta_info(p) == {"entries": 2, "decoys": 1, "gb": p.stat().st_size / 1e9}
    assert fragpipe.decoy_problem({"percolator.run-percolator": "true"}, p) == ""


def test_a_go_crash_report_is_said_in_words(tmp_path):
    log = tmp_path / "philosopher.log"
    log.write_text("INFO[10:00:00] Executing Database\n"
                   "panic: runtime error: index out of range [0] with length 0\n\n"
                   "goroutine 1 [running]:\n"
                   "github.com/Nesvilab/philosopher/lib/dat.ProcessDB(...)\n"
                   "\tE:/Projects/philosopher/phi-5.1.1_RC/philosopher/lib/dat/dat.go:120 +0x85\n"
                   "github.com/spf13/cobra.(*Command).Execute(...)\n"
                   "\tE:/Projects/philosopher/vendor/github.com/spf13/cobra/command.go:968\n"
                   "main.main()\n\t.../main.go:23 +0x85\n")
    text = fastasource._last_lines(log)
    assert ".go:" not in text and "cobra" not in text and "main.main" not in text
    assert "panic: runtime error" in text and "Philosopher crashed" in text
    log.write_text("ERRO[0000] something went wrong\n")
    assert fastasource._last_lines(log) == "ERRO[0000] something went wrong"


def _zero_file(path: Path, size: int, head: bytes = b"") -> Path:
    """`size` bytes, no line break: `head`, then zeros (sparse where the file system allows; written in no time)."""
    with open(path, "wb") as fh:
        fh.write(head)
        fh.truncate(size)
    return path


def test_a_200_mb_file_without_line_breaks_is_read_in_linear_time(tmp_path, monkeypatch):
    """#156 item 5: a file with no line break used to be kept whole between chunks (quadratic; 60 s for 1 GB)."""
    import time
    import tracemalloc

    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    zeros = _zero_file(tmp_path / "zeros.fasta", 200 << 20)
    one_header = _zero_file(tmp_path / "header.fasta", 200 << 20, b">sp|P1|A ")
    started = time.monotonic()
    tracemalloc.start()
    try:
        assert fastasource._scan(zeros, "rev_") == {"entries": 0, "decoys": 0, "contaminants": 0}
        assert fragpipe.fasta_info(zeros)["entries"] == 0
        assert fastasource.contaminants_from_fasta(zeros) == []
        assert fastasource.text_before_first_header(zeros)[0] == 1
        res = fastasource.prepare_for_fragpipe(zeros, tmp_path / "out")
        with open(one_header, "rb") as fh:
            heads = list(fastasource.fasta_headers(fh))
        assert fastasource.contaminants_from_fasta(one_header) == []
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert not res.ok and "no FASTA entries" in res.error
    assert len(heads) == 1 and len(heads[0]) == fastasource.HEADER_MAX and heads[0].startswith(b"sp|P1|A ")
    assert peak < 16 * fastasource.CHUNK, f"bounded memory: a few chunks, not the file ({peak / 1e6:.0f} MB)"
    assert time.monotonic() - started < 15, "linear: a few seconds for 200 MB, not minutes"
