"""
What a folder of FragPipe output is, before it is analysed (D80).

    sc = scan(Path("D:/Ana/HeLa_DIA"), filed="TMT")
    sc.method        -> "DIA"                what to analyse it as
    sc.dest          -> the experiment folder results/ go in
    sc.workdir       -> the FragPipe output folder the tables are read from
    sc.findings      -> [Finding]            what is wrong, missing or guessed, and what to do about it
    sc.blocking      -> True when the analysis cannot run as things are

    workdir, output = locate(dest)           # the quick version postprocess.prepare uses on every analysis

Lab members still run FragPipe themselves and point the Analysis tab at what it wrote. That folder can be:
  - an Ionomos experiment (ionomos.json, fragpipe/, ionomos_run/), possibly filed as the wrong method
  - FragPipe's output folder itself (fragpipe.workflow, fragpipe-files.fp-manifest, log_<date>.txt, tables)
  - a folder holding one or several FragPipe output folders, with the raw files beside them
  - a part of one (tmt-report/, dia-quant-output/): the output folder above it is used

What the folder holds is decided from evidence, most trusted first:
  1. the tables actually there and readable (what can be analysed)
  2. the workflow FragPipe saved next to them (what the search was set up to do)
  3. the manifest's data types (DDA / DIA)
  4. what Ionomos filed the folder as (ionomos.json), which is only a name someone chose
so a DIA search in a folder filed as TMT is analysed as DIA, and the window says so.

Nothing here writes anything: it only reads names, headers, the ends of logs and FragPipe's small text files.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ionomos import names

# The FragPipe kinds the analysis reads (downstream._load_quantities), and the files that mean each.
# Patterns are tried in order; the first that matches is the table read.
TABLES: dict[str, tuple[str, ...]] = {
    "isoDTB": ("combined_modified_peptide_label_quant.tsv",),
    # TMT-Integrator names its tables after the normalisation chosen: _MD (median centring, the lab's), _GN, _None
    "TMT": ("abundance_gene_MD.tsv", "abundance_protein_MD.tsv", "abundance_*_MD.tsv", "abundance_gene_*.tsv",
            "abundance_protein_*.tsv", "abundance_*.tsv"),
    "DIA": ("report.pg_matrix.tsv", "*pg_matrix.tsv"),
    "DIA-NN": ("report.tsv", "report.parquet"),   # DIA-NN's long report: proteins are rolled up from it
    "LFQ": ("combined_protein.tsv",),
}
STRONG = ("isoDTB", "TMT", "DIA")       # a table only that kind of search writes
KIND_WORDS = {
    "isoDTB": "isoDTB (labelled cysteine sites)",
    "TMT": "TMT (TMT-Integrator)",
    "DIA": "DIA (DIA-NN protein matrix)",
    "DIA-NN": "DIA (DIA-NN precursor report)",
    "LFQ": "label-free DDA (IonQuant)",
}
WHAT_TABLE = {
    "isoDTB": "combined_modified_peptide_label_quant.tsv (IonQuant's labelled peptides)",
    "TMT": "tmt-report/abundance_gene_MD.tsv (TMT-Integrator's table)",
    "DIA": "dia-quant-output/report.pg_matrix.tsv or diann-output/report.pg_matrix.tsv (DIA-NN's protein matrix)",
    "DIA-NN": "report.tsv or report.parquet (DIA-NN's precursor report)",
    "LFQ": "combined_protein.tsv with Intensity columns (IonQuant)",
}
STEP_OF = {"isoDTB": "IonQuant (labelling)", "TMT": "TMT-Integrator", "DIA": "DIA-NN", "DIA-NN": "DIA-NN",
           "LFQ": "IonQuant (MS1 quantification)"}
MANIFEST = "fragpipe-files.fp-manifest"
MARKERS = ("fragpipe.workflow", MANIFEST, "fragpipe.job")
TABLE_DIRS = ("tmt-report", "diann-output", "dia-quant-output")   # FragPipe's sub-folders for the main tables
SKIP_DIRS = {"results", "__MACOSX", ".git", "$RECYCLE.BIN", names.RUN_DIR, *names.LEGACY_RUN_DIRS}
RAW_SUFFIXES = (".raw", ".mzml", ".mzxml", ".d", ".wiff", ".mgf")
MAX_DEPTH = 4                         # below the picked folder
MAX_DIRS = 4000                       # a whole drive picked by mistake must not hang the window

SEVERITY_ORDER = {"error": 0, "input": 1, "warning": 2, "info": 3}


@dataclass
class Run:
    """One line of FragPipe's manifest."""
    path: str
    experiment: str = ""
    replicate: str = ""
    data_type: str = ""

    @property
    def stem(self) -> str:
        from ionomos.downstream.quant import run_stem

        return run_stem(self.path)


@dataclass
class Output:
    """One FragPipe output folder and what is in it."""
    root: Path
    tables: dict[str, Path] = field(default_factory=dict)     # kind -> its main table (readable or not)
    broken: dict[str, str] = field(default_factory=dict)      # kind -> why its table can't be read
    workflow: Path | None = None
    props: dict[str, str] = field(default_factory=dict)
    manifest: Path | None = None
    runs: list[Run] = field(default_factory=list)
    log: Path | None = None
    log_text: str = ""

    @property
    def readable(self) -> list[str]:
        return [k for k in TABLES if k in self.tables and k not in self.broken]

    @property
    def asked(self) -> str | None:
        """What the saved workflow was set up to run (None without a workflow)."""
        return workflow_kind(self.props) if self.props else None

    @property
    def data_type(self) -> str:
        """DIA when the manifest lists any DIA run, DDA when it lists runs and none is DIA, '' without one."""
        types = {r.data_type.upper() for r in self.runs if r.data_type}
        if any(t.startswith(("DIA", "GPF-DIA")) for t in types):
            return "DIA"
        return "DDA" if types else ""

    def newest(self) -> float:
        times = []
        for p in [*self.tables.values(), self.log, self.workflow]:
            try:
                if p is not None:
                    times.append(p.stat().st_mtime)
            except OSError:
                pass
        return max(times, default=0.0)


@dataclass
class Finding:
    code: str
    severity: str           # error (can't run) | input (decide; a recommendation is pre-selected) | warning | info
    title: str
    message: str
    fix: str = ""           # what to do, in words
    actions: list[tuple[str, str]] = field(default_factory=list)   # (button text, action) the window offers

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "title": self.title, "message": self.message,
                "fix": self.fix, "actions": [list(a) for a in self.actions]}


@dataclass
class Scan:
    picked: Path
    dest: Path | None = None
    workdir: Path | None = None
    method: str | None = None
    recommended: str | None = None
    why: str = ""                           # why `recommended`, in words
    choices: list[str] = field(default_factory=list)      # what this folder can be analysed as
    outputs: list[Output] = field(default_factory=list)
    chosen: Output | None = None
    filed: str | None = None
    found: list[tuple[str, str]] = field(default_factory=list)   # (label, text) of what was found, for the window
    findings: list[Finding] = field(default_factory=list)
    guess: dict[str, str] = field(default_factory=dict)   # run stem -> condition, when the manifest names none

    @property
    def blocking(self) -> bool:
        return self.method is None or any(f.severity == "error" for f in self.findings)

    @property
    def needs_a_look(self) -> bool:
        return any(f.severity in ("error", "input") for f in self.findings)

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))

    def text(self) -> str:
        """Plain text of the whole check (`ionomos check-folder`, the log)."""
        lines = [f"Folder: {self.picked}"]
        lines += [f"  {label}: {text}" for label, text in self.found]
        if self.method:
            lines.append(f"Analyse as: {self.method}" + (f" ({self.why})" if self.why else ""))
        if self.dest:
            lines.append(f"Results go to: {self.dest / 'results'}")
        for f in self.sorted_findings():
            lines.append(f"[{f.severity}] {f.title}")
            lines += [f"    {ln}" for ln in f.message.splitlines()]
            if f.fix:
                lines.append(f"    -> {f.fix}")
        return "\n".join(lines)


# ---------------------------------------------------------------- reading --


def workflow_kind(props: dict[str, str]) -> str | None:
    """What a FragPipe workflow quantifies, from its run-* switches (fragpipe.workflow_needs reads the same keys)."""
    def on(key: str) -> bool:
        return props.get(key, "").strip().lower() == "true"

    if on("tmtintegrator.run-tmtintegrator"):
        return "TMT"
    if on("diann.run-dia-nn"):
        return "DIA"
    if on("quantitation.run-label-free-quant"):
        return "isoDTB" if on("ionquant.use-labeling") else "LFQ"
    return None


def read_manifest(path: Path) -> list[Run]:
    """FragPipe's .fp-manifest: path, experiment, bioreplicate, data type, tab-separated (blanks allowed)."""
    runs = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return runs
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")] + ["", "", ""]
        runs.append(Run(cells[0], cells[1], cells[2], cells[3]))
    return runs


def manifest_lines(runs: list[Run]) -> list[dict]:
    """The manifest as the lines of an Ionomos plan (file, experiment, bioreplicate), so a folder searched outside
    Ionomos gets the same sample names and conditions as one it searched. [] when FragPipe was given no experiment
    names (the analysis then reads the conditions from the file names)."""
    named = [r for r in runs if r.experiment]
    if not named or len(named) < len(runs):
        return []
    out, seen = [], {}
    for r in named:
        exp, rep = r.experiment, r.replicate
        if not rep.isdigit():
            m = re.match(r"^(.*?)[_-](\d+)$", exp)
            if m:   # "DMSO_2" with no bioreplicate: condition DMSO, replicate 2
                exp, rep = m.group(1), m.group(2)
        if not rep.isdigit():
            seen[exp] = seen.get(exp, 0) + 1
            rep = str(seen[exp])
        out.append({"file": r.path, "experiment": exp, "bioreplicate": int(rep)})
    return out


def _lfq_columns(path: Path) -> bool:
    from ionomos.downstream.tables import read_header

    try:
        header = read_header(path)
    except (OSError, UnicodeError, ValueError):
        return False
    return any(h.endswith(" Intensity") and not h.startswith(("Unique", "Total", "Razor")) for h in header)


def _pyarrow() -> bool:
    try:
        import pyarrow.parquet  # noqa: F401
    except ImportError:
        return False
    return True


def _table_problem(kind: str, path: Path) -> str:
    """Why this table can't be analysed ('' when it can)."""
    from ionomos.fragpipe import table_problem

    if path.suffix.lower() == ".parquet":
        return "" if _pyarrow() else "is a .parquet file, and the optional pyarrow package that reads it is not installed"
    why = table_problem(path)
    if why:
        return why
    if kind == "LFQ" and not _lfq_columns(path):
        return "holds no Intensity columns (MS1 quantification was off: only spectral counts)"
    return ""


def _walk(top: Path, depth: int = MAX_DEPTH):
    """(folder, its file names) breadth-first, skipping Ionomos' own folders and earlier attempts."""
    queue, seen = [(Path(top), 0)], 0
    while queue and seen < MAX_DIRS:
        d, level = queue.pop(0)
        seen += 1
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        files, subdirs = [], []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS and "_previous_" not in e.name and not e.name.startswith(".") \
                            and not e.name.lower().endswith(".d"):   # a Bruker run is a folder: never output
                        subdirs.append(Path(e.path))
                else:
                    files.append(e.name)
            except OSError:
                continue
        yield d, files
        if level < depth:
            queue += [(s, level + 1) for s in sorted(subdirs)]


def _match(name: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase

    return fnmatchcase(name.lower(), pattern.lower())


def _is_fragpipe_log(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return b"FragPipe" in head or b"fragpipe" in head


def _natural_root(table_dir: Path) -> Path:
    return table_dir.parent if table_dir.name.lower() in TABLE_DIRS else table_dir


def find_outputs(top: Path, depth: int = MAX_DEPTH) -> list[Output]:
    """Every FragPipe output folder at or below `top`: folders with FragPipe's own files (its saved workflow,
    manifest, job or log) and folders whose tables say a search wrote there. A table belongs to the nearest
    output folder above it."""
    top = Path(top)
    marked: dict[Path, list[str]] = {}
    tables: list[tuple[str, Path]] = []
    for d, files in _walk(top, depth):
        low = {f.lower(): f for f in files}
        if any(m in low for m in MARKERS) or any(_match(f, "log_*.txt") and _is_fragpipe_log(d / f) for f in files):
            marked[d] = files
        for kind, pats in TABLES.items():
            for f in files:
                if any(_match(f, p) for p in pats):
                    if kind == "DIA-NN" and d.name.lower() not in ("diann-output", "dia-quant-output") \
                            and not any(m in low for m in MARKERS):
                        continue  # a report.tsv elsewhere is anybody's; DIA-NN standalone output is engines.py's
                    tables.append((kind, d / f))
    roots: dict[Path, Output] = {d: Output(d) for d in marked}

    def owner(p: Path) -> Path:
        """The nearest output folder above a table (within top), else the folder FragPipe would have used."""
        for anc in (p.parent, *p.parent.parents):
            if anc in roots:
                return anc
            if anc == top:
                break
        return _natural_root(p.parent)

    for kind, p in tables:
        r = owner(p)
        out = roots.setdefault(r, Output(r))
        prev = out.tables.get(kind)
        if prev is None or _better(kind, p, prev, r):
            out.tables[kind] = p
    for out in roots.values():
        _fill(out)
    # a marker-only folder inside an output with tables is part of it (e.g. a copy of the workflow in a sub-folder)
    found = [o for o in roots.values()
             if o.tables or not any(a in roots and roots[a].tables for a in o.root.parents)]
    return sorted(found, key=lambda o: (len(o.root.parts), str(o.root)))


def _better(kind: str, new: Path, old: Path, root: Path) -> bool:
    """For two tables of one kind in one output: the one matching an earlier pattern, then the shallower."""
    pats = TABLES[kind]

    def rank(p: Path) -> tuple[int, int, str]:
        i = next((k for k, pat in enumerate(pats) if _match(p.name, pat)), len(pats))
        try:
            d = len(p.relative_to(root).parts)
        except ValueError:
            d = 99
        return i, d, str(p)

    return rank(new) < rank(old)


def _fill(out: Output) -> None:
    """The workflow, manifest, log and table problems of one output folder."""
    from ionomos import fragpipe

    root = out.root
    wf = root / "fragpipe.workflow"
    if not wf.is_file():
        wfs = sorted(root.glob("*.workflow"))
        wf = wfs[0] if wfs else None
    run_dir = None
    if root.name == fragpipe.WORKDIR:   # an Ionomos job: its inputs and the console log are beside it
        rd = names.run_dir(root.parent)
        run_dir = rd if rd.is_dir() else None
    if wf is None and run_dir is not None:
        wfs = sorted(run_dir.glob("*.workflow"))
        wf = wfs[0] if wfs else None
    if wf is not None:
        out.workflow = wf
        try:
            out.props = fragpipe.read_properties(wf.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            out.props = {}
    for cand in (root / MANIFEST, *(sorted(root.glob("*.fp-manifest"))),
                 *([run_dir / MANIFEST] if run_dir is not None else [])):
        if cand.is_file():
            out.manifest = cand
            out.runs = read_manifest(cand)
            break
    logs = [p for p in root.glob("log_*.txt") if _is_fragpipe_log(p)]
    if run_dir is not None and (run_dir / fragpipe.CONSOLE_LOG).is_file():
        logs.append(run_dir / fragpipe.CONSOLE_LOG)
    if logs:
        out.log = max(logs, key=lambda p: p.stat().st_mtime)
        out.log_text = fragpipe.attempt_text(fragpipe.read_tail_text(out.log))
    for kind, p in out.tables.items():
        why = _table_problem(kind, p)
        if why:
            out.broken[kind] = why


# --------------------------------------------------------------- deciding --


def decide(out: Output, filed: str | None = None) -> tuple[str | None, bool, str]:
    """(method, confident, why) for one output folder. confident: the evidence is strong enough to overrule what
    the folder was filed as without asking (a table only one kind of search writes, or the workflow agrees)."""
    readable = out.readable
    asked = out.asked
    if asked and asked in readable:
        return asked, True, f"FragPipe's workflow ran {KIND_WORDS[asked]} and its table is here"
    if asked == "DIA" and "DIA-NN" in readable:
        return "DIA-NN", True, "FragPipe's workflow ran DIA-NN; there is no protein matrix, so its report is read"
    strong = [k for k in STRONG if k in readable]
    if filed in strong:
        return filed, True, f"its {KIND_WORDS[filed]} table is here"
    if strong:
        return strong[0], True, f"the table here is {KIND_WORDS[strong[0]]}'s"
    if filed and filed in readable:
        return filed, True, f"its {KIND_WORDS.get(filed, filed)} table is here"
    if "DIA-NN" in readable and asked in (None, "DIA"):
        return "DIA-NN", out.data_type == "DIA" or asked == "DIA", "DIA-NN's precursor report is the only table here"
    if "LFQ" in readable and asked in (None, "LFQ"):
        sure = asked == "LFQ" or (out.data_type == "DDA" and filed in (None, "LFQ"))
        return "LFQ", sure, "the only table here is IonQuant's combined_protein.tsv"
    return None, False, ""


def locate(dest: Path) -> tuple[Path, Output | None]:
    """(the folder to read tables from, the FragPipe output there or None) for an experiment folder: its
    fragpipe/ (an Ionomos job), else the one FragPipe output found below it, else the folder itself."""
    dest = Path(dest)
    if (dest / "fragpipe").is_dir():
        outs = find_outputs(dest / "fragpipe", depth=2)
        return dest / "fragpipe", next((o for o in outs if o.root == dest / "fragpipe"), outs[0] if outs else None)
    outs = [o for o in find_outputs(dest) if o.tables]
    if len(outs) == 1:
        return outs[0].root, outs[0]
    return dest, None


def _filed(dest: Path) -> tuple[str | None, dict]:
    """(what Ionomos filed this folder as, read as its kind when intake recorded one; its status record)."""
    import json

    try:
        record = json.loads(names.status_path(dest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    key = ((record.get("plan") or {}).get("folder") or {}).get("method")
    return ((record.get("method_config") or {}).get("analysis_method") or key), record


def _rel(p: Path, base: Path) -> str:
    try:
        return p.relative_to(base).as_posix() or "."
    except ValueError:
        return str(p)


def _size(p: Path) -> str:
    try:
        n = p.stat().st_size
    except OSError:
        return "?"
    for unit in ("bytes", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return "?"


def _raw_count(top: Path) -> int:
    n = 0
    for _d, files in _walk(top, 2):
        n += sum(1 for f in files if f.lower().endswith(RAW_SUFFIXES))
    try:   # Bruker .d runs are folders
        n += sum(1 for p in Path(top).iterdir() if p.is_dir() and p.suffix.lower() == ".d")
    except OSError:
        pass
    return n


def scan(picked: Path, filed: str | None = None, method: str | None = None, root: Path | None = None) -> Scan:
    """Check a folder (or a file in one) before analysing it. filed: the method kind the folder was filed as
    (from ionomos.json, or the job's); method: what the person chose in the window (None = recommend);
    root: the FragPipe output they picked when there are several."""
    picked = Path(picked)
    sc = Scan(picked=picked, filed=filed)
    if picked.is_file():   # a FragPipe file dropped: its folder
        picked = sc.picked = picked.parent
    if not picked.is_dir():
        sc.findings.append(Finding("NOT_FOUND", "error", "This folder doesn't exist",
                                   f"{picked} can't be found (moved, renamed, or a drive that isn't connected).",
                                   "Choose the folder again.", [("Choose another folder…", "choose")]))
        return sc
    status_method, _record = _filed(picked)
    if filed is None and status_method:
        sc.filed = filed = status_method
    outs = find_outputs(picked)
    if not outs:   # picked inside an output folder (tmt-report/, diann-output/): the output folder above
        for up in list(picked.parents)[:2]:
            if any((up / m).is_file() for m in MARKERS) or picked.name.lower() in TABLE_DIRS:
                found = [o for o in find_outputs(up, depth=2) if o.root == up]
                if found:
                    outs = found
                    sc.findings.append(Finding(
                        "PARENT_USED", "info", "Using the FragPipe output folder above",
                        f"{picked.name}/ is part of FragPipe's output; the whole output folder {up} is analysed."))
                    picked = up
                    break
    sc.outputs = outs
    if not outs:
        return _nothing_here(sc, picked)

    with_tables = [o for o in outs if o.tables]
    pool = with_tables or outs
    chosen = None
    if root is not None:
        chosen = next((o for o in pool if o.root == Path(root)), None)
    if chosen is None:
        chosen = max(pool, key=lambda o: (bool(o.readable), o.newest()))
    sc.chosen = chosen
    if chosen.root in picked.parents:  # picked a part of the output (its table, tmt-report/, dia-quant-output/)
        sc.findings.append(Finding(
            "PARENT_USED", "info", "Using the FragPipe output folder above",
            f"{picked.name} is part of FragPipe's output; the whole output folder {chosen.root} is analysed."))
        picked = sc.picked = chosen.root
    if len(pool) > 1:
        lines = []
        for o in pool:
            m, _sure, _w = decide(o, filed)
            when = datetime.fromtimestamp(o.newest()).strftime("%Y-%m-%d %H:%M") if o.newest() else "?"
            what = KIND_WORDS.get(m, m) if m else "no readable table"
            lines.append(f"• {_rel(o.root, picked)}  —  {what}, {when}")
        sc.findings.append(Finding(
            "SEVERAL_OUTPUTS", "input", f"{len(pool)} FragPipe outputs are in this folder",
            "Each is analysed on its own; the newest readable one is selected:\n" + "\n".join(lines),
            "Pick the one to analyse in the list 'FragPipe output' above.",
            [(f"Use {_rel(o.root, picked)}", f"root:{o.root}") for o in pool if o is not chosen][:4]))

    # where results go: the picked folder when it holds exactly this one output (results/ beside the raw files),
    # else the output folder itself, so a re-run finds the same one
    if len(with_tables) <= 1 and (chosen.root == picked or picked in chosen.root.parents):
        sc.dest = picked
    else:
        sc.dest = chosen.root.parent if chosen.root.name == "fragpipe" and \
            names.status_path(chosen.root.parent).is_file() else chosen.root
    sc.workdir = chosen.root
    _describe(sc, chosen, picked)
    _judge(sc, chosen, filed, method)
    if sc.method in ("DIA", "DIA-NN", "LFQ"):
        _conditions(sc, chosen)
    _writable(sc)
    _previous(sc)
    return sc


def _nothing_here(sc: Scan, picked: Path) -> Scan:
    """No FragPipe output: another engine's results, a table, raw files waiting for a search, or nothing."""
    from ionomos.downstream import anytable, engines

    found = engines.detect(picked)
    if found:
        sc.dest = sc.workdir = picked
        sc.method = sc.recommended = found.method
        sc.choices = [found.method]
        sc.why = f"{found.engine}'s {found.path.name} is here"
        sc.found.append(("Results", f"{found.engine}: {_rel(found.path, picked)} ({_size(found.path)})"))
        sc.findings.append(Finding("NOT_FRAGPIPE", "info", f"These are {found.engine} results, not FragPipe's",
                                   f"{_rel(found.path, picked)} is read as {found.engine} output."))
        _writable(sc)
        _previous(sc)
        return sc
    table = anytable.find_table(picked)
    if table is not None:
        sc.dest = sc.workdir = picked
        sc.method = sc.recommended = "table"
        sc.choices = ["table"]
        sc.why = f"{table.name} looks like a protein or results table"
        sc.found.append(("Table", f"{_rel(table, picked)} ({_size(table)})"))
        sc.findings.append(Finding(
            "ANY_TABLE", "warning", "No FragPipe output: a table is read instead",
            f"No FragPipe output folder was found, but {_rel(table, picked)} looks like a protein or results table. "
            "It is read as it is: check the samples and conditions before running.",
            "If this folder should hold FragPipe output, pick the folder FragPipe wrote to instead.",
            [("Choose another folder…", "choose")]))
        _writable(sc)
        _previous(sc)
        return sc
    raws = _raw_count(picked)
    try:
        shown = sorted(p.name + ("/" if p.is_dir() else "") for p in picked.iterdir())[:12]
    except OSError:
        shown = []
    listing = ("It holds: " + ", ".join(shown) + (" …" if len(shown) == 12 else "")) if shown else "It is empty."
    if raws:
        sc.findings.append(Finding(
            "RAW_ONLY", "error", f"Raw files, but no search results ({raws} raw file{'s' if raws != 1 else ''})",
            f"{picked.name} has raw files but nothing FragPipe wrote: no fragpipe.workflow, no log_….txt, no result "
            f"tables. {listing}",
            "Search them first: drop the folder in the Ionomos inbox (Ionomos runs FragPipe), or run FragPipe and "
            "pick its output folder here.", [("Choose another folder…", "choose"), ("Open folder", f"open:{picked}")]))
    else:
        sc.findings.append(Finding(
            "NO_OUTPUT", "error", "No FragPipe results in this folder",
            "Nothing here looks like FragPipe output (fragpipe.workflow, fragpipe-files.fp-manifest, log_….txt) or a "
            "result table it writes: " + ", ".join(WHAT_TABLE[k] for k in ("DIA", "TMT", "isoDTB", "LFQ")) +
            f". {listing}",
            "Pick the folder FragPipe wrote to (FragPipe's 'Output dir' on its Workflow tab) or the folder above it.",
            [("Choose another folder…", "choose"), ("Open folder", f"open:{picked}")]))
    return sc


def _describe(sc: Scan, o: Output, picked: Path) -> None:
    from ionomos import fragpipe

    ver = o.props.get("workflow.saved-with-ver") or o.props.get("fragpipe.version") or ""
    sc.found.append(("FragPipe output", f"{_rel(o.root, picked)}" + (f" (FragPipe {ver})" if ver else "")))
    if o.workflow is not None:
        asked = o.asked
        sc.found.append(("Workflow", f"{o.workflow.name}: " + (KIND_WORDS.get(asked, "?") if asked else
                                                                "no quantification step switched on")))
    if o.runs:
        exps = list(dict.fromkeys(r.experiment for r in o.runs if r.experiment))
        dtype = o.data_type
        sc.found.append(("Manifest", f"{len(o.runs)} run{'s' if len(o.runs) != 1 else ''}"
                                     + (f", {dtype}" if dtype else "")
                                     + (f", {len(exps)} experiment name{'s' if len(exps) != 1 else ''}" if exps else
                                        ", no experiment names")))
    for kind in TABLES:
        if kind in o.tables:
            p = o.tables[kind]
            state = f" — can't be read: {o.broken[kind]}" if kind in o.broken else ""
            sc.found.append((f"{kind} table", f"{_rel(p, o.root)} ({_size(p)}){state}"))
    if o.log is not None:
        facts = fragpipe.console_facts(o.log_text)
        if facts["all_jobs_done_minutes"]:
            how = f"finished (all steps done in {facts['all_jobs_done_minutes']} min)"
        elif fragpipe.failed_step(o.log_text):
            how = f"step {fragpipe.failed_step(o.log_text)[0]} failed"
        else:
            how = "no 'ALL JOBS DONE' line"
        sc.found.append(("Log", f"{o.log.name}: {how}"))


def _log_reason(o: Output) -> tuple[str, list[str]]:
    """What FragPipe's log says went wrong ('' when it says nothing), with the plain-English causes."""
    from ionomos import fragpipe

    if o.log is None:
        return "", []
    text = o.log_text
    bad = fragpipe.failed_step(text)
    hints = fragpipe.explain(text)
    if bad:
        name, code, said = bad
        return f"FragPipe's log says step {name} failed (exit code {code})" + (f": {said}" if said else ""), hints
    facts = fragpipe.console_facts(text)
    if facts["dry_run"]:
        return "FragPipe's log shows only a dry run: nothing was searched", hints
    if facts["cancelled_tasks"]:
        return f"FragPipe's log says it cancelled {facts['cancelled_tasks']} remaining step(s)", hints
    if facts["error_lines"]:
        return "FragPipe's log has errors: " + " | ".join(facts["error_lines"][:2]), hints
    if not facts["all_jobs_done_minutes"]:
        return ("FragPipe's log ends without its 'ALL JOBS DONE' line: the search was stopped, crashed or is still "
                "running"), hints
    return "", hints


def _judge(sc: Scan, o: Output, filed: str | None, forced: str | None) -> None:
    """The method to analyse as, and every problem with the tables, the workflow and what it was filed as."""
    rec, sure, why = decide(o, filed)
    sc.recommended, sc.why = rec, why
    sc.choices = list(o.readable)
    asked = o.asked
    log_why, hints = _log_reason(o)
    log_action = [("Open FragPipe's log", f"open:{o.log}")] if o.log is not None else []

    for kind, why_broken in o.broken.items():
        p = o.tables[kind]
        sev = "error" if kind in (asked, rec, filed) or not o.readable else "warning"
        fix = {"DIA-NN": "Install pyarrow (pip install pyarrow) or switch on DIA-NN's matrices in FragPipe "
                         "(it then writes report.pg_matrix.tsv)."}.get(kind) if p.suffix.lower() == ".parquet" else None
        sc.findings.append(Finding(
            f"TABLE_BROKEN_{kind}", sev, f"{p.name} can't be read",
            f"{_rel(p, o.root)} {why_broken}." + (f"\n{log_why}." if log_why else ""),
            fix or ("Re-run the last FragPipe steps (or the whole search) and make sure it finishes; check there is "
                    "free disk space."), log_action))

    if asked and asked not in o.readable and not (asked == "DIA" and "DIA-NN" in o.readable):
        missing = WHAT_TABLE[asked]
        msg = (f"The saved workflow ({o.workflow.name if o.workflow else '?'}) ran {KIND_WORDS[asked]}, but its "
               f"table is not here: expected {missing}.")
        if log_why:
            msg += f"\n{log_why}."
        elif o.log is None:
            msg += "\nThere is no FragPipe log here to say why (FragPipe writes log_<date>.txt when it ends)."
        if hints:
            msg += "\nLikely cause: " + hints[0]
        sev = "error" if rec is None else "warning"
        sc.findings.append(Finding(
            "STEP_MISSING", sev, f"FragPipe's {STEP_OF[asked]} step left no table",
            msg + (f"\nA {KIND_WORDS[rec]} table is here and can be analysed instead." if rec else ""),
            "Open FragPipe's log to see where it stopped, fix that and re-run FragPipe (only the last steps "
            "need to run again if MSFragger's results are kept).", log_action))
    elif log_why and rec is not None and not o.broken:
        sc.findings.append(Finding("FRAGPIPE_LOG", "warning", "FragPipe's log reports a problem",
                                   log_why + "." + (f"\nLikely cause: {hints[0]}" if hints else ""),
                                   "Check the log before trusting these results.", log_action))

    if rec is None and not o.broken and not any(f.code == "STEP_MISSING" for f in sc.findings):
        what = ", ".join(_rel(p, o.root) for p in o.tables.values()) or "none"
        sc.findings.append(Finding(
            "NO_TABLE", "error", "FragPipe ran here, but left no table to analyse",
            f"Tables found: {what}. Ionomos needs one of: " +
            "; ".join(WHAT_TABLE[k] for k in ("DIA", "TMT", "isoDTB", "LFQ")) + "." +
            (f"\n{log_why}." if log_why else "") + (f"\nLikely cause: {hints[0]}" if hints else ""),
            "Re-run FragPipe with a quantification step switched on (DIA-NN for DIA, TMT-Integrator for TMT, "
            "IonQuant 'MS1 quant' for label-free / isoDTB) and let it finish.", log_action))

    if filed and rec and filed != rec and filed in TABLES and not (filed == "DIA" and rec == "DIA-NN"):
        evidence = []
        if asked:
            evidence.append(f"FragPipe's saved workflow ran {KIND_WORDS[asked]}")
        if o.data_type:
            evidence.append(f"the manifest lists the runs as {o.data_type}")
        evidence.append(f"the table here is {_rel(o.tables[rec], o.root)}")
        sc.findings.append(Finding(
            "FILED_AS_OTHER", "input", f"Filed as {filed}, but this is a {rec if rec != 'DIA-NN' else 'DIA'} search",
            f"Ionomos filed this folder as {filed}, so it looked for {WHAT_TABLE.get(filed, filed)} — which isn't "
            f"here. But " + ", ".join(evidence) + f". It will be analysed as {KIND_WORDS[rec]}.",
            f"Keep {rec} (recommended)." + (f" If it really is {filed}, that search's table is missing: re-run "
                                            f"FragPipe with the {filed} workflow." if filed in STRONG else ""),
            [(f"Analyse as {rec}", f"method:{rec}")]))
    elif rec and not sure:
        sc.findings.append(Finding(
            "METHOD_GUESSED", "input", f"Analysed as {KIND_WORDS[rec]}: please check",
            f"There is no saved workflow saying what this search was, and {why}.",
            "If that is wrong, choose the right kind under 'Analyse as'.",
            [(f"Analyse as {k}", f"method:{k}") for k in o.readable if k != rec][:3]))

    if asked == "DIA" and rec == "DIA-NN":
        sc.findings.append(Finding(
            "DIA_REPORT_ONLY", "warning", "No DIA-NN protein matrix: proteins are rolled up from the report",
            f"There is no report.pg_matrix.tsv, so proteins are built from DIA-NN's precursor report "
            f"({o.tables['DIA-NN'].name}) with its PG.MaxLFQ values. The numbers can differ a little from the matrix.",
            "To use DIA-NN's own protein matrix, switch on its matrices in FragPipe and re-run the DIA-NN step."))

    if o.data_type == "DIA" and rec in ("TMT", "isoDTB", "LFQ"):
        sc.findings.append(Finding(
            "DATA_TYPE", "warning", f"The manifest says DIA, the table is {rec}",
            f"FragPipe's manifest lists the runs as DIA, but the readable table is {KIND_WORDS[rec]}'s.",
            "Check that the right folder was picked."))

    sc.method = rec
    if forced:
        if forced in o.readable:
            sc.method = forced
            if rec and forced != rec:
                sc.findings.append(Finding(
                    "METHOD_CHOSEN", "warning", f"Analysed as {forced}, not the recommended {rec}",
                    f"You chose {KIND_WORDS.get(forced, forced)}; Ionomos recommends {KIND_WORDS[rec]} because "
                    f"{why}.", "", [(f"Use {rec} (recommended)", f"method:{rec}")]))
        else:
            sc.method = None
            sc.findings.append(Finding(
                "METHOD_UNREADABLE", "error", f"Nothing here can be read as {forced}",
                f"{KIND_WORDS.get(forced, forced)} needs {WHAT_TABLE.get(forced, 'its table')}, which isn't in "
                f"{_rel(o.root, sc.picked)}.", "Choose one of the kinds offered under 'Analyse as'.",
                [(f"Use {rec} (recommended)", f"method:{rec}")] if rec else []))

    if sc.method == "TMT":
        _tmt_names(sc, o)


def _tmt_names(sc: Scan, o: Output) -> None:
    from ionomos.downstream import tmt
    from ionomos.downstream.tables import read_header

    try:
        header = read_header(o.tables["TMT"])
    except (OSError, UnicodeError, ValueError):
        return
    if not tmt.annotation_rows(header):
        sc.findings.append(Finding(
            "TMT_NAMES", "input", "TMT sample names don't say their conditions",
            "The TMT channels are not named condition_replicate_channel (e.g. DMSO_1_126), so each channel's "
            "condition is guessed from the text before its first '_'.",
            "Check the conditions in the samples list after loading, and change any that are wrong. Next time, give "
            "the channels names in FragPipe's annotation file.", [("Guess conditions from the names", "guess")]))


def _conditions(sc: Scan, o: Output) -> None:
    from ionomos.downstream.doctor import suggest_conditions

    runs = o.runs
    if not runs:
        return
    exps = [r.experiment for r in runs]
    named = [e for e in exps if e]
    if not named or (len(set(named)) == 1 and len(runs) > 1):
        guess = suggest_conditions([r.stem for r in runs])
        groups: dict[str, int] = {}
        for c in guess.values():
            groups[c] = groups.get(c, 0) + 1
        why = ("FragPipe's manifest gives the runs no experiment names" if not named else
               f"FragPipe's manifest puts all {len(runs)} runs in one experiment ('{named[0]}')")
        if len(groups) >= 2:
            sc.guess = guess
            sc.findings.append(Finding(
                "NO_CONDITIONS", "input", "The conditions come from the file names",
                f"{why}, so the conditions are guessed from the file names: " +
                ", ".join(f"{c} ({n})" for c, n in groups.items()) + ".",
                "Check them in the samples list after loading; change any that are wrong.",
                [("Use these conditions", "guess")]))
        else:
            sc.findings.append(Finding(
                "NO_CONDITIONS", "error" if len(runs) > 1 else "warning", "Every run is in one condition",
                f"{why}, and the file names don't tell the conditions apart, so there is nothing to compare.",
                "Load the samples and type each one's condition (select several to change them together).",
                [("Set conditions…", "review")]))
        return
    counts: dict[str, int] = {}
    for e in named:
        counts[e] = counts.get(e, 0) + 1
    single = [e for e, n in counts.items() if n == 1]
    if len(counts) >= 2 and single and len(single) < len(counts):
        sc.findings.append(Finding(
            "ONE_RUN", "warning", f"{len(single)} condition{'s have' if len(single) != 1 else ' has'} a single run",
            f"{', '.join(single[:6])}: one run each. A condition needs 2 runs (3 to be useful) to be tested.",
            "Check the experiment names in FragPipe's manifest, or merge these runs into a condition in the samples "
            "list."))
    elif len(counts) == len(named) and len(named) > 1:   # every run its own experiment: replicates as experiments
        guess = suggest_conditions([r.stem for r in runs])
        if len(set(guess.values())) >= 2 and len(set(guess.values())) < len(runs):
            sc.guess = guess
            sc.findings.append(Finding(
                "EXPERIMENT_PER_RUN", "input", "Each run is its own experiment in FragPipe's manifest",
                "Every run was given its own experiment name, so each would be a condition of one. From the file "
                "names they group as: " + ", ".join(sorted(set(guess.values()))) + ".",
                "Use the conditions from the file names, then check them in the samples list.",
                [("Use these conditions", "guess")]))


def _writable(sc: Scan) -> None:
    dest = sc.dest
    if dest is None:
        return
    res = dest / "results"
    target = res if res.is_dir() else dest
    if not os.access(target, os.W_OK):
        sc.findings.append(Finding(
            "READ_ONLY", "error", "Ionomos can't write the results here",
            f"{target} is read-only for you (a shared drive, another person's folder or a CD/USB copy). The analysis "
            "writes a results/ folder there.",
            "Copy the folder to your own folder (e.g. under C:\\Fragpipe_General\\<you>\\) and analyse the copy.",
            [("Open folder", f"open:{dest}")]))


def _previous(sc: Scan) -> None:
    rep = sc.dest / "results" / "report.html" if sc.dest else None
    if rep is not None and rep.is_file():
        when = datetime.fromtimestamp(rep.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        sc.found.append(("Analysed before", f"{when} — running again updates results/"))
