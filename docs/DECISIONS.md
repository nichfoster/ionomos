# Decision log

Short ADR-style entries. Newest at the bottom. Reverse a decision by adding a
new entry, not editing the old one.

---

### D1 — Watch at the folder level, not the file level
**2026-09-15.** The unit of work is an experiment (many raws + optional
manifest), not a raw file. The QC pipeline in `prior-work` watches single
`.raw` files because a QC run *is* one file; that doesn't transfer. The
watcher tracks top-level directories in the inbox and treats the whole tree's
(size, mtime) fingerprint as the stability signal.

### D2 — Polling, not filesystem events
**2026-09-15.** Same reasoning as prior-work: `watchdog`/`ReadDirectoryChangesW`
is unreliable over SMB and for drag-copies that emit thousands of events. At
one drop a day, polling every 10 s costs nothing. Stability window is
generous (60 s) because a 20 GB drag from a USB HDD can stall.

### D3 — The drag into the inbox is the "submit" action
**2026-09-15.** We do not auto-pull from the Eclipse share. Users must
deliberately drop into `inbox\`. This keeps a human decision in the loop
before a 60-minute compute job starts, avoids processing half-copied instrument
output, and means the Eclipse-side layout can stay however it is. Revisit if
users find the double copy annoying (see ROADMAP).

### D4 — Move, don't copy, into the user directory
**2026-09-15.** Inbox and `C:\Fragpipe_General` are on the same volume (C:),
so `os.replace` is an atomic rename — instant, no double disk usage on a 99 GB
free drive. If a future config puts them on different volumes, intake must
copy + verify sizes + delete source, and must be told so explicitly (config
flag), never silently.

### D5 — Python, not R, for post-processing
**2026-09-15.** R is not installed on the PC, the two scripts are ~100 lines
each of tidyverse that map 1:1 to pandas, and one runtime is easier to keep
alive than two. The R scripts stay in `reference/lab-scripts/` as the spec,
and each port gets a golden-file test against real lab output.

### D6 — Headless FragPipe only; never drive the GUI
**2026-09-15.** `--headless --workflow --manifest --workdir` is documented and
already proven in prior-work. Workflows are pinned files exported once from
the GUI by the maintainer.

### D7 — Sequential jobs
**2026-09-15.** One FragPipe run at a time, using ~28 threads / 48 GB. Lab
throughput is a few runs per week. Parallelism would only add contention and
failure modes.

### D8 — Status lives next to the data
**2026-09-15.** `ionomos.json` + `DONE.txt`/`FAILED.txt` in the experiment
folder. Users look at their folder, not at a dashboard or log. SQLite is the
machine-readable ledger for the CLI and restart recovery; the JSON is the
human-facing copy.

### D9 — Naming convention over manifest file
**2026-09-15.** The folder name carries user/method/ID and the raw filenames
carry replicate/fraction; `experiment.yaml` is optional and only needed for
TMT channel maps or overrides. Reason: people already name raws
`<prefix>_<rep>_<frac>.raw`, and asking for a YAML file on every drop would
be the step everyone forgets.

### D10 — Config-driven method table
**2026-09-15.** Each method (`isoDTB`, `TMT`, `DIA`) is a config entry:
workflow file, FASTA, data type, post-processing steps. Adding a method or
changing a workflow should not require a code change.

### D11 — Repo layout: monorepo, `ionomos` is the first package
**2026-09-15.** `lab-informatics` is the umbrella; `ionomos/` is a normal
installable Python package with its own `pyproject.toml`. The QC pipeline and
any future tools sit beside it rather than inside it. Reference material and
lab artefacts live under `reference/`, never inside a package.

### D12 — Keyword matching instead of a fixed folder layout; sanitise, don't reject
**2026-09-15.** Supersedes the fixed-field part of D9. Nich's direction:
"as flexible as possible." Method is found by keyword anywhere in the folder
name, user by initials/alias token, date opportunistically. Spaces and
punctuation are sanitised on the way in rather than rejected, with the
original name recorded. The *tail* of raw file names stays strict because
that's what FragPipe's manifest is built from, and its meaning is per-method
(isoDTB rep_frac; TMT frac with biorep 1; DIA cond_biorep).

### D13 — A tiny tkinter window beats a rejection note
**2026-09-15.** When a folder can't be interpreted, the watcher (running in
the interactive session on the PC) opens a pre-filled tkinter dialog rather
than only writing a note. tkinter ships with Python (no dependency), opens
instantly, and the answer is persisted as `experiment.yaml` + learned aliases
so the same question is never asked twice. The note remains the fallback for
headless runs and for "Skip". Rejected folders are re-evaluated when their
tree changes or the note is deleted, so users fix things in place.

### D14 — One executable, two faces; the GUI writes the same config.yaml
**2026-09-15.** `Ionomos.exe` with no arguments is the setup wizard /
control panel; with arguments it is the CLI. Everything the app does goes
through the same `config.yaml` and the same `config.load` validation, so the
app can't produce a config the watcher rejects, and power users can still edit
the file by hand. Two exes are shipped (windowed for the app and for the
Task-Scheduler `run`, console for terminal use) because a windowed exe can't
print and a console exe leaves a window open at logon. PyInstaller must run
on Windows; the Mac build only validates the spec.

### D15 — Prototype on the PC from a git checkout, not from the exe
**2026-09-16.** The frozen exe is right for the lab's final install but wrong
for iteration: every change meant rebuild → unzip → reinstall. While the tool
is being developed the PC runs an *editable* install of a git clone
(`deploy/dev_install.ps1`), so an update is `git pull` — exposed as an
"Update from GitHub & restart" button in the app and `ionomos update`. The
update refuses to clobber hand edits on the PC (all changes go via the Mac and
GitHub). The reverse channel is `ionomos diagnose` / "Copy diagnostics": one
text block with version + commit, check, status, config, log tail and inbox
notes, so a report from the PC carries everything needed to reproduce it.
GitHub Actions runs the suite on Windows on every push and builds the exe on
tags, so the Windows-specific parts are exercised without a hand build.

### D16 — FragPipe runs inside the watcher process; setup gaps hold, job problems fail
**2026-09-22.** The worker is a thread in `ionomos run` (not a second
service): one process to start, stop, and put in Task Scheduler, and the
startup task already runs interactively. One search at a time. Before each
job, missing *setup* (launcher, the method's pinned workflow or FASTA) holds
the job in `queued` with a visible "waiting: …" reason and it starts on its own
once the file exists — a fresh install can accept drops before FragPipe is
configured, and nothing fails because an admin hasn't finished. Problems that
belong to the job (its experiment.yaml names a missing workflow, raws moved
away, FragPipe exits non-zero, timeout) fail it with `FAILED.txt`. The FASTA
is written into a per-job copy of the workflow (`database.db-path`), so the
per-method FASTA setting is authoritative and "FASTA file path is empty" can't
happen; the pinned workflow file is never modified. Inputs go in
`ionomos_run/`, FragPipe's `--workdir` (`fragpipe/`) starts empty, and a
re-run moves the previous output aside instead of deleting it. Launcher is
`fragpipe.bat` (the 22.0 inventory shows `bin/fragpipe.bat` next to a GUI
`fragpipe.exe`); a configured `fragpipe.exe` is swapped for the `.bat` beside it.

### D17 — Fail visible, never fail silent, never lose data
**2026-09-23.** Failsafes are layered rather than clever: each loop catches
per-iteration errors; a supervisor restarts a loop that escapes; excepthooks
record anything else to `crash-*.txt`; heartbeats reveal hangs that none of
those can see. Coordination between processes (app ↔ watcher) goes through
files in `log_dir` — `ionomos.lock`, `heartbeat.json`, `STOP`, `PAUSED`, a
job's `ionomos_run/CANCEL` — because they work identically for the startup
task, the app, the CLI and a second Windows session, with no ports or IPC. The
ledger is treated as a cache of the `ionomos.json` files, so it can always be
rebuilt. A folder whose contents make intake fail deterministically is
rejected with a note (never retried forever); only OS-level transient errors
retry. `ionomos testbed stress` checks the end-to-end invariants (no raw file
lost or duplicated, every drop accounted for, nothing stuck) under chaos, and
runs in CI.

### D18 — Downstream statistics in pure Python, validated against R
**2026-09-23.** No pandas/numpy/scipy/matplotlib: the Windows exe stays small,
the report is one offline HTML file with hand-written SVG, and nothing new has
to be installed on the PC. The price is owning the numerical code, so every
piece is checked against the reference implementation it replaces: the
isoDTB and TMT scripts run *unmodified* on shared inputs and our output is
byte-identical (including readr's number formatting and R's mean algorithm);
t-tests and BH against scipy (1e-10); the moderated t-test against limma 3.68
(1e-8). Generator scripts for every golden file live next to them.

### D19 — Moderated t-test (limma eBayes) is the default
**2026-09-23.** With three replicates a plain t-test has 2–4 degrees of freedom
and barely finds anything after FDR correction (5–33 % of planted 3–4-fold
changes in simulation). Borrowing variance across all proteins, as limma does,
found 95–100 % with no false positives. It is the field standard for small-n
proteomics, so it is the default; Welch/Student stay available per lab or per
experiment. Missing values are not imputed (a feature needs `min_valid` values
per group) — imputation choices belong to the lab, not to a default.
*(Superseded in 0.6.0 by D24: FragPipe-Analyst's pipeline, which imputes
label-free data by default; `imputation: none` gives the old behaviour.)*

### D20 — Setup is a checklist, not a manual
**2026-09-23.** The app opens on a live checklist (folders writable, safe
layout, people, config valid, FragPipe + MSFragger, workflow + FASTA with
decoys per method, disk, watcher, startup task), each open item with a one-click
fix. Layouts that would make ionomos act on its own files (inbox = users
folder, one inside the other, logs inside the inbox) are config *errors*, not
warnings. `ionomos init` gives the same result without a display.

### D21 — Renamed to Ionomos; the old name stays readable forever
**2026-09-23.** LabWatch → Ionomos everywhere (package, commands, exes, files,
task, repo). The lab PC already holds LabWatch-era data, so every name that
exists on disk or in Windows has its old twin in `names.py`: readers accept
both (`labwatch.json`, `labwatch_run/`, `labwatch.log`, `labwatch.lock`, the
`labwatch` task, `%APPDATA%\labwatch`, `LABWATCH_CONFIG`), writers use the new
one, and the only thing ever renamed is our own status file, on first write.
A still-running LabWatch watcher blocks an Ionomos one (same inbox). User
folders (`C:\Fragpipe_Auto`, `C:\Fragpipe_General`) keep their names — they are
the lab's, not the tool's.

### D22 — A real installer; updates and support are one button each
**2026-09-23.** An Inno Setup installer replaces "unzip into the data folder":
the program goes to `C:\Ionomos` (per-user, no admin), data stays where it is,
so install, upgrade and uninstall can never touch config or experiments. It
stops the watcher gracefully before replacing files and retires a LabWatch
install. (Since 0.5.2 the repository is public and the app downloads updates itself —
size- and SHA-256-checked against GitHub's published digest — and still
accepts a Setup found in Downloads.) Support goes the other way
through one button that leaves a single zip on the Desktop, with the exact
build (commit) inside so a report maps to code. CI installs, upgrades and
uninstalls the real installer on Windows for every release.

### D23 — Loose raw files get a folder; ambiguity still goes to a person
**2026-09-23.** On the first real drop, Chris's files went straight into the
inbox and were silently ignored. Ionomos now groups loose `.raw` files by their
shared leading name parts (≥ 2) into a folder in the inbox, after the usual
stability wait, and hands that folder to intake immediately. Only moves, never
overwrites; files arriving later join the folder Ionomos made (marker file).
Xcalibur's `_YYYYMMDDhhmmss` re-acquisition suffix is ignored when reading
the tail, but a re-acquired replicate next to its original is a duplicate the
resolver window asks about — which one is right is a scientist's call.

### 2026-09-23 — recoverable inbox deletion and naming memory

Inbox and resolver deletion move items into a hidden `.removed` folder, preserving
raw data while withdrawing it from intake. Intake compares folder fingerprints
across the resolver wait so removed/changed input cannot be queued using stale
answers. Confirmed resolver interpretations are logged in `naming-history.jsonl`
and reused by user/method; exact file corrections and sample-label mappings are
supported, and ambiguous examples are not applied. Explicit YAML wins.

### 2026-09-23 — DIA output identity and incomplete analysis warnings

FragPipe's `_uncalibrated`/`_calibrated` mzML suffixes must be matched back to
original manifest runs before condition grouping. Exact run identity wins;
acquisition timestamp fallback is allowed only for a unique match. Missing-value
markers such as `NaN` must not cause entire run columns to be dropped. Report
missing manifest runs and zero-test comparisons explicitly. Re-analysis reads
current per-file experiment.yaml labels/replicates so a completed search can be
repaired without rerunning FragPipe. Recognize FragPipe 24 `dia-quant-output` files.

### D24 — The analysis is FragPipe-Analyst's, ported and checked against the package
**2026-09-23.** The lab wants FragPipeAnalystR / FragPipe-Analyst (Nesvilab,
Monash) for downstream analysis. R is not installed on the PC, and it would
need ~40 Bioconductor packages, so the pipeline is ported to Python
(`downstream/fpa.py`) in FragPipe-Analyst's order: contaminants → missing-value
filters → normalisation → imputation → `test_limma` (one `~0 + condition` model,
per-contrast refit when values are missing, one eBayes) → `add_rejections`
(inclusive cut-offs), plus its QC plots (PCA on the 500 most variable features,
clustered correlation, missing-value pattern, CVs, identifications), the
significant-feature heatmap and enrichment. Checked three ways
(`tests/test_fpa.py`): R's own RNG is reproduced so Perseus-type imputation with
`set.seed(123)` gives R's numbers; limma 3.68 with the package's functions
transcribed to base R (all / control / others / missing values, CIs to 1e-8);
and the **real FragPipeAnalystR 1.1.1** running the `reproduce_in_R.R` script
Ionomos exports: every protein, both comparisons, identical to 1e-10, zero
disagreements on significance (`tests/golden/fpa/e2e/`).

Changed for the lab, each a setting (Analysis tab → "Use FragPipe-Analyst's
defaults" restores theirs): the missing-value filter keeps features measured
in ≥ 50 % of at least one condition (theirs: 0 %), because Perseus imputation
turns one-hit wonders into large fold changes; samples are median-centred
(theirs: none) — a no-op on DIA-NN/MaxLFQ output that is already normalised,
and a rescue when it isn't; the default comparison is each condition vs the
recognised control (theirs: all pairs), as the lab designs experiments. Kept
theirs: Perseus-type imputation for label-free data (it makes on/off proteins
testable; in simulation it costs ~15 % recall on randomly missing values,
which `imputation: none` recovers), none for TMT. isoDTB ratios keep the
lab-script site table and a moderated one-sample test per condition. Not
ported: VSN normalisation, MLE/bpca imputation, paired designs, fdrtool.

FragPipeAnalystR and FragPipe-Analyst are GPL-3, and this is a translation of
their code, so Ionomos is now distributed under GPL-3.0-or-later (LICENSE).

### D25 — The report is interactive, from data embedded in the page
**2026-09-23.** `report.html` carries its results as JSON and draws them in the
browser with a small hand-written script (`downstream/assets/report.js`): no
plotting library, no internet, one file to email. Cut-offs change live, points
and rows open a protein (values per condition, imputed values hollow, stats in
every comparison), enrichment terms mark their genes on the volcano, every
chart downloads as SVG and the filtered table as CSV. The statistics are
still computed once in Python; the page only re-thresholds them. Static
`volcano_*.svg` files stay for slides and for viewers without scripts.

### D26 — Enrichment runs on the PC against the right background
**2026-09-23.** FragPipe-Analyst sends each hit list to the Enrichr web service
(genome background) and corrects the odds ratio afterwards. Ionomos downloads
the same Enrichr libraries once (Hallmark, GO, KEGG, Reactome, WikiPathways;
cached in `%APPDATA%\Ionomos\genesets`) and runs a one-sided hypergeometric
test per term against the genes that were actually quantified, BH across
terms. Gene lists never leave the PC, it works offline after the first
download, and a lab `.gmt` can be added. No library → a note in the report,
never a failed analysis.

### D27 — Anything that needs a person pops up a window, and stays on a list until fixed
**2026-09-24.** Log lines and notes in folders were the only way Ionomos told
anyone something went wrong; nobody reads them. Now every such event raises an
item in a durable queue (`attention.py`, JSON files in `<log_dir>/attention/`):
an analysis that needs a decision, an analysis that failed, a failed or held
search, a rejected folder, an empty raw file. The app pops a window for each new
item (and shows "⚠ N need attention"); when the app isn't open, the watcher's
own Tk window does (the app's `app.alive` heartbeat decides who, so it never
pops twice). Each window has the likely causes, what to do, the log tail, and
the buttons that fix it — for analyses, the experiment editor itself (fix
conditions, leave a run out, pick the control, Run). Items close by
themselves when the cause is gone (a retry starts, a re-analysis comes back
clean, the rejected folder is dealt with); "Remind me in an hour" and Dismiss
exist, and `gui.popups: false` turns the windows off (the list stays).

### D28 — The analysis always finishes, checks itself, and always draws a volcano
**2026-09-24.** Every analysis stage runs isolated: a crash in QC, enrichment
or an export is recorded and the rest carries on; a limma failure falls back to
Welch; comparisons that don't fit the data fall back to the defaults; a report
failure falls back to a plain page with every volcano plot; volcano files are
written with retries and verified. Then the "doctor" (`downstream/doctor.py`)
turns what it saw into issues — no result table (with the method's likely
causes and the tables that *were* found), empty table, runs without
quantities, runs that didn't match their samples, duplicate runs, every sample
in one condition (with conditions suggested from the file names), no
recognisable control (the guess is used and the person is asked), groups too
small to test, a failed injection (identifications < 40 % of the median),
nothing testable, no volcano, heavy imputation, few features, no hits. Input
and error issues become the pop-up; warnings go in the report's issues panel.
The stress test now also checks every comparison has a volcano and every
analysis that isn't "ok" told a person.

### D29 — Cross-volume copies are hash-verified before the source is deleted
**2026-09-24.** When inbox and users_root sit on different drives, intake
copies the tree and used to compare file sizes before deleting the source —
silent same-size corruption would pass and destroy the original. `_move_tree`
now SHA-256s every copied file (1 MiB chunks) on both sides and removes the
source only when every hash matches; any mismatch or missing file raises
IntakeError and the source is left in place. The EXDEV rename fast path is
unchanged, and the disk-space check before copying still sizes the tree —
it estimates capacity, it does not verify content.

**2026-09-25 addendum.** When verification fails, `_move_tree` now removes
its own partial destination before raising, so a re-drop isn't blocked by
"destination already exists". This is the one place intake deletes on the
destination side, and it's safe only because `shutil.copytree` refuses an
existing `dst`: everything under `dst` was written by this call, and the
source is still intact. Keep that invariant (never `dirs_exist_ok=True`) or
the cleanup could remove real data. **2026-09-27:** a copy that dies partway
through `copytree` itself (disk full, a locked file) is cleaned up the same
way. The original error is re-raised so the watcher's retry logic applies,
or it becomes an IntakeError naming the leftover if cleanup fails too.

### D30 — Numbers read out of names must be plausible
**2026-09-25.** A parser that accepts any digit run turns run IDs and dates
into phantom data. Replicate/fraction tails are capped at three digits and
1–999 by value (a longer run is part of the sample name for DIA/TMT, a
rejection for isoDTB). The six-digit `MMDDYY` date only matches when
`2000+yy` is within `[today.year − 25, today.year + 1]`. Consequence: the
same folder name can parse differently in a different decade. That's
acceptable because the parse is recorded in `ionomos.json` at intake and
never re-derived.

### D31 — Agents open PRs; a person merges them
**2026-09-27.** Several coding agents (Obvious autobuild) work on this repo.
From 2026-09-24 an automerge workflow squash-merged any green PR and agents
could merge their own. In three days about 30 PRs landed unreviewed, including
a new deletion path in intake (the D29 addendum) and docs that fell out of
sync. CI can't see that kind of problem. The workflow is removed and
`.obvious/config.yml` sets `require_human_merge: true`. Every PR also asks
@nichfoster for review (`.github/CODEOWNERS`). Those are instructions, not
enforcement: an app with write access can still merge. To enforce this, turn
on branch protection for `master` requiring one approving code-owner review,
with admins exempt so the owner can still push directly.

### D32 — Small groups are tested and labelled, never refused; p-values are never invented
**2026-09-27.** A real run (Chris, 22Rv1 FLAG-AR, one DMSO against three
MA25) ended with "not enough replicates" and an empty volcano. The lab wants a
plot from whatever it has. A group of one is now tested: limma's group-means
model pools residual variance across every condition, and a Welch test falls
back to a pooled t-test. The comparison is labelled *low confidence* wherever
it appears. When nothing in the experiment has replicates, there's nothing to
estimate variance from, so the result is *fold change only*: candidates by
|log2FC|, no p-values, and a fold-change-vs-abundance plot instead of a
volcano. The label travels with every output (report, SVG, TSV notes,
`analysis.json`, CLI), so a copied plot can't lose it. `min_valid` stays the
line between "normal" and "low".

### D33 — Any table in, a volcano out; results go in a folder Ionomos owns
**2026-09-27.** People bring MaxQuant, Spectronaut, Perseus, limma, DESeq2 and
Excel sheets, not only FragPipe. `downstream/anytable.py` reads any of them
(stdlib only, including `.xlsx`) and hands the pipeline the same QuantMatrix
the FragPipe loaders make. A table that already holds results is plotted as
given and never recomputed. Such a table can sit in any folder, and that
folder may already have its own `results/`. So its analysis writes to
`<table name>_ionomos/` next to it and never touches the files beside it (the
D17 rule). The generic loader only runs when the method is unknown. A
pipeline job that knows it's DIA still reports "no pg_matrix" instead of
analysing a stray `psm.tsv`.

### D34 — Every drop is shown before it is filed; config changes apply without a restart
**2026-09-28.** D13 opened the window only when a name couldn't be parsed. A
real drop (Kosuke, `KC_DIA_D1..C3`) showed that a clean parse can still be
wrong: each file became its own condition, and nobody saw it before the
search. So every parsed drop now opens the same window in review mode
(`gui.review_drops`, on by default). It shows user, method, date, each file's
condition / replicate / fraction, and one line per condition with its role
and replicates. The Control picker uses the analysis's own rule; a different
choice is pinned in `experiment.yaml` `analysis.control`. The answer is saved
with `resolved_by: gui`, so a folder is never asked about twice. With no
display, drops are filed as read (the headless watcher can't block). On a
timeout, a review files as read while a problem is skipped; a clean reading
is a better default than a stuck inbox.

DIA names may glue a 1–2 letter condition code to the replicate (`_D1` =
DMSO rep 1, `_C1` = Compound rep 1, from `naming.condition_codes`). Longer
codes count only when listed, because the PC inventory has
`…_DIA_HCD33.raw`, where HCD33 is a collision energy. Short codes differ
between labs, which is one more reason for the review.

The watcher now reads config through `config.LiveConfig`, which re-loads
`config.yaml` and the learned aliases whenever either file changes. It keeps
the last good copy if a file is caught mid-save, and keeps the folders it
already has open. The window's user list refreshes every 2 s from the same
source. A user or alias added in the app therefore counts for the open window
and the next drop without restarting the watcher. The app's config writer
keeps the new keys, and a lab's code list replaces the defaults instead of
being merged with them.

### D35 — Every report checks its samples and looks past the volcano
**2026-09-30.** A volcano plot answers one question at one cut-off. The
questions that decide whether it can be trusted, and where the rest of the
biology is, were left to whoever reads the report. The report now answers
them every time, each in pure Python (`downstream/insights.py`), bounded by
features × samples, run as an isolated stage.

**Is each sample fine?** A scorecard of robust z-scores (median/MAD, with the
MAD floored so six near-identical samples can't make noise look extreme)
covers:
- identifications
- correlation with its replicates
- spread around its group
- MS-DAP's leave-one-out CV

A sample fails on two flags, or on the existing "far fewer identifications"
rule.

**Is there a batch?** A one-way ANOVA R² relates each principal component to
condition and to replicate number. Replicate number is the lab's de facto
batch (rep 1 of every condition is usually prepared together). It is only
used when every condition has two or more replicates.

**Does the imputation fit?** Detection rate is compared with mean intensity:
- a Spearman ρ
- the gap between complete and incomplete features, in SDs of the feature
  means

Missingness that doesn't depend on intensity makes Perseus-type imputation
invent fold changes.

**Can the p-values be read at face value?** The histogram's shape is
classified and Storey's π0 is estimated. The other two warnings:
- **imputation-driven hits**: half or more of a group imputed
- **presence/absence features**: at least 75% (and at least 2) of one group,
  none of the other

These produce **warnings, never pop-ups**. They are advice about data that
may be fine, and the thresholds haven't met real lab data yet. The clean
simulated experiments raise none of them (`tests/test_insights.py`).

Enrichment gains a **rank-based test on every protein**, because
over-representation only sees the hits and misses a pathway whose members
all move a little. It is a Wilcoxon rank-sum on the moderated t, as limma's
geneSetTest / wilcoxGST. Its variance is inflated by 1 + (k − 1)·r̄, where
r̄ is the set's mean inter-gene correlation of residuals. That is camera's
idea, applied to ranks, and computed in O(k·samples) with unit vectors.
Without it, co-regulated sets look far more significant than they are.
fgsea-style permutations were rejected as too slow in pure Python.

The page gains what analysts otherwise do by hand:
- search by list / wildcard / regex / gene set, with "not found" reported
- box selection
- highlight groups
- comparison-vs-comparison quadrants and an UpSet chart
- correlated proteins
- abundance rank, mean–variance, a power curve

Groups live in the browser's localStorage, so a lab member's "E3 ligases"
list follows them across reports. Pinning them into a report would need a
server. The view lives in the address hash, so a link reopens it. Protein
complexes (CORUM: non-commercial licence), CysDB for isoDTB sites, and
PSM-level QC from `psm.tsv` were left for later (ROADMAP).

### D36 — Ionomos is for other labs too: engines are adapters, analysis installs from pip
**2026-09-30.** The goal is now that other labs can use Ionomos (ROADMAP
Phase 5).

The research found that no open tool automates the path from a lab's Windows
instrument PC to a finished, trustworthy report without a bioinformatician:
- quantms, Frag'n'Flow and ProtPipe need Linux, containers and HPC.
- AlphaPept watches folders only for its own engine.
- MSAID's `watch` is a commercial cloud product.
- Downstream tools start from an uploaded table.

So Ionomos stays on-premise and Windows-native for the watcher, and adds:

1. **An analysis-only install from pip that runs anywhere.** The pure-Python
   analysis has one dependency, and the literature on tool adoption says
   installation decides whether a tool is tried at all.
2. **Engines as adapters.** A registry of adapters can each recognise their
   outputs, load one canonical quantity matrix and report provenance.
   FragPipe is the first; the any-table loader is the fallback. Import comes
   before running, because it is useful immediately and carries no licence
   risk.
3. **Ionomos never bundles an engine with a restrictive licence.** MSFragger
   is academic-only, DIA-NN is not redistributable from 1.9 on, and MaxQuant
   is not redistributable either. Each lab installs and accepts its own.
   Sage (MIT) is the only candidate for bundling.
4. **SDRF-Proteomics for sample metadata** (export first). mzTab is not used
   internally: it has stalled for quantification and adds nothing over the
   TSVs.

Priorities are in ROADMAP Phase 5. Whether numpy may become an optional
speed-up is left open until dose-response or a limpa-style model needs it.

### D37 — Another lab's names are config: templates per method, a regex for power users
**2026-09-30.** ROADMAP Phase 5A. The naming rules were tuned to this lab in
code: the per-method raw-file tails, the date formats, the DIA condition
codes. Another lab needs its convention to be a `config.yaml` change it can
check before the first drop. (D36, the Phase 5 plan, is in a separate PR.)

`naming:` now holds, besides `condition_codes`:
- **`methods.<name>`**: how that method's `.raw` names are read. It can be:
  - a readable **template** (`'{sample}_R{rep}_F{fraction}'`, with `[ ]` for
    optional parts); a plain string is short for `files:`
  - a **regex** with named groups `sample` / `rep` / `fraction` (`pattern:`)
  - **`like: isoDTB | TMT | DIA`**, which borrows a built-in rule
- **`date_formats`**: which of six named formats to try, in order.

Choices:
- **The built-in rules are templates too.** These three compile to exactly the
  old regular expressions, character for character (a test pins them):
  - `{sample}_{rep}[_{fraction}]`
  - `{sample}[_TMT][_{fraction}]`
  - `{sample}[_{rep}]`

  With no `naming:` block every name reads as before, and every earlier test
  passes unchanged.
- **Templates before regexes.** A template is what a lab would write on a
  whiteboard. `_` and `-` stay interchangeable, letters match either case, and
  `{rep}` / `{fraction}` keep the R/F prefixes. The regex is there for names a
  template can't describe.
- **D30 holds for every rule:**
  - Template numbers are 1–3 digits.
  - Every rule's numbers are checked 1–999 by value when they are read, so a
    hand-written `\d+` can't mint replicate 1000.
  - A group that captures letters is an error, not a guess.
  - The Xcalibur stamp is stripped and the fraction-set check runs as before.
- **Keywords stay in `methods.<name>.aliases`.** The app's Methods tab edits
  them there, and a second place would drift. A keyword listed under two
  methods is now a config error, because every folder containing it would be
  ambiguous. So is a blank keyword, which would match every folder.
- **`like:` is about names only.** The analysis after FragPipe still branches
  on the method's name (isoDTB sites, TMT annotation, DIA `pg_matrix`), so a
  renamed DIA method gets the generic analysis. The documented way to use
  another word for DIA is to add it to `methods.DIA.aliases`. Carrying `like`
  into the pipeline is left open. (Decided in D54: it carries through.)
- **Mistakes fail at load and name the setting** (`naming.methods.DIA:
  '{rep}_{fraction}' needs {sample} once, outside [ ]`). The watcher then
  keeps its last good config (D34) and the app won't save. Unknown keys under
  `naming:` are errors, as they are under `analysis:`.
- **"Test your names" is one read-only function** (`namecheck.py`). It sits
  behind `ionomos names test <folder> <file.raw> …` and the app's Methods tab
  → **Test names…**. For each name it prints user, method, rule and date, and
  each file's sample, replicate and fraction, or why the file is rejected. It
  uses the same parser and fraction check as intake, so what it says is what a
  drop will do.

The app's config writer keeps `naming.methods` and `naming.date_formats`,
but the app has no editor for them. They are rare, one-off settings, and the
comments in the config file and the check cover them.

### D38 — Every analysed experiment gets an SDRF sample sheet; unknowns stay "not available"
**2026-09-30.** ROADMAP Phase 5A (D36): the sample metadata other labs and
repositories need should come out of Ionomos, not be retyped. Each analysis now
writes `results/sdrf.tsv` in SDRF-Proteomics v1.1.0 (PSI; template
`ms-proteomics`), as an isolated stage whose failure never touches the report.

What it says, and why:
- **One row per raw file, and per label in it.** TMT: a row per file × channel
  (all the files of a plex carry all its channels). isoDTB: a light and a heavy
  row per file sharing the assay name, the spec's pattern for SILAC. PRIDE has
  no isoDTB label, so the rows say `ICAT light` / `ICAT heavy`: isoDTB tags are
  chemical, cysteine-directed, isotope-coded affinity tags like ICAT, and
  `SILAC …` would claim metabolic labelling. The actual isoDTB masses go in
  `comment[modification parameters]` (`NT=isoDTB light;…;MM=561.3387`). Which
  treatment carried which tag isn't recorded anywhere, so both rows carry the
  experiment's condition.
- **The analysis' conditions, all the raw files.** `factor value[condition]` is
  the condition the statistics used (`sample_conditions` applied). Samples left
  out of the analysis are still listed: their raw files belong to the
  experiment and would be deposited with it; `analysis.json` names them.
  A replicate number is never reused within a condition, so a sample moved into
  another condition gets the next free number.
- **Derived where it's reliable, otherwise asked for.** Files, samples and
  replicates come from `ionomos.json`'s manifest; fractions from file names;
  TMT channels from `annotation.txt` (what FragPipe used), then experiment.yaml
  `tmt:`, then names like `DMSO_1_126`. Organism comes from the FASTA's UniProt
  `OS=` when one species holds ≥ 90 % of targets. Enzyme and modifications come
  from the workflow FragPipe copied into its output (MSFragger's enzyme;
  enabled mods mapped to Unimod by mass, unknown masses kept with `MM=`).
  Instrument, organism part, cell type and disease can't be read from anything
  Ionomos has. They come from a new `analysis.sdrf` setting (lab-wide in
  config.yaml, per experiment in experiment.yaml, keys merged). Reading the
  instrument from `.raw` headers was left out: it can't be tested without real
  files. The official validator rejects `not available` for organism,
  instrument, cleavage agent, label and data file, so when those are unknown
  they are listed in `analysis.json` → `sdrf.fill_in` and in the report's
  Methods instead of being guessed. `comment[file uri]` is left out: a path on
  the lab PC isn't a URI anyone can fetch, and PRIDE assigns its own.
- **No SDRF for a table on its own** (`ionomos analyze <table>`, D33). It names
  no raw files, and linking samples to raw files is what an SDRF is for;
  `analysis.json` says so.

`tests/test_sdrf.py` checks the spec's structure rules on every run, and runs
the official validator (`sdrf-pipelines`, `--skip-ontology`) when it is
installed: CI installs it on Linux; it is never a runtime dependency.

### D39 — The watcher can run DIA-NN itself
**2026-09-30.** A lab that searches DIA with DIA-NN alone (no FragPipe)
couldn't use the watcher. A method can now say `engine: diann`.
- `runner.py` picks FragPipe or DIA-NN per method.
- `diann.py` builds the job: the lab's `diann_exe`, FASTA or spectral library,
  and `ionomos_run/diann.cfg`, run as `diann --cfg …`.
- Both engines share `fragpipe.run`'s start / cancel / stop / timeout loop and
  `check_raws()`, so holds, failures, Retry, pop-ups and "never delete an old
  attempt" (`diann_previous_*`) behave the same.

The cfg file, not a long command line, is the job's settings. It is
readable, kept with the results, and one option per line. The price is that
paths can't have spaces; Ionomos already requires that for FragPipe. The
defaults follow DIA-NN's GUI defaults for a tryptic search, and a lab adds
its own with `diann_args`. DIA-NN is never shipped: from 1.9 it can't be
redistributed, and each lab installs its own edition (Academia / Enterprise).
MaxQuant and Sage runners would slot into `runner.py` the same way (ROADMAP
5B). Tested end to end with a fake DIA-NN (`ionomos fake-diann`) that reads
the cfg and fails like the real one on missing inputs.

### D40 — `pip install ionomos` is the analysis; the demo is simulated, offline and writes only new folders
**2026-09-30.** First step of ROADMAP Phase 5A (the plan in D36): a stranger
can `pip install ionomos`, run `ionomos demo`, then `ionomos analyze` on their
own table, on Windows, macOS or Linux (docs/QUICKSTART.md).

- **One package, no split.** The watcher and the app ship in the same wheel.
  Only the window modules import Tk, and the CLI loads them only for commands
  that open windows, so the analysis path never loads Tk (Windows-only calls
  are likewise made inside the functions that need them). A test runs `demo` and `analyze` in a
  Python where `import tkinter` fails, so this can't quietly regress.
- **The demo is simulated until a real anonymised experiment exists**
  (`downstream/simulate.py`). It has three conditions, four replicates, planted
  hits, on/off proteins, and gene sets moved strongly (found by the hit lists)
  or slightly (found only by the rank test), so every section of the report has
  something true to show. Its gene sets are a small bundled `.gmt` of real
  symbols, labelled as simplified. The demo downloads nothing, so it works
  offline and gives the same result every time.
- **The demo never writes into anything that exists.** The default
  `./ionomos_demo` moves on to `_2`, `_3`, ... and a folder given by the user
  must be new or empty. The name has no spaces (the FragPipe rule, although no
  FragPipe runs here).
- **A relative `enrichment_gmt` is read from the experiment folder first**, so
  a folder that carries its own gene sets (the demo, a shared experiment) can
  be moved and re-analysed from anywhere.
- **Publishing is the maintainer's step.** `publish-pypi.yml` builds the
  package on every `v*` tag and installs it into a fresh venv on all three
  platforms. It uploads only after PyPI trusted publishing is configured and
  the `PYPI_PUBLISH` variable is set, so no token is stored and an
  unconfigured repository never fails a release. On 2026-09-30 the name
  `ionomos` was free on PyPI.

### D41 — An instrument setting at the end of a name is part of the sample
**2026-09-30.** The PC inventory has `…_DIA_CV-35.raw`, `…_CV-45.raw` and
`…_CV-55.raw`: one sample acquired at three FAIMS compensation voltages. The
DIA rule read them as one sample, `…_DIA_CV`, with replicates 35, 45 and 55,
and would have tested the settings against each other as replicates. TMT
would have read `_CV-40` as fraction 40, and the short-code rule would read a
glued `CV35` as condition "CV", replicate 35.

Now a trailing `CV` / `FAIMS` / `HCD` / `NCE` / `CID` followed by 2–3 digits
is masked before the file rule reads the name, then restored in the sample
name, for every method (built-in or configured, D37). `HCD33` was already
safe, because only 1–2 letter codes count. Guards:
- 2–3 digits only, since settings are ≥ 10, so `X_CV_1` is still condition
  CV, rep 1.
- The setting must follow some text; a bare `CV-35.raw` reads as before.
- A key listed in `naming.condition_codes` keeps the lab's meaning.

isoDTB requires a replicate, so a name ending in a setting is refused with a
hint instead of guessed. `CE` is left out on purpose: as a condition it is
too plausible.

### D42 — Designs are limma's general linear model: blocks as fixed effects, covariates, a moderated F
**2026-09-30.** ROADMAP 5C #1. FragPipe-Analyst fits `~0 + condition`, so a
batch stays in the residuals. That batch can be:
- a prep day (the replicate number, D35's `BATCH_SUSPECT`)
- a TMT plex
- a patient or a pair

In a simulated three-condition experiment with a replicate batch, the plain
model found none of the 48 planted changes per comparison. Blocking on the
replicate found 14 and 24, with no false ones.

**How a design is given.** It goes under `analysis:` in config.yaml or
experiment.yaml (spec in NAMING_CONVENTION.md):
- `block: replicate`: the replicate number is the block.
- `block: {sample: block}`: a block per sample.
- `block_from: <regex>`: the block is read from the sample names (group
  `block`, else group 1).
- `covariates: {name: {sample: value}}`: numbers become a slope, text a
  factor; SDRF-style names are fine.

**How it is fitted.** The model is `~0 + condition + block + covariates`
(treatment coding, levels in order of appearance). It is fitted the way
limma's lmFit → contrasts.fit → eBayes → topTable does it:
- row by row, with missing values dropped
- QR with lm.fit's limited pivoting (tol 1e-7), so a coefficient a row can't
  estimate is NA
- residual df = observed values − rank
- the same squeezeVar across features as before

With missing values in a non-orthogonal design, a contrast's SD uses limma's
approximation: each row's own coefficient SDs combined with the full design's
correlation. The exact per-row c'(X'X)⁻¹c would differ from limma, and parity
with limma was preferred; the two agree whenever a row is complete.

**Choices:**
- **Fixed effects, not duplicateCorrelation.** This is Smyth's advice for a
  handful of blocks, and it is exact limma. A random block (for blocks that
  cross conditions incompletely) is left for later, as are interactions,
  spline time courses and SDRF as the design source. A numeric time is
  already possible as a linear covariate.
- **The default is untouched.** With no design setting, the comparisons run
  the old code path, so every FragPipe-Analyst golden passes unchanged.
- **A design that can't be used never stops the analysis.** That covers a
  block equal to the condition, a sample without a value, a rank-deficient
  matrix or no residual df. The doctor raises `DESIGN_NOT_USED` (input, a
  pop-up) naming the term. The comparisons then use `~0 + condition`,
  byte-identical to a run without the setting. `BATCH_SUSPECT` now suggests
  `block: replicate`, and says so when it is already used.
- **The moderated F runs for every experiment with 3+ conditions.** It is
  limma's classifyTestsF / topTableF on every condition against the control,
  a basis for all condition differences, with df₂ = d0 + df as limma. It asks
  whether a feature changes anywhere, so it applies no fold-change cut-off.
  It is reported as the `F` / `F_p` / `F_p_adj` columns of
  `<level>_results.tsv`, `analysis.json` → `f_test`, an "Any change (F)" tile
  and a table column.
- **FragPipe-Analyst can't repeat a blocked model.** `reproduce_in_R.R` says
  it repeats the plain model. `reproduce_design_in_R.R` repeats Ionomos's
  model in plain limma, on the values Ionomos tested. A dev test runs it when
  R is available: identical to 1e-6, the TSV precision.

**Checked** against limma 3.68.5 (`tests/test_design.py`,
`tests/golden/design/`): 9,600 values, the worst relative difference
8.5e-11. The cases:
- a replicate block, with complete data and with missing values (a condition
  absent, a block unestimable in a row)
- block + numeric + factor covariates
- one-vs-others with a block
- topTableF for the blocked, covariate and plain models


### D43 — DEqMS is an optional variance prior, ported exactly
**2026-09-30.** ROADMAP 5C #4. limma gives every protein the same prior
variance, but a protein quantified from one peptide is noisier than one
quantified from twenty. DEqMS (Zhu et al., Mol. Cell. Proteomics 2020) fits
log s² against log2 of the peptide count and uses the fitted value as each
protein's prior. `variance_prior: deqms` turns it on. The counts are the ones
the loaders already read (D36): DIA-NN `N.Sequences`, FragPipe and MaxQuant
peptides, TMT-Integrator PSMs.

This is a port of DEqMS 1.30 `spectraCounteBayes(fit.method = "loess")`:
- the loess of log s² on log2(count)
- the digamma / trigamma bias correction
- the prior df from its 0.1-step grid search
- post df = d0 + df, uncapped, unlike limma

It needs R's `loess` exactly, which is not a plain local regression: dloess
builds a k-d tree of cells with ≤ floor(n·span·0.2) points (ties go to one
side), fits a quadratic (value and slope) at each cell vertex, and
interpolates with cubic Hermite polynomials. `deqms.loess` reproduces it for
one predictor, to 1e-12 against R on tied and continuous data. DEqMS
t-statistics and p-values agree with the package to 1e-10 on the blocked
and plain fits, with and without missing values.

**Deviations, where DEqMS would stop or misbehave:**
- A feature without a count (or with 0, or with no residual df) keeps limma's
  prior. DEqMS stops on a missing count.
- With fewer than 20 usable features or fewer than 3 distinct counts, limma's
  prior is used for all, with the warning `DEQMS_NOT_USED`. So does a table
  without counts.
- The count is whatever the table reports for the protein, usually counted
  over the whole experiment. DEqMS' vignette suggests the minimum across
  samples. The trend is fitted on whichever is given.

**When it helps:** many proteins with few peptides and a clear dependence of
variance on count (DDA label-free, DIA with a wide range of counts, TMT with
PSM counts). DEqMS ranks those better than one prior.

**Known conservativeness:** one- and two-peptide proteins get a larger prior
variance, so they are called less often, including real changes. DEqMS'
moment-matched d0 is often larger than limma's (10.8 against 3.8 on the golden
data), which shrinks every variance harder towards the trend. Without a trend
(the same count everywhere), DEqMS is just a different estimate of the
limma prior. It stays opt-in.

The F-test with DEqMS uses DEqMS' posterior variances and df. DEqMS has no F,
so that combination has no R reference.


### D44 — Titrations get CurveCurator's dose-response curves, ported and checked against CurveCurator
**2026-09-30.** ROADMAP 5C #2. Chemoproteomics and drug labs titrate a
compound (DMSO + several concentrations); a volcano per dose answers the
wrong question. When the conditions are doses, the analysis now fits a curve
per feature (`downstream/doseresponse.py`, an isolated stage like the others)
and writes `results/dose_response.tsv`, an `analysis.json` `dose_response`
block and a report section.

- **CurveCurator's statistics, not a new method.** CurveCurator (Bayer et al.,
  *Nat. Commun.* 2023; Apache-2.0, compatible with GPL-3) is the published,
  calibrated answer to "is this curve real": a 4-parameter log-logistic fit
  within its bounds, the mean model as the null, the recalibrated F-statistic
  (n/k scaling, F(5, dfd) with loc 0.12), the curve fold change, and the
  SAM-style relevance score with s0 from alpha and fc_lim. Up / down / not
  follow its rules; its blank class is shown as "unclear". Defaults are its
  decryptM settings (alpha 0.05, fc_lim 0.45; `dose_alpha`, `dose_fc_lim`).
- **Same starts, a different local optimiser.** CurveCurator's "standard" fit
  (its default) refines the best of its alternative guesses from slopes 0.01,
  1 and 10 with scipy's L-BFGS-B. Ionomos uses the same guesses and slopes
  with a bounded Levenberg-Marquardt in pure Python, so the base install
  stays numpy-free. Against CurveCurator 0.6.0 on 600 simulated curves (two
  designs; `tests/golden/dose_response/`): 600/600 classes identical, pEC50
  within 0.05 for 263 of the 266 regulated curves, and median F and
  log10 p differences below 1e-3. In the three exceptions Ionomos found a
  lower sum of squares (flat valleys where pEC50 is poorly determined).
  About 5–10 ms per feature.
- **Added to CurveCurator:** a BH q-value on the curve p-values
  (CurveCurator's own q-values need its decoy simulation, which is not
  ported) and a 95% interval for pEC50 (its Jacobian standard error × t with
  n − 4 df). The interval is approximate, and the report says a pEC50 is only
  worth reading for up / down curves.
- **Doses from names, or from `analysis.doses`.** A condition name holding
  one concentration (`Cmpd_10nM`, `Cmpd_0p1uM`, `10 µM`) is a dose of the
  compound named by the rest, so two compounds in one experiment get two
  series sharing the control (`find_control`, dose 0). Units are required:
  a bare number is refused unless `dose_unit` says what it means, because
  a wrong unit silently shifts every EC50 by 1000×. A name with two doses (a
  combination) is left out with a warning; a configured dose naming a
  missing condition is an `input` issue (a window asks).
- **Only real titrations.** A compound needs `dose_min_doses` (4) doses above
  zero; fewer gives a note and the section explains why. A two-condition
  experiment shows nothing new. Intensities are divided by the mean of the
  control samples, measured values only (no imputation: an imputed low value
  would invent a curve), after Ionomos' filters and median normalisation.
  isoDTB ratios are already to DMSO: the control point is ratio 1, or a
  control condition re-centres them when there is one.
- **The report draws, never refits.** The page carries each curve's
  parameters and its measured ratios, and draws the fitted curve over the
  points (log dose, control at the left), a potency vs effect scatter
  coloured by class, and a sortable, filterable table; the report search
  rings matching curves.

Not ported: CurveCurator's decoy FDR, MAD outlier analysis, imputation,
interpolation points, MLE fits, fixed parameters and weights. They can come
later behind settings if a lab needs them. Open: median normalisation assumes
most proteins don't move; a compound that changes a large share of the
proteome at high doses would bias the flat curves (seen in simulation with
40% responders), and CurveCurator's own normalisation is off by default.


### D45 — The QC standard is trended from the searches Ionomos already runs
**2026-09-30.** ROADMAP 5C #6. Labs inject a QC standard (a HeLa or K562
digest) on a schedule to watch the LC and the mass spectrometer. The numbers
usually end up in a spreadsheet, if anywhere. Ionomos files and searches every
one of those runs, so it now trends them (`qctrend.py`, docs/QC_TREND.md).

- **Recognised by name, not by a new folder.** A run counts when its folder
  or `.raw` name contains a `qc_trend.match` word, or when its method is in
  `qc_trend.methods`. The default words are `hela`, `k562`, `qc_std`, `qcstd`
  and `_qc_`. `_ - .` and spaces are one separator, so `_qc_` means QC as a
  word. HeLa is also a cell line people experiment on, so a folder with two
  or more samples of two or more replicates each is an experiment, not a
  standard. `exclude` covers the rest. Trending is on by default because it
  is inert until a matching run is searched, and it never changes what
  happens to the job.
- **Numbers from the engines' own tables.** Every number is read from what
  the search wrote:
  - DIA-NN's per-run `stats.tsv`: IDs, total quantity, FWHM, and median
    MS1/MS2 mass accuracy before its recalibration, so instrument drift shows
  - the `pg_matrix`
  - FragPipe's `psm.tsv` and `combined_protein.tsv`

  Nothing is recomputed from spectra, and no raw-file reader is needed. The
  DDA mass error is Observed vs Calculated Peptide Mass, isotope-error
  corrected; more than 50 ppm counts as a mass offset and is ignored.
  Tables are streamed and size-capped, and a broken one leaves a note on the
  run. RT drift uses the standard's own 200 most intense peptides against
  their baseline RTs (median over ≥ 5 shared peptides), so no iRT spike-in is
  needed. Acquisition time comes from the Xcalibur stamp in the name, else
  the raw file's modification time (intake keeps it), and the row records
  which.
- **One store per lab, next to the ledger.** `<log_dir>/qc_trend.jsonl`
  (names.py) holds one JSON object per line and is appended. The last line
  for a run (experiment folder + run name) wins when read, so a re-run
  updates its row, and the file is compacted when mostly superseded. JSON
  lines beat a second SQLite file here: they are readable, need no schema,
  and survive a half-written last line. Experiment folders are only read.
  `--rebuild` merges what it finds and keeps rows for runs no longer on
  disk, for example experiments archived elsewhere.
- **Classic laboratory QC, not a model.** Each series (instrument · method ·
  standard · amount) has a baseline: the first `baseline_runs` (10), or the
  runs between two pinned dates, for example after a column change. It gives
  each metric's mean and SD, with the SD floored at 2 % of the mean for
  counts so near-identical baseline runs can't turn noise into alarms.
  Later runs get:
  - Levey-Jennings z-scores
  - the Westgard rules 1-3s, 2-2s, R-4s and 10-x, with 1-2s as a warning
  - a tabular CUSUM (k = 0.5, h = 5 SD) for the slow drift single-run rules
    miss

  The CUSUM starts after the baseline, clips each z at ±3 and restarts after
  a run another rule rejected. Without that, one failed injection read as
  "drift" for weeks, which a test pins. These are the rules every clinical
  and proteomics core already knows, so a verdict can be checked by hand.
- **Direction matters.** Fewer IDs, less signal, broader peaks and more
  missed cleavages are problems; any shift in mass error, RT or charge is a
  problem either way; R-4s (imprecision) is always one. More IDs after a new
  column is "watch", with a hint to pin a new baseline, not a warning.
- **A warning, not a pop-up.** A broken rule on a series' newest run raises
  an attention item (kind `qc_trend`, severity warning) with the likely
  causes. The next run back within the baseline closes it. The instrument
  still works, and nobody's experiment is blocked, so no window interrupts
  whoever is at the PC. `qc_trend.popup: true` makes it one, like a failed
  search.
- **The page is static.** `<log_dir>/qc_trend.html` is SVG drawn in Python
  with `<title>` tooltips and the report's stylesheet inlined. It has no
  script, so it needs no JS harness and opens anywhere. It is rewritten after
  each QC run and by `ionomos qc-trend`; the app's Jobs tab opens it.
- **Isolated.** `postprocess.run_all` calls `qctrend.after_job` after the
  analysis, even when the analysis crashed. `after_job` never raises, and its
  verdicts go in the job's `ionomos.json` (`results.qc_trend`).

Left open: TMT QC runs, the RT shift from DIA-NN 2.x `report.parquet`,
telling instruments apart within one config, and tuning the SD floor and
CUSUM h on real QC data.


### D46 — Help for users comes from one Markdown source, shown in every report, a help page and the terminal
**2026-09-30.** The people who read the reports and drop the folders are lab
members, not bioinformaticians. What each chart shows, what "adjusted p" or
"imputed" means, and what to do about `BATCH_SUSPECT` or a `.REJECTED.txt`
lived in the docs folder, the doctor's one-line causes and the app's setup
text. Nobody at the PC reads those.

The help is now one set of plain Markdown files in `ionomos/help/`: getting
started, reading the report, a glossary, troubleshooting, what Ionomos never
does to your data, and questions (docs/HELP.md). Each topic is a
`## Title {#id}` entry. The id's namespace says where it is used (`report.`,
`qc.`, `glossary.`, `issue.<CODE>`, `attention.<kind>`, `intake.<kind>`, …).
It is shown in three places:
- **Every report** embeds the report and QC entries, the glossary, the entries
  for the issues it found, and every entry those link to (about 40 KB). A
  **?** beside each section title, QC tab, the cut-offs and each issue box
  opens the text in place, and a Help section at the end lists it all. It
  works offline, like the rest of the page.
- **`help.html`**: everything, self-contained, with a search box. The app's
  Help button, each pop-up's **More help** (opened at the topic that explains
  that item) and `ionomos help --open` all write it to `<log_dir>/help/` and
  open it in the browser.
- **`ionomos help TOPIC`** prints one topic (an issue code, a word, a
  section) as plain text.

Why these choices:
- **Markdown, not a Python or YAML structure.** The content is prose that
  the maintainer and lab members edit. It reads as-is on GitHub and diffs
  cleanly. A small renderer in the standard library handles the subset the
  help needs (paragraphs, lists, bold, italic, code, links). It escapes all
  text before applying the markup and turns only `#id` and `https://` targets
  into links, so no content can inject a tag or a `javascript:` URL. The price
  is two packaging entries (`pyproject.toml` package-data, the PyInstaller
  spec), which a test checks.
- **Rendered in Python, drawn in JS.** The report gets ready, escaped HTML
  per entry, so report.js only places it. The only data-derived text in the
  help (issue titles and codes) goes through `esc()` there. A failure while
  building the help leaves it out of the report; the report is still made.
- **Coverage is tested, not remembered.** Tests read the code: every
  `Issue("CODE")`, every attention kind, every intake rejection kind and every
  `raise Hold(…)` reason must have an entry, so a new code can't ship
  unexplained. The FragPipe failure causes on the help page come from
  `fragpipe.EXPLANATIONS` itself.
- **The "never do" page states only what the code does**, checked against
  intake, the runners and D17 / D29 / D33 / D40. One thing it says plainly
  because it could surprise someone: `results/` belongs to Ionomos, so a
  re-analysis replaces the report in it.

The app's Help tab keeps its setup text for whoever runs the PC, with a
button to the full help above it. The help is English only; translations would
be one file set per language, and nobody has asked yet.


### D47 — An SDRF in the folder is the design; only sample_conditions beat it
**2026-09-30.** ROADMAP 5B #3 (design import) and 5A (SDRF). A lab that
already has its samples in SDRF-Proteomics (for PRIDE, or from quantms)
shouldn't have to type the conditions again. `downstream/sdrfdesign.py` reads a
`*.sdrf.tsv` / `sdrf.tsv` from the experiment folder (two levels deep) or from
the folder of the table given to `ionomos analyze`. It runs inside
`load_quantities`, so the Analysis tab shows the same conditions the analysis
uses.

Choices:
- **Precedence**, highest first:
  1. `sample_conditions` (the Analysis tab / experiment.yaml)
  2. the SDRF
  3. the engine's own condition column (Spectronaut, MSstats, MSstatsTMT,
     Proteome Discoverer)
  4. the `ionomos.json` manifest
  5. the names

  The SDRF sits above the engine because it is the document the lab wrote
  about its samples. The engine's columns are usually typed in from the same
  sheet, or generated from it (quantms). A person's explicit correction always
  wins. `analysis.json` → `design` records which source was used and which
  samples `sample_conditions` overrode.
- **Matching** is by `comment[data file]` stem, with the manifest's rules:
  exact, then FragPipe's `_calibrated` / `_uncalibrated` suffix, then a
  unique Xcalibur stamp. The helper moved to `quant.match_run_stem`, so both
  use one. TMT matches a plex's raw files plus `comment[label]`. Files
  carrying the same channel → source map are one plex, so fractions and
  technical replicates group without a plex column (the spec has none).
  Otherwise the sample name must equal a source or assay name.
- **Sample names don't change**; only condition and replicate do. Renaming
  them to `<condition>_<rep>`, as the manifest does, would invalidate
  `sample_conditions` keys written before the SDRF was added.
- **Several factor columns are joined** with ` | `. The quantms examples
  repeat `factor value[spiked compound]` four times. `analysis.sdrf_factor`
  picks columns instead; an unknown name is a note, not an error.
- **`results/` is output.** It is never searched, and neither are old runs
  or `<table>_ionomos/`. A copy of Ionomos' own SDRF that a person puts in the
  experiment folder is read, since that is a deliberate act.
- **Runs the SDRF doesn't name** become `SDRF_UNMATCHED_RUNS` (input, like
  `UNMATCHED_RUNS`). An SDRF that names none of the runs is not used, and the
  report says so. Technical replicates stay separate samples, and label-free
  fractions stay separate columns, each with a note: the engines combine
  fractions before Ionomos sees them.


### D48 — TMT plexes are joined by IRS before the processing; nothing is normalised twice
**2026-09-30.** ROADMAP 5C #5, and the MSstatsTMT import from 5B #3. Between
TMT plexes the same protein's level moves with the peptides each plex happened
to sample, so a PCA of several plexes separates them by plex. A bridge
(reference) channel present in every plex puts them on one scale.

- **IRS as in Plubell et al. 2017** (`downstream/plex.py`). Per protein and
  plex, the reference is the log2 linear mean of the plex's reference
  channels. The target is their mean across plexes (the geometric mean). Every
  channel of the plex is shifted by the difference. The reference channels
  then leave the matrix, as MSstatsTMT removes its Norm channels. A plex
  without a reference value for a protein gets missing values for it, rather
  than unscaled ones.
- **Where it runs**: an isolated `plex` stage between reading and
  `fpa.process`, not inside it. Removing the reference channels changes the
  samples, and fpa.process's before-filter matrices must keep their shape. It
  also leaves fpa.py (where the limma model is changing) untouched. Plubell's
  order is SL → IRS. In log2 both are additive shifts, so IRS followed by the
  median centring equals SL → IRS → centring up to a constant per sample,
  which the centring removes. This also holds for `gn`, whose MAD scaling is
  shift-invariant.
- **Reference channels**, in order:
  1. `analysis.tmt_reference`: a channel (e.g. `126`) or a sample name.
     experiment.yaml `tmt.reference_channel` is the same setting, and the
     `tmt:` block now allows it without `channels`, for engines FragPipe
     didn't run.
  2. The SDRF's pooled rows.
  3. Names: pool, pooled, bridge, reference, norm.
- **Without a reference**: pwilmart's notebooks use each plex's own sum. That
  is valid only when every plex holds the same mix of samples; otherwise it
  scales real biology away. So `irs: auto` uses it only for such balanced
  designs (conditions after `sample_conditions`, left-out samples ignored).
  Otherwise it leaves the plexes alone and warns
  (`TMT_PLEXES_NOT_NORMALISED`). That is a warning, not a pop-up, since the
  data may still be usable with a blocking design. `irs: sum` forces the plex
  means; `none` switches IRS off.
- **Never twice**: TMT-Integrator abundances are already ratios to the
  reference or virtual reference (`meta["ratio_to_reference"]`), so they are
  never scaled. That is recorded, and noted when plexes are known or IRS was
  asked for.
- **MSstatsTMT format is summarised as MSstatsTMT does it**
  (`engines.msstats_tmt_summary`). It is transcribed from MSstatsTMT 2.20 /
  MSstatsConvert 1.22 and identical to them to 1e-9 on
  `tests/golden/msstatstmt/`:
  - fractions of a Mixture × TechRepMixture combined as the converters do
    (largest mean, then sum, then max intensity)
  - global median normalisation of every run × channel
  - Tukey median polish per protein and run
  - Norm-channel normalisation between runs (the median of the runs' Norm
    means)

  Simplifications:
  - `method = "MedianPolish"`, not the default `"msstats"`. The latter needs
    MSstats' AFT model to impute censored values; here, missing stays missing.
  - When a feature has several PSMs in a run, the one with the largest total
    intensity is kept. The converters do something similar, and MSstatsTMT
    format files have usually been through them already.
  - Technical-replicate mixtures are averaged into one sample per channel.
    MSstatsTMT models them as a random effect instead.

  The loader applies MSstatsTMT's normalisation and marks it, so the IRS
  stage leaves it alone.
- **Plexes from each engine**:
  - MaxQuant `Reporter intensity corrected N <experiment>`. The totals over
    experiments are dropped, and `summary.txt` gives each experiment's raw
    files for SDRF matching.
  - MSstatsTMT `Mixture`.
  - Proteome Discoverer's file `F1`.
  - SDRF file groups.
- **The report** gets `plex` per sample and the PCA of the same data before
  IRS. The PCA can colour by plex and switch between before and after. The
  change to report.js is small and local.


### D49 — The assistant on the PC is local, grounded, and can only propose
**2026-09-30.** The owner wants a local AI that helps lab members troubleshoot
and work with their data in plain language. The plan is ROADMAP Phase 6,
written but not built. The decisions it fixes up front:
1. **Ionomos speaks the OpenAI-compatible chat API to a runtime the lab
   installs** (Ollama by default, llama.cpp `llama-server` for a PC with no
   internet). It never bundles a runtime or model weights, as with DIA-NN and
   MaxQuant.
2. **Tools, never a shell.** A small set of read-only wrappers over existing
   functions, plus proposals. Any change goes through a native dialog built
   from the structured arguments, and the user clicks Confirm. Chat text can
   never trigger an action.
3. **Every claim cites** an issue code, log line, help anchor or analysis
   field, and Ionomos checks the citation exists before showing the answer.
   Without one, the assistant falls back to the doctor text.
4. **Everything it reads is treated as untrusted:** names, logs,
   experiment.yaml.
5. **Local only by default.** A cloud model needs an admin flag, a visible
   banner, and a preview of exactly what would be sent (never data files).

The model is chosen by a measured scorecard on the PC (Phase 6.0), not by
leaderboards: CPU-only generation is memory-bound, and the model shares RAM
with FragPipe.


### D50 — The watcher can run MaxQuant; its mqpar is always the installed version's
**2026-09-30.** MaxQuant is the most widely used free search engine for DDA
label-free work, so a DDA method can now say `engine: maxquant`
(`maxquant.py`, through `runner.py` as for DIA-NN, D39). `mqpar.xml` changes
from version to version, so a shipped template would silently break with the
next MaxQuant. The job instead starts from the lab's own saved parameters
(`mqpar:`) or from `MaxQuantCmd --create`, the installed version's own
defaults, with label-free quantification switched on. Ionomos replaces only
the job-specific lists and paths. Experiments are named
`<condition>_<replicate>`, so `proteinGroups.txt` comes back as
`LFQ intensity DMSO_1` …, which the engines importer and the analysis read
directly. The manifest has no fraction column, so fractions come from the
file names by the lab's naming rules, else file order. A single-shot sample
gets MaxQuant's 32767.

The analysis of a MaxQuant job reads `proteinGroups.txt` whatever the lab
calls the method (`postprocess.prepare`). Tested end to end with
`ionomos fake-maxquant`; not yet against a real MaxQuant.



### D51 — The watcher can run Sage; conversion and search are one job, and its telemetry is off
**2026-09-30.** Sage (MIT) is the one search engine a lab can use with no
licence at all, which makes it the hedge against FragPipe's academic-only
terms (ROADMAP Risks). A DDA method can now say `engine: sage` (`sage.py`,
through `runner.py` as for DIA-NN and MaxQuant, D39 / D50).

1. **Ionomos does not bundle Sage or ThermoRawFileParser.** Sage's licence
   would allow it, but ThermoRawFileParser carries Thermo's RawFileReader
   licence, and a bundled engine would have to be updated with every Ionomos
   release. The lab downloads both and points `sage_exe` / `raw_converter`
   at them, as for the other engines. `sage` is never taken from the PATH,
   where it is usually SageMath.
2. **One process for two steps.** Sage reads mzML, so each `.raw` is first
   converted. The worker starts `ionomos sage-job ionomos_run/sage_job.json`,
   which runs the converter per file and then Sage. The shared run loop
   therefore still sees one process tree: one console log, and cancel, stop
   and timeout kill whichever step is running.
3. **Converted files are Ionomos' own and are kept.** They go to
   `<experiment>/sage_mzml/`, written to `converting/` first and moved into
   place when complete, so a cancelled conversion is never taken for a
   finished one. A retry reuses them. The raw files are not touched.
4. **Settings: the lab's JSON, or defaults Ionomos writes.** Unlike
   `mqpar.xml` (D50), Sage's JSON is documented, and every key it lacks takes
   Sage's default, so a small default file (tryptic, high-resolution MS2,
   carbamidomethyl C, oxidised M, label-free) is safe across versions. Only
   the FASTA, mzML paths and output folder are replaced in a lab file;
   label-free quantification is switched on because the analysis needs
   `lfq.tsv`. A lab file that asks for TMT holds the job: there is no protein
   roll-up for Sage's `tmt.tsv` yet.
5. **Telemetry off.** Sage posts usage statistics after each search unless
   told not to. A lab PC's activity should not leave it unasked, so the job
   passes Sage's own switch whenever `sage --help` lists it, and logs which
   happened. An older Sage without the switch still runs.
6. **Protein roll-up of `lfq.tsv`** (`engines.load_sage`): peptide q ≤ 1%
   and, with `results.sage.tsv`, the peptide's best protein q ≤ 1%; proteins
   with the same peptides are one group and shared peptides go to the group
   with the most peptides (razor); fractions of a sample are added; the
   protein quantity is the Tukey median polish of its ions, as for
   MSstats-format input. MaxLFQ would be the alternative; the median polish
   is already in the code and checked, and the roll-up can change without
   touching the runner.

Tested end to end with stand-ins (`ionomos fake-sage`, `fake-rawparser`);
the formats come from Sage's source (0.14 / 0.15), not yet from a real run.


### D52 — Liganded cysteines are called by the field's rule, with the lab's thresholds as settings
**2026-09-30.** Chemoproteomics is the niche Ionomos is for (ROADMAP
positioning), and until now an isoDTB report only tested each site's ratio
against 0. That answers "is the ratio different from 1", not the question
the field asks: which cysteines does the compound engage. `downstream/cys.py`
adds the convention used in isoTOP-ABPP / isoDTB work.

1. **The rule is a threshold on replicates, not a test.** A site is liganded
   when its competition ratio R reaches `liganded_ratio` (4) in at least
   `liganded_min_replicates` (2) replicates. Sites reaching it in fewer are
   *inconsistent*, and sites measured in fewer replicates than the rule
   needs are not assessed. The moderated test stays in the Differential
   section; the two are shown side by side and the report says they answer
   different questions.
2. **Built before the lab confirmed its thresholds**, at the maintainer's
   request, so every part of the rule is a setting, lab-wide or per
   experiment. The defaults are the common R ≥ 4 in 2 of 3.
3. **The ratio's direction is a setting with a check.** FragPipe gives
   log2 heavy / light. Which tag the treated sample carries is the lab's
   choice, so `liganded_direction` is `high` (R = heavy / light) or `low`.
   When far more sites would be liganded the other way round, the doctor
   warns (`LIGANDED_DIRECTION`) rather than switching by itself.
4. **Measured ratios only.** No normalisation and no imputation: a liganded
   call must never rest on a made-up value.
5. **Selectivity needs evidence on both sides.** A site is *selective* only
   when every other compound was measured as not liganding it; if they
   weren't measured well enough it is *unresolved*.
6. **CysDB is not bundled.** The lab downloads the table; Ionomos reads any
   site table with a recognisable key (`site_annotation`), so it also works
   with a lab's own list. The column layout was taken from the CysDB paper
   (identifier `UniProtKBID_C#`; identified / hyperreactive / ligandable),
   not from a real download.
7. **Left out: the protein-abundance correction** (MSstatsPTM). It needs a
   matching unenriched proteome per condition, and where that comes from is
   a question for the lab. The per-protein view ("most of this protein's
   cysteines are liganded") is the warning available without it.


### D53 — Time courses are tested with time as a factor, on the comparisons' own model
**2026-09-30.** D42 left time courses out of the design work ("a numeric
time is already possible as a linear covariate"). A covariate answers
nothing about which proteins change over time, so `downstream/timecourse.py`
adds the tests.

1. **Time is a factor, not a curve.** With the three to six time points and
   three replicates a proteomics time course usually has, a spline has
   nothing to smooth. limma's guide treats such designs as groups and asks
   questions with contrasts (User's Guide 9.6.1); so does Ionomos. The model
   is the one the comparisons already use (`~0 + condition`, plus block and
   covariates, with the same variance prior), so nothing is fitted twice and
   the pairwise results are untouched.
2. **Three questions.** Change over time: the moderated F on every time
   point against the first. Trend: the moderated t on the linear contrast
   over the *order* of the time points (hours would let one long gap carry
   the test). Against a control series: the moderated F on the interaction
   contrasts.
3. **Found from the names, like doses (D44).** `Drug_4h` is 4 h of series
   `Drug`; `analysis.times` overrides. An untimed control is time 0. Three
   time points are the minimum: two are an ordinary comparison.
4. **"Changing" uses the report's own cut-offs** (`alpha` on the F's BH
   q-value, `log2fc` on the largest change from the first time point), so
   one experiment has one definition of a hit.
5. **Patterns are descriptive and repeatable.** k-means on each changing
   feature's profile scaled to its largest change, k growing with the number
   of features (at most 6), started from the strongest feature and then the
   farthest ones: no random seed, the same patterns on every run. They are
   a way to browse, not a result to report as statistics.
6. **Left out:** spline fits for long series, and the Welch / Student tests
   and ratio data (they have no shared model to take contrasts from).

**Checked** against limma 3.68.5 (`tests/golden/timecourse/`): the F, its
p and adjusted p, the fold changes, the trend t and p and the interaction F,
for the plain model, a replicate block and data with missing values, to
1e-8.


### D54 — `like:` makes a method that method everywhere; one resolver says what a key behaves as
**2026-10-01.** D37 left `naming.methods.<X>: {like: DIA}` as a rule for
names only, so a lab's `DIA_phospho` or `TMTpro` method was searched with its
own workflow and then analysed as generic label-free: no TMT annotation, no
site tables, a control asked for where there is none. A second DIA or TMT
workflow under its own key is an ordinary thing to want, so `like:` now
carries through.

1. **A method has a kind.** `naming.method_kind()` returns it, and is the
   only place that decides:
   - the engine first: `engine: diann` is DIA, `engine: maxquant` and
     `engine: sage` are label-free (`LFQ`)
   - else the `like:` target (`isoDTB`, `TMT` or `DIA`)
   - else the key itself. A key that is not a built-in method is a method of
     the lab's own: label-free, read from `combined_protein.tsv`, as before.

   `naming.analysis_method()` is the same with MaxQuant's and Sage's own
   tables for those engines. `Config.kind()` and `Config.analysis_method()`
   wrap them.
2. **Everything that branched on the key asks for the kind.** FragPipe's
   expected outputs and the TMT `annotation.txt` (`fragpipe.py`); the review
   window's control and summary (`resolve.py`, through `Draft.kinds`); the
   analysis, with statistics on or off (`postprocess.py`). The analysis
   itself (`downstream/`) is unchanged: it is handed the kind, so its
   loaders, the SDRF and the doctor's messages need no second lookup.
3. **The lab's name stays where a person reads it.** The job, its
   `<key>.workflow` copy, the ledger, the QC trend's series and the report's
   header keep the key. `analysis.json` → `method` is the kind, as it already
   was the engine's table for MaxQuant and Sage.
4. **The engine beats `like:`.** docs/ENGINES.md used `LFQ: {like: isoDTB}`
   to give a MaxQuant or Sage method the `<sample>_<rep>_<fraction>` names.
   Those configs keep working as label-free: the engine decides. The docs
   now write the template out, which says what is meant.
5. **`ionomos.json` records `method_config.analysis_method`,** so a folder
   analysed without its lab's config (`ionomos analyze <folder>` elsewhere)
   is still read as it was there. Older folders have no such entry and
   behave as before.
6. **`ionomos analyze --method` takes the config's own keys** besides the
   built-in names, and `ionomos names test` prints what a custom method is
   run as.

**What changes for an existing config:** nothing for the built-in keys, and
nothing for a config without `like:`. A FragPipe method that used `like:`
only to borrow a rule's shape for a different kind of experiment (say a
label-free method with `like: isoDTB`) is now analysed as isoDTB; it should
write the template out instead (docs/NAMING_CONVENTION.md). A DIA-NN method
under a key other than `DIA` used to get no analysis and now gets DIA's.

**Left as it was:** the SDRF reads fractions with the built-in rule of the
kind, not the lab's own template; a method cannot be `like:` another custom
method; the QC trend's `methods:` list and series names use the lab's keys.

**Checked** end to end with the testbed's fake FragPipe, DIA-NN and MaxQuant
(`tests/test_method_kinds.py`): a custom key like each built-in gives the
same analysis, SDRF, annotation, control handling and doctor messages as the
built-in method. The FragPipe-Analyst and R goldens pass unchanged.


### D55 — Search quality per run comes from the QC trend's reader, with two wide warnings
**2026-10-01.** D35 left PSM-level QC for later. The report's QC tabs judge
samples from the quantities; none of them says how the search went for each
raw file. `downstream/psmqc.py` adds that, as a **Search quality** QC tab,
`results/psm_qc.tsv` and `analysis.json` → `psm_qc`.

1. **One reader.** `qcmetrics.read_psm` (D45) already streamed `psm.tsv`
   for the instrument QC trend. It now also keeps, per run, the quantiles of
   the mass error and the PSM counts by missed cleavages, charge and peptide
   length. `psmqc.py` only arranges those numbers. The trend's own metrics
   are unchanged.
2. **Every run, no manifest.** The trend matches runs against the
   experiment's file list. The report takes every run the tables name
   (`qcmetrics.search_tables`), so it also works for a folder analysed
   without an `ionomos.json`. DIA-NN's big `report.tsv` is not opened.
3. **A run is a raw file.** For TMT that is a fraction of a plex, not a
   sample. The folder the `psm.tsv` is in (FragPipe's experiment) is shown
   beside it as the sample.
4. **Mass error is the uncalibrated one** (`Observed Mass` against
   `Calculated Peptide Mass`, isotope-error corrected, as in D45), because
   the question is the instrument's calibration, not what the search made
   of it. Errors over 50 ppm are mass offsets and are left out.
5. **Spread is shown as quartiles**, with the 5th and 95th percentile in the
   chart, not as a standard deviation: a few wrong matches would dominate it.
6. **Two warnings, both wide.** `PSM_MASS_ERROR`: a run's median error is
   10 ppm or more from 0. `PSM_MISSED_CLEAVAGES`: half or more of a run's
   PSMs have a missed cleavage. A run needs 100 PSMs to be judged. These are
   not the lab's limits: they were chosen so that only a run nobody would
   call normal is flagged, and they are constants in `psmqc.py` until the lab
   has seen its own numbers. Both are notes (no pop-up) and are raised even
   when there is no quant table, because they may be why.
7. **Left out:** a warning for a run with few PSMs (TMT fractions differ by
   design, and `LOW_SAMPLE` covers samples); a warning for a run unlike the
   others (it needs the lab's numbers first); warnings on DIA-NN's summary,
   which is shown as DIA-NN reports it; settings for the limits.
8. **Size.** Files are read row by row; one over 4,096 MB (the trend's
   default) is not read and the report says so. A simulated 90 MB file with
   400,000 PSMs took about 2 s on the development Mac; the lab PC was not
   timed. `psm_qc: false` switches the step off.

**Not checked on real data.** The `psm.tsv` column names are from the
FragPipe documentation, as in the testbed's fake FragPipe. The tests use
tables with those names and planted values.


### D56 — Sage TMT is summarised as MSstatsTMT input is; plexes are left to IRS
**2026-10-01.** D51 held a Sage job whose settings asked for TMT, because
`tmt.tsv` holds reporter ions per spectrum and nothing rolled them up.
ROADMAP 5B #5 listed it as still to do.

1. **The join.** `tmt.tsv` has a row per MS2 / MS3 spectrum (`filename`,
   `scannr`, `ion_injection_time`, then a column per reporter);
   `results.sage.tsv` has the PSMs. They are joined on (filename, scannr):
   for MS3 quantification Sage writes the MS2 scan's id there. Both files
   are read line by line, since both have a row per spectrum.
2. **Which PSMs.** Targets (`label` 1) of `rank` 1 with `spectrum_q`,
   `peptide_q` and `protein_q` ≤ 1%. A spectrum with more than one such PSM
   (a chimeric search) is left out, since its reporter ions belong to both
   peptides. A reporter intensity of 0 (no peak) is missing.
3. **No new summary.** The PSMs go through the summary the MSstatsTMT
   importer uses (`engines._tmt_summarise`, checked against MSstatsTMT 2.20
   in D48): one PSM per peptide ion and file, fractions of a plex combined,
   global median normalisation, Tukey median polish per plex. Proteins are
   the razor groups of D51. A test checks that Sage input and the same PSMs
   as MSstatsTMT rows give the same numbers.
4. **Between plexes: IRS, not MSstatsTMT's reference normalisation.** Sage
   records no `Norm` condition, so the loader stops after the median polish
   and `plex.py` joins the plexes on the reference channel
   (`tmt.reference_channel`, the SDRF, or a channel named pool), as for
   MaxQuant and Proteome Discoverer (D48).
5. **Channels.** Sage names the columns `tmt_1 … tmt_n` in the kit's order,
   so they map through `plex.TMT_ORDERS`. A custom list of reporter masses
   (`User`, columns `user_1 …`) keeps Sage's names.
6. **Plexes.** With the watcher's manifest, a file's experiment is its
   plex, and the files of a plex are its fractions. Without it, the lab's
   TMT file rule (`<plex>[_TMT][_F<fraction>]`).
7. **Names and conditions.** experiment.yaml's `tmt:` map is used as
   FragPipe's annotation is: the name is the sample, and the condition is
   the text before the first `_`. A channel it calls NA / empty is left
   out before the summary. A channel nothing names is `<plex>_<channel>`
   with condition `unassigned`: one condition for all, so the analysis asks
   (`ONE_CONDITION`) instead of testing channels against each other.
   `irs: auto` does not fall back to the plex means while a channel is
   unassigned, because nothing says the plexes hold the same mix.
8. **The runner.** A lab `sage_config` with `quant.tmt` runs. Label-free
   quantification is not forced on for it, and the job expects `tmt.tsv`.
   An unknown kit name still holds the job. The default settings stay
   label-free: TMT needs the lab's own modifications and MS level, so there
   is no TMT default to ship.
9. **QC trending** reads `results.sage.tsv` per file. The mass error is
   computed from `expmass` and `calcmass` (signed, isotope-corrected), not
   taken from `precursor_ppm`. `fragment_ppm` is an unsigned average, so it
   gives no MS2 mass error.
10. **Left out: Parquet.** `--parquet` writes one `results.sage.parquet`
    with the reporter ions as a list column and `lfq.parquet` in long
    format: different layouts from the `.tsv` files, which Sage's own log
    calls unstable. Reading them is a second loader, not a small addition
    to the optional pyarrow reader. A method with `--parquet` in
    `sage_args` is held with that explanation.

Tested with stand-ins only. The layouts are from Sage's source
(`sage-cli/src/runner.rs`, `sage/src/tmt.rs`, master in September 2026),
not from a real run.


### D57 — The assistant's first part is read-only, and is tested as a harness, not as a model
**2026-10-01.** ROADMAP Phase 6.1 ("Explain") is built inside the rules D49
set, in `ionomos/assistant/`. Phase 6.0 (measuring models on the PC) has not
happened, so there is no default model, and nothing has run against a real
model or runtime. The choices made on the way:

1. **A fifth citation form, `[job:ID]`.** D49 lists issue, log, help and
   analysis. "Is job 3 finished?" has an answer that none of them can carry,
   so a job the tools returned can be cited too.
2. **Every paragraph needs a valid citation, and one invalid citation sinks
   the answer.** "Every claim cites" has to be something Ionomos can check
   without understanding the text; a paragraph is the unit it can see. An
   invented citation is treated as an invented claim. The model gets one
   chance to correct an answer, then Ionomos's own text is shown.
3. **A refusal is the silent branch.** There is no separate refusal message
   to trust: an answer with no valid citation ("I don't know", a poem,
   statistics advice) is never shown, and the fallback names who to ask.
4. **What a citation proves is that the source exists, not that the sentence
   follows from it.** The Sources lines under an answer are written by
   Ionomos from the tools' results so a reader can compare. Whether models
   misread their sources is for the scorecard on real models.
5. **An answer that says "I retried / deleted / changed …" is not shown.**
   No tool changes anything, so the claim is false whatever it cites. This
   is a coarse pattern, not a proof, and stays until 6.2 gives actions a
   dialog.
6. **Non-local addresses are refused even with `assistant.allow_cloud:
   true`.** D49 ties a cloud model to a banner and a preview of what is
   sent. Neither exists before 6.4, so the flag is read and reported but
   opens nothing. The request also ignores proxy settings and refuses
   redirects, so a local address cannot become a remote one on the way.
7. **A wrong `assistant:` address is not a config error.** Typos in the block
   fail at load like any other section, but where `base_url` points is
   checked when a question is asked: a bad address must not stop the watcher.
8. **Ionomos does the obvious lookups itself.** With `--experiment` or
   `--item` it fetches the job, its attention items and a failed search's
   log tail before the model's first turn, in the shape of tool calls. The
   system prompt and tool schemas stay byte-identical (about 1,000 tokens; a
   test pins their digest) so a runtime can cache them.
9. **Streaming is for timing only.** Nothing is shown before the citations
   are checked, so tokens are not printed as they arrive. The stream gives
   the time to first token for the audit log.
10. **Help search is BM25 with a shared crude stemmer**, in SQLite FTS5 when
    present and in plain Python otherwise, over the same tokens. No
    embeddings (D49: only if the evaluation shows misses).
11. **The audit log stores hashes of tool arguments and of the shown text**,
    and the question in clear, in `assistant-audit.jsonl` (named in
    `names.py`) in app data. It is never trimmed.
12. **The scenario corpus is scripted, and says so.** The 53 scenarios in
    `tests/assistant_scenarios/` carry model turns written by hand: what a
    good or a misbehaving model would send. CI replays them through an
    in-process fake of the chat endpoint. That tests the loop, the
    validators, the citation check, the fallbacks and the audit log. It
    does not measure a model. The rubrics in the same files are
    model-independent and are what a real model will be scored on.

**Left out:** the "Ask about this" button in the pop-ups (the backend,
`ask(item_id=…)`, exists; the Tk part could not be checked without opening
windows), a runner that scores a real model over the corpus, and runtime
tuning (keep-alive, threads, priority). Phase 6.1's box stays unticked.

**For the maintainer to confirm:** 1, 2 and 6.


### D58 — Notifications are off by default, say little, and can never touch a job
**2026-10-01.** ROADMAP Phase 4 asked for "email/Slack/Teams notify on
done/failed" and log rotation. The lab's rule is that nothing leaves the PC
unasked, so the first is built to be safe to ignore (`notify.py`).

1. **Off unless configured.** No `notify:` block, or `enabled: false`, sends
   nothing and writes nothing. `enabled: true` with no channel is a config
   error, not a silent no-op.
2. **Four channels, standard library only**: a generic JSON webhook, Teams,
   Slack (`urllib`) and SMTP (`smtplib`). PyYAML stays the only dependency.
   Teams gets one Adaptive Card in a `message` (the shape both a Workflows
   webhook and the older incoming webhook accept); Slack gets `{"text": …}`.
3. **A fixed, short list of what is sent**: status, job number, time,
   experiment name, user, method, the reason (first 600 characters), the hit
   counts per comparison, the local path of the report, the PC's name.
   Never a file, a table, a feature name or a measured value: the log tail
   and likely causes of a failure stay in `FAILED.txt` and the pop-up.
   `include_names: false` cuts it to the job number, status and time. The
   generic webhook's JSON is this list and nothing else, and the tests check
   the keys.
4. **Secrets.** Webhook addresses are bearer secrets, like the SMTP
   password. They may come from an environment variable (`url_env`,
   `password_env`; the variable wins over the file). They are never logged:
   errors are reduced to "HTTP 404", "could not connect" and so on. The
   diagnostics report and bundle redact the `config.yaml` they include and
   scrub every other file in them for the same values (and for anything
   shaped like a Slack / Teams hook address), also when the config doesn't
   parse. `config-backups/` keeps full copies: it never leaves the PC.
5. **https only.** `http://` is refused except for this computer
   (`localhost`), because the address is the secret. Redirects are not
   followed. An SMTP password with `security: none` is a config error.
6. **A job never waits for a message.** The worker calls `announce` after the
   ledger, `ionomos.json` and `DONE.txt` / `FAILED.txt` are written; it
   builds the message and hands it to a daemon thread. One try per channel,
   a timeout (`timeout_seconds`, 1 to 60, default 10), no retries, no queue
   of unsent messages: a message lost while the network was down is lost.
   A channel's error is logged once until it changes or the channel works
   again.
7. **"Held" once per job and reason.** The worker polls a held job every few
   seconds. The reasons already sent are kept in
   `<log_dir>/notify_state.json` (numbers removed, so "12 GB free" and
   "11 GB free" are one reason), which also covers a restart. The entry is
   dropped when the job finishes. A search cancelled by a person sends
   nothing, and neither does one re-queued because Ionomos was stopped.
8. **`on:` and YAML.** PyYAML reads a bare `on:` key as the boolean `true`
   (YAML 1.1). The loader and the config writer accept both, so the block
   can be written the natural way.
9. **Settings live in `config.yaml`, not the app, for now.** A tab would
   need GUI tests and a place to show secrets. The app's Save keeps the
   block (`configio.py` writes it, commented). The worker reads `notify:`
   at start: restart the watcher after a change.
10. **Log rotation existed** (`ionomos.log`, 5 MB, 5 old files). What was
    missing is Windows: a rename refused because another process has the
    file open made the stock handler drop records. The handler now keeps
    appending and retries a minute later. Size and count are constants in
    `names.py`, not settings.
11. **Left alone**: auto-archive to `D:` (it moves user data) and a status
    web page (it adds a server). The disk-space hold already existed
    (`fragpipe.check_raws`, `fragpipe.min_free_gb`).

**Not verified**: a real Teams, Slack or SMTP server. The tests use an HTTP
server and a small SMTP server inside the test process, and a stub for
STARTTLS + login.

### D61 — Conditions have roles; a competition experiment gets its own comparisons and a specific-targets call
**2026-10-01.** The maintainer's priority: the analysis should know that
"DMSO vs competitive vs compound will have DMSO have less samples". Until now
a control was recognised by keyword and every other condition was compared
with it. `downstream/roles.py` adds the design.

1. **Five roles**: control, compound, competition, reference (pool /
   bridge), qc. Every condition that is nothing else is a compound. A
   competition is linked to the compound it competes.
2. **Where a role comes from**, highest first: `analysis.roles`
   (`{Probe_Comp: competition of Probe}`), `analysis.control`, an SDRF
   column `characteristics[role]` (not part of the SDRF specification; a
   convenience), then the name. `find_control` honours `analysis.roles`, so
   the dose-response, time-course and F-test code see the same control.
3. **Competition keywords**, matched as a whole word of the name (split at
   `_ - . + space` and at a lower-to-upper step, so `ProbeComp` works for
   TMT, where the lab's names allow one word): `comp`, `competition`,
   `competitor`, `competed`, `compete`, `competing`, `excess`. Only `Comp`
   is from the lab (the folder `KL6283A_TMTPD_Comp_KL6159A` in
   `reference/pc-inventory/`); the SOPs name none. The rest are the usual
   words. **Unconfirmed.**
4. **Weak keywords** (`pre`, `pretreat…`, `block…`, `cold`, and a number
   followed by `x`) also mean other things (pre / post, a dose). They make a
   competition only when the rest of the name is another condition
   (`Probe_pre` next to `Probe`), the guess is used, and the doctor asks
   (`ROLES_UNSURE`, input). Without that condition they change nothing.
5. **Linking**: the compound whose name the competition's name contains
   (the longest such), else the only compound. With several compounds and no
   match the competition is compared with the control only, and the doctor
   asks.
6. **Default comparisons** (only for `de_type: control` without explicit
   `comparisons:`, and only when a competition condition exists): compound
   vs control, competition vs its compound, competition vs control. Not
   compared: a second control with the control, a pool or QC standard, a
   competition with another compound. Probe and probe + competitor without
   a vehicle give one comparison and no "which is the control?" question.
7. **Explicit settings win.** `comparisons:` and `de_type: all | others`
   are untouched. `control:` names the control and the role comparisons are
   still made, because the Analysis tab writes `control:` on every run; with
   it set, every condition is still compared with that control, so the set
   of comparisons is the earlier one plus competition vs compound.
   `role_comparisons: false` gives the earlier behaviour. The statistics of
   a comparison do not depend on how it was chosen (one limma model, BH per
   comparison), which a test checks.
8. **Specific targets**: significant up in compound vs control and
   significant down in competition vs compound, each by that comparison's
   own call (so the cut-offs, adjusted or raw p, and fold change only where
   there are no replicates). This is the common reading of a competition
   pulldown; the lab has not confirmed it. Alternatives not built: a
   threshold on the share competed off, an interaction test.
9. **Unequal groups.** What was checked on simulated 2 / 4 / 4 data, and
   what changed:
   - *Missing-value filter*: unchanged. It asks for values in 50 % of one
     condition, whichever, so a small control cannot remove a feature. One
     of two values is 50 %, so a feature can pass on one DMSO value alone:
     6 of 12,000 features in 8 simulated DIA experiments, left as it is.
   - *`min_valid` without imputation*: changed, with a setting. With DMSO
     n=2 one missing DMSO value left the feature untested against DMSO:
     6.1 % of features (20 simulated TMT experiments × 1,000 genes, 3 %
     missing) against 0.03 % with four DMSO channels. Now the smaller group
     of an unequal comparison needs half its samples
     (`small_group_min_valid: half`): 0.1 % untested, 1,205 features tested
     on one DMSO value, 49 hits among them, all true, no false hit among
     the 1,156 unchanged ones; specific targets found 400 of 400 instead of
     375. The justification: limma's residual variance comes from every
     group, and D32 already tests a group of one this way. Equal groups are
     not touched. limma only; Welch and Student keep `min_valid`.
   - *Imputation-driven flags*: unchanged. The rule is a share (half of a
     group), so one imputed value of two flags the hit and one of four
     does not. With two controls more hits rest on imputed values (57
     against 11 in 8 simulated DIA experiments), and they are flagged.
   - *limma*: unchanged. The pooled variance and the `sqrt(1/n1 + 1/n2)`
     standard error are checked against the formula for 2 / 4 / 4. R's
     limma was not available for an unequal-groups golden file.
   - *Power*: per comparison with its own n; the single "per group" number
     used the median group size (4 for 2 / 4 / 4).
   - *Scorecard*: changed. A sample with one mate scatters √2 σ around it,
     one with five mates √1.2 σ around their mean. In clean 2 / 6 / 6 data
     the DMSO samples' z-score was 3.25 (median of 40 simulations, max
     4.42; the flag starts at 3.5) and is now 0.01 (max 0.92). No sample
     was flagged before either, because the flag also needs 1.5× the
     typical spread, but the margin was thin. Equal groups: the factor is
     1.
   - *Low confidence*: unchanged for one control. Two controls are tested
     normally. The title said "a group has one sample" also when
     `min_valid: 3` made a group of two low confidence; it now says the
     number.
10. **Found on the way, not fixed**: median normalisation assumes most
    features don't change. In the simulated pulldown 8 % were enriched in
    one direction, which shifted the unchanged features by about 0.26 log2
    between DMSO and probe; with two controls that produced false hits just
    over |log2FC| = 1 (without imputation 17 in 8 experiments against 0 with
    four controls; with it 14 against 2). On the roadmap.
11. **isoDTB** is a competition experiment by construction: every
    condition has the role competition (of the probe), nothing is asked, and
    the comparisons and `cys.py` are unchanged.

**Not verified**: any real lab experiment; the report section in a browser
other than the one check made while building; R's limma on unequal groups.

### D62 — One export style; a figure is exported by drawing it again; every file says where it came from
**2026-10-01.** The maintainer asked for figures that are easy to export for
slides, customisable, and friendlier options. Before this, each chart had an
SVG and a PNG button that copied the chart as it stood on screen (page
colours, page size, no legend, no cut-offs), and the static `volcano_*.svg`
used CSS variables, which PowerPoint does not read.

1. **One style, the same keys everywhere.** `size`, `width`, `height`,
   `unit`, `font_pt`, `font_family`, `line_scale`, `point_scale`, `palette`,
   `up`, `down`, `neutral`, `background`, `title`, `subtitle`, `legend`,
   `note`, `labels`, `label_count`, `png_scale`, `png_dpi`, `zip_format`,
   `figures`. The report (`report.js` STYLE_DEFAULTS), the saved style file
   (`export_style.json`), `config.yaml` → `analysis.export`, and
   `ionomos export` (`charts.py` STYLE_DEFAULTS) all read them. A test
   compares the two copies of the defaults, sizes, palettes, ranges.
2. **A figure is drawn again, not restyled.** While a figure is exported, a
   module variable (`EX`) makes `css()` answer from the style's colours and
   `widthOf()` / `heightOf()` from its size; the chart's own renderer runs
   and hands its SVG to `svgTools()`, which is where the export copies it.
   The renderers changed by one call each (`H = heightOf(300)`), so a section
   another change adds is exported too: its chart calls `svgTools`, and the
   export draws the report (`redraw()`) again to capture it. The page is then
   drawn back as it was. The heatmap is a canvas on screen and has an SVG
   twin for export.
3. **Sizes.** Slides are 1280 × 720 px (16:9) and 960 × 720 px (4:3), which
   is a PowerPoint slide at 96 px per inch; half a slide is 640 × 600; journal
   columns are 85 mm and 180 mm. Text size is in points in the finished
   figure: the chart is drawn in units where the axis text is 12, and the
   SVG's `viewBox` scales it (14 pt → × 14 / 9). A size named without a text
   size takes 14 pt (slides), 12 pt (half) or 7 pt (journal). A chart that
   can take any height (volcano, PCA, the bar and line charts) fills the size
   exactly. A chart with a shape of its own (heatmap, correlation, UpSet,
   enrichment bars) is never stretched: the figure is made smaller, and when
   it had to be scaled down to fit, the dialog and the README say by how much.
4. **SVG for editing.** Text is `<text>`, colours are attribute values, the
   font is a family name with fallbacks (`'Segoe UI', Arial, Helvetica,
   sans-serif`), no `<style>`, `class`, `foreignObject` or CSS variable. A
   font name may hold letters, digits, spaces, `-` and `_` only.
5. **Every figure can be traced back.** The cut-offs in force, the hit
   filters and the test are written under the plot options on the page
   (`#viewnote`), under each figure (the "cut-offs" line, which can be
   switched off), and always inside the file: SVG `<title>` and `<desc>`; PNG
   an `iTXt` `Description` chunk, and a `pHYs` chunk so that 300 dpi means
   300 dpi in a layout program. A figure that rests on the report's saved
   cut-offs (heatmap, over-representation) says so. A low-confidence or
   fold-change-only comparison says so in its figure whatever is switched off.
6. **Palettes.** The default, Okabe and Ito's colour-blind-safe set, greyscale
   (every colour, the inks too; the heatmap becomes one light-to-dark ramp),
   and custom up / down / neutral. Backgrounds: white, dark, transparent
   (dark text, for a light slide).
7. **PNG is the browser's job.** The report draws the SVG onto a canvas.
   `ionomos export` writes SVG only: the standard library cannot rasterise,
   and no dependency was added. `--format png` explains this and exits 2.
8. **The zip is written by hand**: a store-only writer (local headers, data,
   central directory, CRC-32), about 40 lines, no library. The JS tests read
   it back with their own reader and CRC.
9. **The style lives in the browser** (`localStorage`, `ionomos.export.v1`,
   like the highlight groups) and starts from the lab's `analysis.export`
   (sent with each report as `exportDefaults`). Nothing is stored until
   something is changed. **A loaded style file is untrusted**: at most 20 kB,
   must be JSON with `"ionomos_export_style": 1`, and each key is taken only
   if its value passes its check (a list of allowed words, a number range, a
   `#rrggbb` colour, a font-name pattern); the rest is named in a text
   message and dropped. The same checks run on what storage and the report's
   payload hold. In `config.yaml` an unknown key or a bad value is an error,
   as for every other analysis setting.
10. **File names** go through one function (`safeName` / `charts.safe_name`):
    ASCII letters, digits and `. _ + -`, no `..`, no dot or underscore at
    either end, not a Windows device name, at most 120 characters. A name with
    a line break cannot add a line to the README or to a `<desc>`: control
    characters are replaced by spaces. Text cells in CSV exports that start
    with `=`, `+`, `-` or `@` get a leading apostrophe.
11. **`ionomos export` reads the report, and writes only what is its own.**
    The figures are drawn from the JSON inside `results/report.html`, so they
    show the report's numbers and nothing is analysed again. Style order: the
    defaults, the style the report was made with, the lab's `analysis.export`
    now, `--style FILE`, the flags. A file of the same name is replaced only
    when Ionomos wrote it (its figures and README say so inside); otherwise
    the new file gets `_2`. Nothing is deleted.
12. **The watcher writes no extra figures unless asked**
    (`analysis.export.figures`, default `[]`). `volcano_<comparison>.svg` is
    unchanged: it follows the browser's light / dark setting and is what the
    fallback page embeds.
13. **Options.** Labels became plain words with units; every control has a
    tooltip; the cut-off bar says what a log2 fold change is in fold; **Reset**
    became **Reset cut-offs**, and **Reset to lab defaults** puts the
    cut-offs, plot options and hit filters back. Plot options and hit filters
    are still not kept between reports: a hit filter left on would change the
    hit counts of the next report without a word.

**Verified**: 86 JS tests (jsdom) and the Python suite. In Chromium (the
desktop app's browser pane, a report from `ionomos demo` served from a local
web server): the dialog and its preview in light and dark page themes; the
volcano at 16:9, half a slide with the colour-blind palette on a dark
background, and the heatmap at one journal column in greyscale on a
transparent background; "Export for slides" (25 figures as SVG and PNG, the
tables, the style, the README; every CRC right; PNG at 2560 × 1440 with the
`pHYs` and `iTXt` chunks; the PNGs shown back in the page); the four
`ionomos export` SVG files opened as pictures. The zip a jsdom run wrote was
also listed and tested by Python's `zipfile` and by `unzip -t`.

**Not verified**: the SVG files in PowerPoint, Illustrator or Inkscape (text
editable, fonts, `viewBox` scaling, mm sizes); Firefox, Safari, Edge; a real
clipboard write (the test and the browser check replaced
`navigator.clipboard.write`); a report opened from `file://` on Windows;
Windows at all. Text widths are estimated (0.56 × the text size per
character), so a legend can wrap earlier than needed, and a long title is cut
with "…".

**For the maintainer to confirm**: the default size and text (16:9, 14 pt,
Arial); that the SVG / PNG buttons now use the export style instead of the
on-screen size; `figures: []` as the default; the apostrophe before formula
cells in CSV exports; SVG and PNG both in the zip by default; the lab's
current `analysis.export` winning over the style a report was made with in
`ionomos export`.
