# Validation: how accurate is the analysis, and how to check it yourself

The downstream analysis (filter, normalise, impute, limma, QC, report) is a
Python port of FragPipe-Analyst and limma. This page says what it has been
checked against, how a lab member checks it on their own experiment
(`ionomos compare`), how its accuracy is measured against known truth
(`ionomos benchmark`), what it does with messy tables, and what the "How far
to trust this" list in every report is made of (D60).

**Not verified, anywhere on this page: real lab data.** Every number below
comes from simulated tables or from reference runs of R packages on simulated
tables. No mixed-species sample has been run on the lab's instrument yet, and
no real FragPipe-Analyst, MSstats or Perseus export has been compared.

## What the numbers are checked against

| Part | Checked against | How close | Test |
|---|---|---|---|
| isoDTB site table, TMT annotation | the lab's R scripts | byte-identical | `tests/test_downstream.py`, `tests/golden/` |
| Imputation (Perseus-type), R's random numbers | R's `set.seed(123)`, `rnorm`; FragPipeAnalystR's `manual_impute` | 1e-13; 1e-9 | `tests/test_fpa.py` |
| limma (`~0 + condition`, all / control / others, missing values) | limma 3.68 | 1e-8 | `tests/test_fpa.py`, `tests/golden/fpa/` |
| Unequal groups (DMSO 2, Probe 4, Probe_Comp 4) through filter, median normalisation, imputation or none, the role comparisons and `small_group_min_valid` (`half` and `same`) | limma 3.68.5 | 1e-8 | `tests/test_roles.py`, `tests/golden/unequal/` |
| The whole pipeline on a DIA-NN matrix | FragPipeAnalystR 1.1.1 | 1e-8 on every result column | `tests/test_fpa.py`, `tests/golden/fpa/e2e/` |
| Blocks, covariates, the moderated F, DEqMS | limma 3.68.5, DEqMS 1.30.0 | 1e-8 | `tests/test_design.py` |
| Time courses | limma 3.68.5 | 1e-8 | `tests/test_timecourse.py` |
| Spline time courses (D77): the natural spline basis and its `predict()` (8 time vectors, df 1-6, knots shoved off a boundary, tied knots); the F on the spline coefficients, the fitted change at each time point and the interaction F from limma's own `~Group * ns(time)` (plain, replicate block, missing values with df 3; a condition outside the series) | R 4.6.1 `splines::ns`, limma 3.68.5 | 1e-12 (basis); 1e-8 (worst 5.4e-10 on 10,350 values) | `tests/test_timecourse.py`, `tests/golden/timecourse/` |
| Dose-response curves | CurveCurator 0.6.0 | classes, pEC50, F, p | `tests/test_dose_response.py` |
| TMT summaries | MSstatsTMT 2.20 | 1e-9 | `tests/test_plexes.py` |
| MaxLFQ roll-up (`analysis.rollup: maxlfq`, D76) on 54 proteins × 8 samples: disconnected sample groups, missing values, one feature, one sample, a chain of samples | `iq::maxLFQ()` 2.0.1 (both its scaling and the summed-intensity scaling, and its components); `diann::diann_maxlfq()` 1.0.1 (profiles of connected proteins) | 1e-9; 1e-3 (DIA-NN regularises) | `tests/test_rollup.py`, `tests/golden/maxlfq/` |
| IRS on the plex means (3 plexes, no reference channel), filter, median normalisation, limma with each protein's residual df reduced by its plexes - 1 | base R + limma 3.68.5 | 1e-8 | `tests/test_tmt_plex_stats.py`, `tests/golden/tmt_sum/` |
| isoDTB sites corrected for protein abundance: the sites' moderated one-sample SE and df, then the adjustment (log2FC, SE, Satterthwaite df, t, p, BH), a protein table in MSstats format, two proteins with DF = Inf (D70) | limma 3.68.5 + MSstatsPTM 2.14.0 (`.applyPtmAdjustment`) | 1e-9 on 213 sites | `tests/test_protein_correction.py`, `tests/golden/ptm/` |
| t-tests, Benjamini-Hochberg | scipy | 1e-9 (p), 1e-12 (BH) | `tests/test_downstream.py` |

These say the port computes what the reference computes on the same input.
They do not say the method suits your data. The two commands below are for
that.

## Check it on your own experiment: `ionomos compare`

```
ionomos compare <experiment folder> <reference> [--open]
```

The first argument is an analysed experiment (a folder with
`results/analysis.json`). The reference is another result for the same
experiment:

| Reference | What to give |
|---|---|
| another Ionomos analysis (other settings, an older version) | its folder |
| FragPipe-Analyst | the downloaded results table (`..._log2 fold change`, `..._p.val`, `..._p.adj`, `..._significant` per comparison) |
| limma | a `topTable` written to a file (`logFC`, `P.Value`, `adj.P.Val`) |
| MSstats | `groupComparison`'s result (long format: `Protein`, `Label`, `log2FC`, `pvalue`, `adj.pvalue`) |
| Perseus | the matrix with `Difference`, `-Log p-value`, `q-value`, `Significant` columns |
| the lab's old R output, a hand-made sheet | any table with a fold-change column and a p-value column per comparison (`.tsv`, `.csv`, `.txt`, `.xlsx`) |

Tables are read by `downstream/anytable.py`, which already recognises
fold-change and p columns (D33); long format is read by `compare.py`.

What it reports, per pair of comparisons:

- **Matching.** Features are matched by protein ID (the UniProt accession;
  any member of a protein group) or by gene, whichever matches more
  (`--by id|gene` decides). Counted: matched, only in Ionomos, only in the
  reference.
- **Fold changes.** Pearson and Spearman correlation of log2FC; the slope
  (major axis, so neither side is treated as error-free); the offset, which
  is the median of Ionomos minus reference. A normalisation difference shows
  as an offset with a slope of 1.
- **Hit calls.** Both / only Ionomos / only reference / opposite direction,
  at each side's own cut-offs (a reference's `significant` column when it
  has one, else `--ref-alpha` / `--ref-log2fc`, else Ionomos' cut-offs, and
  the page says which) and at common cut-offs (`--alpha`, `--log2fc`,
  `--raw-p`; default: the analysis' own).
- **p-values.** Spearman of -log10 p, the median log10 ratio (negative:
  Ionomos' p-values are smaller), the share within a factor of 10.
- **Largest disagreements.** The 15 features whose fold changes differ most
  once the offset is taken out.
- **A verdict**, from these thresholds:

| Verdict | When |
|---|---|
| `agrees` | Pearson r ≥ 0.95, slope between 0.9 and 1.1, \|offset\| ≤ 0.10 log2, and, when the two hit lists together hold at least 10 features, at least 70 % of them shared at the common cut-offs |
| `agrees after an offset of …` | the same with a larger offset; the hit lists are then not judged, because the fold-change cut-off falls on different features |
| `differs: …` | any of the above fails; the line says which. r ≤ -0.5 is reported as "the other way round" (`--flip`) |
| `not judged: …` | fewer than 20 features, or less than half of the smaller side, matched |

The thresholds are Ionomos' own choice, not a standard. They are constants at
the top of `compare.py` and are printed on the page. Read the numbers, not
only the verdict: two tools that impute differently disagree on exactly the
imputed features, and that is "differs" without either being wrong.

Output, in the experiment's `results/` (or `--out DIR`): `compare.html`
(two scatter plots per comparison, the tables, the thresholds), `compare.tsv`
(every matched and unmatched feature) and `compare.json`. Neither result is
changed. Exit code 0: every comparison agrees; 1: one differs or was not
judged; 2: nothing could be compared. The next `ionomos analyze` of the folder
shows the verdict under "How far to trust this", with a note if the analysis
settings have changed since.

Tested on references made from the Ionomos result itself (shifted, scaled,
renamed, rewritten in each layout), so the right verdict is known. **Not
tested on a real export of any of these tools**: the column layouts are the
documented ones.

## Accuracy against known truth: `ionomos benchmark`

### Simulated data

```
ionomos benchmark                  # the standard grid, about a minute
ionomos benchmark --grid quick     # seconds
ionomos benchmark --like <experiment folder>   # adds that experiment's settings and group sizes
ionomos benchmark --kind isodtb    # isoDTB site ratios (below)
ionomos benchmark --kind tmt       # several TMT plexes with a pooled reference (below)
ionomos benchmark --kind rollup    # peptide tables rolled up by median polish or MaxLFQ (below, D76)
```

Without `--kind` the data is label-free DIA, as described first. With
`--like`, the kind is the experiment's own (site ratios: isodtb; several TMT
plexes: tmt). Each kind writes its own files (`benchmark_simulated.*`,
`benchmark_simulated_isodtb.*`, `benchmark_simulated_tmt.*`), so the three
can sit side by side.

#### Label-free DIA

It runs the pipeline's own loader, processing and statistics
(`benchmark.run_pipeline`, the calls `analyze()` makes; a test checks the two
give the same hits) on tables from `downstream/simulate.py` with planted
changes:

- 1,000 proteins, 10 % of them changed, half up and half down;
- replicate SD 0.3 log2 on average, different for each protein (log-normal);
  a loading difference per run; missing values more likely at low abundance;
- **designs**: 2 vs 2, 3 vs 3, 4 vs 4, 6 vs 6 and 2 controls vs 4 treated;
  **effects**: 1.5-, 2- and 4-fold; **missing values**: none, typical (about
  3 % of the values), heavy (about 9 %); 5 tables per scenario, pooled;
- **settings**, each on the same tables: every imputation with median
  normalisation, Perseus with `gn`, and Perseus / none without normalisation.

Measured per scenario and setting: **sensitivity** (planted changes called
in the right direction, of all planted), the **observed false discovery
proportion** (FDP: false calls among the calls) and the **fold-change bias**
(estimated minus planted, negative = underestimated). Both with the analysis'
cut-offs (adjusted p ≤ 0.05 and |log2FC| ≥ 1) and with alpha alone, which is
the case Benjamini-Hochberg makes its promise about. Output:
`benchmark_simulated.html` / `.tsv` / `.json` in `./ionomos_benchmark` (or the
experiment's `results/` with `--like`).

The standard grid, measured 2026-10-01 (45 scenarios per setting; the range
is over the scenarios with at least 50 calls):

| Setting | FDP at adj. p ≤ 0.05 | range | FDP with \|log2FC\| ≥ 1 too | Found at adj. p ≤ 0.05: 1.5- / 2- / 4-fold | log2FC bias |
|---|---|---|---|---|---|
| perseus + median (**default**) | 4.0 % | 0.6 – 6.2 % | 2.3 % | 6 / 30 / 71 % | -0.06 |
| none + median | 4.6 % | 2.8 – 6.2 % | 2.6 % | 10 / 47 / 86 % | -0.01 |
| knn + median | 5.1 % | 3.0 – 9.5 % | 3.0 % | 10 / 45 / 86 % | -0.09 |
| minprob + median | 4.2 % | 0.3 – 6.2 % | 2.4 % | 6 / 30 / 71 % | -0.05 |
| mindet + median | 4.3 % | 0.3 – 6.2 % | 2.6 % | 6 / 30 / 71 % | -0.05 |
| min + median | 5.3 % | 0.0 – 11.5 % | 3.8 % | 6 / 29 / 67 % | -0.01 |
| zero + median | 8.0 % | 0.0 – 92 % | 7.0 % | 6 / 29 / 61 % | +0.41 |
| perseus + gn | 5.2 % | 0.7 – 14.8 % | 2.4 % | 6 / 30 / 70 % | -0.07 |
| perseus, not normalised | 13.8 % | 0.0 – 68 % | 12.0 % | 1 / 10 / 54 % | -0.06 |
| none, not normalised | 21.4 % | 0.9 – 81 % | 17.0 % | 1 / 18 / 77 % | -0.01 |

(With 10 % of the proteins changed, Benjamini-Hochberg at 5 % aims at 4.5 %.)

What this says about the settings, on data like this simulation:

- **The default is calibrated and conservative.** Its observed FDP stays at
  or below the nominal 5 % overall. Perseus-type imputation costs
  sensitivity: 30 % of 2-fold changes found against 47 % without imputation,
  71 % against 86 % for 4-fold. Imputed low values widen the spread of the
  groups they fill.
- **No imputation finds more and stays near nominal** (4.6 % overall, up to
  6.2 % in single scenarios), at the price of leaving untested the features
  that lack two values in a group.
- **`zero` and `min` imputation bias the fold changes** (+0.41 log2 for
  `zero`) and let the FDP run away in scenarios with many missing values.
- **Skipping normalisation is the costliest choice** when runs differ in
  loading: FDP 14 – 21 % overall.
- **Two replicates find little**: at 2 vs 2 with median normalisation, 0 to
  1 % of the 2-fold changes are called, whatever the imputation; 3 vs 3 with
  typical missingness finds 14 % (default) to 47 % (no imputation) of them.

This is a simulation. It shows how the method behaves on data shaped like
the simulation: log-normal noise, missingness that depends only on
abundance, changes of one size, no outlier samples. It does not show how the
lab's samples behave.

**The calibration guard** (`tests/test_benchmark.py`) runs a small grid in
every test run (3 vs 3, 4 vs 4, 2 vs 4; 4-fold changes; Perseus and no
imputation; 10 tables of 600 proteins each, about 1 s). It fails when the
pooled FDP at adjusted p ≤ 0.05 of any row exceeds **8.5 %**, when the
sensitivity drops below 0.68 (Perseus) or 0.80 (none), or when the bias
exceeds 0.15 log2. Where the limits come from: over 30 other blocks of 10
seeds, the pooled FDP per design had a mean of 4.9 – 5.3 % (none) and
2.9 – 3.3 % (Perseus), an SD of 0.7 – 1.1 %, and ranged 1.6 – 7.9 %; the
limit is the worst mean plus about 3.5 SD. With its fixed seeds the guard
measures 1.7 – 6.3 %.

#### isoDTB site ratios (`--kind isodtb`, D66)

The table is FragPipe's `combined_modified_peptide_label_quant.tsv`
(`simulate.isodtb_ratios`), merged into sites by the port of the lab's R
script and loaded as `analyze()` loads it; each compound is tested against 0.

- 900 cysteines, three per protein; every site its own replicate SD (0.35
  log2 on average, log-normal); a quarter of the sites seen by two peptides
  (the site table averages them); weak peptides go missing more often;
- **grid**: 2, 3 and 4 replicates; 2- and 4-fold changes; 5 % or 20 % of the
  sites changed, **all one way** (a compound engages its sites; 20 % is a
  promiscuous one); **mixing error**: none, or an SD of 0.2 log2 per replicate
  (heavy and light mixed about 15 % off 1:1, which moves every ratio of that
  replicate); 5 tables per scenario;
- **settings**: limma (the default) and the t-test; since D70 also limma with
  the ratios centred (`ratio_centre: median` and `auto`, below). Imputation
  and intensity normalisation do not apply to ratio data. The table below is
  from 2026-10-02, before the centring settings.

Measured 2026-10-02 (FDP at adjusted p ≤ 0.05, pooled; found = planted
changes called at adjusted p ≤ 0.05; |offset| = how far the unchanged sites'
mean sits from 0, per table):

| | FDP | range over scenarios | FDP with \|log2FC\| ≥ 1 | found 2- / 4-fold | \|offset\| |
|---|---|---|---|---|---|
| limma, no mixing error, 3 – 4 replicates | 4.9 % | 3.7 – 7.0 % | 0.9 – 2.2 % | 72 – 86 / 97 % | 0.005 |
| limma, no mixing error, 2 replicates | 5.8 % | 4.9 – 8.0 % | 4.2 % | 17 / 89 % | 0.006 |
| limma, mixing error, 3 – 4 replicates | 4.2 – 5.0 % | 1.7 – 10.3 % | 1.4 – 2.1 % | 67 – 80 / 97 – 99 % | 0.07 – 0.11 |
| limma, mixing error, 2 replicates | 9.8 % | 8.6 – 10.6 % | 5.7 % | 14 / 89 % | 0.13 |
| t-test, 4 replicates | 2.1 – 4.3 % | 1.1 – 7.1 % | 0 – 0.1 % | 16 – 34 / 65 – 79 % | as limma |

(With 5 – 20 % of the sites changed, Benjamini-Hochberg at 5 % aims at 4 – 4.75 %.)

What this says, on data like this simulation:

- **limma is calibrated with 3 or more replicates**, and finds almost every
  4-fold change. The t-test is calibrated too but finds little: with 3
  replicates it has 2 df per site, and 1 – 32 % of the planted changes are
  found; with 2 replicates it finds nothing.
- **Two replicates lean on the variance prior.** limma's prior assumes
  variances of one shape; with every site its own SD and 1 df each, the FDP
  is 5 – 8 % without any mixing error. With equal SDs it is 3.7 – 4.7 % (20
  seeds), so the prior's fit, not the code, is the cause.
- **A mixing error is not corrected by default.** Every ratio of a replicate
  moves by the error, so the unchanged sites sit 0.07 – 0.13 log2 off 0 (mean
  over the tables; the error is random, so it averages out over many
  experiments, not within one). The test against 0 then calls more of them:
  up to 10.6 % per scenario. Since D70 the lab can centre the ratios
  (`ratio_centre`, below); the default stays `none` until the lab decides.

**Centring the ratios** (`ionomos benchmark --kind isodtb`, grid `centring`
in `benchmark.ISODTB_GRIDS`, D70): 3 replicates, 900 sites, 2- or 4-fold, 5 %
or 20 % of the sites up, mixing error SD 0 or 0.2 log2, 20 tables per
scenario, limma. FDP and found at adjusted p ≤ 0.05; |offset| per table:

| mixing error, sites up | none (default): FDP / found / \|offset\| | median | auto |
|---|---|---|---|
| none, 5 %, 2-fold | 3.4 % / 38 % / 0.005 | 3.4 % / 31 % / 0.021 | as none (not centred) |
| none, 5 %, 4-fold | 4.1 % / 94 % / 0.007 | 4.1 % / 94 % / 0.018 | as none |
| none, 20 %, 2-fold | 4.6 % / 79 % / 0.006 | 5.3 % / 74 % / 0.087 | as none |
| none, 20 %, 4-fold | 4.9 % / 97 % / 0.007 | 6.1 % / 97 % / 0.097 | as none |
| SD 0.2, 5 %, 2-fold | **8.4 %** / 18 % / 0.109 | 4.1 % / 24 % / 0.016 | 4.2 % / 28 % / 0.014 |
| SD 0.2, 5 %, 4-fold | 4.8 % / 95 % / 0.113 | 4.9 % / 93 % / 0.017 | 4.9 % / 93 % / 0.010 |
| SD 0.2, 20 %, 2-fold | 5.1 % / 61 % / 0.088 | 6.3 % / 74 % / 0.091 | 5.5 % / 80 % / 0.012 |
| SD 0.2, 20 %, 4-fold | 3.8 % / 98 % / 0.080 | 5.2 % / 97 % / 0.093 | 4.4 % / 97 % / 0.012 |

(BH at 5 % aims at 4.75 % with 5 % of the sites changed, 4 % with 20 %.)

- **auto** never centred a table without a mixing error (0 of 240 tables of
  900 sites, 2- or 4-fold, 5 % or 20 % up) and centred every one with it
  (240 of 240). Its offsets on the stable sites had a bias of 0.003 log2 and
  an SD of 0.017 with 20 % of the sites up, against a reported standard error
  of 0.020 – 0.027, so the check is on the safe side.
- **median** removes the mixing error as well, but with 20 % of the sites up
  it moves every unchanged site by -0.09 log2 whether or not there is a
  mixing error, and calls more false sites (5.3 – 6.3 %).
- The FDP with a mixing error is not always high: the error also widens the
  replicate spread, which makes the test conservative; what it always does
  is move the unchanged sites (|offset| 0.08 – 0.11).

`ionomos benchmark --kind isodtb` (the standard grid) now runs the two
centring settings beside limma and the t-test, so the lab can see them on
2 – 4 replicates; the table above is the `centring` grid
(`benchmark.simulated("centring", kind="isodtb")`), with more seeds.

**The guard** (`tests/test_benchmark.py`): limma, 3 and 4 replicates, 10 % of
600 sites up 4-fold, no mixing error, 10 tables each. It fails when the
pooled FDP at adjusted p ≤ 0.05 exceeds **9 %**, the sensitivity drops below
0.90, or the bias exceeds 0.06 log2. Over 30 other blocks of 10 seeds the FDP
had a mean of 4.9 – 5.1 %, an SD of 1.0 % and a range of 3.3 – 7.5 %; with its
fixed seeds the guard measures 3.5 – 3.8 %. A second test shows the mixing
error's offset (and that nothing removes it).

#### TMT across plexes (`--kind tmt`, D66)

The table is MaxQuant's `proteinGroups.txt` of several TMT 10-plexes
(`simulate.tmt_plexes`; MaxQuant because its reporter intensities are raw, so
Ionomos' own IRS runs: TMT-Integrator's abundances are already ratios to the
reference and are not scaled again). The channels' conditions come in as
`sample_conditions`, as an SDRF or the Analysis tab gives them.

- 1,000 proteins; per plex a pooled reference at 126 and in every channel the
  samples leave free (131 for 4 + 4), a **plex effect per protein** (SD 1.0
  log2: each plex picks its own peptides), a loading difference per channel,
  replicate SD 0.25 log2 (log-normal per protein); low-abundance proteins go
  missing from whole plexes, single channels rarely;
- **grid**: 2 and 3 plexes; 4 DMSO + 4 Drug or 2 + 6 per plex; 2- and 4-fold;
  10 % of the proteins changed both ways, or 20 % up (a pulldown); 5 tables
  per scenario;
- **settings**: IRS on the pool channels (`tmt_reference`) with `auto`
  normalisation (the default) or `median`; IRS on each plex's own mean
  (`irs: sum`); no IRS; no IRS with the plex as a block (`block_from`). TMT is
  not imputed, so `min_valid` and the small-group rule apply.

Measured 2026-10-03, after D71 (the D66 numbers of 2026-10-02, where they
differ, in brackets):

| Setting | FDP, changes both ways | FDP, pulldown | range | FDP with \|log2FC\| ≥ 1 (worst scenario) | found 2- / 4-fold | offset of unchanged, pulldown |
|---|---|---|---|---|---|---|
| IRS on the pool + auto (**default**) | 4.4 % | 3.8 % | 2.9 – 6.6 % | 0.2 % (1.0 %) | 95 / 99 % | -0.04 – 0.00 |
| IRS on the pool + median | 4.4 % | 67 % | 2.9 – 77 % | 1.0 % (8.4 %) | 94 / 99 % | -0.43 – -0.20 |
| IRS on plex means + auto | 5.1 % (6.6 %) | 4.0 % (5.3 %) | 3.0 – 7.7 % (3.7 – 9.3 %) | 0.3 % (0.9 %) | 93 / 98 % | -0.03 – 0.00 |
| no IRS + auto | 0.3 % | 0.3 % (5.1 %) | 0 – 1.1 % (0 – 9.9 %) | 0.1 % (1.1 %) (6.9 %) | 40 / 90 % (31 / 88 %) | -0.03 – 0.00 (-0.26 – -0.19) |
| no IRS, plex as a block | 5.3 % | 4.7 % (59 %) | 3.4 – 6.4 % (4.0 – 70 %) | 0.2 % (1.2 %) (11 %) | 95 / 99 % | -0.04 – 0.00 (-0.26 – -0.19) |

What changed (D71): without IRS the composition check and the ratio method
work within each plex, so a pulldown is seen through the plex effect; after
IRS on the plex means limma's residual df are reduced by the plexes - 1.
The default's rows are the same tables and the same numbers.

What this says, on data like this simulation:

- **The default is calibrated**, unequal channels (2 vs 6) included, and
  finds 95 % of the 2-fold changes at adjusted p ≤ 0.05.
- **A pulldown needs `auto` normalisation (D64) in TMT too.** After IRS,
  median centring shifts every unchanged protein by -0.2 (2-fold) to -0.4
  log2 (4-fold); at adjusted p alone most calls are then false. The
  fold-change cut-off keeps most of them out of the hits, not all: 8.4 % false
  hits with 2 DMSO channels per plex. The lab PC's `normalize: median` is
  this row.
- **Without IRS the plex effect stays in.** The plain model then finds 40 %
  of the 2-fold changes (conservative: 0.3 % false); the doctor warns
  (`TMT_PLEXES_NOT_IN_MODEL`) and "How far to trust this" marks it. A plex
  block gets the power back. Since D71 the normalisation compares the
  samples within each plex when the plexes are not on one scale, so a
  pulldown is seen there too (before, its unchanged proteins shifted by
  -0.2 log2 and 59 % of the calls at adjusted p alone were false).
- **IRS on the plex means spends a degree of freedom per plex.** The plex
  mean is estimated from the channels that are then tested; limma did not
  know and was slightly liberal (6.6 %, up to 9.3 % in one scenario). Since
  D71 each protein's residual df are reduced by the plexes - 1 (5.1 %, up to
  7.7 %), the same as R's limma given the reduced df
  (`tests/golden/tmt_sum/`). Fitting the plex as a block after it instead
  does not help (6.5 % / 5.2 %): with a missing channel, limma's
  `contrasts.fit` approximates a non-orthogonal design's standard errors,
  and such proteins came out liberal (7 – 10 % of unchanged p-values below
  0.05). `irs: auto` uses the plex means only when no reference channel is
  found.

**The guard**: IRS on the pool + auto, 2 and 3 plexes of 4 vs 4 and 2 vs 6,
10 % of 600 proteins 2-fold both ways, 10 tables each; the pooled FDP must
stay at or below **8.5 %**, the sensitivity at or above 0.85, the bias within
0.05 log2. Over 30 other blocks of 10 seeds: mean 4.5 – 4.8 %, SD 0.8 –
1.0 %, range 2.6 – 7.0 %; the fixed seeds measure 2.6 – 5.4 %. A second test
holds the pulldown numbers above in their direction: median centring off by
more than 0.12 log2 and over 20 % false at alpha, `auto` within 0.03, the plex
block within 0.06.

**Two more guards (D71)**, the same way (30 other blocks of 10 seeds):
- IRS on the plex means + auto, 2 and 3 plexes of 4 vs 4 and 2 vs 6, 10 % of
  600 proteins 2-fold both ways: pooled FDP at most **10 %**, sensitivity at
  least 0.83; over the blocks mean 4.7 – 5.0 %, SD 0.8 – 1.4 %, range 2.6 – 9.0 %. The same seeds without the df
  reduction must come out worse (over the blocks: mean 6.2 – 7.4 %, up to
  11.8 %).
- No IRS, 3 plexes of 4 vs 4 and 2 vs 6, 20 % of 600 proteins up 2-fold:
  with the plex as a block FDP at most **10 %** (over the blocks mean 5.0 –
  6.0 %, SD 0.8 – 1.2 %, range 2.7 – 9.3 %), and with or without the block
  the unchanged proteins within 0.06 log2 (over the blocks -0.047 to
  -0.012); without the block the sensitivity stays below 0.7 (0.30 – 0.53),
  which is what the doctor warns about.

These simulations share the DIA benchmark's limits: normal noise on the log
scale, missingness from abundance alone, changes of one size, no outlier
channel, no ratio compression from co-isolated ions (MS2 TMT shrinks real
ratios; the lab's workflow is MS3). They show how the methods behave on such
data, not how the lab's samples behave.

#### Peptides to proteins: median polish vs MaxLFQ (`--kind rollup`, D76)

`simulate.peptide_msstats` writes peptide-level label-free data in the
MSstats format, which the pipeline's own loader rolls up once per
`analysis.rollup`:
- 800 proteins of 1 to 30 peptides, more for abundant proteins.
- Each peptide has its own ionisation offset (SD 1.5 log2).
- 10 % of the proteins are changed.
- Replicate SD is 0.3 log2, differing between proteins, with a loading
  shift per run.
- Weak peptides go missing more often.
- 2 % of the values are off by an interference (SD 2 log2).

The standard grid has 3v3, 4v4, 6v6 and 2v4; 1.5-, 2- and 4-fold changes;
typical and heavy missing values; 5 seeds; median normalisation (2026-10-04):

| Roll-up + imputation | FDP, alpha only | range | FDP, both cut-offs | found 1.5× / 2× / 4× | log2FC bias |
|---|---|---|---|---|---|
| median polish + Perseus (default) | 3.9 % | 1.8 – 6.8 % | 1.2 % | 31 / 71 / 88 % | −0.06 |
| MaxLFQ + Perseus | 4.6 % | 1.3 – 13.2 % | 1.1 % | 33 / 72 / 88 % | −0.06 |
| median polish, no imputation | 4.5 % | 1.6 – 7.1 % | 1.1 % | 37 / 77 / 90 % | −0.03 |
| MaxLFQ, no imputation | 5.8 % | 2.0 – 16.5 % | 1.2 % | 40 / 79 / 90 % | −0.02 |

- **Sensitivity.** MaxLFQ finds a little more of the small changes.
- **FDP.** MaxLFQ's FDP is a little higher, and its worst scenarios are
  worse. In the one traced (4v4, 2-fold, heavy missing values), both
  roll-ups carry a median-normalisation offset of the unchanged proteins
  (−0.17 and −0.19 log2), and MaxLFQ's moves more of them past the cut-off.
- **Precision.** The spread of the unchanged fold changes is the same.

The defaults stay as they were (D76). The guard in `tests/test_rollup.py`
(2 designs, 6 seeds) holds both at ≤ 8.5 %. The model has no shared
peptides, no peptide that changes on its own (a PTM, a variant), and
missingness from abundance only. Whether MaxLFQ suits real Sage data better
is open (ROADMAP).

### A real benchmark sample on the lab's instrument

A simulation cannot stand in for the instrument. The standard real benchmark
is a mix of whole-proteome digests in known ratios, usually human, yeast and
E. coli ("HYE"; Navarro et al., *Nat. Biotechnol.* 2016, doi:10.1038/nbt.3685).

1. **Make two samples** from commercial digests (for example Pierce HeLa
   digest, Promega yeast digest, Waters MassPREP E. coli digest), by weight:

   | | human | yeast | E. coli |
   |---|---|---|---|
   | A | 65 % | 30 % | 5 % |
   | B | 65 % | 15 % | 20 % |
   | expected B / A | 1 | 0.5 | 4 |

   Any ratios work as long as you know them. Keep the total load the same and
   keep most of the protein unchanged (the human part): median normalisation
   assumes that most proteins do not change.
2. **Acquire** at least 3 injections of A and 3 of B (4 is better) with the
   method you want to judge, alternating A and B. Name them so the conditions
   are `A` and `B` (for example `HYE_A_1.raw`); for DIA use the lab's naming
   (`docs/NAMING_CONVENTION.md`).
3. **Search** with a FASTA that holds all three proteomes (reviewed human,
   yeast and E. coli K12 from UniProt, with decoys) and let Ionomos analyse it
   with `A` as the control. Entries must keep their UniProt names
   (`sp|P04406|G3P_HUMAN`): the species is read from them.
4. **Write the expected ratios** in a small YAML file, for example `hye.yaml`:

   ```yaml
   comparison: B vs A          # optional when the analysis has one comparison
   expected:                   # ratio B / A per group; 1 = the unchanged background
     HUMAN: 1
     YEAST: 0.5
     ECOLI: 4
   # log2: true                # the numbers above are log2 ratios instead
   # species_column: Organism  # a column of the quant table that names the species
   # fasta: C:/Fragpipe_Auto/fasta/hye.fasta   # species per accession from the FASTA headers (entry names, OS=)
   # tolerance_log2: 0.25      # how far a group's median may be off before the line says so (default 0.25)
   # proteins:                 # a spike-in instead of species: protein lists as groups
   #   UPS1: ups1_accessions.txt     # a file with one accession or gene per line, or a list
   ```

   Group names are UniProt organism codes (`HUMAN`, `YEAST`, `ECOLI`,
   `MOUSE`, …) or their usual names (`human`, `E. coli`, `Homo sapiens`).
5. **Run** `ionomos benchmark <experiment folder> --expected hye.yaml --open`.

The page (`results/benchmark.html`, with `benchmark.tsv` and
`benchmark.json`) shows per group: the measured against the expected log2
ratio (median, bias, MAD, quartiles, a box plot with the expected value
marked); for the unchanged background, the share called anyway (the **false
positive rate**); for the changed groups, the share called in the right
direction (**sensitivity**); and the false discovery proportion over all
calls. A protein group with members of two species is left out and counted.

How to read it:

- A bias shared by every group is normalisation: the mix was not mostly
  unchanged protein, or the loads differed.
- Ratios pulled towards 1 for the low-abundance species (E. coli at 5 %) are
  ratio compression from interference and from imputation near the detection
  limit. Compare `--imputation none` with the default: analyse the folder
  twice and benchmark each.
- The false positive rate of the human background is the number to watch
  when choosing an imputation setting.

A spike-in (UPS1 / UPS2, a labelled standard) works the same way with
`proteins:` lists; everything not in a list is left out unless it has its
own expected ratio.

**Not run yet on real data.** The code path is tested on
`simulate.mixed_species_pg_matrix`, which has the layout of a DIA-NN matrix
of such a sample (species in `Protein.Names`), not its noise.

## Messy input

`analyze()` never raises, always writes a report, and says what it did with
input it had to repair or could not use. What "plausible but malformed"
covers, and what happens (`tests/test_robustness.py`):

| Input | What happens | Said as |
|---|---|---|
| duplicate feature IDs, blank IDs | each row stays its own feature | a note with the count |
| two columns with one sample name (any table) | read as two samples (`name`, `name.2`) | a note, `DUPLICATE_SAMPLES` |
| two columns with one sample name (FragPipe tables) | the reader can keep only one: the repeat is dropped | a note |
| a sample column without a name | left out | a note |
| a column with no values, or with too many non-numbers | left out | a note naming it |
| all-missing rows | removed by the filter | the pipeline line |
| a single replicate, a single condition, 1 vs 6 | fold change only / QC only / low confidence (D32) | `FOLD_CHANGE_ONLY`, `ONE_CONDITION`, `LOW_CONFIDENCE` |
| constant values, zero variance | tested with the prior, or p = 0 for a t-test | `ZERO_VARIANCE`, `IDENTICAL_SAMPLES` |
| infinities, text in numeric cells (`n.d.`, `#DIV/0!`, `Filtered`) | missing | a note with the count and examples |
| negative or zero intensities | missing; the table is still read as intensities | a note with the count |
| decimal commas, thousands separators (also in tab files) | read; `1,234` alone is read as 1234 | a note; mixed formats are refused with a note |
| values beyond 2^100 or below 2^-100, numbers too large for a float | missing | a note with the count |
| 1 or 2 features | analysed; median normalisation is pointed out as the reason nothing is testable | a note, `FEW_FEATURES`, `VARIANCE_PRIOR` |
| 50,000 features × 6 samples | about 3 s on the development Mac (the lab PC was not timed) | – |
| ragged rows, a BOM, CRLF, NUL bytes, an unterminated quote | read | a note for ragged rows |

Statistics that run but may not mean what they say are reported by
`downstream/guards.py`, never repaired silently:

| Code | When |
|---|---|
| `NO_RESIDUAL_DF` | features tested with one value per group: their p-values come from limma's variance prior alone |
| `VARIANCE_PRIOR` | no prior could be estimated (fewer than 3 features with replicate spread), or its fit did not converge (it stopped at the lower edge of its search range) |
| `ZERO_VARIANCE` | at least 5 % of the tested features (or any hit) have identical replicates in every group |
| `IDENTICAL_SAMPLES` | two samples hold exactly the same values |

Groups that are too small were already handled (D32). A prior with infinite
degrees of freedom (one pooled variance) is limma's normal answer when the
variances are alike; it is stated under "How far to trust this", not raised.

The fuzzing: 37 hand-made messy tables, each also with three random settings
(111 analyses), and 160 seeded random damages to simulated DIA, TMT and
isoDTB tables run in every test run; 6,000 further seeds and 1,665 table ×
setting combinations were run once while this was built. What they found is
in D60. The fuzz damages simulated tables: a real export can be wrong in ways
nobody thought to simulate.

## "How far to trust this"

Every report has this list under the key findings, and `analysis.json` has it
under `trust`. It is not a score. Each line repeats one check the analysis
already makes and gives its number; a line is marked **check** when that
check crossed its own threshold:

| Line | Numbers | Marked when |
|---|---|---|
| Replicates | samples per group | a group has fewer than 3 samples, or the groups differ 2-fold or more in size |
| Replicate agreement | median and lowest correlation of a sample with its own replicates, median CV per condition | the scorecard fails a sample, or a principal component follows the replicate number (`BATCH_SUSPECT`) |
| Missing values | share not measured, share imputed, the missingness pattern | more than 40 % imputed, or random missingness imputed as low values (`IMPUTATION_MISMATCH`) |
| each comparison | tested, up, down; hits resting on imputed values; p-value histogram shape and π0; the prior | low confidence or fold change only; 5 or more hits and 30 % or more of the hits rest on imputed values; a "conservative" or "hump" histogram |
| Power | the fold change found 80 % of the time at p 0.05 and 0.001, at this design's group size and median spread | that fold change is above the \|log2FC\| cut-off |
| Statistics | one line per guard finding above | always |
| Compared with a reference / Benchmark | the verdict lines of `compare.json` / `benchmark.json` / `benchmark_simulated.json` (or `_isodtb` / `_tmt`, whichever `--like` wrote) in the results folder, with a link | made on an analysis with other settings |

The thresholds are those of the checks themselves (the doctor, the scorecard,
D35), plus two that are new here and stated in the list: fewer than 3
samples, and a 2-fold difference in group size.
