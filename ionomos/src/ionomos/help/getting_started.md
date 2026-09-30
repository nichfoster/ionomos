# Getting started {#start}

From an instrument run to a report, in six steps. You only ever touch the
inbox folder; Ionomos does the rest and tells you when it needs you.

## What Ionomos does {#start.overview}

You drag an experiment folder into the **inbox**. Ionomos waits until the copy
has finished, reads the names to work out whose experiment it is and which
[method](#glossary.method) it used, and shows you what it read. After you
accept, it moves the folder into your own folder, searches it with FragPipe
(or DIA-NN), and writes an interactive report with the statistics.

One search runs at a time, oldest first. A 3 × 7 isoDTB experiment takes about
30–60 minutes.

## 1. Name the folder {#start.naming}

The folder name must contain two things, anywhere and in any order:

- **your initials** (or the name of your folder under `C:\Fragpipe_General`),
- **the method**: `isoDTB`, `TMT` or `DIA`.

A date is optional. The recommended shape sorts well:
`YYYYMMDD_<initials>_<method>_<anything>`, for example
`20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h`.

Spaces and brackets are fine: they are removed on the way in, because
FragPipe can't handle spaces. The original name is kept in `ionomos.json`.

## 2. Name the raw files {#start.files}

The **end** of each `.raw` file name carries the numbers. What they mean
depends on the method:

- **isoDTB**: `<sample>_<replicate>_<fraction>.raw`, e.g. `X_1_1.raw` …
  `X_3_7.raw`. Every [replicate](#glossary.replicate) needs the same
  [fractions](#glossary.fraction).
- **TMT**: `<sample>_TMT_F<fraction>.raw`. All replicates are in the channels,
  so the replicate is always 1.
- **DIA**: `<condition>_<replicate>.raw`, e.g. `DMSO_1.raw`, `Drug_2.raw`. The
  part before the number is the [condition](#glossary.condition).

A short code glued to the number also works for DIA: `_D1` is DMSO
replicate 1 and `_C1` is Compound replicate 1. An instrument setting at the
end (`_CV-35`, `_HCD33`) stays part of the sample name; it is never read as a
replicate.

Not sure? `ionomos names test <folder name> <file.raw> …` (or the app's
Methods tab → **Test names…**) shows how names are read, and changes nothing.

## 3. Drop it in the inbox {#start.drop}

Drag the folder into `C:\Fragpipe_Auto\inbox`. The folder needs at least one
`.raw` file and must stop changing for about a minute, so a slow copy is fine.
Only the `.raw` files, without a folder, works too: Ionomos groups them by
their shared name into a new folder.

## 4. Check the review window {#start.review}

Before anything is filed, a window shows what Ionomos read: user, method, date,
and each file's condition, replicate and fraction. The **Control** picker is
the "vs" side of every [volcano plot](#glossary.volcano).

Fix anything that is wrong, then **Accept & queue**. Your answers are saved in
the folder's `experiment.yaml`, so you are never asked twice. **Not now**
leaves the folder in the inbox.

If Ionomos can't work something out (no initials, no method, a file name it
can't read), the same window opens with the problem at the top.

## 5. Where your experiment goes {#start.where}

The folder moves to `C:\Fragpipe_General\<you>\<experiment>\`. In it you will
find, as the work goes on:

- your `.raw` files, unchanged (a name with spaces or symbols gets `-` in
  their place),
- `ionomos.json`: the status and everything Ionomos read,
- `ionomos_run\`: what Ionomos gave FragPipe, and FragPipe's own log,
- `fragpipe\`: FragPipe's output,
- `results\`: the statistics, the plots and **`report.html`**,
- `DONE.txt` or `FAILED.txt`.

## 6. DONE, FAILED and other notes {#start.notes}

Ionomos leaves short text notes where you will look:

- **`DONE.txt`**: the search and the analysis finished. It names the report
  and counts the hits in each comparison. Open `results\report.html`.
- **`FAILED.txt`**: the search stopped. It gives the reason, the most likely
  cause in plain words, and where FragPipe's log is. Fix the cause, then press
  **Retry** (app → Jobs tab, or the pop-up). See
  [A search failed](#attention.search_failed).
- **`<folder>.REJECTED.txt`** (in the inbox, next to your folder): the folder
  could not be taken in, and the note says why. Rename the folder or files (or
  add an `experiment.yaml`); Ionomos tries again by itself. See
  [A folder was not taken in](#attention.intake_rejected).
- **"waiting: …"** in the app or `ionomos status`: the search is held until a
  setup file is in place. See [A search is waiting](#attention.search_waiting).

## When a window pops up by itself {#start.popups}

When Ionomos can't go on without a person, it opens a window, and the app's
bottom bar shows **⚠ N need attention** until it is dealt with. Each window
says what happened, the likely causes, and has the buttons that fix it
(Retry, the experiment editor, the log). Windows close by themselves once the
cause is fixed. **Remind me in an hour** or **Dismiss** if it can wait.

## Open the report {#start.report}

Double-click `results\report.html` in the experiment folder, or app → Jobs tab
→ **Open report**. It is one file that works offline: you can copy it, email
it, or keep it on a lab drive. See [Reading the report](#report).

## Analyse a table on any computer {#start.anywhere}

You don't need the lab PC to get a report. On Windows, macOS or Linux with
Python 3.11 or newer:

- `pip install ionomos`
- `ionomos demo --open` writes a small simulated experiment and opens its
  report.
- `ionomos analyze <table or folder> --open` analyses your own FragPipe,
  DIA-NN, MaxQuant, Spectronaut or other table.

For a table on its own, the results go to a new `<table name>_ionomos\`
folder next to it; nothing else is touched.
