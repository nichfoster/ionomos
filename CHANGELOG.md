# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **A feature opens from the keyboard in the report** (D116, #176): every table whose rows open something is one
  Tab stop; arrows, Home, End, Page Up / Down move, Enter or Space opens the row (Shift+Enter pins), and the row
  rings its point on the chart beside it with its tooltip. Pin and "Behaves like" chips are buttons.

- **The app, slices 2–4 of docs/UI.md (D95): robust for daily driving, and tested with hidden windows.**
  - The tabs are grouped **Today · Inbox · Jobs · Analysis | Setup · Folders · Users · Methods · Advanced ·
    Run & Test · Notifications | Help** and lose their numbers; every help text, checklist line and hint names
    a tab by name (a test fails on "tab 3"). Ctrl+1 … Ctrl+9 jump to the tab in that position. (#114)
  - The line under the bottom bar shows the watcher's state and the FragPipe queue on every tab. (#115)
  - The Setup list scrolls on a laptop; the experiment editor's Run analysis / Save choices / Open report row
    is at its top, and the editor scrolls inside the analysis pop-ups. (#116)
  - Run & Test has a Watcher page and a Testing page (the testbed, the development-install box); the job-number
    prompts (Retry a failed job…, Open job folder…) are gone — the Jobs tab does both on a selected row — and
    there is one Stop watcher; Save & Check lives in the bottom bar only. (#117)
  - Pop-up windows size themselves in text units (`tkutil.text_size`, `fit_window`) and are clamped to the
    screen, so columns are not cut at 150 % scaling. (#118)
  - Every error box says what to do next (D46); `tests/test_ui_words.py` reads every `showerror` in the Tk
    modules and fails when one names what happened but not the next step. (#119)
  - Nothing slow runs on the Tk thread: Import workflow's FASTA copy, the Instrument QC page, Reset testbed,
    waiting for the watcher to stop (Quit, Update), the 2 s status tick's ledger and lock-file reads, the log
    follow and the Inbox scan (now only while the Inbox tab is shown, with the path and "empty" said) run on
    worker threads and come back through `App.post`. (#120)
  - One busy indicator: `App.run_bg(label, fn, done)` shows "working: …" with a progress bar in the bottom bar,
    disables the button that started the work and refuses a second start of the same work (D67's rule in one
    place). Used by the checks, Check FragPipe install, Re-run analysis, Import workflow, the QC page,
    diagnostics, the bundle and updates. (#121)
  - **Undo last save** (Advanced → Config file) brings the newest `config-backups/` copy back into the window;
    Restore a previous config… lists every backup with its time and what differs from the active config. Two
    Saves in one second no longer overwrite the same backup. (#122)
  - Save shows **what will change**, in words (`configio.diff_words`: "FragPipe → Threads: 24 → 28"), and Cancel
    writes nothing; nothing changed = nothing written; a tick skips the window for a single changed setting
    (remembered in `ionomos-ui.json`). (#123)
  - Unsaved changes are not lost silently: a dot in the title, and Quit, Reload and Change… ask Save / Discard /
    Cancel. (#124)
  - **Check everything** (the Setup tab, `ionomos doctor`): one list from the setup checklist, FragPipe read from
    disk (no start), the workflow library's update check, health (crash reports, log errors, disk) and what needs
    you (open items, paused searches), each row with what to do and a Fix button where the app can do it
    (`checks.py`, no Tk). The full preflight with dry runs stays Jobs → Check FragPipe install. (#125)
  - The quick check runs when the app opens; Today's line says "Checks: N problems". (#126)
  - Number fields on Advanced show the Folders tab's ● dot and the problem in the hint while a value is one the
    loader refuses (`forms.check_setting`). (#127)
  - `tests/tkharness.py`: hidden-window tests (every Toplevel withdrawn, dialogs recorded, Tk errors captured;
    fixtures `hidden_app`, `no_dialogs`, `tk_errors`). (#128)
  - `tests/test_ui_smoke.py`: every tab refreshed with F5 and F1, every window of the inventory opened with fake
    data and checked to fit a 1366×768 screen (#129); the untested windows got tests (Test names, Change…,
    Download and Prepare dialogs, the colour picker, the startup-task buttons, Reset testbed) (#130);
    `scripts/screenshots.py` saves a PNG of every tab and window at the natural size and at 1366×768, and
    Windows CI uploads them as an artifact (#131); a layout test checks the main buttons are inside the window at
    1366×768 (#132).
- **The workflow and FASTA library: `ionomos library`** (D89, docs/LIBRARY.md).
  The PC holds well over a hundred FragPipe workflows (one in every output
  folder on `D:\`, custom ones such as `fragpipe_pax8.workflow`, the stock ones
  of three FragPipe versions) and FASTAs in several places, and nothing said
  which is which. `ionomos library scan` finds them (read-only; the pinned
  folders, every FragPipe installation, users_root and `library.roots`, e.g.
  `D:/`; at most 6 folders deep and 120 s, then it says how far it got), and
  describes each in words: *"A TMT-10 MS3 search with TMT-Integrator:
  carbamidomethyl C, TMT K fixed; …; saved with FragPipe 24.0"*, *"Human,
  reviewed UniProt (20,698 proteins) with decoys (rev_) and N contaminants,
  made 2026-01-30"*. 25 marks with what to do (`FASTA_NO_DECOYS`,
  `FASTA_DECOY_SHARE`, `WF_TOOL_MISSING`, `WF_SDRF_ON`, `WF_DIFFERS_FROM_PINNED`
  …), each with a help entry. `list`, `show PATH`, `recommend` (the best
  workflow and FASTA per method, with reasons: finished searches that used it,
  the FragPipe version, its FASTA), `recommend --apply` (only for methods
  whose files are missing or broken; config.yaml backed up first), `adopt PATH
  --method KEY` (copies, never moves; the replaced file kept as `.bak-<time>`;
  its FASTA copied in under a name without spaces) and `check` (a newer stock
  workflow, an old FASTA, a newer FASTA of the same proteome). The catalog is
  `logs/library.json`. A 1 GB FASTA is read in under 2 s, once per version.
  A search's own `fragpipe.workflow` copy (it differs only in `workdir=`)
  counts as the pinned workflow's evidence, not as a rival, and FragPipe's
  per-search `protein.fas` is never taken for a search database.
  `config.yaml` has a `library:` block (`roots`, `exclude`, `max_depth`,
  `time_limit_s`); a config without it gets the defaults.
- **App, tab 3 Methods: a panel "Workflows and FASTAs on this PC"**
  (`library_tab.py`, D89/D90).
  - **Find on this PC…** lists every FragPipe workflow and FASTA the library
    found, ranked for the selected method (kind, name, folder, summary, marks,
    why recommended), with the details of each (settings, every flag and what
    to do). The scan runs in the background with a Stop button and is
    remembered, so the window opens at once next time (Rescan looks again).
    **Use for <method>** copies the file into the workflows / FASTA folder
    (never moves it) and puts it in the method's box; **Open folder**;
    **Show all** and a filter.
  - **Choose…** beside the workflow and FASTA boxes: the same list, only
    workflows or only FASTAs. The method's FASTA is described in words under
    the buttons (read in the background; FASTAs can be 1 GB).
  - **Download from UniProt…** (organism → proteome → reviewed / isoforms →
    download + decoys and contaminants into the FASTA folder, progress and
    Stop) and **Add decoys and contaminants…** (a FASTA you pick → a new
    FragPipe-ready file; the source is not changed), each ending with **Use
    for <method>**. **Check for updates**: newer stock workflows, old or
    superseded FASTAs, each FASTA against UniProt's current release. Changes
    nothing.
  - First setup: when a method's workflow or FASTA is missing or broken, a
    banner "Ionomos found N workflows and M FASTAs on this PC …" → **Review
    proposals…** (the file Ionomos would use for each such method, with
    reasons). Only **Apply** changes anything; config.yaml is saved through
    the app's Save (checked, backed up).
  - Setup checklist: "Workflows and FASTAs: N pinned, flags: …" from the last
    scan (no scan during a check). Help: "How do I find or update a workflow
    or FASTA?" (`faq.library`).
- **FASTAs from UniProt, made ready for FragPipe, and a check of how old they
  are** (D90, docs/FASTA.md). `ionomos fasta search human` finds a proteome's
  id; `ionomos fasta download UP000005640` downloads its reviewed entries
  (`--unreviewed`, `--isoforms`) from UniProt's REST API, checks the count
  against UniProt's, and adds `rev_` decoys and `contam_` contaminants with
  FragPipe's own Philosopher, as FragPipe's Database tab does (the same steps
  in Python when there is no FragPipe). The result is named as FragPipe names
  it (`2026-10-14-decoys-reviewed-contam-UP000005640_9606.fasta.fas`) and
  passes FragPipe's 40–60 % decoy rule. `ionomos fasta prepare FILE` does the
  second half for a FASTA you have; `ionomos fasta release` says, for each
  FASTA in the FASTA folder, whether UniProt has released since it was made;
  `ionomos fasta stock-workflows 24.0 [--get NAME]` lists or saves FragPipe's
  stock workflows of a release from its GitHub source. Nothing is overwritten;
  offline, every command says so instead of failing. New help entry "How do I
  get or update a FASTA?" (`faq.fasta`).
- **TMT, end to end** (D91, WORKFLOWS.md). `tmtkit.py` is the one place that
  knows TMT label types (TMT-6/10/11, TMTpro 16/18: FragPipe's channels in
  order, reagent masses 229.1629 / 304.2071), reads annotation files as
  FragPipe does, applies FragPipe's channel-map rules and Ionomos' own checks
  as flags with fixes, and plans every plex of a drop (the map from
  `experiment.yaml` or the drop's own `annotation.txt`, read only).
  `ionomos tmt plan <folder>` (what will be written and every flag, read
  only), `ionomos tmt annotation <folder> [--write]` (never over an existing
  file), `ionomos tmt suggest --tag TMT-16 --conditions DMSO,Drug
  --replicates 3 --reference 126 [--plexes a,b]` (a `tmt:` block to paste),
  `ionomos tmt qc <folder>`. Each refuses a job that is not TMT ("this is a DIA
  job, not TMT"), and `suggest` refuses an impossible map (no replicates, a
  condition or reference twice, more samples than channels) in one sentence.
- **TMT QC after the search** (`downstream/tmtqc.py`, D91): per plex and
  channel from `psm.tsv` (reporter signal, distance from the other channels,
  the reference), labelling efficiency (N-termini, K, fully labelled),
  precursor purity. `results/tmt_qc.json`, `results/tmt_qc_<plex>.tsv`,
  `analysis.json` → `tmt_qc`, a report section **TMT plexes and channels**
  (channel chart per plex, labelling bars, purity histogram; all three export
  as figures, `--figures tmt`), and doctor issues `TMT_LOW_LABELLING`,
  `TMT_LOW_PURITY`, `TMT_CHANNEL_EMPTY`, `TMT_REFERENCE_WEAK`,
  `TMT_LABEL_TYPE_MISMATCH`, `TMT_CONDITIONS_UNKNOWN`. Runs for TMT kinds
  only, also when `analysis.enabled` is off. Analysis settings
  `tmt_min_labelling` (0.95) and `tmt_min_purity` (0.7).
- Testbed: scenarios `tmt16_na`, `tmt_tag_mismatch`, `tmt_own_annotation`,
  `tmt_bridge`, `tmt_poor_label`; a `TMT16.workflow`; the fake FragPipe writes
  TMT `psm.tsv` files (reporter columns, Purity, labels) and the `tmt-report`
  set (gene/protein abundance and ratio, `_None`, peptide, single-site with
  phospho), acts out `tmt-poor-labelling`, `tmt-low-purity`,
  `tmt-empty-channel`, and stops on a missing reference tag as FragPipe does.
- Help: `report.tmt`, glossary (channel, plex, label type, reference/bridge,
  labelling efficiency, precursor purity, ratio compression, MS2 vs MS3),
  troubleshooting for every new issue and `trouble.tmt-map`.
- **Docs for a published tool.** `docs/VISION.md`: what Ionomos is for in one paragraph, who it is for (the lab
  running the PC, a lab member who only drops folders, a core facility, a scientist with a table, another lab, a
  reviewer), its principles with the decisions behind them, what it is not, a comparison with FragPipe-Analyst,
  MSstats, Perseus, Prostar, DEP, MS-DAP, quantms and limpa (what each does that Ionomos does not), the road to
  publishing (PyPI, a real-data example from PRIDE, the evidence, JOSS, a preprint and a J. Proteome Res.
  technical note, a documentation site), versioning and support promises from 1.0, and what "done for v1.0"
  means (D92).
- **Community files**: `CITATION.cff` (GitHub's "Cite this repository"; it also lists FragPipe-Analyst, limma and
  CurveCurator, whose methods are ported), `CONTRIBUTING.md` (reporting with the diagnostics or a bundle, the dev
  loop, the rules: docs are the spec, the decision log, agents open pull requests and a person merges them),
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), issue templates for bugs and features, and a pull-request
  template.
- The app opens on a **Today** tab: what needs you (the attention items, Open = the pop-up with what to do), the
  watcher and queue line, the setup checklist's one line, and the last 15 jobs with Open report, Open folder,
  Re-run analysis, Analysis options… and Show in Jobs. Read off the Tk thread (`uinav.snapshot`). A new install
  still opens on ✓ Setup. (#109)
- The app remembers its window size, maximised state and last tab in `ionomos-ui.json` next to `config.yaml`
  (`names.UI_STATE_FILE`), fitted to the screen on the next start; a window bigger than the screen is shrunk. (#110)
- **F1** opens the help page at the current tab's topic; **F5** refreshes the tab; **Ctrl+S** saves; **Ctrl+F**
  finds a job; **Ctrl+0** Today, **Ctrl+1 … Ctrl+8** the tab with that number; Ctrl+Tab / Ctrl+Shift+Tab; Enter
  opens the report in a job list. Listed on the Help tab and in the new help entry `faq.app`. (#112)
- A **Find** box on the Jobs tab (every word must match; `12` / `#12` is job 12; Escape clears). (#113)
- `docs/UI.md`: the UI's principles, every tab and window and who uses it, what was seen on screen, and the plan
  in four slices (#109–#132).
- **TMT channels** editor (`tmt_editor.py`, D91): a grid per plex (channel, sample name, condition, replicate,
  role: sample / reference (bridge, pooled) / NA), the label type pre-set from the method's workflow
  (`tmtintegrator.channel_num`, an orange line when another is picked), **Read annotation.txt from the folder**
  (never changed), **Suggest…** (conditions, replicates per condition, reference channel), **Clear**, FragPipe's
  rules and Ionomos' checks under the grid in `tmtkit`'s own words (what FragPipe would stop on in red), and a
  summary in plain words.
  - In the **review window** for a TMT drop, under the file table (pre-filled from `experiment.yaml` `tmt:`, else
    the drop's own `annotation.txt`, else empty with "Suggest… (start here)"). Accept writes the map as
    `experiment.yaml` `tmt:`; a map FragPipe would stop on keeps the window open with the reason; a cleared grid
    removes an old `tmt:` block.
  - On the **Analysis tab** (experiment editor) for a TMT experiment: Save choices / Run analysis write `tmt:`
    (the old `experiment.yaml` kept in `experiment-backups/`); the names are used by the next search.
- Help: "How do I tell Ionomos which TMT channel is which sample?" (`faq.tmt-channels`); a line in the TMT
  paragraph of Getting started.
- `tmtkit.reference_conditions(tmt, ref_tag)`.
- **A real-data example: `ionomos example`** (D96, docs/EXAMPLE.md, `example.py`). It downloads the example
  datasets of FragPipe-Analyst's own paper (Hsiao et al., J. Proteome Res. 2024; Zenodo
  doi:10.5281/zenodo.13147961, CC BY 4.0, 20.9 MB), checks the size, MD5 and SHA-256, and writes
  `./ionomos_example/` with three real experiments: a label-free cyclin D1 pull-down (CCND1 3 vs control 4), and
  clear cell renal cell carcinoma tumours against normal tissue (20 vs 12) by DIA-NN and by four TMT-11 plexes.
  Each table is copied out of the zip byte for byte. The design (`experiment.yaml`) comes from the authors'
  `experiment_annotation.tsv`, leaving out what they left out. Each experiment is analysed like `ionomos analyze`,
  and each folder's README.txt says what its report should show. The whole run takes under a minute.
  `--dataset apms|dia|tmt`, `--list`, `--zip PATH` (a copy downloaded by hand), `--no-enrichment`, `--open`.
  The download goes to a `.part` file and is renamed only once it is verified. A copy kept by an earlier run, or
  one in the current folder, is reused, so no network is needed the second time. Offline (or with
  `IONOMOS_OFFLINE`), the message names the URL and the `--zip` command. It never writes into a folder that holds
  anything. The data are never bundled. `tests/golden/example/` holds a 240-row excerpt of each table with its
  licence, so the tests run the whole path offline.
- docs/EXAMPLE.md: the data, how to run it, and a walk through the three reports: the cyclin D1 complex (CDK4/6/2,
  p21/p27/p57) found and seen only in the bait; CA9 up and oxidative phosphorylation down in the tumours; why the TMT
  fold changes are smaller. It also covers what Ionomos does differently from FragPipe-Analyst, the settings that
  match the DIA tutorial, and why no comparison with FragPipe-Analyst is claimed. README docs index,
  QUICKSTART.md ("Then real data"), ARCHITECTURE.md and VALIDATION.md updated.
- `SECURITY.md`: private vulnerability reporting, scope, supported versions.
- `deploy/check_frozen.py`: the exe build fails when a module or data file of the package is not inside the exe.
- CI: a `package` job on every push (build, `twine check`, contents, the wheel alone in a fresh venv); Python 3.13
  in the test matrix (the classifier promised it).
- `deploy\build_exe.ps1 -NoInstaller`: stop after the portable zip.
- **`FILTER_REMOVES_ONE_CONDITION`** (D84): a warning when a filter on all
  samples removes features that a condition had in every sample. It says
  how many, in which condition, and how many were seen nowhere else. It
  suggests 0 % of all samples and 50 % of one condition (Ionomos' default).
  It is in the report, the attention list and the help.
- **Pull-downs with a negative control** (D88). A condition named as an
  empty vector, beads only, IgG or an untagged bait (`EV`, `empty`, `beads`,
  `IgG` …) is the pull-down's *background* when its samples also miss
  clearly more proteins than the others (then it is sure), or the
  experiment folder's name says pull-down (`IP`, `pulldown`, `BioID` …).
  Then:
  - every pull-down is compared against it (`DMSO vs EV`, `Drug vs EV`), so
    what the bait pulls down reads as *up*; the background is no longer
    compared with the control (`EV vs DMSO`);
  - when sure, the missing-value filter across all samples doesn't count
    its samples, and it is normalised among its own samples, the pull-downs
    among theirs.

  The name alone is not enough (an empty vector is also the plain control
  of an over-expression); a note says so. New role `background` in
  `analysis.roles` and the Roles list; `background_keywords` setting; report
  note `PULLDOWN_DESIGN`; `simulate.pulldown_pg_matrix` for tests.
- **Round 4: tests against hostile and odd input** (2026-10-15). Each area was attacked with malformed, empty,
  huge, unicode, CRLF, concurrent, interrupted, read-only, missing and duplicated input; each round-4 fix under
  Fixed has a regression test.
  - **Loaders** (D97): `tests/test_loader_formats.py` + `tests/loader_formats.py` (343 tests). One simulated
    experiment written in every format (DIA-NN matrix / long / Parquet, FragPipe DIA and IonQuant, MaxQuant,
    Spectronaut long and pivot, AlphaDIA, MSstats, Proteome Discoverer, Sage, any table as tab / comma / semicolon
    / .xlsx) gives one hit list; each format with nine harmless differences reads to the same numbers; with ten
    kinds of damage it is read with a note or refused in words and analyze() never crashes; 100,000 rows of each;
    the TMT formats (PD, MSstatsTMT, Sage TMT, MaxQuant) take the same changes and find the planted hits;
    provenance on binary / empty / wrong-shaped logs and records for every method.
  - `tables.decode`, `tables.open_text`, `tables.unquote`, `tables.reader`, `tables.delimiter`: the one place text
    encodings, quotes and delimiters are decided (D97).
  - **Statistics**: `tests/test_stats_edges.py` (232 cases, ~6 s): n = 1 and n = 0 groups, all-missing features,
    constant columns and tables, duplicate IDs, 0 / negative / inf / NaN intensities, one sample, 500 samples,
    values rounded to 3 decimals, a block with one level, self-comparisons, an empty sample under every
    imputation; seeded property tests on random designs: A vs B is the mirror of B vs A; no p-value without values
    in both groups and a variance (D32); the missing-value filter removes exactly the failing features, each once;
    sample order and units change nothing; IRS leaves one plex, no plex and label-free data unchanged; the roles
    never compare a condition with itself. Every golden still holds to its tolerance, none touched.
  - **Intake and the watcher**: `tests/test_intake_faults.py` (54 fault-injection tests against the testbed and
    its fake FragPipe): copies still running, renamed / removed / duplicated drops, unicode / device /
    200-character / trailing-dot names, a read-only inbox, every kind of wrong `experiment.yaml`, damaged history
    / status files / ledger rows, locked and held-open ledgers and `repair-ledger`, the lock of a killed watcher, a
    stop while filing, a restart mid-job with a damaged `ionomos.json`.
  - **Engines run directly** (D98): `tests/standin_engine.py`, a stand-in engine (driven by `IONOMOS_STANDIN`)
    that hangs, prints garbage or binary, exits with any code or signal, writes whole / partial / zero-byte /
    header-only tables, locks a table from a grandchild, leaves a process behind, or floods its log;
    `tests/test_engine_faults.py` (109 tests) runs it as FragPipe, DIA-NN, MaxQuant, Sage, ThermoRawFileParser and
    the preflight's launcher, and kill_tree / kill_pid_tree / stop_leftover for Windows and POSIX with the
    module's os / subprocess / signal stubbed. `fragpipe.code_text`, `fragpipe.move_aside`, `fragpipe.run_to_file`.
  - **The command line's exit codes, the same for every subcommand** (D102), in `cli.py`'s docstring (so in
    `ionomos --help`): 0 done, 1 the answer is no, 2 nothing was done (usage, unreadable config, missing folder or
    file), 3 a watcher is already running, 70 crashed, 130 Ctrl-C. `cli.build_parser()`; a help text for every
    option `cli.py` defines (14 had none). Tests: `test_cli_contract.py`, `test_config_errors.py`,
    `test_config_roundtrip.py`, `test_check_agreement.py`, `test_hostile_pc.py`.
  - **Security** (D99, D100): `docs/SECURITY_NOTES.md`, what each component protects against whom, what it does
    not, the code and the tests. `tests/test_security.py` (90 tests): zip-slip and absolute members, a 10 GB
    zip-bomb entry, a manifest bomb, webhook credentials and redirects to a private address, `!!python` tags in
    every YAML reader, `& | < > ^ % "` and line breaks through the command lines, PowerShell quoting, a hostile log
    read by the assistant, a symlinked log, a dead UNC scan root, network paths inside scanned files, update
    refusals, and secrets planted in config.yaml searched for in the notification, fingerprint, status file, logs,
    catalog, `ionomos check` and every assistant tool result.
  - **The assistant**: 19 scenarios (85 in all, 53 scored by `ionomos ask-eval`): ambiguous questions, a job with
    no log, every tool with bad arguments, tool names that do not exist, injections in an experiment's name and in
    a pop-up's folder name, a slow model past and within its time limit, a 10 MB reply, a change off the
    whitelist, and a proposal that passes every check but changes another job. The injection fixture state gains
    job 3 (its name an instruction) and a rejected folder whose name is one. `tests/test_assistant_robustness.py`:
    the audit log under threads and processes at once, concurrent asks, a trickling model over real HTTP, every
    tool against every kind of bad argument, an injected help entry, whitelist values that try to smuggle a
    second `experiment.yaml` key. The scripted model can be slow (`"seconds"`) or huge (`"pad"`); its HTTP server
    sends a paced reply as it comes.
  - **The report's front end**: the QC and enrichment tabs are ARIA tab lists (arrow keys, Home, End); sortable
    column headers take Tab, Enter and Space and carry `aria-sort`; the two-way switches carry `aria-pressed`;
    every chart (`svg role=img`, the heatmap canvas) has an `aria-label` naming what it shows; a `?` controls its
    panel (`aria-controls`) and Escape on it closes the panel. JS harness: `hostile.test.mjs`,
    `searchjunk.test.mjs`, `keyboard.test.mjs`, `exportall.test.mjs` (34 tests).
  - **The library** (D101): `ionomos library scan --only`, `--replace`; `ionomos library adopt --force`;
    `library.save_scan`, `merge_scan`, `adopt_problems`, `live_workflows`, `lab_taxon`, `CANONICAL_STOCK`,
    `LABEL_KINDS`; flag `WF_KIND_MISMATCH` with a help entry.
  - **FASTAs**: `fastasource.fasta_headers(fh, stop, deadline, chunk=…)`, `fastasource.text_before_first_header`;
    `prepare_for_fragpipe(…, fasta_dir=)` and `download_proteome(…, fasta_dir=)` (for the wording of the space
    note).
  - **The testbed** (D103): methods LFQ (`LFQ-MBR.workflow`, keyword `labelfree`), Phospho (`LFQ-phospho.workflow`,
    keyword `lfqphospho`) and Open (`Open.workflow` / `Open-noLOS.workflow`, keyword `opensearch`), each with a
    `naming.methods` rule `{sample}_{rep}[_{fraction}]`. Scenarios `lfq_good` (label-free, `combined_protein.tsv`
    in a real file's layout), `phospho_lfq` (`combined_site_STY_79.9663.tsv`, sites analysed), `dia_fractions`
    (two fractions per sample), `open_search` and `open_search_no_los` (`psm.tsv` with mass shifts,
    `ptm-shepherd-output/`), `non_ascii_log` (tools print umlauts, µ and Japanese), `retry_previous` (fails once,
    then a Retry leaves `fragpipe_previous_*`). Fake modes `non-ascii` and `fail-once`, which also join the stress
    run's fault drops. `ionomos testbed list` shows each sample's fake mode and every fake mode.
    `tests/test_testbed.py`: 19 tests, 32 cases.
  - **The docs are checked against the code on every test run** (`tests/test_docs_sync.py`): every CLI
    subcommand (read from the real parser) is in `cli.py`'s docstring, ARCHITECTURE's `cli.py` row and README, and
    every `ionomos X` a doc shows exists; every key `configio.dump_config` writes is explained in ARCHITECTURE's
    config section or a doc it names; every help id the code opens and every `faq.*` / `issue.*` a doc names
    exists; every relative link in `docs/*.md` and the top-level `*.md` resolves, `#anchors` included; every
    `Dnn` cited in the changelog, docs, code and tests is a DECISIONS heading, and the headings are unique, in
    order, without gaps; no paragraph that says "not built yet" / "wave 2" / "awaits" names what [Unreleased]
    added; every testbed scenario is in TESTING.md; every file name in `names.py` is in ARCHITECTURE's on-disk
    layout.

### Changed

- `fragpipe.prepare` plans TMT jobs with `tmtkit.plan`: annotation files are
  written in the kit's order; a `tag:` that is not the workflow's label type
  fails the job before the search, saying which to change; a person's own
  `*annotation.txt` is checked with FragPipe's rules (a file FragPipe would
  stop on fails the job before the search, the file untouched); the "no
  channel map" warning adds that the report will not know the conditions.
- `experiment.yaml` `tmt:` is checked with `tmtkit`: unknown keys and tags are
  refused with the known ones; a plex may have its own `reference:`;
  `reference_channel` may be a list. Intake records each TMT plex's channel
  map and source in `ionomos.json` (`plan.tmt`) and says at intake what would
  stop the search.
- The TMT loader reads TMT-Integrator's `_GN`, `_None` and `ratio_*` tables
  when `_MD` is absent (in a fixed order, `quant.TMT_TABLES`), with a note
  saying which. `fake_fragpipe.TMT_CHANNELS` comes from `tmtkit`; its TMT-6
  channels are now FragPipe's (126 127N 128C 129N 130C 131N, was 126–131).
- The Methods tab's "workflow + FASTA ready" checklist fix now points to
  Choose… / Find on this PC…. `deploy/ionomos.spec`: hidden imports for
  `ionomos.library`, `ionomos.fastasource`, `ionomos.library_tab`.
- **`docs/SCOPE.md` describes today's scope**: the watcher and the pip-installable analysis, in and out of scope
  with the decision for each, what moved since the v1 plan; the 2026-09-15 lab plan is kept as "Where it
  started", with which of its success criteria hold.
- **README**: three sentences on what Ionomos is before the lab diagram; a shorter Status (detail in
  ROADMAP.md); Cite and Contributing sections; VISION, LIBRARY and FASTA in the docs index.
- **ROADMAP Phase 5D is a publishing checklist** in order, each step with an owner (the maintainer or an agent):
  PyPI, repository settings and a Zenodo DOI, community files (done), a real-data example, the real-data
  evidence, pilot labs, a documentation site, name and logo, a preprint and a technical note, a JOSS paper (not
  before 2027-03-17), versioning promises, v1.0. Three new risks: mostly agent-written code, real data lagging
  behind the code, users the maintainer can't see.
- **A TMT reference (bridge / pooled) channel is never compared** ("Pool vs DMSO" is gone from the `tmt_bridge`
  scenario). Its condition takes the existing reference role: written as `analysis.roles: {Pool: reference}` by
  the editor, derived from `experiment.yaml` `tmt:` (`reference_channel`, a plex's `reference:`, pool / bridge
  words) by the analysis unless a person gave that condition a role, or read from the name in TMT data. Also with
  `analysis.control` set and in competition designs; an explicit `comparisons:` list still wins. Other data keep
  D61's rule. The pool is never the guessed control.
- `configio.dump_config` writes `analysis.irs`, `tmt_reference`, `tmt_min_labelling`, `tmt_min_purity` when set
  (the app's Save lost them).
- `Draft` carries `experiment.yaml`'s `tmt:` and each TMT method's workflow label type / ref tag; the review
  window's TMT notes no longer say "conditions come from the channel annotation" (a dead end).
- `deploy/ionomos.spec`: hidden imports `ionomos.tmtkit`, `ionomos.tmt_editor`.
- build-exe.yml sets up Python 3.14, the version the exe was already built with.
- **Round 4** (2026-10-15):
  - **Loaders** (D97): `tables.read_tsv` (FragPipe's tables and Ionomos' own) now takes UTF-16 / cp1252, a comma
    or semicolon file, and cells in quotes; a quote still never spans cells or lines.
  - **Engines**: the time-limit hint says "the search hung", not "FragPipe hung" (it is shown for every engine).
  - **Exit codes** (D102): `ionomos check` and `ionomos preflight` exit 2 (was 1) when config.yaml can't be read,
    like every other command; `analyze` / `export` of a job that isn't in the ledger exit 1 (was 2), like `retry`
    and `cancel`; `cancel` of a job that doesn't exist exits 1 (was 0). An empty value in config.yaml
    (`poll_seconds:` with nothing after it) means the default.
  - **experiment.yaml**: the unknown-key message names the key probably meant (`methd (did you mean method?)`)
    and every key the file can have. A channel written twice in `tmt:` is REJECTED at intake (`TMT_BLOCK`), was
    taken with the last name; `TMT_ALL_NA` is an error (the job fails before the search), was a warning (#156).
  - **The library** (D101): `library scan --root DIR` adds DIR to the default roots instead of replacing them
    (`--only` for the old meaning). Kinds `SILAC`, `isoTOP`, `ipIAA`, `PAL`, `labelled` beside isoDTB / TMT / DIA
    / LFQ / phospho / open / other. `library._headers` delegates to `fastasource.fasta_headers` (a header is kept
    up to 64 kB; real ones are < 1 kB).
  - **The assistant**: a proposal made from a question about an attention item without a job, or about an
    experiment Ionomos did not find, says so in its window, as one for another job already did (D75 4).
  - **The assistant corpus cites an engine log line by what it says** (#183): `[log:2#{ERROR: DIA-NN crashed}]`
    is numbered in the state just built (`scenarios.resolve`, used by the replay and `ionomos ask-eval`). The
    numbers had moved with the fake's new console (D103), and on Linux the fake's DIA command list is two lines
    shorter (CmdDiann's plot step is Windows only), so the Linux CI failed on numbers right on Windows.
  - `tests/test_service.py::test_create_desktop_shortcut_encoded_command` asserted the broken PowerShell for
    `O'Brien`; it now asserts the doubled quote (D100).
- Every option and positional of `library`, `testbed`, `fasta` and `tmt` has a `--help` line;
  `tests/test_cli_contract.py` now requires one for every option of every parser (#166).
- Assistant tests read a cited log line's number from the fake FragPipe's console (`scenarios.log_line`), so a
  change to the fake no longer breaks them; when no line holds the text, the error names the scenario and the
  console it read (#183).
- Hidden-window tests: a dialog nobody expected prints the widget whose Tk callback asked for it, its
  interpreter and the stack, so the next "Find FragPipe" flake names its cause (#185, not yet found).

### Fixed

- **A drop's `experiment.yaml` can no longer choose a workflow or FASTA outside the lab's folders (#168, D105).**
  `workflow:` and `fasta:` are file names in `workflow_dir` / `fasta_dir`, as documented; a path (absolute,
  `..`, a drive, or a `//server/share` that made Windows sign in to that server as the watcher's account) is
  refused at intake with what to do. A network `analysis.site_annotation:` is never opened; the analysis warns.
- `ionomos attention show / dismiss / snooze / resolve` with an id such as `../../outside` read, and rewrote, a
  JSON file outside the attention folder (#169). `attention.py` now takes only an id it could have made
  (`[A-Za-z0-9._-]`) for every caller; anything else is "no item".
- **A search an earlier Ionomos left running that won't stop now holds its job** (#175, D107). `stop_leftover`
  says whether the process is gone and keeps `engine_pid.json` while it runs; the worker then waits ("an earlier
  search of this experiment is still running (process N, …): end it in Task Manager; the search starts by itself
  once it has ended", help `search.hold-leftover`) instead of starting a second search beside it, and tries again
  on each pass. A cancelled search that did not stop is named in the reason (`cancelled by user — DIA-NN did not
  stop when told to (process N): …`) as a timed-out one is, and gets a pop-up; its Retry waits for it.
- **Round 5 issue sweep (D108–D117):**
  - **PCA of a large study takes seconds, not minutes** (D108, #163): above 60 samples only the 6 components
    the report shows, from the smaller Gram matrix; up to 60, Jacobi as before. 500 samples × 300 proteins:
    6 min → about 3 s. **knn imputation of 10,000 proteins** takes about 25 s instead of 4 min, with the same
    numbers bit for bit (D108, #164).
  - A MaxQuant TMT `proteinGroups.txt` without an SDRF or `sample_conditions` no longer gets one condition per
    channel and 19 fold-change-only comparisons: each channel is `unassigned`, the report is QC only, and one
    note asks for the conditions (D109, #165).
  - The FASTA readers of the bundle, the SDRF organism, Sage's gene names and the benchmark's species count the
    first entry of a FASTA saved with a UTF-8 BOM and read a file without line breaks in bounded chunks, through
    `fastasource.fasta_headers` (#160).
  - Notes that only say what was read ("quantity: MaxLFQ Intensity", the TMT table kind, phosphosite filter
    counts) stay in the report and `analysis.json`, not in a done job's reason, `run.warnings`, DONE.txt or the
    WARNING log (D110, #182).
  - An open search (IonQuant, TMT-Integrator and DIA-NN all off) is done with a note pointing at
    `fragpipe/ptm-shepherd-output/`: no NO_TABLE, no "analysis had a problem", no attention item (#181).
  - A DIA experiment with fractions has each sample's fractions added up in DIA-NN's protein matrix, with a note,
    instead of being called re-acquisitions and stopping for input (D111, #180).
  - A FragPipe refusal Ionomos has no words for is quoted ("FragPipe exited with code 1 (refused: …)") with
    "fix what it names", not "ended from outside: Retry"; "Database looks to be empty", "Some loaded files don't
    exist" and "Not all TMT groups have annotation files set" are explained; "badly formatted lines" is a
    warning on the done job naming the runs FragPipe left out (D112, #179).
  - A SILAC, PAL, isoTOP or ipIAA search folder is no longer analysed as isoDTB: the Analysis tab says Ionomos
    doesn't analyse that label (`LABEL_NOT_HANDLED`) (D113, #161).
  - `ionomos run` with a read-only `logs/ionomos.lock` names the file and exits 2; with a damaged job list open
    in another program it says what to close and exits 1. Both were tracebacks (#172).
  - A hand-written or copied `resolved_by: gui` no longer files a drop under a new user folder for a misspelt
    `user:`, and no longer skips the review window on its own (D114, #173).
  - `ionomos bundle <missing job>` exits 1 and `ionomos library show <missing file>` exits 2, as the exit-code
    table says (#167).
  - Report text reaches WCAG AA 4.5:1 in light and dark: warning words, flagged cells, pills, badges and links
    take text-only inks; help.html's muted grey and the static volcano's ticks are `#6f6d68`; chart marks keep
    the palette (D115, #177).
  - The report's own Export of the TMT QC figures matches `ionomos export --figures tmt`: no statistics or
    `hits:` in the footer, no doubled title, `TMT channels: plexB` (#178).
  - A question to the assistant has one time limit, twice `assistant.timeout_seconds` (4 minutes by default);
    each request and its socket get what is left, and the Ask window says how long it may wait (D117, #171).
- **Round 5 follow-ups (D118–D119):**
  - The other engines' notes that only say what was read (the summaries of DIA-NN, Spectronaut, MSstats,
    MSstatsTMT and Sage, q-value and PSM filter counts, razor peptides, fractions combined, gene names from the
    FASTA; MaxQuant's, Spectronaut's, AlphaDIA's and Proteome Discoverer's sample columns and log2 step) no longer
    become a done job's warnings. Real problems still warn. MaxQuant's Reverse / contaminant columns are no
    longer listed as "columns with no values" (D118).
  - The note asking for the conditions of `unassigned` TMT channels (MaxQuant, Sage without a `tmt:` map) is
    left out once an SDRF or `sample_conditions` gives every channel analysed one; pooled reference channels and
    channels left out need none (D118).
  - A sample's DIA fractions are also added up in DIA-NN's long report and its phosphosite matrix, before an
    SDRF is applied; runs without fractions are read as before (D119).
  - `fastasource.fasta_headers`' docstring names all seven of its callers.
- The Advanced tab's RAM (GB) field refuses a decimal (47.5) as config.yaml's loader does (D102); it used to
  accept it and the next start stopped on `fragpipe.ram_gb must be a whole number`.
- **TMT-Integrator protein tables are labelled by gene.** In `ratio_protein_*.tsv` / `abundance_protein_*.tsv`,
  `Index` is the UniProt accession and `Gene` the symbol. Ionomos used `Index` as the label, so volcano names
  were accessions. Worse, enrichment, which matches gene symbols, found nothing on these tables. The label is now
  `Gene` when the table has one. Gene tables (`abundance_gene_*`, no `Gene` column) are unchanged.
  (`downstream/quant.py`, found with `ionomos example`'s TMT data, tested in `tests/test_example.py`.)
- `ionomos analyze` on a DIA-NN `report.pg_matrix.tsv` on its own no longer reads `N.Sequences` and
  `N.Proteotypic.Sequences` as two extra samples (DIA-NN 1.9 and later write them before the runs).
- The sdist no longer carries a partial copy of the tests (`ionomos/MANIFEST.in`).
- `deploy\build_exe.ps1` works from a source zip without `.git` (the build is stamped "unknown").
- **`ionomos analyze` on a scratch table no longer picks up the lab's
  config.yaml that happens to sit on the PC**, nor posts the table's problems
  to the lab's attention list (#134). The lab's settings apply to a job id, to
  a folder or table under the lab's users folder, and to anything when
  `--config` or `IONOMOS_CONFIG` names the config; any other folder or table
  gets Ionomos' defaults. The output now says which: `settings: C:\Fragpipe_Auto\config.yaml`
  or `settings: Ionomos' defaults (… not used: --config applies it)`. Only the
  lab's own experiments raise attention items. `check-folder` follows the
  same rule.
- **An anonymised bundle no longer keeps a name that follows a JSON `\uXXXX`
  escape** (#135). attention.py writes its items with ASCII escapes, so the
  curly quotes around a rejected drop's name became `\u201c…\u201d` and the
  name followed the letters `u201c`: the scrubber saw one word, and the leak
  check (which uses the same search) missed it, so a bundle said "none found"
  while its attention items held the user's initials and experiment names.
  A `\uXXXX` escape is now a name boundary for the scrubber, the leak check,
  `bundle inspect` and `bundle translate`.
- **`ionomos bundle inspect` no longer reports ordinary words (run, then,
  own, txt) as real names** (#142). It registered every entry of the key
  file's `names` group (the words of experiment names, which the bundle
  replaces only inside those names) as a name of its own, and found "run"
  in every workflow and "txt" in every file list; the bundle's own check had
  rightly found nothing. The key's words are now fragments: found inside the
  names they belong to, and on their own only when distinct (KL6159A), so
  `inspect` agrees with the leak check on the same zip.
- **An unknown key in config.yaml is reported, with the nearest known one**
  (#137). `watcher.poll_secnds`, `fragpipe.thread`, `timout_minutes` or a
  `notifyy:` section were silently ignored while `ionomos check` said all
  good and the PC ran with the defaults. `check` now prints a `!` line for
  each ("watcher.poll_secnds: unknown setting, did you mean poll_seconds?
  (ignored)"), the app's checklist marks Settings saved as a warning, and
  `Config.unknown_keys` holds them; a warning, not an error, so an older
  config with a retired key still loads. `paths`, `watcher`, `fragpipe`,
  `gui`, `users`, `qc_trend`, `library` and the top-level sections are
  checked; `analysis`, `naming`, `notify` and `methods` check their own.
- **CLI polish** (#143): a fold-change-only comparison (1 vs 1 sample) prints
  "34 up, 34 down by fold change alone (no replicates to test)" instead of "of
  0 tested"; `ionomos cancel` of a missing job exits 1 (as `retry` does) and
  `ionomos analyze` exits 1 when the report says the analysis failed (an
  unusable table), so a script can tell; `ionomos check` shows a workflow's
  label type only when TMT-Integrator runs (every stock workflow carries
  `tmtintegrator.channel_num`, so isoDTB and DIA read as "TMT-6");
  `ionomos dry-run` no longer promises the resolver window when
  `gui.enabled` is off; `repair-ledger --force` on a healthy ledger keeps
  the old file as `.before-rebuild-<time>`, not `.broken-`; `ionomos compare`
  matches Ionomos' own `DrugA_vs_DMSO_differential.tsv` by name; and the
  static volcano SVG draws every significant point but at most ~20,000
  non-significant ones (an even sample, said in the figure), so a 200k-row
  table no longer makes a 37 MB file that PowerPoint cannot open (the report
  still draws everything).
- **`check-folder` on a table file checks that file, and a folder of several
  tables says which one was read** (#141). `check-folder a_small.tsv` used to
  check the parent folder and recommend `analyze <folder>`, which writes
  elsewhere than `analyze a_small.tsv` does; now it describes the file (a
  results table or a table of quantities, rows, samples or comparisons), its
  `<name>_ionomos/results` folder and the command for that file. When a
  folder holds more than one usable table, `analyze` notes "read b_main.tsv;
  also here: a_small.tsv. Name the file to analyse it instead", and
  `check-folder` (and the app's check window) list them under "Also here".
- **`ionomos analyze --normalize` takes `auto` and `ratio`** (#133), the values
  the doctor's own advice tells a command-line user to set; it refused them
  with "invalid choice". Every value `analysis.normalize` takes is accepted.
- **Per-comparison result files no longer repeat the experiment prefix past
  Windows' path limit** (#140). By the DIA file rule each condition carries
  the experiment prefix, so `<cond>_vs_<cond>_differential.tsv` and
  `volcano_<cond>_vs_<cond>.svg` held it twice on top of the experiment
  folder, and in a deep folder the tables and volcano plots went missing
  while the job stayed `done`. When a comparison's full file name would take
  its paths past 250 characters, the part both condition names share is
  dropped and a short hash of the comparison's name keeps the stem unique
  (`Drug_vs_DMSO-3f2a9c_differential.tsv`); a note in the analysis says so,
  and analysis.json, the report and the figures use the same stem.
- **A search that fails on a path longer than Windows allows is explained as
  that, not as "ended from outside, nothing in the search failed: Retry"**
  (#138). A missing-file error (Java's FileNotFoundException or
  NoSuchFileException, Python's FileNotFoundError) naming a path of 240+
  characters gets "A path got longer than Windows allows (260 characters with
  long paths off): shorten the experiment folder and raw file names, or move
  the users folder nearer the drive root". And a log that ends in a traceback
  or an ERROR line Ionomos does not know is no longer read as "stopped in the
  middle of a step without saying why" (which led to "ended from outside"):
  it says the search stopped on an error Ionomos does not recognise and
  points at the log's last lines. `ionomos testbed init` warns when Windows
  long paths are off and its folder is so deep that the samples' searches
  would pass 260 characters.
- **A drop whose path is longer than Windows allows is rejected once, not
  ignored or retried for ever** (#139). With long paths off, a folder of 260+
  characters cannot be stat'ed, so the watcher skipped it in silence on every
  poll; a folder whose files were too long was retried every 15 s as a
  "transient problem", because the rejection note's temp name was itself too
  long and the FileNotFoundError looked transient. Now: the watcher says once
  in the log and as a needs-attention item that the folder cannot be opened
  (closed when it leaves the inbox); intake reads a FileNotFoundError on a
  path of 260+ characters as "This path is N characters long; Windows allows
  259 here" and rejects with a note, or with an attention item when even the
  note's path would be too long; the note's temp name is shortened when the
  note's own is near the limit; a rejection without a note waits for the
  folder to change instead of being offered again every poll.
- **The app no longer dies at start when config.yaml has a YAML slip** (an
  unclosed bracket from hand editing, #136). `configio.read_config` turns the
  parser's error into the same `could not parse <file>: line N, column M: …`
  message `ionomos check` prints; the app opens on the defaults with that
  message in the status bar and the first checklist item, the file untouched,
  and a dialog offers **Restore a previous config…** when config-backups/ has
  one (else says to fix the line and press Reload). Start watcher, Reload,
  the config switcher and `ionomos init` say the same instead of a traceback,
  and any other error while the app starts is shown in a dialog before the
  windowed exe exits.
- On a small screen (1366×768) the bottom bar — Save, Help, Report a problem, the ⚠ badge — disappeared; it is now
  packed before the tabs, and its buttons before the config path. (#111)
- 4 Advanced: the hint "Needs the watcher to run in the logged-in session" covered the "Pop up a window when
  something needs you" checkbox. The Jobs tab's method column was too narrow ("isoDTI"). (#111)
- **A job ledger shared by two threads no longer returns torn rows** (D104).
  Reading the ledger on one thread while another read or wrote it could
  return a job with no `parsed` data (`TypeError … not NoneType` in
  `Job._from_row`), a short job list, or `InterfaceError`. This made master
  CI's `test_the_worker_loop_runs_through_a_queue_of_faults` fail now and
  then. Every use of the ledger's connection now holds one lock, and
  `start_attempt` counts each attempt once when two threads call it.
  `ionomos run` already gave the worker its own ledger.
- **A protein seen in one condition only no longer vanishes when a global
  missing-value filter removes it** (D84). With `filter_global_pct: 66` and
  three conditions of three, a protein in every FPS run and no other was
  removed before testing. It was also missing from "only in one condition",
  which was built after the filter. That list is now built before the
  filter, and what the filter removed is marked: `removed_by_filter` in
  `presence_absence.tsv`, in grey in the report's table and key findings,
  and `only_in_one_condition_filtered_out` in `analysis.json`.
- **Leaving samples out no longer breaks the run-order stage** (D84).
  `exclude_samples: [EV_1, EV_2, EV_3]` stopped it with `KeyError: 'EV_2'`,
  because the manifest's map of samples to runs still held the samples left
  out.
- **A replicate or fraction number outside 1–999 no longer reaches FragPipe**
  (D85). On 0.5.1 the naming window wrote Xcalibur's time stamp
  (`20260508180610`) as a replicate into `experiment.yaml` and FragPipe's
  manifest, and DIA-NN's matrix lacked both runs that had one: the number
  does not fit a 32-bit integer. File names were bounded to 1–999 in 0.8.0;
  now everything else is too:
  - `files.<name>.bioreplicate` / `fraction` in `experiment.yaml` is refused
    with a message saying why (fraction `-1` still means single-shot). Such a
    file in the inbox opens the naming window, which shows the files as their
    names read; its answer replaces the old `files:` block. Without a window,
    the folder stays in the inbox with a note;
  - the naming window accepts 1–999 only;
  - the naming history skips a learned number outside 1–999;
  - a job already queued with such a number waits (no crash, nothing sent to
    FragPipe), naming the file and the `experiment.yaml` to fix. Once that
    file gives the replicate, the search starts by itself. Its help entry is
    "A replicate number is out of range";
  - a re-analysis whose `experiment.yaml` can't be read says so in the
    report's warnings instead of ignoring the file silently.
- **A `config.yaml` saved in Notepad as "ANSI" no longer stops Ionomos**
  (D86). This had happened since LabWatch (2026-09-16 on the PC): an em dash
  in a comment became byte 0x97, and Ionomos stopped with a bare
  `UnicodeDecodeError`. Such a file is now read as Windows-1252, with a
  warning that names the file, the byte and its line, and says how to save
  it as UTF-8. A UTF-8 file with a BOM and a UTF-16 file ("Unicode" in
  Notepad) are read as well. A byte Windows-1252 doesn't have either stops
  with a config error saying the same. The same goes for `experiment.yaml`,
  learned aliases and benchmark specs. A running watcher keeps its settings
  on such an error instead of failing.
- **A support bundle or diagnostics from an "ANSI" config missed what it
  should hide** (D86). They read the file as empty. A webhook address was
  then left in the logs, and in the config itself when written the short way
  (`teams: <address>`); `url:` and `password:` lines were hidden anyway. The
  users and aliases listed only in the config were not anonymised.
- **Contaminants are removed from FragPipe DIA searches** (D87). DIA-NN writes the bare
  accession (`P02769`) in `report.pg_matrix.tsv`, so the `contam_` rule removed nothing:
  bovine serum albumin was reported as `ALB` next to human albumin, and porcine trypsin
  stayed (REAL_RUNS.md, 2026-10-06). The analysis now reads the FASTA the search used. It
  takes `database.db-path` from FragPipe's `fragpipe.workflow`, else `run.fasta` in
  `ionomos.json`, and removes every protein group that contains one of the FASTA's
  `contam_` accessions. Groups that also hold another protein are listed in the notes.
  Without the FASTA, the old rule applies and the notes say the FASTA was not found. The
  count is the report's "contaminants" step.
- **Two tests failed on a Windows PC that CI doesn't look like.** Neither
  was a bug in Ionomos.
  - The stress run's 161-character drop name doesn't fit in Windows' 260
    characters under a deep folder when long paths are off. With pytest's
    temp folder the drop couldn't even be made. `ionomos testbed stress` now
    cuts drop names to what fits there and says so in its report; with long
    paths on (as on CI) nothing changes.
  - The Sage setup checklist test found a real ThermoRawFileParser in
    `C:\ThermoRawFileParser\`. It no longer looks there.
- **Diagnostics and a search's run fingerprint no longer fail when Windows
  won't say its version.** Python's `platform.platform()` asks WMI, and
  (on 3.12 at least) raises `KeyError: 'BuildType'` when WMI answers with
  fields missing, now and then; "Copy diagnostics" then copied nothing (seen
  on CI). The version now reads "OS version unknown" with the error instead.
- **The assistant's time to first token was measured to ~16 ms on Windows**
  with Python before 3.13, whose `time.monotonic()` ticks that coarsely. A
  0.2 s wait read as 0.188 s, which failed a test on CI's Python 3.12. It
  is now measured with `time.perf_counter()`.
- **`ionomos testbed stress` left its temp folder behind on Windows.** The
  ledger connections were never closed, and Windows can't remove an open
  `ionomos.db`.
- **Round 4: tables from every engine, in every state they arrive in** (D97).
  - A cell longer than 131,072 characters (MaxQuant's `Evidence IDs` / `MS/MS IDs` of an abundant protein on a
    big experiment, a long protein list) crashed every table reader (`CRASH_READ`: "field larger than field
    limit"). The limit is raised for the whole process (`tables.py`).
  - A cut-off, empty or not-Parquet `report.parquet` crashed the read with pyarrow's own error; it is now refused
    in words (`UNUSABLE_TABLE`: "can't be read as a Parquet file … copy or export it again").
  - Sage's `results.json` with a `database` that is not a section (hand-edited or damaged) crashed the read and
    the provenance (`'str' object has no attribute 'get'`).
  - A sample named with a superscript digit (`Ctrl_²`) crashed the read (`str.isdigit()` accepts it, `int()`
    does not).
  - UTF-16 files (Excel's "Unicode text") were not recognised by any engine: a Spectronaut long report was
    analysed as a plain table with `R.Replicate` and `PG.Quantity` as its two samples, a DIA-NN / FragPipe matrix
    had no runs, an SDRF was not found. Every reader now takes UTF-8 (BOM or not), UTF-16 with a BOM, and cp1252
    (with a note).
  - Files saved again from Excel with every cell in quotes: FragPipe's `pg_matrix` / `combined_protein` had no
    samples; a quoted cell holding a tab shifted the rest of its row (silently wrong numbers); a Proteome
    Discoverer export saved as `.csv` lost its conditions (the header reader split `"…: F1: Sample, DMSO"` at its
    commas); a FragPipe table saved with commas had one "sample".
  - A Spectronaut (or DIA-NN, MSstats, MSstatsTMT, Sage) report written with decimal commas (`1234,56`, a German
    or French Windows) or thousands separators was read as all missing, its q-values too. The engines' long
    readers now read numbers the way the any-table reader does, with the same note.
  - A row of a DIA-NN / Spectronaut long report or Sage's `lfq.tsv` whose q-value is blank or not a number passed
    the 1 % filter; it is now left out and counted. A q-value column with no number at all is not used as a
    filter (a note says so) instead of leaving out every row.
  - Sage's `lfq.tsv` with a file's column twice added that file to itself (read as two fractions: doubled
    intensities); a repeated peptide-ion row was added to the first. Both are read once, with a note.
  - A run column twice in a DIA-NN matrix read the same column as two samples (`IDENTICAL_SAMPLES`); the repeat
    is left out with the note FragPipe's TMT tables already had.
  - A quote that is never closed made the any-table reader (and every engine read through it) join the rest of
    the file into one cell; the file is read again with quotes as plain characters, with a note. Sage's own
    tables never treat a quote as special.
  - A file cut off in the middle of its last row (an export or copy that stopped) gave a half row: the row is
    left out and the note says the file looks cut off. NUL bytes (an interrupted copy) are counted in a note.
  - An engine's own table of 0 bytes (`report.tsv`, `report.parquet`, `proteinGroups.txt`, `lfq.tsv`, `tmt.tsv`,
    `pg.matrix.tsv`) gave "no FragPipe result table found"; it is now named and called empty (0 bytes: the engine
    stopped or the copy did not finish). A header-only DIA-NN, Spectronaut, MSstats or MSstatsTMT report is
    refused as "a header but no rows" instead of loading an empty matrix.
  - An engine report saved as `.xlsx` (Proteome Discoverer's Excel export, a Spectronaut or MSstats report opened
    in Excel) was not recognised as that engine; Spectronaut's long report was then analysed as a plain table.
  - DIA-NN's main report named by `--out` (`exp1.tsv`, or `exp1.parquet` with `exp1.stats.tsv` beside it) was
    only recognised as `report.tsv`; without the matrices it was read as a plain table. A spectral library
    (`*lib*.parquet`) is never taken for the report.
  - A semicolon file with dots between the thousands (`1.234.567,8`) is read (a German Excel export).
  - Sage TMT: a cut-off `tmt.tsv` (rows shorter than its header) is said in a note instead of skipped in silence.
  - An SDRF or TMT `annotation.txt` saved as UTF-16 / cp1252 / with quoted cells is read.
- **Round 4: the statistics on degenerate input** (each fix restores D32, D48 or D42's "explained, never
  crashed").
  - **A condition is never compared with itself.** `comparisons: ["Drug vs Drug"]` was accepted: limma gave
    every feature p = 1 (an invented p-value, D32), and with a block or covariates the statistics stage crashed
    with "division by zero" and the design was dropped with that as its reason. It is now refused with a sentence
    (`settings_from`; a run falls back to the default comparisons and says why), and every model leaves such a
    contrast untested.
  - **`analysis.control` and `analysis.roles` disagreeing no longer gives "EV vs EV".** With `control: EV` and
    `roles: {EV: compound}`, the roles' comparisons held EV against itself. It is skipped, and a note says the two
    settings disagree.
  - **An empty sample no longer knocks out every comparison under mindet / minprob imputation.** A sample without
    a single value (a failed run kept in the table) was imputed with NaN in every row, so limma's pooled variance
    was NaN and no feature was tested, without a word; knn wrote the log2 value 0 into it. Such a sample now stays
    missing, with a note, as under perseus (R's MinDet / MinProb give NA there too, and impute.knn refuses).
  - **An infinite value no longer gives an infinite fold change with p = 0.** Both limma models count NaN and
    ±inf as missing, as lmFit does (`is.finite`).
  - **A covariate or block given as `NA` / `NaN` / `not available` is no value**, not a level of its own
    (`design.MISSING_WORDS`): a batch of 1, 2 and NA silently gave one sample a batch of its own, and an age with
    one NA was reported as "confounded with condition". The design is not used and `DESIGN_NOT_USED` names the
    sample.
  - **IRS: a protein with no reference value in any plex is missing, not left unscaled** (D48). Plex-mean IRS
    (`irs: sum`) is unchanged.
  - The moderated F-test with a single condition gives no p-value instead of p = 1.
- **Round 4: intake, the watcher, the ledger and the worker.**
  - An `experiment.yaml` with a date that does not exist (`date: 2026-02-30`) rejected the folder as "an
    unexpected problem — send diagnostics" and crashed the naming window's reading; it is now refused as a date,
    with how to write it.
  - `allow_uneven_fractions: 'false'` (or `'no'`) in `experiment.yaml` switched the fraction check *off*
    (`bool('false')`); yes/no values are read as such and anything else is refused.
  - `method:`, `user:`, `workflow:` or `fasta:` given as a list or a mapping in `experiment.yaml` were turned into
    text (`user: [EJQ]` filed under `EJQ`); they are refused with an example.
  - A naming-history file with one non-UTF-8 byte rejected **every** drop as "an unexpected problem"; such a line
    now matches nothing.
  - A raw file whose name has a whole word in another script (`実験_1_1.raw`, `Probe_α_1_1.raw`) was filed with
    the word gone and the numbers read as the sample; it now goes to the naming window or gets a note saying to
    rename it.
  - A drop that could never be moved (an inbox Ionomos may read but not change, a file another program never lets
    go of) was retried every five minutes for ever with only a log line; after ten minutes an attention item
    ("Can't take in … yet") says what to check, and closes itself when the folder is filed (D17).
  - The job list locked by another program for longer than the ledger waits rejected the drop with "send
    diagnostics", and it stayed rejected after the lock was gone; it is retried.
  - Ionomos stopping between moving a drop and writing its `ionomos.json` left the experiment in no list, never
    searched; the first status record is written straight after the move and the hourly check adopts it.
  - Inbox and users_root on two drives: a file still being written (Explorer sets the final size first) was
    copied half-written, and the clean-up then deleted the finished files from the inbox while the message said
    to keep the filed copy. The copy now waits until no file in the drop is open (Windows, D29).
  - Windows: an inbox folder whose name ends in a dot or a space (made on a Mac, in WSL) was skipped for ever
    without a word, or stood in for the folder without the dot; it is renamed in the inbox, or the log says once
    how to.
  - An `ionomos.json` that is valid JSON of the wrong shape (`null`, `[]`, a plan without its folder) stopped
    `ionomos run` at start-up, and crashed `repair-ledger` and the hourly adoption; it is now skipped with a
    warning like broken JSON.
  - One damaged job record in the job list (`parsed_json`) made every list of jobs fail (worker, app, `status`);
    that job alone now fails.
  - An unexpected error while preparing a search escaped the worker's loop, and the same first queued job failed
    again every poll, so no job behind it ever ran; it now fails that job with the reason.
  - `ionomos repair-ledger` while the job list or its journal is open in another program stopped half way with a
    `PermissionError`; it now changes nothing, says to close the program, and exits 1.
  - Loose raw files named like a Windows device (`NUL_1.raw`, `NUL_2.raw`) asked for a folder named `NUL`; it is
    `NUL_files`.
- **Round 4: DIA-NN, MaxQuant, Sage and FragPipe's run loop** (D98).
  - DIA-NN, MaxQuant and Sage named an earlier attempt's output folder to the second only
    (`diann_previous_<time>`), so a second retry in the same second failed. All four runners now share
    `fragpipe.move_aside` (`-2`, `-3` …), as FragPipe had (D69).
  - A full disk (or any write error that wasn't a locked file) while writing a DIA-NN job's `diann.cfg`, or a
    MaxQuant / Sage job without a lab file, held the job for ever as "workflow file for DIA missing: diann.cfg".
    The job now fails with the real cause ("could not prepare DIA-NN inputs: … No space left on device" → "The
    disk is full").
  - `MaxQuantCmd --create` ran under `subprocess.run(capture_output=True, timeout=300)`: at the limit Python kills
    MaxQuant but then waits, with no limit, for the pipe a child of it still holds, so the worker hung for good
    (Stop could not end it). It now runs with its output in `ionomos_run/maxquant_create.log` and its process tree
    killed at the limit (`fragpipe.run_to_file`, which the preflight now uses too). Its failures (exit code, no
    template, not XML) fail the job with what to check.
  - A lab `mqpar:` that is not XML or not MaxQuant's (`<MaxQuantParams>`) failed every attempt as "Ionomos hit an
    unexpected error: ParseError"; it now holds the job before an attempt is counted ("mqpar … can't be used: …;
    save the parameters again from the MaxQuant GUI"), help topic `search.hold-mqpar`.
  - Sage: a half-written mzML left in `sage_mzml/converting/` by a killed conversion was moved into place as a
    finished one when the converter exited 0 without writing. It is now removed before that file is converted.
  - Sage: a raw file stamped in the future was converted again on every retry, and a raw file replaced by a copy
    with an older time stamp was never converted again. Each conversion now records the raw file's size and time
    (`<name>.mzML.source.json`).
  - Sage: a damaged `sage_job.json` ended `ionomos sage-job` with a traceback; it now prints what is wrong and to
    Retry (exit code 2). `sage --help` output is decoded with `errors="replace"`.
  - DIA-NN, MaxQuant or Sage exiting 0 without their main table (`report.pg_matrix.tsv`,
    `combined/txt/proteinGroups.txt`, `lfq.tsv` / `tmt.tsv`) was a "done" job with nothing to analyse; it now
    fails ("… exited 0 but did not write its result table …").
  - A result table held open by another program was reported as "the run did not finish writing it"; it is now
    named as open in another program, with its own cause.
  - Windows exit codes read as 4294967295 / 3221225477; they are now shown as `-1` / `0xC0000005`. `0xC0000135`
    / `0xC0000139` / `0xC000007B` (a DLL missing or for the wrong Windows: DIA-NN without the Visual C++
    Redistributable) get their own cause, and any other `0xC0xxxxxx` status reads "crashed with Windows error
    0x…". A failed FragPipe step's code says what it means.
  - `kill_tree` gave up when `taskkill` could not run and left the search running; it now ends at least the
    process Ionomos started. A search that still does not stop at the time limit or the log limit is named in the
    reason with its process number and what to do.
  - `stop_leftover` raised on a record that was valid JSON but not an object (`null`, `[]`); a damaged record is
    now removed. `kill_pid_tree` never signals pid ≤ 1.
  - POSIX: a process an engine left running after it exited by itself is stopped, and the job carries a note
    saying so. Windows: #174.
  - Preflight: its time limits were bound when the module loaded; a launcher with no output said "output: "
    instead of "none"; a launcher that could not be started showed only the hint, not why; `install_report`
    raised when the folder above the installation could not be listed.
- **Round 4: config.yaml and the command line** (D102).
  - `service._creationflags` read `subprocess.CREATE_NEW_PROCESS_GROUP` directly; Linux's subprocess has no
    such flag, so a test standing in a Windows `os` crashed there. Both it and `fragpipe._popen_kwargs` take the
    flags with `getattr`.
  - Every config.yaml error names the file, the key, the key's line and what to do
    (`C:/Fragpipe_Auto/config.yaml, line 14: 'watcher.poll_seconds' must be above 0, not 0; e.g. 10`); the
    watcher's folder checks give each problem its line.
  - A wrong type in config.yaml is an error naming the key instead of a crash or a surprise: `users:` or `gui:`
    that isn't a mapping, `threads: eight`, `auto_run: 'false'` in quotes (read as on), `poll_seconds: 0` (the
    inbox scanned non-stop), `.nan`, a negative time.
  - One alias written alone is one alias: `Isaac: IJ` under `users.aliases`, a learned alias, a method's
    `aliases: tmt`, `postprocess: tmt_annotation` and `users.ignore: FragPipe*` were split into characters.
  - The app's Save (and `ionomos init`, `library adopt`) no longer drops what it has no field for: six analysis
    settings the loader takes (`comparisons`, `exclude_samples`, `sample_conditions`, `sdrf_factor`,
    `ratio_centre`, `protein_correction`), any other key a lab typed, a typo (D67 item 4) and an unknown section.
  - A method with no `aliases:` keeps its built-in ones after a Save: isoDTB was written back as `[isodtb]`, and
    folders named `…iso-dtb…` were no longer recognised.
  - A Save that dies half-way (crash, full disk, power cut) leaves the previous config.yaml as it was: the text is
    written to `config.yaml.tmp`, flushed to disk and swapped in. The same for `learned_aliases.yaml` and
    `config_path.txt`. Two Saves within one second keep both backups.
  - `ionomos check` finds what the app's checklist finds: a folder the account can't write, an inbox inside the
    users folder (or the reverse, or logs inside the inbox), a `users.default` without a folder, paths on two
    drives, a disk whose free space can't be read, the startup task (Windows). Initials for a user without a
    folder are a warning in both.
  - No Task Scheduler (schtasks missing, its service stopped or hung) is "unavailable" with what to do, not a
    crash of the checklist, `ionomos diagnose` or Install task.
  - `ionomos run` with a log folder it can't write says so and exits 2 (was a traceback).
  - A damaged job ledger: `status`, `retry`, `cancel` say which file and to run `ionomos repair-ledger`, exit 1.
  - Every command starts when the app-data folder can't be made or the remembered-config file isn't text.
  - The update check never raises on an answer of another shape (a proxy page, a list, a missing download
    address, `size: big`) or a connection cut mid-answer; a download cut mid-file or a Downloads folder that
    can't be written is a `DownloadError`.
  - `health.LOCK_NAME` reads `names.LOCK_FILE`.
- **Round 4: security and privacy** (D99, D100, docs/SECURITY_NOTES.md).
  - `ionomos bundle unpack` keeps every file inside the folder it is given: a job's folder name in BUNDLE.json
    (`../../x`, a drive, an absolute or UNC path) no longer chooses where files land; members with a colon (NTFS
    stream), a Windows device name, a trailing dot or space or a control character are left out and listed; two
    members with one name are a note; a damaged member is a plain error.
  - `ionomos bundle unpack` refuses a zip bomb (a member over 1 GB that deflated more than 100:1) and a bundle
    bigger than the disk's free space before writing anything; `inspect` refuses a BUNDLE.json that claims to be
    gigabytes. The unpacked config.yaml no longer keeps the sender's `library:` block, engine program paths
    (`*_exe`) or FragPipe tool / Python folders.
  - A webhook address with `user:password@` printed the password in the watcher log and `ionomos notify-test`:
    such an address is refused when read, and the path, query and user part of every hook address are hidden
    wherever they appear on their own.
  - Diagnostics and bundles of a config.yaml that does not parse could show a webhook address or password written
    in flow style, as a list item, or on the lines below the key; they now also hide a webhook address or
    password replaced earlier (the secrets of `config-backups/`).
  - Slack messages escape `&`, `<` and `>`: a reason can no longer `@channel` or make a link.
  - A search whose launcher is a `.bat` under a folder with `&`, `|`, `<`, `>`, `^`, `%` or `"` (e.g. `C:/R&D/`)
    ran part of the path as a second command; it now fails at once saying which folder to rename, and preflight
    does not start such a launcher either (D100).
  - The Desktop shortcut failed for a Windows profile with an apostrophe (`C:\Users\O'Brien`); quotes (also the
    typographic ones) are doubled. The startup task refuses a command with `%`.
  - `ionomos library scan` (and adopt / Import workflow) no longer opens a network path written inside a
    workflow or fragpipe-ui.cache it finds; the workflow is flagged WF_DATABASE_MISSING "on another computer"
    (D99). A scan root on a server that is off cost ~20 s each; now 5 s on a helper thread and a note.
  - The assistant's `log_tail` no longer follows a console log that is a symbolic link out of the run folder.
  - `updates.download()` refuses a release without a published SHA-256, a non-https address and an asset name
    that is not `Ionomos-Setup-<version>.exe`; `check_latest()` no longer raises on an odd GitHub answer.
- **Round 4: the assistant.**
  - The audit log lost records when several questions were asked at once (on Windows about one in ten, others
    glued into unreadable lines). Each record is now one write of one whole line under a lock; a line cut short
    by a crash costs only that record; a question with a U+2028 line separator no longer loses its record.
  - `assistant.timeout_seconds` now limits the whole reply; it only bounded each wait for data, so a model that
    trickled a token every few seconds was never stopped. A reply is read in 64 KB pieces, so a 10 MB reply on
    one line stops at the 2 MB cap.
  - `ionomos ask --experiment ²` (or 5,000 digits, or a number SQLite cannot hold) crashed; it now says "there is
    no job". An `--item` id that cannot be a file name in the attention folder is "no attention item".
  - A tool call with megabytes of arguments was sent back to the model in every later request; arguments over
    4,000 characters are now refused and `{}` goes back. Call ids are cleaned, capped and unique.
  - An answer saying "job 2 has now been retried" was shown (only first-person claims were caught); a claim with
    "now" / "just" is not shown either (D57 5).
  - `list_attention` for a job that does not exist said "nothing needs attention"; it is now "there is no job N".
  - A fixture state whose search failed (a too-deep folder) was scored as if it had worked; the states now check
    the jobs that must end done.
- **Round 4: the report's front end.**
  - The report no longer goes blank when its data lacks an optional key (notes, settings, a comparison's
    confidence intervals or counts, a feature's values).
  - A two-sample report's PCA tab said "could not be drawn" (one principal component); PC1 is drawn.
  - An axis over values past 2^53 looped forever and froze the page; a dose-response series of 150,000 curves
    overflowed `Math.min(...)` and its potency plot was not drawn.
  - A one-word search, `desc:` or `/pattern/` that found nothing said nothing and left the table showing every
    hit; it now says "0 matches · not found …" and the table shows nothing. A run of `*` no longer freezes the
    page, `/re/g` and `/re/y` no longer carry their position from one name to the next, and a very long search is
    said back shortened.
  - Keyboard: a QC or enrichment tab, a select, a pager button or a column header whose section is drawn again
    keeps the keyboard; closing a help panel with × or Escape and closing the Export dialog give the keyboard back
    to what opened them; the Export dialog keeps Tab inside it; the "Import .gmt" and "Load style" file pickers
    can be reached with Tab.
  - Light-mode muted text (`--muted`) is `#6f6d68` (5:1) instead of `#898781` (3.5:1, below WCAG AA for 12–13 px
    text); no other colour changed.
  - `npm test` in `tests/js` ran nothing on Node 22; it is `node --test` now, which finds the same files on Node
    20 (CI) and 22.
- **Round 4: the workflow and FASTA library** (D101).
  - The library no longer proposes SILAC3-phospho for the isoDTB method or Nonspecific-HLA-TMT10 for TMT when a
    method's workflow is missing: SILAC, isoTOP, ipIAA and PAL are their own kinds; FragPipe's own stock workflow
    for a kind ranks first; candidates that tie are never proposed and `recommend` / Review proposals… say why
    (#148).
  - Saving a config kept outside C:\Fragpipe_Auto no longer re-points `users.learned_aliases_file` at
    C:/Fragpipe_Auto: when it is not set, it stays beside config.yaml (#149).
  - `library adopt --no-config` never replaces the workflow a method searches with (the copy gets
    `<method>-2.workflow`); `adopt` refuses a workflow of another kind than the method's (`WF_KIND_MISMATCH`) or a
    file with a ✗ mark unless `--force`; the app's Use for <method> asks first and copies beside the saved
    config's files, so only Save changes the next search (#150).
  - A FASTA's organism is named only when most of its targets carry `OX=`: FragPipe's own human GENCODE FASTA is
    no longer "Cow, 59 % unreviewed UniProt"; FASTA_UNREVIEWED needs unreviewed entries to be over half of the
    proteins (#151).
  - `library scan` that stopped early or looked in fewer folders keeps what the last scan found that it did not
    see (`--replace` keeps only the new scan); `--root` adds to the default folders (`--only` for them alone); a
    `library.roots` folder that does not exist is a note; `C:\FragPipe*` matches only real FragPipe
    installations, so C:\Fragpipe_Auto's workflows are not "stock" (#153).
  - Library wording and corners (#156 items 1-5): "An iTRAQ-4 search", "1 protein"; the FASTA organism reason
    says where it came from; `library list --kind foo` says there is no such kind; identical copies are
    represented by the newest FragPipe version; a FASTA without line breaks is read in linear time.
  - The Today tab says when the job list can't be read (a damaged ionomos.db) instead of an empty list, and the
    Jobs tab's Find box no longer matches "none" on every job without a reason (#156 items 15, 16).
- **Round 4: FASTAs.**
  - A FASTA saved as "UTF-8 with BOM" keeps its first protein: `fasta prepare` (Python and Philosopher), the
    library and FragPipe's decoy check (`fragpipe.fasta_info`) all skip a leading byte-order mark. Philosopher,
    which crashed on it, is given a BOM-less copy in its hidden work folder, so the result is byte for byte that
    of the file saved without a BOM; a note says the mark was there (#152).
  - `fasta prepare` refuses a FASTA with text before its first `>` header, naming the line and the fix, instead
    of silently losing the first protein; a UTF-16 file is told to be saved as UTF-8 (#152).
  - A Philosopher failure note leaves out the frames of a Go crash report (#152).
  - FASTA files without line breaks are read in linear time and bounded memory (a 1 GB zero-filled file took
    60 s): one chunked header reader, `fastasource.fasta_headers`, for the library, `fragpipe.fasta_info` and
    `fasta prepare` (#156 item 5).
  - `ionomos fasta release` on a path that does not exist says so and exits 1; a folder stands for the FASTAs in
    it (#156 item 6).
  - `fasta prepare "name with space.fasta"` no longer writes a FASTA name with spaces: spaces become `_`, with a
    note; the note for a destination folder with a space suggests config.yaml's `fasta_dir` (#156 item 7).
- **Round 4: TMT** (D91 unchanged).
  - A TMT job that failed on its channel map is searched with the fixed map after Retry: every search plans from
    the experiment folder's `experiment.yaml` as it is then (`tmtkit.current_block`), not from the block saved in
    `ionomos.json` at intake; the intake block is used only when `experiment.yaml` is gone. `ionomos.json` →
    `run.tmt_map` records the map searched and where it came from. The Analysis tab's channel grid now reaches the
    next search too (#147).
  - `tag: iTRAQ-4`, `iTRAQ-8`, `TMT-35`, `TMT-2`, `TMT-0`, `iodoTMT-6` (FragPipe 24's own label types) are
    accepted at intake, planned and checked by their channels; sCLIP-6, IBT-16, DiLeu-12, DiLeu-1, DeAla-13,
    Trp-2 are accepted and passed on (`TMT_LABEL_UNKNOWN`); `TMT_TAG_MISMATCH` compares any tag with the workflow
    by FragPipe's name; `tmt suggest --tag iTRAQ-4` works; the channel editor no longer turns an iTRAQ map into
    TMT-10 (#154).
  - Multi-plex TMT: `results/fragpipe-analyst/experiment_annotation.tsv` carries each sample's plex and channel
    and replicate numbers used once per condition; `results/experimental_annotation.tsv` keeps every plex's
    `condition_<k>_channel` names when the search had several plexes (one plex stays byte-identical to the lab's R
    script); `TMT_NAME_PATTERN` asks for `condition_plex_channel` with several plexes (#155).
  - TMT polish (#156 items 8-14): a channel written twice in `experiment.yaml`'s `tmt:` is refused saying so; a
    `reference_channel` the kit does not have is said (`TMT_REFERENCE_UNKNOWN`), a reference on an NA channel of
    a single plex too; `tmt plan` says a not-UTF-8 `experiment.yaml` once; `TMT_DUPLICATE_NAME` inside one plex
    says "used twice in plex X"; `TMT_ALL_NA` fails the job before the search; the channel editor says which names
    a label switch put aside and brings them back; exported TMT QC figures have no "hits:" / statistics footer,
    no doubled `<title>`, no "plex plexB".
  - No "TMT sample names don't follow condition_1_channel" note when `sample_conditions` (or an input SDRF) gives
    every sample its condition; with some given, the note counts the guessed ones (#158).
  - A comparison of conditions with long names (runs named after their files) gets its volcano and table: file
    names are cut at 80 characters plus a short hash (`analysis.SLUG_MAX`); the paths had passed Windows' 260 and
    the doctor said "the volcano plot wasn't written" per comparison (#157).
- **Round 4: the testbed's fake FragPipe does what FragPipe 24's source does** (D103; each difference and its
  source is in `fake_fragpipe.py`'s header).
  - A manifest file that does not exist stops a headless run ("Some loaded files don't exist. Please double
    check.", exit 1). It used to be dropped without a word, and WORKFLOWS.md said the same.
  - A bioreplicate `Integer.parseInt` refuses drops its line ("Manifest file contained some badly formatted
    lines") and the run goes on without it. This explains the two runs missing from the lab's first real search
    (REAL_RUNS.md, job 1).
  - Dialog messages keep their `<b>` tags in headless mode (`SwingUtils.stripHtml` only removes line breaks). The
    output and group folders exist before the file and FASTA checks; a check that fails then leaves an empty
    `log_<date>.txt`.
  - A dry run writes the manifest, `fragpipe.workflow` and `fragpipe.job`; `fragpipe.job` is FragPipe's
    tab-separated batch table; `log_<date>.txt` holds the console's text in UTF-8 (a name already taken gets
    `_1`); the console is in the Windows code page (cp1252, `?` for a letter it lacks) with CRLF line ends.
  - `experiment_annotation.tsv` and the file lists are written before the first step; TMT conditions are read
    FragPipe's way. Decoy share "20%", not "20.0%"; new refusal "Database looks to be empty"; the same-name
    refusal is FragPipe's whole message; the execution order lists each command once; `--ram 0` shows FragPipe's
    memory in the commands, and `--ram` / `--threads` values Java refuses get Java's exception.
  - DIA runs with DIA-NN 2.3.2 (the PC's), with its Parquet -> TSV and plot steps. DIA-NN gets
    `<raw>_uncalibrated.mzML`, written beside each raw; the report's run columns carry those names, and the output
    folder gets a flat `psm.tsv`.
  - The open-search "Localize mass shift" ERROR line is printed, and the run goes on. `sdrf.tsv` has the
    precursor window, `NT=Trypsin/P;CS=(?<=[KR])`, a column per modification and the organism from the FASTA.
  - FragPipe's real "<b>MSFragger</b> is not valid ..." (and IonQuant, diaTracer, "Decoy protein prefix")
    messages get their explanation; the `<b>` tags kept them from matching (`fragpipe.EXPLANATIONS`).
  - `ionomos testbed stress`: the TMT drops carry a whole TMT-10 channel map. Since D91 every TMT drop failed
    before the search, so the stress run searched no TMT.
- **A dose-response curve is no longer fitted for a protein measured at fewer than `dose_min_doses` doses
  (#162, D106).** Replicates of two doses counted as four points, so a protein missing at the low doses got a
  4-parameter fit, an "up" call and a tight pEC50 the data could not give. It is now "not fitted" and counted
  in the stage's note, which names `analysis.dose_min_doses`.

### Documentation

- **`docs/REAL_RUNS.md`: the real runs on the lab PC** (D83). These are the
  two DIA searches of 2026-09-23 (0.5.1, 0.5.3) and the analysis of a
  FragPipe GUI result on 2026-10-06 (0.18.0), with what went wrong in each
  and which release fixed it. FIRST_REAL_RUN.md, ROADMAP.md, PROTEOMICS_PC.md,
  VALIDATION.md and TESTING.md no longer say nothing has run on a real
  FragPipe. They record what those runs answered: `fragpipe.bat` works, `.raw`
  works for DIA, FragPipe 24 uses its own DIA-NN 2.3.2, and the PC has .NET 6.
- **Why fragpipe-analyst.org showed a hit that Ionomos didn't**: the protein
  was measured in one condition only. The Ionomos run's global
  missing-value filter (≥ 66 % of all samples) removed it, while the web app
  has no filter by default and imputed its missing values. Without the
  filter, Ionomos gives 19 of the web's 21 hits, that protein included. The
  port was not changed. (A first version of this entry blamed the
  normalisation.)
- New open problems in ROADMAP.md:
  - contaminants are not removed from FragPipe DIA searches, because DIA-NN
    drops the `contam_` prefix (fixed above, D87);
  - `experiment.yaml` replicate numbers are not bounded (fixed above, D85);
  - an empty-vector control reads bait-enriched proteins as "down" (D88, above);
  - the FragPipeAnalystR golden is tidier than a real matrix.
- **Round 4: the docs agree with the code** (`tests/test_docs_sync.py` checks it).
  - `cli.py`'s docstring (the top of `ionomos --help`) lists `preflight`, `stop`, `check-folder` and `attention`,
    which existed but were missing; `run` no longer says "worker: Phase 2". README has a Commands section.
  - ARCHITECTURE.md: the `cli.py` row lists every command and the hidden ones; the on-disk layout adds
    `inbox\.removed\`, `config-backups\`, `logs\ionomos.lock` / `ionomos.pid` / `naming-history.jsonl` /
    `ionomos_benchmark\`, an experiment's `experiment-backups\` and `results\tmt_qc*`, and the app-data and
    Desktop folders; the configuration section names where every other key is explained and has a table of the
    30-odd keys that were explained nowhere, the assistant's runtime keys among them. The three
    `runners/*` rows now say they are 0.4 leftovers nothing imports, and point at `postprocess.py` →
    `downstream/`.
  - TESTING.md: the testbed's drops counted right (29 now), an interrupted job is re-queued, not failed, no stale
    test count, rows for the docs check and every round-4 test file. LIBRARY.md: "Wave 2: the app" is "The app".
  - `docs/SECURITY_NOTES.md` (D99, D100): the threat model of each component, named in ARCHITECTURE's doc list and
    the README docs index.
  - DECISIONS.md: D97–D103 (round 4).

## [0.18.1] - 2026-10-06

### Fixed

- **"Failed to load Python DLL … python314.dll" after installing or updating**
  (D81). This happened since 0.1: an update's installer inherited the old
  app's PyInstaller settings, so the new Ionomos.exe it opened looked for
  Python in the old app's unpacked `%TEMP%\_MEI…` folder, which was already
  deleted. The app now sets `PYINSTALLER_RESET_ENVIRONMENT` for every
  program it starts. The installer sets it too, so updating from an older
  version is fixed as well. The same reset fixes a watcher started from
  the app that lost files when the app was closed.
- **Dropping a folder on the app froze it** (0.18.0, D82). The drop handler
  called Tk from inside Windows' message handling, where Tk was already
  waiting, so the window hung on the first drop. The handler now only puts
  the dropped names in a queue, and the app's timer opens them. It gives
  Windows its own handler back when the window closes, and never raises into
  Windows. A dropped shortcut, zip file, missing drive or anything else that
  isn't a folder or table gets a message saying what to do instead of an
  error. The app comes to the front after a drop.

### Added

- **`Ionomos-fault.log`** next to the exe (D82): when the app or the watcher
  closes in a way Python can't catch, every thread's stack is written there.
  The diagnostics and **Report a problem…** include its end.

## [0.18.0] - 2026-10-06

### Added

- **Analyse FragPipe searches run outside Ionomos, checked first** (D80).
  The Analysis tab's **Folder…**, and a folder dragged from Explorer onto
  the app window (Windows), open a check window. It shows:
  - the FragPipe output it found, at any depth below the picked folder, or
    above it when a part such as `dia-quant-output/` or a table was picked
  - FragPipe's saved workflow, manifest, tables and log
  - what the folder will be analysed as, and why
  - every problem in plain words with what to do: a step that left no table
    (and the failed step and likely cause from FragPipe's log), a table cut
    off or holding only spectral counts, several outputs, no condition
    names, a read-only folder, raw files that were never searched

  Buttons fix what can be fixed there: analyse as another kind, use another
  output, take the conditions from the file names, open the log. Then
  **Review samples** or **Analyse now**. A finished job's folder only opens
  the window when something needs a look. `fpfolder.py`, `folder_check.py`,
  `dragdrop.py` (ctypes, no new dependency).
- **`ionomos check-folder <folder>`**: the same check in the terminal
  (exit 1 when the folder can't be analysed).

### Fixed

- A folder filed as one method but holding another kind of search is now
  analysed as what it holds. Before, a DIA search filed as TMT failed with
  "no tmt-report/abundance_*_MD.tsv found". When the filed method's table is
  missing and the tables (and the saved workflow, when there is one) clearly
  show another kind of search, that kind is read and a note says so.
  `ionomos analyze --method` still reads exactly the method asked for.
- A folder with no Ionomos record now takes its method from FragPipe's saved
  workflow before its table names.
- A FragPipe output in a sub-folder (`<experiment>/fp_out/`) is read from
  there. FragPipe's own `fragpipe-files.fp-manifest` names the samples and
  conditions, as Ionomos' manifest does for its own searches.
- TMT: `abundance_*_GN.tsv` and `abundance_*_None.tsv`, which TMT-Integrator
  writes when median centring is off, are read (with a note); before, only
  `_MD` was.

## [0.17.0] - 2026-10-04

### Added

- **MaxLFQ protein roll-up** (D76): `analysis.rollup: auto | median_polish |
  maxlfq`. It decides how a table of peptides or precursors becomes proteins:
  Sage's `lfq.tsv`, the MSstats format, and DIA-NN and Spectronaut long reports.
  MaxLFQ (Cox et al. 2014) is computed in pure Python
  (`downstream/rollup.py`). For each pair of samples it takes the median
  log-ratio of their shared features, solves least squares per connected
  component and scales to the summed intensity. It matches R's
  `iq::maxLFQ()` 2.0.1 to 1e-9 on 54 proteins, including disconnected sample
  groups, missing values, single features and single samples. Its profiles
  match `diann::diann_maxlfq()` to 1e-3. `auto` keeps every loader's
  previous behaviour: median polish for Sage and the MSstats format, and
  DIA-NN's `PG.MaxLFQ` and Spectronaut's `PG.Quantity` as they are. For a
  DIA-NN report, `maxlfq` / `median_polish` uses `Precursor.Normalised`; for
  Spectronaut it uses `FG.Quantity`, now an optional column in
  `ionomos spectronaut-columns`. When those columns are missing, or the table
  already holds proteins, a note says the setting was not used. When a
  protein's samples share no feature, a note gives how many proteins that
  affected.
- **`analysis.f_test: auto | off`** (D76): `off` switches off the moderated
  F-test ("any change") that limma runs on 3+ conditions of intensity data.
  The report's Methods and settings and `analysis.json` (`f_test: {off:
  true}`) then say it was switched off. The default `auto` behaves as
  before. Time-course F-tests are not affected; `time_course` controls those.
- **`ionomos benchmark --kind rollup`** (D76): simulated peptide tables with
  their own ionisation offsets, missing values and interferences, rolled up
  by median polish or MaxLFQ, with and without imputation
  (`benchmark_simulated_rollup.*`).

- **Spline fits for long time courses** (D77). A series with 7 or more time
  points (`analysis.time_model: auto`, the default) or any series with
  `time_model: spline` is fitted as a natural cubic spline in hours
  (`time_spline_df`, default 4, at most the time points − 2), as in the limma
  User's Guide for many time points: the moderated F on the spline
  coefficients, the series-vs-control test on the interaction of two curves
  (`~group * ns(time)`), and the profile is the fitted curve's change from
  the first time point. `downstream/splines.py` is R's `splines::ns`
  ported step by step. The report's profile and the static `time_profiles`
  figure draw the fitted curve on an axis in hours; `time_course.tsv` gains
  a `model` column, `analysis.json` → `time_course` a `time_model` and
  `spline` per series. A `time_spline_df` the series can't carry raises
  the new `TIME_SPLINE` issue. Series with up to 6 time points are tested
  exactly as before. Checked against R 4.6.1 `splines::ns` (1e-12) and
  limma 3.68.5 (10,350 values, worst relative difference 5.4e-10).

- **The assistant can propose a change; a person confirms it** (D75,
  [docs/ASSISTANT.md](docs/ASSISTANT.md#proposals-and-the-confirm-window)).
  Asked to act, the local assistant may propose one change per question:
  retry a failed search, give a sample another condition, leave a sample out
  (or use it again), one analysis setting (imputation, normalisation, p ≤,
  |log2FC| ≥, the control, what is compared, the comparisons) or a
  condition's role. Ionomos checks it first (a sample or condition the
  experiment has, a value the analysis accepts, a failed job for a retry) and
  opens a window written by Ionomos, not by the model: the job, what Confirm
  does and the `experiment.yaml` diff, with **Cancel** (focused) and
  **Confirm**. Typing "yes" does nothing. Confirm re-checks the proposal
  against the job and file as they are now and uses the app's own Retry and
  the experiment editor's Save. `ionomos ask` prints the proposal and the
  command or app steps; it never applies it. Proposals and decisions are in
  the assistant's audit log. 13 new scenarios (66 in all).
- **experiment.yaml backups**: the experiment editor's Save (and a confirmed
  proposal) keeps the version it replaces in the experiment's
  `experiment-backups/` folder (D75). Backups are never replaced or removed.

- **Acquisition time per raw file** (D78). Intake records when each raw file
  was acquired in `ionomos.json` (`acquisition`), read from the Thermo
  `.raw` file's own header (the acquisition start Xcalibur writes; only the
  first bytes are read, nothing is written), else from ThermoRawFileParser's
  output, the Xcalibur stamp in the name, or the file's modification time,
  marked approximate. Each entry says where its time came from.
- **Run order QC** (D78). A **Run order** tab under Quality control: each
  sample's identifications, missing values, signal, PSMs, mass error and
  missed cleavages (or DIA-NN's precursors and mass accuracy) in the order of
  acquisition, with a drift test within each condition and which runs each
  condition was in. Two new warnings: `RUN_ORDER_DRIFT` (a number drifts
  over the run) and `RUN_ORDER_CONFOUNDED` (the conditions were run in
  blocks, so a drift would look like biology). `analysis.json` →
  `run_order`; a `run_order` figure for slides (`ionomos export --figures
  run_order`, `analysis.export.figures`, the report's zip).

- **Phosphoproteomics, opt-in** (D79, `downstream/phospho.py`). Nothing
  changes unless `phospho: true` is set under `analysis:`:
  - The search's phosphosite table is analysed instead of its proteins:
    FragPipe's `combined_site_STY_79.9663.tsv` (IonQuant, label-free), TMT-Integrator's
    `abundance_single-site_MD.tsv`, or DIA-NN's `report.phosphosites_90.tsv`
    / `_99.tsv`. Sites are named like `MAPK1 T185` and go through the same
    filter, normalisation, imputation, limma, volcano and report as proteins.
  - A localisation filter, `phospho_min_localization` (0.75): applied on
    the best localisation probability of FragPipe's label-free table (and per
    sample with `phospho_localization_per_sample`); for TMT-Integrator and
    DIA-NN, which filter first, their threshold is read and compared
    (`PHOSPHO_LOCALISATION`).
  - `protein_correction` (D70) works on phosphosite comparisons: each site's
    change minus the same comparison's protein change in an unenriched
    proteome, MSstatsPTM's adjustment, as a second comparison.
  - **Kinase activity (KSEA)** with a kinase-substrate table the lab
    downloads (`kinase_substrates`: PhosphoSitePlus's
    `Kinase_Substrate_Dataset`, or KSEAapp's PSP&NetworKIN file; never
    shipped): a z-score per kinase and comparison, `ksea_min_substrates`
    (5), Benjamini-Hochberg; `results/kinase_activity.tsv`, a bar chart in
    the report and the `kinase_activity` figure for slides. The scores are
    KSEAapp 2.0's (checked against it); the p-value is two-sided.
- **STRING partners among the hits** (D79): `string_network` names a STRING
  download (protein.links with protein.info, or a website export), and the
  report lists each hit's partners among the same comparison's hits;
  `results/string_partners.tsv`. Works for proteins too.
- Doctor issues with help: `PHOSPHO_TABLE`, `PHOSPHO_LOCALISATION`,
  `KINASE_SUBSTRATES`, `STRING_NETWORK`.

### Changed

- Every Retry (the Jobs tab, the failed-search pop-up, `ionomos retry`, a
  confirmed proposal) is one function, `worker.request_retry`; the Jobs tab's
  Retry now also closes the job's "search failed" item, as the others did
  (D75).
- The assistant's system prompt and tools changed (five proposal tools), so
  its prompt digest changed: score real models again with `ionomos ask-eval`.

- **The QC trend orders runs by the raw file's header** (D78) when the
  experiment's `ionomos.json` or the file has one, before the name stamp and
  the file time.

- `proteincorr.correct_comparison`: the per-comparison MSstatsPTM
  adjustment is one function, shared by isoDTB site ratios and phosphosites
  (no change in results).
- A site-level analysis writes no FragPipe-Analyst `reproduce_in_R.R` (it
  would read the site table as proteins).


## [0.16.0] - 2026-10-03

### Added

- **Anonymised "Copy diagnostics"** (D74). The app's **Copy diagnostics**
  now replaces the lab's names with the same pseudonyms a bundle uses, and
  the text is searched for every name Ionomos knows before it reaches the
  clipboard (a name left: nothing is copied, and the message says so).
  **Copy with real names** copies the text as before. The saved copy in the
  log folder has its key file next to it, so `ionomos bundle translate`
  reads a pasted copy back. `ionomos diagnose --anonymise` does the same in
  a terminal.
- **DIA-NN's main report and peptide / ion tables in a bundle, on request**
  (D74): `ionomos bundle --include diann-report,peptides` (implies
  `--level validate`) and a box in the **Report a problem** window. Main
  report: `report.tsv`, or `report.parquet` written as text so its names can
  be replaced (needs pyarrow). Peptide level: FragPipe `peptide.tsv` /
  `ion.tsv`, DIA-NN's precursor matrix, MaxQuant `peptides.txt` /
  `modificationSpecificPeptides.txt`. Each is anonymised like the other
  tables, row-sampled above `--extra-mb` (200 MB), and the first thing the
  size limit leaves out. Spectral libraries never go in.
- **`ionomos bundle inspect` checks every file for real names** (D74): each
  file of the zip, and its name, is searched for the lab's user,
  experiment and sample names (on the lab's PC) and for the originals in the
  key file (next to the zip, or `--key`), and listed as clean or with what
  was found.
- **`ionomos spectronaut-columns [--out DIR]`** (D74): the columns a
  Spectronaut report needs for Ionomos and how to make a report schema with
  exactly those in Spectronaut. Ionomos does not ship an `.rs` schema file:
  its format is Spectronaut's own and not published.

- **Ask about this** (D72, [docs/ASSISTANT.md](docs/ASSISTANT.md#ask-about-this)):
  a button in every pop-up and in the needs-attention list. It asks the local
  assistant a question written for that kind of item (editable) and shows the
  answer with its sources, as plain text, in a window of its own; the answer
  is made off the Tk thread. Not set up, not answering or paused: Ionomos's own
  explanation and the help, as a normal state. Nothing from the item's names
  goes into the question, and an item without a job is named to the model by
  its kind and time.
- **`ionomos ask-eval`** (D72): scores a model on this PC over the assistant's
  scenario corpus with the same rubric as CI, timing the first token and each
  answer, idle and while a search runs, and writes a scorecard (JSON and a
  table) that is never written over. `--scripted` checks the runner without a
  model. Only an address on this PC is accepted, checked before anything is
  built. The corpus (`ionomos/assistant/scenarios/`) now ships with Ionomos;
  15 scenarios that only make sense with their script are marked
  `harness_only` and left out of the scorecard.
- **`assistant.keep_alive` and `assistant.while_searching`** (D72): how long
  the runtime keeps the model loaded (sent only when set; Ollama reads it),
  and what changes while the worker runs a search (another model or address,
  a shorter keep-alive, a longer timeout, or a pause), read from the worker's
  heartbeat. The table of what Ollama and llama-server honour is in
  docs/ASSISTANT.md.

- **isoDTB site changes corrected for protein abundance** (D70, ROADMAP 5C #3):
  `analysis.protein_correction: {proteome, match, conditions}` names an
  unenriched proteome (an analysed Ionomos experiment, an MSstats
  groupComparison table or an Ionomos `*_differential.tsv`; only read). Each
  site's log2 heavy / light minus its protein's change on the same scale
  (from `liganded_direction`), with MSstatsPTM's SE, Satterthwaite df and BH
  per condition, checked against MSstatsPTM 2.14.0 to 1e-9
  (`tests/golden/ptm/`). Reported beside the uncorrected comparison as
  `<condition> (log2 H/L vs 0, protein-corrected)` with its own volcano and
  table; sites whose protein is not found are flagged. Off by default.
  New issues `PROTEIN_CORRECTION_CONDITIONS` (a site condition without its
  proteome comparison; never guessed) and `PROTEIN_CORRECTION`.
- **Opt-in centring of isoDTB ratios** (D70): `analysis.ratio_centre: none |
  median | auto` (default `none`). `auto` centres a condition's replicates on
  their stable sites only when one is clearly off 0 (a heavy / light mixing
  error), without the median's shift when many sites go one way. The
  offsets are always measured; with `none` a clear one is the note
  `RATIO_OFFSET`. Liganded calls say which ratios they used. `ionomos
  benchmark --kind isodtb` runs the centring settings too; a `centring` grid
  holds the D70 numbers.
- Every `*_differential.tsv` has `se` and `df` columns (the standard error
  and degrees of freedom behind `t`).
- `cysteine_sites.tsv` gets `<compound> protein_log2_R` and
  `log2_R_corrected` when the protein correction ran.

### Changed

- CI's test jobs may run 25 minutes instead of 15: the Windows jobs took
  up to 14 min 46 s with the 0.16.0 suite (D73).

- `ionomos bundle translate KEY FILE` reads the file line by line (a main
  report can be gigabytes).
- The Spectronaut loader reads its columns from the same list
  (`engines.SPECTRONAUT_COLUMNS`) that `spectronaut-columns` prints.

- The assistant's time to first token waits for the first generated token
  (text, reasoning or a tool call), not the opening event. Answers and audit
  records say whether a search was running (`mode`).

- **TMT across plexes: the normalisation looks within each plex, and IRS on
  the plex means is no longer liberal** (D71, from the D66 benchmark's open
  questions):
  - With several TMT plexes that are not on one scale (IRS off, or not
    possible), `normalize: auto`'s composition check and the `ratio` method
    are worked out within each plex and combined. Across such plexes a
    protein jumps with the plex, which hid a pulldown: with the plex as a
    block, 59 % of the calls at adjusted p alone were false (unchanged
    proteins -0.19 to -0.26 log2 off); now 4.7 % (within 0.04). The doctor's
    `NORMALISATION_COMPOSITION` now also fires there, and says the check was
    made within plexes. Plexes joined by IRS are compared all together as
    before, so the default's numbers are unchanged (4.4 % / 3.8 %); without
    plexes the check is the D64 one to the last digit.
  - IRS on each plex's own mean (`irs: sum`, used when no reference channel
    is found) estimates the plex level from the channels it then tests;
    limma's residual df are now reduced by the plexes - 1 for each protein
    (`plex.df_spent`), unless the design already has a block per plex. FDP
    on the simulated grid 6.6 % → 5.1 % with changes both ways, 5.3 % → 4.0 %
    in a pulldown (worst scenario 9.3 % → 7.7 %), sensitivity unchanged; checked against limma 3.68.5 in R
    (`tests/golden/tmt_sum/`). `analysis.json`'s `model` says so
    (`plex_df`).
  - "How far to trust this" has a **TMT plexes** line: how the plexes were
    put on one scale (reference, plex means with the df reduced, a plex
    block), marked "check" when they are neither on one scale nor in the
    model, or when a t-test follows IRS on the plex means.
  - New doctor warning `TMT_PLEXES_NOT_IN_MODEL` (with help): `irs: none`
    and no block for the plex, which keeps the tests valid but finds 40 %
    instead of 95 % of 2-fold changes.
  - Two more calibration guards in the test suite (IRS on the plex means;
    a pulldown without IRS).

- The liganded-site rule text ends with the ratios it used ("on the ratios as
  measured (not centred)").

### Fixed

- **A hung test run now says where it hung and ends** (D73). The suite had
  hung on macOS three times with nothing on screen until a 30-minute limit
  killed it. It did not hang again in 14 full runs and about 3,300 more
  tests from the process- and thread-heavy files on macOS / Python 3.14,
  with up to three suites running at once, so its cause is not known.
  Instead:
  a test still running after 5 minutes prints every thread's stack and ends
  the run (`faulthandler_timeout` + `faulthandler_exit_on_timeout` in
  `pyproject.toml`; pytest ≥ 9 in `[dev]`); a process that can't exit after
  its last test is dumped and ended after 120 s (`tests/conftest.py`,
  `IONOMOS_TEST_EXIT_SECONDS`); every wait in the tests that had no time
  limit has one now (a `proc.wait()`, a `join()`, the `ps` / `tasklist`
  probes, `python -m ionomos` and `git` runs, the R probe), a second
  `ionomos run` whose output is read is killed and drained if it doesn't
  answer, `while worker.run_once()` loops are capped, and the fake SMTP
  server stops when its client goes away in the middle of a message instead of
  spinning. `tests/test_hang_guards.py` checks the guards themselves.
- **Windows: a `taskkill` or `tasklist` that never returns can't hold the
  worker** (D73). Stopping a search, the time limit, a cancel, stopping a
  leftover FragPipe and the app's Stop gave `taskkill /T` no time limit; it
  now gets 60 s, then Ionomos goes on as if it had failed (a stuck
  `tasklist` counts as "not running").


## [0.15.0] - 2026-10-02

### Added

- **Settings and checks in the app instead of `config.yaml` or a terminal**
  (D67):
  - **Analysis tab → Figure style**: the lab's style for exported figures
    (`analysis.export`, D62): size preset or a custom size, text size and
    font, colours (with a colour picker for a custom palette), background,
    what the "Export for slides" zip holds, and which figures are written to
    `results/figures/` after each analysis. Empty fields show and take the
    default; keys the page doesn't show are kept. **Check** says what the
    figures will look like, or what is wrong.
  - **Analysis tab → Check accuracy**: **Compare** an analysis with a
    reference result (a table or another analysed folder) and **Run
    benchmark** on simulated data (quick / standard, optionally with an
    experiment's settings) or on a benchmark sample with its expected
    ratios. Runs in the background, shows the verdict in colour and opens
    the page. Same code as `ionomos compare` / `ionomos benchmark`.
  - **8 Notifications** tab: `notify:` (Teams, Slack, a JSON webhook,
    SMTP email; which events; names or not), with **Send test** (the same
    code as `ionomos notify-test`, on the values in the window). Addresses
    and the password are masked unless "Show" is ticked, and never logged.
  - Help entries for each (`faq.figure-style`, `faq.check-accuracy`, and
    `faq.notify` rewritten for the tab); a Help button on each page.

- **Roles in the experiment editor and the review window** (D65,
  [docs/WORKFLOWS.md](docs/WORKFLOWS.md)). Each condition is listed with its
  number of samples and its role (control, compound, competition of a
  compound, pool / reference, QC standard) and where the role came from. A
  list changes it; **automatic** goes back to the name. A role read from a
  word that can mean something else (`pre`, `block`, `cold`, `10x`) is
  marked **?** with a **Confirm** button. Under the list: the comparisons
  that will be run, in words, and what uneven groups mean ("DMSO has 2
  samples, Probe 4: a feature needs 1 of 2 DMSO values"). Choices are saved
  as `analysis.roles` in `experiment.yaml`. The review window shows this for
  DIA and label-free drops.

- **Unequal groups checked against R's limma** (D66, [docs/VALIDATION.md](docs/VALIDATION.md)).
  A competition experiment with DMSO 2, Probe 4 and Probe_Comp 4, missing
  values and rows on each edge of the filters, goes through the analysis'
  own steps (filter, median normalisation, no imputation or Perseus-type,
  the role comparisons, `small_group_min_valid: half` and `same`, limma, BH)
  and agrees with limma 3.68.5 to 1e-8 for every feature
  (`tests/golden/unequal/`, with the R script that made it).
- **`ionomos benchmark --kind isodtb` and `--kind tmt`** (D66). The simulated
  benchmark now covers isoDTB site ratios (FragPipe's label quant through the
  lab's site table; replicates, sites changed one way, a heavy / light
  mixing error) and several TMT plexes with a pooled reference (MaxQuant's
  reporter intensities; IRS on the pool or on the plex means or none, `auto`
  or `median` normalisation, a pulldown). Each kind writes its own
  `benchmark_simulated_<kind>.*`; `--like` picks the experiment's kind. The
  test suite has a calibration guard for each. Measured: both defaults are
  calibrated (isoDTB 4.9 % false discoveries with 3 – 4 replicates, TMT 3.8 –
  4.4 % where 4 – 4.75 % is aimed at); in a TMT pulldown, median centring
  after IRS makes 67 % of the calls at adjusted p alone false and `auto`
  holds. What the grids found and was not changed (a heavy / light mixing
  error is not corrected; TMT without IRS; IRS on plex means slightly
  liberal; two isoDTB replicates) is in the roadmap's open questions with
  the numbers.

- **Figures for slides from the dose-response, time-course and liganded-site
  sections** (D68, `downstream/sectionfigs.py`). `ionomos export` and
  `analysis.export.figures` now also draw, in the same export style:
  - `dose_potency_<compound>.svg`: pEC50 against the curve's fold change,
  - `dose_curves_<compound>.svg`: a grid of curves (points, the fit, the 95%
    interval of pEC50), the six most relevant regulated or those you name,
  - `time_patterns_<series>.svg` and `time_profiles_<series>.svg`: the
    patterns of changing features, and the most significant features over
    time with the series they were compared with,
  - `liganded_rank_<compound>.svg` and `liganded_selectivity.svg`: sites
    ranked by competition ratio, and which sites each compound ligands.
  `figures:` takes these names or `dose`, `time`, `liganded`. The report's
  **Export for slides** .zip holds them too (one file per curve and feature
  there), as well as each series' patterns and the selectivity map.
- **Choose what to export**: `ionomos export --list` names every figure the
  report can draw and what can be chosen for it; `--figures` takes kinds,
  groups or those names (`volcano_Drug*`); `--features EGFR,BTK` and
  `--top N` choose the curves, profiles and sites drawn. A grid that would be
  too small to read at the chosen size draws fewer panels and says so.
- **PNG from `ionomos export`** (`--format png | both`, `--png-dpi`,
  `--png-scale`, `--renderer`), drawn by a program the computer already has:
  cairosvg, resvg, rsvg-convert or Inkscape (also found in its usual install
  folder). Ionomos still installs nothing for it; without one it says what to
  install and writes nothing. The PNG carries its print size and the
  cut-offs, as the report's does.

- **A fault-injection suite for FragPipe searches** (D69,
  `tests/test_faults.py`). The testbed's fake FragPipe acts out a hang, being
  ended from outside, a full disk, a raw file vanishing, garbled and huge
  console output, and empty, header-only, cut-off or missing result tables,
  per experiment (`fake_fragpipe_mode.txt`) or for all of them
  (`IONOMOS_FAKE_FP_MODE`, comma-separated). The testbed has `fp_cut_table`
  and `fp_hang` samples, and the stress tester mixes such faults into its drops.
- **TMT drops laid out one folder per plex** (`<plex>\*.raw`) are filed and
  searched as dropped: each folder is a plex, with its own `annotation.txt`.
  A flat drop with several plexes is filed as before, with a warning that
  FragPipe will name the channels itself (testbed samples `tmt_plexes`,
  `tmt_flat_plexes`).
- New plain-English causes for a failed search: ended from outside, a crash,
  the time limit, a runaway console log, a result table not written to the
  end, a run that did not finish, a raw file that disappeared during the
  search, an error inside Ionomos. New holds, with help pages: a raw file
  still open in another program, a path with a space (on Windows), earlier
  output that can't be moved aside.

### Changed

- `ionomos compare`, `ionomos benchmark` and `ionomos notify-test` now run
  through `accuracy.py` / `notify.run_test`, shared with the app. Their
  output is unchanged.

- In a report with more than one compound or time series, an exported
  dose-response curve or time-course feature is named after both
  (`dose_curve_CmpdA_EGFR.svg`), so the .zip keeps one of each.

### Fixed

- **A journal-size figure style got slide-size text.** An `analysis.export`
  block naming `size: col1` (or `col2`, `half`) without `font_pt` was read
  by the app with the slide's 14 pt and written back that way on the next
  Save. Now the size's own text size applies (7 pt for a journal column).

- **The experiment editor could not take a choice back.** It merged its
  choices into the saved `analysis:` block, so a sample used again, a role
  back to automatic or cleared comparisons stayed in `experiment.yaml`. The
  editor now writes the whole block (D65).

- **A raw file in a drop laid out one folder per plex could not be removed
  from the app** (D69). The inbox list, the review window's **Delete** and
  the name check now find raws there too (`intake.raw_paths`).

- **The experiment editor's Normalisation list lacked `auto` and `ratio`**
  (D64). It now offers the same choices as the Analysis tab.

- **A FragPipe left running by an Ionomos that was ended from Task Manager
  or crashed ran beside the re-run of its job.** The next start now stops it
  first, only when it is certainly the process Ionomos started.
- **A job found running at start-up** kept saying `running` in its folder;
  after three interruptions it was failed without a `FAILED.txt`.
- **A result table cut off by a full disk counted as a finished search**; so
  did exit code 0 without the end line and without any result table.
- **An error inside Ionomos during a search** (e.g. a full disk for its own
  files) left the job `running` and FragPipe possibly unwatched.
- **Re-running a job twice within one second** failed it (the earlier output's
  folder name was taken).
- **A second job row for the same experiment folder** searched it again over
  the first job's output; it is now refused as a duplicate.
- **Console logs**: the reason's "last lines" read the whole log into memory;
  text in the Windows code page, UTF-16 and colour codes are now read
  correctly in `FAILED.txt` and the hints.


- **A test of the slow-copy wait failed now and then on Windows CI.** Its
  settle time (0.3 s) was only six times the pause between copied files, so
  a slow runner made the watcher file a half-copied folder. The test now
  uses 2 s. The app's own settle time (`stable_seconds`, 60 s) is unchanged.

## [0.14.1] - 2026-10-01

### Changed

- **Normalisation: `auto` is the new default** (D64, [docs/WORKFLOWS.md](docs/WORKFLOWS.md)).
  It is median centring, with identical numbers, unless that would shift
  the conditions against each other; then the samples are normalised on the
  ratios of their stable features (`ratio`, also selectable). An existing
  `config.yaml` that says `normalize: median` keeps median centring; set it
  to `auto` on the Analysis tab.

### Fixed

- **A pulldown's unchanged proteins were shifted by the normalisation.** When
  many features are enriched in one direction, median centring moved every
  unchanged feature the other way (about 0.2 log2 with 8 % enriched in
  simulation), and with two controls some passed the fold-change cut-off.
  `auto` now detects this and normalises on stable features: the shift
  falls to about 0.01 log2 and the false hits from 11 to 2 over 8 simulated
  experiments without imputation (6 to 4 with it). With `median` or `gn`
  chosen, the report now asks (`NORMALISATION_COMPOSITION`). `analysis.json`
  → `normalisation` records what was used. Checked on simulated pulldowns
  only.

## [0.14.0] - 2026-10-01

### Added

- **The analysis knows the experiment's design** (D61; `downstream/roles.py`).
  Each condition gets a role: control, compound, competition (the probe plus
  a competitor), pooled reference or QC standard. Roles are read from the
  condition names, from `analysis.roles` in `experiment.yaml` /
  `config.yaml`, or from a `characteristics[role]` column of an SDRF, and
  are shown in the report (Methods → Settings used) and in `analysis.json`
  → `roles`.
  - With a competition condition (`Probe_Comp`, `Probe+Comp`, `ProbeComp`,
    `Comp`, `…_competition`, `…_excess`) the default comparisons follow the
    design: compound vs control, competition vs its compound, competition vs
    control. A second control, a pool and a QC standard are not compared.
    `comparisons:`, `de_type: all | others` and `role_comparisons: false`
    work as before; with `control:` set, every condition is still compared
    with it. Without a competition condition nothing changes.
  - **Specific targets**: per compound, the features enriched against the
    control and competed off by the competitor, each at the report's
    cut-offs. `results/specific_targets.tsv` (both fold changes, both
    adjusted p-values), `analysis.json` → `specific_targets`, and a report
    section with enrichment against competition and the quadrant of
    specific binders marked.
  - **Unequal groups** (DMSO n=2 against a compound n=4) are handled
    knowingly. The report says how many samples each side of each
    comparison had, and the Power tab gives the minimum detectable fold
    change per comparison with those numbers. Without imputation (TMT), the
    smaller group of an unequal comparison needs half its samples measured
    instead of `min_valid` (`small_group_min_valid: half`; `same` is the
    earlier rule): on simulated TMT data with two DMSO channels, 6.1 % of
    the features were untested against DMSO before and 0.1 % are now, with
    no false hit among them. The sample scorecard no longer scores a sample
    of a small group as scattering more than the others. A low-confidence
    label names the real group size.
  - Two new notes: `COMPETITION_DESIGN` (what was read, how to change it)
    and `ROLES_UNSURE` (asks, when a name is ambiguous: `Probe_pre`,
    `Probe_10x`, or a competition that can't be linked to one compound).
    Glossary entries for role, control, compound and competition.
  - New settings: `roles`, `competition_keywords`,
    `competition_keywords_weak`, `role_comparisons`,
    `small_group_min_valid`. Competition keywords are also on the Analysis
    tab (Lab defaults).
  - **Not confirmed by the lab**: the competition keywords (`Comp` is the
    one form seen on the lab PC) and the specific-targets rule. **Not
    tested on a real experiment**: simulated data only.

- **Export for slides, and one export style** (D62). Every chart in the
  report has **SVG**, **PNG** and **Export…**; the top bar has **Export for
  slides**.
  - **Export…** opens one dialog with a preview: the size (16:9 slide, 4:3
    slide, half a slide, one or two journal columns, or a custom width and
    height in px or mm), text size (pt) and font, line and point size, the
    palette (the default, colour-blind safe, greyscale, or your own up / down
    / neutral colours), a white, dark or transparent background, title and
    subtitle (on / off, editable), legend, the cut-offs line, which names are
    drawn, and the PNG resolution (1× to 4×, or 150 / 300 / 600 dpi). The
    figure can be downloaded as SVG or PNG, or copied to the clipboard as an
    image where the browser allows it.
  - The SVG keeps text as text, writes colours out (no CSS), names the font
    with fallbacks and uses no `foreignObject`. Every file records the
    experiment, comparison, cut-offs, hit filters and test (SVG `<title>` and
    `<desc>`; PNG a `Description` text chunk and its print size), so a figure
    on a slide can be traced back.
  - **Export for slides** saves one .zip, built in the browser: every figure
    (a volcano and p-value histogram per comparison, compare, heatmap,
    enrichment with a result, each QC tab, and what else the report shows) as
    SVG and / or PNG, the tables as CSV, the style, and a README that lists
    each file with its cut-offs. File names are safe on Windows.
  - The style is kept in the browser and used by every report opened in it.
    **Save style** / **Load style** share it as a small JSON file; **Reset to
    lab defaults** goes back to the lab's style. A loaded file is checked key
    by key.
  - **`ionomos export <experiment or results folder>`** writes the volcano,
    PCA, heatmap and correlation figures as SVG to `results/figures/` from
    the finished report's own data, with the same style options (`--preset`,
    `--palette`, `--font-pt`, `--style FILE` …). SVG only: PNG needs a
    renderer, and no dependency was added for it; the command says so.
  - **`analysis.export:`** in `config.yaml` (or `experiment.yaml`) holds the
    lab's style and `figures:`, the static figures written after every
    analysis (default: none). The config writer keeps the block, commented.
  - The heatmap, a canvas on screen, exports as SVG. The CV charts got export
    buttons.
  - **Options are easier to read**: plain labels with units, a tooltip on
    every control and QC tab, a **?** on the Plot and export groups, the
    cut-offs in force written under the options, and **Reset to lab
    defaults** (cut-offs, plot options, hit filters). **Reset** is now called
    **Reset cut-offs**. No option was removed and no default changed.
  - Changed with it: the SVG and PNG buttons now use the export style (before:
    the chart at its on-screen size, PNG at 3×). Text cells in every CSV
    export that start with `=`, `+`, `-` or `@` get a leading apostrophe.
  - **Not checked**: opening the SVG files in PowerPoint, Illustrator or
    Inkscape; any browser but Chromium; the real clipboard; Windows.
    docs/DECISIONS.md D62 lists what was verified and how.

- **FragPipe preflight** (D59; `preflight.py`): `ionomos preflight`, and the
  app's **Check FragPipe install** now runs it. Besides reading the
  installation from disk it starts FragPipe twice over, without a window and
  without a search: `fragpipe.bat --help` (version, Java, .NET) and one
  `--headless --dry-run` per FragPipe method, in which FragPipe itself
  checks its tools, the FASTA, the workflow and the TMT annotation and lists
  the commands it would run. It also checks: the launcher is `fragpipe.bat`,
  FragPipe's own Java, MSFragger / IonQuant / diaTracer and the Thermo
  reader folder, Philosopher, Python, DIA-NN, FragPipe's settings cache,
  each workflow's tools against what is installed, decoys, paths without
  spaces, free disk, RAM and threads against the settings, room for long
  paths, write permission. Each line says what to do. `--static` starts
  nothing; `--raw PATH` uses a real file for the dry run; `--method M`
  limits it. Its files go to a new folder under `<log_dir>\preflight\`.
- **Run fingerprint** (D59; `fingerprint.py`): every search, whatever its
  outcome, leaves `ionomos_run\run_fingerprint.json` next to the job: the
  launcher and command line, FragPipe's version block, the workflow's key
  settings, the names and sizes of the output files, the first and last
  lines of the console log, what Ionomos' parsers read out of it, and the
  timings. Text only, a few tens of kB; an earlier one is kept as
  `run_fingerprint_<time>.json`. It is for checking the parsers against the
  first real runs.
- **More failed searches get a plain cause** (`fragpipe.EXPLANATIONS`, from
  FragPipe's source and issue tracker): no Java for `fragpipe.bat`, an
  option FragPipe doesn't know, decoys missing or not about half, a path
  with a space, a licence that expired, MSFragger's `ext` folder missing, a
  missing .NET runtime, "Not enough memory allocated to MSFragger", the page
  file running out, a path or command line too long for Windows, DIA-NN that
  can't be started, a failed DIA-NN step, Python / FragPipe-SpecLib not set
  up, a TMT annotation FragPipe refuses, two raw files with one name,
  contradicting workflow settings, antivirus or a sync tool holding a file,
  odd characters in the FASTA.
- `fragpipe.config_python` (FragPipe's `--config-python`), in `config.yaml`
  and the app's Advanced tab. FragPipe 24 on Windows uses the Python inside
  its installation whatever this says; it is for other setups.

- **A bundle for troubleshooting and validation** (D63; `bundle.py`,
  docs/DEV_LOOP.md). **Report a problem…** is now a window with the note,
  the jobs, three boxes and a list of what will go into the zip with its
  size; it is also in the failed-search and failed-analysis pop-ups and on
  the Jobs tab (**Zip for troubleshooting…**). The zip is saved on the
  Desktop (also a OneDrive Desktop; else the log folder). Ionomos sends
  nothing. `ionomos bundle [JOB | FOLDER …] [--level diagnose|validate]
  [--out DIR] [--no-anonymise] [--keep-conditions] [--max-mb N] [--dry-run]`
  does the same from a terminal.
  - `diagnose`: settings, logs, crash files, needs-attention items, each
    job's status, run folder (workflow, manifest, console logs, a
    `run_fingerprint.json` when present) and `analysis.json`.
  - `validate`: also the search's result tables and `results/`, so that
    `ionomos bundle unpack ZIP DIR` and `ionomos --config DIR/config.yaml
    analyze DIR/<experiment>` repeat the analysis. `ionomos bundle inspect
    ZIP` says what a bundle holds.
  - Never raw / mzML / `.d` files, FASTA files (name, size, entry count and
    SHA-256 are recorded) or spectral libraries. A size limit (2,000 MB) and
    row-sampling of PSM tables over 25 MB, both listed in the zip.
  - Names are replaced by default: users, the PC, experiments, raw files,
    samples and conditions, e-mail and IP addresses, the same pseudonym for
    the same name in every file, table header and file name. Protein and
    gene identifiers and all numbers are kept. The key file is saved next to
    the zip, not in it (`ionomos bundle translate KEY FILE` turns an answer
    back). The finished zip is searched for every original; if one is found,
    no zip is saved.
  - `BUNDLE.json` and `README.txt` in the zip: version and build, level,
    what was included, capped or left out and why, file hashes.
  - `ionomos diagnose --zip` and the app's **Save diagnostics bundle** now
    make a `diagnose` bundle (anonymised, with a key file), and no longer
    replace a file of the same name.
  - Limits of the replacing (free text, names inside longer words, plain
    words of a name standing alone, pictures) are listed in the help
    (**What Ionomos will never do** → the zip for troubleshooting).
    **Not run on the lab's real folders or on Windows outside CI.**

- **Accuracy checks a lab member can run, and an analysis that holds up on
  messy tables** (D60; [docs/VALIDATION.md](docs/VALIDATION.md)).
  - **`ionomos compare <experiment folder> <reference>`**
    (`downstream/compare.py`): an Ionomos analysis against another result
    for the same experiment: another Ionomos run, or a results table
    (FragPipe-Analyst export, limma, MSstats long format, Perseus, the lab's
    R output). Features are matched by ID or gene. Per comparison it reports
    matched / only in one, Pearson and Spearman of log2FC, slope and offset,
    hit calls (both / only Ionomos / only reference, at each side's own
    cut-offs and at common ones), the largest disagreements, p-value
    agreement, and a verdict: `agrees`, `agrees after an offset of …`,
    `differs: …` or `not judged: …`, from thresholds the page states.
    Writes `compare.tsv`, `compare.json` and `compare.html` (scatter plots)
    into `results/`. Tested on references made from Ionomos' own result;
    **not on a real export of those tools**.
  - **`ionomos benchmark`** (`downstream/benchmark.py`). Without a folder:
    the pipeline on simulated tables with planted changes over a grid
    (2 to 6 replicates, 2 controls vs 4 treated, 1.5- / 2- / 4-fold, three
    levels of missing values) for each imputation / normalisation setting;
    it reports sensitivity, the observed false discovery proportion against
    the nominal alpha, and the fold-change bias. `--like <folder>` adds an
    experiment's own settings and group sizes. Measured on the standard
    grid: the default (Perseus + median) 4.0 % observed FDP at adjusted
    p ≤ 0.05 and 30 % / 71 % of 2- / 4-fold changes found; no imputation
    4.6 % and 47 % / 86 %; no normalisation 14 – 21 %. With a folder and
    `--expected hye.yaml`: an analysed mixed-species (human / yeast /
    E. coli) or spike-in experiment against its expected ratios per species
    or protein list: measured vs expected (median, spread, a box plot), the
    false positive rate in the unchanged background, sensitivity among the
    changed. Species come from UniProt entry names, FASTA `OS=`, a column
    or a FASTA. **No real benchmark run exists yet**; VALIDATION.md says how
    to make one.
  - **A calibration guard in the test suite**: a small simulated grid whose
    observed FDP must stay at or below 8.5 % (measured 1.7 – 6.3 %; the
    tolerance is from 30 other seed blocks).
  - **"How far to trust this"** under the key findings of every report and
    in `analysis.json` → `trust` (`downstream/trust.py`): samples per group
    and balance, replicate agreement, missing and imputed values, per
    comparison what was tested, hits resting on imputed values and the
    p-value histogram shape, and power, each with its number. No score. A
    line is marked "check" when its own check crossed its threshold. A
    compare or benchmark result in the results folder is shown there too.
  - **Guards on statistics that run but may not mean what they say**
    (`downstream/guards.py`), each a new warning: `NO_RESIDUAL_DF`
    (features tested with one value per group), `VARIANCE_PRIOR` (limma's
    prior could not be estimated or did not converge), `ZERO_VARIANCE`
    (identical replicates), `IDENTICAL_SAMPLES` (two samples with the same
    values).
  - **Messy input**: a seeded fuzz of `analyze()` and the loaders
    (`tests/test_robustness.py`), and notes for what a table held besides
    numbers: duplicate or blank IDs, repeated or missing column names,
    columns left out, text and infinite cells, negative intensities,
    ragged rows, implausible values. Decimal commas and thousands
    separators are now read in tab and comma files too.
  - The default output of a well-formed analysis is unchanged apart from the
    new list in the report and the `trust` key: the FragPipe-Analyst, limma
    and R golden tests pass as they were.

### Changed

- **A failed search's reason names the step and what it said**: "FragPipe
  step MSFragger failed (exit code 1); it said: …" with that step's last
  lines, instead of FragPipe's closing "Cancelling N remaining tasks".
- **The progress line** counts steps ("MSFragger (4 of 31 step(s) done)")
  and knows step names with brackets or a colon.
- **TMT**: a channel map FragPipe would stop on fails the job before the
  search, with the rule (every channel of the label type listed, `NA` for
  unused ones, one name without spaces per channel, no name twice). Plexes
  that share a folder get no annotation file and a warning; plexes each in
  their own folder get one `annotation.txt` each.
- **A FASTA without usable decoys holds the job** ("waiting: FASTA … can't
  be searched") instead of letting FragPipe fail on it, and is a ✗ on the
  setup checklist.
- **The testbed's fake FragPipe copies the real one** (`fake_fragpipe.py`):
  its options, checks, messages, console layout, exit codes and output
  files, with the source of each named. New failure modes for testing:
  `IONOMOS_FAKE_FP_MODE=speclib | no-java | locked | diann | cancel-exit0 |
  no-done-line | child`. The sample TMT drop lists all ten channels; the
  failing sample (`fp_fail`, a DIA drop) now fails in the DIA-NN step, as a
  DIA search has no IonQuant step.
- The default launcher path is `C:/FragPipe/FragPipe-24.0/bin/fragpipe.bat`
  (the 23 / 24 installer's layout).

### Fixed

- **`fragpipe.bat` found no Java on the lab PC's kind of setup.** The
  launcher is Gradle's start script and needs `JAVA_HOME` or `java` on PATH;
  the PC has neither. Ionomos now sets `JAVA_HOME` for it to the `jre`
  folder in the FragPipe installation.
- **`FragPipe-24.0.exe` without a `fragpipe.bat` beside it is no longer
  run.** It is a window program that returns at once; the job is held with
  that explanation.
- **Every failed real search would have been blamed on the FASTA.** The hint
  "No protein database" matched the bare setting name `database.db-path`,
  which FragPipe prints on every run with all its other settings. It now
  needs FragPipe's own messages. A test runs every hint against a healthy
  FragPipe log.
- **A successful retry could be marked failed** because of the failed step
  of an earlier attempt still in the console log. Only the latest attempt's
  part of the log is judged, also for the progress line.
- **The progress line named the last step from the start.** FragPipe lists
  every command before running any; that list was read as steps starting.
- **Two TMT plexes in one folder got two annotation files**, which makes
  FragPipe use neither. See Changed.
- A run in which FragPipe cancelled its remaining tasks, or only did a dry
  run, but exited 0 is now a failed search.
- FragPipe 24 writes DIA-NN's tables to `dia-quant-output\`; the fake and
  the docs said `diann-output\` (the analysis already read both).

- A failed FragPipe search on .raw files no longer gets the cause "A .raw
  file couldn't be read" from Thermo's `RawFileReader reading tool` banner,
  which FragPipe logs on every such search. 0.13.0 fixed this for the
  converter's name in Sage jobs; the reader's name was still matched alone.
  Either name now needs an error word on its line, or `ionomos sage-job`'s
  own `converting X failed` line (also with exit code 0 and no mzML
  written). A name inside a path does not count, so a converter that could
  not be started is no longer reported as a bad raw file.
- Found by fuzzing the analysis with messy tables (D60):
  - A table of raw intensities with one negative cell was read as log2
    values, so the statistics ran on numbers around a million and the QC
    crashed. The median now decides; negative intensities count as missing,
    with a note.
  - Two columns with the same name in a table were both read from the last
    of them (the same values twice). Each is now its own sample.
  - A numeric column without a header became a sample of its own condition
    (named `sample`, the next one `.2`). It is now left out, with a note.
  - A tab-separated table with decimal commas (`1234,5`) or thousands
    separators (`1,234,567.8`) was refused as having no numeric columns.
  - A sample column where a few cells held a number too large for a float
    (`1e400`) was dropped without a word; so was a column with no values.
    Such cells now count as missing, and columns left out are named.
  - A value beyond any measurement (an intensity of 1e300, `1e308` in a log2
    table) crashed the QC, insight, statistics or liganded-site steps with an
    overflow. Values beyond 2^±100 now count as missing, with a note.
  - A FragPipe table with two columns of one sample name counted that sample
    twice with identical values. The repeat is now dropped, with a note.
  - An isoDTB sample name with a character no file name can hold (a NUL
    byte, `:`, `?`) crashed the read step when its site table was written.
  - A table with one feature said "nothing could be tested" without the
    reason (median normalisation leaves nothing). A note now says it.
- **FragPipe's own `sdrf.tsv` is no longer taken for the experiment's design.**
  FragPipe 24's stock workflows write one into the output folder with no
  sample names and no conditions; the analysis read it as the user's SDRF and
  raised "needs your input" (`SDRF_UNMATCHED_RUNS`) on every search. It is now
  skipped with a note.

## [0.13.0] - 2026-10-01

### Added

- **Search quality per run** (ROADMAP Phase 4, D55; `downstream/psmqc.py`).
  For FragPipe results with `psm.tsv` files, the report's Quality control
  section gets a **Search quality** tab with, per raw file: PSMs, peptides
  and proteins; the precursor mass error (median, quartiles, 5th to 95th
  percentile, ppm); the share of PSMs with a missed cleavage; the charge
  states; the peptide length. With DIA, it shows DIA-NN's own per-run
  summary (`report.stats.tsv`). Output: `results/psm_qc.tsv` and
  `analysis.json` → `psm_qc`. The numbers come from the reader the
  instrument QC trend already uses (`qcmetrics.py`); files are streamed, and
  one over 4,096 MB is left unread with a note. Two new warnings, with wide
  limits the lab has not confirmed: `PSM_MASS_ERROR` (a run's median error
  is 10 ppm or more from 0) and `PSM_MISSED_CLEAVAGES` (half or more of a
  run's PSMs have a missed cleavage). New setting: `psm_qc` (false switches
  it off). **Not tested on real FragPipe output**: the `psm.tsv` column
  names are from the FragPipe documentation.

- **Notifications when a search is done, failed or waiting** (ROADMAP
  Phase 4, D58; `notify.py`). Off by default; set up under `notify:` in
  `config.yaml`:
  - channels: a generic JSON webhook, a Microsoft Teams webhook (an Adaptive
    Card), a Slack incoming webhook, and SMTP email (STARTTLS or SSL).
    Standard library only
  - a message holds the experiment name, user, method, status, the reason
    or the hit counts, and the local path of the report; never a file or a
    quantity. `notify.include_names: false` sends the job number and status
    only
  - `on: [done, failed, held]` picks the events. A waiting search sends one
    message per reason, also across a restart
  - a message is sent after the status is recorded, in its own thread, with
    a timeout and no retries: a dead webhook can't fail or delay a job. A
    channel's error is logged once
  - webhook addresses and the SMTP password can come from environment
    variables (`url_env`, `password_env`), are never logged, and are
    replaced by `***` in `ionomos diagnose`, the diagnostics bundle and
    **Report a problem…**
  - `ionomos notify-test` sends a test message to every channel and says
    what happened; `ionomos check` shows whether notifications are on
  - not in the app yet: edit `config.yaml` (the app keeps the block when it
    saves)
- **Log rotation that is safe on Windows** (`health.SafeRotatingFileHandler`).
  `ionomos.log` already rotated at 5 MB with 5 old files kept, but a rename
  refused by Windows (another program has the file open) made the stock
  handler drop every log line until the file was free. Now the watcher keeps
  writing to the same file and tries the rotation again a minute later.
  `app.log` uses the same handler.

- **Sage TMT** (ROADMAP 5B #5, D56, [docs/ENGINES.md](docs/ENGINES.md)).
  - **Import:** `ionomos analyze <Sage output folder>` reads `tmt.tsv`
    (reporter ions per spectrum) with `results.sage.tsv` (the PSMs) beside
    it. PSMs are targets of rank 1 at spectrum, peptide and protein
    q ≤ 1%, joined to their reporter ions by file and scan. Proteins are
    grouped by razor peptides and summarised per plex and channel as the
    MSstatsTMT format already is (one PSM per peptide ion, fractions
    combined, global median normalisation, Tukey median polish). Several
    plexes are then joined by IRS on the reference channel, as for the
    other engines (D48). `tmt_1 … tmt_n` are the kit's channels in order.
  - **Run:** a lab `sage_config` with `quant.tmt` (`Tmt6` … `Tmt18`) no
    longer holds the job. Label-free quantification is not switched on
    for it; the job expects `tmt.tsv`. The files of a plex are its
    fractions (`<plex>_F<fraction>.raw`, the TMT naming rule).
  - **Channel names:** experiment.yaml's `tmt:` map names the channels and
    gives them their condition (the text before the first `_`, as for
    FragPipe TMT); a channel it calls `NA` or `empty` is left out. Without
    a map the channels are `<plex>_<channel>` with condition `unassigned`,
    and the analysis asks for the conditions (Analysis tab, or an SDRF).
    Plexes whose channels have no condition yet are not scaled by their
    own means.
  - **Instrument QC from Sage:** a QC-standard run searched by Sage is
    trended from `results.sage.tsv`: PSMs, peptides, proteins, summed
    fragment signal, a signed precursor mass error, missed cleavages,
    charge and RT ([docs/QC_TREND.md](docs/QC_TREND.md)).
  - A method whose `sage_args` has `--parquet` is held: Sage's Parquet
    output is not read (Sage itself calls the format unstable).

  Tested against the stand-in Sage only (`ionomos fake-sage` now writes
  `tmt.tsv` and the full `results.sage.tsv` header). The `tmt.tsv` and
  `results.sage.tsv` layouts come from Sage's source, not from a real run.

- **The assistant, read-only "Explain"** (ROADMAP Phase 6.1, D49, D57;
  `ionomos/assistant/`, [docs/ASSISTANT.md](docs/ASSISTANT.md)).
  `ionomos ask "why did my search fail?" --experiment 12` answers from the
  job's log, the doctor's findings and the help, through a model running on
  the PC. **Built and tested against a scripted fake model only: no real
  model or runtime has been tried, none is recommended, and the assistant is
  off by default.**
  - It speaks the OpenAI-compatible chat API to `assistant.base_url`
    (standard library only). An address that is not this PC is refused before
    anything is sent.
  - Seven read-only tools with schema-checked arguments: list experiments, a
    job, attention items, an issue code in the help's words, a numbered log
    tail, the analysis summary, help search (BM25; SQLite FTS5 or pure
    Python). No file paths as arguments, no changes, no shell, no network.
  - Every paragraph of an answer must cite `[issue:CODE]`, `[log:JOB#LINE]`,
    `[help:ID]`, `[analysis:FIELD]` or `[job:ID]`, and each citation must be
    something a tool returned. Otherwise the answer is not shown and Ionomos
    prints its own text: likely causes, fixes, the help entry, and who to
    ask. The same text is the answer when the assistant is not set up.
  - Names, logs and `experiment.yaml` are treated as untrusted: tool results
    are passed as data, cleaned of control characters and capped.
  - Every question is appended to `assistant-audit.jsonl` in app data.
  - New: `assistant:` in `config.yaml`, an `assistant` row in `ionomos
    check`, help entries `faq.assistant` and `faq.assistant-setup`, and 53
    scenarios in `tests/assistant_scenarios/` replayed in CI.
  - Not built: the "Ask about this" button in the pop-ups, and a runner that
    scores real models. Phase 6.1's exit criteria are still open.
- **Time courses** (ROADMAP 5C #1, D53; `downstream/timecourse.py`). When the
  conditions are time points (`Drug_0h`, `Drug_1h`, `Drug_4h`, `Drug_24h`,
  or `analysis.times`) and a series has at least 3, every feature gets
  limma's time-course tests on the comparisons' own model (block and
  covariates included):
  - change over time: the moderated F on every time point against the first
  - a trend: the moderated t on the linear contrast over the ordered time
    points
  - with a control series (`DMSO_0h`, `DMSO_1h`, …): whether the series
    responds differently over time (the interaction F)

  Changing features (the report's alpha and |log2FC| cut-offs) are classed
  up / down / mixed and grouped into patterns by profile shape. Output:
  `results/time_course.tsv`, `analysis.json` → `time_course`, and a **Time
  course** report section: pattern charts to click, a sortable table, and
  each feature drawn over its replicates with the control series dashed.
  New settings: `times`, `time_unit`, `time_min_points`, `time_course`; a
  `TIMES` issue when a time point can't be read. Checked against limma
  3.68.5 to 1e-8 (plain model, replicate block, missing values).

- **Liganded cysteines for isoDTB** (ROADMAP 5C #3, D52;
  `downstream/cys.py`, [docs/WORKFLOWS.md](docs/WORKFLOWS.md)). Each site is
  called per compound by the chemoproteomics rule: liganded when the
  competition ratio R reaches 4 in at least 2 replicates, inconsistent when
  it does in fewer, not liganded, or too few replicates to say. Every part is
  a setting, for the lab or one experiment: `liganded_ratio`,
  `liganded_min_replicates`, `liganded_direction` (`high`: R = heavy /
  light; `low`: the other way round), `liganded`.
  - Per compound: the liganded fraction. Across compounds: selective, shared
    and unresolved sites. Per protein: how many of its cysteines are
    liganded, with "most sites" marked.
  - `site_annotation`: a site table the lab downloads (CysDB, or its own
    list) marks each site known liganded / known hyperreactive / seen before
    / new. CysDB is not shipped.
  - Output: `results/cysteine_sites.tsv`, `results/cysteine_proteins.tsv`,
    `analysis.json` → `cysteines`, and a **Liganded sites** report section
    (tiles, a rank plot of R against the threshold, a site × compound table
    shaded by R, a protein view), with help, glossary and Methods text.
  - Two new report notes: `LIGANDED_DIRECTION` when far more sites would be
    liganded with the ratio reversed, and `SITE_ANNOTATION` when the
    annotation file can't be used.

  The calls use measured ratios only (no normalisation, no imputation). Not
  included: correcting site changes for protein abundance. The defaults
  (R ≥ 4, 2 replicates, heavy / light) are the field's common ones and have
  not been confirmed by the lab; the CysDB layout is from its paper, not a
  real download.

- **Sage as a search engine** (ROADMAP 5B #5, D51, [docs/ENGINES.md](docs/ENGINES.md)).
  - **Run:** a DDA method with `engine: sage` is searched by the lab's own
    [Sage](https://github.com/lazear/sage) (open source, MIT). Thermo `.raw`
    files are first converted to mzML with the lab's ThermoRawFileParser
    (`raw_converter:`) into `<experiment>/sage_mzml/`; a retry reuses what is
    already converted, and the raw files are never touched. Settings are the
    lab's own Sage JSON (`sage_config:`) or Ionomos' defaults (tryptic,
    high-resolution MS2, label-free), written to `ionomos_run/sage.json`.
    Watching, holding, cancel, Retry, pop-ups and the analysis work as for
    FragPipe; results go to `<experiment>/sage/`, and a second run keeps the
    first as `sage_previous_<time>/`. Sage's usage telemetry is switched off
    whenever the installed Sage has the flag for it.
  - **Import:** `ionomos analyze <Sage output folder>` reads `lfq.tsv`:
    peptides at q ≤ 1% (and proteins at q ≤ 1% when `results.sage.tsv` is
    there), proteins grouped by razor peptides, each sample's fractions
    added, protein quantities by Tukey median polish, gene names from the
    search's FASTA. The report's Data source shows Sage's version and FASTA
    from `results.json`.
  - New help entries for a held Sage job (Sage not found, raw file converter
    not found, Sage settings missing or unusable); held MaxQuant jobs now
    open their own help entry too.

  Tested against stand-ins for Sage and ThermoRawFileParser only
  (`ionomos fake-sage`, `ionomos fake-rawparser`), with the file formats
  taken from Sage's source. Not yet run against a real Sage.

### Changed

- **A method that is `like:` a built-in method is that method everywhere**
  (D54; ROADMAP "found while building 5A/5B"). `naming.methods.<key>:
  {like: isoDTB | TMT | DIA}` used to borrow the name rule only, and the
  method was then analysed as generic label-free. Now a `DIA_phospho` or
  `TMTpro` method gets the built-in method's whole behaviour: the TMT
  `annotation.txt`, the expected FragPipe outputs, the control question in
  the review window, the analysis, the SDRF and the doctor's messages. A
  method run by another engine is what the engine makes (`engine: diann`:
  DIA under any key, which used to get no analysis; `maxquant` / `sage`:
  label-free), whatever `like:` says. One resolver decides
  (`naming.method_kind`, `Config.kind`). Built-in keys and configs without
  `like:` behave as before. A FragPipe method that used `like:` only for the
  shape of its names should now write the template out
  (`files: '{sample}_{rep}[_{fraction}]'`).
  `ionomos analyze --method` also takes the config's own method keys, and
  `ionomos names test` prints what a custom method is run as.

### Fixed

- A failed Sage search no longer gets the hint "A .raw file couldn't be read"
  just because the console log names the converter after successful
  conversions; the hint now needs the converter's own error or exit code.

## [0.12.0] - 2026-09-30

### Added

- **Dose-response curves for titrations** (ROADMAP 5C #2, D44;
  `downstream/doseresponse.py`). When the conditions are doses of a compound
  (`Cmpd_10nM`, `Cmpd_0p1uM`, … or experiment.yaml `analysis.doses`) and a
  compound has at least 4 doses above the control, every feature gets
  CurveCurator's fit (Bayer et al., *Nat. Commun.* 2023; Apache-2.0), ported
  to pure Python:
  - a 4-parameter log-logistic curve on ratios to the control, with pEC50
    and a 95% interval, EC50 in the doses' unit, slope and plateaus
  - the recalibrated F-statistic and its p-value, a BH q-value, the curve
    fold change and the relevance score
  - up / down / not / unclear classes with CurveCurator's defaults (alpha
    0.05, |log2 fold change| 0.45)

  Output: `results/dose_response.tsv`, `analysis.json` → `dose_response`, and
  a report section with a sortable, filterable table, each curve drawn over
  its measured points (control at the left), and a potency vs effect scatter;
  SVG / PNG export, and the report search rings matching curves. Works for
  intensities (ratio to the mean of the DMSO runs) and isoDTB ratios. New
  settings: `doses`, `dose_unit`, `dose_response`, `dose_min_doses`,
  `dose_alpha`, `dose_fc_lim`. Fewer doses: a note, no curves. Unreadable
  doses: a `DOSES` issue. Checked against CurveCurator 0.6.0 itself on 600
  simulated curves: identical classes, pEC50 within 0.05 for 263 of 266
  regulated curves (`tests/golden/dose_response/`, `tests/test_dose_response.py`).
  `simulate.dose_titration` / `dose_pg_matrix` make titrations with known
  pEC50s.

  The 95% pEC50 interval is clipped to the range a fit may take: a steep curve between two doses has no
  local error estimate, and it read like -907792 – 907806.
- **Help for users** (D46, [docs/HELP.md](docs/HELP.md)). One set of plain
  Markdown texts in `ionomos/help/` covers getting started, reading the
  report chart by chart, a glossary, troubleshooting (every analysis issue
  code, pop-up kind, rejected folder and held or failed search), what Ionomos
  never does to your data, and common questions. It is shown in three places:
  - **every report**: **Help** in the top bar, a **?** beside each section
    title, QC tab and issue box that opens its text in place, and a Help
    section at the end with the glossary and what to do about the issues in
    that report (offline, inside the file);
  - **`help.html`**, the whole help with a search box: the app's new **Help**
    button, **More help** in each pop-up (opened at the topic that explains
    it) and `ionomos help --open`;
  - **`ionomos help [TOPIC]`** prints one topic (`NO_TABLE`, `pca`,
    `glossary`, …).

  Tests fail when an issue code, attention kind, intake rejection or held-search
  reason has no help entry.

- **Analysis-only install from pip** (ROADMAP Phase 5A, D40). `pip install
  ionomos`, then `ionomos analyze <table or folder>`, on Windows, macOS or
  Linux with no Tk; a test runs the analysis in a Python where `import
  tkinter` fails. New [docs/QUICKSTART.md](docs/QUICKSTART.md): a 10-minute
  guide for someone new, covering what `analyze` reads and what the report
  shows.
- **`ionomos demo [FOLDER] [--open]`**: writes a simulated DIA experiment and
  analyses it, offline. It has DMSO / DrugA / DrugB × 4 replicates, planted
  hits, on/off proteins and gene-set shifts, with a bundled demo `.gmt`. The
  default folder is `./ionomos_demo`, then `_2`, `_3`, ... if taken; the demo
  never writes into a folder that holds anything.
- PyPI metadata in `ionomos/pyproject.toml`: SPDX licence, classifiers,
  keywords, project URLs, and a short PyPI-facing `ionomos/README.md`.
- `.github/workflows/publish-pypi.yml`. On `v*` tags or by hand, it builds the
  wheel and sdist, runs `twine check`, installs them into fresh venvs on
  Windows / macOS / Linux and runs `demo` and `analyze` from them. It uploads
  with PyPI trusted publishing only once the maintainer has configured it
  (steps at the top of the file).
- **SDRF-Proteomics sample sheet for every analysed experiment**
  (`results/sdrf.tsv`, `downstream/sdrf.py`, D38). It follows spec v1.1.0,
  template `ms-proteomics`, and has one row per raw file: per channel for TMT,
  and a light and a heavy row for isoDTB. Where the values come from:
  - samples, replicates and conditions: the manifest and the analysis
    (renamed conditions applied; left-out samples still listed)
  - fractions: the file names
  - TMT channels: `annotation.txt`, experiment.yaml `tmt:` or the sample names
  - organism: the FASTA's `OS=`
  - enzyme and Unimod modifications: FragPipe's workflow
  - instrument, organism part, cell type, disease: a new `analysis.sdrf`
    setting (config.yaml lab-wide, experiment.yaml per experiment)

  Anything unknown is `not available`. `analysis.json` → `sdrf` and the
  report's Methods name the columns to fill in before depositing to PRIDE.
  A table analysed on its own gets none. Checked with the official validator
  (`sdrf-pipelines`, dev-only, installed in CI on Linux).
- **Configurable naming** (`config.yaml` `naming:`, D37; ROADMAP Phase 5A).
  Another lab's convention is now a config change, not a code change:
  - `naming.methods.<name>`: each method's `.raw` rule as a template
    (`'{condition}_rep{rep}'`, `'{sample}_R{rep}_F{fraction}'`, `[ ]` for
    optional parts, `{any}` to skip text), a regex with named groups
    (`pattern:`), or `like: isoDTB | TMT | DIA`. New method keys get file
    rules this way.
  - `naming.date_formats`: `YYYYMMDD`, `MMDDYYYY`, `DDMMYYYY`, `MMDDYY`,
    `DDMMYY`, `YYMMDD`, tried in order.

  The built-in rules are now templates that compile to the same regexes as
  before, so nothing changes without the block. Mistakes are config errors
  that name the setting: a bad template, a regex without a `sample` group, an
  unknown field or date format, or a method keyword listed under two methods
  (or blank). Numbers read by any rule must be 1–999 (D30). The app's Save
  keeps the new settings.
- **`ionomos names test <names…>`** (`namecheck.py`): prints how the live
  config reads folder names, `.raw` names or folders on disk (user, method,
  file rule, date, and each file's sample / replicate / fraction), or why a
  name is rejected. `.raw` names after a folder name are read as its files.
  The app's Methods tab has the same check (**Test names…**), using the
  unsaved settings.
- A `.raw` name for a method with no file rule now says how to add one
  (`naming.methods.<name>`).
- **Results from other engines** (`downstream/engines.py`, docs/ENGINES.md,
  D36). `ionomos analyze` (and the Analysis tab's Table…) recognises and
  loads:
  - DIA-NN standalone: `pg_matrix`, the 1.x `report.tsv`, and the 2.x
    `report.parquet` with the optional `pip install ionomos[parquet]`
  - MaxQuant `proteinGroups.txt` (LFQ / Intensity / TMT reporters)
  - Spectronaut pivot and long reports
  - AlphaDIA `pg.matrix.tsv`
  - MSstats-format tables (quantms, Skyline, …), summarised by Tukey median
    polish
  - Proteome Discoverer protein exports

  Long reports are filtered at 1% precursor and protein FDR, and conditions
  and replicates are taken from the engine when it records them.
- **Data source in every report and in `analysis.json` (`engine`)**: engine,
  version, tools (MSFragger / IonQuant / DIA-NN versions under FragPipe),
  table, quantity, FDR filter, FASTA and parameter files, when the folder
  records them. The Methods paragraph names the engine that produced the
  numbers.
- MaxQuant `CON__` contaminants are removed like FragPipe's `contam_`.
- **The watcher can run DIA-NN** instead of FragPipe for a DIA method
  (`engine: diann`, `diann_exe`, optional `library` / `diann_args`; D39,
  docs/ENGINES.md):
  - each job's settings are written to `ionomos_run/diann.cfg`
  - results go to `diann/`
  - cancel / Retry / pop-ups / previous-attempt folders work as for FragPipe
  - the setup checklist checks the DIA-NN install and FASTA / library

  Done notes, failure pop-ups and logs name the engine that ran.
- **An SDRF as the design** (`downstream/sdrfdesign.py`, D47, docs/ENGINES.md). A
  `*.sdrf.tsv` / `sdrf.tsv` in the experiment folder, or next to the table
  given to `ionomos analyze`, sets each run's condition (`factor value[...]`;
  several joined, or picked with the new `analysis.sdrf_factor`), biological
  replicate, TMT plex and pooled reference. Runs are matched by
  `comment[data file]` (and `comment[label]` for TMT). `sample_conditions`
  still wins; the SDRF beats the engine's own columns, the manifest and the
  names. `results/sdrf.tsv` is never read back. `analysis.json` → `design`
  says where the conditions came from; runs the SDRF doesn't name raise
  `SDRF_UNMATCHED_RUNS`.
- **MSstatsTMT format import** (`engines.load_msstats_tmt`, D48): summarised
  as MSstatsTMT's `proteinSummarization(method = "MedianPolish")` (fractions
  combined, global median normalisation, median polish per run, Norm-channel
  normalisation between runs), identical to MSstatsTMT 2.20 to 1e-9 on a
  golden file; one sample per mixture and channel.
- **TMT across plexes: IRS** (`downstream/plex.py`, ROADMAP 5C #5, D48).
  Several plexes (MaxQuant experiments, MSstatsTMT mixtures, Proteome
  Discoverer files, SDRF file groups) are put on one scale on their reference
  channel (new `analysis.tmt_reference`, experiment.yaml
  `tmt.reference_channel`, the SDRF's pooled rows, or names like Pool / Norm),
  or on the plex means when every plex holds the same conditions (new
  `analysis.irs: auto | reference | sum | none`). Otherwise the doctor warns
  (`TMT_PLEXES_NOT_NORMALISED`). TMT-Integrator abundances are never scaled
  twice. The report's PCA colours by plex and shows the values before and
  after IRS; `analysis.json` → `tmt` records what was done.
- `ionomos analyze --method MSstatsTMT`.

- **Experimental designs** (ROADMAP 5C #1, D42): blocks (batch, plex, pair,
  patient) as fixed effects and numeric or factor covariates, in limma's
  general linear model (`downstream/design.py`). Set under `analysis:`:
  `block: replicate`, `block: {sample: block}`, `block_from: <regex>`,
  `covariates: {name: {sample: value}}`. It is fitted like lmFit →
  contrasts.fit → eBayes, with missing values per row, and checked against
  limma 3.68.5 to 1e-8 (`tests/test_design.py`). A design that can't be used
  raises `DESIGN_NOT_USED` and falls back to `~0 + condition`.
  `BATCH_SUSPECT` now suggests `block: replicate`. `fragpipe-analyst/`
  gains `reproduce_design_in_R.R`, since `test_limma` can't fit a block.
- **Moderated F-test** for experiments with 3+ conditions: "any change between
  the conditions" (limma topTableF). It appears as the `F` / `F_p` /
  `F_p_adj` columns of `<level>_results.tsv`, `analysis.json` → `f_test`,
  and an "Any change (F)" tile and table column in the report.
- **DEqMS** (ROADMAP 5C #4, D43): `variance_prior: deqms` gives each
  protein a prior variance from its peptide (or PSM) count. It is a port of
  DEqMS 1.30 spectraCounteBayes, including R's loess, and matches the package
  to 1e-8. Features without a count keep limma's prior; a table without counts
  warns `DEQMS_NOT_USED`.
- The report's Methods paragraph and Settings table and `analysis.json` →
  `model` describe the model used (formula, blocks, covariates, variance
  prior, F-test).

- **Instrument QC trending** (ROADMAP 5C #6, D45, new
  [docs/QC_TREND.md](docs/QC_TREND.md)). Runs of the lab's QC standard (a
  HeLa or K562 digest) are recognised by name: `qc_trend.match`, default
  `hela, k562, qc_std, qcstd, _qc_`. A dedicated QC method also counts
  (`qc_trend.methods`). A replicated design is never mistaken for one. After
  each search of one, Ionomos:
  - reads its numbers from the search's own tables: IDs, signal, peak width,
    MS1/MS2 mass error, missed cleavages, charge, and the RT of the most
    intense peptides (DIA-NN `report.stats.tsv` / `pg_matrix` / `report.tsv`,
    FragPipe `psm.tsv` / `combined_protein.tsv`)
  - stores them in `logs/qc_trend.jsonl` (a re-run updates its row)
  - judges them against a baseline (the first 10 runs, or pinned dates):
    Levey-Jennings z-scores, the Westgard rules 1-3s / 2-2s / R-4s / 10-x
    (1-2s warns) and a CUSUM drift flag, with a plain-English verdict
    ("Precursors 18% below baseline (1-3s) — check the column and the spray…")
  - rewrites `logs/qc_trend.html`: Levey-Jennings charts, every run,
    self-contained
  - when a rule is broken, raises a `qc_trend` attention item (a warning; no
    pop-up unless `qc_trend.popup: true`), which closes when a run is back
    within the baseline

  `ionomos qc-trend [--rebuild] [--open]` rebuilds the page; `--rebuild`
  re-reads past QC runs under users_root, read-only. The app's Jobs tab has an
  **Instrument QC** button. It is all isolated: a QC table that can't be read
  never fails the job.
- The testbed's fake FragPipe and fake DIA-NN write DIA-NN's
  `report.stats.tsv`; the fake FragPipe also writes a `psm.tsv` per DDA
  experiment (FragPipe's columns). A raw name containing `QCBAD` makes a bad
  injection.

- **The watcher can run MaxQuant** for a DDA method (`engine: maxquant`,
  `maxquant_exe`, optional lab `mqpar`; D50, docs/ENGINES.md). Each job's
  `ionomos_run/mqpar.xml` starts from the lab's saved parameters or MaxQuant's
  own `--create` template. The job's raws, `<condition>_<replicate>`
  experiments, fractions from the names, FASTA, threads and output folder are
  filled in. Results go to `maxquant/`, and the analysis reads
  `proteinGroups.txt`.

### Changed

- ROADMAP Phase 6 (D49): a local AI assistant on the proteomics PC. It runs local-first, grounded in citations,
  can only propose changes the user confirms, and is evaluated by a scenario scorecard. A Help section is added
  to Phase 5A.
- A relative `enrichment_gmt` (in `experiment.yaml` or the lab config) is read
  from the experiment folder when the file is there.
- `simulate.dia_pg_matrix` can name the proteins, plant chosen effects and
  leave proteins out of whole conditions. Output for existing seeds is
  unchanged.
- ROADMAP Phase 5: 5A and most of the 5B imports ticked off; issues found on the way listed.
- ROADMAP Phase 5 and D36: the plan for making Ionomos useful to other labs
  (pip-installable analysis, engine adapters, SDRF, configurable naming, more
  engines and analyses).

### Fixed

- An instrument setting at the end of a raw file name is part of the sample,
  never a replicate or fraction (D41, NAMING_CONVENTION.md). FAIMS runs
  `…_DIA_CV-35/-45/-55.raw` were read as one sample with replicates 35, 45 and
  55; they are now three samples. The same applies to `X_CV35` (was condition
  code CV, rep 35) and TMT `…_CV-40` (was fraction 40). isoDTB names ending
  in a setting are refused with a hint.
- MaxQuant TMT with several experiments no longer analyses the channel totals
  over experiments (`Reporter intensity corrected 1`) as extra samples.
- Proteome Discoverer TMT columns `Abundance: F1: 126, Sample, DMSO` are
  recognised (the pattern needed a space before the colon), and tab-separated
  files whose headers hold many commas are no longer read as comma-separated.

## [0.11.0] - 2026-09-30

### Added

- **Deeper QC in every report** (`downstream/insights.py`, D35). Each sample
  gets a scorecard, with robust z-scores and plain-word flags, in the report
  and in `sample_qc.tsv`. The scorecard covers:
  - identifications and missingness
  - loading before normalisation
  - correlation with its own replicates
  - spread around its group mean
  - the group's CV with the sample left out

  Also new:
  - Each principal component is related to condition and to replicate number,
    so a batch shows up as "PC1 follows the replicate number".
  - Missing values are checked against intensity (low-abundance dropouts or
    random gaps), which says whether low-value imputation fits.
  - The p-value histogram gets its shape and Storey's π0.
  - New doctor warnings (report and attention list, never a pop-up):
    `SAMPLE_OUTLIER`, `BATCH_SUSPECT`, `IMPUTATION_MISMATCH`,
    `P_VALUE_SHAPE`, `IMPUTATION_DRIVEN`.
- **Discovery views**:
  - **Key findings** at the top of the report: top hits, on/off features,
    pathways and the sample verdict per comparison.
  - **Only in one condition**: features measured in most replicates of one
    group and never in the other, the hits a t-test can't see. They are listed
    in the report, in `presence_absence.tsv`, and marked ▲ on the volcano.
  - **Compare comparisons**: log2FC against log2FC in four quadrants, plus an
    UpSet chart of the hit lists; a bar marks its features.
  - **Rank-based gene-set test** on every protein, with no cut-off. It is a
    Wilcoxon rank-sum test with the variance inflated by the set's
    inter-gene correlation, as in limma's camera, and comes with a barcode
    plot. Results are in `gene_set_ranks.tsv`.
  - A protein's **"Behaves like"** list: the proteins most correlated with it
    across samples.
  - For site-level (isoDTB) data: a **Proteins** view of how many of each
    protein's sites move.
  - New QC tabs: missingness vs intensity, mean–variance, abundance rank, and
    **power**. The power tab shows the smallest detectable fold change against
    replicates per group, from the experiment's own noise.
- **Volcano search**:
  - Accepts a single word, a pasted list (with "found 38 of 42, not found:
    …"), `KRT*`, `/^RPL\d/`, `desc:kinase` or `term:apoptosis`.
  - Suggestions as you type; <kbd>/</kbd> jumps to the box.
  - Matches are labelled and shown in the table, heatmap, compare plot and
    abundance rank.
- Other volcano and page features:
  - **Box select** mode on the volcano.
  - **Highlight groups**: named, coloured gene lists kept in the browser,
    shown in every report, with .gmt import and export.
  - **Hit filters**: ignore imputation-driven hits, or require at least N
    peptides / PSMs. Peptide / PSM counts are read from DIA-NN, FragPipe and
    TMT-Integrator tables.
  - Plot options for point size, label size and cut-off lines.
  - PNG export next to SVG.
  - "Copy up / down genes" for STRING or Enrichr.
  - The page state (comparison, cut-offs, search, open protein) is kept in the
    address, and a **Link** button copies it.
  - `ionomos analyze` prints the on/off count and π0 per comparison.

### Fixed

- A `<!--` inside a protein or sample name could stop the report from
  drawing (finding F-01). The data script now escapes it.
- One chart that fails to draw no longer blanks the rest of the page.
- Running the tests on a dev machine no longer covers the screen with Tk
  windows. Tests that open real windows run in CI; locally they run only
  with `IONOMOS_GUI_TESTS=1`.
- Flaky GUI test `test_open_window_picks_up_a_user_added_meanwhile` on macOS.
  Config reloading was fine. The test accepted the window with a synthetic
  Return key, which Tk drops whenever the window has lost keyboard focus (for
  example while someone uses the desktop). It now presses Accept directly, and
  its watchdog check runs after the window closes instead of before it opens.

## [0.10.1] - 2026-09-28

Same features as 0.10.0, which was tagged but never released: its installer
build stopped on a stress-test harness race.

### Fixed

- `ionomos testbed stress`: the "wait until settled" census no longer crashes
  when intake moves a folder between listing a raw file and reading its size.
  The final no-raw-lost check stays strict.

## [0.10.0] - 2026-09-28

### Added

- **Review before filing**: every drop that parses opens the naming window in
  review mode. It shows user, method, date, each file's condition / replicate /
  fraction (all editable), and one line per condition marking the CONTROL and
  the treated ones with their replicates and fractions. A Control picker pins
  a different control in `experiment.yaml`. On by default
  (`gui.review_drops`); drops are filed as read when there's no display. (D34)
- **DIA condition codes**: `X_D1.raw` = DMSO rep 1, `X_C2.raw` = Compound
  rep 2 (1–2 letter codes; `naming.condition_codes`, default `D: DMSO`,
  `C: Compound`). Instrument tails like `HCD33` are unaffected.

### Fixed

- A user or alias added in the app now counts without restarting the watcher:
  it re-reads `config.yaml` when it changes (keeping the last good copy if a
  file is mid-save), and an open naming window refreshes its user list and
  fills in a blank user when an alias like `KC` → Kosuke appears.
- Saving settings in the app keeps `gui.review_drops` and `naming:`, and a
  saved window answer that sets a control no longer wipes other
  `experiment.yaml` analysis settings.

## [0.9.0] - 2026-09-27

### Added

- **Every comparison gets a plot.** A group with one sample is tested anyway
  (limma borrows the variance from the replicated groups; Welch becomes a
  pooled t-test) and labelled *low confidence*. With no replicates anywhere,
  the plot is *fold change only* (log2FC against abundance, or rank for ratio
  data), with no invented p-values. Both labels appear in the report, the SVG,
  `analysis.json` and the CLI output. (D32)
- **Any table → volcano plots**: `ionomos analyze <file>` and Analysis tab →
  **Table…** read TSV / CSV / TXT / Excel `.xlsx`. Quantity tables (MaxQuant,
  Spectronaut, Proteome Discoverer, hand-made sheets) go through the full
  pipeline; results tables (limma, Perseus, DESeq2, FragPipe-Analyst exports)
  are plotted as given. Output goes to `<table>_ionomos/`, never beside the
  file. (D33)
- `EACH_OWN_CONDITION`: when every sample is its own condition (e.g. replicates
  named `_a`/`_b`), a grouping is suggested for you to confirm.

### Changed

- `SMALL_GROUP` no longer blocks a comparison; it remains only when not even a
  fold change can be computed. `ONE_SAMPLE` applies to intensity data only (a
  single isoDTB replicate still gets its fold-change plot).

## [0.8.0] - 2026-09-27

### Analysis robustness (from a 58-case edge-case sweep and a replay of a real 22Rv1 DIA run)

- Inputs that used to finish as `state: ok` with nothing to show now say what's
  wrong: no measured values at all (`NO_QUANTITIES`), a single sample
  (`ONE_SAMPLE`), every sample left out or filtered away (`NOTHING_LEFT`), and
  an isoDTB ratio test with one replicate (`SMALL_GROUP` now applies to every
  method).
- An isoDTB table without probe-labelled peptides or ratio columns is reported
  as a data problem (`UNUSABLE_TABLE`, naming the probe mass) instead of "the
  read step crashed".
- A few non-numeric cells in a DIA-NN run column no longer drop the whole run
  when there's no manifest; they count as missing, with a note.
- One invalid analysis setting (e.g. `min_valid: 1`) no longer throws away
  every other setting for the run; only that key falls back to its default, and
  the note names it. `config.yaml` / `experiment.yaml` validation stays strict.

### Fixed

- `.REJECTED.txt` notes are written whole (temp file + rename), so a person or
  the app never opens one that exists but is still empty. Windows CI caught the
  gap on Python 3.14.

### Added

- Added an `.obvious` onboarding contract — agent guidance (`.obvious/obvious.md`),
  codebase map, repo config, and a local-dev skill — generated from a verified
  dev stack (ruff clean, 350 passed / 23 skipped, testbed end-to-end with the
  fake FragPipe). (#1)
- `runners/isodtb.py`: the isoDTB site-merge runner entry, a thin adapter over
  the existing port in `downstream/isodtb.py`, with golden tests. (#9)
- jsdom test harness for the report front end, run in CI on both OSes. (#8)
- `docs/FIRST_REAL_RUN.md`: the runbook for the first real FragPipe run on the
  PC (isoDTB, then DIA). (#31)

### Changed

- Dev installs now pin `pip>=26.2` (`deploy/dev_install.ps1` and the `dev`
  extra). (#3)
- CI tests Python 3.14 (what the lab PC runs) and 3.12 (the exe build) on
  Windows, and 3.14 and 3.11 (the floor) on Linux; previously only 3.12. The
  test job has a 15-minute timeout. (#21)
- Agents no longer merge their own PRs: the automerge workflow (#10, #11) is
  removed, `.obvious/config.yml` requires a human merge, and `.github/CODEOWNERS`
  requests owner review on every PR (D31).
- Naming: replicate and fraction tails are bounded to 1–999, and a digit run
  longer than three is never read as one, so a date-shaped tail can't become a
  replicate or fraction. (#32, #41)
- Naming: the six-digit `MMDDYY` date only matches when its year is within the
  last 25 years through next year, so run IDs like `113056` aren't read as
  dates. (#35, #41)

### Fixed

- Intake hash-verifies cross-volume copies (per-file SHA-256 over source and
  destination) before deleting the source, so a same-size corrupted copy can no
  longer silently destroy the original experiment. (#2)
- Post-move intake failures now file a minimal queued record at the destination:
  the folder is already moved, so the reconciliation sweeps re-adopt it as a job
  instead of the experiment being silently lost from all tracking. (#4)
- Cross-volume source cleanup retries transient Windows locks
  (antivirus/indexer) with bounded backoff; a lock that survives the retries
  writes a truthful `.REJECTED.txt` at the source — the filed copy is complete
  and verified, so keep it and delete the partial inbox folder — instead of the
  partial folder being re-offered and filed as an incomplete experiment. (#4)
- Desktop-shortcut creation passes the PowerShell command as base64
  `-EncodedCommand` (UTF-16LE), so install paths containing apostrophes no
  longer break shortcut creation. (#3)
- `experiment.yaml` override path fields (`user:`, `files.*.experiment:`) are
  validated before any path join: path-like values are refused (surfaced as the
  existing inbox `.REJECTED.txt` note) instead of being able to file data
  outside `users_root`. (#5)
- Installer execution requires a verified SHA-256 digest for the exact version:
  digest-less releases are never downloaded or installed, and a Downloads-found
  installer is verified against the release whose version it claims before it
  is run. (#6)
- A cross-volume copy that fails hash verification now removes its own partial
  destination (the source is untouched), so re-filing isn't wedged by
  "destination already exists"; if removal fails, the rejection names the
  leftover. (#34) The same cleanup now runs when the copy itself dies
  partway (disk full, a locked file), which used to leave a half-copy that
  got the retry rejected as "destination already exists".
- Drops with `.raw` files both at the top level and in `raw/` are rejected
  instead of silently filing a manifest of only one set; the app's Inbox view
  and naming window show the reason instead of a Tk traceback. (#36, #42)
- A raw file that vanishes (or can't be read) while a search is being prepared
  fails the job with a clear reason instead of leaving it queued forever. (#39)
- A cancel delivered while a search is starting is no longer discarded. (#38)
- FragPipe steps that die with a negative exit code (killed by a signal) are
  flagged as failed. (#27)
- A job missing from the ledger when its attempt starts fails visibly
  (`LedgerError`) instead of crashing the worker; ledger rebuild/adoption log
  every status file they skip. (#37)
- Tests: Windows CI hangs in the Tk dialog tests fixed (deterministic dialog
  drive, leak-proof teardown, bounded Tk root retry); edge-behavior tests
  pinned for ledger, watcher, worker, postprocess. (#12, #23, #25, #26, #28,
  #29, #30, #33, #40, #43)
- Tests: the two "raw vanishes during prepare" worker tests no longer depend on
  how `Path.is_file()` is implemented, so they pass on Python 3.14 as well as
  CI's 3.12. A testbed test that faked Windows by patching `os.name` globally
  crashed the whole suite on Python 3.11; it now stubs the module instead.
