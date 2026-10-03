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

## Report a problem: the zip was not saved, or files were left out {#trouble.bundle}

**Report a problem…** saves a zip on the Desktop
([how](#faq.bundle), [what is in it](#safety.bundle)).

- **"LEAK CHECK FAILED"**: after replacing the names, Ionomos searched the
  zip and still found one, so it saved nothing. The message names the file
  and the text. Your data is fine; this is a fault in Ionomos. Report the
  message. Until it is fixed, a zip of fewer jobs or without the result
  tables may pass the check.
- **Files were left out**: the window and `README.txt` in the zip list them
  with the reason. A zip has a size limit (2,000 MB before compression;
  `ionomos bundle --max-mb` raises it). A PSM table above 25 MB is cut down
  to every n-th row. A picture or another file that is not text is left out
  when names are replaced, because the names in it can't be.
- **It was saved in the log folder, not on the Desktop**: there was no
  Desktop folder to write to. The message gives the place.
- **A name is still readable in the zip**: see the list of what the replacing
  cannot do in [The zip for troubleshooting](#safety.bundle), and delete the
  zip if it should not be sent.

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
[the wrong launcher](#search.hold-launcher-window),
[FASTA](#search.hold-fasta), [decoys](#search.hold-decoys), [disk space](#search.hold-disk),
[DIA-NN](#search.hold-diann), [spectral library](#search.hold-library),
[MaxQuant](#search.hold-maxquant), [MaxQuant parameters](#search.hold-mqpar),
[Sage](#search.hold-sage), [raw file converter](#search.hold-converter),
[Sage settings](#search.hold-sage-config),
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

The folder has no `.raw` files at the top level, in a `raw\` subfolder, or one
folder down (a folder per TMT plex, `<plex>\*.raw`). Such a folder is simply
left alone; add the raw files.

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

## The launcher is FragPipe's window program {#search.hold-launcher-window}

The launcher set on tab 1 is FragPipe's `.exe` (for example
`bin\FragPipe-24.0.exe`), and there is no `fragpipe.bat` next to it. The
`.exe` opens FragPipe's window and returns at once, so Ionomos could not
follow a search started with it. FragPipe 23 and 24 install `fragpipe.bat`
in the same `bin` folder; Ionomos uses it by itself when it is there. If it
is missing, reinstall FragPipe, then tab 1 → **Find FragPipe**.

## Workflow file missing {#search.hold-workflow}

The method's FragPipe workflow isn't in the workflows folder. App → tab 3
Methods → **Import workflow…** and pick a `.workflow` file (for example the
`fragpipe.workflow` inside a search that worked).

## FASTA missing {#search.hold-fasta}

The method's protein database isn't set or was moved. App → tab 3 Methods: pick
the FASTA. It must contain decoys (`rev_` entries); **Check FragPipe install**
on the Jobs tab checks that (see [decoys](#search.hold-decoys)).

## The FASTA has no usable decoys {#search.hold-decoys}

FragPipe run without its window stops at once when the protein database has
no decoys, or when decoys are not about half of its entries (40-60 %). A
decoy is an entry whose name starts with the workflow's decoy tag, usually
`rev_`. In FragPipe's window: Database tab → **Add decoys** (once, on a
FASTA without any), copy the new file into the FASTA folder and pick it for
the method on tab 3. The search then starts by itself. The usual mistakes
are a FASTA straight from UniProt (no decoys) and decoys added twice.

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

## Sage not found {#search.hold-sage}

The method is set to run Sage (`engine: sage`) but `sage.exe` isn't where the
settings say. Download Sage (it is free and open source:
github.com/lazear/sage, Releases), unzip it into a folder without spaces such
as `C:\sage`, and set the method's `sage_exe` to the file. The search starts
by itself once it is found.

## Raw file converter not found {#search.hold-converter}

Sage can't read Thermo `.raw` files, so Ionomos first converts each one to
mzML with ThermoRawFileParser. Download it
(github.com/compomics/ThermoRawFileParser, Releases), unzip it into a folder
without spaces such as `C:\ThermoRawFileParser`, and set the method's
`raw_converter` to `ThermoRawFileParser.exe`. The converted files go to
`sage_mzml\` in the experiment folder; your `.raw` files are not touched.

## Sage settings missing or unusable {#search.hold-sage-config}

The method names a Sage settings file (`sage_config:`) that isn't in the
workflow folder, isn't valid JSON, or names a TMT kit Sage doesn't have
(`quant.tmt` must be `Tmt6`, `Tmt10`, `Tmt11`, `Tmt16`, `Tmt18` or
`{"User": [reporter masses]}`). Put a working Sage JSON there (the one from a
search that worked: `results.json` in its output folder is a complete copy),
or remove `sage_config:` to use Ionomos' defaults: tryptic, high-resolution
MS2, label-free quantification.

The job is also held when the method's `sage_args` has `--parquet`: Ionomos
reads Sage's `.tsv` tables, so take that option out.

## The method is no longer set up {#search.hold-method}

The experiment's method was removed from the settings after it was queued.
Add the method back (tab 3 Methods), or change the experiment's method.

## A raw file can't be read yet {#search.hold-raw-locked}

Just before a search starts, Ionomos opens each raw file. One of them could
not be opened: it is still open in another program (Xcalibur still writing
it, a copy that has not finished, an antivirus scan), or this Windows
account may not read it. Nothing is wrong with the experiment. Close the
program or wait for the copy; the search starts by itself once every file
can be read. If it never does, check the file's permissions in Explorer
(right-click → Properties → Security).

## A path has a space in it {#search.hold-spaces}

FragPipe can't use a path with a space in it, so the search waits instead of
failing half-way. The reason names the path: usually a FASTA whose file name
has a space (pick or rename it on tab 3 Methods) or a FragPipe or tools
folder under one (move it, for example to `C:\FragPipe`). Experiment folders
and raw file names never have spaces: Ionomos takes them out when it files a
drop.

## The earlier output can't be moved aside {#search.hold-previous-output}

A search starts in an empty `fragpipe` folder, and the output of an earlier
attempt is kept as `fragpipe_previous_<time>`. Moving it aside failed because
a file in it is open in another program (a table open in Excel, Explorer's
preview pane). Ionomos never writes a new search over it: close the file and
the search starts by itself.

## A notification did not arrive {#trouble.notify}

A message that can't be sent is given up on after a few seconds and noted
once in the log ("could not notify by ..."). The search itself is not
affected: its status, `DONE.txt` / `FAILED.txt` and the report are written
before the message is sent.

Press **Send test** on the app's **8 Notifications** tab, or run
`ionomos notify-test`. It sends a test message to every channel and says
what happened to each:

- **notifications are not set up**: there is no channel under `notify:`
  ([how to set one up](#faq.notify)).
- **notify.enabled is false**: the channels work, but jobs send nothing
  until `enabled: true`.
- **the server answered HTTP 404 / 403 / 410**: the webhook address is
  wrong or was removed. Make a new one in Teams or Slack and paste it in.
- **could not connect** or **no answer in time**: the PC has no route to
  the service (no internet, a firewall, a proxy), or the address is wrong.
- **environment variable ... is not set**: `url_env` / `password_env`
  names a variable the watcher can't see. Set it for the Windows account
  that runs Ionomos, then sign out and in again.
- **email**: "authentication" errors mean the user name or password is
  wrong, or the mail server wants an app password; "certificate" errors
  mean `security` or `port` don't match the server (587 with `starttls`,
  465 with `ssl`).

The test works but jobs send nothing: restart the watcher after changing
`config.yaml`, and check `on:` lists the event (`done`, `failed`, `held`).

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
- **FragPipe step X failed (exit code N)**: one of FragPipe's tools stopped.
  The reason quotes the last lines that tool printed, which usually name the
  cause; the list below explains the common ones.
- **FragPipe stopped early, or only did a dry run**: FragPipe ended without
  finishing its steps. Open the log from the pop-up and send **Report a
  problem…** if the cause isn't in the list below.
- **experiment.yaml tmt: ...** (TMT): the channel list doesn't fit what
  FragPipe accepts. List every channel of the label type once, with one
  sample name without spaces per channel, `NA` for an unused channel, and
  no name twice.
- **A path has a space in it** (DIA-NN): DIA-NN can't take spaces in paths.
  Rename the folder or file.
- **The experiment folder is gone**: it was moved or renamed after filing.
  Put it back where it was.
- **Ended from outside, a cut-off table, a duplicate job**: see
  [When a search ends early](#search.ended-early).

## When a search ends early {#search.ended-early}

- **Ended from outside, or stopped without saying why**: something ended
  FragPipe while it ran (Task Manager, signing out, the PC going to sleep or
  shutting down, Windows running out of memory). Retry; keep the PC awake.
- **The result table was cut off, or is empty**: FragPipe ended while it
  wrote its table, usually because the disk filled up. Free space, then
  Retry.
- **No 'ALL JOBS DONE' line and no result tables**: FragPipe said it
  succeeded but did not finish. Retry; if it repeats, check that tab 1 names
  `fragpipe.bat`.
- **A raw file disappeared while FragPipe searched it**: it was moved,
  renamed or deleted during the search. Put it back, then Retry.
- **The console log grew past its limit**: a tool printed the same line over
  and over, and the search was stopped before the log filled the disk.
- **Duplicate of job N**: the job list had two jobs for one experiment
  folder. The second is never searched; retry job N.
- **Ionomos hit an unexpected error while running the search**: Ionomos
  itself could not carry on (often a full disk). Fix the cause, then Retry.

When Ionomos itself is ended in the middle of a search (Task Manager, a
crash), FragPipe can keep running on its own. The next time Ionomos starts
it stops that FragPipe first and runs the search again from the start, so
one experiment is never searched twice at once.

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

## Check the roles of the conditions {#issue.ROLES_UNSURE}

Ionomos gives each condition a [role](#glossary.role) from its name, and
here it is not sure. The message says which condition and why:

- a name has a word that often, but not always, means "plus a competitor"
  (`pre`, `block`, `10x`): it was read as a
  [competition](#glossary.competition) and the comparisons were made that
  way;
- or a competition can't be linked to one compound, because there are
  several and its name doesn't say which. It was then only compared with the
  control.

Open the experiment editor (the pop-up window's button, or tab 7 Analysis):
under **Roles** the condition is marked **?**. Press **Confirm** if the guess
is right, or pick its role in the list, then **Run analysis**
([How?](#faq.roles)). A condition that is not a competition gets `compound`.
The same can be written under `analysis:` in the experiment's
`experiment.yaml`:
`roles: {DMSO: control, Probe: compound, Probe_Comp: competition of Probe}`.
Listing `comparisons:` yourself also settles it.

## Read as a competition experiment {#issue.COMPETITION_DESIGN}

A note, not a problem. One condition's name says it is the compound plus a
competitor, so the comparisons follow the design instead of "everything
against the control": each compound vs the control (what it enriches), each
[competition](#glossary.competition) vs its compound (what the competitor
takes off) and vs the control (what is left). Nothing else is compared by
default: not two controls, not a pool or a QC standard, not one compound's
competition with another compound. The report gets a
[Specific targets](#report.specific) section.

If a role is wrong, change it under **Roles** in the experiment editor
([How?](#faq.roles)), or set `roles:` under `analysis:` in `experiment.yaml`
(see [Check the roles](#issue.ROLES_UNSURE)). For the old behaviour, every
condition against the control, set `role_comparisons: false`.

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
replicate numbers, the comparisons are still valid but less sensitive, and
you can get the sensitivity back: add `block: replicate` under `analysis:` in
the experiment's `experiment.yaml` and re-run the analysis. The replicate
number then becomes a blocking factor in the model, so each comparison is
made within a batch ([How to re-run](#faq.rerun)). Next time, randomise the
preparation and run order.

## Precursor masses are off in some runs {#issue.PSM_MASS_ERROR}

In the runs named, the measured precursor masses are 10 ppm or more away from
the calculated ones (the median over the run's PSMs, from FragPipe's
`psm.tsv`). The usual cause is the instrument's mass calibration: a lock mass
that was off, or a calibration that is due. The search corrects a steady
offset, so the results usually stand, but tell whoever looks after the
instrument and calibrate before the next runs. If a flagged run also has far
fewer PSMs than the others, re-acquire it. A search that allows mass offsets
can also give this warning without anything being wrong. The numbers per run
are in the [Search quality](#qc.psm) tab. The 10 ppm limit is a wide default,
not yet the lab's own.

## Many missed cleavages in some runs {#issue.PSM_MISSED_CLEAVAGES}

In the runs named, half or more of the PSMs are peptides with a site the
enzyme did not cut. That points to an incomplete digestion (too little
enzyme, too short, the wrong pH, old enzyme), or to a workflow whose enzyme is
not the one used. A sample digested less completely than the others measures
different peptides, so its quantities can differ for that reason alone:
compare it with its replicates in the [scorecard](#qc.card) and
[PCA](#qc.pca) before trusting it. The numbers per run are in the
[Search quality](#qc.psm) tab. The 50% limit is a wide default, not yet the
lab's own.

## Time course: the time points need a look {#issue.TIMES}

Ionomos found what looks like a time course but couldn't place every
condition on it. The message names the condition. Common causes:
- `analysis.times` names a condition that isn't in this experiment, or gives
  a number without a unit (`4` instead of `4 h`).
- A condition name holds two times (`Drug_1h_then_4h`).
- Two conditions of one series are at the same time (`A_1h` and `A_60min`).

List every condition's time under `analysis:` in the experiment's
`experiment.yaml`, e.g. `times: {Drug_a: 0, Drug_b: 1 h, Drug_c: 4 h}`, and
re-run the analysis ([How to re-run](#faq.rerun)). If the experiment isn't a
time course, add `time_course: false`. The [Time course](#report.time)
section and the pairwise comparisons for the other conditions are not
affected.

## Liganded sites: the ratio may be the other way round {#issue.LIGANDED_DIRECTION}

Far more sites would count as [liganded](#glossary.liganded) if the
[competition ratio](#glossary.competition-ratio) were read the other way
round. Ionomos reads R as heavy / light, which is right when the
compound-treated sample carries the light tag. Check which sample got which
tag. If the treated one is heavy, add `liganded_direction: low` under
`analysis:` in the experiment's `experiment.yaml` (or in the lab's settings,
if the lab always labels that way) and re-run the analysis
([How to re-run](#faq.rerun)). If the tags are as assumed, the compound may
make many sites more reactive, or the two samples were mixed unevenly; the
[Liganded sites](#report.cys) rank plot shows which.

## The site annotation could not be used {#issue.SITE_ANNOTATION}

`site_annotation` names a table of known sites (for example a CysDB
download) that wasn't found, or that has no readable site column. Put the
file in the experiment folder, or give its full path. It needs one column of
site keys like `P04406_C152`, or a protein accession column plus a residue
number column; yes / no columns such as `ligandable` and `hyperreactive`
become the known / new marks. The liganded calls themselves don't depend on
it.

## A replicate's site ratios sit off 0 {#issue.RATIO_OFFSET}

In an isoDTB experiment most cysteines are not engaged by the compound, so
their heavy / light ratio should be 1 (log2 0) in every replicate. Ionomos
measures each replicate's offset on those stable sites, and in the replicates
named it is clearly away from 0. The usual cause is the mixing: heavy and
light were not combined exactly 1:1, which moves every ratio of that
replicate by the same amount. Unchanged sites then look changed, and the test
against 0 calls some of them.

The ratios were used as measured, because centring is the lab's decision. If
the mixing is the likely cause, add `ratio_centre: auto` under `analysis:` in
the experiment's `experiment.yaml` (or in the lab's settings) and re-run the
analysis ([How to re-run](#faq.rerun)). `auto` centres each replicate on its
stable sites, and only when one is clearly off; `median` centres every
replicate on its median site, which goes wrong when a compound moves many
sites one way. If the compound really does move most sites, leave the ratios
as they are. The [liganded calls](#report.cys) say which ratios they used.

## The protein correction needs a look {#issue.PROTEIN_CORRECTION}

`protein_correction` asks Ionomos to subtract each site's protein change, from
an unenriched proteome of the same treatment, from the site's ratio (the
MSstatsPTM adjustment). It couldn't do that as asked:
- The proteome wasn't found or isn't usable. `proteome:` takes the folder of
  an analysed Ionomos experiment (or its `results` folder), or a protein table:
  MSstats groupComparison output (Protein, Label, log2FC, SE, DF) or an
  Ionomos `*_differential.tsv`. A relative path is read from the experiment
  folder.
- Few sites found their protein. With `match: gene` both sides need the same
  gene names; with `match: protein` the same UniProt accessions (isoforms are
  joined). A table with accessions only needs `match: protein`.

The uncorrected results are not affected: the site comparison without the
correction is always reported. Fix the setting under `analysis:` in
`experiment.yaml` and re-run the analysis ([How to re-run](#faq.rerun)).

## Protein correction: the proteome's comparisons don't match the sites' conditions {#issue.PROTEIN_CORRECTION_CONDITIONS}

Each site condition (an isoDTB sample, often named after the experiment, like
`EJQ_2_027`) needs the proteome comparison of the same compound against its
control, and Ionomos never guesses it. By default it looks for a comparison
whose first condition has the site condition's name. Name it instead:

```yaml
analysis:
  protein_correction:
    proteome: D:/Fragpipe_General/EJQ/20261001-DIA_EJQ-2-030
    conditions: {EJQ_2_027: Cmpd vs DMSO}   # the compound first
```

and re-run the analysis ([How to re-run](#faq.rerun)). The message lists the
proteome's comparisons. Site conditions without one are reported uncorrected.

## Dose-response: the doses need a look {#issue.DOSES}

Ionomos found what looks like a titration but couldn't read every dose, so
some conditions were left out of the [dose-response curves](#report.dose),
or none were fitted. The message names the condition. Common causes:
- `analysis.doses` names a condition that isn't in this experiment (a typo,
  or a renamed condition), or gives a number without a unit (`10` instead
  of `10 nM`).
- A condition name holds two doses (a combination, e.g. `A_1uM_B_10nM`).
- The vehicle isn't recognisable as the control (not named DMSO, vehicle, …).

List every condition's dose under `analysis:` in the experiment's
`experiment.yaml`, e.g. `doses: {DMSO: 0, Cmpd_A: 10 nM, Cmpd_B: 100 nM}`, and
re-run the analysis ([How?](#faq.rerun)).

## Some runs aren't in the SDRF {#issue.SDRF_UNMATCHED_RUNS}

The experiment folder holds an SDRF (`*.sdrf.tsv`), which Ionomos uses as the
design: each run's condition and replicate. Some runs of the quant table have
no row in it, so they kept the condition read from their names. If the SDRF
names none of the runs, it wasn't used at all. Runs are matched by the SDRF's
`comment[data file]` column (the raw file name, without its extension), and
for TMT also by `comment[label]` (`TMT126`, …). Common causes:
- The SDRF belongs to another experiment, or to an older search of this one.
- The raw files were renamed or converted after the SDRF was written.
- TMT: the SDRF's channels don't match the table's channels.

Fix the file names in the SDRF, or give the runs their condition in
`sample_conditions` (the Analysis tab, or `experiment.yaml`), which always
wins over the SDRF. Then re-run the analysis ([How?](#faq.rerun)).

## The TMT plexes are not on one scale {#issue.TMT_PLEXES_NOT_NORMALISED}

The experiment has several TMT plexes, and Ionomos couldn't put them on one
scale (internal reference scaling, IRS). Differences between plexes will then
look like differences between samples: colour the report's PCA by plex to see
it. IRS needs one of these:
- a pooled reference (bridge) channel in every plex: set
  `analysis.tmt_reference: [126]` (the channel, or the sample's name) in
  `experiment.yaml`, or mark the row `pooled` in the SDRF's biological
  replicate column;
- or the same mix of conditions in every plex, so each plex's own mean can
  serve as the reference.

Then re-run the analysis ([How?](#faq.rerun)). If the plexes can't be joined,
compare conditions within a plex, or block on the plex
(`analysis.block: {sample: plex}`).

## The experimental design couldn't be used {#issue.DESIGN_NOT_USED}

The experiment asks for blocks or covariates (`block`, `block_from` or
`covariates` in `experiment.yaml`), but Ionomos couldn't fit that model, so
the comparisons used the plain one (each condition against the control, no
blocks). The message says why. Common causes:
- **The block is the same as the condition:** every block holds a single
  condition, e.g. a batch processed one condition at a time. Then the batch
  and the treatment can't be told apart, and no model can fix that.
- **A sample has no block or covariate value:** a typo in a sample name, or
  samples renamed or left out since the design was written.
- **Too many blocks or covariates for the number of samples.**

Fix the `analysis:` settings in `experiment.yaml` and re-run. In a paired
design, every pair (block) needs samples from at least two conditions.

## DEqMS wasn't used {#issue.DEQMS_NOT_USED}

`variance_prior: deqms` makes the statistics take into account how many
peptides each protein was measured with: proteins seen with many peptides
get more trust. That needs a peptide (or PSM) count for each protein, and
this result table has none, or too few proteins had one. The statistics
used limma's usual variance prior instead, so the results are still valid.
DIA-NN, FragPipe, MaxQuant and TMT-Integrator tables carry peptide counts.

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

## Samples hold exactly the same values {#issue.IDENTICAL_SAMPLES}

Two or more samples have the same number for every feature. Real replicates
never do. Usually one raw file or one column was loaded under two names, or a
column was copied in a spreadsheet. Identical samples make the spread look
smaller than it is, so the p-values come out too small.

Leave the copy out (Analysis tab, or `exclude_samples` in `experiment.yaml`)
and run the analysis again. See
[How do I leave a sample out?](#faq.leave-out)

## Features tested without replicate spread of their own {#issue.NO_RESIDUAL_DF}

These features have one value per group, so there is nothing to estimate
their own spread from. limma still gives a p-value, using the typical spread
of the other features. It tells you whether the fold change is unusual for a
typical feature, not for this one.

It happens when a group has one sample, or when missing values leave one value
per group and nothing is imputed. Treat these hits as leads to confirm. More
replicates fix it.

## Features with identical replicates {#issue.ZERO_VARIANCE}

For these features every replicate of a group has exactly the same value.
Measurements always differ a little, so the values were probably rounded,
capped, copied, or filled in with one number (imputation `min`, `zero` or
`mindet` does that). limma then uses the spread of the other features; a Welch
or Student test divides by zero and reports p = 0.

Click one of these features in the table and look at its values. Use the
unrounded table, or imputation `auto` or `none`, and run the analysis again.

## The variance prior could not be estimated {#issue.VARIANCE_PRIOR}

limma borrows information about the spread from all features (the "prior").
Here it could not: either fewer than 3 features have replicate spread (a very
short table), or the features' spreads differ so much that the estimate did
not settle. The p-values are then ordinary t-tests, or close to them. They are
valid, but with few replicates they find fewer changes.

There is nothing to fix in the settings. For a short list of proteins, a Welch
test (`test: welch`) is the plainer choice.

## Low confidence (a group has too few samples) {#issue.LOW_CONFIDENCE}

The comparison was tested, but a group has only one sample (or fewer than
the `min_valid` setting asks for), so the noise estimate comes from the
other groups. If replicates exist, fix the conditions
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

## The normalisation shifts the conditions against each other {#issue.NORMALISATION_COMPOSITION}

Normalisation puts the samples on one scale so that a difference in how much
was loaded doesn't look like biology. Median centring does it by lining up
the middle of each sample's values. That works when most features are the
same in every sample. In a pulldown, a depletion or a strong treatment a
large share of the features is enriched or depleted in one direction, the
middle moves, and every unchanged feature is pushed the other way. A few of
them then pass the fold-change cut-off, mostly when a group has only two
samples.

Ionomos checks for this by also normalising on the features themselves: each
sample is shifted by the median ratio of its stable features to their mean
across samples. When the two methods disagree by more than 0.1 log2 between
two conditions, median centring is not safe.

- With **Normalisation: auto** (the default for new set-ups) Ionomos uses the
  ratio method by itself whenever that happens, and this entry only tells
  you. Nothing to do.
- With **median** or **gn** chosen, the results are shifted by the amount in
  the message. Set Normalisation to **auto** on the Analysis tab (or
  `normalize: auto` under `analysis:`), and run the analysis again
  ([How to re-run](#faq.rerun)). **ratio** uses the ratio method always.

Neither method is right when nearly everything changes, for example a
pulldown against empty beads. Then no normalisation (`none`), equal loading
and more replicates are the honest choices.
