"""Write input.csv: a small MSstatsTMT-format table (the real column names) for the golden test.

Two mixtures of a TMT 6-plex (126 = Norm, 127/128 = A, 129/130 = B, 131 = Empty). Mixture 1 was injected twice
(TechRepMixture 1 and 2), mixture 2 was fractionated (Fraction 1 and 2, some peptides in both), mixture 2 carries a
plex effect for every protein, a few values are missing, P7 has no Norm value in mixture 2 and P8 was only seen in
mixture 1. Deterministic (fixed seed). Then `Rscript run_msstatstmt.R <R library>` writes expected*.csv with
MSstatsTMT itself; tests/test_plexes.py compares Ionomos' msstats_tmt_summary() with them.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHANNELS = [("126", "Norm"), ("127", "A"), ("128", "A"), ("129", "B"), ("130", "B"), ("131", "Empty")]
RUNS = [("M1_T1.raw", "M1", 1, 1), ("M1_T2.raw", "M1", 2, 1), ("M2_F1.raw", "M2", 1, 1), ("M2_F2.raw", "M2", 1, 2)]


def main() -> None:
    rng = random.Random(7)
    prots = [(f"sp|P0000{k}|G{k}_HUMAN", 2 ** rng.uniform(18, 24), rng.randint(3, 4)) for k in range(1, 9)]
    plex_shift = {prot: rng.gauss(0, 0.8) for prot, _b, _n in prots}  # the same protein, a different level per plex
    rows = []
    for prot, base, npep in prots:
        for k in range(npep):
            pep = f"PEPTIDE{prot[4:10]}{'ACDEFGHK'[k]}K"
            pfac = 2 ** rng.uniform(-2, 2)
            for run, mix, tech, frac in RUNS:
                if prot.endswith("G8_HUMAN") and mix == "M2":
                    continue
                if mix == "M2" and k % 2 != frac - 1 and k != 0:  # peptide 0 in both fractions, the others in one
                    continue
                shift = plex_shift[prot] if mix == "M2" else 0.0
                for ch, cond in CHANNELS:
                    effect = {"A": 0.0, "B": 1.0 if prot.endswith(("G1_HUMAN", "G2_HUMAN")) else 0.0,
                              "Norm": 0.5, "Empty": -9.0}[cond]
                    v = base * pfac * 2 ** (shift + effect + rng.gauss(0, 0.15))
                    if frac == 2 and k == 0:
                        v *= 0.4  # the weaker fraction of a peptide seen in both
                    missing = (cond == "Norm" and prot.endswith("G7_HUMAN") and mix == "M2") or rng.random() < 0.04
                    bio = cond if cond in ("Norm", "Empty") else f"{mix}_{cond}{ch}"
                    rows.append([prot, pep, 2, f"{pep}_2", mix, tech, run, frac, ch, cond, bio,
                                 "NA" if missing else f"{v:.2f}"])
    with open(HERE / "input.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["ProteinName", "PeptideSequence", "Charge", "PSM", "Mixture", "TechRepMixture", "Run", "Fraction",
                    "Channel", "Condition", "BioReplicate", "Intensity"])
        w.writerows(rows)
    print(f"wrote {HERE / 'input.csv'} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
