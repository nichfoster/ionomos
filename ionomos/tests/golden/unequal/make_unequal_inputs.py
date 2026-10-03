"""Inputs for run_unequal_reference.R: a competition experiment with unequal groups (DMSO n=2, Probe n=4,
Probe_Comp n=4), 320 proteins in log2, with missing values. Deterministic (D66).

    python make_unequal_inputs.py && Rscript run_unequal_reference.R <R library with limma>

Planted: specific targets (up with the probe, back to DMSO with the competitor), unspecific binders (up in both),
a few random changes, values missing more often at low abundance, and rows built to sit on each edge of the
filters: one DMSO value of two (tested with small_group_min_valid: half, not with same), no DMSO value (never
tested against DMSO), one value in a group of four (not tested in the equal 4 vs 4 comparison), and rows the
FragPipe-Analyst filter removes (fewer than half the values in every condition) or that have no value at all."""
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZES = {"DMSO": 2, "Probe": 4, "Probe_Comp": 4}


def main() -> None:
    rng = random.Random(661)
    samples = [(f"{c}_{r}", c) for c, n in SIZES.items() for r in range(1, n + 1)]
    rows = []
    for i in range(320):
        base, sd = rng.gauss(23, 2.4), rng.uniform(0.15, 0.6)
        eff = {"DMSO": 0.0, "Probe": 0.0, "Probe_Comp": 0.0}
        if i < 24:                      # specific targets: enriched by the probe, competed off
            eff["Probe"] = rng.uniform(2.0, 3.5)
        elif i < 40:                    # unspecific binders: enriched with or without the competitor
            eff["Probe"] = eff["Probe_Comp"] = rng.uniform(1.5, 3.0)
        elif rng.random() < 0.06:       # other changes, either way
            eff["Probe"] = rng.choice((-1, 1)) * rng.uniform(0.8, 2.0)
            eff["Probe_Comp"] = eff["Probe"] * rng.uniform(0, 1)
        vals = []
        for _s, c in samples:
            v = base + eff[c] + rng.gauss(0, sd)
            gone = rng.random() < (0.30 if base < 21 else 0.05)
            vals.append(None if gone else v)
        k = i % 23
        if k == 3:                      # one DMSO value of two
            vals[1] = None
            vals[0] = vals[0] if vals[0] is not None else base + rng.gauss(0, sd)
        elif k == 7:                    # no DMSO value
            vals[0] = vals[1] = None
        elif k == 11:                   # one Probe_Comp value of four
            vals[6:10] = [None, None, None, base + eff["Probe_Comp"] + rng.gauss(0, sd)]
        elif k == 13:                   # one Probe value of four, one DMSO value: only Probe_Comp vs DMSO (half)
            vals[1] = None
            vals[2:6] = [base + rng.gauss(0, sd), None, None, None]
        elif k == 17:                   # removed by the filter: below half in every condition
            vals = [None, None, base, None, None, None, None, None, base + 0.1, None]
        if i in (50, 151):              # no value at all
            vals = [None] * len(samples)
        rows.append(vals)
    (HERE / "unequal_samples.tsv").write_text("sample\tcondition\n" + "".join(f"{s}\t{c}\n" for s, c in samples),
                                              encoding="utf-8")
    (HERE / "unequal_matrix.tsv").write_text("ID\t" + "\t".join(s for s, _c in samples) + "\n" + "".join(
        f"P{i:04d}\t" + "\t".join("NA" if v is None else repr(v) for v in r) + "\n" for i, r in enumerate(rows)),
        encoding="utf-8")


if __name__ == "__main__":
    main()
