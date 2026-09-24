"""
QuantMatrix: one shape for every method's result table, so statistics, plots and
reports are written once.

    features x samples of log2 values (None = missing), plus sample -> condition.
    kind "intensity": log2 abundances; conditions are compared with each other.
    kind "ratio":     log2 ratios (isoDTB heavy/light); each condition is tested against 0.

Loaders (all tolerant of column order and extra columns):
    from_isodtb_sites(<prefix>_sites.tsv)              site level, ratio
    from_pg_matrix(DIA-NN *pg_matrix.tsv, samples)     protein level, intensity
    from_combined_protein(FragPipe combined_protein.tsv)  protein level, intensity (DDA label-free)
    from_tmt_abundance(tmt-report/abundance_*_MD.tsv)  gene/protein level, intensity (already log2)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ionomos.downstream.tables import NA_STRINGS, num, read_tsv

RAW_EXTS = (".raw", ".mzml", ".mzxml", ".d", ".dia", ".wiff", ".mgf")


@dataclass
class Feature:
    id: str
    label: str
    description: str = ""


@dataclass
class QuantMatrix:
    kind: str  # "intensity" | "ratio"
    level: str  # "protein" | "gene" | "site"
    features: list[Feature]
    samples: list[str]
    values: list[list[float | None]]  # [feature][sample], log2
    condition: dict[str, str]
    source: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def conditions(self) -> list[str]:
        seen = []
        for s in self.samples:
            c = self.condition[s]
            if c not in seen:
                seen.append(c)
        return seen

    def column(self, sample: str) -> list[float | None]:
        j = self.samples.index(sample)
        return [row[j] for row in self.values]

    def samples_of(self, cond: str) -> list[str]:
        return [s for s in self.samples if self.condition[s] == cond]


def _log2(v: float | None) -> float | None:
    return math.log2(v) if v is not None and v > 0 else None


def condition_of(sample: str) -> str:
    """'DMSO_3' -> 'DMSO'; 'Drug-A_1_126' -> 'Drug-A' (the lab's R rule: text before the first '_'),
    'x' -> 'x'."""
    return re.match(r"^[^_]+", sample).group(0) if sample else sample


def run_stem(col: str) -> str:
    """'C:\\\\data\\\\DMSO_1.raw' or '/x/DMSO_1.mzML' -> 'DMSO_1'."""
    name = PureWindowsPath(col).name if ("\\" in col or ":" in col[:3]) else Path(col).name
    low = name.lower()
    for ext in RAW_EXTS:
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


# ---------------------------------------------------------------- isoDTB --


def from_isodtb_sites(path: Path) -> QuantMatrix:
    header, rows = read_tsv(path)
    cols = [h for h in header if re.match(r"^Mean_(.+)_(\d+) Log2 Ratio HL$", h)]
    samples = [re.match(r"^Mean_(.+ ?)Log2 Ratio HL$", c).group(1).strip() for c in cols]
    prefix = re.match(r"^Mean_(.+)_\d+ Log2 Ratio HL$", cols[0]).group(1) if cols else Path(path).stem
    feats, vals = [], []
    for r in rows:
        residue = r.get("ModifiedResidue", "")
        pos = r.get("ResiduePositionInProtein", "")
        pos = pos[:-2] if pos.endswith(".0") else pos
        name = r.get("Gene") if r.get("Gene") not in (None, "", "NA") else (r.get("Entry Name") or r.get("Protein", ""))
        feats.append(Feature(id=f"{r.get('Protein', '')}|{residue}{pos}", label=f"{name} {residue}{pos}",
                             description=r.get("Protein Description", "") if r.get("Protein Description") != "NA" else ""))
        vals.append([num(r.get(c)) for c in cols])
    return QuantMatrix("ratio", "site", feats, samples, vals, {s: prefix for s in samples}, str(path))


# ------------------------------------------------------------------- DIA --

_PG_META = {"Protein.Group", "Protein.Ids", "Protein.Names", "Genes", "First.Protein.Description",
            "N.Sequences", "N.Proteotypic.Sequences"}


def from_pg_matrix(path: Path, sample_map: dict[str, tuple[str, int]] | None = None) -> QuantMatrix:
    """DIA-NN protein-group matrix. sample_map: run file stem -> (condition, replicate), from the manifest."""
    header, rows = read_tsv(path)
    sample_map = sample_map or {}
    probe = rows[:300]
    notes = []
    matched = set()

    def match_run(stem):
        # Preserve exact identity first; only then remove known conversion suffixes.
        from ionomos.naming import strip_acq_stamp

        candidates = [stem, re.sub(r"_(?:uncalibrated|calibrated)$", "", stem, flags=re.IGNORECASE)]
        for candidate in candidates:
            if candidate in sample_map:
                return candidate
        canonical = strip_acq_stamp(candidates[-1])
        hits = [key for key in sample_map if strip_acq_stamp(key) == canonical]
        return hits[0] if len(hits) == 1 else None


    def numeric(h: str) -> bool:  # a run column holds numbers (or blanks); annotation columns hold text
        vals = [r.get(h, "") for r in probe if r.get(h, "").strip() not in NA_STRINGS]
        return all(num(v) is not None for v in vals)

    runs = [h for h in header if h not in _PG_META and (match_run(run_stem(h)) is not None or numeric(h))]
    samples, cond = [], {}
    for h in runs:
        stem = run_stem(h)
        key = match_run(stem)
        if key is not None:
            matched.add(key)
            c, rep = sample_map[key]
            if not numeric(h):
                notes.append(f"Run {stem}: nonnumeric quantities were treated as missing")
            s = f"{c}_{rep}"
        else:
            clean = re.sub(r"_(?:uncalibrated|calibrated)$", "", stem, flags=re.IGNORECASE)
            s, c = stem, _dia_condition(clean)
            if sample_map:
                notes.append(f"Run {stem} did not match the manifest; inferred condition {c!r}. Check sample labels.")
        base, k = s, 2
        while s in cond:  # two runs of one sample (technical reps): keep both
            s, k = f"{base}.{k}", k + 1
        samples.append(s)
        cond[s] = c
    feats, vals = [], []
    for r in rows:
        genes = r.get("Genes") or ""
        gid = r.get("Protein.Group") or r.get("Protein.Ids") or ""
        feats.append(Feature(id=gid, label=genes.split(";")[0] or (r.get("Protein.Names") or gid).split(";")[0],
                             description=r.get("First.Protein.Description", "")))
        vals.append([_log2(num(r.get(h))) for h in runs])
    missing = sorted(set(sample_map) - matched)
    if missing:
        notes.append("Expected runs missing from the DIA protein matrix: " + ", ".join(missing) +
                     ". Check the original pg_matrix.tsv and DIA-NN logs before interpreting comparisons.")
    return QuantMatrix("intensity", "protein", feats, samples, vals, cond, str(path), notes=notes)


def _dia_condition(stem: str) -> str:
    from ionomos.naming import NamingError, parse_raw_name

    try:
        return parse_raw_name(stem + ".raw", "DIA").sample
    except NamingError:
        m = re.match(r"^(.*?)[_-]?\d+$", stem)
        return m.group(1) if m and m.group(1) else stem


# --------------------------------------------------------- DDA label-free --


def from_combined_protein(path: Path) -> QuantMatrix:
    header, rows = read_tsv(path)
    maxlfq = [h for h in header if h.endswith(" MaxLFQ Intensity")]
    cols = maxlfq or [h for h in header if h.endswith(" Intensity") and not h.startswith(("Unique", "Total", "Razor"))]
    suffix = " MaxLFQ Intensity" if maxlfq else " Intensity"
    samples = [c[: -len(suffix)] for c in cols]
    cond = {s: (re.match(r"^(.*)_\d+$", s).group(1) if re.match(r"^(.*)_\d+$", s) else s) for s in samples}
    feats = [Feature(id=r.get("Protein ID") or r.get("Protein", ""), label=r.get("Gene") or r.get("Entry Name") or "",
                     description=r.get("Description") or r.get("Protein Description") or "") for r in rows]
    vals = [[_log2(num(r.get(c))) for c in cols] for r in rows]
    return QuantMatrix("intensity", "protein", feats, samples, vals, cond, str(path),
                       notes=[f"quantity: {suffix.strip()}"])


# ------------------------------------------------------------------- TMT --

_TMT_META = {"Index", "Gene", "NumberPSM", "ProteinID", "Protein ID", "MaxPepProb", "ReferenceIntensity", "Peptide",
             "Start", "End", "Protein"}


def from_tmt_abundance(path: Path, annotation: list[dict] | None = None) -> QuantMatrix:
    """TMT-Integrator abundance table (values are already log2 ratios to the reference)."""
    header, rows = read_tsv(path)
    by_sample = {a["sample"]: a for a in annotation or []}
    start = header.index("ReferenceIntensity") + 1 if "ReferenceIntensity" in header else 0
    cols = [h for h in header[start:] if h not in _TMT_META]
    cols = [c for c in cols if sum(num(r.get(c)) is not None for r in rows[:200]) > 0] or cols
    cond = {c: (by_sample[c]["condition"] if c in by_sample else condition_of(c)) for c in cols}
    feats = [Feature(id=r.get("Index") or r.get("ProteinID", ""), label=r.get("Index") or r.get("Gene") or "",
                     description=r.get("ProteinID", "")) for r in rows]
    vals = [[num(r.get(c)) for c in cols] for r in rows]
    return QuantMatrix("intensity", "gene" if "gene" in Path(path).name else "protein", feats, cols, vals, cond,
                       str(path), notes=["TMT-Integrator values are log2 ratios to the reference channel"])
