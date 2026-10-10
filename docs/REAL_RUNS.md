# Real runs on the lab PC

What has actually run on the proteomics PC, what went wrong, and what each
problem became. [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md) is the checklist for
a run; this page is the record of the runs. Add a row (and a section if
something was learned) after every real search or analysis that tells us
something new.

The evidence is the "Report a problem" bundles (`Ionomos-report-<date>-v<x>.zip`),
the experiment's `ionomos.json`, `experiment.yaml` and `results/`, and the
maintainer's notes. The repository is public: protein names and hit lists
from unpublished experiments stay out of this page.

## The runs

| Date | Version | What ran | Outcome |
|---|---|---|---|
| 2026-09-16 | LabWatch 0.1.0 | `labwatch status --all` on the PC's testbed config | Crashed: `UnicodeDecodeError … byte 0x97`. [Open](#open) |
| 2026-09-23 14:23 | 0.5.0 | First install, setup checklist | No DIA or isoDTB workflow pinned yet. FragPipe copies, `Fasta-files`, `New folder` and `QC` were listed as users (fixed in 0.5.1) |
| 2026-09-23 15:05 | 0.5.1 | Chris's 7 `.raw` files dropped loose in the inbox | Ignored (fixed in 0.5.2, D23) |
| 2026-09-23 15:16 | 0.5.1 | **Job 1**, `CS_22rv1_FLAG_AR_MA25`, DIA, 6 raws | **First real FragPipe search.** Exit 0 after 14 min. DIA-NN's matrix has 4 of the 6 runs; the report had no volcano. [Job 1](#job-1-the-first-real-search) |
| 2026-09-23 16:40 | 0.5.3 | **Job 2**, the same raws dropped loose again, grouped by Ionomos | Searching when the 16:45 bundle was taken; how it ended was not reported. [Job 2](#job-2-loose-files-grouped) |
| 2026-10-06 | 0.18.0 | First drop on the app's Analysis tab | The app froze (fixed in 0.18.1, D82) |
| 2026-10-06 | 0.18.0 | Analysis tab on a FragPipe GUI result (Sheena, a FLAG pull-down, DIA, 9 runs) | Report made. Compared with FragPipe-Analyst: a protein seen in one condition only was a hit there and filtered out here (the missing-value filter). [The FragPipe-Analyst comparison](#2026-10-06-a-fragpipe-gui-result-analysed-and-compared-with-fragpipe-analyst) |

## Job 1: the first real search

2026-09-23, Ionomos 0.5.1, FragPipe 24.0 through `bin\fragpipe.bat
--headless`.

- The folder name had no method keyword, so the resolver window opened. The
  first answer was "skip" (`REJECTED`). After a restart, DIA was chosen and
  the job queued. The `Chris` user folder was made from the window.
- The inbox first held 7 files: `…_DMSO_1.raw` and
  `…_DMSO_1_20260508180610.raw` were the same sample. One was removed before
  the job queued.
- **The Xcalibur re-acquisition timestamp was read as the replicate.**
  `experiment.yaml` and the FragPipe manifest got bioreplicate
  `20260508180610` and `20260508204737` for DMSO 1 and 2. Since 0.5.2 the
  timestamp is ignored (D23), and since 0.8.0 replicate tails are 1–999.
  Current naming reads these files as DMSO 1, 2, 3 and MA25 1, 2, 3.
- **DIA-NN's `report.pg_matrix.tsv` has 4 run columns, not 6.** The two
  missing runs are exactly the two with the 14-digit replicate number. That
  number does not fit a 32-bit integer. **Not confirmed**: the job's
  `ionomos_run\fragpipe_console.log`
  (`C:\Fragpipe_Auto_Users\Chris\CS_22rv1_FLAG_AR_MA25\`) should say what
  FragPipe did with those two lines of the manifest. Job 2 (replicates 1 and
  2) passed all six raws to MSFragger.
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
`CS_22rv1_FLAG-AR_MA25-10uM` (D23). The folder name had no method keyword, so
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

Sheena searched a FLAG pull-down (DIA, 9 runs: vehicle, compound,
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
  FragPipe-Analyst uses the same rule and keeps them too. [Open](#open)
- The empty-vector condition was taken as a control by its name (`EV` is a
  control keyword), with the vehicle as *the* control. So the comparison is
  "empty vector vs vehicle", and proteins enriched by the bait are listed as
  *down*. The numbers are correct; the direction is easy to misread for a
  pull-down. [Open](#open)

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
treatment is often the interesting part. Sheena, 2026-10-09: the three
empty-vector runs are the negative control of the FLAG IP, there to show
the bait is enriched; they see little beyond what sticks to the beads, so
many proteins are *expected* to be missing from them, and she would not
use a global filter on this design. Ionomos' own default (0 % of all
samples, 50 % of one condition) keeps it. It is then tested (q 0.10 with
median centring: not a hit, because the imputed values vary) and listed
under "only in one condition". **Also a gap in the
report:** Ionomos' "only in one condition" table is made after the filter,
so a protein the filter removes for being in one condition only is not
listed there either. [Open](#open)

To reproduce a FragPipe-Analyst web session, press **Use FragPipe-Analyst's
defaults** in the Analysis tab, set the control, and match the web's
normalisation choice. **Not verified**: FragPipe-Analyst's own table. Its
volcano's labels were compared, not its numbers. Next time, download its
results table, note its settings, and run `ionomos compare <experiment>
<table>` ([VALIDATION.md](VALIDATION.md)).

**A test gap this showed.** The end-to-end FragPipeAnalystR golden
(`tests/golden/fpa/e2e/`) has its samples in sorted order, one accession per
protein group and unique gene names. A real matrix has interleaved run
columns, a gene name used twice (`ALB`) and a group without a gene. The
port handles each of these, but no golden pins them.

## Open

| Seen | Problem | Next step |
|---|---|---|
| 2026-09-16 | A `config.yaml` that is not UTF-8 (Notepad saving as ANSI turns `—` into byte 0x97) still stops Ionomos 0.18.1 with a bare `UnicodeDecodeError`, not a `ConfigError` saying what to do | Read cp1252 when UTF-8 fails, or say which line and how to save it |
| 2026-10-06 | A protein measured in only one condition is removed by a global missing-value filter and is then missing from the "only in one condition" table too | Build that table before the filter; warn when a global filter of x % removes features complete in one condition |
| 2026-10-06 | Leaving samples out (`exclude_samples`) makes the run-order stage fail (`KeyError` on a left-out sample) | Fix `run_order` to use the chosen samples |
| Job 1 | Two runs missing from DIA-NN's matrix | Read job 1's console log for the two DMSO files |
| Job 1 | An explicit `bioreplicate:` in `experiment.yaml` is passed to FragPipe unchecked (14 digits got through on 0.5.1) | Hold the job or refuse the value, as the naming rules do (1–999) |
| 2026-10-06 | FragPipe's `contam_` prefix is lost in DIA-NN's tables, so contaminants stay and BSA reads as `ALB` | Take the contaminant accessions from the FASTA FragPipe used (or `protein.tsv`) |
| 2026-10-06 | An empty-vector control next to the vehicle: bait-enriched proteins read as "down" | Decide how a pull-down's background control is shown (ROADMAP) |
| 2026-10-06 | FragPipeAnalystR golden doesn't cover a real matrix's shape | Add a case with interleaved columns, a duplicated gene and a blank gene |
| Job 2 | How it ended | Its `ionomos.json` / `DONE.txt`, `run_fingerprint.json` |
