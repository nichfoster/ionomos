"""
An SDRF-Proteomics file as the experiment's design (D47): which condition, biological and technical replicate,
fraction and label each run of the quant table is.

    path, notes = find(dest, workdir, table)      # a *.sdrf.tsv / sdrf.tsv the user put there, never results/
    design = read(path, factor=["compound"])       # its rows (PSI SDRF-Proteomics, github.com/bigbio/proteomics-sample-metadata)
    m, info = apply(m, design)                     # conditions / replicates / plexes set on the QuantMatrix

Matching a run of the quant table to an SDRF row (`comment[data file]`, compared by file stem):
    label-free   the run's column (DIA-NN, Spectronaut, MSstats: the raw file) or its name, with the same rules as
                 the manifest (quant.match_run_stem: exact stem, FragPipe's _calibrated suffix, Xcalibur stamp)
    TMT          a sample is a channel of a plex: one of the plex's raw files plus `comment[label]` (TMT126 -> 126);
                 files that share one channel -> source map are one plex (their fractions and technical replicates)
    otherwise    the sample name equals the SDRF source name or assay name (TMT-Integrator, MaxQuant experiments)

Precedence, highest first: experiment.yaml / Analysis tab `sample_conditions` (applied later, in fpa.process) >
this SDRF > conditions the engine recorded (Spectronaut R.Condition, MSstats Condition, Proteome Discoverer) >
ionomos.json manifest > names. The condition is `factor value[...]`; with several factor columns their values are
joined with " | " unless the `sdrf_factor` setting names the one(s) to use. Samples keep the table's names; only
their conditions and replicate numbers change, so sample_conditions written for a table stay valid. Runs the SDRF
doesn't describe keep their earlier condition and become a doctor issue (SDRF_UNMATCHED_RUNS).

A column `characteristics[role]` (or `[sample role]`, `[experimental role]`, `comment[role]`) gives each
condition its role for the comparisons (roles.py, D61): control, compound, competition or "competition of
Probe", reference, qc. It is not part of the SDRF specification. analysis.roles wins over it.

Ionomos writes its own SDRF to results/sdrf.tsv (sdrf.py); results/ is output and is never read back here.
"""
from __future__ import annotations

import csv
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ionomos.downstream.plex import channel_key, same_channel
from ionomos.downstream.quant import QuantMatrix, match_run_stem, run_stem

SKIP_DIRS = {"results", "ionomos_run", "fragpipe-analyst", "__MACOSX"}
RESERVED = {"not available", "not applicable", "na", "n/a", ""}
POOLED_WORDS = {"pooled", "pool", "reference", "norm", "bridge"}
JOIN = " | "


class DesignError(ValueError):
    """The file is not an SDRF Ionomos can read."""


@dataclass
class Row:
    source: str
    assay: str
    file: str            # data file name, no folders
    stem: str
    label: str           # comment[label] as written
    channel: str         # "126", "127N"; "" for label-free
    condition: str | None
    biorep: int | None
    pooled: bool
    techrep: int = 1
    fraction: int = 1
    role: str = ""       # characteristics[role]: control, compound, competition of X ... (roles.py)


@dataclass
class Design:
    path: Path
    rows: list[Row]
    factors: list[str]                      # the factor value columns used for the condition
    notes: list[str] = field(default_factory=list)
    plex_of: dict[str, str] = field(default_factory=dict)  # data file stem -> plex (TMT)

    @property
    def labelled(self) -> bool:
        return any(r.channel for r in self.rows)


def is_sdrf_name(name: str) -> bool:
    low = name.lower()
    return low == "sdrf.tsv" or low.endswith((".sdrf.tsv", "_sdrf.tsv", "-sdrf.tsv", ".sdrf"))


def _header(path: Path) -> list[str]:
    try:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            return [h.strip().lower() for h in fh.readline().rstrip("\r\n").split("\t")]
    except OSError:
        return []


def _looks_like_sdrf(path: Path) -> bool:
    h = _header(path)
    return "source name" in h and "comment[data file]" in h


def _engine_template(path: Path, rows: int = 200) -> bool:
    """An SDRF that describes no samples: no factor value column and every source name "not available". FragPipe
    24's stock workflows write one into their output folder (workflow.misc.save-sdrf); it is FragPipe's
    template for the user to fill in, not this experiment's design."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            head = [h.strip().lower() for h in fh.readline().rstrip("\r\n").split("\t")]
            if any(h.startswith("factor value") for h in head) or "source name" not in head:
                return False
            col = head.index("source name")
            seen = 0
            for line in fh:
                cells = line.rstrip("\r\n").split("\t")
                if not any(c.strip() for c in cells):
                    continue
                if (cells[col].strip().lower() if col < len(cells) else "") not in ("", "not available", "na"):
                    return False
                seen += 1
                if seen >= rows:
                    break
            return seen > 0
    except OSError:
        return False


def find(dest: Path, workdir: Path | None = None, table: Path | None = None) -> tuple[Path | None, list[str]]:
    """The SDRF describing this experiment: in the experiment folder (two levels deep, so fragpipe/ and raw/ are
    included) or next to the table given to `ionomos analyze`. results/, old runs and folders Ionomos made are
    skipped. Several candidates: the shallowest wins, with a note."""
    notes: list[str] = []
    found: list[tuple[int, str, Path]] = []
    seen: set[Path] = set()

    def skip(d: str) -> bool:
        return d in SKIP_DIRS or "_previous_" in d or d.endswith("_ionomos") or d.startswith(".")

    def scan(root: Path, depth: int) -> None:
        if not root.is_dir():
            return
        items: list[Path] = []
        for here, dirs, names in os.walk(root):  # pruned: never into results/, old runs or deeper than depth
            level = len(Path(here).relative_to(root).parts)
            dirs[:] = sorted(d for d in dirs if not skip(d)) if level < depth else []
            items += [Path(here) / n for n in sorted(names) if is_sdrf_name(n)]
        for p in items:
            rel = p.relative_to(root).parts
            if p.name.startswith((".", "~$")):
                continue
            try:
                key = p.resolve()
            except OSError:
                continue
            if key in seen or not p.is_file() or not _looks_like_sdrf(p):
                continue
            seen.add(key)
            if _engine_template(p):
                notes.append(f"{p.name} in {p.parent.name}/ names no samples (the search engine's own SDRF "
                             "template), so it is not used as the design")
                continue
            found.append((len(rel), p.name.lower(), p))

    scan(Path(dest), 2)
    if workdir is not None and Path(workdir) != Path(dest):
        scan(Path(workdir), 1)
    if table is not None:
        scan(Path(table).parent, 0)
    if not found:
        return None, notes
    found.sort(key=lambda x: (x[0], x[1]))
    if len(found) > 1:
        notes.append(f"several SDRF files found; used {found[0][2].name} (also: " +
                     ", ".join(x[2].name for x in found[1:4]) + ")")
    return found[0][2], notes


def _int(v: str) -> int | None:
    v = (v or "").strip()
    return int(v) if re.fullmatch(r"\d{1,6}", v) else None


def _basename(v: str) -> str:
    v = v.strip()
    return PureWindowsPath(v).name if ("\\" in v or ":" in v[:3]) else Path(v).name


def read(path: Path, factor: list[str] | str | None = None) -> Design:
    """Rows of an SDRF (header names are compared without case). factor: the factor value column(s) that make the
    condition (`compound` or `factor value[compound]`); default every factor column, their values joined."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            grid = [r for r in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE) if any(c.strip() for c in r)]
    except OSError as exc:
        raise DesignError(f"{path.name}: {exc}") from exc
    if not grid:
        raise DesignError(f"{path.name} is empty")
    head = [h.strip().lower() for h in grid[0]]
    if "source name" not in head or "comment[data file]" not in head:
        raise DesignError(f"{path.name} has no 'source name' / 'comment[data file]' columns (not an SDRF)")
    notes: list[str] = []

    def col(*names: str) -> int | None:
        return next((head.index(n) for n in names if n in head), None)

    fcols = [(j, re.sub(r"^factor value\[(.*)\]$", r"\1", h).strip()) for j, h in enumerate(head)
             if h.startswith("factor value[")]
    wanted = [factor] if isinstance(factor, str) else list(factor or [])
    wanted = [re.sub(r"^factor value\[(.*)\]$", r"\1", str(w).strip().lower()).strip() for w in wanted if str(w).strip()]
    if wanted:
        picked = [(j, n) for j, n in fcols if n in wanted]
        missing = [w for w in wanted if w not in {n for _j, n in fcols}]
        if missing:
            notes.append(f"sdrf_factor {', '.join(missing)} is not a factor value column of {path.name} "
                         f"(it has: {', '.join(dict.fromkeys(n for _j, n in fcols)) or 'none'})")
        fcols = picked or fcols
    if not fcols:
        notes.append(f"{path.name} has no factor value[...] column, so it sets replicates but not conditions")
    ix = {"source": col("source name"), "assay": col("assay name"), "file": col("comment[data file]"),
          "label": col("comment[label]"),
          "biorep": col("characteristics[biological replicate]", "comment[biological replicate]"),
          "techrep": col("comment[technical replicate]"), "fraction": col("comment[fraction identifier]"),
          "pooled": col("characteristics[pooled sample]"),
          "role": col("characteristics[role]", "characteristics[sample role]", "characteristics[experimental role]",
                      "comment[role]", "comment[sample role]")}

    def cell(r: list[str], k: str) -> str:
        j = ix[k]
        return r[j].strip() if j is not None and j < len(r) else ""

    rows: list[Row] = []
    for r in grid[1:]:
        file = _basename(cell(r, "file"))
        if not file or file.lower() in RESERVED:
            continue
        label = cell(r, "label")
        low = label.lower()
        channel = channel_key(label) if ("tmt" in low or "itraq" in low or re.fullmatch(r"\d{3}[nc]?", low)) else ""
        values = []
        for j, _n in fcols:
            v = r[j].strip() if j < len(r) else ""
            if v.lower() not in RESERVED and v not in values:
                values.append(v)
        cond = JOIN.join(values) or None
        bio = cell(r, "biorep")
        pooled_col = cell(r, "pooled").lower()
        pooled = (bio.lower() == "pooled" or pooled_col.startswith(("pooled", "sn=")) or
                  (cond or "").lower() in POOLED_WORDS)
        role = cell(r, "role")
        rows.append(Row(cell(r, "source"), cell(r, "assay"), file, run_stem(file), label, channel, cond,
                        _int(bio), pooled, _int(cell(r, "techrep")) or 1, _int(cell(r, "fraction")) or 1,
                        "" if role.lower() in RESERVED else role))
    if not rows:
        raise DesignError(f"{path.name} has no rows with a data file")
    d = Design(path, rows, list(dict.fromkeys(n for _j, n in fcols)), notes)
    if d.labelled:  # files sharing one channel -> source map are one plex (fractions, technical replicates)
        maps: dict[str, set] = defaultdict(set)
        for r in rows:
            maps[r.stem].add((r.channel, r.source))
        ids: dict[frozenset, str] = {}
        for stem in dict.fromkeys(r.stem for r in rows):
            key = frozenset(maps[stem])
            ids.setdefault(key, f"plex{len(ids) + 1}")
            d.plex_of[stem] = ids[key]
    return d


def _stems_of(m: QuantMatrix, s: str) -> list[str]:
    runs = list((m.meta.get("runs") or {}).get(s) or [])
    col = m.columns.get(s, "")
    return list(dict.fromkeys(x for x in [*runs, run_stem(col) if col else "", s, run_stem(s)] if x))


def apply(m: QuantMatrix, d: Design) -> tuple[QuantMatrix, dict]:
    """Set each sample's condition, replicate (and TMT plex, pooled reference) from the SDRF. The matrix is changed
    in place and returned, with what happened (for notes, the doctor and analysis.json)."""
    by_stem: dict[str, list[Row]] = defaultdict(list)
    for r in d.rows:
        by_stem[r.stem].append(r)
    by_name: dict[str, list[Row]] = defaultdict(list)
    for r in d.rows:
        for k in {r.source.lower(), r.assay.lower()} - {""}:
            by_name[k].append(r)
    channels = m.meta.get("channel") or {}
    hits: dict[str, Row] = {}
    for s in m.samples:
        row = None
        for stem in _stems_of(m, s):
            key = match_run_stem(stem, by_stem)
            if key is None:
                continue
            cands = by_stem[key]
            if channels.get(s):
                cands = [r for r in cands if r.channel and same_channel(r.channel, channels[s])]
            elif any(r.channel for r in cands):
                cands = []  # a labelled file, but this sample has no channel: can't tell which row
            if len({(r.source, r.condition) for r in cands}) == 1:
                row = cands[0]
                break
        if row is None:
            cands = by_name.get(s.lower(), [])
            if cands and len({(r.condition, r.biorep) for r in cands}) == 1:
                row = cands[0]
        if row is not None:
            hits[s] = row
    rel = d.path.name
    info: dict = {"file": rel, "factors": d.factors, "matched": len(hits), "samples": len(m.samples),
                  "unmatched": [s for s in m.samples if s not in hits], "notes": list(d.notes)}
    if not hits:
        info["used"] = False
        m.meta["sdrf"] = info
        return m, info
    info["used"] = True
    design = m.meta.setdefault("design", {})
    plex = m.meta.setdefault("plex", {})
    refs = list(m.meta.get("reference_samples") or [])
    changed = 0
    for s, r in hits.items():
        if r.condition and m.condition.get(s) != r.condition:
            m.condition[s] = r.condition
            changed += 1
        if r.biorep is not None:
            m.replicate[s] = r.biorep
        design[s] = {"source": r.source, "file": r.file, "label": r.label or "", "biological_replicate":
                     "pooled" if r.pooled and r.biorep is None else r.biorep, "technical_replicate": r.techrep,
                     "fraction": r.fraction}
        if r.channel and r.stem in d.plex_of and s not in plex:
            plex[s] = d.plex_of[r.stem]
        if r.pooled and s not in refs:
            refs.append(s)
    if refs:
        m.meta["reference_samples"] = refs
        m.meta.setdefault("reference_from", f"SDRF {rel} (pooled)")
    said: dict[str, set[str]] = defaultdict(set)   # condition -> the role(s) its rows give it
    for s, r in hits.items():
        if r.role:
            said[m.condition[s]].add(r.role)
    for c, rs in said.items():
        if len(rs) > 1:
            info["notes"].append(f"the SDRF gives {c} more than one role ({', '.join(sorted(rs))}); not used")
    given = {c: next(iter(rs)) for c, rs in said.items() if len(rs) == 1}
    if given:
        m.meta["roles"] = given
    if not plex:
        m.meta.pop("plex", None)
    # technical replicates / fractions of one sample kept as separate columns: say so
    src = Counter((r.source, r.fraction) for r in hits.values() if not r.channel)
    tech = sorted({k[0] for k, n in src.items() if n > 1})
    if tech:
        info["notes"].append("technical replicates of " + ", ".join(tech[:6]) + " are separate samples in the "
                             "statistics (the SDRF's comment[technical replicate])")
    fr = Counter((r.source, r.techrep) for r in hits.values() if not r.channel)
    frac = sorted({k[0] for k, n in fr.items() if n > 1 and len({h.fraction for h in hits.values()
                                                                if h.source == k[0]}) > 1})
    if frac:
        info["notes"].append("fractions of " + ", ".join(frac[:6]) + " are separate columns in the table; engines "
                             "normally combine fractions before this step")
    info["changed"] = changed
    m.meta["sdrf"] = info
    m.meta["conditions_from"] = f"SDRF {rel}" if d.factors else m.meta.get("conditions_from", "")
    return m, info


def load_into(m: QuantMatrix | None, dest: Path, workdir: Path, table: Path | None = None,
              factor=None) -> tuple[QuantMatrix | None, list[str]]:
    """find + read + apply, for load_quantities: (matrix, notes). Never raises for a bad SDRF (a note instead)."""
    if m is None or not m.samples or m.meta.get("precomputed"):
        return m, []
    path, notes = find(dest, workdir, table)
    if path is None:
        return m, notes
    try:
        d = read(path, factor)
    except DesignError as exc:
        return m, notes + [f"SDRF not used: {exc}"]
    m, info = apply(m, d)
    if not info["used"]:
        notes.append(f"{path.name} describes none of the {len(m.samples)} runs of the table (matched by "
                     "comment[data file], label, source name), so it was not used")
    else:
        what = "conditions and replicates" if d.factors else "replicates"
        notes.append(f"{what} from the SDRF {path.name} for {info['matched']} of {info['samples']} samples"
                     + (f" (condition: {', '.join(d.factors)})" if d.factors else ""))
    return m, notes + info["notes"]
