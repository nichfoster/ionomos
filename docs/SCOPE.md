# Scope

## Problem

Every FragPipe analysis in the lab is run by hand: someone copies raw files off
the Eclipse PC, opens the FragPipe GUI on the proteomics PC, loads a workflow,
sets experiment/replicate for each file, picks an output folder, waits 30–60
min, then opens R and edits paths in a script to post-process the result. Each
step is a chance to mis-set a replicate, pick the wrong FASTA, write into a
non-empty folder, or forget the R step. Results end up in inconsistently named
folders on `C:\Fragpipe_General\<user>\` and `D:\<user>\`.

## Goal

A user should be able to:

1. Name a folder according to a short convention,
2. put their `.raw` files in it,
3. drag it into one inbox folder on the proteomics PC,

and get back, without touching anything else, a completed FragPipe run in
their own working directory with the lab's standard post-processing applied,
plus a clear status/log if anything went wrong.

## In scope (v1)

- **Watcher**: detect a newly dropped experiment folder in the inbox, wait
  until the copy is complete, validate it, and move it to
  `C:\Fragpipe_General\<user>\<experiment>\`.
- **Naming convention + manifest**: parse user, method, experiment ID, and
  replicate/fraction structure from the folder and raw-file names; optional
  `experiment.yaml` for what names can't express (TMT channel annotation,
  FASTA override, etc.).
- **Job records**: a `ionomos.json` in each experiment folder and a SQLite
  ledger, so status is always inspectable and jobs survive a restart.
- **FragPipe headless runs** for the three lab methods, in this order of
  delivery: **isoDTB → DIA → TMT**. One job at a time.
- **Post-processing** equivalent to the two existing R scripts, ported to
  Python (R is not installed on the PC):
  - isoDTB: merge modified peptides to labelled sites.
  - TMT: generate the corrected `experimental_annotation.tsv` for FragPipe Analyst.
- **Operational basics**: runs at login via Task Scheduler, logs to file,
  dry-run mode, a `ionomos status` CLI.

## Out of scope (v1)

- Anything on the Eclipse PC. The instrument PC is untouched; users still copy
  raw files to the shared folder themselves.
- Automatically pulling from the shared folder (`C:\Proteomics_File_Sharing`).
  Users drag into the inbox deliberately — that drag is the "submit" action.
  (Could be revisited; see ROADMAP.)
- A web UI / dashboard. Status is the JSON file + CLI for now.
- Proteome Discoverer, Spectronaut, Skyline.
- QC trending (that's `reference/prior-work/proteomics-qc-pkg`, a separate concern).
- Multi-user permissions. The PC is a single shared Windows account in a WORKGROUP.
- Parallel FragPipe runs. The machine has 32 threads / 64 GB; one run at a time
  is intentional.

## Users

- **Lab members** (~15 people, judging by `D:\` folders): drop folders, read
  results. They should never need to open a terminal.
- **Maintainer** (you): edits `config.yaml`, updates workflow files, reads logs.

## Constraints from the target machine

See [PROTEOMICS_PC.md](PROTEOMICS_PC.md) for the full inventory. The ones
that shape the design:

- Windows 11 Pro for Workstations, single shared local account, no domain.
- FragPipe 24.0 at `C:\FragPipe\FragPipe-24.0\` with bundled JRE. Use its
  headless CLI; never automate the GUI.
- **No R.** The R scripts get ported to Python.
- Python 3.14 (user install) — `python` on PATH is the Microsoft Store stub, so
  the launcher must use `py -3.14` or an absolute interpreter path.
- FragPipe requires **no spaces in any input/output path**. The inbox, user
  dirs, and experiment names must all be space-free.
- C: has ~99 GB free; D: (18.6 TB USB HDD) has 14 TB free. Raw files for one
  isoDTB run (3×7 fractions) are ~20 GB before mzML conversion. Where results
  live long-term matters — see DECISIONS.

## Success criteria for v1

- An isoDTB experiment dropped by a lab member is fully processed (FragPipe +
  site-merge table) with zero maintainer intervention.
- A badly named or incomplete folder is rejected with a human-readable reason
  written next to it, and nothing else happens.
- Restarting the PC mid-run does not lose or duplicate the job.
