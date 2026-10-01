# Lab workflows → headless FragPipe

What each method needs, distilled from the lab SOPs in
[`reference/lab-sops/`](../reference/lab-sops/), the R scripts in
[`reference/lab-scripts/`](../reference/lab-scripts/), and the FragPipe output
folders seen in the inventory. This is what the runners have to reproduce.

## Common: headless FragPipe

```
fragpipe.exe --headless --workflow <wf> --manifest <mf> --workdir <out>
             [--threads N] [--ram G]
             [--config-tools-folder <dir>] [--config-diann <DiaNN.exe>]
```

- Confirmed CLI shape in `prior-work/fragpipe_runner.py`. Launcher: the 22.0
  copy in the inventory has `fragpipe\bin\fragpipe.bat` (headless, console) next
  to `fragpipe.exe` (GUI wrapper); ionomos uses the `.bat` and swaps a configured
  `.exe` for the `.bat` beside it. On the lab PC, FragPipe 24.0 came from its Windows installer: `C:\FragPipe\FragPipe-24.0\bin\FragPipe-24.0.exe`, `lib\fragpipe-24.0.jar`, `jre\`, `tools\` (MSFragger 4.4.1, IonQuant 1.11.20, diaTracer 2.2.1, DIA-NN 2.3.2) — from the first Ionomos report, 2026-09-23. Whether that exe prints headless output like the old .bat is confirmed by the first real search.
- Manifest = `.fp-manifest`, tab-separated: `path \t experiment \t bioreplicate \t DDA|DIA`.
- The FASTA is baked into the `.workflow` file (`database.db-path`). ionomos
  writes a per-job copy of the pinned workflow with `database.db-path` set to the
  method's FASTA from `fasta_dir`, so "FASTA file path is empty" can't happen. If
  that FASTA isn't there, the workflow's own path is used when it exists.
  The FASTA must already contain decoys (FragPipe's "Add decoys").
- **No spaces in any path** (SOP: "Directory must not contain any spaces!").
- Output dir must be empty (SOP). Our `--workdir` is always a fresh `fragpipe\` subfolder.
- Every existing run folder contains: `fragpipe.workflow`, `fragpipe.job`,
  `fragpipe-files.fp-manifest`, `filelist_ionquant.txt`, `modmasses_ionquant.txt`.
  These are written by FragPipe itself — good provenance, keep them.

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
`annotation.txt` per plex: `<channel>\t<sample_name>` lines — FragPipe looks
for it next to the raw files / in the workdir (confirm exact lookup rule for
24.0 headless: it is referenced from the workflow's TMT-Integrator section as
`tmtintegrator.annotation` or found by name — check).

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

**Key output**: `<workdir>\diann-output\report.tsv` (+ `report.pg_matrix.tsv`, etc.).

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
- [ ] Answer: how does FragPipe 24.0 headless locate `annotation.txt`?

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
  FragPipe-Analyst: 0) → median centring (`normalize`: median | gn | none) →
  imputation (`imputation: auto` = Perseus-type down-shifted draws for DIA and
  label-free, none for TMT; also none | min | zero | mindet | minprob | knn) →
  limma (`~0 + condition`, eBayes, 95 % CIs) → Benjamini–Hochberg. Hits need
  |log2FC| ≥ `log2fc` *and* adjusted p ≤ `alpha` (inclusive, as `add_rejections`).
  Welch and Student remain selectable (`test`).
- **Comparisons:** `de_type: control` (each condition vs the recognised
  control, default), `all` (every pair, control as reference), `others` (each
  vs the rest), or an explicit `comparisons:` list. isoDTB: each condition's
  ratios vs 0 (moderated one-sample test).
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
  (settings and every processing step).

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
  | LOW_CONFIDENCE | note | a group has one sample: tested anyway, p borrowed from the replicated groups (D32) |
  | FOLD_CHANGE_ONLY | note | no replicates anywhere (1 vs 1, one isoDTB replicate, a table without p): fold change only |
  | LOW_SAMPLE | decide | a sample has < 40 % of the median identifications (failed injection?) |
  | ZERO_TESTED / NO_VOLCANO / CRASH_* | problem | nothing testable, plot not written, a step crashed |
  | HIGH_IMPUTATION, FEW_FEATURES, NO_HITS, ENRICHMENT | note | worth knowing |
  | TIMES | decide / note | a time course whose time points can't all be read (a name in `analysis.times` that isn't a condition, two times in one name) |
  | LIGANDED_DIRECTION, SITE_ANNOTATION | note | isoDTB: the competition ratio looks reversed; the site annotation file can't be used |

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

Open questions for the lab: which tag the compound-treated sample carries
(`liganded_direction`) and which R and replicate count the lab calls
liganded; which comparisons matter for isoDTB (vs 0, or
compound vs compound?); whether DIA should use FragPipe's own
`combined_protein.tsv` or DIA-NN's matrix; which thresholds people use today.
