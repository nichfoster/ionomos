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
   call must never rest on a made-up value. (D70: the lab may opt into centring the ratios per
   replicate; the calls then use the centred ratios and say so.)
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
   cysteines are liganded") is the warning available without it. (Built in D70,
   with the proteome named explicitly.)


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
   at start: restart the watcher after a change. (2026-10-02: the app has
   a Notifications tab now, D67.)
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

### D59 — FragPipe is run the way its source says, checked before a search, and recorded after one
**2026-10-01.** Every search so far ran against the testbed's fake FragPipe,
which was written from guesses. Before the first real runs, the runner was
checked line by line against FragPipe's headless tutorial and the source of
FragPipe 24.0 (and 23.1 where it could differ). What follows is what was
decided; the mismatches found are in CHANGELOG (Unreleased) and the sources are
named in `fake_fragpipe.py` and WORKFLOWS.md.

1. **The launcher is `bin\fragpipe.bat`, with FragPipe's own Java.**
   `fragpipe.bat` is the start script FragPipe's build makes for every
   release (Gradle's): it runs `%JAVA_HOME%\bin\java.exe`, else `java` from
   PATH, else stops. The lab PC has no Java on PATH, so Ionomos sets
   `JAVA_HOME` for the launcher to the `jre` folder in the installation (the
   one FragPipe's `.exe` uses). It does this whenever that folder exists,
   also when the PC has another Java: FragPipe is built and tested with its
   own.
2. **`FragPipe-24.0.exe` is never run.** It is a launch4j wrapper with the
   window ("gui") header: by launch4j's documentation it starts `javaw` and
   returns without waiting or passing output on. Run by Ionomos it would end
   at once with exit code 0 while the search went on unseen. A configured
   `.exe` is swapped for the `fragpipe.bat` beside it, as before; without
   one the job is **held** with that explanation instead of started.
   *To confirm on the PC:* `C:\FragPipe\FragPipe-24.0\bin\fragpipe.bat`
   exists (the build says it does; the first Ionomos report only named the
   `.exe`).
3. **A FASTA FragPipe would refuse holds the job.** Headless FragPipe stops
   at once unless 40-60 % of the FASTA's entries start with the decoy tag
   (`FragpipeRun.checkDbConfig`; in the window it is a question one can
   click through). Ionomos applies the same rule before starting, when the
   workflow says Percolator, PeptideProphet or the report runs: the job
   waits ("waiting: FASTA … can't be searched") and starts when the file is
   fixed. A FASTA of 1 GB or more is not counted, as in FragPipe.
4. **TMT channel maps are checked with FragPipe's rules, and plexes sharing
   a folder get no annotation file.** FragPipe takes a plex's annotation
   from the folder that holds its files, and only when exactly one file
   ending in `annotation.txt` is there. So: one plex → `annotation.txt`
   (not written beside a user's own `*annotation.txt`); plexes each in their
   own folder → one `annotation.txt` per folder; plexes sharing a folder →
   none is written, a warning says FragPipe will name the channels
   `<plex>_<channel>`. A map FragPipe would stop on (not every channel of
   the label type listed, a name with a space, a name used twice) fails the
   job before the search, with the rule.
5. **Exit code 0 is not enough, and one line is not yet required.** A run
   fails when its log has a step with a non-zero exit code, or "Cancelling N
   remaining tasks", or the dry-run notice, or when nothing was written. A
   finished run also prints `ALL JOBS DONE IN x MINUTES`; a log without it
   is a **warning** on a done job, not a failure, until a real headless run
   has shown that line in the console Ionomos captures.
6. **Only the latest attempt's part of the console log is judged.** The log
   keeps every attempt. An earlier attempt's failed step used to fail a
   successful retry whose output fitted in the 400 kB read back.
7. **A failure's reason quotes the step that failed.** After a failing step
   FragPipe prints only "Process returned non-zero exit code, stopping" and
   "Cancelling N remaining tasks", so "last lines" said nothing. The reason
   is now "FragPipe step X failed (exit code N); it said: …" with the
   step's own last lines.
8. **The preflight starts FragPipe, and says so.** `ionomos preflight` (and
   the app's Check FragPipe install) runs `fragpipe.bat --help` and one
   `--headless --dry-run` per FragPipe method, each with a time limit, its
   output in a file, no window, and its files in a new folder under
   `<log_dir>\preflight\`. A dry run makes all of FragPipe's own checks
   (tools, FASTA, workflow, annotation) and lists the commands it would run.
   Its file list names a 64-byte placeholder `.raw` unless `--raw` gives a
   real file; `--static` starts nothing. FragPipe saves its settings cache
   on every run, dry or not, as it does for any headless run.
9. **Every search leaves a fingerprint**:
   `ionomos_run\run_fingerprint.json` (`names.FINGERPRINT_FILE`), written by
   the worker whatever the outcome, never in the job's way. Text only, tens
   of kB: launcher, command line, FragPipe's version block, the workflow's
   key settings, output file names and sizes, the first 80 and last 120
   console lines, what the parsers read (steps, exit codes, end marker),
   timings. An earlier one is kept as `run_fingerprint_<time>.json`. It
   holds names and paths, as the console log does; the diagnostics bundle
   is what strips them before anything leaves the PC.
10. **The fake FragPipe copies the real one, with its sources named**
    (`fake_fragpipe.py`): options and exit codes, the checks and their
    messages, the console layout, experiment names (`-` becomes `_`), group
    folders, `dia-quant-output`, `experiment_annotation.tsv`, `sdrf.tsv`.
    The tools' own chatter and every number are invented and the file says
    so. The testbed's workflows are small real-looking ones (39 settings).
11. **Not changed here, and open** (ROADMAP "Open questions"): FragPipe 24's
    stock workflows write `fragpipe\sdrf.tsv`, which the analysis reads as
    the experiment's own design (`downstream/sdrfdesign.py`) and then
    reports that it describes none of the runs. The testbed's workflows
    switch it off; `test_fragpipes_own_sdrf_is_not_taken_for_the_users_design`
    is the expected-failure test for it.

**Verified**: against FragPipe's source and documentation, and by the test
suite on the fake. **Not verified**: anything against a running FragPipe.
The launch4j behaviour of the `.exe` (2), that `fragpipe.bat` honours
`JAVA_HOME` on the PC (1), that a dry run accepts a placeholder `.raw` (8)
and the exact text of the tools' own error lines are from documentation and
issue reports, not from the lab PC.


### D60 — Accuracy is something the lab can measure, and messy tables are analysed and talked about
**2026-10-01.** The analysis was checked against R on golden files, which a
lab member cannot repeat on their own data, and it had only met tidy tables.
Four pieces, all in new modules ([VALIDATION.md](VALIDATION.md)):

1. **`ionomos compare`** (`downstream/compare.py`) compares an analysed
   folder with a reference: another analysed folder, or a results table read
   by `anytable.py` (D33), plus MSstats' long format. It reads both and
   changes neither.
   - Features are matched by accession (any member of a protein group) or
     by gene, whichever matches more. A reference row is used once.
   - The slope is the major axis, not least squares: both sides carry noise,
     and least squares would report a slope below 1 for two equally good
     results. The offset is the median difference.
   - The verdict's thresholds (r ≥ 0.95, slope 0.9 to 1.1, |offset| ≤ 0.10
     log2, 70 % of the hits shared when there are at least 10; at least 20
     and half of the features matched to judge at all) are **Ionomos' own
     choice**. They are constants, printed on the page, and the numbers
     stand beside the verdict.
   - An offset is its own verdict ("agrees after an offset of …"), because a
     normalisation difference is the commonest reason two correct analyses
     disagree. Hit lists are not judged then.
   - A reference the other way round is flipped only when its name says so
     (`DMSO vs Drug`). Fold changes that merely anti-correlate are reported
     and `--flip` is suggested: the direction is not guessed.
   - Exit code 1 for "differs" or "not judged", so a script can use it.
2. **`ionomos benchmark`** (`downstream/benchmark.py`).
   - *Simulated.* It calls the pipeline's own loader, `fpa.process`,
     `run_contrasts` and `to_diff` (`run_pipeline`; a test holds it equal to
     `analyze()`), not `analyze()` itself: a grid of 2,250 runs must not
     write 2,250 reports. `simulate.dia_pg_matrix` got three options (noise,
     a per-protein spread of the noise, a missingness scale); without them
     its output is byte-identical, so every existing fixture stands. The
     benchmark uses a per-protein spread, because proteins that all share
     one SD are limma's best case.
   - FDP is reported twice: with the analysis' cut-offs, and at adjusted
     p ≤ alpha alone. Only the second is what Benjamini-Hochberg promises,
     so only it is compared with the nominal alpha.
   - A scenario's FDP enters a quoted range only with 50 or more calls.
   - *Real.* The expected ratios are a small YAML, per species or per
     protein list. Species are read, in this order, from the protein lists,
     a named column, UniProt entry names / `OS=` in the feature's own text
     and in its row of the quant table, and a FASTA. A protein group with
     two species is left out and counted. It needs an analysed folder and
     does not analyse by itself: one command, one thing written.
   - Real and simulated results have different file names
     (`benchmark.*`, `benchmark_simulated.*`), so both can sit in one
     results folder.
3. **Messy input** (`downstream/guards.py`, `anytable.py`).
   - The rule: `analyze()` never raises, always writes a report, and a
     repair is always said. A stage crash (`CRASH_*`) on a plausible table
     is a bug; the fuzz test fails on one.
   - Values beyond 2^±100, NaN and infinities become missing right after
     loading, for every loader, with a count. 2^100 is far beyond any
     intensity; the limit exists so that no later step can overflow.
   - `anytable.read_table` reads decimal commas and thousands separators in
     tab and comma files only when asked (the `notes` argument), so the
     engines' readers behave as before. `1,234` alone is ambiguous and is
     read as 1234, with a note saying so.
   - A column without a name is left out, not guessed: it has no condition,
     and it is as likely a row number as a sample.
   - Statistical guards only read. `NO_RESIDUAL_DF`, `ZERO_VARIANCE`,
     `VARIANCE_PRIOR` and `IDENTICAL_SAMPLES` are warnings (no pop-up): the
     numbers are limma's, and limma is not overruled. An infinite prior df
     (one pooled variance) is not raised, because it is the right answer
     for alike variances; a prior that stops at the lower edge of the
     search (df 2) is.
   - What the fuzz found is listed in the changelog (nine bugs: one that
     ran the statistics on unlogged intensities, one that read a column
     twice, the rest crashes of single stages or refused tables).
4. **"How far to trust this"** (`downstream/trust.py`) is a list, not a
   score: any single number would hide which check failed and invite a
   threshold nobody can defend. Each line repeats an existing check with its
   number, and "check" marks a line whose own threshold was crossed. Two
   thresholds are new and are stated: fewer than 3 samples in a group, and
   groups that differ 2-fold in size. The block is static HTML written by
   `report.py`; `report.js` and `report.css` are untouched. A compare or
   benchmark result in the results folder is shown with whether the
   analysis settings are still the same (a digest of the settings); it is
   picked up at the next `ionomos analyze`, because a command that reads
   two results should not rewrite a report.

**Measured** (simulated, standard grid, 2026-10-01): default settings
(Perseus + median) 4.0 % observed FDP at adjusted p ≤ 0.05, 30 % of 2-fold
and 71 % of 4-fold changes found; no imputation 4.6 %, 47 % and 86 %; no
normalisation 14 – 21 %; `zero` imputation 8.0 % and a fold-change bias of
+0.41 log2. The test suite's guard allows 8.5 % (its fixed seeds measure
1.7 – 6.3 %).

**Not verified**: anything on real data. No mixed-species sample has been
run; no real FragPipe-Analyst, MSstats or Perseus export has been compared
(the layouts are the documented ones); the fuzz damages simulated tables.

**For the maintainer to confirm**: the verdict thresholds of `compare`; the
8.5 % tolerance of the guard; that Perseus-type imputation stays the default
although it found fewer planted changes than no imputation on the simulated
data (a real benchmark sample should decide); the two new "check"
thresholds; that a nameless numeric column is left out.


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


### D63 — A bundle is saved, never sent; names are replaced word by word and the zip is checked before it exists

2026-10-01. The maintainer needs the lab's files to troubleshoot and to
validate the analysis, and will carry them by hand (Desktop → Dropbox).
"Report a problem" already wrote a diagnostics zip; it is generalised
(`bundle.py`) instead of adding a second mechanism. `save_problem_report`,
`save_diagnostics_zip` and `ionomos diagnose --zip` now make a `diagnose`
bundle.

1. **Ionomos sends nothing.** It writes a zip to the Desktop (the registry's
   shell folder first, so a OneDrive-redirected Desktop works; else
   `%OneDrive*%\Desktop`, `%USERPROFILE%\Desktop`), else the log folder,
   else the home folder. No upload code exists. The bundle only reads
   experiment folders; it writes the zip (as `.part`, renamed after the
   check) and the key file, and numbers a name that is taken.
2. **Two levels.** `diagnose` is what the old zip had plus the whole run
   folder (so a `run_fingerprint.json` comes along; the name is
   `names.RUN_FINGERPRINT`), needs-attention items and `analysis.json`.
   `validate` adds the tables the analysis reads and `results/`. With no job
   named: the running, waiting and last five failed jobs; `validate` also
   takes the last finished job, so that a plain `ionomos bundle --level
   validate` has something to validate.
3. **Never raw data, FASTA or libraries**, by extension and name, also inside
   `results/`. A FASTA is described (name, size, entries, decoys, SHA-256).
   DIA-NN's main report is not taken: the analysis starts from the protein
   matrix.
4. **Limits said out loud.** 2,000 MB per bundle before compression, small
   files first so a log is never the thing dropped. A PSM-level table over
   25 MB is row-sampled (header + every n-th row) under its own name, so the
   analysis still runs; the job is then marked not fully reproducible. A
   quant table is never cut: it is in or out.
5. **Anonymised by default.** Replaced: users and aliases, the OS account and
   any home folder in a path, the PC name, experiment / inbox folder names,
   raw-file, sample and condition names, e-mail and IP addresses. Secrets go
   through `notify.scrub` / `redact_config_text` whether or not names are
   replaced.
6. **Word by word, not name by name.** `Drug_10uM_3h_2` becomes
   `condA_10uM_3h_2`: the analysis reads conditions (text before the first
   `_`), doses, times, TMT channels and replicates from the shape of a name,
   so the shape must survive. Kept words: numbers with units, replicate
   marks, control / reference words, the config's method names, the
   analysis' setting names and a short list of Ionomos' own words. The
   maintainer's examples (`user_01`, `exp_003`, `sample_A1`) have an
   underscore; the pseudonyms have none (`user01`, `exp001`, `condA_1`),
   because an added `_` changes what `condition_of` returns.
7. **Generic role words are kept by default** (DMSO, vehicle, WT, pool, but
   also drug, compound, treated): they say what a group is and name nobody.
   `--keep-conditions` keeps every condition word. There is no option to
   replace the control words: the analysis would lose its control.
8. **Order is part of the numbers.** Perseus imputation (`fpa.impute`) draws
   per sample in byte order of the names. `cond` pseudonyms are assigned in
   the originals' order and get a leading letter that keeps them on the same
   side of the kept words (`CondA` < `DMSO` < `condB`). Each job's names are
   checked afterwards and the bundle says when the order changed. Users are
   numbered, not ordered.
9. **A plain word is only replaced inside its name.** Folder and file names
   are made of ordinary words (`pulldown`, `enrichment`); replacing those
   everywhere would rewrite `enrichment.tsv`, a setting name or a JSON key
   and break the unpacked experiment. So: whole names, their `_`-prefixes
   and re-joined forms (`A-B_c` = `A_B_c`) are replaced; a word alone only
   when it is itself a user, sample or condition name, or has letters and
   digits (a compound or notebook number). A run of name characters that
   holds a known name is treated as a name. Experiment folders are replaced
   as a whole (`exp001`); a folder path outside the lab's tree as a whole
   (`folder01`).
10. **Identifiers are never rewritten.** Protein / gene / peptide columns of
    tab-separated tables are left as they are, even when a user's initials
    are a gene symbol (`AR`). Such hits are counted by the check and listed
    in the key file, not failed. In files that are not tables (the report's
    embedded JSON) the same word is replaced; the re-run report has it
    right.
11. **Structured first, then text.** `ionomos.json` and `experiment.yaml` are
    parsed: `notes` is removed (free text), the folder's `tokens` list is
    rewritten as names. Everything, also those, then goes line by line
    through the same scrub. Files that are not text cannot be scrubbed and
    are left out with a reason (UTF-16 text is read).
12. **Verify the product, not the process.** The finished zip is reopened and
    every file and file name searched for every original. A hit triggers one
    rewrite (a home-folder name first met in a late file), then
    `BundleLeak`: no zip. The tests add a search of their own (plain
    substring / word search over every file, the HTML report included) that
    does not use `bundle.py`; it found a real miss during development
    (multi-word user folders).
13. **The key stays in the lab**: `<zip>-KEY-keep-in-the-lab-DO-NOT-SHARE.json`
    next to the zip, never in it. `ionomos bundle translate` applies it.
14. **The reader gets the lab's settings.** `unpack` writes a `config.yaml`
    from the bundled one (methods, `analysis:` defaults) with every path
    under `_lab/` and no `notify:` / `assistant:`, so `ionomos --config …
    analyze` repeats the lab's analysis and cannot send or touch anything.
15. **"Copy diagnostics" is unchanged**: a text block with real names,
    secrets redacted. The bundle is the anonymised route.

**Verified**: the round trip on the testbed (fake FragPipe DIA job with
conditions on both sides of `DMSO`, isoDTB and TMT by hand): every result
table of the re-run is byte-identical to the bundled one, and translating it
back with the key gives the lab's own file. **Not verified**: real lab data
and real FragPipe / DIA-NN / MaxQuant / Sage tables (their column names are
from the engines' documentation and Ionomos' loaders); Windows (CI only);
the window on screen; a bundle over a few hundred MB.


### D64 — Normalisation: `auto` keeps median centring until it would shift the conditions, then uses feature ratios
**2026-10-01.** D61 found that median centring shifted the unchanged features
of a simulated pulldown by about 0.2 log2 between DMSO and probe, which made
false hits near the cut-off when DMSO had two samples. The cause is general:
the middle of a sample's abundance distribution, which spans several log2
units, moves whenever a share of the features is enriched in one direction.

1. **A second method, `ratio`.** Each sample is shifted by the median, over
   stable features, of the feature's value minus its mean across samples. A
   feature's ratio to its own mean has only the replicate noise, so 8 %
   enriched features move its median by about 0.01 log2 instead of 0.2. Only
   features measured in every sample are used, and of those the three
   quarters with the steadiest profile (three passes), the idea of edgeR's
   TMM trimming. Fewer than 20 such features: median centring, with a note.
2. **`auto` is the default, and is median centring unless a check fails.**
   FragPipe-Analyst's median centring stays the reference (D24), so where it
   is safe the numbers are identical to before; the goldens are unchanged.
   The check compares the two methods' sample shifts per condition. `auto`
   switches when they disagree by more than 0.1 log2 between two conditions
   and by more than 3 times the scatter among replicates.
3. **An explicit `median` or `gn` is respected, and asks.** The same finding
   then raises `NORMALISATION_COMPOSITION` as "needs your decision": the
   results are shifted, and the lab PC's config, written by earlier
   versions, says `median`. With `auto` it is a note.
4. **Numbers** (simulated, `competition_pg_matrix`, 8 experiments each,
   DMSO 2 / Probe 4 / Competition 4; unchanged features' median log2FC,
   false hits, true hits):
   - median, no imputation: -0.198, 11, 320 / 320; ratio or auto: -0.002, 2, 320 / 320
   - median, Perseus imputation: -0.209, 6, 276 / 320; ratio or auto: -0.013, 4, 276 / 320
   - with four DMSO samples the shift is the same size (-0.15) and is removed
     the same way.

   The check itself, on a two-condition table with random loading: with no
   enrichment it never fired on 1,500 or 4,000 features (100 tables each);
   on 100 features with 20 % missing values it fired in 14 of 187, where
   median centring really is unsteady. With 8 % enriched among 1,500 it
   fired in 99 of 100; with 3 % (a shift of 0.08) in 10 of 100.
5. **Limits.** Both methods assume most features don't change. A pulldown
   against empty beads breaks that for both; `none` is then the honest
   setting, and the help says so. The limits (0.1 log2, 3 times the scatter,
   three quarters kept) are choices, not measurements on real data. The
   reproduce-in-R script notes that FragPipeAnalystR has no ratio step.

### D65 — Roles are shown and chosen in the experiment editor and the review window
**2026-10-02.** D61 left the roles visible only in the report and settable
only by editing `experiment.yaml`. The maintainer asked for Ionomos to know
"DMSO vs competitive vs compound, with DMSO having fewer samples" and for
options that stay friendly. Now both windows show each condition's role and
let a person change it.

1. **One function holds the logic.** `roles.preview(sizes, settings,
   overrides, kind, exp, sdrf_roles)` returns the rows (condition, samples,
   role, where it came from, what "automatic" would give, whether to
   confirm), the comparisons in words, what the group sizes mean, and notes.
   The comparisons are `analysis.choose_comparisons` run on an empty matrix
   of those sizes, so the preview cannot drift from what the analysis does;
   a test checks this for every name in the roles table (D61's, including
   the lab PC's `KL6283A_Comp_KL6159A`). The Tk code only draws the rows and
   maps a choice to a value (`roles.role_choices`, `roles.set_role`).
2. **The key is the one roles.py already reads**: `analysis.roles` in the
   experiment's `experiment.yaml`, written as `{Probe_pre: compound}` or
   `competition of Probe`. No new setting. A role chosen in a window is an
   entry; **automatic** removes the entry.
3. **The choices** are roles.py's five roles: control, compound,
   competition of each compound condition (and plain competition, linked by
   name), pool / reference, QC standard. The task sketch had a sixth,
   "other"; it is not built, because every role must change what the
   analysis does, and "other" would need a rule nobody has decided (left out
   of the comparisons? compared like a compound?). On the open questions.
4. **Weak keywords are marked, not changed.** A competition read from `pre`,
   `pretreat…`, `block…`, `cold` or `10x` is shown with "?" and the reason;
   **Confirm** writes the guess to `analysis.roles`, which settles
   `ROLES_UNSURE`, or the list makes it a compound. A competition that can't
   be linked has no Confirm: the person picks which compound. The editor's
   "Run anyway?" check lists unconfirmed roles; the review window does not
   block on them (the analysis asks afterwards, as before).
5. **Uneven groups in words**, from the same rules the analysis uses
   (`fpa.resolve_imputation`, `analysis.group_needs`): imputed data (DIA
   default) "every feature is tested; the smaller group makes comparisons
   with DMSO less sensitive"; not imputed (TMT) "a feature needs 1 of 2 DMSO
   values and 2 in the other group (small_group_min_valid: half)"; Welch /
   Student, a group under `min_valid` (low confidence), and no replicates at
   all (fold change only) each have their line. Equal groups say nothing.
6. **A control chosen by role is the control.** Picking "control" for a
   condition also sets the Control box. In the review window the automatic
   control honours the roles (`guess_control`, as `find_control` does), so
   a control given by its role is not also pinned as `analysis.control`.
7. **The review window** shows roles for DIA and label-free drops only:
   TMT conditions come from the channel annotation and isoDTB is a
   competition by construction. Counts there are replicates (fractions of one
   replicate count once). It previews with the lab's and the experiment's
   other settings (new `Draft.lab_analysis` / `Draft.exp_analysis`) and
   writes `analysis.roles` only when the choice differs from what
   `experiment.yaml` had; entries naming a condition not in the drop are
   kept as written.
8. **SDRF roles** reach the editor: `inspect_folder` now returns the SDRF's
   roles, its file name and the data type, and the preview applies them
   below `analysis.roles`, as `analyze()` does.
9. **Found on the way, fixed**: the experiment editor saved its choices by
   merging them into the existing `analysis:` block, so a choice taken back
   (a sample used again, a role back to automatic, comparisons cleared)
   stayed in the file. `save_overrides(..., replace_analysis=True)` makes the
   editor's block the whole block; the review window still merges (its
   control keeps saved comparisons).

**Verified**: unit tests of the preview, the choices, the review window's
logic and `experiment.yaml` round trips; the Python suite and the JS tests.
**Not verified**: the two windows on screen. Their Tk tests (one each) were
written in the existing style and run in CI only; nobody has looked at the
layout, on Windows or macOS. Not tried on a real lab experiment.

### D66 — Accuracy checks for unequal groups, isoDTB and TMT: an R golden file and two more simulated grids
**2026-10-02.** The maintainer's priority: the downstream analysis should be
robust, and how accurate it is should be checkable. Two gaps were left: R's
limma had never seen unequal groups (D61), and the simulated benchmark (D60)
was label-free DIA only.

1. **Unequal groups against R** (`tests/golden/unequal/`). DMSO 2 / Probe 4 /
   Probe_Comp 4, 320 proteins with missing values and rows on each edge of
   the filters, through `benchmark.run_pipeline` (the calls `analyze()`
   makes) with median normalisation, no imputation (`small_group_min_valid`
   `half` and `same`) or Perseus-type imputation. The R script repeats each
   step in base R + limma 3.68.5. The small-group rule is expressed in R as
   the coefficient set to NA before `eBayes`, which is what Ionomos does: the
   variance prior is fitted on every feature, and BH counts the tested ones.
   Fold changes, intervals, t, p, adjusted p, the processed matrix and the
   prior agree to 1e-8 in all three cases. Normalisation is `median`, not
   `auto`: the ratio method is Ionomos' own and R has nothing to check it
   against.
2. **`ionomos benchmark --kind isodtb | tmt`** (`benchmark.py`, two new
   simulators in `simulate.py`). Each kind goes through the loader the lab's
   data would take, so the loader is part of what is measured:
   - *isoDTB*: FragPipe's label quant, the port of the lab's site script,
     `from_isodtb_sites`. The scenarios change sites **one way** (a compound
     engages its sites) and add a per-replicate **mixing error** (heavy and
     light not mixed exactly 1:1), the error the lab's protocol can make and
     the pipeline does not see. Settings: limma or the t-test, since ratio
     data is neither imputed nor normalised.
   - *TMT*: MaxQuant's `proteinGroups.txt`, because its reporter intensities
     are raw and Ionomos' own IRS (D48) then runs; TMT-Integrator's
     abundances are already ratios to the reference and are not scaled again.
     A pooled reference in every plex, a plex effect per protein, changes
     both ways or a pulldown one way. Settings: IRS on the pool (auto or
     median normalisation), IRS on the plex means, no IRS, no IRS with the
     plex as a block.
   - `run_pipeline` now calls `plex.normalise` first, as `analyze()` does;
     for anything that is not several TMT plexes it changes nothing, so the
     DIA grid and its guard give the same numbers as before.
   - Files are named by kind (`benchmark_simulated_isodtb.*`, `_tmt.*`), so
     the three sit side by side; `--like` takes the kind from the experiment
     (site ratios, or a TMT analysis with plexes) and "How far to trust this"
     shows whichever the folder holds.
   - A new number per scenario: the unchanged features' |mean log2 fold
     change| per table (`fc_offset_unchanged_abs`). The signed offset pooled
     over tables averages a random mixing error away; per table it does not.
   - A guard per kind in the test suite, with limits from 30 other blocks of
     10 seeds (worst mean + about 3.5 SD): isoDTB 9 %, TMT 8.5 %.
3. **What the grids found** (numbers in [VALIDATION.md](VALIDATION.md)):
   - The defaults are calibrated: isoDTB with limma and 3 – 4 replicates
     4.9 % (BH aims at 4 – 4.75 % here), TMT with IRS on the pool and `auto`
     3.8 – 4.4 %, unequal channels included.
   - D64 holds for TMT: after IRS, median centring shifts a pulldown's
     unchanged proteins by -0.2 to -0.4 log2 and most calls at adjusted p
     alone are false (67 %); `auto` keeps them within 0.04. The fold-change
     cut-off hides most of this, not all (8.4 % false hits with 2 DMSO
     channels per plex, 2-fold).
   - **Not fixed, on the roadmap with numbers**: (a) isoDTB ratios are never
     normalised, so a mixing error moves every unchanged site of an
     experiment (0.07 – 0.13 log2 at an SD of 0.2) and the test against 0
     calls more of them (up to 10.6 % per scenario). Centring each replicate
     on its median removes it but brings back the composition shift when
     many sites go one way (-0.09 log2 with 20 % up); which is right depends
     on how the lab mixes and how promiscuous its compounds are, and the
     liganded calls (D52) use the ratios as measured. (b) Without IRS the
     composition check and the ratio method compare a protein across plexes,
     where the plex effect hides the composition: a plex block recovers the
     power but not the normalisation (59 % false at alpha in a pulldown).
     (c) IRS on the plex means is slightly liberal (6.6 %; up to 9.3 %): the
     plex mean is estimated from the channels that are then tested. (d) Two
     isoDTB replicates give 5.8 – 9.8 % with limma: with 1 df per site the
     test rests on a variance prior whose shape the sites do not follow (with
     equal SDs it is calibrated).
   None of these is a small, clearly right change, so none was made. ((b)
   and (c) were fixed in D71.)

**Not verified**: real data of any kind. The simulated tables have the
layouts of FragPipe's label quant and MaxQuant's protein groups, not their
noise; there is no ratio compression (MS2 TMT) and no outlier channel. No
TMT-Integrator multi-plex table was simulated: its abundances are already on
the reference, so IRS is not part of that path.

**For the maintainer to confirm**: the two guards' limits; that a mixing
error of 15 % (SD 0.2 log2) is a fair stress for the lab's isoDTB protocol;
the open questions (a) to (d) on the roadmap.

### D67 — Settings and checks a lab member can reach without config.yaml; the logic is outside Tk
**2026-10-02.** The maintainer asked for "good options and settings while also
being user friendly". Three things were command-line or `config.yaml` only:
the lab's figure style (`analysis.export`, D62), `ionomos compare` /
`ionomos benchmark` (D60, ROADMAP 5C #10), and notifications (D58 item 9: "a
tab would need GUI tests and a place to show secrets").

1. **Where they live.** Two new pages on the Analysis tab, **Figure style**
   and **Check accuracy**, and a new tab, **8 Notifications**. Figure style
   and Notifications are saved with the app's one Save button like every
   other setting (validated on a probe file first, the old config backed
   up); Check accuracy writes no settings.
2. **The logic has no Tk.** `forms.py` turns `analysis.export` and
   `notify:` into the text and ticks a window shows and back;
   `accuracy.py` runs compare / benchmark and builds their command lines.
   Both have their own tests that run everywhere (`test_forms.py`,
   `test_accuracy.py`); the Tk modules (`analysis_tab.py`,
   `accuracy_page.py`, `notify_tab.py`) only copy values in and out. GUI
   tests in `test_app.py` drive them in CI.
3. **One definition of valid.** Every value goes through the check the
   loader uses: `charts.style_layer` for the figure style,
   `notify.settings_from` for notifications. An error names the field as the
   window labels it ("Figure style → Text size (pt): must be a number from
   4 to 48") and the config key. Friendly input is accepted where it is
   unambiguous: `7,5` for 7.5, a colour without `#`.
4. **Nothing is lost.** A field left empty leaves its key out, so the
   default applies (the default is shown in or beside the field). Keys the
   window has no field for (`line_scale`, `title`, `png_scale` …) are kept as
   they were, and so is a typo, so that the loader still names it rather
   than the app silently dropping it.
5. **Text size follows the size.** A size preset has its own text size
   (14 pt on a slide, 7 pt in a journal column). The field follows the preset
   when it held the old preset's size and stays as typed otherwise. Found
   while building this: `read_config` filled in the slide's 14 pt for an
   `export:` block that named `size: col1` without `font_pt`, so the app
   wrote 14 pt for a journal column on its next Save. It now leaves the key
   out (the writer then writes 7).
6. **One code path for each check.** `cli.cmd_compare`, `cmd_benchmark` and
   `cmd_notify_test` now call `accuracy.run_compare`,
   `run_benchmark_real` / `run_benchmark_simulated` and `notify.run_test`;
   the app calls the same functions with a `say` callback that posts each
   line to the window. A test checks the app's lines equal what the command
   line prints.
7. **Off the Tk thread.** A check or a test message runs on a daemon thread;
   every line and the verdict come back through `App.post` (the queue the
   app pumps with `root.after`). The buttons are disabled while it runs and a
   second click is refused. Problems that can be seen before starting (no
   analysis in the folder, no expected-ratios file) are listed in a dialog
   instead of starting a thread.
8. **Secrets.** Webhook addresses and the SMTP password are typed into
   masked fields (`•`). "Show addresses and password" unmasks them while
   ticked, and every load masks them again. The app logs only channel names
   and "all sent / not all sent", never a value; the test's lines are the
   scrubbed results `notify.py` already makes. The app had no secret fields
   before, so there was no convention to follow; `config.yaml` and its
   backups still hold the values in plain text, as D58 decided.
   "Send test" uses the values in the window, saved or not.
9. **Defaults without spaces.** A simulated benchmark started from the app
   writes into `<log_dir>/ionomos_benchmark` (C:/Fragpipe_Auto/logs on the
   PC), not the app's working folder (which may be under Program Files);
   `names.BENCHMARK_DIR`. Compare and a real benchmark write into the
   analysis' own results folder, as on the command line.
10. **Left out of the window**: compare's per-comparison choice, its common
    cut-offs, `--ref-alpha`; the benchmark's seeds. They remain command-line
    options; **Copy the command line** gives the run as a start.

**Not verified**: none of the three pages has been seen on screen. The GUI
tests (figure style round trip, masked fields, Send test with a stubbed
sender, compare and a tiny benchmark through the page) run only in CI;
locally they are skipped so windows don't cover the maintainer's screen.
Layout on Windows at the PC's display scaling, and the colour picker, are
untested.

### D68 — Every section exports for slides; PNG is drawn by a renderer the computer already has
**2026-10-02.** The maintainer's priority: figures that are easy to export for
slides and customisable. After D62, `ionomos export` drew four figures
(volcano, PCA, heatmap, correlation), wrote SVG only, and could only choose
among those four. The report's .zip held the dose-response potency plot and
whatever curve, profile or compound happened to be open.

1. **Six new static figures** (`downstream/sectionfigs.py`), drawn from the
   report's own data with `charts._compose`, so the style, the title / legend /
   cut-offs line, the `<desc>` and the D62 SVG rules (text is text, colours
   written out, no CSS) are the same: `dose_potency`, `dose_curves`,
   `time_patterns`, `time_profiles`, `liganded_rank`, `liganded_selectivity`.
   Kinds go in `analysis.export.figures` and `--figures`; `dose`, `time` and
   `liganded` stand for both of a section's. One file per compound or series.
2. **Curves and profiles are a grid in one figure** (a slide usually shows
   several), the panels as large as the size allows. By default the six most
   relevant: regulated curves by CurveCurator's relevance (the payload's
   order), changing features by F p-value. `--features` names others (gene,
   accession, site, its parts, `*` / `?`), `--top N` (1-24) takes more. A
   panel smaller than 135 x 115 drawing units is not drawn: the figure shows
   fewer and says "2 of 6: the rest do not fit this size", and a y-axis title
   with no room goes into the legend. At one journal column (85 mm, 7 pt)
   that is two panels; the lab can pick `col2` or `--top`.
3. **The pEC50 interval is drawn as a band** on the dose axis (pEC50 is
   −log10 dose, so its interval runs the other way), clipped to the doses; a
   curve that is not up or down says its pEC50 is not read, as the report
   does. The selectivity map shows sites liganded by any compound, selective
   ones first (grouped by compound), then shared, then unresolved; colour is
   the median R from 1 (white) to R² of the threshold; a dot marks a liganded
   call; rows that do not fit are counted in the legend. With one compound
   there is no map.
4. **The browser's .zip gets the same content** in its own way: each compound's
   six most relevant curves and each series' six most significant features as
   one figure each (`TOP_PANELS`, compared with `sectionfigs.TOP_PANELS` by a
   test), each series' patterns and the selectivity map (two export-only
   renderers, like the heatmap's SVG twin), every compound's rank plot. With
   more than one compound or series the curve / feature file names carry the
   series, so the zip keeps one of each. The view is drawn back as it was
   (the time series, its focus and the compound are now held too).
5. **Choosing figures.** `ionomos export --list` prints every figure the
   report can draw (file name, what it is, what can be chosen, notes such as a
   named feature that is not there) and writes nothing. `--figures` takes
   kinds, groups and those names with `*` / `?`; a name that matches nothing
   is an error that lists the names. `charts.catalog()` lists figures without
   drawing them; `charts.figures()` draws a catalog.
6. **PNG: an optional renderer, never a dependency.** Options weighed:
   a pure-Python rasteriser would need a font rasteriser (TrueType outlines,
   hinting, kerning, fallback fonts) to draw text correctly, which is most of
   the work and the part a slide shows; faking text with strokes was ruled out.
   So `downstream/raster.py` uses the first of: the `cairosvg` module if it
   imports (an `OSError` from a missing Cairo library counts as absent),
   `resvg`, `rsvg-convert`, `inkscape` on PATH, and Inkscape in its usual
   install folders (`%ProgramFiles%\Inkscape\bin`, `/Applications`), which
   are not on PATH by default. `--renderer` picks one. Nothing is downloaded or
   installed; with none, `--format png | both` prints what to install and exits
   2 before writing anything. resvg is the suggestion for the lab PC: one
   file, no installer. The PNG gets the report's size rule (`png_dpi` / 96 or
   `png_scale` times the px size, at most 16,000 px a side) and the report's
   `pHYs` and `iTXt` Description chunks; a `pHYs` the renderer wrote is
   replaced. Every PNG is made before anything is written, so a renderer that
   fails leaves the folder as it was (exit 1, its last lines of output said).
   Renderers run with a 120 s timeout and, on Windows, no console window. The
   watcher still writes SVG only: it should not start other programs.
7. **The watcher's figures had no section data**: `_static_figures` built the
   payload without the dose / time / liganded views. It now passes them.

**Verified**: the Python suite (new: `tests/test_export_sections.py`, with the
renderers stubbed for the found and not-found branches and a real PNG built
in the test) and the JS suite (new: `sections_export.test.mjs`, the zip with
synthetic dose / time / site payloads, also in greyscale at half a slide).
By eye, on simulated dose, time-course and isoDTB experiments: every new
figure at 16:9 and at one journal column, drawn to PNG by a real cairosvg 2.9
(Cairo from Homebrew) through `ionomos export --format both --png-dpi 150`;
sizes 2000 x 1125 and the chunks as described.

**Not verified**: resvg, rsvg-convert and Inkscape themselves (their command
lines are from their documentation and are only checked as stubs); any
renderer on Windows; text in PNG with fonts other than macOS's Arial; the new
SVG files in PowerPoint, Illustrator or Inkscape (as for D62); real lab
titrations, time courses or isoDTB data; the .zip in a real browser (jsdom
only).

### D69 — FragPipe faults are acted out by the fake, end in a clear state, and never cost data or a second search
**2026-10-02.** Keeping FragPipe working matters most, and nothing has run
against a real FragPipe yet (D59). Every way a search can go wrong on the lab
PC was acted out against the worker (`tests/test_faults.py`) and what broke
was fixed. A fault ends **done** (with a note), **failed** (with a cause from
`fragpipe.EXPLANATIONS` and `FAILED.txt`) or **held** (waiting, starts by
itself); nothing the user made is deleted or written over; the worker goes on
with the next job.

1. **The fake acts out faults per experiment.** `fake_fragpipe.MODES` gained
   `hang`, `killed`, `disk-full`, `raw-vanished`, `garbled-log`, `huge-log`,
   `runaway-log`, `empty-table`, `header-only`, `truncated-table`,
   `missing-table`, `truncated-psm`; several combine with commas. A file
   `fake_fragpipe_mode.txt` (`names.FAKE_FP_MODE_FILE`) in an experiment
   folder sets them for that experiment only, so the testbed (`fp_cut_table`,
   `fp_hang`) and the stress tester (a `fault` drop) mix faults with good
   jobs. Where FragPipe's own words for a fault are not known, the fake's are
   invented (Java's standard messages are used for a full disk and a missing
   file).
2. **A FragPipe that outlives Ionomos is stopped before the job runs again.**
   FragPipe runs in its own process group, so ending Ionomos from Task
   Manager (or a crash) left it running; the next start re-queued the job and
   started a second FragPipe on the same folder. While a search runs, its run
   folder now holds `engine_pid.json` (`names.ENGINE_PID_FILE`): the pid and
   when the OS says that process started (`health.process_started`). At
   start-up (`worker.recover`) and before every attempt, a recorded process
   that is still running **and started at that time** is stopped with
   everything it started; a process that only has the same number now is
   left alone. Waiting for the orphan and adopting its result was the
   alternative: it saves a search, but the exit code is lost and the worker
   needs another state. It is rare (a reboot or sign-out ends FragPipe too),
   and a re-run is certain.
3. **Recovery tells the folder.** A job found `running` at start-up went back
   to `queued` or, after `MAX_ATTEMPTS`, to `failed` in the job list only; its
   `ionomos.json` kept saying `running` and a failed one had no `FAILED.txt`.
   `worker.recover` writes both.
4. **Exit code 0 needs whole tables.** After a run that says it succeeded, the
   method's main tables that exist are checked (`fragpipe.table_problem`,
   first and last 64 kB): empty, binary, or cut off in the middle of a row
   fails the job ("not written to the end": usually a full disk); a header
   alone fails it as "no identifications". `psm.tsv` / `combined_protein.tsv`
   problems are a note. No end line **and** no result table is a failure;
   either alone stays as D59 had it (a note).
5. **What an exit code says.** A POSIX signal, a Windows NTSTATUS (Ctrl+C or
   the console closing, an access violation, out of memory), or a run that
   stopped in the middle of a step without any message gets a cause: "ended
   from outside" (Task Manager, sign-out, sleep, Windows out of memory) or a
   crash. A time limit and a runaway console log get theirs too.
6. **Console text as Windows writes it.** Each line is UTF-8 when it is, else
   cp1252 (the PC's ANSI code page); NULs (UTF-16), colour codes and control
   characters are removed before anything is matched or shown. Every reader
   takes only the end of the log (`tail()` used to read all of it), and the
   fingerprint reads lines in pieces of at most 64 kB. A console log that
   grows by more than 2 GB in one search stops it (`MAX_CONSOLE_BYTES`).
7. **Ionomos' own failures end the job.** An error inside Ionomos while a
   search ran (a full disk for the console log or the job list, a bug) left
   the job `running` with nobody watching it, and FragPipe possibly running
   unseen. FragPipe is now killed and the job failed with the reason
   ("Ionomos hit an unexpected error …"); Ionomos' own marker lines in the
   console log are best effort.
8. **New holds, not failures, for what is outside the job.** A raw file that
   can't be opened and read yet (Xcalibur still acquiring it, a copy still
   running, antivirus); on Windows, a FASTA, launcher, tools folder or raw
   path with a space (off Windows a note, as the config does); the workflow or
   FASTA gone between the checks and the start; the earlier attempt's output
   that can't be moved aside (a file open in Excel): a new search is never
   written into it. A hold found after the attempt was counted gives the
   attempt back (`Ledger.requeue(undo_attempt=True)`), and the worker sleeps
   instead of spinning on it.
9. **One folder, one search.** A second job row for an experiment folder (a
   rebuilt or hand-edited job list) is failed in the job list as "duplicate
   of job N" and never searched; the folder and its notes belong to job N and
   are not touched. Two drops of the same raws under two names are two
   experiments, as before.
10. **Earlier output is never in the way.** `fragpipe_previous_<time>` gets
    `-2`, `-3` … when the name is taken (two attempts in one second used to
    fail the job).
11. **TMT plexes as dropped.** A TMT drop with no raw at the top and none in
    `raw\` but `<plex>\*.raw` folders is filed with that layout kept (folder
    names cleaned as file names are; the same raw name in two folders is
    refused, as FragPipe needs each once). Each folder is one plex
    (FragPipe's experiment); experiment.yaml `files:` still wins. For other
    methods raws one folder down are still "no raw files", as before. Each
    plex's `annotation.txt` is written in its folder, and the SDRF reads it
    there. A flat drop with several plexes is **not** restructured (that is
    the lab's choice): it is filed as it is, with a warning in the log,
    `ionomos.json` (`plan.warnings`) and `DONE.txt`, now also without a
    `tmt:` map. A drop with raws at the top level still ignores subfolders.

**Verified**: by the suite on the fake, on macOS and in CI. **Not
verified**: anything against a running FragPipe; that `taskkill /T` and the
creation-time check behave on the PC as in CI; that Xcalibur's lock makes a
file unreadable; which code page FragPipe's tools really write (ROADMAP
"Open questions"). The app's inbox list, the review window's **Delete** and
the name check find raws through `intake.raw_paths`, so a `<plex>\` drop's
files are listed and can be removed (moved aside, as every inbox removal is);
added while combining the 0.15.0 PRs.


### D70 — isoDTB: site changes corrected for protein abundance, and opt-in centring of the ratios
**2026-10-03.** Two open items for isoDTB results the lab can trust: the
protein-abundance correction left out of D52 (ROADMAP 5C #3), and the mixing
error D66 found and did not fix. Both are built **off by default**: where the
proteome comes from, and whether to centre, are the lab's decisions.

1. **The proteome is named, never guessed.** `analysis.protein_correction:
   {proteome, match, conditions}` (usually in `experiment.yaml`). `proteome`
   is an analysed Ionomos experiment (its folder or `results/`), or a protein
   table: MSstats groupComparison output (Protein, Label, log2FC, SE, DF), the
   format MSstatsPTM itself takes, or an Ionomos `*_differential.tsv`. A
   relative path is read from the experiment folder; the proteome is only
   read. `match: gene | protein` (accessions with isoforms joined, as the site
   annotation does). A site condition (an isoDTB prefix such as `EJQ_2_027`)
   takes the comparison `conditions` names, else the one whose first
   condition has its name; none or several is `PROTEIN_CORRECTION_CONDITIONS`
   (decide) and that condition is not corrected. Alternatives weighed: taking
   a proteome's only comparison when there is one (a guess, ruled out by the
   task), and matching by roles (the proteome's roles are not the site
   data's).
2. **The scale comes from `liganded_direction`.** Site ratios are log2
   heavy / light; a proteome "Cmpd vs DMSO" is log2(Cmpd / DMSO), so on the
   site's scale it is −log2FC when the treated sample is light (`high`) and
   +log2FC when it is heavy (`low`). A proteome analysed as ratios (a
   comparison "X (log2 H/L vs 0)") is taken as it is. The orientation is
   written in `analysis.json` → `protein_correction.orientation`.
3. **MSstatsPTM's adjustment, exactly.** `proteincorr.adjust` is
   `.adjustProteinLevel`: log2FC = site − protein, SE = √(SE_site² +
   SE_protein²), Satterthwaite df, two-sided p; BH per condition over the
   sites tested (`.applyPtmAdjustment`). The site's SE and df are the
   moderated one-sample test's posterior SE and residual + prior df; nothing
   is moderated again, as in MSstatsPTM. To have them, `ContrastResult`
   carries `se` and `df` for every test (limma, the designs, the t-tests) and
   **every `*_differential.tsv` gains `se` and `df` columns** (additive; a
   proteome table without them is read from `t` and the 95 % interval).
   Infinite df (limma's pooled prior) drops out of the Satterthwaite sum; both
   infinite gives a normal p.
4. **Both results, flagged.** The site comparison stays as it was; a second
   comparison `<condition> (log2 H/L vs 0, protein-corrected)` is added like
   any comparison (volcano, table, report, results table, `analysis.json`),
   with the protein's key, status, log2 H/L, SE and df and the site's own
   numbers in its table. Sites whose protein is missing or ambiguous keep the
   uncorrected result and are flagged (MSstatsPTM drops them). Fewer than
   half the sites finding their protein, or a proteome that can't be read, is
   `PROTEIN_CORRECTION`. The liganded calls stay on the site ratio (R ≥ 4 is
   a rule about the measured competition); `cysteine_sites.tsv` shows the
   protein's ratio and the corrected R beside each call.
5. **`analysis.ratio_centre: none | median | auto`, default `none`.**
   - `median`: each replicate's median site to 0.
   - `auto` reuses D64's idea: centre on the sites that don't change, and only
     when it matters. Each replicate's offset is the median of its ratios over
     the half of the sites whose mean deviation from the replicates' centres
     is smallest (five passes): engaged sites all move one way and leave that
     half, so they stop pulling the centre. A condition is centred, all its
     replicates, when one of them is more than 0.05 log2 **and** 3 standard
     errors (1.2533 × robust SD / √sites) off 0. A condition is the unit
     because its replicates share the compound's composition.
   - The offsets are measured whatever the setting (`analysis.json` →
     `normalisation.ratio_centre`); with `none` a clear one is the note
     `RATIO_OFFSET`, which says what to set.
   - The liganded calls use the ratios the analysis used and say which in
     their rule ("on the ratios as measured" / "centred per replicate on the
     stable sites …"), in `analysis.json` (`cysteines.centred`) and Methods.
6. **What it measured** (`benchmark.simulated("centring", kind="isodtb")`: 3
   replicates, 900 sites, limma, 20 tables per scenario; FDP / planted changes
   found at adjusted p ≤ 0.05 / |mean log2 ratio of the unchanged sites| per
   table; BH aims at 4.75 % with 5 % changed, 4 % with 20 %):

   | mixing error, sites up, fold | none (default) | median | auto |
   |---|---|---|---|
   | 0, 5 %, 2× | 3.4 % / 38 % / 0.005 | 3.4 % / 31 % / 0.021 | = none |
   | 0, 5 %, 4× | 4.1 % / 94 % / 0.007 | 4.1 % / 94 % / 0.018 | = none |
   | 0, 20 %, 2× | 4.6 % / 79 % / 0.006 | 5.3 % / 74 % / 0.087 | = none |
   | 0, 20 %, 4× | 4.9 % / 97 % / 0.007 | 6.1 % / 97 % / 0.097 | = none |
   | 15 % (SD 0.2), 5 %, 2× | **8.4 %** / 18 % / 0.109 | 4.1 % / 24 % / 0.016 | 4.2 % / 28 % / 0.014 |
   | 15 %, 5 %, 4× | 4.8 % / 95 % / 0.113 | 4.9 % / 93 % / 0.017 | 4.9 % / 93 % / 0.010 |
   | 15 %, 20 %, 2× | 5.1 % / 61 % / 0.088 | 6.3 % / 74 % / 0.091 | 5.5 % / 80 % / 0.012 |
   | 15 %, 20 %, 4× | 3.8 % / 98 % / 0.080 | 5.2 % / 97 % / 0.093 | 4.4 % / 97 % / 0.012 |

   `auto` left all 240 tables without a mixing error untouched (identical
   numbers to `none`) and centred all 240 with one; on the stable sites its
   offsets had a bias of 0.003 and an SD of 0.017 log2 with 20 % of the sites
   up (reported SE 0.020 – 0.027). `median` removes the mixing error too but
   moves the unchanged sites by −0.09 whenever 20 % go one way. The mixing
   error does not always raise the FDP (it also widens the replicate spread,
   making the test conservative), but it always moves the unchanged sites.
7. **Default `none`, on purpose.** `auto` is better than `none` and `median`
   in every simulated scenario, but the simulation's mixing error is a guess
   (D66), a real compound may move more than half the sites, and centring
   changes the liganded calls. ROADMAP asks the lab.

**Verified**: the adjustment against MSstatsPTM 2.14.0 and limma 3.68.5 on
the same sites and protein table, to 1e-9 (`tests/golden/ptm/`, R script
committed; the test needs no R); the readers, the condition matching, the
issues and the scale on simulated isoDTB + DIA experiments end to end; the
centring on simulated tables and the benchmark grid above. **Not verified**:
real data of any kind; a real MSstats or MSstatsTMT protein table (built from
their documented columns); a proteome searched separately from the sites
(gene names that differ between the two searches); 2 or 4 replicates with
centring (the standard isoDTB grid now runs them); the report's new
comparison in a real browser (the JS is unchanged; the comparison is one more
entry in the existing list).

### D71 — TMT plexes: the composition check looks within plexes; IRS on the plex means pays its degrees of freedom
**2026-10-03.** D66's simulated TMT grid left two open questions: (b) without
IRS the composition check (D64) could not see a pulldown, and (c) IRS on each
plex's own mean (`irs: sum`) was slightly liberal. Both are fixed; the
default (IRS on a pool + `auto`) gives the same numbers on the same tables.

1. **The composition check within plexes** (`fpa.plex_groups`,
   `fpa._composition`, `fpa._plex_ratio_shifts`). When the samples belong to
   two or more TMT plexes that are **not** on one scale (IRS off or refused),
   the ratio method's shifts are taken within each plex (a feature's ratio
   to its mean over that plex's own channels, which a per-protein plex effect
   does not touch), and the check compares two conditions within each plex
   and combines the plexes holding both, weighted 1 / (1/n_a + 1/n_b), with
   its standard error from the replicate scatter within plex and condition.
   The plexes' levels come from the ratio method over all samples (else the
   sample medians). Each plex needs two channels and 20 complete features,
   else the samples are compared all together as before.
   - **Not after IRS.** Plexes already on one scale (IRS, MSstatsTMT's Norm
     channels, TMT-Integrator's ratios) are compared all together, as in
     D64. Tried first within plexes there too: the shift it measured was a
     little smaller (10 tables of a 2-fold, 20 % pulldown with 2 DMSO
     channels per plex: 0.153 against 0.157 on average), and in one table it
     fell to 0.100, at the 0.1 limit, kept median centring and put that
     scenario at 8.4 % false; across all channels there are three times as
     many values per feature.
   - With one plex (or none known) the check is D64's to the last digit (a
     test recomputes it), so earlier analyses do not change.
2. **IRS on the plex means spends a df per plex** (`plex.df_spent`,
   `fpa.spend_df`; `limma_contrasts`, `limma_others`, `design.limma_design`,
   `limma_design_others`, `f_test` take `df_spent`). Each plex a protein was
   scaled in (every channel of it measured) had its level estimated from the
   channels then tested, and the common target gives one back: limma's
   residual df are reduced by (plexes - 1), the residual variance rescaled to
   the same sum of squares, before the variance prior. That is what fitting
   the plex as a fixed effect costs, without the fit. Not applied when the
   design already holds the plexes (a block per plex or finer:
   `plex.holds_plexes`, by rank), nor to the t-tests.
   - **Why not a plex block instead?** Measured: after sum-IRS a block gave
     6.5 % (both ways) / 5.2 % (pulldown), no better than nothing. limma's
     `contrasts.fit` approximates the standard error of a protein with a
     missing value when the design is not orthogonal, and such proteins came
     out liberal (unchanged p < 0.05: 9.9 % after sum-IRS, 7 % for the block
     without IRS, 5.4 – 5.6 % for complete proteins). The df reduction keeps
     the plain, orthogonal model.
   - **Against R** (`tests/golden/tmt_sum/`, `run_tmt_sum_reference.R`):
     three plexes of 3 + 3 channels without a pool, with proteins in one, two
     or three plexes, a channel missing in one plex or in all; IRS, the
     filter, median normalisation and limma 3.68.5 given the reduced
     `df.residual` and `sigma` agree to 1e-8 (fold change, interval, t, p,
     adjusted p, the prior); without the reduction most p-values differ.
   - `analysis.json` → `model.plex_df` says so; a model note too.
3. **What a person is told.** "How far to trust this" gets a **TMT plexes**
   line: reference channels, plex means with the df reduced, or a plex
   block (ok); neither on one scale nor in the model, or a t-test after IRS
   on the plex means ("check"). New doctor warning
   `TMT_PLEXES_NOT_IN_MODEL` (`irs: none` and no plex block; help entry).
   `NORMALISATION_COMPOSITION` names the plexes when the check was made
   within them, and now fires without IRS, where it should.
4. **Numbers** (`ionomos benchmark --kind tmt`, the standard grid; D66's in
   brackets): IRS on plex means + auto 5.1 % (6.6 %) with changes both ways,
   4.0 % (5.3 %) in a pulldown, range 3.0 – 7.7 % (3.7 – 9.3 %), the same
   sensitivity (93 / 98 %). No IRS with a plex block, pulldown: 4.7 % (59 %),
   unchanged proteins within -0.04 (-0.19 to -0.26). No IRS without a block,
   pulldown: 0.3 % (5.1 %), 40 % of 2-fold changes found (31 %). Default and
   IRS + median: unchanged. On 20 seeds of the four "both ways" cells (1,000
   proteins, 2-fold): plex means 6.89 % → 5.11 %, where the exact plex-block
   model without IRS gives 5.54 % and IRS on a pool 4.16 %.
5. **Guards** (`tests/test_benchmark.py`, `TMT_GRIDS["guard_sum"]`,
   `["guard_pulldown"]`; limits from 30 other blocks of 10 seeds, worst mean
   + about 3.5 SD): plex means FDP at most 10 % (blocks: mean 4.7 – 5.0 %,
   SD 0.8 – 1.4 %; without the reduction 6.2 – 7.4 %, and the same seeds
   without it must come out worse); a pulldown without IRS within 0.06 log2,
   with the plex block at most 10 % false (mean 5.0 – 6.0 %). About 15 s.

**Not verified**: real TMT data; whether real plex effects are additive per
protein, as simulated. **Open** (ROADMAP): limma's block approximation with
missing values; proteins `irs: sum` leaves unscaled or drops a plex for; the
t-tests after plex means; the 0.1 log2 limit at a 2-fold pulldown.

### D72 — "Ask about this", a scorecard runner, and the settings for sharing the PC with a search
**2026-10-03.** What Phase 6.1 could still do without a real model (D57 left
it out), inside D49's rules. No model has been tried; Phase 6.1's box stays
open.

1. **"Ask about this" asks with a fixed question per kind of item.** The
   pop-ups and the attention list get the button; it opens a window that asks
   at once with, for example, "Why did this search fail, and what should I
   do?", which the user can edit. Nothing from the item (title, folder or
   sample names) goes into the question. An item without a job used to be
   named to the model by its id, which carries the folder's name; it is now
   named by its kind and the time Ionomos raised it, and its names reach the
   model only inside tool results, as data.
2. **The logic is outside Tk; the thread holds no widget.** `assistant/askui.py`
   makes the question, asks (never raising: a config that won't load is shown
   as such) and renders each outcome as plain text with a heading and a help
   topic. `popups.AskWindow` only draws. The answer comes back through the
   pop-ups' existing queue (`PopupHost.post`, pumped with `root.after`); the
   worker thread is handed the long-lived `Popups`, a token and plain values,
   never the window, and the window uses no Tk variable (tkutil.py: a Tk
   object freed off the Tk thread aborts the process). A window closed before
   its answer comes drops it. The window has no button that changes anything.
3. **The scenario corpus ships with Ionomos.** `ionomos ask-eval` must run on
   the PC, where Ionomos is a frozen exe without `tests/`, so the JSON files
   and the fixture states moved to `ionomos/assistant/scenarios/` (package
   data, and in the PyInstaller spec). `tests/assistant_scenarios` re-exports
   them. The CI replay and the runner use the same files and the same
   `score()`.
4. **15 scenarios are "harness_only".** Their rubric holds only for their
   script (a runtime that is down, a reply that is not JSON, a model that obeys
   an injected line, invents a citation or never answers): a good model would
   fail them. The runner scores the other 38; each question a harness-only
   scenario asks is scored once, in the scenario with the well-behaved script,
   and a test checks that none is left unscored.
5. **The runner builds its own fake lab and writes two new files.** Each
   fixture state is built once, in a new folder (`C:/ionomos-ask-eval/<time>`
   on Windows: no spaces), and the questions are audited there, not in the
   lab's audit log. The scorecard is JSON plus a table, in app data by
   default, created exclusively: never written over. It keeps each answer and
   its Sources, because a pass proves only that the rubric found nothing wrong
   (D57 4), and a person has to read them before choosing a model.
6. **Only this PC, checked first.** The address (and `while_searching`'s) is
   checked with the client's own `local_problem()` before anything is built,
   and every request goes through `client.chat()`. The tests use a scripted
   HTTP server on 127.0.0.1 and a port the OS picks, with proxy variables set
   to show they are ignored; `--scripted` runs the same server on the PC to
   check the runner and the fixture states there without a model.
7. **Time to first token is the first generated token.** The client counted
   the opening `role: assistant` event, which a runtime sends before reading
   the prompt. It now waits for text, reasoning or a tool call.
8. **Only `keep_alive` is added to the request.** Ollama's
   `/v1/chat/completions` reads `keep_alive` (checked in its source,
   2026-10-03); llama-server ignores it. Neither endpoint takes a thread count:
   Ollama's OpenAI-compatible request has no `num_thread`, and a request with
   other runner options reloads the model, so preloading with fewer threads
   does not stick; llama-server's threads are fixed at start (`-t`, `-tb`).
   So "while a search runs" switches what can be switched per request:
   `while_searching` may name another `model` (a smaller one, or an Ollama
   variant made with `PARAMETER num_thread 4`), another `base_url` (a second
   llama-server started with fewer threads), a shorter `keep_alive`, a longer
   `timeout_seconds`, or `pause: true` (no model at all; Ionomos's own text).
   Empty `keep_alive` (the default) sends nothing.
9. **"A search is running" is read from the worker's heartbeat.** The worker
   beats "running job N: step" every few seconds during a search; a heartbeat
   older than `health.STALE_AFTER` means no watcher, so no search. It is read
   per question, in the app and in `ask-eval` (`--mode auto`). The audit log
   and the scorecard record the mode.
10. **Ionomos does not lower another program's priority.** It only reads and
    asks; starting the runtime at "below normal" is documented as the lab's
    choice, to be decided by the Phase 6.0 measurements.

**Verified:** by the suite on macOS (the window tests are written in the
existing style and run in CI only). **Not verified:** any real model or
runtime; that Ollama on the PC honours `keep_alive` on this endpoint; the
right `keep_alive` / `while_searching` for the PC; the window on the PC's
display scaling.

**For the maintainer to confirm:** 1 (naming an item without a job by kind
and time), 4 (which scenarios are harness-only; `refuse_delete_request`
expects no grounded answer, which a good model citing the help's "Ionomos
never deletes" would fail), and 8 (no thread setting in Ionomos).

### D73 — A hung test run reports itself and ends; no wait in the tests is without a time limit
**2026-10-03.** The full suite hung on macOS (Python 3.14) at least three
times in a day, on different branches, with nothing on screen until a
30-minute limit killed it; re-runs passed and CI (Ubuntu, Windows) never hung.

**Reproduction.** 14 full runs on macOS / Python 3.14.6 (10 of master's code,
4 with these changes), up to three at once next to other agents' suites and
simulations (load average up to 80), and about 3,300 more tests from the
thread- and process-heavy files (`test_faults`, `test_failsafes`,
`test_fragpipe_real`, `test_worker`, `test_watcher`, `test_e2e`,
`test_notify`, `test_service`, `test_stress`, the engine runners) in
forward, reversed and shuffled file orders, each with
`-o faulthandler_timeout=240` so a stuck test would have printed every
thread's stack. **It did not hang**: no stack was printed, no test took more
than ~30 s, and no process was left behind. A full run took 6–8 minutes
with three at once and 13.5 under the heaviest load. `tail` (as in `pytest |
tail`) shows nothing until the end, so a run slowed past an outer limit and
one that hangs look the same. The code was then read
for every wait without a time limit (below). The cause of the three hangs is
**not known**. These changes make the next one say where it is:

1. **A test that runs too long ends the run with every stack.**
   `pyproject.toml`: `faulthandler_timeout = 300` and
   `faulthandler_exit_on_timeout = true` (pytest ≥ 9, now the `[dev]`
   floor). The dump comes from a C thread, so it works even when the hang
   holds the GIL; the run exits 1 rather than waiting for the outer limit.
   300 s is 10× the slowest test on a loaded Mac and fits inside CI's
   15-minute job.
2. **A run that can't exit after its last test is ended too.** A thread or
   a child something waits on at interpreter exit (a non-daemon thread, an
   atexit handler) is past every test hook. `tests/conftest.py` arms
   `faulthandler.dump_traceback_later(120, exit=True)` in
   `pytest_unconfigure`, on a copy of the real stderr taken while output
   capture is off (`IONOMOS_TEST_EXIT_SECONDS` changes it).
3. **Waits without a limit, given one.** In the tests: `proc.wait()` after
   a kill in `test_faults.py`, the thread `join()` in `test_failsafes.py`,
   the `ps` / `tasklist` probes in `test_fragpipe_real.py`, the `python -m
   ionomos` runs in `test_main_entry.py`, `git` in `test_service.py`, the R
   probe and script in `test_design.py`. A second `ionomos run` whose output
   is read (`test_failsafes.py`) is killed and drained if it doesn't answer
   in 30 s. `while w.run_once(): pass` (three in `test_faults.py`) became
   `_drain`, which fails after 20 jobs instead of looping. The fake SMTP
   server's DATA loop span on an empty read when the client went away; it
   now returns.
4. **Product: `taskkill` / `tasklist` get 60 s** (Windows only). Every
   way a search is stopped (stop, cancel, the time limit, a console log too
   large, an error in Ionomos, a leftover FragPipe) and the app's Stop ran
   `taskkill /T /F` with no limit, as did the `tasklist` behind the
   watcher's pid. One that never returned would have held the worker (and
   `ionomos run`'s shutdown) for ever. Now `fragpipe._taskkill` gives up after
   `TASKKILL_SECONDS` (also on `OSError`, which used to escape `kill_tree`),
   and a `tasklist` that doesn't answer counts as "not running", so no
   process is named that can't be seen. Not seen on the PC; found by
   reading.

**Checked and left alone**: `fragpipe._watch` waits with `poll`, `kill_tree`
and `request_stop` already had limits, `health.process_started` gives `ps`
10 s and never raises, every thread the product starts is a daemon,
`notify.flush` and the test HTTP / SMTP servers are bounded, the resolver's
`done.wait()` (GUI only; `ionomos run` joins its threads with a limit and they
are daemons). Not done: pytest-timeout (a dependency for what pytest and the
standard library already do); killing a hung run's children. An `ionomos
run` or fake FragPipe that a test started is left running when the run is
ended this way (their output goes to files, so they hold no pipe of the
run); `ps` shows them by the test's temporary folder.

### D74 — Diagnostics are anonymised as a bundle is; more tables on request; a name check in `inspect`; Spectronaut gets a column list, not an `.rs` file

**2026-10-03.** The maintainer wants anonymous zips (and text) from the
lab's PC, saved on the Desktop, to carry to Dropbox for troubleshooting and
validation. D63 left four things open. This entry replaces D63 point 15
("Copy diagnostics is unchanged") and extends point 3.

1. **"Copy diagnostics" is anonymised by default; the real text is one
   button away.** The app's **Copy diagnostics** now copies the text with
   the lab's names replaced; **Copy with real names** (next to it) copies it
   as before. `ionomos diagnose` keeps printing real names (a terminal on
   the PC) and takes `--anonymise`. One code path: `bundle.anonymise_text`
   learns the names with the same `collect` + `learn` a `diagnose` bundle
   uses (same settings, job list, inbox, problem jobs), rewrites the text
   with the same `_Scrub`, and runs the same leak check (`check_text`, the
   search behind `verify`, now one class `_Check`). A name still in the text
   raises `BundleLeak`: nothing is copied or printed, and the message says
   so. The pseudonyms are the ones a `diagnose` bundle made at the same time
   gives (tested). A checkbox was the alternative; two buttons make the
   choice visible each time and need no state.
2. **The key of the text stays in the lab.** The saved copy
   (`logs/diagnostics-<time>-anonymised.txt`) is the anonymised text, with
   `<name>-KEY-keep-in-the-lab-DO-NOT-SHARE.json` next to it
   (`bundle.write_key`, the bundle's key format), so `ionomos bundle
   translate` reads a pasted copy back. Daily housekeeping keeps the newest
   50 diagnostics keys (they are tiny, and needed long after the text was
   pasted); the texts stay at 20, as before.
3. **More tables only when asked: `--include diann-report,peptides`.**
   `diann-report` is DIA-NN's main report (`report.tsv`, `report.parquet`,
   or `<out>.tsv` / `.parquet` beside `<out>.pg_matrix.tsv`); `peptides` is
   FragPipe's `peptide.tsv` / `ion.tsv`, DIA-NN's `*pr_matrix.tsv` and
   MaxQuant's `peptides.txt` / `modificationSpecificPeptides.txt`.
   FragPipe's `combined_peptide.tsv`, `combined_ion.tsv` and
   `combined_modified_peptide.tsv` were already in every `validate` bundle
   (`combined_*.tsv`). A spectral library (`lib` in the name, `.speclib`) and
   DIA-NN's first-pass report never go in. `--include` implies `--level
   validate` (`--level diagnose --include …` is refused); the Report a
   problem window has a box for both kinds, which also implies the result
   tables. The tables are added after every other table of every job, so
   the bundle's size limit drops them first, and each one over `--extra-mb`
   (default 200 MB) is row-sampled as a PSM table is (header + every n-th
   row), said in `capped`, `BUNDLE.json` (`one_row_in`) and README. They
   make a job "not fully reproducible" only when the analysis reads them (a
   DIA-NN job without its matrices).
4. **A Parquet report goes in as text.** A binary file's names cannot be
   replaced or its rows sampled, so `report.parquet` is written as
   tab-separated text (`report.tsv`, or `report.parquet.tsv` when a
   `report.tsv` is beside it), batch by batch with pyarrow, each value as
   `str()` as Ionomos' own Parquet reader turns it into a cell
   (`engines._read_long`); `BUNDLE.json` says `converted_from`, and
   `inspect` / `unpack` / README say it. The DIA-NN loader reads the
   converted file to the same matrix as the original (tested). Without
   pyarrow (it is in the `[dev]` and `[parquet]` extras, so in the lab's dev
   install, but not in the exe) the file is left out with that reason.
5. **Run names in cells are learnt.** A long report names its run in every
   row (`Run`, `File.Name`, Spectronaut's `R.FileName`), not in its header,
   so those columns are read whole (Parquet: only those columns) and each
   value registered as a sample (a path: its stem, and its folder when it
   is outside the lab's tree). MaxQuant's header prefixes (`LFQ intensity
   <sample>`, `Intensity <sample>`, `Experiment <sample>`) are now learnt
   too. `unpack` needs nothing new; `translate` reads a file line by line, so
   a translated main report is not read into memory.
6. **`ionomos bundle inspect` checks every file for real names.** It
   searches each file of the zip, and each file name, for the names this
   computer knows: the lab's (`learn` with no job named: settings, aliases,
   the users folder, the OS account and PC, every job in the job list with
   its sample names, the inbox) and the originals in the bundle's key file
   (next to the zip, or `--key`). It lists every file with "no real name",
   or what it found and how often; identifier columns are listed as kept,
   as in the key file. On the developer's computer (no settings, no key) it
   says there is nothing to check against. A bundle made without
   anonymising is expected to be full of names, and `inspect` says so. A
   home-folder path holding a pseudonym (`C:\Users\user01`) is not a name.
7. **Spectronaut: a column list and instructions, not an `.rs` file.**
   Spectronaut's report-schema file (`.rs`) is its own format: the manual
   (Spectronaut 19, "Report Perspective" and the command line's `-rs`)
   describes building, saving and passing a schema but not the file, and a
   public `.rs` (SpectroPipeR's) is an opaque binary file. Writing one would
   be inventing a format. Instead `engines.SPECTRONAUT_COLUMNS` is the one
   list the loader reads (`load_spectronaut` takes its `want` from it) and
   `ionomos spectronaut-columns [--out DIR]` prints it with what each column
   is for, which three are needed (`R.FileName`, `PG.ProteinGroups`,
   `PG.Quantity` or `PG.MS2Quantity`) and how to tick them in the Report
   perspective, save the schema and export a Normal Report. The lab can then
   save its own `.rs` from Spectronaut and share that. The PG and EG column
   names are in the manual's Appendix 8; the R columns are those Ionomos
   already read (also MSstats' Spectronaut converter's).

**Verified**: by the suite on macOS (the window's code by GUI tests that run
in CI only): the anonymised text has none of the testbed's names (plain
search), its pseudonyms are a bundle's, a leak stops it; `--include` bundles
of the fake FragPipe DIA job with a written `report.tsv`, `report.parquet`,
library, precursor matrix, `peptide.tsv` and `ion.tsv` (real column names)
pass the plain search, unpack, load and translate back byte for byte; the
name check finds a name added to a zip. **Not verified**: real DIA-NN 2.x
Parquet reports (their size, types and how long the conversion takes on the
PC); real MaxQuant peptide tables; Spectronaut itself (the instructions
follow the manual, not a session with the program); the buttons and the new
box on screen.


### D75 — The assistant proposes one checked change; only a Confirm button applies it

**2026-10-04.** ROADMAP Phase 6.2, inside D49's rules ("any change goes
through a native dialog built from the structured arguments, and the user
clicks Confirm"). No model has been tried; the box stays open until lab
members have used it (the exit criterion).

1. **Five proposal tools, a closed whitelist.** `propose_retry`,
   `propose_condition`, `propose_leave_out`, `propose_setting` (`imputation`,
   `normalize`, `alpha`, `log2fc`, `control`, `de_type`, `comparisons`; `""` =
   the lab default) and `propose_role`. One job, one sample, one key per
   call; no path, no "all", no delete or move. `de_type` was added to the
   roadmap's list because the editor treats it with the control and the
   comparisons; roles have their own tool because a role is per condition.
   Other analysis keys (min_valid, filters, enrichment, …) stay with the
   editor.
2. **A proposal is built and checked before it exists.** The schema, then
   the ledger (a retry needs a failed job), then the experiment: samples are
   those of its last analysis (`results/analysis.json`) plus the ones
   `experiment.yaml` leaves out, conditions follow `experiment.yaml`'s
   `sample_conditions`; a control, comparison or role must name one; a new
   condition must be a plain name; the resulting `analysis:` block must pass
   `settings_from` with the lab's settings and differ from the file. The
   sample list comes from analysis.json rather than reading the result table
   again, because that reader writes into `results/` and a proposal must only
   read. So an experiment whose analysis never ran gets no sample proposals.
3. **One proposal per question**, the first valid one; later calls get an
   error the model reads. It is offered only with an answer that passed the
   citation check; otherwise it is withheld (audited).
4. **The window is Ionomos's text.** `popups.ProposalDialog` shows the title,
   what Confirm does and a unified diff of `experiment.yaml` rendered by the
   same code that writes it (`manifest.overrides_text`), from the checked
   arguments; nothing the model wrote as prose. A proposal for another job
   than the one asked about says so. It is modal; Cancel has the focus;
   Escape and closing are Cancel. One proposal window at a time (another is
   audited as not shown).
5. **Confirm re-checks, then uses the app's own code.**
   `assistant/actions.apply` rebuilds the proposal from its tool and
   arguments against the ledger and file as they are now and goes on only if
   the id (tool, arguments, job, the file's sha256, the new block) is the
   same: an editor save or a Retry in between makes it refuse. A retry is
   `worker.request_retry`, which the Jobs tab, the pop-up and `ionomos retry`
   now share; an analysis change is `manifest.save_analysis`, which the
   experiment editor's Save now uses. Confirm does not re-run the analysis;
   the message says how.
6. **experiment.yaml gets backups.** The editor's Save overwrote the file;
   the task assumed a backup that did not exist. `save_analysis` copies the
   old file to `<experiment>/experiment-backups/experiment-<time>.yaml`
   (`names.EXPERIMENT_BACKUP_DIR`) first, created exclusively, never pruned.
   This applies to every editor save, not only the assistant's.
7. **Only the Confirm button applies.** A test reads every module of the
   package: `actions.apply` is called in `ProposalDialog.confirm` only, and
   no other assistant module retries a job or writes `experiment.yaml`. The
   replay checks that no scenario, in any fixture state, changes a file or a
   job. "Yes" typed in the chat is a new question; the model has no
   confirming tool.
8. **`ionomos ask` prints, never applies.** The proposal, then `ionomos retry
   N`, or the app's steps and (where `ionomos analyze`'s flags can say it) a
   one-off `ionomos analyze N --exclude …` that does not save. Names a
   terminal could misread are left out of a printed command.
9. **Audit.** The question's record lists the proposal (id, tool, argument
   hash, job, title, offered); each decision is its own record
   (`proposal_decision`: confirmed, applied, the message).
10. **The prompt grew** to about 6,200 bytes (≈1.55k tokens) with the five
    schemas; the size test's limit moved from 6,000 to 6,500 bytes, under the
    roadmap's ~2k tokens. The digest changed, so real models must be scored
    again.

**Verified:** by the suite on macOS (the window's tests are written in the
existing style and run in CI only). **Not verified:** any real model's
proposals; the window on the PC's display; whether lab members finish the
tasks unaided.

**For the maintainer to confirm:** the whitelist (1, with `de_type`); that a
proposal is withheld when its answer fails the citation check (3); the
backups for every editor save, never pruned (6); that Confirm saves but does
not re-run the analysis (5).

### D76 — MaxLFQ is a roll-up option, not the default; the moderated F-test can be switched off

**2026-10-04.** ROADMAP 5B asked whether median polish or MaxLFQ would agree
better with FragPipe's `combined_protein.tsv` for Sage. Ionomos had only
median polish. The open questions also noted that the moderated F-test
always runs with 3+ conditions and has no setting to stop it.

1. **One module, pure Python** (`downstream/rollup.py`). `maxlfq(rows)`
   implements MaxLFQ as Cox et al. (*Mol Cell Proteomics* 13:2513, 2014)
   describe it and `iq` / DIA-NN implement it:
   - For every pair of samples, the median log2 ratio of the features
     measured in both (`min_ratio_count` 1, as in iq and DIA-NN; MaxQuant's
     default is 2, available as an argument but not as a setting).
   - Samples linked by such ratios form connected components.
   - Per component, the least-squares profile is found by solving the
     system iq's `lsfit` solves. Its normal equations plus the constraint
     are solved by Gaussian elimination with partial pivoting
     (`rollup.solve`), so no numpy. The cost is O(n³) per protein in the
     samples of its component. That is fine for tens of samples; a
     100-sample study would take minutes.
   - The scaling is Cox et al.'s: the profile's summed linear intensity
     equals the summed intensity of the component's feature values
     (`scale="sum"`, the default). iq's mean-of-logs scaling
     (`scale="mean"`) is kept for the golden test.
   - A sample in a component of its own gets its summed intensity (iq: the
     median feature). A sample with no value stays missing.
   - Components are **not** put on one scale. Values across them are not
     ratios, and the loaders say how many proteins that affected.
     DIA-NN's solve instead pulls every sample weakly (1e-4) towards its most
     intense feature, which gives disconnected groups a top-1 scale.
     Ionomos does not copy that: it would invent a ratio the data do not
     contain.
   - `median_polish` stays in `engines.py`; `rollup.summarise(rows, method)`
     dispatches.
2. **`analysis.rollup: auto | median_polish | maxlfq`**, applied wherever
   Ionomos rolls features up to proteins:
   - Sage `lfq.tsv`: ions after razor grouping and fractions.
   - The MSstats format: its features.
   - A DIA-NN long report: `Precursor.Normalised`, else
     `Precursor.Quantity`, per `Precursor.Id`. A precursor reported twice
     for a run keeps the larger value, as `diann_maxlfq` does.
   - A Spectronaut long report: `FG.Quantity` per `EG.PrecursorId`. A
     fragment-level report repeats the value on every fragment row.
     `FG.Quantity` is a new optional entry in `SPECTRONAUT_COLUMNS` (D74).

   **`auto` keeps every loader's previous default**, so nothing changes for
   existing experiments:
   - median polish for Sage (D51) and the MSstats format (MSstats' own
     default, TMP);
   - DIA-NN's own `PG.MaxLFQ` and Spectronaut's `PG.Quantity`. MaxLFQ is the
     engine-standard choice for DIA-NN, and DIA-NN has already computed it,
     with its own precursor selection. Recomputing it would only make the
     result differ from DIA-NN's matrices.

   For Sage, MaxLFQ is not *clearly* the engine standard: Sage leaves the
   protein to the user, FragPipe's IonQuant uses MaxLFQ, and MSstats uses
   median polish. The benchmark below found no clear winner, so the default
   stays median polish until a real Sage-vs-FragPipe comparison on the PC
   decides. Where the setting cannot apply, a note says so:
   - a table that already holds proteins (MaxQuant, AlphaDIA, FragPipe,
     pg_matrix, PD, any table);
   - Sage TMT and MSstatsTMT, which keep MSstatsTMT's median polish;
   - a long report without the precursor columns. It then uses the engine's
     protein quantity.

   The report shows the choice in Data source → Quantity, in the notes and
   in the Settings row "Protein roll-up".
3. **Validated against R** (`tests/golden/maxlfq/`, `run_maxlfq_reference.R`):
   `iq::maxLFQ()` 2.0.1 (CRAN) and `diann::diann_maxlfq()` 1.0.1
   (github.com/vdemichev/diann-rpackage at af538f6), R 4.6.1. The input is
   54 proteins × 8 samples:
   - two and three disconnected sample groups;
   - a sample with no value;
   - one feature, one sample, two features (even medians);
   - a chain of samples linked only through each other;
   - a feature 8 log2 above the rest;
   - 45 random proteins with abundance-dependent missing values and lost
     samples.

   Results:
   - Against iq: every value within 1e-9 (measured 6e-14), in both the
     mean and the sum scaling, and the components equal iq's annotation.
   - Against DIA-NN: the centred profiles of connected proteins agree to
     1e-3 (measured 8e-4, the effect of DIA-NN's regularisation).
   - The tests need no R.
4. **Measured on simulated peptide data** (`ionomos benchmark --kind rollup
   --grid standard`, `simulate.peptide_msstats`):
   - 800 proteins of 1 to 30 peptides, each with its own ionisation offset
     (SD 1.5 log2).
   - 10% of the proteins changed 1.5-, 2- or 4-fold.
   - Replicate SD 0.3 log2, spread between proteins; a loading shift per run;
     weak peptides missing more often (typical and heavy); 2% of the values
     off by an interference (SD 2 log2).
   - Designs 3v3, 4v4, 6v6 and 2v4, with 5 seeds per scenario (24
     scenarios), median normalisation.

   FDP is at adjusted p ≤ 0.05 alone, pooled. Its range is over the scenarios
   with ≥ 50 calls. Sensitivity is at adjusted p ≤ 0.05.

   | Roll-up + imputation | FDP | range | FDP with \|log2FC\| ≥ 1 | found 1.5× / 2× / 4× | log2FC bias |
   |---|---|---|---|---|---|
   | median polish + Perseus (default) | 3.9% | 1.8–6.8% | 1.2% | 31 / 71 / 88% | −0.06 |
   | MaxLFQ + Perseus | 4.6% | 1.3–13.2% | 1.1% | 33 / 72 / 88% | −0.06 |
   | median polish, no imputation | 4.5% | 1.6–7.1% | 1.1% | 37 / 77 / 90% | −0.03 |
   | MaxLFQ, no imputation | 5.8% | 2.0–16.5% | 1.2% | 40 / 79 / 90% | −0.02 |

   - **Sensitivity.** MaxLFQ finds 1–3 points more of the 1.5- and 2-fold
     changes.
   - **FDP.** Its pooled FDP is 0.7–1.3 points higher, and its worst
     scenarios are worse. 4v4, 2-fold, heavy missing values reach 13–16%,
     against 7% for median polish. The cause was traced in one seed. The
     false calls are not proteins split into components. They are unchanged
     proteins in a table where median normalisation left an offset of
     −0.17 (median polish) or −0.19 (MaxLFQ) log2, because changed proteins
     went missing one way. Both roll-ups carry the offset, and MaxLFQ's
     slightly larger one moves more proteins past the BH threshold.
   - **Precision.** The SD of the unchanged fold changes is the same
     (0.21–0.22).

   Neither is clearly better on this model, which is why the defaults do
   not change. A guard (`tests/test_rollup.py`, the `guard` grid, 6 seeds)
   holds both at ≤ 8.5% FDP, with the same sensitivity within 6 points.
5. **`analysis.f_test: auto | off`**. `auto` (default) is today's behaviour.
   `off` makes `analysis.f_test()` return nothing, so:
   - no "Any change (F)" tile and no F columns in `<level>_results.tsv`;
   - Methods says it was switched off;
   - Settings used has a row "F-test (any change): off";
   - `analysis.json` has `f_test: {off: true, note}` instead of `null`,
     which means "does not apply".

   A bare `off` in YAML is read as `false` and accepted. Time-course
   F-tests (D53) are separate and still follow `time_course`. The doctor
   has no F-test issue, so it needed no change. The setting is a key of
   `config.yaml` / `experiment.yaml` (written back by `configio`); there is
   no new app field.

**Verified**:
- Both R references to the tolerances above.
- Every loader rolls up its table to exactly `rollup.summarise` of the
  same features (`tests/test_rollup.py`), with the fallbacks and notes.
- End to end: `analyze()` with `rollup: maxlfq` on an MSstats table, and
  with `f_test: off` on 3 conditions.
- The benchmark kind and its files.

**Not verified**:
- Real data of any engine.
- Whether MaxLFQ or median polish agrees better with FragPipe's
  `combined_protein.tsv` on a real Sage search. The question stays open in
  ROADMAP.
- How long MaxLFQ takes on large studies on the PC.
- That Spectronaut's `FG.Quantity` is the precursor quantity the lab's
  reports carry. The name follows Spectronaut's column naming (FG. =
  fragment group, the precursor), not a real export.

### D77 — Long time courses are fitted as natural splines in hours; short ones stay a factor
**2026-10-04.** ROADMAP 5C #1 left "spline fits for long series are not
built" (D53). With many time points the factor model spends a parameter on
every time point and tests noise as change; the limma User's Guide (9.6.2,
many time points) fits a regression spline instead.

1. **When.** `analysis.time_model: auto | factor | spline` (default
   `auto`). `auto` keeps time a factor up to 6 time points and fits a
   spline from 7 (`timecourse.AUTO_SPLINE_POINTS`). Why 7: proteomics time
   courses usually have 3 to 6 points (D53), and every series that exists
   today keeps its numbers; with the default 4 df, a spline only saves
   parameters once a series has more than 5 time points, at 6 it saves one,
   and from 7 it saves at least two, which is the lack of fit that smoothing
   turns into power. The lab hasn't said whether it runs time courses, so
   the switch is where nothing changes for what exists.
2. **How many df.** `time_spline_df` (default `auto` = 4, the middle of the
   guide's "3 to 5 is reasonable"), never more than the time points − 2: at
   time points − 1 the curve goes through every mean (the factor model with
   extra assumptions), above it can't be fitted. A higher value is lowered
   to time points − 2 with the `TIME_SPLINE` issue (a note, with help), as
   is a spline model that can't be fitted (then that series is a factor).
   `time_model: spline` on a short series uses min(4, points − 2), so 3
   points give a straight line in hours.
3. **The basis is R's, column for column.** `downstream/splines.py` ports
   `splines::ns` (R 4.6.1): interior knots at type-7 quantiles of the
   sample times (with replicates, as `ns(targets$Time)`), R's shoving of a
   knot that lands on a boundary, Cox–de Boor `splineDesign` with
   derivatives, and the natural constraint by LINPACK's Householder QR
   (`dqrdc2` / `qr.qty`), so the coefficients are R's and not only their
   span. `predict()` on new times is the same code.
4. **Hours, not order.** A curve is a function of time, so the spline uses
   the hours (knots at quantiles adapt to uneven spacing). The trend t
   still uses the order of the time points (D53, an open question).
5. **One model per series.** The comparisons' model (`~0 + condition` plus
   block and covariates) with the series' conditions replaced by a level
   and the spline columns; every other condition keeps its own mean, so the
   residual variance uses every sample, as in the comparisons. Change over
   time is the moderated F on the spline coefficients. The series-vs-control
   test fits both series as curves on one basis made from both series'
   times and takes the F on the differences of their coefficients, which is
   limma's `~Group * ns(time)` interaction (the golden computes it in that
   parameterisation). It needs what the factor test needs (the same first
   time and a shared later one, not a shared baseline). The variance prior
   (limma or DEqMS) is squeezed per model.
6. **What the rows mean.** log2FC is the fitted curve's change from the
   first time point at each time point; the largest change, its time, the
   class and the patterns follow from it, so a single noisy time point
   among many does not make a feature "changing". `mean_log2` keeps the
   observed means; `time_course.tsv` has a `model` column (`factor` /
   `spline (4 df)`); `analysis.json` gives `time_model` and the knots.
7. **Drawn as a curve.** The payload carries per series the basis on 61
   points of hours (less its value at the first time point) and per feature
   its coefficients and a level (the mean of each value less the fitted
   change at its time, so the curve runs through the replicates whatever the
   block). The report's profile and `time_profiles` (D68) draw the curve on
   an axis in hours, labels thinned where early time points crowd, the
   control series' curve dashed. Pattern tiles stay by order.

**Checked** against R 4.6.1 `splines::ns` (bases and `predict()` for 8 time
vectors, df 1–6, shoved and tied knots: 1e-12) and limma 3.68.5
(`tests/golden/timecourse/run_spline_reference.R`): 150 features × 51
samples, Drug and DMSO at 8 time points from 0 to 48 h and a Pool condition;
plain, replicate block, missing values with df 3; F, p, adjusted p, the
fitted changes and the interaction F, p, adjusted p: 10,350 values, worst
relative difference 5.4e-10. **Not verified**: a real lab time course; the
default N = 7 and df = 4 against what the lab would call a long series;
very uneven spacing (0 to 2 weeks) where a log-time axis might fit better.

### D78 — Acquisition time comes from the raw file's own header; run order is tested within the conditions
**2026-10-04.** ROADMAP Phase 4 left "run-order drift, once acquisition times
are recorded". The report could not show it, and the QC trend (D45) ordered
runs by a name stamp or a file time.

1. **The time is the Thermo header's, read in pure Python, read-only**
   (`acqtime.py`). Every Thermo `.raw` file starts with a 1356-byte
   FileHeader: magic `0xA101`, `Finnigan` in UTF-16LE, the format version at
   `0x24`, and two audit tags whose first 8 bytes are Windows FILETIMEs
   (100 ns since 1601, UTC): acquisition start at `0x28`, end at `0x98`. The
   layout is the one unfinnigan documented; OpenTFRaw (validated on six real
   files: LTQ, LTQ Orbitrap, Elite, Fusion, Fusion Lumos, Q Exactive HF) and
   Philosopher's `fin` package read the same offsets, and it is what Thermo's
   RawFileReader calls `FileHeader.CreationDate`, which ThermoRawFileParser
   writes as "Creation date" (its metadata writer, read in its source). Only
   the first 264 bytes are read. The header is used only when the magic, the
   signature, a known version (8, 47, 57, 60, 62, 63, 64, 66, or 67–99 for
   newer software) and a time between 1995 and two days from now all check
   out, and not when it is more than a day after the file was last written.
   The variable-length RawFileInfo date (after SeqRow and ASInfo, whose
   layout changes with the version) was **not** used: that would be guessing.
   The audit tags' text (it can be a Windows account) is not kept, so
   `ionomos.json` holds no name a bundle's anonymiser does not know.
2. **Then, in order:** what ThermoRawFileParser already wrote for the file
   (an mzML's `<run startTimeStamp>`, as Sage's conversion leaves in
   `sage_mzml\`, or `-metadata.json` / `-metadata.txt`; times without a
   zone are taken as local); the Xcalibur stamp in the name; the file's
   modification time, flagged **approximate** (the instrument writes the
   file until the run ends; copies keep the time). Ionomos does not start
   ThermoRawFileParser to get a time: the header holds the same field, and
   the watcher should not start programs for it (as for PNG, D68).
3. **Recorded at intake** in `ionomos.json` → `acquisition`
   (`{manifest file: {time, utc, source, approximate, end, matches_file,
   file_time, version}}`). `time` is local wall-clock time, as the QC trend
   has always stored it; `utc` is the same moment. `matches_file` says
   whether the header's end is within 10 minutes of the file's time: the
   check to read on the lab PC. A failure to read leaves `{}` and never stops
   a filing. Older jobs, and `ionomos analyze` on a folder, read the times
   when analysed. The QC trend uses the recorded time, then the same sources;
   its rows say "raw header", "ThermoRawFileParser", "name", "file time" or
   "filed".
4. **Run-order QC** (`downstream/runorder.py`): per sample, identifications,
   missing values, the median log2 intensity before normalisation (the
   scorecard), and from the search the PSMs, the median precursor mass error
   and the missed-cleavage rate (D55), or DIA-NN's precursors and MS1
   accuracy. A sample's time is its first raw file's; samples that share raw
   files (TMT channels) have no order of their own and get none.
5. **Drift is tested within the conditions.** A plain trend against run
   order mistakes a condition run as a block for a drift. The test is Hirsch
   and Slack's seasonal Kendall test with the conditions as the seasons (only
   pairs of one condition are compared). Its p-value is exact up to 1,500
   pairs (the conditions' Mahonian distributions convolved; checked against
   enumeration), normal with the tie-corrected variance above that. The size
   is the stratified Theil-Sen slope times the runs. Spearman's ρ of the
   within-condition residuals is shown beside it. A number is flagged
   (`RUN_ORDER_DRIFT`) at p < 0.01 **and** a change over the run of at least
   10 % (counts), 0.5 log2 (signal), 5 points (missing values, missed
   cleavages) or 3 ppm. Missing values are not warned about beside
   identifications (the same fact). Six samples with a time are needed; a
   perfectly ordered 3 × 3 design reaches p = 0.009, a 2 × 3 cannot (0.056).
6. **Blocks** (`RUN_ORDER_CONFOUNDED`): η² of the run positions (ranks) by
   condition, with how often a random order is as aligned (4,000 seeded
   permutations), flagged at η² ≥ 0.6 with two or more conditions of two or
   more samples. A blocked 2 × 3 is 0.77, a blocked 3 × 3 0.90; interleaved
   (rep 1 of every condition, then rep 2) 0.1. It describes the design and
   is not a test: a randomised order that came out blocked is still blocked
   (about 9 % of random 2 × 3 and 3 × 3 orders, 2 % of 3 × 4). Both are
   warnings, no pop-up.
7. **Validated on simulated data** (`tests/test_runorder.py`), 3 conditions
   × 4, identifications with SD 40: a fall of 40 per run is found in at
   least 90 of 100 randomised experiments (96 % in a 200-run check); no drift
   is flagged in at most 4 of 200 (0 seen); a condition 400 apart run in
   blocks is not a drift; a drift inside blocks is still found; blocks are
   always flagged and randomised orders rarely.
8. **Shown** as the **Run order** QC tab (a chart per number with the
   conditions as a strip above it and the Theil-Sen line, the trend table,
   the samples with their time and its source), `analysis.json` →
   `run_order`, and the `run_order` export figure (a panel per number,
   drifting ones first; `charts.STATIC_FIGURES`, report.js `STATIC_FIGS`).
   The payload goes through `ctx["run_order"]`, so `report.payload`'s
   signature is unchanged.

**Not verified:** any real `.raw` file (the tests build headers to the
documented layout; the testbed's raw files are random bytes, so they fall
back to the file time); whether ThermoRawFileParser's mzML `startTimeStamp`
and metadata "Creation date" are UTC or local for the lab's files; the tab
and the figure in a real browser (jsdom only); the limits on the lab's own
sequences.

### D79 — Phosphosites, kinase activity and STRING partners are opt-in; the downloads stay the lab's
**2026-10-04.** ROADMAP 5C #8 and #9. The lab has not said it runs phospho, so
everything here is **off unless asked for** and an experiment analysed before
is analysed exactly as before (`analysis.json` gains a `phospho` entry that
says "not asked for"). `downstream/phospho.py`.

1. **`phospho: true` swaps the protein table for the site table**, and the
   rest of the analysis does not know the difference: a site is a feature of
   level `site` and kind `intensity`, named `GENE R123` with the id
   `sp|ACC|ENTRY|R123` (the isoDTB site convention, so D70's matching and the
   search work), and goes through the same filter, normalisation, imputation,
   limma, volcano, heatmap, enrichment and report. The readers were built from
   the real column names, not from documentation alone:
   - FragPipe label-free: IonQuant's `combined_site_STY_79.9663.tsv`
     (`Index` = `ACC_S142`, `Gene`, `Protein`, `Protein ID`, `Peptide`,
     `Best Localization Probability`, per sample `… Localization
     Probability`, `… Intensity`, `… MaxLFQ Intensity`; 0 = missing). The
     header was taken from a public FragPipe output
     (prolfqua/prolfquappPTMreaders' example) and Nesvilab's DDA+ notebook;
     FragPipe's own documentation describes only the TMT site reports.
     MaxLFQ is used when it has values, as for proteins.
   - FragPipe TMT: TMT-Integrator's `abundance_single-site_MD.tsv` (`Index`,
     `Gene`, `ProteinID`, `Peptide`, `SequenceWindow`, …,
     `ReferenceIntensity`, then the channels), read with the TMT loader so
     the annotation, plexes and "already relative to the reference" rules are
     the protein table's. Columns as FragPipe-Analyst and FragPipeAnalystR
     read them.
   - DIA-NN: `report.phosphosites_90.tsv` / `_99.tsv` (`Protein`,
     `Protein.Names`, `Gene.Names`, `Residue`, `Site`, `Sequence`, then the
     runs; 0 = missing), named in DIA-NN's README (1.9 – 2.0) and with the
     header of a public DIA-NN output; runs are matched to the manifest as
     for the protein matrix. DIA-NN recommends its Parquet site report over
     these matrices; reading that is not built.
   No site table: the proteins are analysed and `PHOSPHO_TABLE` (input) says
   so. `phospho_table` names one explicitly. Alternatives weighed: finding the
   site table without a setting (would change existing LFQ-phospho
   experiments, whose protein table is analysed today); a separate method
   (`like: LFQ` plus a flag is what the lab would write anyway).
2. **The localisation filter is a setting, and Ionomos says when it cannot
   apply it.** `phospho_min_localization` (0.75, the common class I
   threshold) is applied to the label-free table's best localisation
   probability; with `phospho_localization_per_sample` a sample's value whose
   own probability is lower is also dropped. TMT-Integrator's report and
   DIA-NN's matrices carry no probabilities: they were filtered before
   (TMT-Integrator's `min_site_prob`, read from `fragpipe.workflow`; DIA-NN's
   0.9 / 0.99, and the 0.99 matrix is read when the setting is above 0.9).
   When their threshold is lower than the setting, or unknown,
   `PHOSPHO_LOCALISATION` (warning) says so; nothing is re-filtered on data
   that cannot be.
3. **Protein correction is D70's, per comparison.** With `protein_correction`
   each site comparison (Drug vs DMSO) is corrected by the proteome's
   comparison of the same two conditions (or the one named in `conditions:`,
   keyed by the comparison's name or its treatment); never a guess. The
   per-comparison block of `proteincorr.run` became
   `proteincorr.correct_comparison`, used by both (the D70 goldens pass
   unchanged). Both scales are log2 treatment / control, so the protein's
   change is used as it is. FragPipeAnalystR's `PTM_normalization` (a
   regression of site on protein per sample) was not ported: D70's
   adjustment keeps the site and protein uncertainties, which the regression
   does not.
4. **KSEA as KSEAapp computes it, with two documented differences.**
   (Casado et al., Sci. Signal. 2013; KSEAapp 2.0, MIT.) Per comparison, on
   every site with a log2 fold change: the mean and SD (n − 1) of all of
   them; a kinase's substrates are the measured sites the table lists for it,
   matched on substrate gene and residue (`ksea_match: gene`, KSEAapp's
   merge) or UniProt accession (`protein`); a site measured twice is averaged
   first per (kinase, site, source), as KSEAapp's `aggregate`; mS, Enrichment
   = mS / |mean|, z = (mS − mean) √m / SD. Different: **p is two-sided**
   (KSEAapp's `pnorm(-|z|)` is one-sided; a kinase can go either way; the
   table keeps `p_one_sided`), and **BH runs over the kinases reported**, those
   with at least `ksea_min_substrates` (5, KSEAapp's usual `m.cutoff`)
   substrates (KSEAapp adjusts over every kinase, then filters). A kinase is
   "more / less active" at adjusted p ≤ `alpha`. The z-score is a statement
   about the substrates, not a measurement of the kinase; the report says so.
5. **The kinase-substrate table is the lab's download, never shipped.**
   PhosphoSitePlus is free for non-commercial use only (CC BY-NC-SA), so, as
   CysDB in D52, `kinase_substrates` names a file (a full path or one in the
   experiment folder; `.gz` read as is; its licence lines before the header
   skipped); it is only read. Read: PhosphoSitePlus's
   `Kinase_Substrate_Dataset` (`GENE` = the kinase's gene, `SUB_GENE`,
   `SUB_ACC_ID`, `SUB_MOD_RSD`, organisms), KSEAapp's PSP&NetworKIN file
   (`Source`, `networkin_score`; NetworKIN rows only with `ksea_networkin`
   and a score ≥ `ksea_networkin_score`, PSP rows' `Inf` kept, as KSEAapp),
   or any table with those columns. `ksea_organism` (human) leaves other
   organisms out. A missing or unusable file, or no kinase with enough
   substrates, is `KINASE_SUBSTRATES` (warning); the site results are not
   affected.
6. **STRING partners: a table, for any experiment.** `string_network` names
   a STRING download (CC BY 4.0): `protein.links` with the `protein.info`
   file in the same folder for the names (the links file is streamed and only
   pairs of hit genes are kept), or a network exported from the website.
   Per comparison, each hit gene with its partners among that comparison's
   hits at combined score ≥ `string_min_score` (700, STRING's "high
   confidence"). Not drawn as a network: a layout readable at 100+ nodes is
   its own project. `STRING_NETWORK` (warning) when the file cannot be used.
7. **CORUM is not built.** Its licence was the open question (ROADMAP Phase
   4, D35): release 5.1 (Zenodo, January 2025) is CC BY 4.0, earlier ones
   CC BY-NC. A user download would now be allowed, but its file layout was
   not checked and the task was low priority.
8. **Report and figures in the D62 / D68 style.** A section "Phosphosites,
   kinases, partners" (named after what ran): the filter's tiles, a KSEA bar
   chart per comparison (click a kinase for its substrates, and a button that
   finds them in the volcano), the kinase table, the partners table. The bar
   chart is exported like every chart and is the static figure
   `kinase_activity` (`analysis.export.figures`, `ionomos export`,
   `sectionfigs.figure_kinase_activity`; the kinases with the largest |z|
   when not all fit). The site volcano is the existing volcano (the features
   are sites). `results/kinase_activity.tsv`, `results/string_partners.tsv`;
   no FragPipe-Analyst `reproduce_in_R.R` for a site analysis (it would read
   the site table as proteins).

**Verified**: KSEA against KSEAapp 2.0 (R 4.6.1, installed into a private
library) on made-up sites and kinase-substrate pairs with duplicated sites, a
pair under two names of one kinase and NetworKIN rows: m, mS, Enrichment, z
and KSEAapp's one-sided p to 1e-10, and the two-sided p and the BH over m ≥ 5
computed in the same R script (`tests/golden/ksea/`, `run_kseaapp.R`; the test
needs no R); a hand-computed case; the three readers on files with the real
headers; whole analyses of a simulated LFQ-phospho experiment with phospho
off (identical protein analysis) and on (the planted kinase called, the
partners found, the figure written); the protein correction against
`proteincorr.adjust`; the doctor's issues; the report section and its export
in jsdom, and the section, a kinase click, the volcano search it starts and the
exported `kinase_activity` SVG by eye in Chromium (the desktop app's browser
pane, a simulated experiment served locally). **Not verified**: real data of any kind (no phospho search from the
lab; the readers rest on public files and documentation); a real
PhosphoSitePlus or STRING download (made-up tables in their layouts);
TMT-Integrator's `min_site_prob` key in a real FragPipe 24 workflow; Firefox,
Safari, Edge; Windows.

### D80 — A folder is checked before it is analysed; what it holds beats what it was filed as
**2026-10-06.** A lab member ran a DIA search in FragPipe themselves. The
folder had been filed as TMT, so the Analysis tab looked for TMT-Integrator's
table, found nothing, and could only say "no tmt-report/abundance_*_MD.tsv
found". The method came from `ionomos.json`, a name someone chose, and the
tables only counted when nothing was filed (`detect_method`, which also tries
TMT's file names before DIA's). `fpfolder.py` (no Tk), `folder_check.py`
(the window), `dragdrop.py`.

1. **Evidence, most trusted first.** The method is decided in this order:
   - the tables that are there and readable
   - the workflow FragPipe saved beside them (`fragpipe.workflow`: its
     `run-*` switches, as `fragpipe.workflow_needs` reads them;
     `ionquant.use-labeling` separates isoDTB from label-free)
   - the manifest's data types
   - what the folder was filed as

   A table only one kind of search writes (isoDTB's label quant, TMT-Integrator's
   abundance, DIA-NN's pg_matrix) decides on its own. `combined_protein.tsv`
   does not: FragPipe writes one in DIA and TMT searches too, sometimes with
   spectral counts only. It counts as label-free only with Intensity columns,
   and only when nothing else points elsewhere; otherwise the window asks.
2. **The correction is also made without the window.** `postprocess.prepare`
   switches when the filed method's table is missing and the evidence is
   strong. A note goes in the outcome's warnings and in the editor. Analysing
   as the filed method would fail for certain, so the switch can only turn a
   failure into a result. `ionomos analyze --method X` is explicit and keeps X
   (`strict`). An Ionomos job's own folder never changes: its table is there.
3. **The output can be anywhere below the picked folder** (depth 4, at most
   4000 folders, so a whole drive picked by mistake can't hang the window).
   Ionomos' own `ionomos_run/` and `results/` and earlier `_previous_`
   attempts are skipped. An output folder is one with FragPipe's own files
   (its workflow, manifest, job file or a `log_*.txt` that mentions
   FragPipe) or with a table. Each table belongs to the nearest output above
   it. If the picked folder is part of an output (`dia-quant-output/`, a
   table), the output above it is used.
4. **Where results go.** If the picked folder holds exactly one output,
   `results/` goes in the picked folder (beside the raw files, as for a
   job). With several outputs it goes in the chosen output, so a re-run
   finds the same one. `fpfolder.locate` gives `prepare` that output folder
   as `workdir`, and `analyze()` takes it as a parameter. FragPipe's files
   are only read.
5. **FragPipe's manifest names the samples.** A folder with no Ionomos
   manifest takes FragPipe's `fragpipe-files.fp-manifest` as its plan
   manifest. That does not apply to TMT, where its experiments are plexes,
   not conditions. Without experiment names, or with every run its own
   experiment, the conditions are guessed from the file names
   (`doctor.suggest_conditions`). The window offers that guess, on by
   default, and the samples list shows it to check.
6. **Every problem says what is wrong, what is missing and what to do.**
   Each finding is error (can't run), input (decide; the recommendation is
   already chosen), warning or info. Most carry buttons for what can be done
   in the window: analyse as another kind, use another output, use the
   guessed conditions, open the log or the folder, choose another folder.
   The failed step and likely cause come from FragPipe's log with
   `fragpipe.failed_step` / `explain`, the same reading a job's failure gets.
   A finished job opens the window only when it is filed as another method,
   holds several outputs, or can't be analysed. Otherwise it loads as before.
7. **Drag and drop without a dependency.** Tk has none for files.
   `tkinterdnd2` would add native binaries to the installer, so on Windows
   the window accepts files (`DragAcceptFiles`) and its window procedure is
   wrapped to read `WM_DROPFILES`. `ChangeWindowMessageFilterEx` lets an
   elevated app still take drops from Explorer. Any failure leaves drops
   off and Folder… works. Other systems: no drops.
8. **TMT-Integrator's other normalisations.** `abundance_*_GN.tsv` and
   `_None.tsv` are read when there is no `_MD`, with a note. A lab member's
   own TMT search need not use the lab's median centring.

**Verified**: tests on made-up folders in FragPipe 23's layout
(`tests/test_fpfolder.py`): the DIA-filed-as-TMT case end to end (check,
`inspect_folder`, a full analysis with the manifest's conditions), an
explicit `--method` kept, a failed DIA-NN step with its cause from the log,
raw files only, an empty folder, several outputs, part of an output, an
Ionomos job folder unchanged, no condition names, TMT `_None`, spectral
counts only, `check-folder`. The window and a drop through
`AnalysisTab.dropped` in `tests/test_app.py` (CI only). **Not verified**: a
real drop from Explorer on Windows; a real FragPipe GUI run's
`log_<date>.txt`; real folders from searches the lab ran by hand (ROADMAP
Phase 4).

### D81 — Every program the frozen app starts unpacks its own Python
**2026-10-06.** Since 0.1, installing or updating on the PC ended with
"Failed to load Python DLL 'C:\\Users\\…\\Temp\\_MEI…\\python314.dll'.
LoadLibrary: The specified module could not be found." Ionomos.exe is a
one-file PyInstaller build. It unpacks Python into `%TEMP%\_MEI<n>` and
puts that folder in its environment (`_PYI_ARCHIVE_FILE`,
`_PYI_APPLICATION_HOME_DIR`, `_PYI_PARENT_PROCESS_LEVEL`). A child that is
the same exe reuses the folder instead of unpacking its own, and the folder
is deleted when the parent exits.

The chain:
1. The app starts the installer, and the installer inherits the app's
   environment.
2. The app exits, and its `_MEI` folder is deleted.
3. The installer's last step starts the new Ionomos.exe, which inherits
   those variables.
4. That Ionomos.exe looks for Python in the deleted folder.

Opening a downloaded installer from the app (`os.startfile`) has the same
chain. The watcher started from the app (`Ionomos.exe run`, same exe) also
shared the app's folder, so closing the app could delete files the watcher
had not loaded yet (the report's assets, the help, extension modules).

1. **`__main__.independent_children()`**: a frozen Ionomos sets
   `PYINSTALLER_RESET_ENVIRONMENT=1` (PyInstaller ≥ 6.9; the build uses
   ≥ 6.10) for everything it starts. Each Ionomos child then unpacks its own
   copy. That costs a few seconds and some MB in `%TEMP%` per watcher or
   reopen, which is fine for processes that outlive their parent.
2. **The installer sets it too** (`InitializeSetup`, `SetEnvironmentVariableW`),
   so the first update from an older app, which still hands over its old
   environment, is fixed by the new installer.

**Verified**: the bootloader in PyInstaller 6.22 reads these variables
(its strings); a test that the frozen entry point and the installer set it.
**Not verified**: an update on the PC (0.18.0 → the next release is the
test: no dialog at the end).

### D82 — The drop handler never calls Tk; hard crashes leave a stack
**2026-10-06.** The first real drop on the PC (0.18.0) froze the app. D80's
window procedure ran on every drop inside Tk's event loop. There, _tkinter
holds its Tcl lock and has let go of Python's lock (it runs
`Tcl_DoOneEvent` between `ENTER_TCL` and `LEAVE_TCL`). The procedure
called `top.after(...)`, a Tk call that waits for that Tcl lock. The same
thread already holds it, so the call waited forever. The windnd package,
which uses the same technique, warns about this in its own way: call nothing
of tkinter in the handler.

1. **The procedure only queues.** It reads the names with `DragQueryFileW`,
   puts them in a `queue.SimpleQueue` and returns. A Tk timer (150 ms) takes
   them out and calls the handler on the Tk thread, as the app's own `post`
   does for worker threads. A handler that fails is logged; the timer and
   later drops go on (`dragdrop.Drops`).
2. **Nothing crosses into Windows.** Every message goes to Tk's own procedure
   whatever happens in ours. On `WM_NCDESTROY` Tk's procedure is put back, so
   no message reaches Python code after the window (or Python) is gone.
3. **`AnalysisTab.dropped` never raises.** These drops get a message saying
   what to do: a shortcut (`.lnk`), a zip, a path Windows hands over that
   isn't a file or folder (a library, a phone, a zip's contents, a drive that
   is gone). Anything else that fails gets an error box that names it and
   points to Folder…. The app comes to the front, so the check window is
   seen over Explorer.
4. **Tested with a real drop.** On Windows CI, `tests/test_app.py` posts the
   message Explorer sends (`WM_DROPFILES` with a `DROPFILES` block of
   wide-character names) to the app's window. 0.18.0 would hang in that
   test. The queue and timer are tested everywhere with a stand-in window.
5. **`Ionomos-fault.log`.** The frozen exe turns on `faulthandler` into a file
   next to it. A crash Python can't catch (an access violation, a hang ended
   by Windows) then leaves every thread's stack instead of nothing, and the
   diagnostics include the end of it. The file is opened for appending, so
   it exists, empty, after the first start.

**Not verified**: a drop from Explorer by hand on the PC (the CI test sends
Explorer's message itself, which is not quite the same as dragging); the
fault log on a real hard crash.

### D83 — Real runs are logged; a difference from FragPipe-Analyst is first re-run with its settings
**2026-10-09.** Real runs had happened (two DIA searches on 2026-09-23, an
analysis of a FragPipe GUI result on 2026-10-06), but the docs still said
nothing had run on a real FragPipe. [REAL_RUNS.md](REAL_RUNS.md) is now the
record: one row per real search or analysis that teaches something, what
went wrong, and which version fixed it or that it is open. Evidence is the
support bundles and the experiment's own files. The repository is public, so
protein names and hit lists of unpublished experiments stay out of it.

The 2026-10-06 analysis was run on fragpipe-analyst.org too, and one protein
was a hit there and not in Ionomos. The port was not changed, because the
difference was a setting:

1. Ionomos re-run on the run's own matrix gave the identical result.
2. FragPipe-Analyst's code (the web app and FragPipeAnalystR, read on
   2026-10-09) does what the port does when the settings match. This covers
   the filter rules, median centring, Perseus draws in dplyr's sorted sample
   order with `set.seed(123)`, `test_limma` and `add_rejections`.
3. The web app's defaults are no filter, no normalisation and all pairs.
   The protein was measured in the three compound runs only; this run's
   global filter (≥ 66 % of all samples, so 6 of 9) removed it, while the
   web imputed its six missing values and called it a strong hit. Without
   the filter (and with median centring), Ionomos gives 19 of the web's 21
   hits, that protein included; the other three sit on the cut-offs.
   (Corrected the same day. The first version of this entry blamed the
   normalisation and named a different protein, from a guess made before
   the web's volcano was seen.)

So, when a result differs from FragPipe-Analyst: re-run with the Analysis
tab's **Use FragPipe-Analyst's defaults** (or the web's settings) before
suspecting the port, and compare whole tables with `ionomos compare`, not
hit lists by eye.

Found on the way, open in ROADMAP.md: FragPipe's `contam_` prefix does not
survive into DIA-NN's tables, so contaminants are kept (also by
FragPipe-Analyst); FragPipeAnalystR's `make_se_from_files` removes
contaminants for LFQ only, which `fpa.py`'s docstring now says; a
non-UTF-8 `config.yaml` still crashes; `experiment.yaml` replicates are not
bounded; the end-to-end golden is tidier than a real matrix.

**Not verified**: FragPipe-Analyst's own output for this experiment (not
kept, settings not written down). The protein was identified by re-running
Ionomos, not read from FragPipe-Analyst's table.

### D86 — Hand-edited YAML that isn't UTF-8 is read as cp1252 with a warning
**2026-10-09.** On 2026-09-16 LabWatch 0.1.0 stopped on the PC with
`UnicodeDecodeError … byte 0x97` reading `config.yaml`: Notepad had saved it
as "ANSI", and the em dash in a comment became cp1252's byte 0x97.
0.18.1 still did the same, and not only for `config.yaml`: every reader
of a hand-edited YAML file used `read_text(encoding="utf-8")`
(REAL_RUNS.md, Open).

Two ways out were weighed: read cp1252 when UTF-8 fails, or refuse with a
`ConfigError` that says where and how to re-save. Ionomos now reads
cp1252, with a warning:

1. **The PC's "ANSI" is cp1252**, so the fallback reads such a file as
   Notepad meant it. A wrong letter in a comment changes nothing, and the
   values that matter (paths, method keys) are ASCII.
2. **A refusal stops the watcher**, and drops then pile up in the inbox
   until somebody edits a file in a way they can't see is wrong. Nothing is
   lost by reading on: the warning names the file, the first byte that
   isn't UTF-8 and its line, and how to re-save. For `config.yaml` it goes
   into `Config.warnings`: the `ionomos` commands print it (`status`
   included, where LabWatch crashed), and the running watcher logs it when a
   reload brings it. For the other files it is logged. D69 reads FragPipe's
   console the same way (UTF-8 when it is, else cp1252).
3. **A `ConfigError` is left only for what cp1252 can't read** (its five
   unused bytes 0x81, 0x8D, 0x8F, 0x90, 0x9D, or broken UTF-16). It names
   both bytes and lines and says how to save as UTF-8. The watcher's live
   reload keeps the last good config on it; before, the bare
   `UnicodeDecodeError` escaped `LiveConfig.get`.

All of it is `config.read_yaml_text`: UTF-8 with or without a BOM, UTF-16
with its BOM (Notepad's "Unicode"), then cp1252. It reads `config.yaml`
(`load`, `configio.read_config`, the diagnostics' paths and secrets),
`learned_aliases.yaml`, `experiment.yaml` (`load_overrides`,
`overrides_text`, the assistant's diff), a benchmark's spec, and the
support bundle's copies. Two of these mattered beyond a crash:

- **Redaction.** `notify.file_secrets` read a cp1252 config as nothing. A
  webhook address was then hidden only on a `url:` line, not in the logs or
  in the short form `teams: <address>`.
- **The bundle's anonymiser** read it as `{}` too, so the users and aliases
  of the config went unscrubbed. The bundle's text copy of a YAML file is
  read the same way as the anonymiser reads it, so a name with `ü` is
  matched, not left as `Gr�n`.

An `experiment.yaml` that can't be read at all raises `OverridesError`. A
save merging over it stops, rather than writing a file without what it said.
An unreadable `learned_aliases.yaml` is skipped when the config loads, as a
broken one already was. But learning a new alias (the resolver window) no
longer rewrites it with only the new alias; it is left as it is and the
failure is logged. That is new for broken YAML too.
Ionomos writes UTF-8 everywhere, so a file is converted the first time the
app saves it.

**Not verified**: the warning on the PC itself. That the PC's "ANSI" is
cp1252 is inferred from the em dash arriving as 0x97 (PROTEOMICS_PC.md does
not record the code page).
