"""
Realistic FragPipe result tables with known truth, for the testbed's fake FragPipe and for tests.

Column names and layouts follow the real files:
    isoDTB  combined_modified_peptide_label_quant.tsv   ("<exp>_<rep> Log2 Ratio HL", "[561.3387]" labels)
    DIA     diann-output/report.pg_matrix.tsv           (Protein.Group, Genes, ..., one column per run path)
    TMT     tmt-report/abundance_gene_MD.tsv            (Index, NumberPSM, ProteinID, MaxPepProb,
                                                         ReferenceIntensity, one log2 column per sample)
    doses   dose_titration / dose_pg_matrix             (a compound titration: DMSO + Cmpd_<dose> runs, planted
                                                         log-logistic curves with known pEC50s, flat and noisy features)

Each writer returns the planted truth so a test can measure recall and false
discoveries of the whole downstream pipeline.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from ionomos.downstream.analysis import DEFAULT_CONTROL_KEYWORDS

AA = "ADEFGHIKLMNPQRSTVWY"
GENES = ["ACTB", "GAPDH", "PKM", "ENO1", "HSP90AA1", "TUBB", "EEF1A1", "PARK7", "GSTP1", "PRDX1", "BTK", "EGFR",
         "KRAS", "CASP8", "PPIA", "TXN", "ALDOA", "LDHA", "YWHAZ", "HNRNPA1", "NPM1", "VIM", "PFN1", "CFL1", "UBB"]


def _gene(i: int) -> str:
    return GENES[i] if i < len(GENES) else f"GENE{i}"


def _pep(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(AA) for _ in range(n))


def _control(conds: list[str]) -> str:
    for kw in DEFAULT_CONTROL_KEYWORDS:
        for c in conds:
            if kw.lower() in c.lower().replace("-", "_").split("_") or c.lower() == kw.lower():
                return c
    return sorted(conds)[0]


def isodtb_label_quant(path: Path, experiments: dict[str, list[int]], seed: int = 1, n_sites: int = 300,
                       hit_fraction: float = 0.08) -> set[str]:
    """Returns the planted hit sites as 'Protein|C<pos>' ids (as quant.from_isodtb_sites names them)."""
    rng = random.Random(seed)
    tag, heavy = "[561.3387]", "[567.3462]"
    cols = [f"{e}_{r} Log2 Ratio HL" for e, reps in experiments.items() for r in reps]
    header = ["Peptide Sequence", "Modified Sequence", "Light Modified Peptide", "Heavy Modified Peptide",
              "Start", "End", "Protein", "Protein ID", "Entry Name", "Gene", "Protein Description", *cols]
    hits: set[str] = set()
    lines = []
    for i in range(n_sites):
        g = _gene(i // 3)
        pid = f"P{10000 + i // 3}"
        prot, entry = f"sp|{pid}|{g}_HUMAN", f"{g}_HUMAN"
        left, right = _pep(rng, rng.randint(2, 8)), _pep(rng, rng.randint(2, 8)) + "K"
        start = rng.randint(1, 800)
        site = start + len(left)  # position of the C in the protein
        hit = rng.random() < hit_fraction
        if hit:
            hits.add(f"{prot}|C{site}")
        n_peps = 2 if rng.random() < 0.25 else 1  # the same site seen by a missed-cleavage peptide
        for k in range(n_peps):
            lft = (_pep(rng, 2) + left) if k else left
            st = start - 2 if k else start
            seq = lft + "C" + right
            vals = []
            for _c in cols:
                if rng.random() < 0.08:
                    vals.append("")
                else:
                    mu = rng.gauss(2.4, 0.3) if hit else 0.0
                    vals.append(f"{rng.gauss(mu, 0.35):.4f}")
            light = lft + "C" + tag + right
            lines.append([seq, seq, light, light.replace(tag, heavy), str(st), str(st + len(seq) - 1), prot, pid,
                          entry, g, f"{g} protein", *vals])
    rng.shuffle(lines)
    _write(path, header, lines)
    return hits


def dia_pg_matrix(path: Path, runs: list[tuple[str, str]], seed: int = 1, n_proteins: int = 600,
                  changed_fraction: float = 0.1, effect: float = 2.0, genes: list[str] | None = None,
                  planted: dict[str, dict[str, float]] | None = None,
                  absent: dict[str, list[str]] | None = None) -> dict[str, dict[str, int]]:
    """runs = [(run file path as FragPipe gives it, condition)]. Returns {condition: {gene: +1/-1}} vs control.

    genes: names for the first proteins (the rest are GENE<i>); planted: {condition: {gene: log2 effect}} on top
    of the random changes (and winning over them); absent: {condition: [gene]} never measured in that condition
    (on/off proteins, not in the returned truth). Without these three the output is unchanged for a seed."""
    rng = random.Random(seed)
    planted = planted or {}
    absent_in: dict[str, set[str]] = {}
    for c, gs in (absent or {}).items():
        for g in gs:
            absent_in.setdefault(g, set()).add(c)
    conds = list(dict.fromkeys(c for _, c in runs))
    ctrl = _control(conds)
    truth: dict[str, dict[str, int]] = {c: {} for c in conds if c != ctrl}
    shift = {r: rng.gauss(0, 0.4) for r, _ in runs}  # loading differences -> median normalisation matters
    header = ["Protein.Group", "Protein.Ids", "Protein.Names", "Genes", "First.Protein.Description",
              "N.Sequences", "N.Proteotypic.Sequences", *[r for r, _ in runs]]
    lines = []
    for i in range(n_proteins):
        g = genes[i] if genes and i < len(genes) else _gene(i)
        base = rng.gauss(22, 2.2)
        eff = {}
        for c in truth:
            if rng.random() < changed_fraction:
                sign = rng.choice((1, -1))
                eff[c] = sign * effect
                truth[c][g] = sign
            if g in planted.get(c, {}):
                eff[c] = planted[c][g]
                if eff[c]:
                    truth[c][g] = 1 if eff[c] > 0 else -1
                else:
                    truth[c].pop(g, None)
        peptides = max(1, round((base - 17) * 1.5) + i % 3)  # more abundant, more peptides (no rng draw)
        row = [f"P{20000 + i}", f"P{20000 + i}", f"{g}_HUMAN", g, f"{g} protein", str(peptides), str(peptides)]
        for r, c in runs:
            v = base + eff.get(c, 0.0) + shift[r] + rng.gauss(0, 0.3)
            p_missing = 0.02 + max(0.0, (19 - v)) * 0.15  # low abundance goes missing more often
            gone = rng.random() < p_missing or c in absent_in.get(g, ())
            row.append("" if gone else f"{2 ** v:.1f}")
        lines.append(row)
    _write(path, header, lines)
    return truth


def tmt_abundance(path: Path, samples: list[str], seed: int = 1, n_genes: int = 500,
                  changed_fraction: float = 0.08, effect: float = 1.5) -> dict[str, dict[str, int]]:
    """samples = column names (lab convention condition_1_channel). Returns {condition: {gene: +1/-1}}."""
    rng = random.Random(seed)
    cond_of = {s: s.split("_")[0] for s in samples}
    conds = list(dict.fromkeys(cond_of.values()))
    ctrl = _control(conds)
    truth: dict[str, dict[str, int]] = {c: {} for c in conds if c != ctrl}
    header = ["Index", "NumberPSM", "ProteinID", "MaxPepProb", "ReferenceIntensity", *samples]
    lines = []
    for i in range(n_genes):
        g = _gene(i)
        eff = {}
        for c in truth:
            if rng.random() < changed_fraction:
                sign = rng.choice((1, -1))
                eff[c] = sign * effect
                truth[c][g] = sign
        vals = [f"{rng.gauss(eff.get(cond_of[s], 0.0), 0.25):.4f}" if rng.random() > 0.03 else "" for s in samples]
        lines.append([g, str(rng.randint(2, 80)), f"P{30000 + i}", "1.0000", f"{rng.gauss(18, 1.5):.3f}", *vals])
    _write(path, header, lines)
    return truth


def dose_label(nM: float) -> str:
    """10 -> '10nM', 3000 -> '3uM', 0.3 -> '0.3nM' (how a lab names its conditions)."""
    return f"{nM / 1000:g}uM" if nM >= 1000 else f"{nM:g}nM"


def dose_titration(doses_nm: list[float], replicates: int = 3, controls: int = 3, n: int = 300, seed: int = 1,
                   curve_fraction: float = 0.15, noisy_fraction: float = 0.15, noise: float = 0.15,
                   missing: float = 0.02, ratio: bool = False, compound: str = "Cmpd"):
    """A compound titration with known truth. Returns (samples, condition {sample: condition}, dose_nm {sample:
    dose, 0 = DMSO}, rows [(id, gene, [log2 value per sample or None])], truth {id: {"class": up | down | flat |
    noisy, "pec50": float | None, "back": ratio at top dose}}).

    Curves follow CurveCurator's model with front = 1: ratio = back + (1 - back) / (1 + 10^(slope (x + pEC50))),
    x = log10 M, pEC50 inside the dose range. ratio=True gives log2 ratios to DMSO (isoDTB-like, no DMSO runs);
    otherwise log2 intensities with a per-protein base level and DMSO runs as the control."""
    rng = random.Random(seed)
    samples, cond, dose = [], {}, {}
    for r in range(1, (0 if ratio else controls) + 1):
        s = f"DMSO_{r}"
        samples.append(s)
        cond[s], dose[s] = "DMSO", 0.0
    for d in doses_nm:
        for r in range(1, replicates + 1):
            c = f"{compound}_{dose_label(d)}"
            s = f"{c}_{r}"
            samples.append(s)
            cond[s], dose[s] = c, float(d)
    lo = -math.log10(max(doses_nm) * 1e-9) + 0.3
    hi = -math.log10(min(doses_nm) * 1e-9) - 0.3
    rows, truth = [], {}
    for i in range(n):
        u = rng.random()
        kind = ("down" if rng.random() < 0.6 else "up") if u < curve_fraction else \
            ("noisy" if u < curve_fraction + noisy_fraction else "flat")
        pec50 = rng.uniform(lo, hi) if kind in ("up", "down") else None
        back = rng.uniform(0.05, 0.45) if kind == "down" else rng.uniform(2.2, 5.0) if kind == "up" else 1.0
        slope = rng.uniform(0.8, 2.5)
        sd = noise * (4 if kind == "noisy" else 1)
        base = 0.0 if ratio else rng.gauss(22, 1.5)
        vals = []
        for s in samples:
            x = math.log10(dose[s] * 1e-9) if dose[s] > 0 else -math.inf
            r_true = back + (1 - back) / (1 + 10 ** (slope * (x + pec50))) if pec50 is not None and x > -math.inf else 1.0
            v = base + math.log2(r_true) + rng.gauss(0, sd)
            vals.append(None if dose[s] > 0 and rng.random() < missing else round(v, 5))
        g = _gene(i)
        pid = f"P{40000 + i}"
        rows.append((pid, g, vals))
        truth[pid] = {"class": kind, "pec50": pec50, "back": back}
    return samples, cond, dose, rows, truth


def dose_pg_matrix(path: Path, doses_nm: list[float], seed: int = 1, n: int = 300, **kw) -> dict:
    """A DIA-NN report.pg_matrix.tsv of a titration (dose_titration; runs named <condition>_<rep>.raw).
    Returns the truth."""
    samples, _cond, _dose, rows, truth = dose_titration(doses_nm, seed=seed, n=n, **kw)
    header = ["Protein.Group", "Protein.Ids", "Protein.Names", "Genes", "First.Protein.Description",
              "N.Sequences", "N.Proteotypic.Sequences", *[f"C:\\raw\\{s}.raw" for s in samples]]
    lines = [[pid, pid, f"{g}_HUMAN", g, f"{g} protein", "5", "5",
              *["" if v is None else f"{2 ** v:.2f}" for v in vals]] for pid, g, vals in rows]
    _write(path, header, lines)
    return truth


def _write(path: Path, header: list[str], lines: list[list[str]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(header) + "\n")
        for ln in lines:
            fh.write("\t".join(ln) + "\n")


def recall_and_false(found: dict[str, str], truth: dict[str, int]) -> tuple[float, int]:
    """found: {name: 'up'|'down'}; truth: {name: +1/-1}. (recall, false positives incl. wrong direction)."""
    tp = sum(1 for g, d in found.items() if g in truth and (truth[g] > 0) == (d == "up"))
    fp = len(found) - tp
    return (tp / len(truth) if truth else math.nan), fp
