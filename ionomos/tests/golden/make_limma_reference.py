"""Input for the limma golden test (run once; limma's output is committed as limma_*.tsv)."""
import random

rng = random.Random(11)
n = 400
with open("limma_two_group.tsv", "w", encoding="utf-8") as fh:
    fh.write("id\t" + "\t".join([f"A{i}" for i in range(1, 5)] + [f"B{i}" for i in range(1, 4)]) + "\n")
    for g in range(n):
        base, sd = rng.gauss(22, 2), rng.uniform(0.1, 0.8)
        eff = rng.choice([0] * 8 + [1.5, -1.5])
        a = [base + eff + rng.gauss(0, sd) for _ in range(4)]
        b = [base + rng.gauss(0, sd) for _ in range(3)]
        if g % 7 == 0:  # a missing value, still >= 2 per group
            a[rng.randrange(4)] = None
        cells = ["NA" if v is None else f"{v:.5f}" for v in a + b]
        fh.write(f"g{g}\t" + "\t".join(cells) + "\n")
with open("limma_one_sample.tsv", "w", encoding="utf-8") as fh:
    fh.write("id\tR1\tR2\tR3\n")
    for g in range(n):
        eff = rng.choice([0] * 9 + [2.2])
        vals = [eff + rng.gauss(0, rng.uniform(0.2, 0.6)) for _ in range(3)]
        cells = [f"{v:.5f}" for v in vals]
        if g % 9 == 0:
            cells[rng.randrange(3)] = "NA"
        fh.write(f"s{g}\t" + "\t".join(cells) + "\n")
