"""The troubleshooting / validation bundle (bundle.py, D63).

The end-to-end tests make real jobs with the testbed's fake FragPipe, bundle them, and read the zip back.
The leak tests do not trust the bundle's own check: they search every file of the zip, and every file
name in it, for the lab's names with their own plain search."""
import getpass
import hashlib
import json
import ntpath
import re
import types
import zipfile
from pathlib import Path

import pytest
import yaml

from ionomos import bundle, cli, names, postprocess, service, testbed
from ionomos.bundle import Anonymiser, Options
from ionomos.config import load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker

FOLDER = "20260301_Isaac_DIA_BRD4-JQ1-degrader"
RAWS = [f"{c}_{r}" for c in ("DMSO", "Zanubrutinib", "Abemaciclib") for r in (1, 2, 3)]
# what must never be in an anonymised bundle (the testbed's users, this drop's names)
SECRET_WORDS = ["Isaac", "EJQ", "Aman", "Chris", "Taylor_Elements", "Zanubrutinib", "Abemaciclib", "JQ1", "BRD4",
                FOLDER, "IJD"]


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": Ledger(cfg.database),
            "out": tmp_path / "out", "tmp": tmp_path}


def _drop(bed, folder: str = FOLDER, raws=RAWS, extra: dict | None = None) -> Path:
    d = bed["cfg"].inbox / folder
    (d / "raw").mkdir(parents=True)
    for r in raws:
        (d / "raw" / f"{r}.raw").write_bytes(b"\0" * 256)
    if extra:
        (d / "experiment.yaml").write_text(yaml.safe_dump(extra), encoding="utf-8")
    assert intake(d, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _run(bed) -> None:
    assert Worker(bed["cfg"], bed["ledger"]).run_once()


def _job(bed, **kw) -> Path:
    dest = _drop(bed, **kw)
    _run(bed)
    job = bed["ledger"].list()[-1]
    assert job.status == "done", job.reason
    return dest


def _members(z: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(z) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _survivors(z: Path, words) -> list[tuple[str, str]]:
    """(file, word) for every word still in a file of the zip or in a file name: a plain, case-insensitive
    search that knows nothing of bundle.py. Words under 6 characters must stand alone (EJQ, not 'rejqx')."""
    hits = []
    for name, data in _members(z).items():
        text = data.decode("utf-8", "replace")
        for w in words:
            rx = re.compile((r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])") if len(w) < 6 else re.escape(w),
                            re.IGNORECASE)
            if rx.search(text):
                hits.append((name, w))
            if rx.search(name):
                hits.append((name + " (file name)", w))
    return hits


# ------------------------------------------------------------- pseudonyms ----


def _anon(samples=(), users=None, folders=(), other=(), **kw) -> Anonymiser:
    a = Anonymiser(**kw)
    for u, al in (users or {}).items():
        a.add_user(u, al)
    for f in folders:
        a.add_folder(f)
    for s in samples:
        a.add_sample(s)
    for o in other:
        a.add_name(o)
    return a.freeze()


def test_names_are_replaced_token_by_token_and_numbers_doses_and_control_words_stay():
    a = _anon(samples=["Olaparib_10uM_3h_1", "Olaparib_10uM_3h_2", "DMSO_1", "pool_1"], users={"Isaac": ["IJ"]},
              folders=["20260301_Isaac_DIA_BRD4-JQ1-degrader"])
    new = a.text("Olaparib_10uM_3h_1")
    cond = a.tok["olaparib"]
    assert new == f"{cond}_10uM_3h_1" and "_" not in cond and cond.lower().endswith("cond" + cond[-1].lower())
    assert a.text("DMSO_1") == "DMSO_1" and a.text("pool_1") == "pool_1"       # the analysis reads these words
    assert a.text("Isaac") == "user01" and a.text("IJ") == "user01a"
    assert a.text("20260301_Isaac_DIA_BRD4-JQ1-degrader") == "exp001"
    # the same original gives the same pseudonym wherever it stands, whatever its case or separator
    line = "C:\\Fragpipe_General\\Isaac\\20260301_Isaac_DIA_BRD4-JQ1-degrader\\raw\\OLAPARIB_10uM_3h_1.raw olaparib-10uM"
    assert a.text(line) == f"C:\\Fragpipe_General\\user01\\exp001\\raw\\{cond}_10uM_3h_1.raw {cond}-10uM"
    assert a.text(f"Mean_{'Olaparib_10uM_3h_1'} Intensity\t12.5\t1e-05") == f"Mean_{cond}_10uM_3h_1 Intensity\t12.5\t1e-05"
    assert a.find("nothing here: DMSO_1 12.5 user01 exp001") == []
    assert a.find("left: Olaparib_10uM_3h_2 and isaac") == ["Olaparib_10uM_3h_2", "isaac"]


def test_a_name_inside_a_longer_word_is_left_alone():
    a = _anon(samples=["Al_1", "Al_2", "DMSO_1"], users={"Al": [], "Aman": []})
    assert a.text("Al") == "user01"
    assert a.text("Albumin ALB serum albumin; human, Amanita") == "Albumin ALB serum albumin; human, Amanita"
    assert a.text("Al_1\tAl-2\t(Al)") == "user01_1\tuser01-2\t(user01)"


def test_a_plain_word_of_a_name_is_replaced_inside_the_name_only_an_id_everywhere():
    a = _anon(samples=["EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1", "KL6159A_TMT_F1"], users={"EJQ": []})
    pk, kl = a.tok["pk"], a.tok["kl6159a"]
    assert a.text("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1") == f"user01_{pk}_user01-2-027_isoDTB_1uM_3h_1"
    assert a.text("PK and pk values") == "PK and pk values"            # two letters alone: just a word
    assert a.text("compound KL6159A, kl6159a") == f"compound {kl}, {kl}"
    # a cut-off or re-joined name is still that name (the analysis writes such names)
    assert a.text("series EJQ_PK_EJQ_2_027_isoDTB_3h") == f"series user01_{pk}_user01_2_027_isoDTB_3h"
    assert a.text("EJQ_PK") == f"user01_{pk}"
    assert a.name("PK") == pk


def test_pseudonyms_keep_the_byte_order_of_the_sample_names():
    """Perseus imputation draws per sample in byte order of the names: a pseudonym that sorts on the other side
    of DMSO would change the imputed values."""
    samples = [f"{c}_{r}" for c in ("Abemaciclib", "DMSO", "Zanubrutinib", "abc", "WT", "Mock") for r in (1, 2)]
    a = _anon(samples=samples)
    new = [a.text(s) for s in samples]
    assert sorted(range(len(samples)), key=lambda i: samples[i].encode()) == \
        sorted(range(len(new)), key=lambda i: new[i].encode())
    assert bundle.order_kept(a, samples)
    assert a.text("Abemaciclib_1") < "DMSO_1" < a.text("Zanubrutinib_1")
    # users are numbered, not ordered: when two users' initials lead the sample names, it is reported
    b = _anon(samples=["IJ_x_1", "EJQ_x_1"], users={"Isaac": ["IJ"], "EJQ": []})
    assert not bundle.order_kept(b, ["IJ_x_1", "EJQ_x_1"])


def test_keep_conditions_keeps_every_condition_word_but_not_the_user():
    a = _anon(samples=["EJQ_Olaparib_1", "DMSO_1"], users={"EJQ": []}, keep_conditions=True)
    assert a.text("EJQ_Olaparib_1 Olaparib") == "user01_Olaparib_1 Olaparib"
    assert "olaparib" in a.kept_seen


def test_addresses_home_folders_and_odd_names():
    a = _anon(samples=["Drug A 0.5uM_1"], users={"Nich Foster": []}, folders=["2026 03 01 Zoë (redo)"])
    out = a.text("mail nich.foster@berkeley.edu and NICH.FOSTER@berkeley.edu from 10.32.4.17 or fe80::1ff:fe23:4567:890a; "
                 "ThermoRawFileParser 1.4.3.0 on 127.0.0.1")
    assert "berkeley" not in out and out.count("email01@example.invalid") == 2
    assert "10.32.4.17" not in out and "fe80" not in out
    assert "1.4.3.0" in out and "127.0.0.1" in out                       # a version, and loopback, are not addresses
    home = a.text(r"C:\Users\jsmith\Desktop\x.zip and C:\\Users\\jsmith\\AppData and /Users/mlee/x /home/mlee C:\Users\Public\x")
    assert "jsmith" not in home and "mlee" not in home and r"C:\Users\Public\x" in home
    assert a.text("jsmith") != "jsmith"                                   # learnt from the path, known from then on
    assert a.grew
    assert a.text("by Nich Foster, in '2026 03 01 Zoë (redo)'") == "by user01, in 'exp001'"
    assert a.text(json.dumps("2026 03 01 Zoë (redo)")) == '"exp001"'      # as json.dumps writes it (\\u00eb)
    assert "Drug A" in a.text("Drug A 0.5uM_1") and a.find("x") == []    # every word of it is a kept word


def test_names_nothing_registered_are_recognised_by_their_shape():
    """An old log line names a folder that was rejected and removed long ago: not in the job list, not in the inbox."""
    a = _anon(users={"Isaac": []})
    line = ("2026-01-05 10:00:01 INFO rejected 20251130_Isaac_TMT_Sotorasib-KRAS (uneven fractions): "
            "Sotorasib_KRAS_TMT_F1.raw, sotorasib_kras_tmt_f2.RAW; kept crash-20260105-100001-worker.txt, "
            "ionomos-20260105.db and fragpipe_previous_20260105-100001")
    out = a.text(line)
    assert "Sotorasib" not in out and "sotorasib" not in out and "KRAS" not in out and "kras" not in out
    folder = a.text("20251130_Isaac_TMT_Sotorasib-KRAS")
    assert folder.startswith("20251130_user01_TMT_name") and f"rejected {folder} (uneven fractions)" in out and a.grew
    assert "crash-20260105-100001-worker.txt" in out and "ionomos-20260105.db" in out      # Ionomos' own stamps
    assert "fragpipe_previous_20260105-100001" in out and out.startswith("2026-01-05 10:00:01 INFO")
    f1 = a.text("Sotorasib_KRAS_TMT_F1")
    assert f1.endswith("_TMT_F1") and out.count(f1 + ".raw") == 1 and f1[:-1].lower() + "2.RAW" in out
    assert sorted(a.key["names"].values()) == ["KRAS", "Sotorasib"]
    assert a.find(out) == []


def test_identifier_columns_of_a_table_are_never_rewritten():
    assert bundle.protected_columns("Protein.Group\tGenes\tC:\\x\\AR_1.raw\tN.Sequences\n") == {0, 1}
    assert bundle.protected_columns("id\tlabel\tdescription\tAR_1\n") == {0, 1, 2}
    assert bundle.protected_columns("just a line of text\n") == frozenset()
    a = _anon(samples=["AR_1", "AR_2", "DMSO_1"])
    s = bundle._Scrub(a, [], table=True)
    new = a.tok["ar"]
    assert s.line("Protein.Group\tGenes\tAR_1\tDMSO_1\n") == f"Protein.Group\tGenes\t{new}_1\tDMSO_1\n"
    assert s.line("P10275\tAR\t12.5\t11.0\n") == "P10275\tAR\t12.5\t11.0\n"   # the gene AR is not the condition AR
    assert bundle._Scrub(a, [], table=False).line("gene AR\n") == f"gene {new}\n"


def test_analysis_setting_names_and_ionomos_words_are_never_pseudonyms():
    a = _anon(samples=["enrichment_1", "control_1", "results_2", "log2fc_1"], folders=["20260101_test_results"])
    for word in ("enrichment", "control", "results", "log2fc", "20260101_test_results"):
        assert a.text(word) == word
    # a user folder that is a number or an ordinary word is not a person; a one-word folder is still replaced
    b = _anon(samples=["EJQ_Olaparib_1"], users={"EJQ": [], "2024": [], "test": []},
              folders=["Olaparib", "Venetoclax", "EJQ"])
    assert b.text("in 2024, a test by EJQ") == "in 2024, a test by user01"
    assert b.text("Olaparib") == b.tok["olaparib"] != "Olaparib" and b.text("folder Venetoclax") == "folder exp001"


# ---------------------------------------------------------------- the zip ----


def test_validate_bundle_round_trip_reproduces_the_analysis(bed, capsys):
    """Lab: a DIA job with the fake FragPipe, bundled anonymised at `validate`. Developer: inspect, unpack,
    analyze. The numbers are identical; only the names differ, and the key turns them back."""
    dest = _job(bed)
    lab = {p.name: p.read_text(encoding="utf-8") for p in (dest / "results").glob("*.tsv")}
    assert "Zanubrutinib_vs_DMSO_differential.tsv" in lab and "Abemaciclib_1" in lab["protein_matrix_processed.tsv"]
    assert "imputed" in lab["protein_results.tsv"]

    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "1", "--level", "validate", "--out", str(bed["out"]),
                     "--note", "Isaac's JQ1 run looked odd"]) == 0
    said = capsys.readouterr().out
    z = next(bed["out"].glob("Ionomos-bundle-*-validate-*.zip"))
    key = next(bed["out"].glob("*KEY*DO-NOT-SHARE.json"))
    assert "leak check" in said and str(z) in said and "stays in the lab" in said
    assert key.name not in _members(z) and not list(bed["out"].glob("*.part"))

    # -- nothing of the lab's names is left, in any file or file name (the HTML report included)
    me = [w for w in {getpass.getuser()} if len(w) >= 3]
    assert _survivors(z, [*SECRET_WORDS, *RAWS[3:], *me]) == []
    members = _members(z)
    report = next(n for n in members if n.endswith("results/report.html"))
    assert b"condA" in members[report].replace(b"CondA", b"condA") and b"DMSO" in members[report]
    assert not [n for n in members if n.lower().endswith((".raw", ".fas", ".fasta"))]

    # -- the developer's side
    assert cli.main(["bundle", "inspect", str(z)]) == 0
    seen = capsys.readouterr().out
    assert "level: validate" in seen and "status done  method DIA" in seen and "analysis can be repeated: yes" in seen
    assert "human_reviewed_decoys.fas, " in seen and "4 entries (2 decoys)" in seen and "anonymised" in seen
    un = bed["tmp"] / "unpacked"
    assert cli.main(["bundle", "unpack", str(z), str(un)]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if " analyze " in ln)
    exp = un / "exp001"
    assert f'analyze "{exp}"' in line and (un / "config.yaml").is_file() and (un / "_bundle" / "BUNDLE.json").is_file()
    shipped = {p.name: p.read_text(encoding="utf-8") for p in (exp / "results").glob("*.tsv")}
    assert cli.main(["--config", str(un / "config.yaml"), "analyze", str(exp), "--quiet"]) == 0
    capsys.readouterr()
    again = {p.name: p.read_text(encoding="utf-8") for p in (exp / "results").glob("*.tsv")}
    assert set(again) == set(shipped) and len(again) == len(lab) >= 8
    for name in shipped:
        assert again[name] == shipped[name], f"{name} differs after re-analysis"
    # ... and they are the lab's numbers: put the names back with the key and compare with the lab's own files
    for name, text in again.items():
        back = bundle.translate(key, name)
        assert bundle.translate(key, text) == lab[back], f"{back} is not the lab's table with other names"
    assert cli.main(["bundle", "translate", str(key), str(exp / "results" / "sample_qc.tsv")]) == 0
    assert "Zanubrutinib_1" in capsys.readouterr().out


def test_diagnose_bundle_of_a_failed_search(bed, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "oom")
    dest = _drop(bed)
    _run(bed)
    assert bed["ledger"].get(1).status == "failed"
    (names.run_dir(dest) / names.RUN_FINGERPRINT).write_text(
        json.dumps({"fragpipe": "24.0", "folder": str(dest), "user": "Isaac"}), encoding="utf-8")
    (bed["cfg"].log_dir / "ionomos.log").write_text(
        f"2026-03-01 12:00:00 ERROR job 1 {FOLDER} failed for Isaac (IJ) on 10.0.5.9\n", encoding="utf-8")
    res = bundle.create(bed["cfg_path"], opts=Options(note="it broke"), dest_dir=bed["out"])   # no job named
    m = _members(res.path)
    assert res.manifest["level"] == "diagnose" and [j["status"] for j in res.manifest["jobs"]] == ["failed"]
    arc = res.manifest["jobs"][0]["arc"]
    assert arc == "jobs/1-exp001"
    for inside in ("ionomos.json", "FAILED.txt", "ionomos_run/fragpipe_console.log", "ionomos_run/DIA.workflow",
                   "ionomos_run/fragpipe-files.fp-manifest", f"ionomos_run/{names.RUN_FINGERPRINT}"):
        assert f"{arc}/{inside}" in m, inside
    for top in ("BUNDLE.json", "README.txt", "note.txt", "build.json", "report.txt", "config.yaml", "logs/ionomos.log"):
        assert top in m, top
    assert any(n.startswith("attention/") for n in m)                  # the failed search's needs-attention item
    assert "OutOfMemoryError" in m[f"{arc}/ionomos_run/fragpipe_console.log"].decode()
    assert json.loads(m[f"{arc}/ionomos.json"])["status"] == "failed"
    log = m["logs/ionomos.log"].decode()
    key = json.loads(res.key_path.read_text(encoding="utf-8"))
    ip = next(p for p, orig in key["addresses"].items() if orig == "10.0.5.9")
    assert f"job 1 exp001 failed for user01 (user01a) on {ip}" in log
    assert not [n for n in m if "/fragpipe/" in n or n.endswith(".tsv")]   # no result tables at this level
    assert _survivors(res.path, SECRET_WORDS) == []
    # hashes in BUNDLE.json are those of the files in the zip
    listed = {f["path"]: f for f in res.manifest["files"]}
    assert set(listed) == set(m) - {"BUNDLE.json", "README.txt"}
    for n, f in listed.items():
        assert hashlib.sha256(m[n]).hexdigest() == f["sha256"] and len(m[n]) == f["bytes"]
    readme = m["README.txt"].decode()
    assert "Ionomos sent it nowhere" in readme and "stays in the lab" in readme and "Not covered" in readme
    assert key["users"]["user01"] == "Isaac" and key["experiments"]["exp001"] == FOLDER
    assert key["bundle"] == res.path.name and "KEEP IT IN THE LAB" in key["what"]


def test_without_anonymising_names_stay_and_secrets_still_go(bed):
    _job(bed)
    cfg_text = bed["cfg_path"].read_text(encoding="utf-8")
    bed["cfg_path"].write_text(cfg_text + "notify:\n  slack: {url: 'https://hooks.slack.com/services/T0/B0/SECRETKEY'}\n",
                               encoding="utf-8")
    res = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", anonymise=False), dest_dir=bed["out"])
    m = _members(res.path)
    assert res.key_path is None and not list(bed["out"].glob("*KEY*"))
    assert f"jobs/1-{FOLDER}/results/Zanubrutinib_vs_DMSO_differential.tsv" in m
    assert not res.manifest["anonymised"]["enabled"] and "NOT anonymised" in m["README.txt"].decode()
    assert not any(b"SECRETKEY" in data for data in m.values())
    assert "NOT anonymised" in res.message


def test_what_is_never_included_and_what_cannot_be_scrubbed(bed):
    dest = _job(bed)
    for rel in ("fragpipe/library.tsv", "fragpipe/lib.predicted.speclib", "fragpipe/DMSO_1.mzML", "results/db.fasta",
                "results/copy.raw", "fragpipe/combined_protein.tsv.bak"):
        (dest / rel).write_text("Isaac\n", encoding="utf-8")
    (dest / "results" / "volcano.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR Isaac")
    (dest / "results" / "notes_utf16.txt").write_bytes("Zanubrutinib by Isaac\n".encode("utf-16"))
    res = bundle.create(bed["cfg_path"], ["1"], Options(level="validate"), dest_dir=bed["out"])
    m = _members(res.path)
    assert not [n for n in m if n.lower().endswith((".raw", ".mzml", ".fasta", ".speclib", ".png", ".bak"))]
    assert not [n for n in m if "library" in n]
    why = {Path(s["path"]).name: s["why"] for s in res.manifest["skipped"]}
    assert "never included" in why["db.fasta"] and "never included" in why["copy.raw"]
    assert "not a text file" in why["volcano.png"]
    assert m["jobs/1-exp001/results/notes_utf16.txt"].decode() == f"{bundle.learn(bundle.collect(bed['cfg_path'], ['1'])).text('Zanubrutinib')} by user01\n"
    assert _survivors(res.path, SECRET_WORDS) == []
    # not anonymised: the picture can go in as it is; the never-list still holds
    raw = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", anonymise=False), dest_dir=bed["out"])
    mm = _members(raw.path)
    assert f"jobs/1-{FOLDER}/results/volcano.png" in mm and not [n for n in mm if n.endswith((".raw", ".fasta"))]


def test_size_limit_and_row_sampling_are_said_plainly(bed):
    testbed.drop(bed["root"], "iso_good")
    assert intake(bed["cfg"].inbox / "20260902-isoDTB_EJQ-2-027", bed["cfg"], bed["ledger"]).value == "queued"
    _run(bed)
    dest = Path(bed["ledger"].get(1).dest_dir)
    psm = sorted(dest.glob("fragpipe/*/psm.tsv"))
    rows = len(psm[0].read_text(encoding="utf-8").splitlines()) - 1
    # -- a PSM table over its cap is row-sampled, with its header, and the job is marked
    res = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", psm_mb=0.05), dest_dir=bed["out"])
    m = _members(res.path)
    sampled = next(n for n in m if n.endswith("_1/psm.tsv"))
    lines = m[sampled].decode().splitlines()
    assert lines[0].startswith("Spectrum\tSpectrum File\t") and 1 < len(lines) - 1 < rows / 2
    capped = {c["path"]: c for c in res.manifest["capped"]}
    assert "1 row in" in capped[sampled]["why"] and capped[sampled]["bytes"] == psm[0].stat().st_size
    job = res.manifest["jobs"][0]
    assert job["reproducible"] is False and "PSM-level numbers will differ" in job["incomplete"][0]
    assert "NOT fully" in bundle.inspect_text(res.path) and "capped:" in bundle.inspect_text(res.path)
    assert _survivors(res.path, ["EJQ", "EJQ-2-027", "20260902-isoDTB_EJQ-2-027"]) == []
    # -- over the limit for the whole bundle: tables are left out and listed; the small files all stay
    small = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", max_mb=0.2), dest_dir=bed["out"])
    ms = _members(small.path)
    assert "jobs/1-exp001/ionomos.json" in ms and "jobs/1-exp001/ionomos_run/fragpipe_console.log" in ms
    left = [s for s in small.manifest["skipped"] if "size limit" in s["why"]]
    assert left and all(s["path"] not in ms for s in left) and "--max-mb" in left[0]["why"]
    assert small.manifest["jobs"][0]["reproducible"] is False
    assert "left out" in small.message and "left out:" in bundle.inspect_text(small.path)
    assert sum(len(v) for n, v in ms.items() if n.startswith("jobs/")) <= 0.2e6


def test_a_bundle_never_replaces_a_file_and_only_reads_the_experiment(bed):
    dest = _job(bed)
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in dest.rglob("*") if p.is_file()}
    target = bed["out"] / "report.zip"
    first = bundle.create(bed["cfg_path"], ["1"], Options(level="validate"), dest=target)
    second = bundle.create(bed["cfg_path"], ["1"], Options(level="validate"), dest=target)
    assert first.path == target and second.path == bed["out"] / "report-2.zip"
    assert first.key_path != second.key_path and first.key_path.is_file() and second.key_path.is_file()
    assert sorted(p.name for p in bed["out"].iterdir()) == sorted(
        [first.path.name, second.path.name, first.key_path.name, second.key_path.name])
    assert {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in dest.rglob("*") if p.is_file()} == before


def test_a_leak_fails_the_bundle_and_leaves_no_zip(bed, monkeypatch):
    _job(bed)
    real = bundle._Scrub.line

    def forgetful(self, s):  # a bug: one kind of file goes in unscrubbed
        return s if "Zanubrutinib_2" in s and "\t" in s else real(self, s)

    monkeypatch.setattr(bundle._Scrub, "line", forgetful)
    with pytest.raises(bundle.BundleLeak) as err:
        bundle.create(bed["cfg_path"], ["1"], Options(level="validate"), dest_dir=bed["out"])
    assert "LEAK CHECK FAILED" in str(err.value) and "no bundle was written" in str(err.value)
    assert any("Zanubrutinib" in o for _arc, o, _n in err.value.leaks)
    assert list(bed["out"].iterdir()) == []                              # no zip, no key, no .part
    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "1", "--level", "validate", "--out", str(bed["out"])]) == 1
    assert list(bed["out"].iterdir()) == []


def test_file_names_in_the_zip_are_checked_too(bed):
    _job(bed)
    res = bundle.create(bed["cfg_path"], ["1"], Options(level="validate"), dest_dir=bed["out"])
    anon = bundle.learn(bundle.collect(bed["cfg_path"], ["1"], Options(level="validate")))
    bad = bed["tmp"] / "bad.zip"
    with zipfile.ZipFile(res.path) as src, zipfile.ZipFile(bad, "w") as out:
        out.writestr("jobs/1-exp001/results/Zanubrutinib_vs_DMSO.svg", "<svg/>")
        out.writestr("logs/x.log", "fine\nrun by Isaac\n")
        out.writestr("jobs/1-exp001/results/protein_results.tsv", "id\tlabel\tdescription\tx\nP1\tChris\tIsaac\t1\n")
        out.writestr("BUNDLE.json", src.read("BUNDLE.json"))
    leaks, kept = bundle.verify(bad, anon)
    assert ("jobs/1-exp001/results/Zanubrutinib_vs_DMSO.svg (its name)", "Zanubrutinib", 1) in leaks
    assert ("logs/x.log", "Isaac", 1) in leaks and len(leaks) == 2
    # a name that is also in an identifier column is kept there and reported, not failed
    assert sorted(o for _a, o, _n in kept) == ["Chris", "Isaac"]
    assert bundle.verify(res.path, anon)[0] == []


def test_free_text_and_name_fragments_in_structured_files(bed):
    extra = {"notes": "Isaac's JQ1 titration for the Smith collaboration", "analysis": {"control": "DMSO"}}
    dest = _drop(bed, extra=extra)
    _run(bed)
    res = bundle.create(bed["cfg_path"], ["1"], dest_dir=bed["out"])
    m = _members(res.path)
    exp_yaml = yaml.safe_load(m["jobs/1-exp001/experiment.yaml"])
    assert exp_yaml["notes"].startswith("[removed by the bundle:") and exp_yaml["analysis"] == {"control": "DMSO"}
    rec = json.loads(m["jobs/1-exp001/ionomos.json"])
    assert rec["plan"]["folder"]["original"] == "exp001" and rec["plan"]["folder"]["user"] == "user01"
    assert "Smith" not in m["jobs/1-exp001/ionomos.json"].decode()
    original = json.loads((dest / "ionomos.json").read_text(encoding="utf-8"))["plan"]["folder"]["tokens"]
    assert "BRD4" in original and len(rec["plan"]["folder"]["tokens"]) == len(original)
    assert not {"BRD4", "JQ1", "degrader", "Isaac"} & set(rec["plan"]["folder"]["tokens"])
    files = [x["file"] for x in rec["plan"]["manifest"]]
    assert "raw/DMSO_1.raw" in files and len(files) == 9 and not [f for f in files if "nib" in f or "lib" in f]
    assert _survivors(res.path, [*SECRET_WORDS, "Smith", "degrader"]) == []


def test_a_folder_that_is_not_a_job_non_ascii_and_long_paths(bed):
    def deep_folder(filler: int) -> Path:
        return (bed["tmp"] / "Zoë Müller" / ("lange_ordner_" + "x" * filler) / ("noch_einer_" + "y" * filler)
                / "Größe_试验_BRD4")

    deep = deep_folder(90)
    out = deep / "diann"
    try:
        out.mkdir(parents=True)
    except OSError:  # a Windows without long paths switched on: the analysis itself could not write there either
        deep = deep_folder(3)
        out = deep / "diann"
        out.mkdir(parents=True)
    samples = ["DMSO_1", "DMSO_2", "DMSO_3", "Größe_1", "Größe_2", "Größe_3"]
    from ionomos.downstream import simulate

    simulate.dia_pg_matrix(out / "report.pg_matrix.tsv", [(f"D:\\Daten\\Zoë Müller\\{s}.raw", s.rsplit("_", 1)[0])
                                                          for s in samples], 7)
    assert postprocess.run_for_folder(deep, None, "DIA").report
    assert len(str(deep / "results" / "protein_matrix_processed.tsv")) > 260 or deep == deep_folder(3)
    res = bundle.create(bed["cfg_path"], [str(deep)], Options(level="validate"), dest_dir=bed["out"])
    m = _members(res.path)
    job = res.manifest["jobs"][0]
    assert job["arc"] == "jobs/exp001" and job["result_tables"] == 1 and job["reproducible"]
    assert "jobs/exp001/diann/report.pg_matrix.tsv" in m and "jobs/exp001/results/report.html" in m
    assert _survivors(res.path, ["Größe", "试验", "Zoë", "Müller", "BRD4"]) == []
    header = m["jobs/exp001/diann/report.pg_matrix.tsv"].decode().splitlines()[0]
    # the raw files' folder is not one of the lab's own: it is replaced as one piece, the run names part by part
    assert "\tfolder01\\DMSO_1.raw\t" in header and "D:" not in header
    key = json.loads(res.key_path.read_text(encoding="utf-8"))
    assert key["folders"]["folder01"] == "D:\\Daten\\Zoë Müller" and str(deep.parent) in key["folders"].values()
    # a path that is no job is said, and the rest is still bundled
    res2 = bundle.create(bed["cfg_path"], [str(deep), "99", str(bed["tmp"] / "nope")], dest_dir=bed["out"])
    assert len(res2.problems) == 2 and "no job 99" in res2.problems[0] and len(res2.manifest["not_found"]) == 2
    assert "nope" in res2.problems[1] and res2.manifest["not_found"][1] == "a folder that was not found"


def test_a_large_table_is_streamed_not_loaded(tmp_path):
    import tracemalloc

    a = _anon(samples=["Olaparib_1", "DMSO_1"], users={"Isaac": []})
    table = tmp_path / "combined_protein.tsv"
    row = "P12345\tALB\t" + "\t".join(f"{i * 1234.5678:.4f}" for i in range(60)) + "\n"
    with open(table, "w", encoding="utf-8") as f:
        f.write("Protein\tGene\tOlaparib_1 Intensity\tDMSO_1 Intensity" + "\tx" * 58 + "\n")
        f.writelines([row] * 20_000)
    size = table.stat().st_size
    assert size > 12_000_000
    item = bundle.Item(arc="t/combined_protein.tsv", what="t", src=table, size=size, full=size)
    tracemalloc.start()
    try:
        with zipfile.ZipFile(tmp_path / "t.zip", "w", zipfile.ZIP_DEFLATED) as z:
            written, sha = bundle._put(z, item.arc, bundle._lines(item, a, []), bundle._Scrub(a, [], True))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 6_000_000, f"peak {peak / 1e6:.1f} MB for a {size / 1e6:.0f} MB table"
    with zipfile.ZipFile(tmp_path / "t.zip") as z, z.open("t/combined_protein.tsv") as f:
        head = f.readline().decode()
        digest = hashlib.sha256(head.encode() + f.read()).hexdigest()
    assert head.startswith(f"Protein\tGene\t{a.tok['olaparib']}_1 Intensity\tDMSO_1 Intensity") and digest == sha


def test_long_paths_get_the_windows_prefix(monkeypatch):
    fake = types.SimpleNamespace(name="nt", path=ntpath)
    monkeypatch.setattr(bundle, "os", fake)
    long = "C:\\Fragpipe_General\\" + "a" * 250 + "\\x.tsv"
    assert bundle._long(long) == "\\\\?\\" + long
    assert bundle._long("\\\\server\\share\\" + "b" * 250) == "\\\\?\\UNC\\server\\share\\" + "b" * 250
    assert bundle._long("C:\\short\\x.tsv") == "C:\\short\\x.tsv"
    assert bundle._long("\\\\?\\" + long) == "\\\\?\\" + long


# ----------------------------------------------------- where it is saved ----


def test_the_desktop_is_found_when_onedrive_moved_it(tmp_path, monkeypatch):
    import os as real_os
    import sys

    one = tmp_path / "OneDrive - Lab" / "Desktop"
    one.mkdir(parents=True)
    (tmp_path / "profile").mkdir()                                       # no Desktop under the profile
    env = {"USERPROFILE": str(tmp_path / "profile"), "OneDriveCommercial": str(tmp_path / "OneDrive - Lab")}
    fake_os = types.SimpleNamespace(name="nt", environ=env, path=real_os.path)
    monkeypatch.setattr(service, "os", fake_os)
    monkeypatch.setitem(sys.modules, "winreg", None)                     # no registry answer: the OneDrive variables
    assert service.desktop_dir() == one

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    reg = types.SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=lambda *a: _Key(),
                                QueryValueEx=lambda key, name: (str(tmp_path / "Redirected" / "Desktop"), 2))
    monkeypatch.setitem(sys.modules, "winreg", reg)
    assert service.desktop_dir() == one                                  # the registry's folder does not exist: next
    (tmp_path / "Redirected" / "Desktop").mkdir(parents=True)
    assert service.desktop_dir() == tmp_path / "Redirected" / "Desktop"


def test_without_a_desktop_the_bundle_goes_to_the_log_folder(bed, monkeypatch):
    monkeypatch.setattr(service, "desktop_dir", lambda: bed["tmp"] / "no such desktop")
    assert bundle.output_dir(bed["cfg_path"]) == bed["cfg"].log_dir
    res = bundle.create(bed["cfg_path"])
    assert res.path.parent == bed["cfg"].log_dir and res.key_path.parent == bed["cfg"].log_dir
    assert str(bed["cfg"].log_dir) in res.message


def test_the_old_entry_points_make_the_same_bundle(bed, capsys):
    z = service.save_problem_report(bed["cfg_path"], "nothing moved", dest_dir=bed["out"])
    assert z.name.startswith("Ionomos-report-") and z.with_name(z.stem + names.BUNDLE_KEY_SUFFIX).is_file()
    m = _members(z)
    assert json.loads(m["BUNDLE.json"])["level"] == "diagnose" and "nothing moved" in m["note.txt"].decode()
    assert cli.main(["--config", str(bed["cfg_path"]), "diagnose", "--zip", str(bed["out"] / "d.zip")]) == 0
    assert "BUNDLE.json" in _members(bed["out"] / "d.zip")
    old = bed["tmp"] / "old.zip"                                          # a report from before bundles
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("build.json", json.dumps({"version": "0.12.0"}))
        zf.writestr("jobs/3-some_job/ionomos.json", "{}")
    text = bundle.inspect_text(old)
    assert "no BUNDLE.json" in text and "0.12.0" in text and "jobs/3-some_job" in text
    with pytest.raises(bundle.BundleError):
        bundle.inspect(bed["cfg_path"])


# ----------------------------------------------------------- the reader ----


def test_unpack_replaces_nothing_ignores_paths_that_climb_out_and_checks_hashes(bed):
    _job(bed)
    res = bundle.create(bed["cfg_path"], ["1"], Options(level="validate"), dest_dir=bed["out"])
    un = bed["tmp"] / "un"
    out = bundle.unpack(res.path, un)
    assert out["experiments"] == [un / "exp001"] and out["hash_mismatches"] == [] and out["level"] == "validate"
    cfg = load(out["config"], check_paths=False)
    assert "DIA" in cfg.methods and str(cfg.log_dir).startswith(str(un)) and cfg.notify.get("enabled") is False
    with pytest.raises(bundle.BundleError, match="never replaces"):
        bundle.unpack(res.path, un)
    evil = bed["tmp"] / "evil.zip"
    with zipfile.ZipFile(res.path) as src, zipfile.ZipFile(evil, "w") as dst:
        for n in src.namelist():
            data = src.read(n)
            dst.writestr(n, data + b"tampered\n" if n.endswith("sample_qc.tsv") else data)
        dst.writestr("../outside.txt", "x")
        dst.writestr("/abs.txt", "x")
    un2 = bed["tmp"] / "un2"
    out2 = bundle.unpack(evil, un2)
    assert [Path(p).name for p in out2["hash_mismatches"]] == ["sample_qc.tsv"]
    assert not (bed["tmp"] / "outside.txt").exists() and not list(un2.rglob("abs.txt"))
    assert cli.main(["bundle", "unpack", str(evil), str(bed["tmp"] / "un3")]) == 1


def test_cli_usage_and_dry_run(bed, capsys):
    _job(bed)
    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "1", "--level", "validate", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Level: validate" in out and "DIA-NN protein matrix" in out and "Estimated size" in out
    assert "Never included: raw / mzML" in out and not bed["out"].exists()
    assert cli.main(["bundle", "inspect"]) == 2 and cli.main(["bundle", "unpack", "x.zip"]) == 2
    assert cli.main(["--config", str(bed["cfg_path"]), "bundle", "77", "--out", str(bed["out"])]) == 2
    assert "no job 77" in capsys.readouterr().err
    with pytest.raises(bundle.BundleError):
        Options(level="everything")


# ------------------------------------------------- the window's choices ----


def test_the_windows_logic_needs_no_window(bed):
    _job(bed)
    jobs = bundle.recent_jobs(bed["cfg_path"])
    assert [j["id"] for j in jobs] == [1] and jobs[0]["status"] == "done" and not jobs[0]["problem"]
    assert FOLDER in bundle.job_label(jobs[0]) and bundle.preselect(jobs, 1) == [0] and bundle.preselect(jobs) == []
    c = bundle.Choice(validate=True, jobs=(1,), note="x", keep_conditions=True)
    assert c.targets() == ["1"] and c.options().level == "validate" and c.options().keep_conditions
    assert bundle.Choice().options().level == "diagnose" and bundle.Choice().options().anonymise
    assert not bundle.Choice(anonymise=False, keep_conditions=True).options().keep_conditions
    plan = bundle.collect(bed["cfg_path"], c.targets(), c.options())
    lines = bundle.summary_lines(plan)
    assert lines[0].startswith("Level: validate") and any("DIA-NN protein matrix" in ln for ln in lines)
    assert any("Estimated size" in ln for ln in lines) and plan.total > 100_000
    # with no job named: the problem jobs, and for validate also the last finished one
    assert [j.job_id for j in bundle.collect(bed["cfg_path"], (), Options(level="validate")).jobs] == [1]
    assert bundle.collect(bed["cfg_path"], (), Options()).jobs == []
    kept = bundle.create(bed["cfg_path"], ["1"], Options(level="validate", keep_conditions=True), dest_dir=bed["out"])
    assert "jobs/1-exp001/results/Zanubrutinib_vs_DMSO_differential.tsv" in _members(kept.path)
    assert kept.manifest["anonymised"]["conditions"] == "keep" and _survivors(kept.path, ["Isaac", FOLDER, "BRD4"]) == []
