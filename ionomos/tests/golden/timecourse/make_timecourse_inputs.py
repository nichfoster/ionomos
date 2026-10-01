"""Inputs for run_timecourse_reference.R: 150 features x 24 samples (Drug and DMSO at 0h, 1h, 4h, 24h, three
replicates), complete and with missing values. Deterministic."""
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERIES, TIMES, REPS = ("Drug", "DMSO"), ("0h", "1h", "4h", "24h"), (1, 2, 3)


def main() -> None:
    rng = random.Random(42)
    samples = [(f"{s}_{t}_{r}", f"{s}_{t}", r) for s in SERIES for t in TIMES for r in REPS]
    rows, miss = [], []
    for i in range(150):
        base = rng.gauss(22, 2)
        shape = rng.choice(("up", "down", "pulse")) if i < 40 else ""
        vals = []
        for _name, cond, rep in samples:
            series, t = cond.split("_")
            frac = TIMES.index(t) / 3
            eff = {"up": 2 * frac, "down": -2 * frac, "pulse": 2 * (1 - abs(2 * frac - 1))}.get(shape, 0.0) \
                if series == "Drug" else 0.0
            vals.append(base + eff + 0.3 * rep + rng.gauss(0, 0.3))
        rows.append(vals)
        miss.append([None if rng.random() < 0.08 else v for v in vals])
    (HERE / "tc_samples.tsv").write_text("sample\tcondition\treplicate\n" + "".join(
        f"{n}\t{c}\t{r}\n" for n, c, r in samples), encoding="utf-8")
    for name, data in (("tc_matrix.tsv", rows), ("tc_matrix_missing.tsv", miss)):
        (HERE / name).write_text("ID\t" + "\t".join(n for n, _c, _r in samples) + "\n" + "".join(
            f"F{i}\t" + "\t".join("NA" if v is None else repr(v) for v in r) + "\n" for i, r in enumerate(data)),
            encoding="utf-8")


if __name__ == "__main__":
    main()
