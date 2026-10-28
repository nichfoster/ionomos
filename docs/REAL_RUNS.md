# Real runs on the lab PC

What has actually run on the proteomics PC, what went wrong, and what each
problem became. [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md) is the checklist for
a run; this page is the record of the runs. Add a row (and a section if
something was learned) after every real search or analysis that tells us
something new.

The evidence is the "Report a problem" bundles (`Ionomos-report-<date>-v<x>.zip`),
the experiment's `ionomos.json`, `experiment.yaml` and `results/`, and the
maintainer's notes. The repository is public: people, cell lines, compounds,
targets, protein names and hit lists from unpublished experiments stay out
of this page. An experiment is described by its design; its real folder
name is in the job's `ionomos.json` on the PC.

## The runs

| Date | Version | What ran | Outcome |
|---|---|---|---|
| 2026-09-16 | LabWatch 0.1.0 | `labwatch status --all` on the PC's testbed config | Crashed: `UnicodeDecodeError … byte 0x97`. Read with a warning since D86 ([Open](#open)) |
| 2026-09-23 14:23 | 0.5.0 | First install, setup checklist | No DIA or isoDTB workflow pinned yet. FragPipe copies, `Fasta-files`, `New folder` and `QC` were listed as users (fixed in 0.5.1) |
| 2026-09-23 15:05 | 0.5.1 | A lab member's 7 `.raw` files dropped loose in the inbox | Ignored (fixed in 0.5.2, D23) |
| 2026-09-23 15:16 | 0.5.1 | **Job 1**, `<initials>_<cell line>_FLAG_<bait>_<compound>`, DIA, 6 raws | **First real FragPipe search.** Exit 0 after 14 min. DIA-NN's matrix has 4 of the 6 runs; the report had no volcano. [Job 1](#job-1-the-first-real-search) |
| 2026-09-23 16:40 | 0.5.3 | **Job 2**, the same raws dropped loose again, grouped by Ionomos | Searching when the 16:45 bundle was taken; how it ended was not reported. [Job 2](#job-2-loose-files-grouped) |
| 2026-10-06 | 0.18.0 | First drop on the app's Analysis tab | The app froze (fixed in 0.18.1, D82) |
| 2026-10-06 | 0.18.0 | Analysis tab on a FragPipe GUI result (a lab member's FLAG pull-down, DIA, 9 runs) | Report made. Compared with FragPipe-Analyst: a protein seen in one condition only was a hit there and filtered out here (the missing-value filter). [The FragPipe-Analyst comparison](#2026-10-06-a-fragpipe-gui-result-analysed-and-compared-with-fragpipe-analyst) |
| 2026-10 (reported 2026-10-28) | not recorded (0.19.0 to 0.21.0) | TMT, DIA and DDA searches through the watcher (FragPipe headless) | Finished, as reported by the maintainer. The run records (`run_fingerprint.json`, console logs) are still to be filed here. [The October searches](#2026-10-tmt-dia-and-dda-searches-through-the-watcher) |
| 2026-10-28 | 0.21.0 | Default analysis of three real FragPipe results (isoDTB, TMT, DIA) from the owners' output folders, not through the watcher | Three reports. The doctor flagged a mixing error (isoDTB) and a batch effect by replicate (TMT). The TMT result against the owner's FragPipe-Analyst table: same log2 fold changes (r = 1.000), 134 of its 143 hits shared. [The analyses](#2026-10-28-real-isodtb-tmt-and-dia-results-analysed-and-a-tmt-result-compared-with-fragpipe-analyst) |

## Job 1: the first real search

2026-09-23, Ionomos 0.5.1, FragPipe 24.0 through `bin\fragpipe.bat
--headless`. The experiment folder was named
`<initials>_<cell line>_FLAG_<bait>_<compound>`; the real name is in the
job's `ionomos.json` on the PC.

- The folder name had no method keyword, so the resolver window opened. The
  first answer was "skip" (`REJECTED`). After a restart, DIA was chosen and
  the job queued. The owner's user folder was made from the window.
- The inbox first held 7 files: `…_DMSO_1.raw` and
  `…_DMSO_1_20260508180610.raw` were the same sample. One was removed before
  the job queued.
- **The Xcalibur re-acquisition timestamp was read as the replicate.**
  `experiment.yaml` and the FragPipe manifest got bioreplicate
  `20260508180610` and `20260508204737` for DMSO 1 and 2. Since 0.5.2 the
  timestamp is ignored (D23), and since 0.8.0 replicate tails are 1–999.
  Current naming reads these files as DMSO 1, 2, 3 and `<compound>` 1, 2, 3.
  An explicit `bioreplicate:` in `experiment.yaml` was still passed on
  unchecked until D85 (after 0.18.1). It is now bounded to 1–999 like a
  file name's: such a file opens the naming window (or leaves a note), and a
  job already queued with such a number waits until its `experiment.yaml`
  gives the file's replicate.
- **DIA-NN's `report.pg_matrix.tsv` has 4 run columns, not 6.** The two
  missing runs are exactly the two with the 14-digit replicate number. That
  number does not fit a 32-bit integer. **FragPipe's source explains it**
  (round 4, 2026-10-15): `TabWorkflow.manifestLoad` reads the bioreplicate
  with `Integer.parseInt`, and a line it refuses is left out with one ERROR
  line, "Manifest file contained some badly formatted lines", while the run
  goes on with the other four files (exit code 0). The testbed's fake now
  does the same. **Still to confirm** in the job's
  `ionomos_run\fragpipe_console.log`
  (`C:\Fragpipe_Auto_Users\<user>\<experiment>\`): that ERROR line
  with the two DMSO lines. Job 2 (replicates 1 and 2) passed all six raws to
  MSFragger.
- 0.5.1 looked for DIA-NN's tables in `diann-output\`. FragPipe 24 writes
  them to `dia-quant-output\`, so the job ended `done` with the warning "none
  of the expected DIA outputs found". Fixed on 2026-09-23 (the DIA output
  identity entry in DECISIONS.md); the docs followed in 0.14.0.
- **No volcano** (the maintainer's note on the 16:45 bundle). The 0.5.3
  re-analysis read the matrix's run columns, the `…_uncalibrated.mzML` files
  FragPipe writes for DIA-NN. They did not match the manifest's `.raw` names,
  so each run became its own condition. The result was three comparisons of
  one run against one run, 0 proteins tested and no plot. Fixed: the
  `_uncalibrated` / `_calibrated` suffix is matched back to the manifest, and
  runs missing from the matrix are reported (2026-09-23 entry).
  A group of one is now tested and labelled low confidence (D32).
- The matrix held 1,608 protein groups.

## Job 2: loose files grouped

2026-09-23, 0.5.3. The six raws, dropped loose, were grouped into
`<initials>_<cell line>_FLAG-<bait>_<compound>-<dose>` (D23; the real name
is in the job's `ionomos.json` on the PC). The folder name had no method keyword, so
the window was asked again; the answer was learned (`naming-history`). The
manifest had replicates 1–3. What the console shows about the PC:

- `fragpipe.bat` started FragPipe with its own Java (`Java Info: 17.0.10,
  OpenJDK … Eclipse Adoptium`). It did so on 0.5.1 and 0.5.3, before Ionomos
  set `JAVA_HOME` for it (0.14.0, D59). So on this PC the script finds a Java
  by itself; setting `JAVA_HOME` does no harm.
- Tools: FragPipe 24.0, MSFragger 4.4.1, MSBooster 1.4.14, Percolator 3.7.1,
  FragPipe-SpecLib 0.1.58, DIA-NN 2.3.2, Philosopher 5.1.3-RC9.
- The lab's `DIA.workflow`: MSFragger → MSBooster → Percolator → spectral
  library → DIA-NN. diaTracer and DIA-Umpire are off. FASTA
  `2026-01-30-decoys-contam-UP000005640_9606.fasta.fas` (20,698 targets +
  20,698 `rev_` decoys, plus FragPipe's `contam_` entries).
- **Thermo `.raw` works for DIA.** MSFragger read the `.raw` files directly.
  FragPipe wrote `…_uncalibrated.mzML` for the later steps.
- The console reached Ionomos line by line; the progress line read
  "MSFragger (3 step(s) done)".
- While it ran, the status listed two causes for a problem that did not
  exist: "No protein database" (from the `database.db-path` setting
  FragPipe prints on every run) and "A .raw file couldn't be read" (from
  Thermo's reader banner). Both were fixed in 0.14.0.

## 2026-10-06: a FragPipe GUI result analysed and compared with FragPipe-Analyst

A lab member searched a FLAG pull-down (DIA, 9 runs: vehicle, compound,
empty-vector control, 3 replicates each) in the FragPipe window. The
Analysis tab (0.18.0) then analysed the FragPipe folder (D80).

- 2,194 protein groups loaded. The missing-value filter (values in ≥ 66 % of
  all samples and ≥ 66 % of one condition) left 2,040. Then median centring
  (the composition check passed: 0.09 log2 between conditions, limit 0.1),
  Perseus-type imputation of 597 values (3.3 %), and limma against the
  vehicle.
- `RUN_ORDER_CONFOUNDED` fired, correctly: the conditions were acquired in
  three blocks of three (D78).
- **First real `psm.tsv` and DIA-NN `stats.tsv`.** Search quality per run
  (D55) read all 9 runs and 38,105 PSMs from FragPipe's real column names.
- **Contaminants were not removed.** The FASTA has FragPipe's `contam_`
  entries, but DIA-NN writes the plain accession in `Protein.Group`
  (`P02769`, `P00761`). The rule looks for the text "contam", so it removed
  nothing. Bovine serum albumin was then reported under the human gene name
  `ALB`, next to human albumin, and porcine trypsin stayed in the matrix.
  FragPipe-Analyst uses the same rule and keeps them too. Fixed after 0.18.1
  (D87): the accessions of the FASTA's `contam_` entries are removed too.
  [Open](#open) until it is seen on the PC.
- The empty-vector condition was taken as a control by its name (`EV` is a
  control keyword), with the vehicle as *the* control. So the comparison is
  "empty vector vs vehicle", and proteins enriched by the bait are listed as
  *down*. The numbers are correct; the direction is easy to misread for a
  pull-down. Since D88 (unreleased) such a condition is a pull-down's
  *background*: on this matrix it is read from its name and the data, the
  comparisons become `<compound> vs DMSO`, `DMSO vs EV` and `<compound> vs EV` (153
  proteins up in `DMSO vs EV`), and the EV samples are left out of the
  global filter and normalised apart.
- **Leaving the empty-vector runs out failed a stage.** Re-run with
  `exclude_samples: [EV_1, EV_2, EV_3]`, the run-order stage stopped with
  `KeyError: 'EV_2'`. The manifest's map of samples to runs still held the
  samples left out. Fixed (D84).

### Why FragPipe-Analyst showed a protein Ionomos didn't

The same `report.pg_matrix.tsv` was run on fragpipe-analyst.org (vehicle vs
compound; its volcano has 21 hits). The compound-vs-vehicle comparison in
Ionomos had 17. The protein that caught the eye there was measured in all
three compound replicates and in none of the six other samples.

**Cause: the missing-value filter.** This Ionomos run asked for values in
≥ 66 % of *all* samples, which is 6 of 9. A protein seen only in the three
compound runs has 3 of 9, so it was removed before testing (one of the 154
the filter removed). The web app's default has no filter. There, the six
missing values were imputed (Perseus-type, from the low end of each sample),
and the protein came out at log2FC ≈ 2.9, q ≈ 1e-4: a strong hit whose size
is set by the imputed values, not by a measurement. It is an on/off
detection, best read as "seen only with the compound".

What was checked:

1. **Ionomos is deterministic on this data.** The run's
   `protein_matrix_log2.tsv` was rebuilt into a pg_matrix and re-analysed
   with the run's own settings. The result was identical: 2,040 tested, and
   the same hits in both comparisons.
2. **FragPipe-Analyst's code.** The web app (MonashProteomics/FragPipe-Analyst)
   and FragPipeAnalystR (Nesvilab) were read as of 2026-10-09. With equal
   settings, every step matches the port:
   - the two filters (the web app's global filter keeps rows with at most
     100 − x % missing, which is the same rule);
   - median centring (`sweep` by column medians);
   - Perseus imputation with `set.seed(123)`, drawn per sample in dplyr's
     sorted `group_by` order, as `fpa.impute` does;
   - `test_limma` and `add_rejections`.

   One difference that did not matter here: the web app drops rows with
   "contam" in `Protein.Group`, but FragPipeAnalystR's `make_se_from_files`
   only does that for LFQ, not DIA. No row in this matrix had the prefix.
3. **Re-run with the web's filter.** Ionomos with no missing-value filter
   and median centring gives 20 hits; 19 are among FragPipe-Analyst's 21,
   including that protein. The other three sit on the cut-offs (q 0.051 and
   0.055 against 0.05; a log2FC of 1.00 against 1).
   Where values are imputed, the random draws depend on the sample names
   (they set the order the samples are drawn in), and the web session's
   names were not kept. The web's own normalisation default ("No
   normalization") fits less well (17 shared, 2 extra, 4 missing), so the
   session most likely used "Median centered".

**Conclusion: a settings difference (the missing-value filter), not an
error in the port.** A global filter of 66 % with three conditions of three
removes everything seen in only one condition, which in a pull-down or a
treatment is often the interesting part. The experiment's owner
(2026-10-09) explained that the three empty-vector runs are the negative
control of the FLAG IP, there to show the bait is enriched; they see little
beyond what sticks to the beads, so many proteins are *expected* to be
missing from them, and they would not use a global filter on this design. Ionomos' own default (0 % of all
samples, 50 % of one condition) keeps it. It is then tested (q 0.10 with
median centring: not a hit, because the imputed values vary) and listed
under "only in one condition". **Also a gap in the
report:** Ionomos' "only in one condition" table was made after the filter,
so a protein the filter removed for being in one condition only was not
listed there either. Fixed (D84): the table is made before the filter and
marks what the filter removed, and the doctor warns when a global filter
removes features that a condition had in every sample.

To reproduce a FragPipe-Analyst web session, press **Use FragPipe-Analyst's
defaults** in the Analysis tab, set the control, and match the web's
normalisation choice. **Not verified** for this experiment:
FragPipe-Analyst's own table. Its volcano's labels were compared, not its
numbers (a results table was compared for a TMT experiment on 2026-10-28,
[below](#the-tmt-result-against-fragpipe-analyst)). Next time, download its
results table, note its settings, and run `ionomos compare <experiment>
<table>` ([VALIDATION.md](VALIDATION.md)).

**A test gap this showed.** The end-to-end FragPipeAnalystR golden
(`tests/golden/fpa/e2e/`) has its samples in sorted order, one accession per
protein group and unique gene names. A real matrix has interleaved run
columns, a gene name used twice (`ALB`) and a group without a gene. The
port handles each of these, but no golden pins them.

## 2026-10: TMT, DIA and DDA searches through the watcher

Reported by the maintainer on 2026-10-28: TMT, DIA and DDA searches have
run on the PC through the watcher (FragPipe headless, `fragpipe.bat
--headless`) and finished. Neither the version (somewhere from 0.19.0 to
0.21.0) nor the dates or experiments were recorded. These are the first TMT
and DDA searches through Ionomos, and the first searches on the D59 runner
(0.14.0 and later) reported to have finished.

**The run records (`run_fingerprint.json`, console logs) are still to be
filed here.** Until they are, this is a report, not evidence: this page
cannot say which version, workflow or steps ran. To file, for each search:

- `ionomos_run\run_fingerprint.json`: the Ionomos version, the command
  line, the output files and their sizes;
- FragPipe's version line (in the fingerprint's version block, or the top
  of `ionomos_run\fragpipe_console.log`);
- the step list (`N commands to execute:` and each
  `Process '…' finished, exit code: …` line) and whether the console ends
  with `ALL JOBS DONE` ([FIRST_REAL_RUN.md](FIRST_REAL_RUN.md), "Report
  back");
- for the DDA search, whether it used the isoDTB workflow. Until that is
  known, no isoDTB search through Ionomos is recorded;
- for the TMT search, what [WORKFLOWS.md](WORKFLOWS.md)'s TMT section says
  the first real TMT run must confirm (the `psm.tsv` header, that FragPipe
  found Ionomos' `annotation.txt`, the labelling and purity numbers).

## 2026-10-28: real isoDTB, TMT and DIA results analysed, and a TMT result compared with FragPipe-Analyst

Ionomos 0.21.0 with its default settings analysed three real FragPipe
results of the lab, read from the owners' own FragPipe output folders (not
through the watcher). For comparison, the published CPTAC ccRCC data went
through the same version (`ionomos example --dataset tmt --dataset dia`,
[EXAMPLE.md](EXAMPLE.md)).

- **isoDTB**: a competition experiment, 3 replicates, one compound at one
  dose and time. 12,440 cysteine sites tested. The doctor raised
  `RATIO_OFFSET`: the replicates' ratios sit off 0 by +0.07, +0.19 and
  −0.09 log2 on the stable sites, the mark of a heavy / light mixing error.
  That is the doctor doing its job: `ratio_centre` is `none` by default and
  the lab decides whether to centre (D70; WORKFLOWS.md, "isoDTB: centring
  the ratios").
- **TMT**: compound vs vehicle, 3 vs 3, one plex. 5,639 genes. The doctor
  warned that the second principal component follows the replicate number,
  a batch effect by replicate. Also the doctor doing its job: the check is
  there to show such an effect before the hits are read.
- **DIA**: knockdown vs wild type, 4 vs 3. 5,262 proteins. Report made.

### The TMT result against FragPipe-Analyst

The owner of the TMT experiment had run the same search's results through
FragPipe-Analyst and kept its results table. Compared by hand with the
Ionomos result (not through `ionomos compare`, as far as is recorded):

- **log2 fold changes**: the same, Pearson r = 1.000. FragPipe-Analyst's
  sign convention is the reverse of Ionomos'.
- **Hits**: FragPipe-Analyst has 143; 134 of them are hits in Ionomos too.
  2 hits are in Ionomos only.

**What this shows.** It is the first check of the TMT path on real data and
the first numeric comparison with a FragPipe-Analyst results table (on
2026-10-06 only the web volcano's labels were kept). On this experiment,
Ionomos' defaults give FragPipe-Analyst's fold changes and nearly the same
hit list.

**What it does not show.** The 9 FragPipe-Analyst hits that are not hits in
Ionomos were not examined one by one, so why they differ is not known. It
is one experiment with one plex. It was not run through `ionomos compare`,
so there is no verdict on file and no report shows it. Next: run
`ionomos compare <experiment> <table>` on the same table
([VALIDATION.md](VALIDATION.md)) and look at the 9.

## Open

| Seen | Problem | Next step |
|---|---|---|
| 2026-09-16 | A `config.yaml` that is not UTF-8 (Notepad saving as ANSI turns `—` into byte 0x97) still stops Ionomos 0.18.1 with a bare `UnicodeDecodeError`, not a `ConfigError` saying what to do | **Fixed, unreleased (D86)**: read as cp1252 with a warning naming the file, byte and line; `experiment.yaml` too. Confirm the warning on the PC, then remove this row |
| 2026-10-06 | A global filter removes what one condition alone has, before testing | Fixed in D84: listed (marked) under "only in one condition", and the `FILTER_REMOVES_ONE_CONDITION` warning. Open: should the Analysis tab caution before the run? |
| Job 1 | Two runs missing from DIA-NN's matrix | Explained by FragPipe's source (a bioreplicate beyond Java's int drops the manifest line). Read job 1's console log for the "badly formatted lines" ERROR line, then remove this row |
| 2026-10-06 | FragPipe's `contam_` prefix is lost in DIA-NN's tables, so contaminants stay and BSA reads as `ALB` | Fixed in code (D87): the FASTA's `contam_` accessions are removed. Re-analyse the 2026-10-06 folder on the next release; "contaminants" should show −2 or more (P02769, P00761) |
| 2026-10-06 | An empty-vector control next to the vehicle: bait-enriched proteins read as "down" | Done in D88 (unreleased): the `background` role; the lab confirms its words and thresholds (ROADMAP) |
| 2026-10-06 | FragPipeAnalystR golden doesn't cover a real matrix's shape | Add a case with interleaved columns, a duplicated gene and a blank gene |
| Job 2 | How it ended | Its `ionomos.json` / `DONE.txt`, `run_fingerprint.json` |
| 2026-10-28 | The October TMT, DIA and DDA searches are reported, not recorded | File each search's `run_fingerprint.json`, FragPipe's version line and step list, and whether the DDA search used the isoDTB workflow ([above](#2026-10-tmt-dia-and-dda-searches-through-the-watcher)) |
| 2026-10-28 | 9 of FragPipe-Analyst's 143 TMT hits are not hits in Ionomos; not examined | Run `ionomos compare` on the owner's table and look at the 9 |
