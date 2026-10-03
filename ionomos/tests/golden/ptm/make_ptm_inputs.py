"""Inputs for the MSstatsPTM golden (D70): an isoDTB site matrix and a proteome's comparison in MSstats format.

    cd ionomos && .venv/bin/python tests/golden/ptm/make_ptm_inputs.py

Writes ptm_sites.tsv (sites x 3 replicates of log2 heavy / light, as quant.from_isodtb_sites loads FragPipe's label
quant through the port of the lab's site script) and ptm_protein.tsv (the proteome's "Cmpd vs DMSO" from an Ionomos
DIA analysis, as MSstats groupComparison writes it: Protein, Label, log2FC, SE, Tvalue, DF, pvalue, adj.pvalue).
Some sites' proteins are left out of the proteome, and two proteins get DF = Inf, so both branches are checked.
run_msstatsptm.R then computes the reference; tests/test_protein_correction.py compares Ionomos with it."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ionomos import downstream
from ionomos.downstream import isodtb, quant, simulate
from ionomos.downstream.tables import fmt, num, read_tsv, write_tsv

HERE = Path(__file__).resolve().parent


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        lq = td / "iso" / isodtb.LABEL_FILE
        simulate.isodtb_ratios(lq, {"Cmpd": 3}, seed=70, n_sites=240, changed_fraction=0.1, effect=2.0)
        m = quant.from_isodtb_sites(isodtb.write_site_tables(lq, td / "iso")[0])
        write_tsv(HERE / "ptm_sites.tsv", ["id", *m.samples],
                  [[f.id, *row] for f, row in zip(m.features, m.values, strict=True)])
        gene_acc = {}
        for f in m.features:  # 'sp|P10000|ACTB_HUMAN|C12' -> gene ACTB, accession P10000
            acc = f.id.split("|")[1]
            gene_acc.setdefault(f.label.rsplit(" ", 1)[0], acc)
        runs = [(f"DMSO_{r}.raw", "DMSO") for r in (1, 2, 3)] + [(f"Cmpd_{r}.raw", "Cmpd") for r in (1, 2, 3)]
        prot = td / "prot"
        simulate.dia_pg_matrix(prot / "report.pg_matrix.tsv", runs, seed=71, n_proteins=len(gene_acc))
        out = downstream.analyze(prot, method="DIA", analysis_cfg={"enrichment": False, "psm_qc": False})
        _h, rows = read_tsv(prot / out.summary["comparisons"][0]["table"])
        lines = []
        for k, r in enumerate(sorted(rows, key=lambda r: r["label"])):
            acc = gene_acc.get(r["label"])
            if acc is None or k % 9 == 4 or num(r["se"]) is None:  # every 9th protein left out: "not found"
                continue
            df = "Inf" if k in (2, 11) else r["df"]
            lines.append([acc, "Cmpd-DMSO", r["log2fc"], r["se"], r["t"], df, r["pvalue"], r["qvalue"]])
        write_tsv(HERE / "ptm_protein.tsv", ["Protein", "Label", "log2FC", "SE", "Tvalue", "DF", "pvalue",
                                             "adj.pvalue"], lines)
    print("wrote", HERE / "ptm_sites.tsv", HERE / "ptm_protein.tsv", fmt(len(lines)), "proteins")


if __name__ == "__main__":
    main()
