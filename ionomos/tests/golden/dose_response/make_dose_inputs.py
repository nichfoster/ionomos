"""Inputs for the CurveCurator golden test (tests/test_dose_response.py). Run once; outputs are committed.

    cd ionomos && .venv/bin/python tests/golden/dose_response/make_dose_inputs.py
    <a Python 3.11-3.13 venv with curve-curator>/bin/python tests/golden/dose_response/run_curvecurator.py

Two designs, both simulated by downstream/simulate.py dose_titration (planted curves with known pEC50s, flat and
noisy features, a few missing values):
    A  decryptM-like: DMSO x 2 + 8 doses (1 nM - 3 uM), one replicate each
    B  replicated:    DMSO x 3 + 5 doses (10 nM - 100 uM) x 3 replicates
Each design writes <design>_matrix.tsv (id, gene, truth, true_pec50, one log2 intensity column per sample) and
<design>_design.tsv (sample, condition, dose_nM).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from ionomos.downstream import simulate  # noqa: E402

DESIGNS = {
    "A": dict(doses_nm=[1, 3, 10, 30, 100, 300, 1000, 3000], replicates=1, controls=2, n=300, seed=11, noise=0.12, curve_fraction=0.4),
    "B": dict(doses_nm=[10, 100, 1000, 10000, 100000], replicates=3, controls=3, n=300, seed=12, noise=0.2, curve_fraction=0.4),
}


def main() -> None:
    for name, kw in DESIGNS.items():
        samples, cond, dose, rows, truth = simulate.dose_titration(**kw)
        with open(HERE / f"{name}_design.tsv", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("sample\tcondition\tdose_nM\n")
            for s in samples:
                fh.write(f"{s}\t{cond[s]}\t{dose[s]:g}\n")
        with open(HERE / f"{name}_matrix.tsv", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\t".join(["id", "gene", "truth", "true_pec50", *samples]) + "\n")
            for pid, g, vals in rows:
                t = truth[pid]
                pe = "" if t["pec50"] is None else f"{t['pec50']:.4f}"
                fh.write("\t".join([pid, g, t["class"], pe, *["" if v is None else f"{v:.5f}" for v in vals]]) + "\n")
        print(f"wrote {name}: {len(samples)} samples x {len(rows)} features")


if __name__ == "__main__":
    main()
