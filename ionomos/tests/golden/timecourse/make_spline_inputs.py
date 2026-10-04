"""Inputs for run_spline_reference.R (D77): 150 features x 51 samples (Drug and DMSO at 8 time points from 0 to
48 h, three replicates, and a Pool condition outside the time course), complete and with missing values; smooth
planted shapes in the first 40 features of Drug. Deterministic."""
import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERIES = ("Drug", "DMSO")
TIMES = (("0h", 0.0), ("30min", 0.5), ("1h", 1.0), ("2h", 2.0), ("4h", 4.0), ("8h", 8.0), ("24h", 24.0), ("48h", 48.0))
REPS = (1, 2, 3)


def shape(kind: str, t: float) -> float:
    return {"up": 2 * (1 - math.exp(-t / 4)), "down": -2 * (1 - math.exp(-t / 8)),
            "pulse": 2 * (t / 4) * math.exp(1 - t / 4)}.get(kind, 0.0)


def main() -> None:
    rng = random.Random(77)
    samples = [(f"{s}_{lab}_{r}", f"{s}_{lab}", s, t, r) for s in SERIES for lab, t in TIMES for r in REPS]
    samples += [(f"Pool_{r}", "Pool", "", "", r) for r in REPS]
    rows, miss = [], []
    for i in range(150):
        base = rng.gauss(22, 2)
        kind = rng.choice(("up", "down", "pulse")) if i < 40 else ""
        vals = [base + (shape(kind, t) if s == "Drug" else 0.0) + 0.3 * r + rng.gauss(0, 0.3)
                for _n, _c, s, t, r in samples]
        rows.append(vals)
        miss.append([None if rng.random() < 0.08 else v for v in vals])
    (HERE / "sp_samples.tsv").write_text("sample\tcondition\tseries\ttime\treplicate\n" + "".join(
        f"{n}\t{c}\t{s}\t{t}\t{r}\n" for n, c, s, t, r in samples), encoding="utf-8")
    for name, data in (("sp_matrix.tsv", rows), ("sp_matrix_missing.tsv", miss)):
        (HERE / name).write_text("ID\t" + "\t".join(x[0] for x in samples) + "\n" + "".join(
            f"F{i}\t" + "\t".join("NA" if v is None else repr(v) for v in r) + "\n" for i, r in enumerate(data)),
            encoding="utf-8")


if __name__ == "__main__":
    main()
