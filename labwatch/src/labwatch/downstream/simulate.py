"""
Realistic FragPipe result tables with known truth, for the testbed's fake FragPipe and for tests.

Column names and layouts follow the real files:
    isoDTB  combined_modified_peptide_label_quant.tsv   ("<exp>_<rep> Log2 Ratio HL", "[561.3387]" labels)
    DIA     diann-output/report.pg_matrix.tsv           (Protein.Group, Genes, ..., one column per run path)
    TMT     tmt-report/abundance_gene_MD.tsv            (Index, NumberPSM, ProteinID, MaxPepProb,
                                                         ReferenceIntensity, one log2 column per sample)

Each writer returns the planted truth so a test can measure recall and false
discoveries of the whole downstream pipeline.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from labwatch.downstream.analysis import DEFAULT_CONTROL_KEYWORDS

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
                  changed_fraction: float = 0.1, effect: float = 2.0) -> dict[str, dict[str, int]]:
    """runs = [(run file path as FragPipe gives it, condition)]. Returns {condition: {gene: +1/-1}} vs control."""
    rng = random.Random(seed)
    conds = list(dict.fromkeys(c for _, c in runs))
    ctrl = _control(conds)
    truth: dict[str, dict[str, int]] = {c: {} for c in conds if c != ctrl}
    shift = {r: rng.gauss(0, 0.4) for r, _ in runs}  # loading differences -> median normalisation matters
    header = ["Protein.Group", "Protein.Ids", "Protein.Names", "Genes", "First.Protein.Description",
              *[r for r, _ in runs]]
    lines = []
    for i in range(n_proteins):
        g = _gene(i)
        base = rng.gauss(22, 2.2)
        eff = {}
        for c in truth:
            if rng.random() < changed_fraction:
                sign = rng.choice((1, -1))
                eff[c] = sign * effect
                truth[c][g] = sign
        row = [f"P{20000 + i}", f"P{20000 + i}", f"{g}_HUMAN", g, f"{g} protein"]
        for r, c in runs:
            v = base + eff.get(c, 0.0) + shift[r] + rng.gauss(0, 0.3)
            p_missing = 0.02 + max(0.0, (19 - v)) * 0.15  # low abundance goes missing more often
            row.append("" if rng.random() < p_missing else f"{2 ** v:.1f}")
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
