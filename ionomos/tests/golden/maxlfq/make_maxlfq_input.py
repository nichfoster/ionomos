"""Builds input.tsv for the MaxLFQ golden test (tests/test_rollup.py, D76). Run once; the output is committed.

    python make_maxlfq_input.py && Rscript run_maxlfq_reference.R [R library with iq and diann]

input.tsv is long: protein, feature, sample, intensity (linear, > 0; a missing value has no row). Hand-made
proteins cover the cases that matter (disconnected sample groups, a sample with no value, a single feature, a
single sample, an even number of ratios, a chain of samples linked only through each other), then random proteins
with abundance-dependent missing values and feature offsets.
"""
import random

SAMPLES = [f"S{k}" for k in range(1, 9)]
rng = random.Random(20261004)
rows: list[tuple[str, str, str, float]] = []


def add(protein: str, table: dict[str, dict[str, float]]) -> None:
    """table: feature -> {sample: log2 intensity}"""
    for feat, vals in table.items():
        for s in SAMPLES:
            if s in vals:
                rows.append((protein, feat, s, 2.0 ** vals[s]))


def profile(level: float, spread: float = 0.6) -> dict[str, float]:
    return {s: level + rng.gauss(0, spread) for s in SAMPLES}


def peptides(prof: dict[str, float], n: int, samples=SAMPLES, noise: float = 0.2, offset_sd: float = 1.5,
             tag: str = "PEP") -> dict[str, dict[str, float]]:
    out = {}
    for k in range(n):
        off = rng.gauss(0, offset_sd)
        out[f"{tag}{k + 1}"] = {s: prof[s] + off + rng.gauss(0, noise) for s in samples}
    return out


# every feature in every sample
add("P_full", peptides(profile(22), 6))
# about a third of the values missing at random
t = peptides(profile(21), 8)
add("P_missing", {f: {s: v for s, v in vals.items() if rng.random() > 0.33} for f, vals in t.items()})
# two groups of samples that share no feature: S1-S4 and S5-S8
p = profile(20)
add("P_two_groups", {**peptides(p, 3, SAMPLES[:4], tag="A"), **peptides(p, 2, SAMPLES[4:], tag="B")})
# three components: S1-S2, S3-S5, S6 alone (two features), S7 and S8 without any value
p = profile(23)
add("P_three_groups", {**peptides(p, 2, SAMPLES[:2], tag="A"), **peptides(p, 3, SAMPLES[2:5], tag="B"),
                       **peptides(p, 2, SAMPLES[5:6], tag="C")})
# one feature, missing in two samples
add("P_one_feature", {"PEP1": {s: v for s, v in profile(19).items() if s not in ("S2", "S7")}})
# one sample only (three features)
add("P_one_sample", peptides(profile(18), 3, ["S3"]))
# two features: every pairwise median is the mean of two ratios
add("P_even", peptides(profile(24), 2))
# a chain: S1-S2 share a feature, S2-S3 another, ... so S1 and S8 are linked only through the others
p = profile(21)
add("P_chain", {f"CH{k}": {SAMPLES[k - 1]: p[SAMPLES[k - 1]] + 0.3 * k + rng.gauss(0, 0.2),
                           SAMPLES[k]: p[SAMPLES[k]] + 0.3 * k + rng.gauss(0, 0.2)} for k in range(1, 8)})
# a feature far above the others (MaxLFQ's medians are robust to it; the summed intensity is not)
t = peptides(profile(20), 5)
t["LOUD"] = {s: v + 8 for s, v in t["PEP1"].items()}
add("P_loud", t)
# random proteins: 1-15 features, offsets, abundance-dependent missing values, now and then a lost sample
for i in range(45):
    p = profile(rng.gauss(21, 2.5), rng.choice((0.3, 0.8, 1.5)))
    t = peptides(p, rng.randint(1, 15), noise=rng.choice((0.1, 0.3)))
    lost = {s for s in SAMPLES if rng.random() < 0.08}
    kept = {}
    for f, vals in t.items():
        keep = {s: v for s, v in vals.items() if s not in lost and rng.random() > 0.05 + max(0.0, 20 - v) * 0.12}
        if keep:
            kept[f] = keep
    if kept:
        add(f"R{i + 1:02d}", kept)

with open("input.tsv", "w", encoding="utf-8", newline="\n") as fh:
    fh.write("protein\tfeature\tsample\tintensity\n")
    for prot, feat, s, v in rows:
        fh.write(f"{prot}\t{feat}\t{s}\t{v!r}\n")
print(f"input.tsv: {len(rows)} values, {len({r[0] for r in rows})} proteins")
