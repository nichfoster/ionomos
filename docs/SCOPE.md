# Scope

What Ionomos does and does not do, as of 2026-10-15 (version 0.18.1 plus the
unreleased library and TMT work). It started as one lab's plan (2026-09-15,
"Where it started" below); D36 (2026-09-30) widened it to other labs.
[VISION.md](VISION.md) says who it is for and the road to publishing.

## Problem

Two problems, one per half of the tool.

**On the instrument PC.** In the lab where Ionomos started, every FragPipe
analysis was run by hand: copy raw files off the instrument PC, open the
FragPipe GUI, load a workflow, set experiment and replicate for each file,
pick an output folder, wait 30–60 min, then open R and edit paths in a script
to post-process. Each step is a chance to mis-set a replicate, pick the wrong
or a stale FASTA, write into a non-empty folder, or forget the R step.
Results end up in inconsistently named folders. Labs with a Windows
instrument PC and no bioinformatician have no open tool for this: the open
pipelines need Linux, containers and a cluster (D36).

**After the search.** A protein table from any engine still needs filtering,
normalisation, imputation, statistics, QC and figures. The usual tools are an
R package, a web upload or a GUI session, and their results are hard to
check: which settings, which version, and does it agree with another
analysis of the same data?

## Goal

1. **A lab member** names a folder by a short convention, puts the raw files
   in it and drags it into one inbox. Without touching anything else they get
   back a finished search in their own directory, the lab's post-processing,
   a report they can read, and a clear status if anything went wrong.
2. **Anyone with a protein table** runs `pip install ionomos` and
   `ionomos analyze` and gets the same report, on Windows, macOS or Linux.
3. **Both can check the result**: against the R tools it ports, against
   another analysis, and against known truth (D60).

## In scope (today)

**The watcher** (Windows, the instrument-side PC):
- Detect a dropped experiment folder in the inbox, wait until the copy is
  complete, show it to a person when its names are unclear, and move it to
  `<users_root>\<user>\<experiment>\` (D1–D4, D12, D13, D34). Loose raw files
  get a folder (D23).
- Naming convention plus optional `experiment.yaml`; another lab's names are
  config (D9, D37, D54).
- Job records next to the data and a SQLite ledger that can be rebuilt from
  them; jobs survive a restart (D8, D17).
- Headless searches, one at a time: FragPipe for isoDTB, DIA and TMT and
  any method a lab adds in config, such as LFQ (D6, D7, D54, D59; TMT channel
  maps checked before the search, D91), DIA-NN
  (D39), MaxQuant (D50) and Sage (D51, D56). Each lab installs its own
  engines.
- The workflow and FASTA library: find the workflows and FASTAs on the PC,
  say what each is and what is wrong with it, pin one per method, download
  and prepare a UniProt FASTA, warn when it is stale (D89, D90;
  [LIBRARY.md](LIBRARY.md), [FASTA.md](FASTA.md)).
- The lab's R post-processing, ported byte-identical (isoDTB sites, TMT
  annotation; D5, D18).
- A Windows app (setup, control panel, analysis, notifications), an
  installer and one-button updates (D14, D22, D58, D67).
- Instrument QC trending of the lab's QC standard (D45, D55).
- A support bundle that is anonymised, saved and never sent (D63, D74).
- A local assistant that explains and proposes; it has not yet run with a
  real model (D49, D57, D75; [ASSISTANT.md](ASSISTANT.md)).

**The analysis** (any computer, `pip install ionomos`):
- Tables from FragPipe, DIA-NN, MaxQuant, Sage, Spectronaut, AlphaDIA,
  MSstats format and Proteome Discoverer, or any protein table (D33, D36;
  [ENGINES.md](ENGINES.md)).
- FragPipe-Analyst's analysis, ported and checked against FragPipeAnalystR
  (D24); designs with blocks, covariates and time courses (D42, D53, D77);
  DEqMS (D43); TMT across plexes (D48, D71); dose-response (D44); isoDTB
  sites and liganded cysteines (D52, D70); roles and competition experiments
  (D61, D88); phosphosites and kinase activity, opt-in (D79).
- One offline `report.html` with its own help, a sample scorecard and
  "How far to trust this" (D25, D35, D46, D60); figures for slides (D62,
  D68); an SDRF sample sheet out and in (D38, D47).
- `ionomos compare` and `ionomos benchmark` (D60, D66).

## Out of scope

| Item | Since | Why |
|---|---|---|
| Anything on the instrument PC itself | v1 | It stays untouched; users copy raw files off it |
| Pulling automatically from a shared folder | v1 | The drag into the inbox is the "submit" (D3); open in ROADMAP |
| A web server, accounts, cloud hosting | v1 (was "a web UI / dashboard") | One maintainer, data stays local (ROADMAP Phase 5 Risks). The report and the QC trend are static HTML files instead |
| Multi-user permissions | v1 | The PC is one shared Windows account |
| Parallel searches | v1 | One search at a time is intentional (D7) |
| Searching or quantifying spectra itself | 2026-09-30 | The engines do that; Ionomos runs and reads them (D36) |
| Bundling an engine or a database with a restrictive licence | 2026-09-30 | Each lab accepts its own (D36, D79) |
| Running Proteome Discoverer or Spectronaut | 2026-09-30 | No supported headless mode / commercial; their exports are read |
| Skyline as an engine | v1 | Read only through its MSstats export ([ENGINES.md](ENGINES.md)) |
| PEAKS, CHIMERYS, MSFragger outside FragPipe | 2026-09-30 | Skipped unless asked (ROADMAP 5B) |
| Sample tracking, billing, project management (a LIMS) | 2026-10-15 | Not the problem this solves ([VISION.md](VISION.md)) |
| Peptide-level differential analysis, mixed models | 2026-10-15 | MSstats, MS-DAP and limpa do these; limpa is on the roadmap (5C #4) |

## What moved

- **Into scope since v1:** Proteome Discoverer and Spectronaut (import only);
  QC trending (it was "a separate concern"; D45 made it part of the watcher);
  status beyond JSON and CLI (the app, D14, and the reports); other engines
  run by the watcher (DIA-NN, MaxQuant, Sage); the whole downstream analysis,
  not only the two R scripts (D24); other labs (D36); the analysis on macOS
  and Linux (D40); TMT moved from last to supported, with its own checks
  (D91).
- **Out of scope since v1:** nothing that was in v1 has been dropped.

## Users

- **Lab members** (about 15 in the first lab): drop folders and read
  reports. They should never need a terminal.
- **The maintainer** of a lab's PC: config, workflows, FASTAs, logs. Today one
  person, who also maintains Ionomos.
- **Anyone with a protein table**: installs from pip and runs one command.
- **Other labs and core facilities** running the watcher with their own names
  and engines; none yet (ROADMAP 5D).

See [VISION.md](VISION.md) for these in more detail.

## Constraints

From the first lab's PC ([PROTEOMICS_PC.md](PROTEOMICS_PC.md)); they still
shape the design, and other labs' PCs are assumed to be like it:

- Windows 11, a single shared local account, no domain.
- FragPipe (24.0 there) with its bundled JRE. Use the headless CLI; never
  automate the GUI (D6).
- **No R.** Everything R did is ported to Python (D5, D18).
- Python 3.14 as a user install there; `python` on PATH can be the
  Microsoft Store stub, so the launcher uses `py -3.14` or an absolute path.
  The package supports 3.11 and newer.
- FragPipe requires **no spaces in any input or output path**.
- Raw files are large (an isoDTB run of 3 × 7 fractions is about 20 GB), so
  where results live long-term matters; intake moves, never copies (D4).

For the analysis on any computer: Python 3.11+, one dependency (PyYAML), no
Tk, no network after the first enrichment download (D40).

## Success criteria

The v1 criteria (below) still hold for the watcher. For the published tool,
"done for v1.0" is defined in [VISION.md](VISION.md#what-done-for-v10-means).

## Where it started

The plan of 2026-09-15 (LabWatch, Phase 0), kept as written in substance.

**Goal (v1).** A lab member names a folder by a short convention, puts the
`.raw` files in it, drags it into one inbox on the proteomics PC, and gets
back a completed FragPipe run in their own working directory with the lab's
standard post-processing applied, plus a clear status or log if anything went
wrong.

**In scope (v1).**
- Watcher: detect a dropped folder, wait for the copy, validate it, move it
  to `C:\Fragpipe_General\<user>\<experiment>\`.
- Naming convention and manifest, with an optional `experiment.yaml` for what
  names can't express.
- Job records: a status file in each experiment folder and a SQLite ledger.
- Headless FragPipe for the three lab methods, in the order isoDTB → DIA →
  TMT, one job at a time.
- Post-processing equal to the two R scripts, ported to Python (isoDTB site
  merge; the TMT `experimental_annotation.tsv`).
- Runs at login via Task Scheduler, logs to file, a dry-run mode, a `status`
  command.

**Out of scope (v1).** The instrument PC; pulling from the shared folder; a
web UI or dashboard; Proteome Discoverer, Spectronaut, Skyline; QC trending;
multi-user permissions; parallel FragPipe runs.

**Success criteria (v1).**
- An isoDTB experiment dropped by a lab member is fully processed (FragPipe +
  site-merge table) with zero maintainer intervention.
- A badly named or incomplete folder is rejected with a human-readable reason
  written next to it, and nothing else happens.
- Restarting the PC mid-run does not lose or duplicate the job.

As of 2026-10-15 the second and third hold in tests and the stress testbed;
the first has not yet happened on the PC: no isoDTB search has run there under
Ionomos ([REAL_RUNS.md](REAL_RUNS.md)).
