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
