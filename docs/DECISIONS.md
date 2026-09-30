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
  into the pipeline is left open.
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

