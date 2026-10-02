# Lab workflows → headless FragPipe

What each method needs, distilled from the lab SOPs in
[`reference/lab-sops/`](../reference/lab-sops/), the R scripts in
[`reference/lab-scripts/`](../reference/lab-scripts/), and the FragPipe output
folders seen in the inventory. This is what the runners have to reproduce.

## Common: headless FragPipe

Checked on 2026-10-01 against FragPipe's
[headless tutorial](https://fragpipe.nesvilab.org/docs/tutorial_headless.html)
and the source of FragPipe 24.0, compared with 23.1 where it could differ
(D59). **Not yet seen on the PC**: every line below is from documentation or
source, and [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md) is how it gets confirmed.

```
bin\fragpipe.bat --headless --workflow <wf> --manifest <mf> --workdir <out>
                 [--threads N] [--ram G] [--dry-run]
                 [--config-tools-folder <dir>] [--config-diann <DiaNN.exe>] [--config-python <dir>]
```

- **Launcher.** The 23 / 24 installer puts `bin\fragpipe.bat`,
  `bin\FragPipe-24.0.exe`, `lib\`, `jre\`, `tools\`, `python\`,
  `workflows\` and (after first use) `cache\` under
  `C:\FragPipe\FragPipe-24.0\`. `fragpipe.bat` is the headless launcher. It
  needs `JAVA_HOME` or `java` on PATH; Ionomos sets `JAVA_HOME` to the
  installation's `jre`. The `.exe` is a window program (launch4j) that
  returns at once: Ionomos swaps it for the `.bat` beside it and holds the
  job if there is none. Zip builds up to 22 have `fragpipe\bin\fragpipe.bat`.
- **Options.** The eleven above are all FragPipe knows; any other stops it
  ("Cannot recognize the argument", exit code 1). `--help` alone prints the
  version, OS, Java and .NET lines and the usage, and exits with code 1.
  There is no `--version`. `--ram 0` lets FragPipe decide; `--threads`
  defaults to cores − 1. Command-line values override the workflow's
  `workflow.ram` / `workflow.threads`.
- **`--dry-run`** makes every check of a real run (workflow, files, FASTA,
  decoys, tools, TMT annotation), prints the commands it would run and "It's
  a dry-run, not running the commands.", and exits 0. `ionomos preflight`
  uses it.
- **Tools, DIA-NN, Python.** Without `--config-*`, FragPipe uses what its
  window was last set to (`<install>\cache\fragpipe-ui.cache`); its tutorial
  says a first headless run must name them. DIA-NN falls back to the bundled
  `tools\diann\1.8.2_beta_8\windows\DiaNN.exe`; `--config-diann` must name
  `DiaNN.exe` itself. On Windows FragPipe 24 always uses the `python\`
  folder in its installation (packages are put there by the installer).
  MSFragger, IonQuant and diaTracer jars and MSFragger's `ext\` folder (the
  Thermo / Bruker readers) come from FragPipe's Config tab → Download /
  Update; a commercial `license.dat` beside the installation is passed on.
- **Manifest** = `.fp-manifest`, UTF-8, tab-separated:
  `path \t experiment \t bioreplicate \t data type`. Lines starting `#` or
  `//` are skipped. Data types: `DDA`, `DDA+`, `DIA`, `GPF-DIA`,
  `DIA-Quant`, `DIA-Lib` (anything else is read as `DDA`); Ionomos writes
  `DDA` or `DIA`. Bioreplicate is a whole number or empty. **A file that
  does not exist is dropped without a message**, which is why Ionomos checks
  the raws itself before every run. Two files with the same name stop the
  run.
- **Experiment names** keep only letters, digits and `_`: FragPipe turns
  every other character into `_` (`EJQ-2-027` → `EJQ_2_027`). Output
  folders are `<experiment>_<bioreplicate>\`, and one flat folder when the
  spectral library or DIA-NN runs. Ionomos warns when a name will change.
- The isoDTB / TMT / DIA sections below apply to a method by its kind, not its
  key: a method under another key that is `like:` one of them (or run by
  another engine) is treated as that method throughout (D54,
  NAMING_CONVENTION.md "Other conventions").
- **FASTA.** It is in the `.workflow` (`database.db-path`); stock workflows
  have none. Ionomos writes a per-job copy of the pinned workflow with
  `database.db-path` set to the method's FASTA from `fasta_dir`. If that
  FASTA isn't there, the workflow's own path is used when it exists.
  **Decoys**: headless FragPipe stops unless 40-60 % of the entries start
  with `database.decoy-tag` (`rev_`); files of 1 GB or more are not
  counted. Ionomos holds a job whose FASTA would be refused.
- **No spaces in any path.** FragPipe refuses an output folder or a tools
  path with whitespace.
- **Output folder.** A non-empty one is only a warning to FragPipe; Ionomos
  still gives every attempt a fresh `fragpipe\` and moves the old one aside.
- **What FragPipe prints** (the lines Ionomos reads):

  | Line | When | Ionomos |
  |---|---|---|
  | `N commands to execute:` then every step's name and command | before the run | the total for "4 of 31 step(s) done"; not counted as steps starting |
  | `MSFragger [Work dir: C:\…]` | a step starts (steps like `CheckCentroid` have no folder) | the progress line |
  | `Process 'MSFragger' finished, exit code: 0` | a step ends | steps done; a non-zero code fails the job and names the step |
  | `Process returned non-zero exit code, stopping` / `Cancelling N remaining tasks` | after a failed step | failed, also when the launcher exits 0 |
  | `2026-10-01 14:03:11,532 ERROR - <message>` | a check failed before any step | quoted in the reason; explained in plain words |
  | `=====…ALL JOBS DONE IN 12.3 MINUTES=====…` | every step ran | missing = a warning on a done job (D59) |

- **Exit code**: 0 when every step ran; the failing step's code otherwise;
  1 when a check fails before any step.
- **Files FragPipe writes itself** in the output folder: `fragpipe.workflow`
  (the settings used), `fragpipe-files.fp-manifest`, `fragpipe.job`,
  `log_<date>_<time>.txt` (its console, good run or bad),
  `experiment_annotation.tsv`, `sdrf.tsv` (when the workflow has
  `workflow.misc.save-sdrf=true`, as the stock ones do), `filelist_*.txt`,
  `modmasses_ionquant.txt`. Good provenance, keep them. ⚠ The analysis
  currently reads that `sdrf.tsv` as the experiment's own design: open in
  ROADMAP.
- **After every search** Ionomos writes `ionomos_run\run_fingerprint.json`:
  the command line, FragPipe's version block, key settings, output file
  names and sizes, the ends of the console log and what the parsers read.

## isoDTB (best-defined; build first)

**SOP summary** (`How-to-FragPipe-isoDTB`):
1. Load raws, define experiment name and replicates. Files come as
   `<prefix>_<rep>_<fraction>.raw`, 3 reps × 7 fractions typical.
2. Check database = human whole proteome.
3. Quantification tab: **Match Between Runs (MBR) checked**.
4. Empty output dir. ~30–60 min for 3×7.
5. Run the R script to group peptides → labelled sites.

**Workflow file**: a stock `isoDTB-ABPP.workflow` ships with FragPipe (seen in
the 22.0 bundle). The lab may have tweaked it (MBR, mods). Action: export the
lab's actual working workflow from the 24.0 GUI and pin it as
`C:\Fragpipe_Auto\workflows\isoDTB.workflow`.

**Manifest**: one line per raw. experiment = `<prefix>`, bioreplicate = `<rep>`,
type `DDA`. Fractions share experiment+rep and FragPipe merges them. The
existing run `20260902-isoDTB_EJQ-2-027` used experiment `EJQ_2_027`,
bioreps 1–3 — output subfolders `EJQ_2_027_1/`, `_2/`, `_3/`.

**Key output**: `<workdir>\combined_modified_peptide_label_quant.tsv`.

**Post-processing** (`isoDTB_Fragpipe_merge-individual-peptides-to-Site.R`):
- Reads `combined_modified_peptide_label_quant.tsv`.
- Finds every `[561.3387]` (the isoDTB light-label mass) in `Light Modified Peptide`.
- Residue position in protein = `Start` + (letters before the bracket) − 1.
- Joins protein metadata + all `<sample_prefix>_<n> Log2 Ratio HL` columns.
- Groups by (Protein, ProteinID, EntryName, Gene, Description, Residue, Position);
  summarises PeptideCount, ExamplePeptides, per-replicate mean ratio, overall mean.
- Writes `<…>_output.tsv`.
- Inputs we must supply: `input_tsv`, `output_tsv`, `sample_prefix` (== FragPipe
  experiment name), `mod_mass` (config, default `561.3387`).
- **Ported** (`ionomos/downstream/isodtb.py`, 2026-09-23). The output is
  byte-identical to the R script on a deliberately awkward test table
  (`ionomos/tests/golden/`), run with the real script. The sample prefix is
  read from the column names instead of typed in, so several samples in one
  folder each get a table.
- ⚠ Quirk kept for identical output — worth a look by the lab: the script counts
  *every letter* before the label, so an N-terminal mod written `n[42.0106]`
  shifts that site's position by one. Easy to change once confirmed.

## TMT

**SOP summary** (`How-to-FragPipe-TMT`):
1. *(Pre-step in a Thermo tool — "Peak Picking & zero Samples", ~15 min.)*
   ⚠ This is a raw-file conversion step done outside FragPipe. Need to confirm
   what tool this is and whether FragPipe 24 still needs it (MSFragger reads
   `.raw` directly via the Thermo library; this step may be legacy).
2. Load **TMT-10 MS3** workflow. "Just load the folder."
3. Bioreplicate = 1 when replicates are within channels.
4. Define replicates, select tag set (TMT-10 etc.).
5. **Keep the channel token (126, 127N…) in each sample name**; save annotation
   to the raw-file folder.
6. Run. Then run the R annotation script; feed `abundance_gene_MD.tsv` +
   `experimental_annotation.tsv` to FragPipe Analyst; filter 100% non-missing.

**Workflow file**: stock `TMT10-MS3` (FragPipe ships TMT10, TMT10-MS3,
TMT10-phospho, TMT16…). Pin the lab's copy. Existing runs on D: used custom
names (`fragpipe_pax8.workflow`) — check those for lab-specific settings.

**Manifest**: each raw = one plex (or one fraction of a plex). experiment =
plex name, bioreplicate = 1 (per SOP) unless overridden. **Plus** an
annotation file per plex, `<channel> <sample_name>` per line (any
whitespace between them). How FragPipe finds it (`TmtiPanel`, 23.1 and
24.0): the **one** file whose name ends in `annotation.txt` in the folder
that holds all of the plex's LC-MS files. With none, or more than one, it
writes its own `<workdir>\<plex>\<plex>_annotation.txt` naming the channels
`<plex>_<channel>`. What it then requires (`CmdTmtIntegrator`), or the run
stops: as many lines as the workflow's label type has channels
(`tmtintegrator.channel_num`, e.g. `TMT-10`), `NA` as the name of an unused
channel, no spaces in a name, no name twice across plexes, and, with a
reference sample set in the workflow, a name containing its tag
(`tmtintegrator.ref_tag`). Ionomos writes `annotation.txt` from
`experiment.yaml` `tmt:` for one plex (not beside a user's own
`*annotation.txt`), one per folder for plexes in their own folders, and
none for plexes sharing a folder (a warning; D59). A channel map FragPipe
would refuse fails the job before the search.

**Key output**: `<workdir>\tmt-report\abundance_gene_MD.tsv` (and `_MD` variants
for peptide/site).

**Post-processing** (`correct_experimental_annotation_for_Fragpipe-TMT.R`):
- Reads only the header of `abundance_gene_MD.tsv`.
- Keeps columns matching `^[A-Za-z0-9]+_1_\d{3}[A-Z]?$` (condition_plex_channel).
- Builds table: plex, channel, sample, sample_name (drops `_1_`), condition
  (prefix before first `_`), replicate (row number within condition).
- Writes `experimental_annotation.tsv`.
- Note the regex assumes plex index `1` and a single-token condition. Real
  headers look like `DMSO_1_126`.
- **Ported** (`ionomos/downstream/tmt.py`), byte-identical to the R script.
  When names don't follow the pattern the file is empty (as in R); the report
  then takes the condition from the text before the first `_` and says so.

## DIA

No lab SOP yet. From the inventory: DIA runs exist for Chris, EJQ, Isaac
(`Fragpipe-DIANN\`, `FRAGPIPE-DIANN\` folders, some with `_RERUN`,
`_incl-Peptide` variants — i.e. people iterate on DIA settings).

**Workflow file**: stock `DIA_SpecLib_Quant` or `DIA_DIA-Umpire_SpecLib_Quant`.
Ask which the lab uses. Pin it.

**Manifest**: experiment = sample prefix, bioreplicate = rep, type **`DIA`**.

**DIA-NN**: FragPipe bundles DIA-NN (1.8.x, license-restricted) and 24.0 may
require pointing at an external `DiaNN.exe` for 2.x. DIA-NN 2.3.2 is installed
on the PC. Try the bundled one first; if the headless run complains, set
`fragpipe.config_diann` in config.

**Key output**: `<workdir>\dia-quant-output\report.tsv` (+
`report.pg_matrix.tsv`, `report.stats.tsv`) in FragPipe 24; earlier versions
wrote `diann-output\`. With a spectral library or DIA-NN in the workflow
there are no per-experiment folders. ⚠ The stock workflow's own notes say
"For quantification using DIA-NN, Thermo/Sciex DIA files should be in mzML
format": whether `.raw` works on the PC is for the first DIA run to show.

**Post-processing**: none known. Leave `postprocess: []` and ask users what
they do next (FragPipe Analyst upload?).

## What to collect from the lab to finish this doc

- [ ] The three pinned `.workflow` files exported from the FragPipe 24.0 GUI
      (with FASTA set), and the FASTA files they reference.
- [ ] One complete, successful output folder per method (copy of the small
      files only — `*.tsv`, `*.workflow`, `*.fp-manifest`, `annotation.txt`,
      `log*.txt`) to develop parsers/post-proc against.
- [ ] One R-script output per method to diff the Python port against.
- [ ] Answer: what is the TMT "Peak Picking & zero Samples" pre-step tool?
- [x] Answer: how does FragPipe 24.0 headless locate `annotation.txt`? → the one
      `*annotation.txt` in the plex's folder (TMT section above).
- [ ] The `run_fingerprint.json` of the first real search of each method.

## Downstream (all methods): statistics, volcano plots, report

After FragPipe, `ionomos/downstream/` turns each method's main table into one
features × samples matrix of log2 values and runs the same statistics:

| Method | Table read | Level | Test |
|---|---|---|---|
| isoDTB | `<prefix>_sites.tsv` (from the R port) | site | each sample's replicate log2 H/L vs 0 |
| DIA | DIA-NN `*pg_matrix.tsv` (runs mapped to conditions via the manifest) | protein | condition vs control |
| TMT | `tmt-report/abundance_gene_MD.tsv` (+ R-port annotation) | gene | condition vs control |
| label-free DDA (future) | `combined_protein.tsv` (MaxLFQ if present) | protein | condition vs control |

- **Pipeline:** FragPipe-Analyst's, ported from FragPipeAnalystR (D24) and
  checked against the real package: contaminants removed → features kept when
  measured in ≥ 50 % of at least one condition (`filter_condition_pct`;
  FragPipe-Analyst: 0) → normalisation (`normalize`: auto | median | gn | ratio | none; see below) →
  imputation (`imputation: auto` = Perseus-type down-shifted draws for DIA and
  label-free, none for TMT; also none | min | zero | mindet | minprob | knn) →
  limma (`~0 + condition`, eBayes, 95 % CIs) → Benjamini–Hochberg. Hits need
  |log2FC| ≥ `log2fc` *and* adjusted p ≤ `alpha` (inclusive, as `add_rejections`).
  Welch and Student remain selectable (`test`).
- **Comparisons:** `de_type: control` (each condition vs the recognised
  control, default), `all` (every pair, control as reference), `others` (each
  vs the rest), or an explicit `comparisons:` list. isoDTB: each condition's
  ratios vs 0 (moderated one-sample test).
- **Roles** (`downstream/roles.py`, D61). Each condition is a control, a
  compound, a competition (probe plus competitor), a pooled reference or a
  QC standard, read from its name, from `analysis.roles`, or from an SDRF's
  `characteristics[role]` column. With a competition condition the default
  comparisons are compound vs control, competition vs its compound and
  competition vs control (see "Competition experiments" below). isoDTB is
  a competition experiment by construction and is unchanged.
- **Per experiment** (Analysis tab or `experiment.yaml analysis:`):
  `sample_conditions: {Drug_4: DMSO}`, `exclude_samples: [DMSO_3]`,
  comparisons and any setting above.
- **Enrichment:** each comparison's up and down hits vs the quantified genes,
  hypergeometric test, BH; Enrichr libraries (Hallmark, GO BP/MF/CC, KEGG,
  Reactome, WikiPathways) downloaded once, or a lab `.gmt` (D26).
- **Outputs** (`results/`): `report.html` (interactive, self-contained, works
  offline), `<comparison>_differential.tsv`, `<level>_results.tsv` (all
  comparisons + values used), `<level>_matrix_log2.tsv` (as loaded),
  `<level>_matrix_processed.tsv` (filtered/normalised/imputed + which values
  were imputed), `enrichment.tsv`, `volcano_<comparison>.svg`,
  `fragpipe-analyst/` (annotation + `reproduce_in_R.R`), `analysis.json`
  (settings and every processing step), and `figures/` when asked for (below).

- **Figures for slides** (D62). In the report, every chart has **SVG**,
  **PNG** and **Export…** (size, text, colours, title, legend; copy as an
  image), and **Export for slides** saves every figure, the tables and a
  README in one .zip. Without the report:

  ```
  ionomos export <experiment or results folder>            # results/figures/*.svg + README.txt
  ionomos export 12 --preset col1 --palette colorblind     # job 12, one journal column, colour-blind safe
  ionomos export <folder> --style export_style.json --figures volcano,pca --out D:/talk
  ```

  It draws the volcano of each comparison, the PCA, the heatmap of the hits
  and the sample correlation from the finished report's data, at the saved
  cut-offs. SVG only (PNG: the report's Export). The lab's style is
  `analysis.export` in `config.yaml` (an experiment can change it in
  `experiment.yaml`): `size` (`slide169` | `slide43` | `half` | `col1` |
  `col2` | `custom` with `width`, `height`, `unit`), `font_pt`,
  `font_family`, `palette` (`default` | `colorblind` | `grey` | `custom` with
  `up`, `down`, `neutral`), `background`, `line_scale`, `point_scale`,
  `title`, `subtitle`, `legend`, `note`, `label_count`, and `figures`: the
  static figures written to `results/figures/` after every analysis (any of
  `volcano`, `pca`, `heatmap`, `correlation`; default none). Style order for
  `ionomos export`: the report's own, the lab's now, `--style`, the flags.
  A figure file of the same name is replaced only if Ionomos wrote it.

- **Small groups** (D32). A comparison is never refused for having too few
  replicates. When a group has one sample, limma still fits the model over
  every sample, so it borrows the variance from the replicated groups (a Welch
  test becomes a pooled t-test). The comparison is labelled **low confidence**
  in the report, on the volcano SVG, in `analysis.json` (`"confidence":
  "low"`) and on the command line. With no replicates anywhere (1 vs 1, a
  single isoDTB replicate), there's nothing to estimate variance from, so the
  plot is **fold change only**. It shows log2FC against mean abundance (or
  rank, for ratio data), and candidates are features with |log2FC| ≥ the
  cut-off. No p-values are invented.

- **Unequal groups** (D61). Two DMSO against four of each compound is a
  normal design and is not flagged. limma estimates each feature's variance
  from all samples of all conditions, so a comparison with a small group
  borrows the variance from the larger ones; its standard error is
  `s·√(1/n₁ + 1/n₂)`. The report gives the samples on each side of every
  comparison (Methods, under the volcano, `analysis.json` → `comparisons[].samples`)
  and the minimum detectable fold change per comparison (Quality control →
  Power; `analysis.json` → `quality.detectable_log2fc`). Without imputation
  (TMT), the smaller group of an unequal comparison needs half its samples
  measured instead of `min_valid` (`small_group_min_valid: half`; `same`
  for the earlier rule); the features this lets in are counted in the notes
  and their n is in the table. A group of one is low confidence as before.

- **Any table** (`downstream/anytable.py`, D33): `ionomos analyze <file>` or
  Analysis tab → **Table…**. TSV, CSV (including `;` with decimal commas),
  TXT and Excel `.xlsx` (first sheet). Two shapes are recognised:
  - Quantities: an ID column plus one numeric column per sample. This covers
    MaxQuant `proteinGroups.txt` (LFQ intensity; `+`-flagged rows dropped),
    Spectronaut `.PG.Quantity`, Proteome Discoverer abundances, FragPipe or
    DIA-NN matrices, and any hand-made sheet. Raw intensities are
    log2-transformed; log2 values are kept. They go through the full pipeline.
  - Results: a fold-change column and a p-value column per comparison, e.g.
    limma topTable, Perseus (`-Log p-value`), DESeq2 (linear FoldChange →
    log2), or a FragPipe-Analyst export with several comparisons. These are
    plotted as given, with the lab's cut-offs; q is BH-computed if missing.

  Output goes to `<table name>_ionomos/` next to the file, never beside it,
  so nothing of the user's is overwritten. With no file given, a folder
  without FragPipe tables is scanned for the best table. Long-format
  intermediates (`psm.tsv`, `report.tsv`, …) and files over 100 MB are
  skipped. A file that is neither shape gets an `UNUSABLE_TABLE` note saying
  what to export.

- **Checks after every analysis** (`downstream/doctor.py`, shown at the top of
  the report; *decide* and *problem* ones also pop up a window with the fix):
  | code | severity | when |
  |---|---|---|
  | NO_TABLE / EMPTY_TABLE | problem | FragPipe produced no (or an empty) result table for the method |
  | UNUSABLE_TABLE | problem | the table exists but holds nothing usable (isoDTB: no probe-labelled peptides / ratio columns) |
  | NO_QUANTITIES | problem | rows but not one measured value in the sample columns |
  | ONE_SAMPLE | problem | a single sample of intensity data was quantified — nothing to compare |
  | NOTHING_LEFT | problem | every sample was left out, or the filters removed every feature |
  | MISSING_RUNS | problem | searched runs with no quantities in the table |
  | UNMATCHED_RUNS | decide | runs not in the experiment's file list (conditions guessed) |
  | DUPLICATE_SAMPLES | decide | two runs with the same sample name (re-acquisitions) |
  | ONE_CONDITION | decide | every sample in one condition — conditions suggested from the file names |
  | NO_CONTROL | decide | no condition looks like a control; the guess is used until confirmed |
  | BAD_COMPARISON | decide | chosen comparisons don't fit; defaults used meanwhile |
  | EACH_OWN_CONDITION | decide | every sample is its own condition (replicates named with letters?) — grouping suggested |
  | SMALL_GROUP | decide | a group is too small and not even a fold change could be computed |
  | ROLES_UNSURE | decide | a condition's role can't be told from its name (`Probe_pre`, `Probe_10x`), or a competition can't be linked to one compound; the guess is used meanwhile (D61) |
  | COMPETITION_DESIGN | note | read as a competition experiment: the roles and comparisons chosen, and how to change them (D61) |
  | LOW_CONFIDENCE | note | a group has one sample (or fewer than `min_valid`): tested anyway, p borrowed from the replicated groups (D32) |
  | FOLD_CHANGE_ONLY | note | no replicates anywhere (1 vs 1, one isoDTB replicate, a table without p): fold change only |
  | LOW_SAMPLE | decide | a sample has < 40 % of the median identifications (failed injection?) |
  | ZERO_TESTED / NO_VOLCANO / CRASH_* | problem | nothing testable, plot not written, a step crashed |
  | HIGH_IMPUTATION, FEW_FEATURES, NO_HITS, ENRICHMENT | note | worth knowing |
  | NORMALISATION_COMPOSITION | decide / note | median centring would shift the conditions against each other (many features change one way): asks when `median` / `gn` is chosen, a note when `auto` switched to the ratio method |
  | TIMES | decide / note | a time course whose time points can't all be read (a name in `analysis.times` that isn't a condition, two times in one name) |
  | LIGANDED_DIRECTION, SITE_ANNOTATION | note | isoDTB: the competition ratio looks reversed; the site annotation file can't be used |
  | PSM_MASS_ERROR, PSM_MISSED_CLEAVAGES | note | a run's median precursor mass error is 10 ppm or more from 0; half or more of a run's PSMs have a missed cleavage |
  | NO_RESIDUAL_DF | note | features tested with one value per group: their p-values come from limma's variance prior alone (D60) |
  | VARIANCE_PRIOR | note | limma's variance prior could not be estimated (fewer than 3 features with replicate spread), or its fit did not converge |
  | ZERO_VARIANCE | note | 5 % or more of the tested features, or any hit, have identical replicates in every group (rounded, copied or constant-imputed values) |
  | IDENTICAL_SAMPLES | note | two samples hold exactly the same values (a file or column loaded twice) |

- **Messy tables** (D60, `downstream/guards.py`). A table is read as far as
  it can be, and every repair is a note in the report: duplicate or blank
  IDs (each row stays a feature), a sample column named twice (any table:
  `name`, `name.2`; FragPipe tables: the repeat is dropped), a column without
  a name or without values (left out), text or infinite cells and negative
  intensities (missing), values beyond 2^±100 (missing). In a table given to
  `ionomos analyze`, decimal commas (`1234,5`) and thousands separators
  (`1,234,567.8`) are read in tab and comma files too; `1,234` alone is read
  as 1234 and the note says so. The full list: [VALIDATION.md](VALIDATION.md).

- **How far to trust this** (D60, `downstream/trust.py`). Under the key
  findings of every report, and in `analysis.json` → `trust`: samples per
  group, replicate agreement, missing and imputed values, what each
  comparison tested and found, the p-value histogram shape, power, and any
  guard finding, each with its number. No score. `ionomos compare` (this
  analysis against a reference result) and `ionomos benchmark` (simulated
  data with planted changes, or a mixed-species run with known ratios)
  measure accuracy; their verdicts show in the same list. See
  [VALIDATION.md](VALIDATION.md).

**Search quality per run** (`downstream/psmqc.py`, D55). When the search
wrote `psm.tsv` files (FragPipe DDA: isoDTB, TMT, LFQ), the report's Quality
control section has a **Search quality** tab with one row per raw file:

| Shown | From `psm.tsv` |
|---|---|
| PSMs, peptides, proteins | rows, distinct `Peptide`, distinct `Protein` of the run (the run is read from `Spectrum`) |
| Mass error (ppm) | `Observed Mass` against `Calculated Peptide Mass`, isotope-error corrected: median, quartiles, 5th and 95th percentile. Errors over 50 ppm are mass offsets and are left out |
| Missed cleavage | share of PSMs with `Number of Missed Cleavages` ≥ 1; the chart splits 0 / 1 / 2 or more |
| Charge states | share of PSMs per `Charge` |
| Length | median `Peptide Length`; the chart shows all runs together |

- A run is a raw file. For TMT that is a fraction of a plex; the folder its
  `psm.tsv` is in is shown as the sample.
- With DIA the tab shows DIA-NN's own summary of each run
  (`report.stats.tsv`): precursors, proteins, MS1 / MS2 mass accuracy, mean
  missed cleavages and charge, peak width. Nothing is flagged on it.
- A run with at least 100 PSMs is flagged when its median mass error is
  10 ppm or more from 0 (`PSM_MASS_ERROR`), or when half or more of its PSMs
  have a missed cleavage (`PSM_MISSED_CLEAVAGES`). Both limits are wide and
  are not the lab's: they are constants in `psmqc.py` until the lab has
  looked at its own runs.
- `psm.tsv` files are read row by row. One over 4,096 MB is not read; the
  report notes it.
- Setting: `psm_qc` (false switches it off).
- Output: `results/psm_qc.tsv`, `analysis.json` → `psm_qc`.
- Not tested on real FragPipe output: the column names are from the FragPipe
  documentation.

**Competition experiments** (`downstream/roles.py`, D61). A condition whose
name has `comp`, `competition`, `competitor`, `competed`, `compete`,
`competing` or `excess` as a word (`Probe_Comp`, `Probe+Comp`, `ProbeComp`,
`KL6283A_Comp_KL6159A`) is the compound plus a competitor, linked to the
compound named by the rest of it (or the only compound).

| Comparison | Reads as |
|---|---|
| compound vs control | enrichment / engagement |
| competition vs its compound | what the competitor displaces (down = competed off) |
| competition vs control | what is left with the competitor |

- **Specific targets** of a compound: significant *up* in compound vs
  control and significant *down* in competition vs compound, each at the
  report's cut-offs. *Enriched, not competed* is listed beside them.
- Not compared by default: two controls, a pool, a QC standard, one
  compound's competition with another compound.
- `pre`, `pretreat…`, `block…`, `cold` and `10x` count only when the rest
  of the name is another condition (`Probe_pre` next to `Probe`); the
  analysis runs on that reading and asks (`ROLES_UNSURE`).

```yaml
analysis:                      # experiment.yaml (one experiment) or config.yaml (lab)
  roles: {DMSO: control, Probe: compound, Probe_Comp: competition of Probe}
  competition_keywords: [comp, competition, competitor, competed, compete, competing, excess]
  competition_keywords_weak: [pre, pretreat, pretreated, pretreatment, block, blocked, blocking, cold]
  role_comparisons: true       # false: every condition vs the control, as before
```

- `comparisons:`, `de_type: all | others` work as before. With `control:`
  set, every condition is still compared with it and competition vs
  compound is added.
- Output: `results/specific_targets.tsv` (per compound and feature: the
  call, enrichment log2FC / p / adjusted p, competition log2FC / p /
  adjusted p, % competed off, what is left, measured n per group),
  `analysis.json` → `roles`, `specific_targets`, and the report's
  **Specific targets** section (enrichment across, competed off up, the
  quadrant of specific binders marked; the calls follow the live cut-offs).
- Not confirmed by the lab: the keywords and the rule. Not tested on a real
  experiment.

**Normalisation** (`fpa.normalize_info`, D64). `auto`, the default for new
set-ups, is FragPipe-Analyst's median centring unless that would shift the
conditions against each other:

- Median centring lines up the middle of each sample's abundance
  distribution. When a share of the features is enriched in one direction (a
  pulldown, a depletion), that middle moves and every unchanged feature is
  pushed the other way: about 0.2 log2 with 8 % enriched in simulation.
- The **ratio** method shifts each sample by the median, over the stable
  three quarters of the features measured in every sample, of the feature's
  value minus its mean across samples. A feature's own ratio is narrow, so
  enrichment barely moves it; the features that vary most are left out first.
  It needs 20 features measured in every sample, else median centring is used.
- **The check**: the two methods' sample shifts are averaged per condition.
  If they disagree by more than 0.1 log2 between two conditions, and by more
  than 3 times the scatter among replicates, `auto` uses the ratio method and
  says so (`NORMALISATION_COMPOSITION`, a note). With `median` or `gn` chosen
  explicitly, the same finding asks the user to switch.
- When nothing changes in one direction, `auto` is median centring, with
  identical numbers.
- `analysis.json` → `normalisation` records the method asked, the method
  used and the check.

**Time courses** (`downstream/timecourse.py`, D53). When a series has three
or more time points (`Drug_0h`, `Drug_1h`, `Drug_4h`, … or `analysis.times`),
the report adds, per series and feature:

| Question | Test (limma, on the comparisons' model) |
|---|---|
| Does it change over time? | moderated F on every time point against the first |
| Is there a steady rise or fall? | moderated t on the linear contrast over the ordered time points |
| Does it respond differently from the control series? | moderated F on the interaction contrasts (two series sharing time points, one a control such as `DMSO_0h` …) |

- *Changing* = F adjusted p ≤ `alpha` and a largest |log2FC| ≥ `log2fc`
  against the first time point; classed up / down / mixed. Changing features
  are grouped into at most 6 patterns by profile shape.
- The trend uses the order of the time points, not the hours, so `0 / 1 h /
  24 h` is not dominated by the long gap.
- Time is a factor: no curve or spline is fitted. Features not measured at
  every time point of a series are not tested (unless imputed).
- Settings: `times`, `time_unit`, `time_min_points` (3), `time_course`
  (false switches it off). Intensity data with the limma test only.
- Output: `results/time_course.tsv`, `analysis.json` → `time_course`, the
  report's **Time course** section. The pairwise comparisons are unchanged.

**isoDTB: liganded sites** (`downstream/cys.py`, D52). Besides the test
against 0, every site gets the chemoproteomics call per compound (each
sample prefix is a compound):

| Call | Rule (defaults) |
|---|---|
| liganded | competition ratio R ≥ 4 in at least 2 replicates |
| inconsistent | R ≥ 4 in some replicates, but fewer than 2 |
| not liganded | measured in at least 2 replicates, R ≥ 4 in none |
| too few | measured in fewer than 2 replicates (not assessed) |

```yaml
analysis:                      # config.yaml (lab) or experiment.yaml (one experiment)
  liganded_ratio: 4            # R a replicate must reach
  liganded_min_replicates: 2   # ... in at least this many replicates
  liganded_direction: high     # high: R = heavy / light (treated sample = light tag) | low: R = light / heavy
  site_annotation: cysdb.csv   # optional: a site table you downloaded (CysDB), in the experiment folder or a full path
  liganded: true               # false: no calls
```

- The calls use the ratios as measured: no normalisation, no imputation. A
  compound with fewer replicates than the rule asks for is judged on the
  replicates it has, with a note.
- **Liganded fraction** per compound = liganded / sites measured in enough
  replicates.
- **Selectivity** (2+ compounds): *selective* = liganded by one compound and
  measured as not liganded by every other; *shared* = liganded by several;
  *unresolved* = liganded by one, the others not measured well enough.
- **Proteins**: for each protein with a liganded cysteine, how many of its
  assessed cysteines are liganded. Three or more assessed with at least half
  liganded is marked "most sites": suspect the protein amount, not a site.
- **Site annotation**: Ionomos does not ship CysDB (its licence is the
  lab's business). A table the lab downloads is matched by UniProt accession
  and residue number: one column of keys like `P04406_C152`, or an accession
  column plus a residue column. Its yes / no columns become flags;
  `ligandable`, `hyperreactive` and `identified` give *known liganded*,
  *known hyperreactive*, *seen before*; a site not in the table is *new*.
- **Direction check**: if at least 10 sites, and more than three times as
  many as are liganded, would be liganded with the ratio the other way
  round, the report warns (`LIGANDED_DIRECTION`).
- Output: `results/cysteine_sites.tsv` (every site: per compound log2 R, R,
  engagement % = 100 × (1 − 1/R), replicates, replicates over, call; then
  liganded_by, selectivity, annotation), `results/cysteine_proteins.tsv`,
  `analysis.json` → `cysteines`, and the report's **Liganded sites** section.
- Not built yet: correcting site changes for protein abundance (the
  MSstatsPTM adjustment). It needs a matching unenriched proteome, and the
  lab has to say where that comes from.

Open questions for the lab: which words mark a competition condition and
what the lab calls a specific target (D61, ROADMAP); which tag the compound-treated sample carries
(`liganded_direction`) and which R and replicate count the lab calls
liganded; which comparisons matter for isoDTB (vs 0, or
compound vs compound?); whether DIA should use FragPipe's own
`combined_protein.tsv` or DIA-NN's matrix; which thresholds people use today.
