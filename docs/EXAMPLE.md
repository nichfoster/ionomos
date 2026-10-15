# The real-data example: `ionomos example`

`ionomos demo` shows a report on simulated numbers, offline. `ionomos example`
shows Ionomos on real, published data. It downloads the example datasets
of FragPipe-Analyst's own paper (20.9 MB), checks them, writes three
experiment folders and analyses each one, in about a minute. Nothing is
bundled with Ionomos: the data are fetched from Zenodo and keep their
licence (D95).

```bash
ionomos example --open          # ./ionomos_example/: three experiments, three reports
ionomos example --list          # what the data are, where they come from, their licence
ionomos example --dataset dia   # only one: apms | dia | tmt (repeatable)
```

## The data

One zip on Zenodo, published with the FragPipe-Analyst paper:

| | |
|---|---|
| Record | <https://zenodo.org/records/13147961> (doi:[10.5281/zenodo.13147961](https://doi.org/10.5281/zenodo.13147961)), "FragPipe-Analyst Manuscript Datasets", Yi Hsiao, 2024 |
| File | `FragPipe_Analyst_Manuscript_Datasets.zip`, 20,856,553 bytes, MD5 `a4927977e8ab5b759c7cb23190b45d8f` (Zenodo's), SHA-256 `1a9d962e4e29b124a6ca26ce08d086183b975aca72479a55ece9fe0979eafa7d` |
| Licence | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): reuse with attribution, changes stated |
| Paper | Hsiao Y. et al. Analysis and Visualization of Quantitative Proteomics Data Using FragPipe-Analyst. *J. Proteome Res.* 2024. doi:[10.1021/acs.jproteome.4c00294](https://doi.org/10.1021/acs.jproteome.4c00294) |

These are the inputs of FragPipe-Analyst's own tutorials and of
FragPipeAnalystR's vignettes. Ionomos uses three of the zip's folders:

| `--dataset` | Experiment | Samples | Table Ionomos reads |
|---|---|---|---|
| `apms` | Affinity purification of cyclin D1 (CCND1) against control pull-downs, from a head and neck cancer interactome study (Swantek et al., *Science* 2021, doi:10.1126/science.abf2911). FragPipe-Analyst's LFQ AP-MS tutorial | CCND1 3, CONTROL 4 | FragPipe label-free, `combined_protein.tsv` (MaxLFQ intensities) |
| `dia` | Clear cell renal cell carcinoma (ccRCC) tumours against normal adjacent tissue (NAT), from the CPTAC study (Clark et al., *Cell* 2019, doi:10.1016/j.cell.2019.10.007), searched again with FragPipe (DIA-NN). FragPipe-Analyst's DIA tutorial | Tumor 20, NAT 12 (33 runs, 1 left out) | DIA-NN, `diann-output.pg_matrix.tsv` |
| `tmt` | The same tumours and normal tissue in four TMT-11 plexes with a pooled reference channel each (TMT-Integrator). FragPipe-Analyst's TMT tutorial | Tumor 20, NAT 12 (36 channels, 4 left out) | TMT-Integrator, `ratio_protein_MD.tsv` (log2 ratios to the pool) |

The zip also holds a TMT phosphosite table and a LiP-MS table; the example
does not use them.

## Running it

```bash
ionomos example [FOLDER] [--dataset apms|dia|tmt] [--zip PATH] [--no-enrichment] [--open] [--quiet]
```

1. **The folder.** `./ionomos_example/`, or `ionomos_example_2`, `_3`, ... when
   that name is taken. A folder you name must be new or empty: the example
   never writes into a folder that holds anything.
2. **The download.** One HTTPS request to zenodo.org, with a progress line.
   The file goes to a temporary `.part` file in `ionomos_example/download/` and
   gets its name only when its size, MD5 and SHA-256 match the values above.
   A download that fails or does not match is removed (only that temporary
   file), and the message says why. The zip is kept, so a second
   `ionomos example` finds it and needs no network.
3. **No network, or a firewall?** The message names the URL. Download the zip
   in a browser, then run `ionomos example --zip <where you saved it>`, or
   put it in the folder you run `ionomos example` from. A zip you give is
   checked the same way and never moved or changed. With
   `IONOMOS_OFFLINE=1` the example makes no network call at all.
4. **The folders.** For each experiment, the table and the authors'
   `experiment_annotation.tsv` (their sample sheet for FragPipe-Analyst) are
   copied out of the zip byte for byte. Ionomos then writes two files of its
   own: an `experiment.yaml` and a `README.txt` (below).
5. **The analysis.** It is the same code as `ionomos analyze <folder>`, with no
   lab config. Enrichment downloads the Enrichr libraries once (Hallmark, GO
   Biological Process, Reactome). The test runs on your computer; nothing is
   sent. Use `--no-enrichment` to skip it.

Time: under a minute. On 2026-10-15 (Ionomos 0.18.1, a Windows laptop on a
fast connection) the whole run took 47 seconds, including the download and
the first fetch of the Enrichr libraries. The three analyses alone take
about 15 seconds.

```
ionomos_example/
  README.txt                 the source, the licence and citation, what changed, the three experiments
  download/FragPipe_Analyst_Manuscript_Datasets.zip
  apms/  combined_protein.tsv  experiment_annotation.tsv  experiment.yaml  README.txt  results/report.html
  dia/   diann-output.pg_matrix.tsv  experiment_annotation.tsv  experiment.yaml  README.txt  results/report.html
  tmt/   ratio_protein_MD.tsv  experiment_annotation.tsv  experiment.yaml  README.txt  results/report.html
```

**The design, in `experiment.yaml`.** The AP-MS sample names say their
conditions (`CCND1_3`, `CONTROL_1`), so its file only names the control and
the comparison. The DIA and TMT names do not (`..._C3L-00079_T.mzML`,
`CPT0088900003`). For those, `experiment.yaml` gives each sample its
condition, taken from the authors' `experiment_annotation.tsv`
(`sample_conditions`). The samples the authors' annotation leaves out are
left out here too (`exclude_samples`). That is what FragPipe-Analyst does
with the same annotation:

- DIA: tumour C3N-00832 is not in the authors' annotation. It is left out of
  the TMT data too. The authors do not say why. Ionomos' scorecard does not
  flag it when it is put back in: delete the name from `exclude_samples` to
  see.
- TMT: the channels QC6, QC7 and QC8 (a QC standard) and the same tumour
  (CPT0078800003). The pooled reference channels are not samples of the
  ratio table, so they never appear.

Edit `experiment.yaml` and re-run `ionomos analyze ionomos_example/dia` to
try other settings.

## A walk through the reports

Open `results/report.html` in each folder. The numbers below are from
Ionomos 0.18.1 on 2026-10-15. Another version may differ a little; the
biology should not.

### `apms`: who binds cyclin D1

- **Key findings, then the volcano plot "CCND1 vs CONTROL".** 10 proteins up
  and none down, of 1,360 tested. The hits are the bait and its known
  complex: CCND1 itself; CDK4, CDK6, CDK2 and CDK5, the kinases cyclin D1
  binds; and the CDK inhibitors CDKN1A (p21), CDKN1B (p27) and CDKN1C (p57),
  which sit in the same complexes. Also up: PCNA (bound by p21) and PPIF.
  Search `CDK*` in the volcano's search box to see them together.
- **Only in one condition.** 28 proteins are measured in the CCND1
  pull-downs and in no control. The bait and its partners are among them. In
  a pull-down this is the expected shape of a real interactor: absent from
  the control, not merely lower.
- **The warning "9 of 10 hits rest on imputed values".** It is true, and it
  is the reason for the previous point. The controls have no values for
  these proteins, so their control level is imputed. Their fold change
  (about 10 log2 for CCND1) depends on the imputed value. That a protein is
  present in the bait and absent from the control does not. Tick **hide
  imputation-driven** in the volcano to see the hits that stand on measured
  values.
- **Quality control.** No sample is flagged. The p-value histogram is
  "flat" with about 8 % of proteins changed: few proteins differ, as
  expected in a pull-down against its control.
- **Enrichment.** The hit list is short, so over-representation finds small
  cell-cycle sets (GO "G1/S transition of mitotic cell cycle": 8 of its 14
  measured members are hits).

### `dia`: kidney tumour against normal kidney

- **Key findings, volcano "Tumor vs NAT".** 603 up and 1,310 down of 7,043
  tested. The p-value histogram says about 65 % of proteins change. Cancer
  against normal tissue is not a subtle comparison. Search the volcano for
  the markers in FragPipeAnalystR's vignettes:
  - CA9 (carbonic anhydrase 9) is about 6.9 log2 up and is not measured in
    any normal sample. VHL loss, the defining event of ccRCC, switches on the
    hypoxia response, and CA9 is one of its targets.
  - AHNAK2 is up.
  - NDUFV2 (respiratory chain complex I), PIGR, and SFRP1 are down. The DIA
    tutorial singles out SFRP1 as a kidney cancer suppressor.
- **Enrichment.** Oxidative phosphorylation is the strongest set in the
  down list (148 of its 185 measured members; adjusted p about 1e-74), then
  fatty-acid metabolism and adipogenesis. The up list holds interferon gamma
  response, hypoxia and epithelial-mesenchymal transition. ccRCC is known
  for exactly this: a switch from respiration to glycolysis, and immune
  infiltration. The rank-based test puts interferon gamma, oxidative
  phosphorylation and hypoxia near the top of its list too, but calls none
  of them significant after adjustment. When two thirds of all proteins
  change, "this set moves more than the rest" is a hard bar; the hit-list
  test asks a different question.
- **Quality control.** The scorecard warns about one tumour,
  C3N-01220 T, whose values "scatter widely around its group". Tumours
  differ from each other more than normal tissue does: the median CV is
  45 % in tumours and 30 % in NAT. Missing values are imputed (Perseus-type,
  the default for label-free and DIA data, D24). The missing-value check
  finds that they follow abundance, which is what that imputation assumes.
  Run order is not checked, because the zip has no raw files.

### `tmt`: the same tumours, by TMT

- **Volcano "Tumor vs NAT".** 330 up and 821 down of 9,963 tested; about 73 %
  changed. CA9, AHNAK2, NDUFV2, PIGR and SFRP1 move as in DIA, but the fold
  changes are smaller (CA9 about 1.9 log2 here, 6.9 in DIA). TMT ratios are
  compressed by co-isolated peptides. The direction agrees; compare the
  `results/Tumor_vs_NAT_differential.tsv` of both folders to see it for
  every protein.
- **Enrichment.** Oxidative phosphorylation and fatty-acid metabolism down;
  allograft rejection, hypoxia and EMT up: the same story as DIA, from
  another measurement.
- **Notes.** Ionomos reads `ratio_protein_MD.tsv`, TMT-Integrator's log2
  ratios to each plex's pool, as FragPipe-Analyst's tutorial does. The
  plexes are already on one scale, so no IRS is applied. No values are
  imputed: TMT data are not, in Ionomos or in FragPipe-Analyst (D24). The
  missing-value check calls the pattern random here, which suits that. The
  names are CPTAC aliquot IDs, not `condition_1_channel`; `experiment.yaml`'s
  `sample_conditions` gives every sample its condition, so no note about the
  names appears (one does only when a condition had to be read from a name,
  #158).
- **Replicate agreement** (under "How far to trust this") is a median r of
  0.51. That is low only in appearance. Ratios to a pool remove what every
  sample shares, so what is left is the biological difference between
  patients.

## What Ionomos does differently from FragPipe-Analyst, and why

The statistics are FragPipe-Analyst's, ported (filter, normalise, impute,
limma; [VALIDATION.md](VALIDATION.md)). The defaults and what the report
adds differ:

| | FragPipe-Analyst (web app; these tutorials) | Ionomos default | Why |
|---|---|---|---|
| Missing-value filter | none | measured in ≥ 50 % of at least one condition | imputation turns proteins seen once into large fold changes (D24) |
| Normalisation | none | `auto`: median centring, or feature ratios when median centring would shift the conditions | sample loads differ; a no-op on data that is already normalised (D24, D64) |
| Imputation | DIA tutorial: Perseus-type | Perseus-type for label-free and DIA, none for TMT | makes on/off proteins testable; TMT as FragPipe-Analyst (D24) |
| Comparisons | all pairs | each condition against the control | two conditions here, so the same (D24) |
| Cut-offs | adjusted p 0.05, \|log2FC\| 1 (DIA tutorial: p 0.01) | adjusted p 0.05, \|log2FC\| 1 | — |
| Design | upload a table and a sample sheet | a folder; the design in `experiment.yaml`, an SDRF or the names | the folder is the record of what was done (D33, D47) |
| Added | — | on/off proteins, hits resting on imputation flagged, a sample scorecard, the p-value histogram with π0, power, a rank-based gene-set test, "How far to trust this", one offline HTML file | so a reader can see how far to trust the hits (D32, D35, D84) |

To run Ionomos with the DIA tutorial's settings, put these lines under
`analysis:` in `ionomos_example/dia/experiment.yaml` and re-run `ionomos
analyze ionomos_example/dia`:

```yaml
  filter_condition_pct: 0
  normalize: none
  imputation: perseus
  alpha: 0.01
```

The AP-MS control is a control pull-down. Ionomos has a role for that
(`background`, D88): such a control is normalised apart and filtered apart,
and "enriched" reads as up. The example keeps the plain `control`, as
FragPipe-Analyst does. With `roles: {CONTROL: background}` under
`analysis:`, the same 10 proteins are the hits on this dataset.

## Is it the same as FragPipe-Analyst's result?

**Not checked: nothing to check against is published.** Neither the
tutorials, the vignettes nor the Zenodo record includes FragPipe-Analyst's
result tables for these data. The tutorials show plots only, and they name
markers without numbers. So there is no `ionomos example --compare`, and
[VALIDATION.md](VALIDATION.md) makes no claim from this dataset. What we
can say is that the markers the tutorials and vignettes name move the same
way in Ionomos.

To make the comparison yourself:

1. Upload a table and its `experiment_annotation.tsv` from one of the folders
   to <https://fragpipe-analyst.org> (or run FragPipeAnalystR's vignette).
2. Download its results table.
3. Run `ionomos compare ionomos_example/dia <that table> --open`.

[VALIDATION.md](VALIDATION.md), "Check it on your own experiment", explains
the verdict. Set the same filter, normalisation and imputation on both sides
first (above), or the verdict measures the settings, not the code.

## For developers

- `ionomos/src/ionomos/example.py` holds the dataset list, the download and
  checks, the folder writer and the command. It uses the standard library
  only; no Tk.
- `ionomos/tests/golden/example/` holds an excerpt for the tests: 240 real
  rows of each table, unchanged, and the annotation files, with their
  licence and `make_excerpt.py`. It is not in the pip package.
  `tests/test_example.py` zips it, stubs the download and runs the whole
  path offline, including the biology above (CCND1 and CDK4 up; CA9 up and
  NDUFV2 down in DIA and TMT).
- If Zenodo changes the record, the size and checksums in `ARCHIVE` stop
  matching. The example then refuses the download with a sentence instead of
  analysing something else.
