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
**2026-09-15.** `labwatch.json` + `DONE.txt`/`FAILED.txt` in the experiment
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

### D11 — Repo layout: monorepo, `labwatch` is the first package
**2026-09-15.** `lab-informatics` is the umbrella; `labwatch/` is a normal
installable Python package with its own `pyproject.toml`. The QC pipeline and
any future tools sit beside it rather than inside it. Reference material and
lab artefacts live under `reference/`, never inside a package.
