# First real runs — isoDTB, then DIA

Every pipeline run so far — in CI, the test suite and the testbed — used a
fake FragPipe. This page is the runbook for the first real ones on the lab
PC: the exit tests for [ROADMAP.md](ROADMAP.md) Phase 1 (watcher + intake)
and Phase 2 (isoDTB end-to-end), plus a first look at Phase 3 (DIA). The
[README](../README.md) status line — "Phase 2 in progress — automatic
FragPipe searches built, awaiting the first real run on the PC" — is what
this page closes.

Work through it from the PC, logged in as the shared lab account. Installing
and day-to-day operation are [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md); what
each method needs is [WORKFLOWS.md](WORKFLOWS.md).

## Before you start

- [ ] **Latest release** (≥ 0.7.0): **Update to x.y.z** in the bottom bar, or *Run & Test → Check for updates*. The first real run should exercise the newest failure reporting and pop-ups, so update first.
- [ ] **✓ Setup all green**: tab 5 → **Save & Check**. A `!` on the FragPipe launcher or a method means those jobs will *wait* until it's fixed.
- [ ] **FragPipe install checked**: tab 6 → **Check FragPipe install** — MSFragger, IonQuant and DIA-NN found, decoys in each FASTA.
- [ ] **isoDTB workflow + FASTA pinned**: tab 3 → **Import workflow…** → pick the `fragpipe.workflow` inside a recent run that worked (its FASTA is copied in too). Details: [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md), A4.
- [ ] **Disk space**: at least twice the raw folder size free on C: — the 2026-09-15 inventory found ~99 GB free, and one 3×7 isoDTB drop is ~20 GB of raws before mzML conversion.
- [ ] **PC won't sleep**: Settings → System → Power → Sleep: Never (plugged in).
- [ ] **Watcher running**: tab 5 → **Start watcher**, with the startup task installed so it comes back after every logon.

One setting matters throughout: *Run FragPipe automatically* (tab 4
Advanced). **Off**, Ionomos only files experiments — the Phase 1 behaviour.
**On**, the full pipeline runs — Phase 2. Each run below says which it needs.

## Run 1 — isoDTB: intake, then the first real search

### Name the folder and the raws

Folder name: your initials and the `isoDTB` keyword, ideally
`YYYYMMDD_<initials>_<method>_<whatever>` — e.g.
`20260925_EJQ_isoDTB_EJQ-2-027_1uM-3h`. Raw files
`<sample>_<rep>_<fraction>.raw` (`EJQ_2_027_1_1.raw … _3_7.raw`), every
replicate with the same fraction set, at the top level or in a `raw\`
subfolder. The full rules (and what gets rejected) are in
[NAMING_CONVENTION.md](NAMING_CONVENTION.md).

Check a folder before dropping it — this moves nothing:

```powershell
ionomos-cli.exe dry-run "C:\path\to\folder"
```

### The Phase 1 week — intake only

Set *Run FragPipe automatically* **off** (tab 4 → **Save**). Over the next
week, drop real folders into `C:\Fragpipe_Auto\inbox\` as the lab works. Each
correctly named folder — ≥1 `.raw`, unchanged for 60 s — should land in
`C:\Fragpipe_General\<user>\<experiment>\` within ~70 s, with `ionomos.json`
saying `queued` and nothing else happening. A bad name gets a
`.REJECTED.txt` next to the folder (and the resolver window asks when the
app is open).

> **Exit:** on the PC, dropping a correctly named folder of raws lands it in the
> right user directory with `ionomos.json` saying `queued` and nothing else
> happens. A bad name gets a `.REJECTED.txt`. Runs for a week without falling over.

- [ ] Dropping a correctly named folder of raws lands it in the right user directory, with `ionomos.json` saying `queued` — and nothing else happens.
- [ ] A bad name gets a `.REJECTED.txt`.
- [ ] It runs for a week without falling over.

### The first real search

Set *Run FragPipe automatically* **on** (tab 4 → **Save**). Anything still
`queued` from the week starts processing — one job at a time, oldest first —
so tab 6 → **Pause searches** first if you'd rather pick. Then drop a
*small* real isoDTB folder (one replicate, 2–3 fractions) and watch tab 5:
*FragPipe: RUNNING job N* → *done*. A full 3×7 experiment takes ~30–60 min.

⚠ This first search also settles an open question ([ROADMAP.md](ROADMAP.md),
0.5.1): whether `FragPipe-24.0.exe` prints headless console output like the
old `.bat`. The live step line in the queue and the "a step failed inside
FragPipe" detection both read that console — if the run succeeds but
`ionomos_run\fragpipe_console.log` stays empty, note it and report back
(below).

### What done looks like

- `DONE.txt` next to the raws — the report path, the FragPipe output folder and per-comparison hit counts.
- `fragpipe\` populated; FragPipe's console at `ionomos_run\fragpipe_console.log`.
- `results\report.html` — the full analysis report.
- A bad name never got this far: `.REJECTED.txt`, or the resolver window.

> **Exit:** one real isoDTB experiment processed with no manual steps; the
> `_sites.tsv` matches the R output on the same input.

- [ ] One real isoDTB experiment processed with no manual steps.
- [ ] The `_sites.tsv` matches the R output on the same input — the procedure is in "The Phase 2 exit test" below.

## Run 2 — DIA: the first DIA search

⚠ DIA is ROADMAP Phase 3 territory and there is **no lab SOP yet** —
[WORKFLOWS.md](WORKFLOWS.md) says exactly that. Two things are unknown going
in, and this run is how they get answered:

- **Which workflow the lab uses.** The stock candidates are
  `DIA_SpecLib_Quant` and `DIA_DIA-Umpire_SpecLib_Quant`; ask the lab, and
  pin it via tab 3 → **Import workflow…** from a good past run. A missing
  workflow file only makes the job wait ("waiting: …"), so this is safe to
  get wrong once.
- **Which DIA-NN runs.** Try the bundled one first; if the headless run
  complains, set `fragpipe.config_diann` in `config.yaml` (DIA-NN 2.3.2 is
  installed on the PC).

Raw files are `<condition>_<biorep>.raw` — `DMSO_1.raw`, `Drug_1.raw` (top
level or `raw\`). What done looks like: `DONE.txt`; the DIA-NN tables in
`fragpipe\diann-output\` (`report.tsv`, `report.pg_matrix.tsv`); a report at
`results\report.html` — the statistics run protein-level, condition vs
control, on `report.pg_matrix.tsv`. Conditions are guessed from the file
names, so expect *decide* pop-ups (`UNMATCHED_RUNS`, `NO_CONTROL`) asking
you to confirm them.

What happens *after* a DIA run is itself an open question — the lab's
post-processing is unknown; ask what they do next (a FragPipe Analyst
upload?). TMT is the Phase 3 step after this and has open questions of its
own (see [ROADMAP.md](ROADMAP.md)).

- [ ] The search completes (`DONE.txt`, not `FAILED.txt`).
- [ ] `fragpipe\diann-output\report.pg_matrix.tsv` exists.
- [ ] `results\report.html` opens, with the conditions confirmed or sensibly guessed.
- [ ] The workflow the lab actually uses is pinned and noted for the repo (below).

## Verifying the results

- `results\report.html` — the doctor banner at the top lists the *decide* and *problem* items with their fixes; below it, volcano plots, the site/protein tables, enrichment and QC.
- `results\analysis.json` — the machine-readable summary: `state` (`ok` / `needs_input` / `failed`) and every comparison's up/down/tested counts.
- isoDTB: `results\<prefix>_sites.tsv`, one per sample prefix — the Phase 2 exit table.
- `results\fragpipe-analyst\` — `experiment_annotation.tsv` and `reproduce_in_R.R`, for reproducing the analysis in the lab's FragPipe-Analyst sessions. Comparing those against this report on a real experiment is a ROADMAP open item.
- **Re-runs without re-searching**: `ionomos-cli.exe analyze <folder> --test welch` (also `--control`, `--compare 'A vs B'`, `--log2fc`, `--alpha`), or tab 7 → **Analyse an experiment** / **Analyse a folder…**. Expect to tune here: the analysis defaults (imputation, control keywords, the LOW_SAMPLE threshold) are provisional until real data says otherwise.

## The Phase 2 exit test — `_sites.tsv` vs the R output

The Phase 2 exit sentence says the `_sites.tsv` must match the R output on
the same input. Concretely:

1. Let Ionomos finish, and keep both tables: FragPipe's
   `fragpipe\combined_modified_peptide_label_quant.tsv` (what the port
   reads) and Ionomos's `results\<prefix>_sites.tsv` (what it wrote).
2. Run the lab's R script
   `isoDTB_Fragpipe_merge-individual-peptides-to-Site.R` on the same
   `combined_modified_peptide_label_quant.tsv`.

⚠ R is not installed on the PC — the 2026-09-15 inventory found none, and
the lab's R scripts are run "somewhere else" today. Run the R half on
whatever machine that is (or install R + tidyverse if there is nowhere). The
script's three settings are hard-coded at the top and must be edited by hand
each run. Filled in for an example drop `20260925_EJQ_isoDTB_EJQ-2-027`
containing `EJQ_2_027_1_1.raw … _3_7.raw`:

```r
input_tsv      <- "C:/Fragpipe_General/EJQ/20260925_EJQ_isoDTB_EJQ-2-027/fragpipe/combined_modified_peptide_label_quant.tsv"
output_tsv     <- "C:/Fragpipe_General/EJQ/20260925_EJQ_isoDTB_EJQ-2-027/combined_modified_peptide_label_quant_output.tsv"
sample_prefix  <- "EJQ_2_027"
```

`sample_prefix` is the FragPipe experiment name — the shared prefix of the
raw file names; Ionomos names its table after it
(`results\EJQ_2_027_sites.tsv`).

3. Compare the R script's `…_output.tsv` with `results\<prefix>_sites.tsv`:

| Must match | Detail |
|---|---|
| Rows | keyed on Protein + ModifiedResidue + ResiduePositionInProtein — same rows (order may differ) |
| PeptideCount, ExamplePeptides | identical |
| Ratios | the per-replicate `Mean_<prefix>_<n> Log2 Ratio HL` columns and the overall `Mean_Log2_Ratio_HL`, equal to ≥10 decimal places |
| Blanks | NA in exactly the same cells |

**What counts as agreement:** identical rows and counts with ratios matching
to display precision — **not** byte equality. Ionomos reimplements R's
`mean()`; on the PC's x86 Windows that agrees to ~1e-15 relative rather than
bit-for-bit. (In the test suite the port is byte-identical against the
goldens in `ionomos/tests/golden/` — this real-data comparison replays that
check on real data, where it has never run before.)

⚠ One quirk is kept deliberately, in both implementations: the script counts
*every* letter before the label, so an N-terminal mod written `n[42.0106]`
shifts that site's position by one. Don't "fix" either side mid-comparison.

4. Record the outcome, and file the R output into `ionomos/tests/golden/` —
   [WORKFLOWS.md](WORKFLOWS.md) asks for exactly one R-script output per
   method to diff the Python port against.

## If something looks wrong

| When | Do |
|---|---|
| Nothing happened after a drop | Is the watcher running (tab 5 status line)? The folder needs ≥1 `.raw` and must be unchanged for 60 s. |
| `<name>.REJECTED.txt` in the inbox | Open it — it says why. Fix the folder (or add an `experiment.yaml`); it is retried automatically. Deleting the note also retries. |
| Job says "waiting: …" | A setup file is missing — the FragPipe launcher or that method's workflow/FASTA. Fix it; the job starts by itself. |
| Job FAILED | Tab 6 shows the most likely cause in plain English. `FAILED.txt` has the reason; `ionomos_run\fragpipe_console.log` has FragPipe's full output. Fix, then **Retry a failed job…** — the old output is kept as `fragpipe_previous_<time>\`. |
| Stopping or updating mid-search | Kills that FragPipe run; the job re-runs from the start when the watcher starts again. Nothing is lost — old output is never overwritten. |
| The PC is needed for something else | Tab 6 → **Pause searches** (the running search finishes; nothing new starts). |
| Something needs a person | A pop-up appears with the likely cause and fix; the bottom bar shows **⚠ N need attention**; `ionomos-cli.exe attention` lists them all. |
| Still stuck | **Report a problem…** (bottom right) → a zip lands on the Desktop → send it. The log itself is `C:\Fragpipe_Auto\logs\ionomos.log`. |

## After the runs: report back

These runs answer open questions the repo is explicitly waiting on:

- [ ] Did `FragPipe-24.0.exe` print headless console output? (The ROADMAP 0.5.1 open item — live progress and step-failure detection depend on it.)
- [ ] The R comparison result for the Phase 2 exit — and the R output itself, filed into `ionomos/tests/golden/`.
- [ ] Did the bundled DIA-NN work, or was `fragpipe.config_diann` needed?
- [ ] Which DIA workflow the lab actually uses, so it can be pinned and documented.
- [ ] How the pop-ups felt with real data, and what the LOW_SAMPLE threshold and control keywords should be (the ROADMAP 0.7.0 open items).
- [ ] The lab's default for imputation (Perseus vs none), once the report is compared with their own analyses (the ROADMAP 0.6.0 open item).

When reporting problems, **Copy diagnostics** (or
`ionomos-cli.exe diagnose --zip`) bundles the version, config, status and
the last 150 log lines into one file.
