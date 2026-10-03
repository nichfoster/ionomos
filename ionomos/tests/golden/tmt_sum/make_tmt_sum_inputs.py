"""Inputs for run_tmt_sum_reference.R: three TMT plexes of 3 DMSO + 3 Drug channels and no reference channel, so
the plexes are put on one scale by their own means (irs: sum), 300 proteins in log2. Deterministic (D71).

    python make_tmt_sum_inputs.py && Rscript run_tmt_sum_reference.R <R library with limma>

Per protein: a base level, its own replicate SD, a plex effect (SD 1 log2), a loading per channel, 10 % changed
either way in Drug. Values go missing from a whole plex (more often at low abundance) or from a single channel,
which makes IRS on the plex means drop that plex for the protein. Rows built for the edges: a protein seen in one
plex only (no df spent), in two of the three, with one channel missing in every plex (no plex can be scaled, the
row is left as it was), and with no value at all."""
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLEXES, LAYOUT = 3, ["DMSO"] * 3 + ["Drug"] * 3


def main() -> None:
    rng = random.Random(7101)
    samples = [(f"{c}_{k + 1}_P{p}", c, f"P{p}") for p in range(1, PLEXES + 1) for k, c in enumerate(LAYOUT)]
    load = [rng.gauss(0, 0.3) for _ in samples]
    rows = []
    for i in range(300):
        base, sd = rng.gauss(22, 2.2), rng.uniform(0.12, 0.5)
        eff = rng.choice((-1, 1)) * rng.uniform(0.8, 2.0) if rng.random() < 0.1 else 0.0
        shift = {f"P{p}": rng.gauss(0, 1.0) for p in range(1, PLEXES + 1)}
        gone = {p for p in shift if rng.random() < (0.25 if base < 20 else 0.03)}
        vals = []
        for (_s, c, p), ld in zip(samples, load, strict=True):
            v = base + (eff if c == "Drug" else 0.0) + shift[p] + ld + rng.gauss(0, sd)
            vals.append(None if p in gone or rng.random() < 0.02 else v)
        k = i % 29
        if k == 5:      # one plex only
            vals[6:] = [None] * 12
        elif k == 9:    # two plexes of three
            vals[12:] = [None] * 6
        elif k == 13:   # a channel missing in every plex: nothing can be scaled
            vals[0] = vals[7] = vals[14] = None
        if i in (60, 211):  # no value at all
            vals = [None] * len(samples)
        rows.append(vals)
    (HERE / "tmt_sum_samples.tsv").write_text("sample\tcondition\tplex\n" + "".join(
        f"{s}\t{c}\t{p}\n" for s, c, p in samples), encoding="utf-8")
    (HERE / "tmt_sum_matrix.tsv").write_text("ID\t" + "\t".join(s for s, _c, _p in samples) + "\n" + "".join(
        f"P{i:04d}\t" + "\t".join("NA" if v is None else repr(v) for v in r) + "\n" for i, r in enumerate(rows)),
        encoding="utf-8")


if __name__ == "__main__":
    main()
