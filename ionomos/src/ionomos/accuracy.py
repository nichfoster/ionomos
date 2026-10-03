"""
`ionomos compare` and `ionomos benchmark` as calls (D60, D67): the command line and the app's
Analysis tab -> Check accuracy run exactly this code.

    out = run_compare(experiment_folder, reference, say=print)          # Outcome(code, page, error)
    out = run_benchmark_simulated("quick", say=print, out=folder)
    out = run_benchmark_real(experiment_folder, "hye.yaml", say=print)
    compare_args(...) / benchmark_args(...)                             # the same run as a command line

`say` gets each line the command line prints, as it comes (the app shows them in its output pane, from a
worker thread through App.post). Nothing here touches Tk. Both read the results and change neither; what
they write is Ionomos' own output (compare.* / benchmark*.* in a results folder, or the benchmark folder).

Exit codes, as the command line's: 0 done (compare: every comparison agrees), 1 compare: one differs or
could not be judged, 2 nothing could be run (`error` says why).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ionomos import names

Say = Callable[[str], None]
BY = ("auto", "id", "gene")
GRIDS = ("quick", "standard")


@dataclass
class Outcome:
    code: int                      # 0 / 1 / 2 as the command line exits
    page: Path | None = None       # the .html written (compare.html, benchmark*.html)
    error: str = ""                # why nothing was run (code 2): what the command line prints on stderr
    verdicts: list[str] = field(default_factory=list)  # one line per comparison / setting / species

    @property
    def ok(self) -> bool:
        return self.code == 0


def _quiet(_line: str) -> None:
    pass


# ------------------------------------------------------------------ compare --


def compare_problems(analysis: str, reference: str) -> list[str]:
    """What stops a compare before it starts (the app checks this before it starts a thread)."""
    from ionomos.downstream.compare import find_analysis

    out = []
    a, b = analysis.strip(), reference.strip()
    if not a:
        out.append("pick the analysed experiment (a folder with results/analysis.json)")
    elif not Path(a).is_dir():
        out.append(f"the experiment folder does not exist: {a}")
    elif find_analysis(Path(a)) is None:
        out.append(f"{a} holds no Ionomos analysis yet (no results/analysis.json): analyse it first")
    if not b:
        out.append("pick the reference: a results table (.tsv .csv .txt .xlsx) or another analysed folder")
    elif not Path(b).exists():
        out.append(f"the reference does not exist: {b}")
    return out


def compare_args(analysis, reference, by: str = "auto", flip: bool = False, out=None, comparison: str = "",
                 open_page: bool = False) -> list[str]:
    """The command line that runs the same compare (shown in the app; arguments, not a shell string)."""
    args = ["compare", str(analysis), str(reference)]
    if comparison:
        args += ["--comparison", comparison]
    if by != "auto":
        args += ["--by", by]
    if flip:
        args.append("--flip")
    if out:
        args += ["--out", str(out)]
    if open_page:
        args.append("--open")
    return args


def run_compare(analysis, reference, say: Say = _quiet, *, by: str = "auto", comparison: str | None = None,
                ref_comparison: str | None = None, flip: bool = False, alpha: float | None = None,
                log2fc: float | None = None, raw_p: bool = False, ref_alpha: float | None = None,
                ref_log2fc: float | None = None, out=None) -> Outcome:
    """`ionomos compare`: the analysis against the reference; compare.tsv / .json / .html into the analysis'
    results folder (or `out`)."""
    from ionomos.downstream import compare

    if by not in BY:
        return Outcome(2, error=f"match by must be one of {', '.join(BY)}")
    try:
        a = compare.load_side(Path(analysis))
        b = compare.load_side(Path(reference))
        if a.kind != "ionomos" and not out:
            return Outcome(2, error=f"{analysis} is a table, not an Ionomos analysis: say where the result goes with "
                                    "--out DIR (or give the analysed folder first)")
        res = compare.compare(a, b, by=by, comparison=comparison or None, ref_comparison=ref_comparison or None,
                              flip=flip, alpha=alpha, log2fc=log2fc, use_adjusted=False if raw_p else None,
                              ref_alpha=ref_alpha, ref_log2fc=ref_log2fc)
        files = compare.write(res, Path(out) if out else a.results_dir)
    except compare.CompareError as exc:
        return Outcome(2, error=f"cannot compare: {exc}")
    except OSError as exc:
        return Outcome(2, error=f"cannot write the comparison: {exc}")
    say(f"Ionomos: {a.name}   reference: {b.name} ({res['reference_kind']})")
    for line in compare.summary_lines(res):
        say(line)
    for n in res["notes"]:
        say(f"note: {n}")
    say(f"page: {files[-1]}")
    if a.kind == "ionomos" and not out:
        say("the verdict shows at the top of report.html after the next `ionomos analyze` of this folder")
    agrees = all(p["verdict"].startswith("agrees") for p in res["pairs"])
    return Outcome(0 if agrees else 1, files[-1], verdicts=list(res.get("verdicts") or []))


# ---------------------------------------------------------------- benchmark --


def default_benchmark_dir(log_dir=None) -> Path:
    """Where a simulated benchmark goes when nothing else is said: <log_dir>/ionomos_benchmark on the lab PC
    (C:/Fragpipe_Auto/logs: no spaces), else ./ionomos_benchmark as on the command line."""
    base = Path(log_dir) if log_dir and str(log_dir).strip() else Path.cwd()
    return base / names.BENCHMARK_DIR


def benchmark_problems(kind: str, folder: str = "", expected: str = "", like: str = "", grid: str = "quick") -> list[str]:
    """What stops a benchmark before it starts. kind: "simulated" | "real"."""
    from ionomos.downstream.compare import find_analysis

    out = []
    if kind == "real":
        if not folder.strip():
            out.append("pick the analysed benchmark experiment (the mixed-species or spike-in folder)")
        elif find_analysis(Path(folder.strip())) is None:
            out.append(f"{folder.strip()} holds no Ionomos analysis yet (no results/analysis.json): analyse it first")
        if not expected.strip():
            out.append("pick the expected-ratios file (.yaml), e.g. expected: {HUMAN: 1, YEAST: 2, ECOLI: 0.25}")
        elif not Path(expected.strip()).is_file():
            out.append(f"the expected-ratios file does not exist: {expected.strip()}")
    elif kind == "simulated":
        if grid not in GRIDS:
            out.append(f"the grid must be one of {', '.join(GRIDS)}")
        if like.strip() and find_analysis(Path(like.strip())) is None:
            out.append(f"{like.strip()} holds no Ionomos analysis (no results/analysis.json)")
    else:
        out.append(f"unknown benchmark {kind!r}")
    return out


def benchmark_args(kind: str, folder="", expected="", like="", grid: str = "standard", out=None,
                   open_page: bool = False) -> list[str]:
    """The command line that runs the same benchmark."""
    if kind == "real":
        args = ["benchmark", str(folder), "--expected", str(expected)]
    else:
        args = ["benchmark", "--grid", grid] + (["--like", str(like)] if like else [])
    if out:
        args += ["--out", str(out)]
    if open_page:
        args.append("--open")
    return args


def run_benchmark_real(folder, expected, say: Say = _quiet, out=None) -> Outcome:
    """`ionomos benchmark FOLDER --expected YAML`: benchmark.tsv / .json / .html into the results folder."""
    from ionomos.downstream import benchmark
    from ionomos.downstream.compare import find_analysis

    try:
        exp = benchmark.load_expected(expected)
        res = benchmark.real(Path(folder), exp)
        files = benchmark.write_real(res, Path(out) if out else find_analysis(Path(folder)))
    except benchmark.BenchmarkError as exc:
        return Outcome(2, error=f"cannot benchmark: {exc}")
    except OSError as exc:
        return Outcome(2, error=f"cannot write the benchmark: {exc}")
    say(f"{res['experiment']}, {res['comparison']}, against {res['expected_file']}")
    for line in res["verdicts"]:
        say(f"  {line}")
    for n in res["notes"]:
        say(f"  note: {n}")
    say(f"page: {files[-1]}")
    return Outcome(0, files[-1], verdicts=list(res["verdicts"]))


def run_benchmark_simulated(grid: str = "standard", say: Say = _quiet, *, like=None, seeds: int | None = None,
                            out=None, progress: Say | None = None, kind: str | None = None) -> Outcome:
    """`ionomos benchmark [--grid G] [--kind K] [--like FOLDER]`: the analysis on simulated data;
    benchmark_simulated[_<kind>].* into `out`, else the --like experiment's results folder, else
    ./ionomos_benchmark. `kind` is dia / isodtb / tmt (D66); with `like` it comes from the experiment."""
    from ionomos.downstream import benchmark

    extra, designs, alpha, log2fc, dest, analysis_info = None, None, 0.05, 1.0, default_benchmark_dir(), {}
    if like:
        try:
            lk = benchmark.like(Path(like))
        except (benchmark.BenchmarkError, OSError, ValueError) as exc:
            return Outcome(2, error=f"cannot read the analysis of {like}: {exc}")
        extra, alpha, log2fc, dest = [(lk["label"], lk["settings"])], lk["alpha"], lk["log2fc"], lk["results"]
        designs = [lk["design"]] if lk["design"] else None
        analysis_info = lk["analysis"]
        if kind and kind != lk["kind"]:
            return Outcome(2, error=f"{like} holds {lk['kind']} data ({benchmark.KINDS[lk['kind']]}); its settings "
                                    f"can't be benchmarked as {kind}. Leave out --kind")
        kind = lk["kind"]
    kind = kind or "dia"
    say(f"simulated benchmark, grid {grid}" + ("" if kind == "dia" else f", {kind} data") +
        (f", with the settings of {like}" if like else ""))
    try:
        res = benchmark.simulated(grid, extra, designs, seeds, alpha, log2fc, progress=progress, kind=kind)
        if like:
            res["analysis"] = analysis_info
            res["headline"] = [v for v in res["verdicts"] if v.startswith(extra[0][0] + ":")]
        files = benchmark.write_simulated(res, Path(out) if out else dest)
    except OSError as exc:
        return Outcome(2, error=f"cannot write the benchmark: {exc}")
    for line in res["verdicts"]:
        say(f"  {line}")
    say(f"page: {files[-1]}")
    return Outcome(0, files[-1], verdicts=list(res.get("headline") or res["verdicts"]))
