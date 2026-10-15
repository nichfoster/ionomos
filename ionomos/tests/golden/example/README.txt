An excerpt of `ionomos example`'s data, for tests/test_example.py
=================================================================

Not Ionomos' data, and not simulated. These files are a small part of
"FragPipe-Analyst Manuscript Datasets" by Yi Hsiao, Zenodo (2024),
doi:10.5281/zenodo.13147961, https://zenodo.org/records/13147961,
licensed under the Creative Commons Attribution 4.0 International licence
(CC BY 4.0, full text in LICENSE-CC-BY-4.0.txt beside this file,
https://creativecommons.org/licenses/by/4.0/). The datasets accompany:
Hsiao Y. et al., Analysis and Visualization of Quantitative Proteomics Data
Using FragPipe-Analyst, J. Proteome Res. 2024, doi:10.1021/acs.jproteome.4c00294.

Changes made: from FragPipe_Analyst_Manuscript_Datasets.zip (20,856,553
bytes, MD5 a4927977e8ab5b759c7cb23190b45d8f), make_excerpt.py kept the header
and 240 of the rows of each table, unchanged byte for byte (the rows of a
few named proteins and every k-th other row); the experiment_annotation
files are copied whole. Nothing else was changed or added.

  AP-MS/combined_protein.tsv                 240 of 3,063 proteins
  AP-MS/experiment_annotation.tsv            whole
  DIA_4plex/diann-output.pg_matrix.tsv       240 of 7,434 protein groups
  DIA_4plex/experiment_annotation_clean.tsv  whole
  TMT_4plex/ratio_protein_MD.tsv             240 of 10,845 proteins
  TMT_4plex/experiment_annotation_clean.tsv  whole

The tests zip these files under the zip's own folder name and feed them to
`ionomos example` with the download stubbed, so the whole path (checks,
unpacking, experiment.yaml, analysis) runs offline. The full data are never
in this repository or the package; `ionomos example` downloads them
(docs/EXAMPLE.md).
