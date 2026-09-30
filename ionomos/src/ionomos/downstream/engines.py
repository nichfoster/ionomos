"""
Search engines as adapters: which engine made the results in a folder, how to load them, and what it
was run with (D36).

    found = detect(workdir)            # best Detected(engine, method, path, score) or None
    m, notes = load(found)             # QuantMatrix, for engines without a dedicated loader in quant.py
    prov = provenance(workdir, method) # {"engine", "version", "files", "quantity", "fdr", ...} for the report

Each adapter recognises its outputs by file name and header, with a confidence score, so a folder of
results from any supported engine can be analysed with `ionomos analyze <folder>`:

    FragPipe            isoDTB label quant, TMT-Integrator, DIA-NN inside FragPipe, IonQuant combined_protein
                        (loaded by quant.py / isodtb.py / tmt.py; detected in downstream.detect_method)
    DIA-NN (standalone) *pg_matrix.tsv (as FragPipe's DIA), or the long report: report.tsv (1.x) or
                        report.parquet (2.x, needs the optional pyarrow package)
    MaxQuant            combined/txt/proteinGroups.txt: LFQ intensity, else Intensity, or Reporter intensity
                        corrected (TMT); reverse / contaminant / only-by-site rows removed
    Spectronaut         a PG pivot report (<run>.PG.Quantity) or the long BGS report (R.FileName, R.Condition,
                        R.Replicate, PG.ProteinGroups, PG.Quantity)
    AlphaDIA            pg.matrix.tsv
    MSstats format      long ProteinName / PeptideSequence / PrecursorCharge / FragmentIon / ProductCharge /
                        IsotopeLabelType / Condition / BioReplicate / Run / Intensity (quantms, Skyline, and any
                        engine with an MSstats converter): proteins summarised by Tukey median polish
    MSstatsTMT format   long ProteinName / PeptideSequence / Charge / PSM / Mixture / TechRepMixture / Run / Channel /
                        Condition / BioReplicate / Intensity: summarised as MSstatsTMT's proteinSummarization
                        (MedianPolish), one sample per mixture and channel, plexes on the Norm channels' scale
    Proteome Discoverer a Proteins table exported as text (Abundance / Abundances (Normalized) columns)
    any table           the fallback (anytable.py, D33)

Ionomos never runs or ships these engines here; it only reads what they wrote. Nothing is written into the
engine's folder.
"""
from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ionomos.downstream import anytable
from ionomos.downstream.quant import Feature, QuantMatrix, run_stem
from ionomos.downstream.tables import num

SKIP_DIRS = {"results", "ionomos_run", "__MACOSX", "fragpipe-analyst"}
LONG_FDR = 0.01  # precursor and protein-group q-value filter for long reports (the engines' own default)


@dataclass
class Detected:
    engine: str             # "DIA-NN", "MaxQuant", ... (shown in the report)
    method: str             # the key load_quantities() dispatches on
    path: Path              # the table to read
    score: float            # 0..1, how sure
    details: dict = field(default_factory=dict)


# ------------------------------------------------------------------ helpers --


def _files(root: Path, depth: int = 4):
    root = Path(root)
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*")):
        try:
            rel = p.relative_to(root).parts
        except ValueError:
            continue
        if len(rel) > depth or any(x in SKIP_DIRS or "_previous_" in x for x in rel[:-1]):
            continue
        if p.is_file():
            yield p


def _header(path: Path) -> list[str]:
    """First line of a text table, split on its delimiter ('' on failure)."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            line = fh.readline()
    except OSError:
        return []
    delim = "\t" if line.count("\t") >= max(line.count(","), line.count(";")) else (
        "," if line.count(",") >= line.count(";") else ";")
    if "\t" in line and Path(path).suffix.lower() in (".tsv", ".txt", ".tab"):  # headers with commas (PD TMT)
        delim = "\t"
    return [h.strip().strip('"') for h in line.rstrip("\r\n").split(delim)]


def _read_long(path: Path, want: list[str]) -> tuple[list[str], list[list[str]]]:
    """A long table (tsv/csv or parquet): only the wanted columns that exist, in that order."""
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq  # optional: DIA-NN 2.x / AlphaDIA parquet reports
        except ImportError as exc:
            raise anytable.TableError(
                f"{path.name} is a Parquet file; reading it needs the optional package pyarrow "
                "(pip install pyarrow), or re-run DIA-NN with --matrices to also get report.pg_matrix.tsv") from exc
        names = pq.ParquetFile(path).schema_arrow.names
        cols = [c for c in want if c in names]
        table = pq.read_table(path, columns=cols)
        data = [table.column(c).to_pylist() for c in cols]
        rows = [["" if v is None else str(v) for v in r] for r in zip(*data, strict=True)]
        return cols, rows
    header, rows = anytable.read_table(path)
    idx = [header.index(c) for c in want if c in header]
    return [header[j] for j in idx], [[r[j] if j < len(r) else "" for j in idx] for r in rows]


def _log2(v: float | None) -> float | None:
    return math.log2(v) if v is not None and v > 0 else None


def _matrix_from_long(path: Path, feats: dict[str, Feature], cells: dict[tuple[str, str], float],
                      runs: list[str], cond: dict[str, str], rep: dict[str, int], exp: str,
                      notes: list[str], meta: dict) -> QuantMatrix:
    """(feature id, run) -> linear quantity, as a QuantMatrix of log2 values (runs renamed to samples)."""
    from ionomos.downstream.doctor import suggest_conditions

    ids = list(feats)
    samples, colmap = [], {}
    for r in runs:
        base = r
        if r in cond and r in rep:
            base = f"{cond[r]}_{rep[r]}"
        s, k = base, 2
        while s in colmap:
            s, k = f"{base}.{k}", k + 1
        samples.append(s)
        colmap[s] = r
    if not cond:
        guessed = suggest_conditions(samples)
        cond_s = {s: guessed[s] for s in samples}
    else:
        cond_s = {s: cond.get(colmap[s], s) for s in samples}
    reps = {s: rep[colmap[s]] for s in samples if colmap[s] in rep}
    for s in samples:
        if s not in reps and s.rsplit("_", 1)[-1].isdigit():
            reps[s] = int(s.rsplit("_", 1)[1])
    values = [[_log2(cells.get((i, colmap[s]))) for s in samples] for i in ids]
    if cond:
        meta = {**meta, "conditions_from": "engine"}
    return QuantMatrix("intensity", "protein", [feats[i] for i in ids], samples, values, cond_s, str(path),
                       notes=notes, exp=exp, replicate=reps, columns=colmap, meta=meta)


# ------------------------------------------------------------------ DIA-NN --


def _diann_long_score(path: Path) -> float:
    if path.suffix.lower() == ".parquet":  # the name is the signal; the columns are checked when it is read
        return 0.9
    h = set(_header(path))
    need = {"Run", "Protein.Group", "Precursor.Id"}
    if not need <= h:
        return 0.0
    return 0.9 if ("PG.MaxLFQ" in h or "PG.Quantity" in h) else 0.5


def load_diann_long(path: Path) -> QuantMatrix:
    """DIA-NN's long report -> protein matrix: one PG.MaxLFQ per run and protein group, precursors at
    Q.Value <= 1% and protein groups at PG.Q.Value <= 1% (DIA-NN's matrices use the same 1%)."""
    want = ["Run", "Protein.Group", "Protein.Ids", "Genes", "Protein.Names", "First.Protein.Description",
            "PG.MaxLFQ", "PG.Quantity", "Q.Value", "PG.Q.Value", "Global.PG.Q.Value", "Precursor.Id"]
    cols, rows = _read_long(path, want)
    ix = {c: j for j, c in enumerate(cols)}
    qcol = "PG.MaxLFQ" if "PG.MaxLFQ" in ix else "PG.Quantity" if "PG.Quantity" in ix else None
    if qcol is None or "Run" not in ix or "Protein.Group" not in ix:
        raise anytable.TableError(f"{path.name}: not a DIA-NN report (needs Run, Protein.Group and PG.MaxLFQ)")
    feats: dict[str, Feature] = {}
    cells: dict[tuple[str, str], float] = {}
    peps: dict[str, set[str]] = {}
    runs: list[str] = []
    seen_runs: set[str] = set()
    dropped = 0
    for r in rows:
        q, pgq = num(r[ix["Q.Value"]]) if "Q.Value" in ix else None, num(r[ix["PG.Q.Value"]]) if "PG.Q.Value" in ix else None
        if (q is not None and q > LONG_FDR) or (pgq is not None and pgq > LONG_FDR):
            dropped += 1
            continue
        run, pg = r[ix["Run"]], r[ix["Protein.Group"]]
        if not pg:
            continue
        if run not in seen_runs:
            seen_runs.add(run)
            runs.append(run)
        if pg not in feats:
            genes = r[ix["Genes"]] if "Genes" in ix else ""
            name = r[ix["Protein.Names"]] if "Protein.Names" in ix else ""
            feats[pg] = Feature(id=pg, label=(genes or name or pg).split(";")[0],
                                description=r[ix["First.Protein.Description"]] if "First.Protein.Description" in ix else "")
        v = num(r[ix[qcol]])
        if v is not None and v > 0:
            cells[(pg, run)] = v
        if "Precursor.Id" in ix:
            peps.setdefault(pg, set()).add(r[ix["Precursor.Id"]])
    for pg, f in feats.items():
        f.peptides = len(peps.get(pg, ())) or None
    notes = [f"DIA-NN long report {path.name}: protein quantity {qcol}, precursors and protein groups at "
             f"q ≤ {LONG_FDR:g}" + (f" ({dropped:,} rows above it left out)" if dropped else "")]
    runs_clean = {r: run_stem(r) for r in runs}
    m = _matrix_from_long(path, feats, {(i, runs_clean[r]): v for (i, r), v in cells.items()},
                          [runs_clean[r] for r in runs], {}, {}, "DIA", notes,
                          {"engine": "DIA-NN", "evidence": "precursors"})
    return m


# ---------------------------------------------------------------- MaxQuant --


def _maxquant_score(path: Path) -> float:
    if path.name != "proteinGroups.txt":
        return 0.0
    h = _header(path)
    return 0.95 if "Majority protein IDs" in h or any(x.startswith("LFQ intensity ") for x in h) else 0.6


def load_maxquant(path: Path) -> QuantMatrix:
    m = anytable.load(path)
    header = _header(path)
    reporter = any(h.startswith("Reporter intensity corrected ") for h in header) and \
        not any(h.startswith("LFQ intensity ") for h in header)
    m.exp = "TMT" if reporter else "LFQ"
    m.meta["engine"] = "MaxQuant"
    pepc = next((h for h in ("Razor + unique peptides", "Peptides", "Unique peptides") if h in header), None)
    if pepc:
        head, rows = anytable.read_table(path)
        ix = {h: j for j, h in enumerate(head)}
        idc = anytable._id_column(head, rows)
        flags = [f for f in anytable._MQ_FLAGS if f in ix]
        kept = [r for r in rows if not any(r[ix[f]].strip() == "+" for f in flags)]
        by = {r[ix[idc]].strip(): r[ix[pepc]] for r in kept}
        for f in m.features:
            v = num(by.get(f.id))
            f.peptides = int(v) if v is not None and v == int(v) else None
        m.meta["evidence"] = "peptides"
    fam = "Reporter intensity corrected" if reporter else (
        "LFQ intensity" if any(h.startswith("LFQ intensity ") for h in header) else "Intensity")
    m.meta["quantity"] = fam
    if reporter:
        m = _maxquant_plexes(m, path)
    return m


_MQ_REPORTER = re.compile(r"^Reporter intensity corrected (\d+)(?: (.+))?$")


def _maxquant_plexes(m: QuantMatrix, path: Path) -> QuantMatrix:
    """MaxQuant TMT: 'Reporter intensity corrected 3 Exp1' is channel 3 of experiment (plex) Exp1. With several
    experiments MaxQuant also writes the channel totals over experiments ('... corrected 3'): those are left out.
    Channels are numbered in kit order (from 0 in old versions); combined/txt/summary.txt names each
    experiment's raw files, which the SDRF import matches."""
    from ionomos.downstream.plex import channel_from_index, keep_samples

    hit = {s: _MQ_REPORTER.match(m.columns.get(s, "")) for s in m.samples}
    if any(x and x.group(2) for x in hit.values()):
        keep = [j for j, s in enumerate(m.samples) if not (hit[s] and not hit[s].group(2))]
        if len(keep) < len(m.samples):
            m.notes.append(f"{len(m.samples) - len(keep)} reporter columns summed over experiments were left out "
                           "(each experiment's own channels are used)")
            m = keep_samples(m, keep)
    idx = sorted({int(x.group(1)) for s, x in hit.items() if x and s in m.condition})
    if not idx:
        return m
    base, n = min(idx), len(idx)
    channel, plex = {}, {}
    for s in m.samples:
        x = hit.get(s)
        if not x:
            continue
        channel[s] = channel_from_index(int(x.group(1)) - base, n)
        if x.group(2):
            plex[s] = x.group(2).strip()
    m.meta["channel"] = channel
    if plex:
        m.meta["plex"] = plex
        summary = path.parent / "summary.txt"
        if summary.is_file():
            try:
                head, rows = anytable.read_table(summary)
            except (anytable.TableError, OSError, UnicodeError, csv.Error):
                head, rows = [], []
            if "Raw file" in head and "Experiment" in head:
                rf, ex = head.index("Raw file"), head.index("Experiment")
                files: dict[str, list[str]] = defaultdict(list)
                for r in rows:
                    if r[rf].strip() and r[ex].strip() and r[rf].strip().lower() != "total":
                        files[r[ex].strip()].append(run_stem(r[rf].strip()))
                m.meta["runs"] = {s: files.get(p, []) for s, p in plex.items()}
    return m


# ------------------------------------------------------------- Spectronaut --


def _spectronaut_score(path: Path) -> float:
    h = _header(path)
    if {"PG.ProteinGroups", "R.FileName"} <= set(h) and any(x in h for x in ("PG.Quantity", "PG.MS2Quantity")):
        return 0.9
    if any(re.match(r"^(?:\[\d+\]\s*)?.+\.PG\.Quantity$", x) for x in h):
        return 0.85
    return 0.0


def load_spectronaut(path: Path) -> QuantMatrix:
    h = _header(path)
    if "R.FileName" not in h:  # pivot report: the any-table loader reads <run>.PG.Quantity columns
        m = anytable.load(path)
        m.exp, m.meta["engine"], m.meta["quantity"] = "DIA", "Spectronaut", "PG.Quantity (pivot report)"
        return m
    qcol = "PG.Quantity" if "PG.Quantity" in h else "PG.MS2Quantity"
    want = ["R.FileName", "R.Condition", "R.Replicate", "PG.ProteinGroups", "PG.Genes", "PG.ProteinDescriptions",
            "PG.ProteinNames", qcol, "PG.Qvalue", "EG.Qvalue", "EG.PrecursorId"]
    cols, rows = _read_long(path, want)
    ix = {c: j for j, c in enumerate(cols)}
    feats: dict[str, Feature] = {}
    cells: dict[tuple[str, str], float] = {}
    cond: dict[str, str] = {}
    rep: dict[str, int] = {}
    runs: list[str] = []
    peps: dict[str, set[str]] = {}
    dropped = 0
    for r in rows:
        pq_, eq = (num(r[ix["PG.Qvalue"]]) if "PG.Qvalue" in ix else None), (
            num(r[ix["EG.Qvalue"]]) if "EG.Qvalue" in ix else None)
        if (pq_ is not None and pq_ > LONG_FDR) or (eq is not None and eq > LONG_FDR):
            dropped += 1
            continue
        run, pg = r[ix["R.FileName"]], r[ix["PG.ProteinGroups"]]
        if not pg:
            continue
        if run not in cond and run not in runs:
            runs.append(run)
        if "R.Condition" in ix and r[ix["R.Condition"]]:
            cond[run] = r[ix["R.Condition"]].strip()
        if "R.Replicate" in ix:
            rv = num(r[ix["R.Replicate"]])
            if rv is not None and rv == int(rv):
                rep[run] = int(rv)
        if pg not in feats:
            genes = r[ix["PG.Genes"]] if "PG.Genes" in ix else ""
            desc = r[ix["PG.ProteinDescriptions"]] if "PG.ProteinDescriptions" in ix else (
                r[ix["PG.ProteinNames"]] if "PG.ProteinNames" in ix else "")
            feats[pg] = Feature(id=pg, label=(genes or pg).split(";")[0], description=desc)
        v = num(r[ix[qcol]])
        if v is not None and v > 0:
            cells[(pg, run)] = v
        if "EG.PrecursorId" in ix:
            peps.setdefault(pg, set()).add(r[ix["EG.PrecursorId"]])
    for pg, f in feats.items():
        f.peptides = len(peps.get(pg, ())) or None
    notes = [f"Spectronaut report {path.name}: protein quantity {qcol}; conditions and replicates from "
             f"R.Condition / R.Replicate" + (f"; {dropped:,} rows above q {LONG_FDR:g} left out" if dropped else "")]
    return _matrix_from_long(path, feats, cells, runs, cond, rep, "DIA", notes,
                             {"engine": "Spectronaut", "evidence": "precursors", "quantity": qcol})


# ---------------------------------------------------------------- AlphaDIA --


def _alphadia_score(path: Path) -> float:
    return 0.9 if path.name.lower() in ("pg.matrix.tsv", "pg.matrix.parquet", "protein_groups.tsv") and \
        (path.suffix.lower() == ".parquet" or (_header(path)[:1] in (["pg"], ["pg.name"], ["protein_group"]))) else 0.0


def load_alphadia(path: Path) -> QuantMatrix:
    if path.suffix.lower() == ".parquet":
        raise anytable.TableError(f"{path.name}: read AlphaDIA's pg.matrix.tsv instead (the Parquet matrix "
                                  "needs pyarrow and is the same numbers)")
    m = anytable.load(path)
    m.exp, m.meta["engine"], m.meta["quantity"] = "DIA", "AlphaDIA", "pg.matrix (MaxLFQ-style)"
    return m


# ---------------------------------------------------------- MSstats format --

_MSSTATS_NEED = {"ProteinName", "Run", "Intensity", "Condition", "BioReplicate"}


def _msstats_score(path: Path) -> float:
    h = set(_header(path))
    if not _MSSTATS_NEED <= h or {"Channel", "Mixture"} <= h:  # MSstatsTMT has its own adapter
        return 0.0
    return 0.9 if ("PeptideSequence" in h or "PeptideModifiedSequence" in h) else 0.6


def median_polish(rows: list[list[float | None]], iters: int = 10, eps: float = 0.01) -> list[float | None]:
    """Tukey median polish of features x runs (log2): returns overall + run effect per run, the protein
    summary MSstats uses (summaryMethod = "TMP"). Runs with no value stay None."""
    import statistics

    if not rows:
        return []
    nr, nc = len(rows), len(rows[0])
    z = [list(r) for r in rows]
    t = 0.0
    re_ = [0.0] * nr
    ce = [0.0] * nc
    old = math.inf
    for _ in range(iters):
        for i in range(nr):
            obs = [v for v in z[i] if v is not None]
            if obs:
                d = statistics.median(obs)
                re_[i] += d
                z[i] = [None if v is None else v - d for v in z[i]]
        d = statistics.median(ce) if ce else 0.0
        ce = [c - d for c in ce]
        t += d
        for j in range(nc):
            obs = [z[i][j] for i in range(nr) if z[i][j] is not None]
            if obs:
                d = statistics.median(obs)
                ce[j] += d
                for i in range(nr):
                    if z[i][j] is not None:
                        z[i][j] -= d
        d = statistics.median(re_) if re_ else 0.0
        re_ = [r - d for r in re_]
        t += d
        new = sum(abs(v) for r in z for v in r if v is not None)
        if new == 0 or abs(new - old) <= eps * new:
            break
        old = new
    present = [any(rows[i][j] is not None for i in range(nr)) for j in range(nc)]
    return [t + ce[j] if present[j] else None for j in range(nc)]


def load_msstats(path: Path) -> QuantMatrix:
    """MSstats long format -> protein matrix: per protein, log2 feature intensities (feature = peptide,
    charge, fragment, product charge) summarised per run by Tukey median polish. Label-free (IsotopeLabelType
    L) only; heavy-labelled reference rows are left out."""
    h = _header(path)
    if "Channel" in h and "Mixture" in h:
        return load_msstats_tmt(path)
    pep ="PeptideModifiedSequence" if "PeptideModifiedSequence" in h else "PeptideSequence"
    want = ["ProteinName", pep, "PrecursorCharge", "FragmentIon", "ProductCharge", "IsotopeLabelType", "Condition",
            "BioReplicate", "Run", "Intensity"]
    cols, rows = _read_long(path, want)
    ix = {c: j for j, c in enumerate(cols)}
    by: dict[str, dict[str, dict[str, float]]] = {}
    cond: dict[str, str] = {}
    rep: dict[str, int] = {}
    runs: list[str] = []
    heavy = 0
    for r in rows:
        if "IsotopeLabelType" in ix and r[ix["IsotopeLabelType"]].strip().upper() not in ("L", "LIGHT", ""):
            heavy += 1
            continue
        prot, run = r[ix["ProteinName"]], r[ix["Run"]]
        if not prot or not run:
            continue
        if run not in cond:
            runs.append(run)
            cond[run] = r[ix["Condition"]].strip()
            rv = num(r[ix["BioReplicate"]])
            if rv is not None and rv == int(rv):
                rep[run] = int(rv)
        feat = "|".join(r[ix[c]] for c in (pep, "PrecursorCharge", "FragmentIon", "ProductCharge") if c in ix)
        v = num(r[ix["Intensity"]])
        if v is not None and v > 1:  # MSstats treats intensities <= 1 as missing
            by.setdefault(prot, {}).setdefault(feat, {})[run] = math.log2(v)
    feats: dict[str, Feature] = {}
    cells: dict[tuple[str, str], float] = {}
    for prot, fmap in by.items():
        mat = [[fmap[f].get(run) for run in runs] for f in fmap]
        summ = median_polish(mat)
        feats[prot] = Feature(id=prot, label=prot.split("|")[-1].split("_")[0] if "|" in prot else prot.split(";")[0],
                              peptides=len({f.split("|")[0] for f in fmap}))
        for run, s in zip(runs, summ, strict=True):
            if s is not None:
                cells[(prot, run)] = 2 ** s
    notes = [f"MSstats-format {path.name}: {len(feats):,} proteins summarised from {sum(len(v) for v in by.values()):,} "
             "features by Tukey median polish (MSstats' default)" + (f"; {heavy:,} heavy / reference rows left out"
                                                                      if heavy else "")]
    samples_cond = {r: cond[r] for r in runs if cond.get(r)}
    return _matrix_from_long(path, feats, cells, runs, samples_cond, rep, "LFQ", notes,
                             {"engine": "MSstats format", "evidence": "peptides", "quantity": "median polish (TMP)"})


# ------------------------------------------------------- MSstatsTMT format --

_MSSTATS_TMT_NEED = {"ProteinName", "Mixture", "Run", "Channel", "Intensity"}
_TMT_SKIP = ("norm", "empty")  # MSstatsTMT's reference channels (Condition 'Norm') and empty channels


def _msstats_tmt_score(path: Path) -> float:
    h = set(_header(path))
    if not _MSSTATS_TMT_NEED <= h:
        return 0.0
    return 0.92 if {"Condition", "BioReplicate"} <= h and ("PeptideSequence" in h or "PSM" in h) else 0.7


def _channel_labels(channels: list[str]) -> dict[str, str]:
    """MSstatsTMT Channel values -> kit labels: '127N' stays; OpenMS / quantms write 1..n (or channel.1): the n-th
    channel of the kit."""
    from ionomos.downstream.plex import TMT_ORDERS, channel_key

    idx = {c: re.fullmatch(r"(?i)(?:channel[._ ]?)?(\d{1,2})", c.strip()) for c in channels}
    if channels and all(idx.values()):
        n = max(int(x.group(1)) for x in idx.values())
        n = next((k for k in sorted(TMT_ORDERS) if k >= n and k != 8 and k != 4), n)
        return {c: (TMT_ORDERS[n][int(idx[c].group(1)) - 1] if n in TMT_ORDERS and 1 <= int(idx[c].group(1)) <= n
                    else c) for c in channels}
    return {c: channel_key(c) or c for c in channels}


def msstats_tmt_summary(cols: list[str], rows: list[list[str]]) -> dict:
    """MSstatsTMT's proteinSummarization(method = "MedianPolish", global_norm = TRUE, reference_norm = TRUE) on
    MSstatsTMT-format rows, before its Norm / Empty channels are removed. Checked against MSstatsTMT 2.20 in
    tests (tests/golden/msstatstmt/). Steps, as in MSstatsTMT / MSstatsConvert:

      features         PeptideSequence + Charge; several PSMs of a feature in one run: the one with the largest
                       total intensity is kept; intensities <= 0 are missing
      fractions        runs of one Mixture + TechRepMixture are fractions (MSstatsConvert .handleFractionsTMT): a
                       feature seen in several keeps the fraction with the largest mean (then sum, then max)
                       intensity, and the run becomes '<Mixture>_<TechRepMixture>'
      log2, global     every run x channel median of log2 intensities is moved to the median of those medians;
      norm             values below log2(1) after it are missing
      summary          per protein and run, Tukey median polish of features x channels: overall + channel effect
      reference norm   per protein, each run is shifted so the mean of its Norm channels equals the median of
                       those means over runs (only for proteins with Norm values in more than one run; a run
                       without one then has no values)
    Not ported: MSstats' model-based imputation of censored values (MBimpute; MSstatsTMT's default "msstats"
    summary); missing values stay missing, like method = "MedianPolish".

    Returns {"abundance": {protein: {(run, channel): log2}}, "before": same before reference norm,
             "annotation": {(run, channel): (mixture, techrep, condition, bioreplicate)}, "runs_of": {run: [stems]},
             "channels": [...], "notes": [...], "normalised": bool}."""
    import statistics

    ix = {c: j for j, c in enumerate(cols)}
    charge = "Charge" if "Charge" in ix else "PrecursorCharge" if "PrecursorCharge" in ix else None

    def get(r, c, default=""):
        return r[ix[c]].strip() if c in ix else default

    psms: dict[tuple, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    annot: dict[tuple, tuple] = {}
    run_info: dict[str, tuple] = {}
    channels: list[str] = []
    for r in rows:
        prot, run, ch = get(r, "ProteinName"), get(r, "Run"), get(r, "Channel")
        if not prot or not run or not ch:
            continue
        mix, tech = get(r, "Mixture"), get(r, "TechRepMixture", "1") or "1"
        run_info.setdefault(run, (mix, tech, get(r, "Fraction", "") or "1"))
        if ch not in channels:
            channels.append(ch)
        annot.setdefault((f"{mix}_{tech}", ch), (mix, tech, get(r, "Condition"), get(r, "BioReplicate")))
        feat = "_".join(x for x in (get(r, "PeptideSequence"), get(r, charge) if charge else "") if x) or get(r, "PSM")
        psm = get(r, "PSM") or feat
        v = num(get(r, "Intensity"))
        if v is not None and v > 0:
            cell = psms[(run, prot, feat)][psm]
            cell[ch] = max(v, cell.get(ch, 0.0))
    notes: list[str] = []
    # one PSM per feature and run: the one with the largest total intensity
    feat_run: dict[tuple, dict[str, float]] = {}
    multi = 0
    for key, by_psm in psms.items():
        if len(by_psm) > 1:
            multi += 1
        feat_run[key] = max(by_psm.values(), key=lambda d: sum(d.values()))
    if multi:
        notes.append(f"{multi:,} features had several PSMs in a run; the one with the largest total intensity was kept")
    # fractions: runs of one mixture + technical replicate
    by_tech: dict[str, list[str]] = defaultdict(list)
    for run, (mix, tech, _f) in run_info.items():
        by_tech[f"{mix}_{tech}"].append(run)
    data: dict[tuple, dict[str, float]] = {}  # (techrun, protein, feature) -> {channel: linear}
    cand: dict[tuple, list[dict[str, float]]] = defaultdict(list)
    for (run, prot, feat), vals in feat_run.items():
        mix, tech, _f = run_info[run]
        cand[(f"{mix}_{tech}", prot, feat)].append(vals)
    merged = 0
    for key, options in cand.items():
        if len(options) == 1:
            data[key] = options[0]
            continue
        merged += 1
        chosen = options
        for agg in (lambda d: sum(d.values()) / len(d), lambda d: sum(d.values()), lambda d: max(d.values())):
            best = max(agg(d) for d in chosen)
            chosen = [d for d in chosen if agg(d) == best]
            if len(chosen) == 1:
                break
        if len(chosen) == 1:
            data[key] = chosen[0]
        else:  # still tied: the mean per channel
            data[key] = {c: sum(d[c] for d in chosen if c in d) / sum(c in d for d in chosen)
                         for c in {c for d in chosen for c in d}}
    fractionated = sorted(t for t, rs in by_tech.items() if len(rs) > 1)
    if fractionated:
        notes.append(f"fractions combined within {len(fractionated)} mixture run(s) ({merged:,} features seen in "
                     "several fractions kept the fraction with the largest mean intensity, as MSstatsConvert)")
    # log2 and global median normalisation between run x channel
    logv = {k: {c: math.log2(v) for c, v in d.items() if v >= 1} for k, d in data.items()}  # < 1: NA in MSstatsTMT
    per: dict[tuple, list[float]] = defaultdict(list)
    for (tr, _p, _f), d in logv.items():
        for c, v in d.items():
            per[(tr, c)].append(v)
    meds = {k: statistics.median(v) for k, v in per.items() if v}
    base = statistics.median(meds.values()) if meds else 0.0
    for (tr, _p, _f), d in logv.items():
        for c in list(d):
            x = d[c] + base - meds[(tr, c)]
            if x < 0:  # 2^x < 1: MSstatsTMT turns it into NA
                del d[c]
            else:
                d[c] = x
    # median polish per protein and run
    order = list(channels)
    by_prot: dict[str, dict[str, list[dict[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for (tr, p, _f), d in logv.items():
        if d:
            by_prot[p][tr].append(d)
    before: dict[str, dict[tuple, float]] = {}
    for p, runs in by_prot.items():
        out: dict[tuple, float] = {}
        for tr, feats in runs.items():
            summ = median_polish([[d.get(c) for c in order] for d in feats])
            for c, v in zip(order, summ, strict=True):
                if v is not None:
                    out[(tr, c)] = v
        before[p] = out
    # reference normalisation between runs on the Norm channels
    is_norm = {k: (a[2].lower() == "norm") for k, a in annot.items()}
    after: dict[str, dict[tuple, float]] = {}
    normalised = len(by_tech) > 1 and any(is_norm.values())
    for p, ab in before.items():
        if not normalised:
            after[p] = dict(ab)
            continue
        runs_with = {tr for (tr, c) in ab}
        norm_by_run: dict[str, list[float]] = defaultdict(list)
        for (tr, c), v in ab.items():
            if is_norm.get((tr, c)):
                norm_by_run[tr].append(v)
        if len(runs_with) > 1 and len(norm_by_run) > 1:
            nab = {tr: sum(v) / len(v) for tr, v in norm_by_run.items()}
            med = statistics.median(nab.values())
            after[p] = {(tr, c): v + med - nab[tr] for (tr, c), v in ab.items() if tr in nab}
        else:
            after[p] = dict(ab)
    if len(by_tech) > 1 and not any(is_norm.values()):
        notes.append("no 'Norm' channel (Condition Norm) in the MSstatsTMT table, so MSstatsTMT's normalisation "
                     "between runs could not be applied")
    runs_of: dict[str, list[str]] = defaultdict(list)
    for run, (mix, _t, _f) in run_info.items():
        runs_of[mix].append(run_stem(run))
    return {"abundance": after, "before": before, "annotation": annot, "runs_of": dict(runs_of),
            "channels": order, "notes": notes, "normalised": normalised, "runs": len(by_tech)}


def load_msstats_tmt(path: Path) -> QuantMatrix:
    """MSstatsTMT format (ProteinName, PeptideSequence, Charge, PSM, Mixture, TechRepMixture, Run, Channel,
    Condition, BioReplicate, Intensity; from MSstatsTMT's converters, quantms / OpenMS, FragPipe's
    philosopher): proteins summarised as msstats_tmt_summary() does, then one sample per mixture and channel:
    technical replicates of a mixture are averaged (log2), Norm and Empty channels are left out. The plexes are
    already on one scale (the reference normalisation), so the IRS step (plex.py) is not applied again."""
    want = ["ProteinName", "PeptideSequence", "Charge", "PrecursorCharge", "PSM", "Mixture", "TechRepMixture", "Run",
            "Fraction", "Channel", "Condition", "BioReplicate", "Intensity"]
    cols, rows = _read_long(path, want)
    if not {"ProteinName", "Mixture", "Run", "Channel", "Intensity"} <= set(cols):
        raise anytable.TableError(f"{path.name}: not MSstatsTMT format (needs ProteinName, Mixture, Run, Channel, "
                                  "Intensity)")
    s = msstats_tmt_summary(cols, rows)
    if not any(s["abundance"].values()):
        raise anytable.TableError(f"{path.name}: MSstatsTMT format, but no protein has a usable intensity")
    labels = _channel_labels(s["channels"])
    keys: list[tuple[str, str]] = []  # (mixture, channel) samples, in mixture order then channel order
    info: dict[tuple, tuple] = {}
    for (_tr, ch), (mix, _tech, cond, bio) in s["annotation"].items():
        if cond.strip().lower() in _TMT_SKIP:
            continue
        if (mix, ch) not in info:
            keys.append((mix, ch))
            info[(mix, ch)] = (cond, bio)
    mix_order = list(dict.fromkeys(a[0] for a in s["annotation"].values()))
    keys.sort(key=lambda k: (mix_order.index(k[0]), s["channels"].index(k[1])))
    name = {k: f"{k[0]}_{labels[k[1]]}" for k in keys}

    def per_sample(table: dict) -> dict[tuple[str, str], float]:
        """(protein, sample) -> linear, technical replicates of a mixture averaged in log2."""
        acc: dict[tuple, list[float]] = defaultdict(list)
        for p, ab in table.items():
            for (tr, ch), v in ab.items():
                mix = s["annotation"][(tr, ch)][0]
                if (mix, ch) in name:
                    acc[(p, name[(mix, ch)])].append(v)
        return {k: 2 ** (sum(v) / len(v)) for k, v in acc.items()}

    feats = {p: Feature(id=p, label=p.split("|")[-1].split("_")[0] if "|" in p else p.split(";")[0])
             for p in s["abundance"]}
    peps: dict[str, set[str]] = defaultdict(set)
    pix = cols.index("PeptideSequence") if "PeptideSequence" in cols else None
    if pix is not None:
        prix = cols.index("ProteinName")
        for r in rows:
            peps[r[prix].strip()].add(r[pix])
        for p, f in feats.items():
            f.peptides = len(peps.get(p, ())) or None
    techreps = s["runs"] > len(mix_order)
    notes = [f"MSstatsTMT-format {path.name}: {len(feats):,} proteins in {len(mix_order)} mixture(s), summarised "
             "as MSstatsTMT's proteinSummarization (median polish per run, global median and reference-channel "
             "normalisation; no model-based imputation)"] + s["notes"]
    if techreps:
        notes.append("technical replicates of a mixture were averaged (log2) into one sample per channel")
    samples = [name[k] for k in keys]
    cond = {name[k]: info[k][0] for k in keys if info[k][0]}
    m = _matrix_from_long(path, feats, per_sample(s["abundance"]), samples, cond, {}, "TMT", notes,
                          {"engine": "MSstatsTMT format", "evidence": "peptides",
                           "quantity": "median polish (MSstatsTMT MedianPolish)"})
    counts: dict[str, int] = defaultdict(int)
    for k in keys:
        c, bio = info[k]
        counts[c] += 1
        m.replicate[name[k]] = int(bio) if bio.isdigit() else counts[c]
    m.meta.update({"plex": {name[k]: k[0] for k in keys}, "channel": {name[k]: labels[k[1]] for k in keys},
                   "runs": {name[k]: s["runs_of"].get(k[0], []) for k in keys}, "conditions_from": "engine"})
    if s["normalised"]:
        bef = per_sample(s["before"])
        ids = list(feats)
        m.meta["bridge_before"] = {"ids": ids, "samples": list(samples),
                                   "values": [[_log2(bef.get((i, x))) for x in samples] for i in ids]}
        m.meta["bridge"] = {"method": "MSstatsTMT reference normalisation (Norm channels)", "applied": True,
                            "plexes": {mx: sum(1 for k in keys if k[0] == mx) for mx in mix_order},
                            "reference": ["Condition Norm"], "reference_from": "MSstatsTMT Condition",
                            "center": "median of the runs' Norm means (MSstatsTMT)"}
    return m


# ---------------------------------------------------------- Proteome Discoverer --

# 'Abundances (Normalized): F1: Sample, DMSO' and 'Abundance: F1: 126, Sample, DMSO' (TMT: file F1, channel 126)
_PD_COL = re.compile(r"^Abundances?(?: \((?:Normalized|Grouped)\))?:\s*(F\d+):\s*([^,]+)(?:,\s*(.*))?$", re.I)


def _pd_score(path: Path) -> float:
    h = _header(path)
    return 0.85 if "Accession" in h and any(_PD_COL.match(x) for x in h) else 0.0


def load_pd(path: Path) -> QuantMatrix:
    """Proteome Discoverer Proteins export: Abundances (Normalized) preferred, else Abundance columns.
    'Abundance: F1: 126, Sample, DMSO' -> condition DMSO (the text after the sample type)."""
    m = anytable.load(path)
    header = _header(path)
    fam = [h for h in header if _PD_COL.match(h) and "normalized" in h.lower()] or [h for h in header if _PD_COL.match(h)]
    used = set(m.columns.values())
    rep_count: dict[str, int] = {}
    rename = {}
    for s in m.samples:
        col = m.columns.get(s, "")
        mm = _PD_COL.match(col)
        if not mm or col not in used or col not in fam:
            continue
        parts = [x.strip() for x in (mm.group(3) or "").split(",") if x.strip()]
        c = (parts[-1] if parts else mm.group(2).strip()) or s
        rep_count[c] = rep_count.get(c, 0) + 1
        rename[s] = (f"{c}_{rep_count[c]}", c, rep_count[c])
    if rename:
        new = [rename.get(s, (s, m.condition[s], m.replicate.get(s)))[0] for s in m.samples]
        m.columns = {rename.get(s, (s,))[0]: m.columns[s] for s in m.samples}
        m.condition = {rename.get(s, (s, m.condition[s]))[0]: rename.get(s, (s, m.condition[s]))[1] for s in m.samples}
        m.replicate = {rename[s][0]: rename[s][2] for s in m.samples if s in rename}
        m.samples = new
    m.exp = "TMT" if any(re.search(r"\b1[23]\d[NC]?\b", c) for c in m.columns.values()) else "LFQ"
    if rename:
        m.meta["conditions_from"] = "engine"
    if m.exp == "TMT":  # 'Abundance: F1: 126, ...': file F1 is the plex, 126 the channel (plex.py IRS)
        from ionomos.downstream.plex import channel_key

        cols = {s: _PD_COL.match(m.columns.get(s, "")) for s in m.samples}
        m.meta["channel"] = {s: channel_key(x.group(2)) for s, x in cols.items() if x and channel_key(x.group(2))}
        m.meta["plex"] = {s: x.group(1) for s, x in cols.items() if x and s in m.meta["channel"]}
    m.meta["engine"] = "Proteome Discoverer"
    m.meta["quantity"] = "Abundances (Normalized)" if any("normalized" in c.lower() for c in m.columns.values()) else "Abundance"
    return m


# ----------------------------------------------------------------- registry --

# (engine, method key, file-name filter, scorer, loader)
ADAPTERS = [
    ("MaxQuant", "MaxQuant", lambda p: p.name == "proteinGroups.txt", _maxquant_score, load_maxquant),
    ("Spectronaut", "Spectronaut", lambda p: p.suffix.lower() in (".tsv", ".csv", ".txt", ".xls"),
     _spectronaut_score, load_spectronaut),
    ("AlphaDIA", "AlphaDIA", lambda p: p.name.lower() in ("pg.matrix.tsv", "protein_groups.tsv"),
     _alphadia_score, load_alphadia),
    ("MSstats format", "MSstats", lambda p: p.suffix.lower() in (".csv", ".tsv", ".txt"), _msstats_score, load_msstats),
    ("MSstatsTMT format", "MSstatsTMT", lambda p: p.suffix.lower() in (".csv", ".tsv", ".txt"), _msstats_tmt_score,
     load_msstats_tmt),
    ("Proteome Discoverer", "PD", lambda p: p.suffix.lower() in (".txt", ".tsv", ".csv"), _pd_score, load_pd),
    ("DIA-NN", "DIA-NN", lambda p: p.name.lower() in ("report.tsv", "report.parquet") or
     p.name.lower().endswith((".report.tsv", ".report.parquet")), _diann_long_score, load_diann_long),
]
METHODS = {a[1]: a for a in ADAPTERS}


def detect_all(workdir: Path, limit_bytes: int = 2 * 1024**3) -> list[Detected]:
    """Every recognised engine table under workdir, best first."""
    out = []
    for p in _files(workdir):
        try:
            if p.stat().st_size > limit_bytes or p.name.startswith((".", "~$")):
                continue
        except OSError:
            continue
        for engine, method, pick, score, _load in ADAPTERS:
            if not pick(p):
                continue
            try:
                s = score(p)
            except (OSError, UnicodeError, csv.Error):
                s = 0.0
            if s > 0:
                out.append(Detected(engine, method, p, s))
    out.sort(key=lambda d: (-d.score, len(d.path.parts), str(d.path)))
    return out


def detect(workdir: Path) -> Detected | None:
    found = detect_all(workdir)
    return found[0] if found else None


def load(method: str, workdir: Path) -> tuple[QuantMatrix | None, list[str]]:
    """Load the best table of this engine under workdir (or workdir itself when it is the table)."""
    adapter = METHODS[method]
    cands = [d for d in detect_all(workdir) if d.method == method]
    if not cands:
        return None, [f"no {adapter[0]} results found in {Path(workdir).name}/"]
    return adapter[4](cands[0].path), []


# --------------------------------------------------------------- provenance --


def _first_match(path: Path, rx: str, lines: int = 400) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for k, line in enumerate(fh):
                if k >= lines:
                    break
                m = re.search(rx, line)
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    return ""


def provenance(workdir: Path, method: str | None, source: str = "", meta: dict | None = None) -> dict:
    """What produced the results: engine, version (when the folder records it), the files that say so,
    which quantity was used and at which FDR. Best effort: missing pieces are left out, never guessed."""
    workdir = Path(workdir)
    meta = meta or {}
    root = workdir if workdir.is_dir() else workdir.parent
    out: dict = {"engine": meta.get("engine") or "", "version": "", "files": [], "quantity": meta.get("quantity", "")}
    files = list(_files(root, depth=3)) if root.is_dir() else []

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            return p.name

    fp_logs = [p for p in files if re.match(r"(?i)^log_.*\.txt$", p.name) or p.name == "fragpipe.workflow"]
    if method in ("isoDTB", "TMT", "LFQ") or (method == "DIA" and fp_logs):
        out["engine"] = "FragPipe"
        for p in fp_logs:
            v = _first_match(p, r"FragPipe (?:version|v)\s*([0-9][\w.\-]*)") or _first_match(p, r"# FragPipe \(([^)]+)\)")
            if v:
                out["version"] = v
                break
        wf = next((p for p in files if p.name == "fragpipe.workflow" or p.suffix == ".workflow"), None)
        if wf:
            out["files"].append(rel(wf))
            fasta = _first_match(wf, r"^database\.db-path=(.+)$", lines=5000)
            if fasta:
                out["fasta"] = fasta.replace("\\\\", "\\").replace("\\:", ":")
        for tool, rx in (("MSFragger", r"MSFragger(?: version|-)\s*v?([0-9][\w.\-]*)"),
                         ("IonQuant", r"IonQuant(?: version|-)\s*v?([0-9][\w.\-]*)"),
                         ("DIA-NN", r"DIA-NN\s+v?([0-9][\w.\-]*)")):
            for p in fp_logs:
                v = _first_match(p, rx, lines=3000)
                if v:
                    out.setdefault("tools", {})[tool] = v
                    break
    elif method in ("DIA", "DIA-NN"):
        out["engine"] = "DIA-NN"
        log = next((p for p in files if p.name.endswith(".log.txt") and "report" in p.name.lower()), None) or next(
            (p for p in files if p.name.endswith(".log.txt")), None)
        if log:
            out["files"].append(rel(log))
            out["version"] = _first_match(log, r"DIA-NN\s+v?([0-9][\w.\-]*)")
        if method == "DIA-NN":
            out["fdr"] = f"precursor and protein-group q ≤ {LONG_FDR:g}"
    elif method == "MaxQuant":
        par = next((p for p in files if p.name == "parameters.txt"), None)
        if par:
            out["files"].append(rel(par))
            out["version"] = _first_match(par, r"^Version\t(.+)$")
            fdr = _first_match(par, r"^Protein FDR\t(.+)$")
            if fdr:
                out["fdr"] = f"protein FDR {fdr}"
        mqpar = next((p for p in files if p.name.lower().startswith("mqpar") and p.suffix == ".xml"), None)
        if mqpar:
            out["files"].append(rel(mqpar))
            out["version"] = out["version"] or _first_match(mqpar, r"<maxQuantVersion>([^<]+)<")
    elif method == "AlphaDIA":
        cfg = next((p for p in files if p.name == "frozen_config.yaml"), None)
        if cfg:
            out["files"].append(rel(cfg))
            out["version"] = _first_match(cfg, r"^version:\s*['\"]?([^'\"\s]+)")
    elif method in ("Spectronaut", "PD", "MSstats", "MSstatsTMT"):
        out["note"] = "the export does not record the engine version"
        if method == "Spectronaut":
            out["fdr"] = f"q ≤ {LONG_FDR:g} (long report)" if "long" in (source or "") else ""
    elif method == "table":
        out["engine"] = out["engine"] or "a table"
    if source:
        out["table"] = rel(Path(source)) if root.is_dir() else Path(source).name
    return {k: v for k, v in out.items() if v not in ("", [], {}, None)}
