"""Inputs for the FragPipeAnalystR golden tests (run once; R's output is committed next to them).

    python make_fpa_inputs.py && Rscript run_fpa_reference.R <R library with limma>
"""
import random

rng = random.Random(7)
conds = {"DMSO": 3, "DrugA": 3, "DrugB": 4}
samples = [f"{c}_{r}" for c, n in conds.items() for r in range(1, n + 1)]
with open("fpa_matrix.tsv", "w", encoding="utf-8") as fh:
    fh.write("ID\t" + "\t".join(samples) + "\n")
    for g in range(300):
        base, sd = rng.gauss(24, 2.5), rng.uniform(0.15, 0.7)
        eff = {"DMSO": 0.0, "DrugA": rng.choice([0] * 7 + [1.6, -1.8]), "DrugB": rng.choice([0] * 8 + [2.1])}
        row = []
        for s in samples:
            c = s.rsplit("_", 1)[0]
            v = base + eff[c] + rng.gauss(0, sd)
            # low abundance -> more missing; a few proteins absent from one condition entirely
            miss = rng.random() < (0.35 if base < 21.5 else 0.06)
            if g % 37 == 5 and c == "DMSO":
                miss = True
            row.append("NA" if miss else f"{v:.6f}")
        if g % 53 == 11:  # all missing: dropped by impute()
            row = ["NA"] * len(samples)
        fh.write(f"P{g:04d}\t" + "\t".join(row) + "\n")
with open("fpa_design.tsv", "w", encoding="utf-8") as fh:
    fh.write("sample_name\tcondition\treplicate\n")
    for s in samples:
        c, r = s.rsplit("_", 1)
        fh.write(f"{s}\t{c}\t{r}\n")
