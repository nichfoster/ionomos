"""Inputs for the KSEAapp golden (D79): a phosphosite table and a kinase-substrate table in KSEAapp's formats.

    cd ionomos && .venv/bin/python tests/golden/ksea/make_ksea_inputs.py

Everything is made up: no PhosphoSitePlus or NetworKIN row is copied (PhosphoSitePlus is non-commercial, so it is
never committed). Kinase names are KIN01..KIN30, substrate genes GENE001..GENE180.
    ksea_px.tsv      KSEAapp's PX: Protein, Gene, Peptide, Residue.Both (one site per row, as Ionomos' site tables),
                     p, FC (linear). Some sites are listed twice under two proteins (two isoforms), so the averaging
                     of duplicates is checked; FC is written with every digit.
    ksea_ksdata.tsv  KSEAapp's KSData columns: KINASE, KIN_ACC_ID, GENE, KIN_ORGANISM, SUBSTRATE, SUB_GENE_ID,
                     SUB_ACC_ID, SUB_GENE, SUB_ORGANISM, SUB_MOD_RSD, SITE_GRP_ID, SITE_...7_AA, networkin_score,
                     Source. PhosphoSitePlus rows have networkin_score Inf (as in KSEAapp's file); NetworKIN rows a
                     score from 1 to 10. Some PhosphoSitePlus pairs are listed twice under two KINASE names of one
                     GENE, and some sites are in the table but not measured.
run_kseaapp.R computes the reference; tests/test_phospho.py compares Ionomos with it.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    rng = random.Random(79)
    sites = []  # (accession, gene, residue+position, log2fc)
    for g in range(1, 181):
        gene, acc = f"GENE{g:03d}", f"Q{g:05d}"
        for _ in range(rng.choice((1, 1, 2, 3))):
            res = rng.choice("SSSSTTY")
            sites.append((acc, gene, f"{res}{rng.randint(5, 900)}", rng.gauss(0.1, 0.8)))
    sites = list({(a, g, r): (a, g, r, fc) for a, g, r, fc in sites}.values())
    # kinases: some move their substrates up, some down, most not
    shift = {f"KIN{k:02d}": (1.2 if k <= 3 else -1.0 if k <= 5 else 0.0) for k in range(1, 31)}
    ks, by_kin = [], {}
    for k in shift:
        n = rng.choice((1, 2, 3, 4, 6, 8, 12))
        for acc, gene, rsd, _fc in rng.sample(sites, n):
            by_kin.setdefault(k, []).append((acc, gene, rsd))
    # move the substrates of the active kinases
    moved = {}
    for k, subs in by_kin.items():
        for acc, gene, rsd in subs:
            moved[(acc, gene, rsd)] = moved.get((acc, gene, rsd), 0.0) + shift[k] * 0.5
    sites = [(a, g, r, fc + moved.get((a, g, r), 0.0)) for a, g, r, fc in sites]
    for k, subs in by_kin.items():
        for acc, gene, rsd in subs:
            ks.append([k, f"P9{k[3:]}00", k, "human", gene, str(100 + int(gene[4:])), acc, gene, "human", rsd,
                       str(rng.randint(1000, 9999)), "_______________", "Inf", "PhosphoSitePlus"])
            if rng.random() < 0.08:  # the same pair under another name of the same kinase gene
                ks.append([k + "_alt", f"P9{k[3:]}00-2", k, "human", gene, str(100 + int(gene[4:])), acc, gene,
                           "human", rsd, "1", "_______________", "Inf", "PhosphoSitePlus"])
    for _ in range(140):  # NetworKIN predictions, some on measured sites
        k = f"KIN{rng.randint(1, 30):02d}"
        acc, gene, rsd, _fc = rng.choice(sites)
        ks.append([k, f"P9{k[3:]}00", k, "human", gene, str(100 + int(gene[4:])), acc, gene, "human", rsd,
                   "", "_______________", f"{rng.uniform(1, 10):.3f}", "NetworKIN"])
    for k in ("KIN01", "KIN07"):  # sites the table lists that were not measured
        ks.append([k, "P90100", k, "human", "NOTSEEN", "1", "Q99999", "NOTSEEN", "human", "S1", "1", "_", "Inf",
                   "PhosphoSitePlus"])
    head = ["KINASE", "KIN_ACC_ID", "GENE", "KIN_ORGANISM", "SUBSTRATE", "SUB_GENE_ID", "SUB_ACC_ID", "SUB_GENE",
            "SUB_ORGANISM", "SUB_MOD_RSD", "SITE_GRP_ID", "SITE_...7_AA", "networkin_score", "Source"]
    with open(HERE / "ksea_ksdata.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(head) + "\n")
        for r in ks:
            fh.write("\t".join(r) + "\n")
    rows = []
    for acc, gene, rsd, fc in sites:
        rows.append([acc, gene, "PEPTIDE", rsd, f"{rng.random():.4f}", repr(2.0 ** fc)])
        if rng.random() < 0.05:  # the same site under a second protein (an isoform): KSEAapp averages them
            rows.append([acc + "-2", gene, "PEPTIDE", rsd, f"{rng.random():.4f}", repr(2.0 ** (fc + rng.gauss(0, 0.2)))])
    with open(HERE / "ksea_px.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(["Protein", "Gene", "Peptide", "Residue.Both", "p", "FC"]) + "\n")
        for r in rows:
            fh.write("\t".join(r) + "\n")
    print(f"{len(rows)} site rows, {len(ks)} kinase-substrate rows, max |log2FC| "
          f"{max(abs(math.log2(float(r[5]))) for r in rows):.2f}")


if __name__ == "__main__":
    main()
