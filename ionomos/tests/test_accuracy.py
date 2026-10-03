"""compare / benchmark as calls (accuracy.py, D67): what the app's Check accuracy page and the command line
both run. No Tk."""
from __future__ import annotations

from pathlib import Path

import pytest

from ionomos import accuracy, cli, downstream, names
from ionomos.downstream import simulate
from ionomos.downstream.tables import read_tsv, write_tsv

RUNS = [(f"C:\\d\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]


@pytest.fixture(scope="module")
def exp(tmp_path_factory):
    d = tmp_path_factory.mktemp("acc") / "exp"
    simulate.dia_pg_matrix(d / "fragpipe/report.pg_matrix.tsv", RUNS, seed=3, n_proteins=300)
    downstream.analyze(d, "DIA", analysis_cfg={"enrichment": False})
    _h, rows = read_tsv(d / "results/Drug_vs_DMSO_differential.tsv")
    return {"dir": d, "rows": rows}


def _limma(path: Path, rows, scale=1.0) -> Path:
    out = [{"ID": r["id"], "logFC": f"{float(r['log2fc']) * scale:.6f}", "P.Value": r["pvalue"],
            "adj.P.Val": r["qvalue"]} for r in rows if r["log2fc"] != "NA"]
    return write_tsv(path, ["ID", "logFC", "P.Value", "adj.P.Val"], out)


def test_problems_before_a_compare_starts(exp, tmp_path):
    assert accuracy.compare_problems("", "")[0].startswith("pick the analysed experiment")
    assert "the reference does not exist" in accuracy.compare_problems(str(exp["dir"]), str(tmp_path / "x.tsv"))[0]
    (tmp_path / "empty").mkdir()
    assert "holds no Ionomos analysis yet" in accuracy.compare_problems(str(tmp_path / "empty"), str(exp["dir"]))[0]
    assert accuracy.compare_problems(str(exp["dir"]), str(exp["dir"] / "results/protein_results.tsv")) == []


def test_run_compare_says_what_the_command_line_prints(exp, tmp_path, capsys):
    ref = _limma(tmp_path / "limma.tsv", exp["rows"])
    lines: list[str] = []
    out = accuracy.run_compare(exp["dir"], ref, lines.append)
    assert out.code == 0 and out.ok and out.page == exp["dir"] / "results" / "compare.html" and out.page.is_file()
    assert out.verdicts and out.verdicts[0].startswith("Drug vs DMSO: agrees")
    assert any(line.startswith("  verdict: agrees") for line in lines) and lines[-2] == f"page: {out.page}"
    assert cli.main(["compare", str(exp["dir"]), str(ref)]) == 0
    assert capsys.readouterr().out.splitlines() == lines  # one code path, one text
    half = accuracy.run_compare(exp["dir"], _limma(tmp_path / "half.tsv", exp["rows"], 2.0), out=tmp_path / "o")
    assert half.code == 1 and half.page == tmp_path / "o" / "compare.html" and "differs" in half.verdicts[0]


def test_run_compare_that_cannot_run(exp, tmp_path):
    out = accuracy.run_compare(tmp_path / "nothing", exp["dir"])
    assert out.code == 2 and out.error.startswith("cannot compare") and out.page is None
    table = exp["dir"] / "results/protein_results.tsv"
    assert "--out" in accuracy.run_compare(table, table).error
    assert accuracy.run_compare(exp["dir"], table, by="name").error.startswith("match by must be one of")


def test_command_lines(tmp_path, monkeypatch):
    assert accuracy.compare_args("e", "r.tsv") == ["compare", "e", "r.tsv"]
    assert accuracy.compare_args("e", "r.tsv", by="gene", flip=True, out="o", open_page=True) == [
        "compare", "e", "r.tsv", "--by", "gene", "--flip", "--out", "o", "--open"]
    assert accuracy.benchmark_args("simulated", grid="quick") == ["benchmark", "--grid", "quick"]
    assert accuracy.benchmark_args("simulated", like="e", grid="standard", out="o") == [
        "benchmark", "--grid", "standard", "--like", "e", "--out", "o"]
    assert accuracy.benchmark_args("real", "e", "hye.yaml") == ["benchmark", "e", "--expected", "hye.yaml"]
    # each one is a command line the real parser reads as meant
    seen = []
    monkeypatch.setattr(cli, "cmd_compare", lambda a: seen.append(a) or 0)
    monkeypatch.setattr(cli, "cmd_benchmark", lambda a: seen.append(a) or 0)
    assert cli.main(accuracy.compare_args("e", "r", by="id", flip=True)) == 0
    assert cli.main(accuracy.benchmark_args("simulated", like="e", grid="quick", out="o")) == 0
    assert cli.main(accuracy.benchmark_args("real", "e", "x.yaml")) == 0
    c, s, r = seen
    assert (c.analysis, c.reference, c.by, c.flip) == ("e", "r", "id", True)
    assert (s.folder, s.like, s.grid, s.out) == (None, "e", "quick", "o")
    assert (r.folder, r.expected) == ("e", "x.yaml")


def test_benchmark_problems(exp, tmp_path):
    assert accuracy.benchmark_problems("simulated", grid="quick") == []
    assert "the grid must be one of" in accuracy.benchmark_problems("simulated", grid="huge")[0]
    assert "holds no Ionomos analysis" in accuracy.benchmark_problems("simulated", like=str(tmp_path))[0]
    p = accuracy.benchmark_problems("real")
    assert len(p) == 2 and "benchmark experiment" in p[0] and "expected-ratios file" in p[1]
    assert "does not exist" in accuracy.benchmark_problems("real", str(exp["dir"]), str(tmp_path / "no.yaml"))[0]
    assert accuracy.benchmark_problems("other") == ["unknown benchmark 'other'"]


def test_the_default_benchmark_folder_has_no_spaces_on_the_lab_pc(tmp_path, monkeypatch):
    assert accuracy.default_benchmark_dir("C:/Fragpipe_Auto/logs").as_posix() == "C:/Fragpipe_Auto/logs/ionomos_benchmark"
    assert " " not in accuracy.default_benchmark_dir("C:/Fragpipe_Auto/logs").as_posix()
    monkeypatch.chdir(tmp_path)
    assert accuracy.default_benchmark_dir() == tmp_path / names.BENCHMARK_DIR == accuracy.default_benchmark_dir("  ")


def test_simulated_benchmark_into_a_folder(tmp_path, monkeypatch):
    from ionomos.downstream import benchmark

    tiny = {"designs": [(3, 3)], "effects": [2.0], "missing": ["typical"], "settings": ["none + median"],
            "seeds": 1, "proteins": 200}
    monkeypatch.setitem(benchmark.GRIDS, "quick", tiny)
    lines, progress = [], []
    out = accuracy.run_benchmark_simulated("quick", lines.append, out=tmp_path / "b", progress=progress.append)
    assert out.code == 0 and out.page == tmp_path / "b" / "benchmark_simulated.html" and out.page.is_file()
    assert lines[0] == "simulated benchmark, grid quick" and progress and out.verdicts[0].startswith("none + median:")
    bad = accuracy.run_benchmark_simulated("quick", like=tmp_path / "nothing")
    assert bad.code == 2 and bad.error.startswith("cannot read the analysis of")


def test_real_benchmark_that_cannot_run_says_why(exp, tmp_path):
    (tmp_path / "e.yaml").write_text("expected: {HUMAN: 1}\n", encoding="utf-8")
    out = accuracy.run_benchmark_real(tmp_path, tmp_path / "e.yaml")
    assert out.code == 2 and "holds no Ionomos analysis" in out.error
