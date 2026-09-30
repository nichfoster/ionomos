"""Inputs for the design / F-test / DEqMS golden tests (run once; R's output is committed next to them).

    python make_design_inputs.py && Rscript run_design_reference.R <R library with limma and DEqMS>

Three conditions × four replicates. Replicate k of every condition shares a batch shift (the lab's
"replicate number is the batch", D35), each sample has an age (numeric) and a sex (factor), and every
protein a peptide count that goes with its variance (few peptides, noisier), as DEqMS assumes.
"""
import random

rng = random.Random(42)
conds = ["DMSO", "DrugA", "DrugB"]
reps = [1, 2, 3, 4]
samples = [f"{c}_{r}" for c in conds for r in reps]
batch = {r: rng.gauss(0, 0.8) for r in reps}
age = {s: round(rng.uniform(30, 70), 1) for s in samples}
sex = {s: rng.choice(["F", "M"]) for s in samples}
sex["DMSO_1"], sex["DMSO_2"] = "F", "M"  # both levels in the control, whatever the draws
with open("design_samples.tsv", "w", encoding="utf-8") as fh:
    fh.write("sample\tcondition\treplicate\tage\tsex\n")
    for s in samples:
        c, r = s.rsplit("_", 1)
        fh.write(f"{s}\t{c}\t{r}\t{age[s]}\t{sex[s]}\n")
full, miss = [], []
counts = []
for g in range(200):
    pep = max(1, int(rng.expovariate(1 / 6)))
    sd = 0.12 + 0.9 / (pep ** 0.5) * rng.uniform(0.5, 1.5)
    base = rng.gauss(23, 2)
    eff = {"DMSO": 0.0, "DrugA": rng.choice([0] * 7 + [1.4, -1.5]), "DrugB": rng.choice([0] * 8 + [1.9])}
    slope = rng.choice([0] * 9 + [0.03])  # an age effect in a few proteins
    row = []
    for s in samples:
        c, r = s.rsplit("_", 1)
        row.append(base + eff[c] + batch[int(r)] + slope * (age[s] - 50) + rng.gauss(0, sd))
    full.append(row)
    # missing values, but each condition keeps >= 2 so every row has residual df
    m = list(row)
    if g % 5 == 0:
        for c in conds:
            if rng.random() < 0.5:
                k = rng.randrange(4)
                m[conds.index(c) * 4 + k] = None
    if g % 41 == 7:  # absent from DMSO: its contrasts with DMSO can't be estimated
        for k in range(4):
            m[k] = None
    miss.append(m)
    counts.append(pep)


def write(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("ID\t" + "\t".join(samples) + "\n")
        for g, row in enumerate(rows):
            fh.write(f"P{g:04d}\t" + "\t".join("NA" if v is None else f"{v:.6f}" for v in row) + "\n")


write("design_matrix.tsv", full)
write("design_matrix_missing.tsv", miss)
with open("design_counts.tsv", "w", encoding="utf-8") as fh:
    fh.write("ID\tcount\n")
    for g, c in enumerate(counts):
        fh.write(f"P{g:04d}\t{c}\n")
