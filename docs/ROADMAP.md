# Roadmap

Phases are ordered so that each one is independently useful and testable
without the next. Phase 1 can be developed and unit-tested entirely on a Mac
with fake folders; nothing before Phase 2 touches FragPipe.

## Phase 0 — Plan ✅ (this commit)

- Repo, docs, package skeleton, reference material organised.
- Fixed inventory script.

**Exit:** naming convention reviewed by 2–3 lab members; fixed inventory re-run
on the PC and results added to `reference/pc-inventory/`.

## Phase 1 — Watcher + intake (no FragPipe) ✅ code complete, not yet deployed

Build, in this order, each with tests:

1. `naming.py` — pure parsers for folder names and raw filenames, with the
   rejection reasons spelled out. Table-driven tests using the real names from
   the inventory (both the ones that should pass and the ones that should fail).
2. `config.py` — load/validate `config.yaml`; friendly errors for missing paths.
3. `watcher.py` — inbox polling with tree-fingerprint stability. Test with a
   thread that slowly writes files.
4. `ledger.py` — SQLite schema + transitions + startup recovery.
5. `intake.py` — validate → move → `ionomos.json` → ledger. Rejection writes
   `.REJECTED.txt`.
6. `cli.py` — `ionomos run`, `ionomos status`, `ionomos dry-run <folder>`
   (parse and print what *would* happen, move nothing).
7. `run_ionomos.bat` + Task Scheduler instructions.

Done 2026-09-15: `naming` (keyword/glued-initials/date formats/method from
file names), `config`, `watcher` (note-aware retry, backoff), `ledger`,
`intake` (experiment.yaml overrides, Windows lock retry), `resolve` (tkinter
window), `testbed`, `cli` (run / check / status / dry-run / retry / testbed),
`app` (setup wizard / control panel with a Testbed tab), `configio`,
`service`, PyInstaller exe (`Ionomos.exe` + `ionomos-cli.exe`), 149 tests incl.
real-GUI and end-to-end, `install.ps1` fallback.
2026-09-16: dev install on the PC (`deploy/dev_install.ps1`, `ionomos update`,
"Update from GitHub" in the app), `ionomos diagnose` / "Copy diagnostics",
GitHub Actions (tests on Linux+Windows; exe built on `v*` tags). See DEV_LOOP.md.
Remaining for exit: the week of real drops on the PC.

**Exit:** on the PC, dropping a correctly named folder of raws lands it in the
right user directory with `ionomos.json` saying `queued` and nothing else
happens. A bad name gets a `.REJECTED.txt`. Runs for a week without falling over.

## Phase 2 — isoDTB end-to-end

2026-09-22: automatic FragPipe runs built (`worker.py`, `fragpipe.py`,
`postprocess.py` hook; DONE/FAILED notes; retry; hold-until-configured;
stop/timeout kill the process tree; re-runs keep old output). Tested against
the testbed's fake FragPipe on macOS + Windows CI. **Not yet run against the
real FragPipe 24.0 on the PC** — first real test: pin `isoDTB.workflow`, drop a
small isoDTB folder, compare with a GUI run. Still to build: step 5 below.

1. Pin the lab's `isoDTB.workflow` (+ FASTA) into `C:\Fragpipe_Auto\workflows\`.
2. `manifest.py` — `.fp-manifest` from parsed raws.
3. `runners/fragpipe.py` — adapt prior-work runner; confirm launcher path on 24.0.
4. `worker.py` — pull queued job, run, record.
5. `runners/isodtb.py` — port the site-merge R script; golden-file test vs an
   existing lab output.
6. `DONE.txt` / `FAILED.txt` + `ionomos retry`.

**Exit:** one real isoDTB experiment processed with no manual steps; the
`_sites.tsv` matches the R output on the same input.

2026-09-23 (0.3.0): failsafes (single instance, supervised loops, crash
reports, heartbeats, graceful stop, ledger backup/rebuild/orphan adoption,
disk-space hold, never-loop intake), richer diagnostics (+ .zip bundle),
FragPipe friendliness (plain-English failure causes, live step progress,
cancel, pause/resume, install check, workflow import, FASTA decoy check),
Jobs tab, stress tester. See ARCHITECTURE.md "Failsafes".

2026-09-23 (0.4.0): downstream analysis built — R ports (byte-identical),
limma-style statistics (validated vs limma), volcano plots, self-contained
HTML report, `ionomos analyze`, app Analysis tab + Re-run analysis; setup
checklist tab + Auto-setup + `ionomos init`, unsafe layouts refused. Next:
run it on real lab output and compare with the lab's current analyses.

2026-09-23 (0.5.0): renamed LabWatch → Ionomos with full backward
compatibility (`names.py`); Windows installer (install / upgrade / uninstall,
retires LabWatch) tested in CI; in-app update from a downloaded Setup; Report
a problem → one zip on the Desktop; build stamp in every report; app log;
self-expiring detailed logging.

2026-09-23 (0.5.1), from the first real install report: Find FragPipe knows the
23/24 installer layout (C:/FragPipe/FragPipe-24.0/bin/FragPipe-24.0.exe) and never
picks a copy under a path with spaces; settings saved in the program folder move
next to the data; FragPipe copies / FASTA folders / "New folder" in the users
folder aren't treated as people; the checklist refreshes after every save.
Open: confirm FragPipe-24.0.exe runs headless with console output on the first
real search (a fragpipe.bat beside it is preferred if FragPipe ships one).

## Phase 3 — DIA, then TMT

- DIA: pin workflow, `data_type: DIA`, sort out DIA-NN version/`--config-diann`.
  No post-proc.
- TMT: `experiment.yaml` `tmt:` block → `annotation.txt`; confirm how headless
  24.0 finds it; port the annotation R script; resolve the "Peak Picking &
  zero Samples" pre-step question.

## Phase 4 — Operations & niceties

- Log rotation, disk-space check before accepting a job (C: has 99 GB free;
  refuse if < 2× raw size), email/Slack/Teams notify on done/failed.
- **Data presentation**: volcano plot + summary at the end of each run —
  either an HTML report (plotly, no server) or a small Shiny/Streamlit app.
  FragPipe Analyst ships R code that can be reused for the stats.
- `ionomos status` as a tiny local web page if people ask.
- Auto-archive finished experiments to `D:\<user>\` after N days.
- Optional: auto-pull from `C:\Proteomics_File_Sharing` (reversing D3) once
  the convention is trusted.

## Open questions (need a human)

Collected from the other docs; resolve before/during Phase 1.

**Lab process**
- [ ] Confirm the naming convention with users (NAMING_CONVENTION.md); collect initials → user-folder aliases for config.
- [ ] Trello board has a naming discussion — reconcile with NAMING_CONVENTION.md.
- [ ] Is `USER` == folder under `C:\Fragpipe_General\`? Who has multiple folders?
- [ ] Is plain `DDA` a real method in the lab or only isoDTB/TMT/DIA?
- [ ] Which DIA workflow is used, and what happens after a DIA run?
- [ ] What is the TMT "Peak Picking & zero Samples" pre-step (which app)? Still needed with FragPipe 24?
- [ ] Where are FASTA files kept, which ones, how often updated?

**Machine**
- [ ] Exact FragPipe 24.0 launcher path and whether `--headless` works on it as installed.
- [ ] Direction/type of the `Proteomics_File_Sharing` share.
- [ ] Sleep/power policy and whether Task Scheduler can run at logon for the shared account.
- [ ] Should results go to C: (fast, 99 GB free) or D: (slow USB, 14 TB free)? Proposal:
      run on C:, archive to D: (Phase 4).
- [ ] Install git on the PC, or deploy via zip/wheel?

**Software**
- [ ] How does FragPipe 24.0 headless locate the TMT `annotation.txt`?
- [ ] Does bundled DIA-NN suffice or is `--config-diann` needed for 2.3.2?
