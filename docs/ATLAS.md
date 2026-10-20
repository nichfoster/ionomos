# The hand-off to the lab's cysteine atlas

The lab keeps a **cysteine atlas**: one database of every cysteine site the lab's isoDTB experiments have
seen (`%USERPROFILE%\nomura-data\atlas\atlas.duckdb`, built by the Nomura-Lab repository's `Lab Informatics\tools`).
FragPipe isoDTB runs reach it through the lab Dropbox: `harvest_fragpipe.ps1` copies a run's result files into
`Nicholas Foster Data\fragpipe-runs`, `ingest_fragpipe.py` turns them into evidence rows, and
`build_site_atlas.py` rebuilds the atlas. Until 2026-10-19 a person ran that by hand.

Ionomos runs the lab's FragPipe searches, so it is the natural sender: when an isoDTB search is done, it copies the
run into the same Dropbox folder, the same way, and says so with a small record the atlas side reads. The atlas
publishes a site lookup back, and the isoDTB report shows it beside each site. Both sides implement one contract,
below, word for word; the Nomura-Lab side is built in that repository.

Decisions: D120 (the contract and the settings), D121 (when a job is handed off), D122 (how files are copied),
D123 (the command line), D124 (atlas context in the report), D125 (the app). Code: `ionomos/src/ionomos/atlas.py`,
`ionomos/src/ionomos/downstream/atlasctx.py`.

## The contract: "ionomos-atlas-handoff/1"

Both sides implement exactly this.

1. **Destination.** `<dest>` is configured as `atlas.handoff_dir`. It is normally the lab Dropbox folder `...\Nicholas Foster Data\fragpipe-runs`, the same folder `harvest_fragpipe.ps1` copies into.
   - There is no default, because that path has spaces and CLAUDE.md forbids default paths with spaces.
   - The app may offer a detected Dropbox folder as a suggestion the person confirms.
   - The feature is off unless `atlas.enabled: true` is set.
2. **What is copied.** For a done job whose method is in `atlas.methods` (default `[isoDTB]`), copy the FragPipe run folder's result files using the SAME selection rule as `harvest_fragpipe.ps1`. Read the script and `Lab Informatics/tools/HARVEST-README.md` "What it takes and what it leaves" in C:\Users\swimn\Desktop\Nomura-Lab, read-only.
   - From the top folder: `*.workflow`, `*.fp-manifest`, `*.job`, `*.tsv`, `*.txt`, `*.csv`, `*.log`, `*.fas`, `*.params`, `*.json`, `*.xml`.
   - From each replicate subfolder: `psm.tsv`, `peptide.tsv`, `protein.tsv` and logs.
   - Never raw data, `.pepXML`, `.pin`, `.prot.xml`, speclibs, or any file over 500 MB.
3. **Layout and copying.**
   - Files go to `<dest>\<DRIVE LETTER>\<original path without the drive>\`, exactly as harvest's `Get-RelativeDestination`, so `ingest_fragpipe.local_run_dir` finds them.
   - Every copy is verified by SHA-256.
   - An existing file with the same size and hash is skipped.
   - A file that exists with different content is a conflict: nothing is overwritten, the hand-off stops, and an attention item is raised.
4. **Completion record.** Only after every file is verified:
   - Write `<run dest>\ionomos-handoff.json`: contract `ionomos-atlas-handoff/1`; every file with its relative path, size and SHA-256; and the job's summary (job number, user, experiment, method, FragPipe version, workflow, FASTA, raw files, replicate plan, Ionomos version).
   - Then write `<dest>\_harvest-logs\runs-ionomos-<YYYYMMDD-HHMMSS>-job<N>.csv` under a temporary name and rename it, so its presence means the copy is complete. It is UTF-8 with BOM, as PowerShell's Export-Csv writes.
   - The CSV has one row. First come harvest's runs-CSV columns in harvest's order: Run, Owner, RunName, LastModified, FragPipeVersion, ProbeClass, Database, Enzyme, VarMods, HasWorkflow, HasManifest, PsmFiles, RawFiles, RawMissing, Experiments, RawPaths, FilesSelected, MBSelected, FilesTooLarge, Destination.
   - Then: IonomosJob, IonomosUser, IonomosExperiment, IonomosMethod, IonomosVersion, HandoffSha256 (the SHA-256 of `ionomos-handoff.json`).
   - Run = the FragPipe output folder's absolute Windows path on the PC. Owner = the Ionomos user folder name. ProbeClass = `isoDTB`.
5. **Re-sending.** Re-sending a job (Retry or re-run) writes a new record. The atlas side keys a run by Run + HandoffSha256 and never double-counts.
6. **Return path.** The atlas side publishes `<dest>\_atlas\atlas_sites_lookup.tsv` and `atlas_sites_lookup.json`.
   - TSV columns: accession, site (1-based position of the C in the canonical UniProt sequence), tier (high/medium/low by Dan's spectra rule of 2026-10-14), experiments, spectra, qc_suspect (0/1). Human only, sorted by accession then site.
   - JSON: contract `ionomos-atlas-lookup/1`, atlas_sha256, built, rule, sources, set, rows, quotable (bool).
   - Ionomos reads it read-only and only if it is present. A malformed file is ignored with a note.

### What the contract leaves open, as Ionomos does it

- **The selection is the script's, not the summary in item 2.** `harvest_fragpipe.ps1`'s `$RootAllow` also takes
  `*.fasta`, `*.yml`, `*.yaml` and `*.manifest` from the top folder, and its `$ReplicateAllow` is `psm.tsv`,
  `peptide.tsv`, `protein.tsv`, `*.log`, `log_*.txt`. `atlas.py` copies the script's constants (`ROOT_ALLOW`,
  `REPLICATE_ALLOW`, `NEVER_COPY`, `SKIP_DIR_NAMES`) in its order. Case is ignored, as PowerShell's `-like` ignores
  it. A replicate subfolder is one level down; `MSBooster\`, `tmp\` and the script's other skipped names are not
  entered, and a junction or link is never followed. "Over 500 MB" is the script's: more than 500 × 1024 × 1024
  bytes. No `ion.tsv` (harvest takes it only with `-IncludeIonTsv`).
- **Run** is `os.path.abspath` of the job's FragPipe output folder (`ionomos.json` `run.workdir`, else
  `<experiment>\fragpipe`), with backslashes and no `\\?\`. A UNC path lands under `<dest>\<server>_<share>\`,
  as harvest's does.
- **`ionomos-handoff.json`** is UTF-8 without a BOM, indented by two:

  ```json
  {
    "contract": "ionomos-atlas-handoff/1",
    "run": "C:\\Fragpipe_General\\Nicholas\\20261019_isoDTB_x\\fragpipe",
    "files": [{"path": "exp_1/psm.tsv", "size": 123456, "sha256": "<64 lowercase hex>"}],
    "job": {"number": 12, "user": "Nicholas", "experiment": "20261019_isoDTB_x", "inbox_name": "…",
            "method": "isoDTB", "fragpipe_version": "24.0", "workflow": "…\\isoDTB.workflow", "fasta": "…",
            "raw_files": ["…\\a.raw"], "replicate_plan": [{"raw": "…", "experiment": "…", "bioreplicate": "1",
            "data_type": "DDA"}], "search_finished_at": "2026-10-19T14:02:11-07:00",
            "probe_class_by_masses": "isoDTB", "ionomos_version": "0.18.1"}
  }
  ```

  `files[].path` is relative to the run folder, with `/`, sorted; the record is not in its own list.
  `replicate_plan` is the rows of the run's `fragpipe-files.fp-manifest`. `probe_class_by_masses` is harvest's
  reading of the workflow's modification masses, for information; the CSV says `isoDTB` because only FragPipe
  isoDTB methods may be in `atlas.methods`. The record holds no send time, so sending the same files and job again
  gives the same bytes and the same HandoffSha256: nothing new for the atlas to count.
- **The runs CSV** is written as PowerShell 5.1's `Export-Csv -NoTypeInformation -Encoding UTF8` writes: a BOM,
  every field in double quotes, CRLF. Values are rendered as harvest renders them: `True` / `False`,
  `MBSelected` as `[math]::Round(MiB, 1)` (`12.3`, `12`, `0`), `LastModified` as the local time of the newest
  file in the run folder (`yyyy-MM-ddTHH:mm:ss`), `Experiments` and `RawPaths` joined with ` | `.
  `FragPipeVersion` is the workflow's `# FragPipe (x)` line, else the run fingerprint's. The time in the name is
  local; a second send in the same second takes the next second's name.
- **Only one file at the destination is ever replaced**: `ionomos-handoff.json`, and only when the job's summary
  changed (Ionomos was updated between two sends). A runs CSV whose HandoffSha256 no longer matches the record was
  superseded by a newer CSV of the same Run.
- **SHA-256 is lowercase hex** (compare without case against PowerShell's `Get-FileHash`).

## The flow on the lab PC

```
worker            job done (FragPipe exit 0, the analysis finished, status=done)
  │               Worker.run_once → _atlas_queue: config.yaml read again; if atlas.enabled and the
  │               method is in atlas.methods → ionomos.json  "atlas": {"state": "pending", …}
  ▼               (a few bytes; nothing is copied here, so the next search starts at once)
atlas thread      HandoffLoop, in the watcher (cli.cmd_run), every 30 s: each done job of atlas.methods whose
  │               hand-off is pending, or failed and due → atlas.send
  ▼
send              lock <log_dir>\atlas-send-job<N>.lock (one process per job at a time)
                  select the files (harvest's rule) → compare with what is at the destination:
                    same size + SHA-256 → skipped; different → CONFLICT: nothing is copied, stop
                  copy each missing file → <name>.partial (hashed while copying) → read back, compare
                    → rename to <name> (never over a file) → keep the source's modified time
                  write <run dest>\ionomos-handoff.json
                  write _harvest-logs\runs-ionomos-<time>-job<N>.csv.partial → rename: complete
                  ionomos.json "atlas": state sent / failed / conflict / gave_up, with the reason
                  attention item atlas_handoff_failed on failure; closed when it is sent
```

| State (`ionomos.json` `atlas.state`) | Meaning | What happens next |
|---|---|---|
| (no `atlas` block) | never asked for: the job was done before the hand-off was turned on, or it is not an atlas method | only by hand: **Send to atlas** (Jobs tab) or `ionomos atlas send <job>` |
| `pending` | the worker queued it | the atlas thread sends it within 30 seconds |
| `sending` | a process holds the job's lock and is copying | a lock older than 6 hours (its process died) is taken over |
| `sent` | every file verified, record and CSV written | nothing; a manual send writes a new record (no double count) |
| `failed` | it could not finish (Dropbox offline, the folder missing, a copy that did not verify) | tried again by itself after `atlas.retry_minutes`, doubling each time, at most 12 hours apart |
| `gave_up` | it failed `atlas.max_tries` times in a row | waits for a person: fix the cause, then **Send to atlas** |
| `conflict` | a different file is already at the destination | waits for a person (below) |

Older searches are not sent when the hand-off is turned on: the atlas side already has what `harvest_fragpipe.ps1`
copied, and sending a year of runs at once is a decision for a person. **Send to atlas** sends any done job.

**A conflict** means the Dropbox folder already holds a different copy of one of the run's files: a copy someone
edited, or an earlier search harvested from the same folder. Ionomos never overwrites it. The attention item names
each file that differs. Ask the person who keeps the atlas which copy is right; they move the old one aside in
Dropbox, then **Send to atlas**.

**Never touched**: the experiment folder. The hand-off only reads it; the one write is the `atlas` block of its
`ionomos.json`. Nothing at the destination is deleted, and nothing outside `<dest>` is written except that
`ionomos.json` block and the lock file in Ionomos' log folder.

## Settings

```yaml
atlas:   # hand finished isoDTB searches to the lab's cysteine atlas. Off by default
  enabled: false
  handoff_dir: ''      # the lab Dropbox folder ...\Nicholas Foster Data\fragpipe-runs
  methods: [isoDTB]    # FragPipe isoDTB methods whose searches are handed off
  max_tries: 10        # a hand-off that keeps failing (Dropbox offline) stops after this many tries
  retry_minutes: 15    # the first wait before trying again; it doubles, up to 12 hours
```

- `handoff_dir` may have spaces: FragPipe never sees it. It must be a full path (`C:\…` or `\\server\share\…`).
  When the folder is not there yet but its parent is (Dropbox has `Nicholas Foster Data` but no `fragpipe-runs`),
  the first hand-off makes it, as harvest does; when the parent is missing too, the hand-off waits (Dropbox not
  running, not signed in, or the folder not synced to this PC).
- `methods` may name only methods searched by FragPipe whose kind is isoDTB (`isoDTB`, or a method with
  `naming.methods.<key>.like: isoDTB`); while the hand-off is on, anything else stops config.yaml from loading with
  the line named, because the runs CSV says `ProbeClass` isoDTB and the atlas would count it. While it is off they
  are not checked, so a lab without an isoDTB method loads (every Save writes the default `[isoDTB]`).
- The watcher reads `atlas:` again on every pass, so turning the hand-off on or off in the app counts without a
  restart.

## The command line

`ionomos atlas send JOB_ID` hands one finished job over now, whatever its state (exit 0 sent; 1 not sent, with why
and every file that differs; 2 the hand-off is off or has no folder). `ionomos atlas status [JOB_ID] [--json]`
lists each finished atlas job and where its hand-off stands. `ionomos atlas check [--no-write]` checks that the
hand-off is on, the folder is set, there and writable (it makes and removes one small test file), and that the
atlas's site lookup is there and reads; exit 1 when something is wrong. Exit codes follow D102.

## In the app

- **Folders tab → Cysteine atlas**: "Hand finished isoDTB searches to the cysteine atlas" (`atlas.enabled`), the
  destination folder (spaces allowed) with **Browse…**, and the Dropbox folder Ionomos found (as
  `harvest_fragpipe.ps1` finds it: `%LOCALAPPDATA%\Dropbox\info.json`, then `Nicholas Foster Data` up to two
  levels down), offered with **Use**, never filled in by itself. **Help** opens `faq.atlas`. **Save** writes
  `enabled` and `handoff_dir` and keeps the other `atlas:` keys.
- **Jobs tab**: an **atlas** column with each isoDTB job's hand-off state (`atlas.describe`), shown while the
  hand-off is on or a listed job has a hand-off; the detail pane says the same, with the first files that differ
  after a conflict. **Send to atlas** sends the selected done job now; a job not done, the hand-off off or a folder
  typed but not saved are explained instead.
- **Setup checklist**: "Cysteine atlas hand-off", only while the hand-off is on, from `atlas.check` (to do: no
  folder; warning: the folder can't be reached or the lookup is malformed; problem: the settings don't load), with
  **Fix** to the folder field.
- **Attention list**: a failed hand-off is an item without a pop-up window; its window (opened from the list) has
  **Send to atlas** and **More help** ([help](../ionomos/src/ionomos/help/troubleshooting.md)).

Sending runs off the window's thread (`App.run_bg`), and so does the Dropbox search (once, when the Folders tab is
first shown). The atlas column reads each done atlas job's `ionomos.json` on a background thread, again only when
the file changed.

## In the report

When the atlas has published its lookup (`<dest>\_atlas\`), the isoDTB report's site table shows, for each human
cysteine site, the atlas tier (high / medium / low), how many atlas experiments saw it, and a "new to the atlas"
mark for a site the atlas has never seen. One line under the table says these are the lab's internal numbers, when
the atlas was built and by which rule, and whether the set may be quoted (`quotable` in the JSON). An isoform, a
non-UniProt protein or a non-human protein gets no atlas context and is never called new. With no lookup, nothing
in the report changes; a malformed lookup is ignored and the report says so in its notes.

## What the lab must confirm

See [ROADMAP.md](ROADMAP.md) ("The cysteine atlas hand-off"): whether the lab PC's Dropbox client is signed in to
the lab Dropbox and syncs `Nicholas Foster Data`, whether the Windows account the watcher runs as can write there,
and who decides a conflict.
