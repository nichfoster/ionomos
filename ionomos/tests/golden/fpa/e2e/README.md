End-to-end reference: the real FragPipeAnalystR 1.1.1 (R 4.6.1, limma 3.68.5) on a simulated
DIA-NN protein matrix, run with the `reproduce_in_R.R` script that Ionomos writes to
`results/fragpipe-analyst/`.

How it was made (see tests/test_fpa.py::test_whole_pipeline_matches_fragpipeanalystr):

    simulate.dia_pg_matrix(<dir>/fragpipe/dia-quant-output/report.pg_matrix.tsv,
                           runs=EXP_{DMSO,DrugA,DrugB}_{1,2,3}.raw, seed=33, n_proteins=400)
    downstream.analyze(<dir>, "DIA", {"enrichment": False}, record=<manifest>)
    cd <dir>/results/fragpipe-analyst && Rscript reproduce_in_R.R    # FragPipeAnalystR installed

Steps in both: 50 % in one condition filter, median normalisation (MD_normalization),
Perseus-type imputation (impute(fun = "man")), test_limma(type = "control", control = "DMSO"),
add_rejections(alpha = 0.05, lfc = 1).
