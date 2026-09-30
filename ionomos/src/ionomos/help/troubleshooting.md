# Troubleshooting {#trouble}

Find the message you see, then what to do. Ionomos never deletes your data
while it waits for you: see [What Ionomos will never do](#safety).

## Nothing happens after a drop {#trouble.nothing}

- Is the watcher running? The app's Run & Test tab (tab 5) shows its status.
- The folder needs at least one `.raw` file and must stop changing for about
  a minute. A copy that is still going is not taken in yet.
- A review window may be waiting for you, perhaps behind another window.
- Look for `<folder>.REJECTED.txt` next to the folder in the inbox.

## The watcher says NOT RESPONDING, or "already running" {#trouble.watcher}

**NOT RESPONDING** (Run & Test tab): press **Stop watcher**, then **Start
watcher**, and send **Report a problem…** so it can be fixed. **"another
ionomos watcher is already running"**: the startup task already runs one.
That is fine; use the app to see it.

## An analysis needs your decision {#attention.analysis_input}

The analysis ran, but on a guess that a person should confirm: which samples
belong together, which condition is the control, a run that didn't match its
sample, a sample that looks like a failed injection. The window shows the
experiment editor: fix the conditions (**Guess from names** helps), leave a run
out, pick the control, then **Run analysis**. The answer is saved in the
experiment's `experiment.yaml`, and the window closes when the new analysis
has nothing left to ask. The issue list names what to decide; each issue is
explained under [Analysis issues](#trouble.issues).

## The analysis had a problem {#attention.analysis_failed}

The search finished, but the statistics could not be made or are incomplete:
no result table, a step crashed, no volcano plot. The window lists the likely
causes. Often FragPipe stopped before quantification: **FragPipe log** shows
the last step. Fix the cause, then **Re-run analysis**. If it fails again,
**Report a problem…**. Each issue is explained under
[Analysis issues](#trouble.issues).

## A search failed {#attention.search_failed}

FragPipe (or DIA-NN) stopped with an error. `FAILED.txt` in the experiment
folder and the window give the reason and the most likely cause in plain
words (see [Why a search fails](#search.failed)). The end of the search log
is in the window; the full log is `ionomos_run\fragpipe_console.log`.

**What to do**: fix the cause, then **Retry search** (or app → Jobs tab →
**Retry**). The earlier attempt's output is kept as
`fragpipe_previous_<time>\`, never deleted.

## A search is waiting {#attention.search_waiting}

The job is queued but held ("waiting: …") because something outside the
experiment is missing: FragPipe itself, the method's workflow or FASTA, or
free disk space. Nothing is lost and nothing needs retrying: fix what the
message names and the search starts by itself. The app's ✓ Setup tab shows
the same gap. The messages are explained below:
[launcher](#search.hold-launcher), [workflow](#search.hold-workflow),
[FASTA](#search.hold-fasta), [disk space](#search.hold-disk),
[DIA-NN](#search.hold-diann), [spectral library](#search.hold-library),
[MaxQuant](#search.hold-maxquant), [MaxQuant parameters](#search.hold-mqpar),
[method](#search.hold-method).

## A folder was not taken in {#attention.intake_rejected}

A dropped folder could not be filed, so it stays in the inbox, untouched, with
a `<folder>.REJECTED.txt` note that says why. Rename the folder or the files,
or add an `experiment.yaml`; Ionomos tries again by itself. Deleting the note
also makes it try again. The reasons are explained under
[Why a folder is not taken in](#trouble.intake).

## The instrument QC standard looks off {#attention.qc_trend}

Your lab's recurring QC standard (a HeLa or K562 run, for example) came
out outside its usual range. Ionomos compares every QC-standard run with the
first runs of the same series (same method, standard and amount), using
control-chart rules. The item names the metric and how far it moved, e.g.
"IDs 18% below baseline" or "MS1 mass error drifting".

This is about the instrument, not your experiment. Nothing was changed or
stopped. What to do:
- Open the trend page: app → **Jobs** → **Instrument QC**, or
  `ionomos qc-trend --open`. One bad point can be a failed injection; a run
  of points drifting the same way points to the column, the spray or the
  calibration.
- Tell whoever looks after the instrument, and check the run's raw file
  (TIC, spray) before trusting experiments acquired since.
- The item closes by itself when a later QC run is back in range.

More about what is measured: docs/QC_TREND.md.

## Why a folder is not taken in {#trouble.intake}

The note names one of the reasons below. When the watcher can show a window,
it asks instead of rejecting for the first four.

## No known user {#intake.user}

The name has no initials Ionomos knows, or two people's. Put your initials
(or your folder name under `C:\Fragpipe_General`) in the folder name, or pick
yourself in the window and tick "Remember" so your initials work next time.

## No method, or two methods {#intake.method}

The folder name (and the file names) must contain exactly one of `isoDTB`,
`TMT` or `DIA` (or another keyword set up for a method). Rename the folder,
or pick the method in the window.

## A file name can't be read {#intake.raws}

The end of a `.raw` name doesn't fit the method's rule, for example an isoDTB
file without `_<replicate>_<fraction>`. Rename the file, or type its replicate
and fraction in the window. `ionomos names test <file.raw>` shows how a name
is read. See [Name the raw files](#start.files).

## Uneven fractions or duplicate files {#intake.layout}

The replicates don't have the same fractions (for example replicate 3 has no
fraction 7), usually because a copy was incomplete. Or two files end up as the
same sample and replicate (a re-acquired run next to the original). Copy the
missing files, remove the extra one, or confirm in the window ("accept uneven
fractions"). Raw files both at the top level and in `raw\` are also refused:
keep them in one place.

## No raw files {#intake.no_raws}

The folder has no `.raw` files at the top level or in a `raw\` subfolder. Such
a folder is simply left alone; add the raw files.

## The experiment already exists {#intake.dest}

A folder with this name was already filed for this user. Ionomos never
overwrites, so it stops. Rename the new folder, for example by adding `_redo`.

## experiment.yaml has a mistake {#intake.overrides}

The folder's `experiment.yaml` could not be read (a typo in the layout, or a
value that doesn't exist, such as a workflow file that isn't there). The note
names the line. Fix or remove the file.

## Something unexpected {#intake.other}

Anything else, including a problem inside Ionomos. The folder was left
untouched. Send **Report a problem…** from the app so it can be fixed.

## Why a search is held {#search.waiting}

A held search waits in the queue and starts by itself once the cause is
fixed. See [A search is waiting](#attention.search_waiting).

## FragPipe launcher not found {#search.hold-launcher}

Ionomos can't find FragPipe. App → tab 1 Folders → **Find FragPipe**, or set
`fragpipe_exe` in the settings. FragPipe must not be under a path with spaces.

## Workflow file missing {#search.hold-workflow}

The method's FragPipe workflow isn't in the workflows folder. App → tab 3
Methods → **Import workflow…** and pick a `.workflow` file (for example the
`fragpipe.workflow` inside a search that worked).

## FASTA missing {#search.hold-fasta}

The method's protein database isn't set or was moved. App → tab 3 Methods: pick
the FASTA. It must contain decoys (`rev_` entries); **Check FragPipe install**
on the Jobs tab checks that.

## Low disk space {#search.hold-disk}

There isn't enough free space for FragPipe's output. Free space on the drive
that holds the experiment folders (move old experiments to the archive drive).
The search starts once there is room.

## DIA-NN not found {#search.hold-diann}

A method set to run DIA-NN directly can't find it. Install DIA-NN (your lab's
own licence) and set `methods.<method>.diann_exe` in `config.yaml`.

## Spectral library missing {#search.hold-library}

The method asks for a spectral library that isn't in the workflows or FASTA
folder. Put the library there, or remove it from the method.

## MaxQuant not found {#search.hold-maxquant}

The method is set to run MaxQuant (`engine: maxquant`) but its
`MaxQuantCmd.exe` isn't where the settings say. Install your lab's MaxQuant
and set the method's `maxquant_exe` to its `bin\MaxQuantCmd.exe`. The search
starts by itself once it is found.

## MaxQuant parameter file missing {#search.hold-mqpar}

The method names a lab MaxQuant parameter file (`mqpar:`) that isn't in the
workflow folder. In the MaxQuant GUI, set up the search you normally use,
then **File → Save parameters**, and put the file in the workflow folder
under that name. Or remove `mqpar:` to use MaxQuant's defaults with
label-free quantification.

## The method is no longer set up {#search.hold-method}

The experiment's method was removed from the settings after it was queued.
Add the method back (tab 3 Methods), or change the experiment's method.

## Why a search fails {#search.failed}

The most common causes, with what to do, are listed in `FAILED.txt` and the
pop-up. After fixing the cause, press **Retry**; the earlier output is kept.

- **Timed out**: the search ran longer than the time limit
  (`fragpipe.timeout_minutes`). Raise it for big experiments, then Retry.
- **Cancelled by user**: someone pressed Cancel. Retry to run it after all.
- **Interrupted (Ionomos stopped)**: the watcher was stopped or the PC
  restarted during the search. Nothing to do: it runs again from the start.
- **Raw files missing or empty (0 bytes)**: a file was moved after filing,
  or an acquisition was aborted. Put it back or copy it again.
- **experiment.yaml asks for a workflow or FASTA that isn't there**: fix the
  name in the experiment's `experiment.yaml`, or put the file in the
  workflows / FASTA folder.
- **A path has a space in it** (DIA-NN): DIA-NN can't take spaces in paths.
  Rename the folder or file.
- **The experiment folder is gone**: it was moved or renamed after filing.
  Put it back where it was.

## Analysis issues {#trouble.issues}

Every analysis ends with a check-up. Its findings appear at the top of the
report, and the ones that need a person also pop up on the PC. **Problem**
means something is missing or broken; **Needs your decision** means the
analysis ran on a guess; **Worth knowing** is advice.

## The results folder can't be written {#issue.NO_RESULTS_FOLDER}

Ionomos could not create or write `results\`. The disk may be full, or the
folder is read-only or open in another program. Free space or close the
program, then **Re-run analysis**.

## A step of the analysis failed {#issue.CRASH}

One stage (reading, statistics, QC, enrichment, an export, the report)
stopped with an error. The rest of the analysis still ran, and the report
says what is missing. Usually an unexpected table layout; sometimes a bug in
Ionomos. **Re-run analysis** once; if it fails again, **Report a problem…**
(the details are in `results\analysis_error.txt`).

## The result table has nothing Ionomos can use {#issue.UNUSABLE_TABLE}

The table exists but holds nothing to analyse. For isoDTB this usually means
the label mass in the workflow differs from the one Ionomos looks for, or the
search found no labelled peptides. For a table you gave it, it isn't one row
per protein with a numeric column per sample. Check the workflow (tab 3) and
Retry, or export the table again.

## No result table {#issue.NO_TABLE}

Ionomos looked for the table the method produces (for DIA a
`pg_matrix.tsv`, for isoDTB `combined_modified_peptide_label_quant.tsv`) and
didn't find it. Usually quantification was off in the workflow, or FragPipe
stopped before it. Open the FragPipe log (Jobs tab) and look at the last
steps; check the method's workflow has quantification on, then Retry. If the
method is wrong, pick the right one and **Re-run analysis**.

## The result table is empty {#issue.EMPTY_TABLE}

The search found nothing that passed its filters. Check the FASTA is the right
species, the raw files are real acquisitions, and the workflow's settings
(enzyme, modifications) fit the experiment. Then Retry.

## The result table has no usable numbers {#issue.NO_QUANTITIES}

The table lists proteins but every sample value is blank or zero:
quantification failed or was switched off, or the table was edited in another
program. Check the table in `fragpipe\` and the workflow, then Retry.

## Every sample or feature was removed {#issue.NOTHING_LEFT}

After leaving samples out and filtering missing values, nothing was left. Check
"Samples left out" and the missing-value filter in the experiment editor, then
**Run analysis**.

## Only one sample {#issue.ONE_SAMPLE}

Only one sample was quantified, so there is nothing to compare. The other runs
failed or are missing from the table: look for them in the FragPipe log, and
re-acquire or re-search them.

## Runs with no quantities {#issue.MISSING_RUNS}

Some runs were searched but are missing from the result table (a failed or
very short acquisition, too few identifications, or a run renamed after the
search). They are not in the statistics. The rest of the analysis is still
valid without them; re-acquire the run or leave it out.

## Runs that couldn't be matched to their samples {#issue.UNMATCHED_RUNS}

The result table names runs that weren't in the experiment's file list, so
their condition was guessed from the name. This happens when files are renamed
after filing. Check each sample's condition in the window, then **Run
analysis**.

## Two runs have the same sample name {#issue.DUPLICATE_SAMPLES}

Two files look like the same sample (a re-acquisition next to the original),
and both count as replicates, which inflates the replicate number. Leave out
the run you don't want, or give it the right condition, then **Run
analysis**.

## Every sample is in the same condition {#issue.ONE_CONDITION}

All samples were read as one condition, so there is nothing to compare. The
file names probably don't carry the condition where Ionomos reads it (`DMSO_1`,
`Drug_2`). Give each sample its condition in the window (**Guess from names**
suggests them), then **Run analysis**. See [How do I change a sample's
condition?](#faq.condition)

## Every sample is its own condition {#issue.EACH_OWN_CONDITION}

The names carry no replicate numbers Ionomos recognises (replicates marked
with letters or words, like `DMSO_a`), so each sample became its own condition
and nothing has statistics. Check the suggested conditions in the window and
**Run analysis**: replicates give real statistics.

## The chosen comparisons don't fit {#issue.BAD_COMPARISON}

A comparison you chose names a condition that isn't in the data (renamed, or a
typo). Ionomos made the default comparisons instead. Pick the comparisons again
in the experiment editor and **Run analysis**.

## Which condition is the control? {#issue.NO_CONTROL}

No condition looks like a control (DMSO, vehicle, WT, …), so Ionomos used its
best guess. Every volcano plot is "condition vs control", so please confirm:
pick the control in the window and **Run analysis**. The choice is remembered
for the experiment. A control name the lab always uses can be added to the
control keywords (tab 7 Analysis → Lab defaults).

## A sample has far fewer identifications {#issue.LOW_SAMPLE}

One sample has less than 40% of the typical number of identifications: a
failed injection or a lost sample. It distorts the statistics. Leave it out in
the window and **Run analysis**; you can always add it back. See
[How do I leave a sample out?](#faq.leave-out)

## Many values were imputed {#issue.HIGH_IMPUTATION}

More than 40% of the numbers in the statistics are
[imputed](#glossary.imputation), not measured, so fold changes of
low-abundance proteins are unreliable. Common with very different samples
(pulldown vs input). Consider imputation "none" or a stricter missing-value
filter for this experiment.

## Not enough replicates to test {#issue.SMALL_GROUP}

A group in this comparison has too few samples for a test, so the comparison
has no p-values. Often a replicate's condition was mistyped and it formed its
own group. Fix the conditions in the window and **Run analysis**.

## Few features in the analysis {#issue.FEW_FEATURES}

Fewer than 100 features passed the filters, so the statistics and the
enrichment are weak. Check the identifications per sample in the QC section; a
small sample amount, a short gradient or a strict filter are the usual causes.

## Samples look like outliers {#issue.SAMPLE_OUTLIER}

The [sample scorecard](#qc.card) flagged these samples on two or more
measures. An outlier replicate hides real changes and can create false ones.
Look at it in the scorecard and the [PCA](#qc.pca). If it is a technical
failure, leave it out and **Run analysis** (you can add it back). If the
sample really is different, keep it. A swapped sample sits with the other
condition in the PCA.

## Samples group by replicate number {#issue.BATCH_SUSPECT}

A main [PCA](#qc.pca) component follows the replicate number more than the
condition: replicates with the same number were probably prepared or run
together, and that [batch](#glossary.batch) shows. If every condition has all
replicate numbers, the comparisons are still valid but less sensitive. Next
time, randomise the preparation and run order.

## Missing values look random but were imputed as low {#issue.IMPUTATION_MISMATCH}

The missing values don't depend on abundance (see
[Missing vs intensity](#qc.mnar)), yet they were filled in as if they were below
detection, which creates fold changes. Try imputation "knn" or "none" for this
experiment and compare the hits ([How?](#faq.rerun)).

## The p-value histogram looks unusual {#issue.P_VALUE_SHAPE}

The p-values pile up near 1 or bulge in the middle instead of being flat with
a peak near 0, so they (and the adjusted ones) may not mean what they say.
Common causes: many tied imputed values, an outlier sample or hidden batch, or
a mislabelled condition. Check the [scorecard](#qc.card) and
[PCA](#qc.pca); try a stricter missing-value filter. See
[p-value distribution](#report.phist).

## Hits rest on imputed values {#issue.IMPUTATION_DRIVEN}

For at least 30% of this comparison's hits, half or more of one group's values
were imputed, so the fold change comes partly from the imputation. Many are
real on/off changes (see [Only in one condition](#report.onoff)). In the
report, tick **ignore imputation-driven hits** (Options → Hits) to see the hits
that stand on measured values.

## Low confidence (a group has one sample) {#issue.LOW_CONFIDENCE}

The comparison was tested, but a group has only one sample, so the noise
estimate comes from the other groups. If replicates exist, fix the conditions
and **Run analysis**; otherwise confirm the hits in another experiment. See
[Low confidence and fold change only](#report.confidence).

## Fold change only {#issue.FOLD_CHANGE_ONLY}

No group in this comparison has replicates, and there are none elsewhere to
borrow from, so no p-values are possible. The plot ranks features by fold
change. Add replicates for statistics.

## Nothing could be tested {#issue.ZERO_TESTED}

Not one feature had enough values in both groups, so the volcano plot is
empty. Usually most values are missing in one group with imputation off, the
missing-value filter removed everything, or samples are in the wrong
conditions. Check the conditions, try imputation "auto", and **Run analysis**.

## No significant changes {#issue.NO_HITS}

Features were tested and none passed the cut-offs. The volcano plot is still
made. There may be no real difference at this depth and replicate number, or
one noisy replicate hides it (check the PCA). Explore looser cut-offs in the
report; they update live. See [Why are there no hits?](#faq.no-hits)

## The volcano plot wasn't written {#issue.NO_VOLCANO}

The statistics exist, but the plot file is missing: the results folder is
read-only or the disk is full. Check free space and that `results\` can be
written, then **Re-run analysis**.

## Enrichment was incomplete {#issue.ENRICHMENT}

Some gene-set libraries could not be loaded, usually because there was no
internet the first time a library was needed. **Re-run analysis** when the PC
is online; after that it works offline.
