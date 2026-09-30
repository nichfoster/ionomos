"""`ionomos demo` and the analysis-only install (ROADMAP Phase 5A): a stranger's `pip install ionomos`,
`ionomos demo` and `ionomos analyze <table|folder>` work offline, with no lab config and no Tk."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ionomos import cli, demo
from ionomos.downstream import enrich


def _rows(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    return [dict(zip(head, ln.split("\t"), strict=False)) for ln in lines[1:]]


# ------------------------------------------------------------------ folder --


def test_default_folder_moves_on_when_taken_and_never_touches_it(tmp_path):
    assert demo.pick_folder(None, cwd=tmp_path) == tmp_path / "ionomos_demo"
    taken = tmp_path / "ionomos_demo"
    taken.mkdir()
    (taken / "mine.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "ionomos_demo_2").write_text("a file, not a folder", encoding="utf-8")
    p = demo.pick_folder(None, cwd=tmp_path)
    assert p == tmp_path / "ionomos_demo_3" and " " not in p.name
    assert (taken / "mine.txt").read_text(encoding="utf-8") == "keep"


def test_given_folder_must_be_new_or_empty(tmp_path):
    assert demo.pick_folder(tmp_path / "new") == tmp_path / "new"
    (tmp_path / "empty").mkdir()
    assert demo.pick_folder(tmp_path / "empty") == tmp_path / "empty"
    full = tmp_path / "full"
    full.mkdir()
    (full / "data.raw").write_bytes(b"\0")
    with pytest.raises(demo.DemoError, match="not an empty folder"):
        demo.pick_folder(full)
    with pytest.raises(demo.DemoError):
        demo.pick_folder(full / "data.raw")
    assert cli.main(["demo", str(full), "--quiet"]) == 2
    with pytest.raises(demo.DemoError):  # the writer checks again itself
        demo.write_demo(full)
    assert sorted(x.name for x in full.iterdir()) == ["data.raw"]


# -------------------------------------------------------------- end to end --


def test_demo_end_to_end_finds_what_was_planted(tmp_path, monkeypatch, capsys):
    def no_network(*a, **k):
        raise AssertionError("the demo must not download gene sets")

    monkeypatch.setattr(enrich, "_download", no_network)
    monkeypatch.setenv("IONOMOS_GENESETS", str(tmp_path / "no-cache"))
    folder = tmp_path / "d"
    assert cli.main(["demo", str(folder), "--quiet"]) == 0
    printed = capsys.readouterr().out
    report = folder / "results" / "report.html"
    assert f"report: {report}" in printed and "DrugA vs DMSO" in printed and report.is_file()
    for name in ("README.txt", "experiment.yaml", demo.GMT_NAME, "fragpipe/diann-output/report.pg_matrix.tsv"):
        assert (folder / name).is_file(), name
    results = folder / "results"
    summary = json.loads((results / "analysis.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in summary["comparisons"]] == ["DrugA vs DMSO", "DrugB vs DMSO"]
    assert all(c["up"] > 5 and c["down"] > 0 for c in summary["comparisons"])
    assert not [i for i in summary.get("issues") or [] if i.get("severity") == "error"]

    ora = {(r["comparison"], r["direction"], r["term"]): float(r["p_adj"]) for r in _rows(results / "enrichment.tsv")}
    assert ora[("DrugA vs DMSO", "up", "DEMO_HEAT_SHOCK_RESPONSE")] < 1e-3
    assert ora[("DrugA vs DMSO", "down", "DEMO_DNA_REPLICATION")] < 1e-3
    assert ora[("DrugB vs DMSO", "up", "DEMO_CHOLESTEROL_BIOSYNTHESIS")] < 1e-3
    assert {r["library"] for r in _rows(results / "enrichment.tsv")} == {"demo_gene_sets"}  # relative .gmt, no Enrichr

    ranks = _rows(results / "gene_set_ranks.tsv")
    top = {c: next(r for r in ranks if r["comparison"] == c) for c in ("DrugA vs DMSO", "DrugB vs DMSO")}
    assert (top["DrugA vs DMSO"]["term"], top["DrugA vs DMSO"]["direction"]) == ("DEMO_PROTEASOME", "up")
    nrf2 = next(r for r in ranks if r["comparison"] == "DrugB vs DMSO" and r["term"] == "DEMO_NRF2_TARGETS")
    assert nrf2["direction"] == "up" and float(nrf2["p_adj"]) < 0.01

    onoff = {(r["comparison"], r["only_in"], r["label"]) for r in _rows(results / "presence_absence.tsv")}
    assert {("DrugA vs DMSO", "DrugA", "HSPA1A"), ("DrugA vs DMSO", "DrugA", "HSPA1B"),
            ("DrugB vs DMSO", "DMSO", "GENE250"), ("DrugB vs DMSO", "DrugB", "INSIG1")} <= onoff


def test_demo_is_reproducible_and_reanalysable_from_anywhere(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    demo.write_demo(a)
    demo.write_demo(b)
    pg = "fragpipe/diann-output/report.pg_matrix.tsv"
    assert (a / pg).read_bytes() == (b / pg).read_bytes()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # experiment.yaml's relative .gmt is read from the experiment folder
    out = demo.analyze(a)
    assert out.report and out.report.is_file()
    assert not [n for n in out.warnings if "gene-set file" in n]
    assert (a / "results" / "enrichment.tsv").read_text(encoding="utf-8").count("demo_gene_sets") > 3


def test_open_without_a_browser_is_a_note_not_a_crash(tmp_path, monkeypatch, capsys):
    from ionomos import service

    opened = []

    def no_browser(p):
        opened.append(p)
        raise FileNotFoundError("xdg-open")

    monkeypatch.setattr(service, "open_path", no_browser)
    assert cli.main(["demo", str(tmp_path / "d"), "--quiet", "--open"]) == 0
    assert opened == [tmp_path / "d" / "results" / "report.html"]
    assert "could not open it here" in capsys.readouterr().err


# --------------------------------------------------------------- no Tk --

NO_TK = """
import os, sys
sys.modules["tkinter"] = None   # `import tkinter` now raises ImportError, as on a Python without Tk
sys.modules["_tkinter"] = None
from ionomos.cli import main
rc = [main(["demo", sys.argv[1], "--quiet"]),
      main(["analyze", os.path.join(sys.argv[1], "fragpipe", "diann-output", "report.pg_matrix.tsv"), "--quiet"]),
      main(["analyze", sys.argv[1], "--quiet"])]
gui = sorted(m for m in sys.modules if m in ("ionomos.app", "ionomos.resolve", "ionomos.tkutil", "ionomos.popups",
                                             "ionomos.analysis_tab", "ionomos.experiment_editor"))
print("RESULT", rc, gui)
"""


def test_demo_and_analyze_run_without_tkinter(tmp_path):
    """pip users may have a Python without Tk (Linux servers, some Homebrew builds): the analysis path
    must never import it, at import time or later."""
    env = {**os.environ, "IONOMOS_OFFLINE": "1", "IONOMOS_NO_GUI": "1",
           "IONOMOS_CONFIG": str(tmp_path / "no-config.yaml"), "IONOMOS_GENESETS": str(tmp_path / "genesets")}
    folder = tmp_path / "demo"
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(NO_TK), str(folder)], cwd=tmp_path, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stderr
    assert "RESULT [0, 0, 0] []" in r.stdout, r.stdout + r.stderr
    assert (folder / "results" / "report.html").is_file()
    assert (folder / "fragpipe" / "diann-output" / "report.pg_matrix_ionomos" / "results" / "report.html").is_file()
