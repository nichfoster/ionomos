"""D74: the anonymised "Copy diagnostics", DIA-NN's main report and the peptide / ion tables on request, and the
name check of `ionomos bundle inspect`.

As in test_bundle.py, the leak assertions use a plain search of their own (_survivors), not bundle.py's."""
import getpass
import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from ionomos import bundle, cli, names, service, testbed
from ionomos.bundle import Options
from ionomos.config import load
from ionomos.downstream import engines
from ionomos.ledger import Ledger
from tests.test_bundle import FOLDER, RAWS, SECRET_WORDS, _drop, _job, _members, _run, _survivors

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


@pytest.fixture
def bed(tmp_path, monkeypatch):
    """test_bundle.py's lab: the testbed with its users, a log folder and an inbox."""
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": Ledger(cfg.database),
            "out": tmp_path / "out", "tmp": tmp_path}


def _plain_hits(text: str, words) -> list[str]:
    """The words still in a text: the same plain search as _survivors, for a text that is not a zip."""
    out = []
    for w in words:
        rx = re.compile((r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])") if len(w) < 6 else re.escape(w),
                        re.IGNORECASE)
        if rx.search(text):
            out.append(w)
    return out


def _failed_job(bed, monkeypatch) -> Path:
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "oom")
    dest = _drop(bed)
    _run(bed)
    assert bed["ledger"].get(1).status == "failed"
    (bed["cfg"].log_dir / "ionomos.log").write_text(
        f"2026-03-01 12:00:00 ERROR job 1 {FOLDER} failed for Isaac (IJ) on 10.0.5.9; Zanubrutinib_2.raw\n",
        encoding="utf-8")
    return dest


# ------------------------------------------------------------ diagnostics ----


def test_copy_diagnostics_is_anonymised_as_a_bundle_is(bed, monkeypatch, capsys):
    _failed_job(bed, monkeypatch)
    me = [w for w in {getpass.getuser()} if len(w) >= 3]
    text, where = service.save_diagnostics(bed["cfg_path"], anonymise=True)
    assert "=== check" in text and "=== config.yaml" in text and "job 1 exp001" in text
    assert _plain_hits(text, [*SECRET_WORDS, *RAWS[3:], "10.0.5.9", *me]) == []
    # saved in the lab with its key next to it; the key reads it back
    assert where.parent == bed["cfg"].log_dir and where.name.endswith("-anonymised.txt")
    assert where.read_text(encoding="utf-8") == text
    key = bundle.key_path_for(where)
    assert key.is_file() and key.name.endswith(names.BUNDLE_KEY_SUFFIX)
    back = bundle.translate(key, text)
    assert "Isaac" in back and FOLDER in back and "Zanubrutinib_2.raw" in back
    # the same pseudonyms as a `diagnose` bundle made from the same lab (one Anonymiser, one way of learning)
    res = bundle.create(bed["cfg_path"], opts=Options(), dest_dir=bed["out"])
    kb, kd = (json.loads(p.read_text(encoding="utf-8")) for p in (res.key_path, key))
    for group in ("users", "experiments", "names", "addresses"):
        assert {p: o for p, o in kd[group].items() if o in kb[group].values()} == \
            {p: o for p, o in kb[group].items() if o in kd[group].values()}, group
    assert kd["users"]["user01"] == "Isaac" and kd["experiments"]["exp001"] == FOLDER
    # the real text is still there, and saved as before (no key)
    real, where2 = service.save_diagnostics(bed["cfg_path"])
    assert "Isaac" in real and FOLDER in real and "-anonymised" not in where2.name
    assert not bundle.key_path_for(where2).exists()
    # the terminal: --anonymise
    assert cli.main(["--config", str(bed["cfg_path"]), "diagnose", "--anonymise"]) == 0
    out = capsys.readouterr().out
    assert "=== check" in out and _plain_hits(out.split("(saved to")[0], [*SECRET_WORDS, *me]) == []
    assert "stays in the lab" in out
    msg = service.diagnostics_copied_message(where, True)
    assert "pseudonyms" in msg and str(key) in msg
    assert "real names" in service.diagnostics_copied_message(where2, False)


def test_the_leak_check_runs_on_the_diagnostics_text(bed, monkeypatch, capsys):
    _failed_job(bed, monkeypatch)
    real = bundle._Scrub.line

    def forgetful(self, s):  # a bug: the log tail goes out unscrubbed
        return s if "failed for Isaac" in s else real(self, s)

    monkeypatch.setattr(bundle._Scrub, "line", forgetful)
    before = sorted(bed["cfg"].log_dir.glob("diagnostics-*"))
    with pytest.raises(bundle.BundleLeak) as err:
        service.save_diagnostics(bed["cfg_path"], anonymise=True)
    assert any(o == "Isaac" for _w, o, _n in err.value.leaks)
    assert sorted(bed["cfg"].log_dir.glob("diagnostics-*")) == before     # nothing saved, no key
    assert cli.main(["--config", str(bed["cfg_path"]), "diagnose", "--anonymise"]) == 1
    said = capsys.readouterr()
    assert said.out == "" and "LEAK CHECK FAILED" in said.err
    # the check itself, on any text
    anon = bundle.learn(bundle.collect(bed["cfg_path"]))
    assert bundle.check_text(anon, "fine: user01 and exp001\n") == []
    assert [o for _w, o, _n in bundle.check_text(anon, "run by Isaac\n", "pasted")] == ["Isaac"]


# --------------------------------------------- tables included on request ----

PR_HEAD = ["Protein.Group", "Protein.Ids", "Protein.Names", "Genes", "First.Protein.Description", "Proteotypic",
           "Stripped.Sequence", "Modified.Sequence", "Precursor.Charge", "Precursor.Id"]
REPORT_HEAD = ["File.Name", "Run", "Protein.Group", "Protein.Ids", "Protein.Names", "Genes", "PG.Quantity",
               "PG.MaxLFQ", "Modified.Sequence", "Stripped.Sequence", "Precursor.Id", "Precursor.Charge", "Q.Value",
               "PG.Q.Value", "Precursor.Quantity"]
PROTS = [("P10275", "ANDR_HUMAN", "AR"), ("P02768", "ALBU_HUMAN", "ALB"), ("Q9Y6K9", "NEMO_HUMAN", "IKBKG")]


def _report_rows(dest: Path) -> list[list]:
    rows = []
    for k, run in enumerate(RAWS):
        for j, (pid, name, gene) in enumerate(PROTS):
            for charge in (2, 3):
                pep = "PEPTIDEK" if j == 0 else "SAMPLER" if j == 1 else "LATTEK"
                rows.append([str(dest / "raw" / f"{run}.raw"), run, pid, pid, name, gene, 1000.0 * (k + j + 1),
                             1100.5 * (k + j + 1), pep, pep, f"{pep}{charge}", charge, 0.001 * charge,
                             0.002 if j < 2 else 0.05, 123.25 * (k + 1)])
    return rows


def _engine_extras(dest: Path) -> dict[str, Path]:
    """DIA-NN's main report (1.x text and 2.x Parquet), its precursor matrix and library, FragPipe's peptide and
    ion tables, beside what the fake FragPipe wrote. Real column names (DIA-NN's and FragPipe's documentation)."""
    dq = dest / "fragpipe" / "dia-quant-output"
    rows = _report_rows(dest)
    (dq / "report.tsv").write_text("\t".join(REPORT_HEAD) + "\n" + "".join(
        "\t".join(str(c) for c in r) + "\n" for r in rows), encoding="utf-8")
    table = pa.table({h: [r[i] for r in rows] for i, h in enumerate(REPORT_HEAD)})
    pq.write_table(table, dq / "report.parquet", row_group_size=7)
    pq.write_table(table, dq / "report-lib.parquet")           # a spectral library: never
    raws = [str(dest / "raw" / f"{r}.raw") for r in RAWS]
    (dq / "report.pr_matrix.tsv").write_text("\t".join(PR_HEAD + raws) + "\n" + "".join(
        "\t".join([pid, pid, name, gene, "desc", "1", "PEPTIDEK", "PEPTIDEK", "2", "PEPTIDEK2",
                   *[str(10.0 + i) for i in range(len(raws))]]) + "\n" for pid, name, gene in PROTS), encoding="utf-8")
    exp = dest / "fragpipe"  # one experiment: FragPipe writes peptide.tsv / ion.tsv at the top of its folder
    (exp / "peptide.tsv").write_text(
        "Peptide\tPrev AA\tNext AA\tPeptide Length\tCharges\tProbability\tSpectral Count\tIntensity\t"
        "Assigned Modifications\tProtein\tProtein ID\tEntry Name\tGene\tProtein Description\tMapped Genes\t"
        "Mapped Proteins\nPEPTIDEK\tK\tL\t8\t2\t0.99\t3\t12345.6\t\tsp|P10275|ANDR_HUMAN\tP10275\tANDR_HUMAN\tAR\t"
        "Androgen receptor\t\t\n", encoding="utf-8")
    (exp / "ion.tsv").write_text(
        "Peptide Sequence\tModified Sequence\tPrev AA\tNext AA\tPeptide Length\tM/Z\tCharge\tObserved Mass\t"
        "Probability\tExpectation\tSpectral Count\tIntensity\tAssigned Modifications\tObserved Modifications\t"
        "Protein\tProtein ID\tEntry Name\tGene\tProtein Description\tMapped Genes\tMapped Proteins\n"
        "PEPTIDEK\tPEPTIDEK\tK\tL\t8\t464.7\t2\t927.4\t0.99\t0.001\t3\t12345.6\t\t\tsp|P10275|ANDR_HUMAN\tP10275\t"
        "ANDR_HUMAN\tAR\tAndrogen receptor\t\t\n", encoding="utf-8")
    return {"report": dq / "report.tsv", "parquet": dq / "report.parquet", "exp": exp}


def test_diann_report_and_peptide_tables_on_request(bed, capsys):
    dest = _job(bed)
    src = _engine_extras(dest)
    plain = bundle.create(bed["cfg_path"], ["1"], Options(level="validate"), dest_dir=bed["out"])
    assert not [n for n in _members(plain.path) if n.endswith(("/report.tsv", "pr_matrix.tsv", "/peptide.tsv", "/ion.tsv"))]

    # --include implies --level validate
    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "1", "--include", "diann-report,peptides",
                     "--out", str(bed["out"] / "x")]) == 0
    capsys.readouterr()
    z = next((bed["out"] / "x").glob("*-validate-*.zip"))
    key = bundle.key_path_for(z)
    m = _members(z)
    arc = "jobs/1-exp001/fragpipe"
    for inside in ("dia-quant-output/report.tsv", "dia-quant-output/report.parquet.tsv",
                   "dia-quant-output/report.pr_matrix.tsv"):
        assert f"{arc}/{inside}" in m, inside
    assert any(n.endswith("/peptide.tsv") for n in m) and any(n.endswith("/ion.tsv") for n in m)
    assert not [n for n in m if "lib" in n.lower() or n.endswith(".parquet")]
    # no name survives, in the cells of the long report either (Run, File.Name)
    assert _survivors(z, [*SECRET_WORDS, *RAWS[3:]]) == []
    manifest = json.loads(m["BUNDLE.json"])
    assert manifest["include"] == ["diann-report", "peptides"] and manifest["jobs"][0]["reproducible"]
    conv = next(f for f in manifest["files"] if f.get("converted_from"))
    assert conv["path"].endswith("report.parquet.tsv") and conv["converted_from"] == "report.parquet"
    assert "Also included on request" in m["README.txt"].decode() and "Parquet" in m["README.txt"].decode()
    # identifiers stay; the Parquet file is the text file's twin, value by value
    text = m[f"{arc}/dia-quant-output/report.tsv"].decode()
    conv_text = m[f"{arc}/dia-quant-output/report.parquet.tsv"].decode()
    assert "\tP10275\tP10275\tANDR_HUMAN\tAR\t" in text and conv_text.splitlines()[0] == "\t".join(REPORT_HEAD)
    assert conv_text.replace(".0\t", "\t") == text.replace(".0\t", "\t")

    # the developer's side: inspect and unpack say what was converted; the loader reads it as it read the original
    assert cli.main(["bundle", "inspect", str(z)]) == 0
    seen = capsys.readouterr().out
    assert "included on request: diann-report, peptides" in seen and "was report.parquet" in seen
    un = bed["tmp"] / "un"
    out = bundle.unpack(z, un)
    assert any("was report.parquet" in n for n in out["notes"])
    got = engines.load_diann_long(un / "exp001" / "fragpipe" / "dia-quant-output" / "report.parquet.tsv")
    want = engines.load_diann_long(src["parquet"])
    assert len(got.samples) == len(want.samples) == len(RAWS)
    assert sorted(map(sorted, ([v for v in r if v is not None] for r in got.values))) == \
        sorted(map(sorted, ([v for v in r if v is not None] for r in want.values)))
    assert [f.id for f in got.features] == [f.id for f in want.features]       # one protein group above 1% left out
    # ... and the key turns the unpacked report back into the lab's own file, line by line
    assert cli.main(["bundle", "translate", str(key), str(un / "exp001" / "fragpipe" / "dia-quant-output" /
                                                         "report.tsv")]) == 0
    assert capsys.readouterr().out == src["report"].read_text(encoding="utf-8")


def test_large_tables_on_request_are_row_sampled_and_dropped_first(bed):
    dest = _job(bed)
    _engine_extras(dest)
    res = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", include=("diann-report",), extra_mb=0.002),
                        dest_dir=bed["out"])
    m = _members(res.path)
    files = {f["path"]: f for f in res.manifest["files"]}
    rep = "jobs/1-exp001/fragpipe/dia-quant-output/report.tsv"
    lines = m[rep].decode().splitlines()
    rows = len(_report_rows(dest))
    assert lines[0].split("\t") == REPORT_HEAD and 1 < len(lines) - 1 < rows
    assert files[rep]["one_row_in"] > 1 and "--extra-mb" in {c["path"]: c for c in res.manifest["capped"]}[rep]["why"]
    par = files["jobs/1-exp001/fragpipe/dia-quant-output/report.parquet.tsv"]
    assert par["one_row_in"] > 1
    assert len(m[par["path"]].decode().splitlines()) - 1 == -(-rows // par["one_row_in"])
    assert res.manifest["jobs"][0]["reproducible"]           # the analysis does not read them: still repeatable
    assert not [n for n in m if n.endswith("peptide.tsv")]  # not asked for
    # over the bundle's size limit, a table on request goes before any table the analysis reads
    plan = bundle.collect(bed["cfg_path"], ["1"], Options(level="validate", include=tuple(bundle.EXTRAS)))
    need = sum(i.size for i in plan.items if not i.extra)
    small = bundle.collect(bed["cfg_path"], ["1"], Options(level="validate", include=tuple(bundle.EXTRAS),
                                                            max_mb=(need + 10) / 1e6))
    assert not [i for i in small.items if i.extra] and [s for s in small.skipped if "size limit" in s["why"]]
    assert small.jobs[0].incomplete == []


def test_parquet_without_pyarrow_and_the_options(bed, monkeypatch, capsys):
    dest = _job(bed)
    _engine_extras(dest)
    monkeypatch.setattr(bundle, "_parquet", lambda: None)
    plan = bundle.collect(bed["cfg_path"], ["1"], Options(level="validate", include=("diann-report",)))
    why = {Path(s["path"]).name: s["why"] for s in plan.skipped}
    assert "needs pyarrow" in why["report.parquet"] and any(i.arc.endswith("/report.tsv") for i in plan.items)
    with pytest.raises(bundle.BundleError, match="validate"):
        Options(include=("peptides",))
    with pytest.raises(bundle.BundleError, match="diann-report, peptides"):
        Options(level="validate", include=("everything",))
    assert bundle.Choice(extras=True).options().level == "validate"
    assert bundle.Choice(extras=True).options().include == ("diann-report", "peptides")
    assert bundle.Choice(validate=True).options().include == ()
    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "1", "--include", "peptides", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Level: validate" in out and "Also: peptide- and ion-level tables (" in out and "FragPipe peptide table" in out
    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "1", "--level", "diagnose", "--include",
                     "peptides"]) == 1
    assert "needs --level validate" in capsys.readouterr().err


# ------------------------------------------------------------- name check ----


def test_inspect_searches_every_file_for_the_labs_names(bed, capsys):
    dest = _job(bed)
    _engine_extras(dest)
    res = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", include=tuple(bundle.EXTRAS)),
                        dest_dir=bed["out"])
    d = bundle.self_check(res.path, bed["cfg_path"])
    assert len(d["sources"]) == 2 and d["hits"] == 0                         # the lab's names and the key's
    assert {f["path"] for f in d["files"]} == set(_members(res.path))
    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "inspect", str(res.path)]) == 0
    seen = capsys.readouterr().out
    assert "name check: no real name found in any file" in seen and len([ln for ln in seen.splitlines() if ln.startswith("    ") and ": no real name" in ln]) == len(d["files"])
    # a file with names in it (a bug, or a file someone added) is named, with what was found
    bad = bed["tmp"] / "bad" / res.path.name
    bad.parent.mkdir()
    with zipfile.ZipFile(res.path) as src, zipfile.ZipFile(bad, "w") as out:
        for n in src.namelist():
            out.writestr(n, src.read(n))
        out.writestr("logs/added.log", "Isaac ran Zanubrutinib_2 again\n")
    d = bundle.self_check(bad, bed["cfg_path"])
    hit = next(f for f in d["files"] if f["path"] == "logs/added.log")
    assert "Isaac" in [o for o, _n in hit["hits"]] and any("Zanubrutinib" in o for o, _n in hit["hits"])
    assert [f["path"] for f in d["files"] if f["hits"]] == ["logs/added.log"]
    assert "logs/added.log: FOUND" in "\n".join(bundle.self_check_lines(d))
    # the developer's side: no lab settings, no key -> said; with the key alone, its originals are searched for
    assert bundle.self_check(bad, None)["sources"] == []
    assert "nothing to check against" in bundle.inspect_text(bad)
    kd = bundle.self_check(bad, None, res.key_path)
    assert kd["sources"] and [f["path"] for f in kd["files"] if f["hits"]] == ["logs/added.log"]
    with pytest.raises(bundle.BundleError, match="no key file"):
        bundle.self_check(bad, None, bed["tmp"] / "nope.json")
    # a bundle that was not anonymised is full of names, and inspect says that is expected
    raw = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", anonymise=False), dest_dir=bed["out"])
    text = bundle.inspect_text(raw.path, bed["cfg_path"])
    assert "(not anonymised: the lab's real names are expected in it)" in text and "FOUND" in text


def test_a_moved_bundle_is_checked_with_the_key_given(bed, capsys):
    _job(bed)
    res = bundle.create(bed["cfg_path"], ["1"], dest_dir=bed["out"])
    moved = bed["tmp"] / "moved" / "b.zip"
    moved.parent.mkdir()
    shutil.copy(res.path, moved)
    assert cli.main(["--config", str(bed["tmp"] / "no-config.yaml"), "bundle", "inspect", str(moved),
                     "--key", str(res.key_path)]) == 0
    seen = capsys.readouterr().out
    assert "name check against the key file's" in seen and "no real name found in any file" in seen


@pytest.mark.parametrize("name, mains, include, what", [
    ("report.tsv", set(), ("diann-report",), "DIA-NN main report"),
    ("report.parquet", set(), ("diann-report",), "DIA-NN main report"),
    ("run42.parquet", {"run42.tsv", "run42.parquet"}, ("diann-report",), "DIA-NN main report"),  # --out run42.tsv
    ("calibration_report.tsv", set(), ("diann-report",), None),           # only DIA-NN's own names
    ("report-lib.parquet", set(), ("diann-report",), None),
    ("report-lib.tsv", set(), ("diann-report",), None),
    ("report.tsv.speclib", set(), ("diann-report",), None),
    ("library.tsv", {"library.tsv"}, ("diann-report",), None),
    ("report-first-pass.parquet", {"report-first-pass.parquet"}, ("diann-report",), None),
    ("report.tsv", set(), ("peptides",), None),                            # not asked for
    ("report.pr_matrix.tsv", set(), ("peptides",), "DIA-NN precursor matrix"),
    ("peptide.tsv", set(), ("peptides",), "FragPipe peptide table"),
    ("ion.tsv", set(), ("peptides",), "FragPipe ion table"),
    ("experiment_annotation.tsv", set(), ("peptides",), None),
    ("peptides.txt", set(), ("peptides",), "MaxQuant peptides table"),
    ("modificationSpecificPeptides.txt", set(), ("peptides",), "MaxQuant modification-specific peptides table"),
    ("peptide.tsv", set(), (), None),
])
def test_which_files_are_tables_on_request(name, mains, include, what):
    assert bundle._extra_what(name, mains, include) == what
