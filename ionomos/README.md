# Ionomos

Proteomics results to an interactive, self-contained report: filtering,
normalisation, imputation, limma statistics, volcano plots, sample QC,
"only in one condition" proteins and gene-set enrichment, in one
`report.html` that opens in any browser, offline. The statistics are a
Python port of FragPipe-Analyst, checked against the R package.

Pure Python, one dependency (PyYAML), Windows / macOS / Linux, no Tk needed
for the analysis.

```bash
pip install ionomos
ionomos demo --open                              # a simulated experiment and its report
ionomos analyze path/to/report.pg_matrix.tsv --open
```

`ionomos analyze` reads:
- a FragPipe results folder (LFQ `combined_protein.tsv`, DIA `report.pg_matrix.tsv`,
  TMT `abundance_*_MD.tsv`, isoDTB `combined_modified_peptide_label_quant.tsv`)
- DIA-NN (`pg_matrix.tsv`, `report.tsv`, or `report.parquet` with
  `pip install "ionomos[parquet]"`), MaxQuant `proteinGroups.txt`, Sage `lfq.tsv`,
  Spectronaut reports, AlphaDIA `pg.matrix.tsv`, MSstats-format tables, Proteome Discoverer
  protein exports ([what is read from each](https://github.com/nichfoster/ionomos/blob/master/docs/ENGINES.md))
- any protein table (`.csv`, `.tsv`, `.txt`, `.xlsx`: Perseus exports, your own sheet)
- a results table that already holds fold changes and p-values (plotted as given)

Results go to `results/` in a folder, or `<table name>_ionomos/` next to a
table; nothing beside your data is changed.

Ionomos is also a Windows app that watches a drop folder on the instrument PC,
files each experiment and runs FragPipe headlessly before the analysis; that
part is set up from the installer on the
[releases page](https://github.com/nichfoster/ionomos/releases).

- 10-minute quickstart: <https://github.com/nichfoster/ionomos/blob/master/docs/QUICKSTART.md>
- Source, docs and issues: <https://github.com/nichfoster/ionomos>
- Changes: <https://github.com/nichfoster/ionomos/blob/master/CHANGELOG.md>

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest && .venv/bin/ruff check src tests
```

Design docs, testing and deployment:
[docs/](https://github.com/nichfoster/ionomos/tree/master/docs).

## License and citation

GPL-3.0-or-later. The analysis is a translation of
[FragPipeAnalystR](https://github.com/Nesvilab/FragPipeAnalystR) and
[FragPipe-Analyst](https://github.com/MonashProteomics/FragPipe-Analyst)
(GPL-3); please cite Hsiao et al., *J. Proteome Res.* 2024,
doi:10.1021/acs.jproteome.4c00294, and limma (Ritchie et al.,
*Nucleic Acids Res.* 2015). Dose-response curves are a translation of
[CurveCurator](https://github.com/kusterlab/curve_curator) (Apache-2.0,
© 2023 Florian P. Bayer); please cite Bayer et al., *Nat. Commun.* 2023,
doi:10.1038/s41467-023-43696-z.
