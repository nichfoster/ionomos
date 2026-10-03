# The Mac ↔ PC loop (while Ionomos is being prototyped)

Code is written on the Mac (with Claude), run on the proteomics PC. GitHub
sits in the middle. Nothing is ever "uninstalled".

```
 Mac                          GitHub                        Proteomics PC
 ┌──────────────────┐  push   ┌──────────────┐   pull    ┌───────────────────────────┐
 │ edit + tests     │ ──────▶ │ public repo  │ ────────▶ │ C:\ionomos-src  (clone)  │
 │ (Claude)         │         │              │           │   └─ .venv  (editable)    │
 └──────────────────┘         └──────────────┘           │ Ionomos app ← runs from  │
        ▲                                                │   the clone directly      │
        │  paste "Copy diagnostics"                      └───────────────────────────┘
        └────────────────────────────────────────────────────────┘
```

**Editable install** is the trick: the PC's Python environment points at the
files in `C:\ionomos-src` instead of a copy. So `git pull` *is* the update.
Config (`C:\Fragpipe_Auto\config.yaml`), the ledger, logs and lab data live
outside the clone and are never touched.

## One-time, on the PC (5 min)

1. Download [`deploy/dev_install.ps1`](../deploy/dev_install.ps1) from GitHub
   (open the file → "Download raw file"), or copy it over. Put it anywhere.
2. Right-click PowerShell → Run, then:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\dev_install.ps1
   ```
   It installs git if missing (asks first), clones the repo to `C:\ionomos-src`
   (the repository is public; no sign-in needed), makes the Python environment, puts a **Ionomos** shortcut on the
   Desktop, and opens the app.
3. In the app: tab 1 → **Apply layout** → **Create all missing folders**, then
   tab 5 → **Save & Check**. Same as [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md) A3.

## Every day after that

| On the Mac | On the PC |
|---|---|
| Describe the problem to Claude (paste the diagnostics), it fixes + tests, then `git push` | Open Ionomos → tab 5 → the *Development install* box says **UPDATE AVAILABLE** → **Update from GitHub & restart** |
| | Test again |
| | Something's off → **Copy diagnostics** → paste into the chat |

That's it. The Update button stops the watcher, pulls, reinstalls, and reopens
the app (press **Start watcher** again if you don't use the startup task).
From a terminal the same thing is `C:\ionomos-src\UPDATE.ps1`.

**"Copy diagnostics"** puts one text block on the clipboard: version + commit,
`check`, `status --all`, the config, the last 150 log lines, what's in the
inbox and any `.REJECTED.txt` notes. Also saved as
`C:\Fragpipe_Auto\logs\diagnostics-<date>.txt`. Paste the whole thing — that's
everything needed to reproduce a problem on the Mac. Since D74 the copied
text has the lab's names replaced as a bundle does (the same pseudonyms, and
the same leak check before it reaches the clipboard); the saved copy is
`diagnostics-<date>-anonymised.txt` with its key next to it, so
`ionomos bundle translate KEY answer.txt` reads an answer back. **Copy with
real names** (beside it) copies the text as before; `ionomos diagnose` prints
real names unless given `--anonymise`. Secrets are `***` either way.

## Bundles: the lab's files on the developer's side (D63)

For a failure the text block does not explain, or to check the analysis on
the lab's real tables, the lab makes a **bundle**: one zip, saved on the
Desktop. Ionomos uploads nothing. A person copies the zip to Dropbox (or
anywhere) and shares it.

```
 Proteomics PC                           Dropbox            developer (or an assistant)
 ┌─────────────────────────────────┐  a person  ┌──────┐   ┌────────────────────────────────────────────┐
 │ Report a problem… / ionomos     │  copies    │ .zip │   │ ionomos bundle inspect  X.zip              │
 │ bundle  →  Desktop\             │ ─────────▶ │      │──▶│ ionomos bundle unpack   X.zip work\        │
 │   Ionomos-bundle-…-validate.zip │            └──────┘   │ ionomos --config work\config.yaml \        │
 │   Ionomos-bundle-…-KEY-keep-in- │                       │         analyze work\exp001                │
 │     the-lab-DO-NOT-SHARE.json   │ ◀── answer about ──── │ compare work\exp001\results with the copy  │
 │ ionomos bundle translate KEY f  │     exp001 / condA    │ of the lab's results in the zip            │
 └─────────────────────────────────┘                       └────────────────────────────────────────────┘
```

**On the PC.** In the app: **Report a problem…** (bottom bar), the same
button in a failed-search or failed-analysis pop-up, or Jobs → select a job
→ **Zip for troubleshooting…**. The window has a note, the job list, three
boxes and a list of what will go in with its size. From a terminal:

```powershell
ionomos bundle                       # diagnose: settings, logs, the running / waiting / last failed jobs
ionomos bundle 12 --level validate   # job 12 with the search's result tables and results\
ionomos bundle C:\Fragpipe_General\EJQ\20260902_EJQ_isoDTB_x --level validate --out D:\tmp
ionomos bundle 12 --level validate --dry-run     # list what would go in; write nothing
ionomos bundle 12 --include diann-report,peptides   # validate + DIA-NN's main report and the peptide / ion tables (D74)
```

| Level | Holds | Does not hold |
|---|---|---|
| `diagnose` (default) | `report.txt` (version, checks, job list, log tail), `config.yaml` (secrets as `***`), watcher and app logs (last 3 MB each), crash files, needs-attention items; per job: `ionomos.json`, `DONE.txt` / `FAILED.txt`, `experiment.yaml`, TMT `annotation.txt`, everything in `ionomos_run\` (workflow, manifest, console logs, engine settings, `run_fingerprint.json` when present), `results\analysis.json` and `analysis_error.txt` | result tables |
| `validate` | all of the above, plus the tables the analysis reads (FragPipe `combined_*.tsv`, `tmt-report\abundance_*.tsv`, `psm.tsv`; DIA-NN `*pg_matrix.tsv`, `*stats.tsv`; MaxQuant `proteinGroups.txt`, `summary.txt`; Sage `lfq.tsv`, `tmt.tsv`, `results.json`, `results.sage.tsv`), FragPipe's own copy of the workflow and manifest, an SDRF in the experiment folder, and all of `results\` | DIA-NN's main `report.tsv` / `report.parquet`, peptide-level FragPipe files other than `combined_*`, earlier attempts (`*_previous_*`) |
| `--include diann-report,peptides` (D74; implies `validate`) | also DIA-NN's main report (`report.tsv`; `report.parquet` written as tab-separated text with pyarrow, value by value as Ionomos reads Parquet), DIA-NN `*pr_matrix.tsv`, FragPipe `peptide.tsv` / `ion.tsv`, MaxQuant `peptides.txt` / `modificationSpecificPeptides.txt`; added after every other table, so the size limit drops them first; each over `--extra-mb` (200) cut to every n-th row | spectral libraries (`*lib*`, `.speclib`), DIA-NN's first-pass report |

Never, at either level: raw / mzML / `.d` files, FASTA files (`BUNDLE.json`
records name, size, entry and decoy count, SHA-256), spectral libraries.
Limits: 2,000 MB per bundle before compression (`--max-mb`); what does not
fit is left out and listed. A `psm.tsv` / `results.sage.tsv` over 25 MB
(`--psm-mb`) is cut to every n-th row, and the job is marked "not fully
reproducible" because PSM-level numbers then differ. Tables are streamed
(about 10 MB/s, a few MB of memory), and the leak check reads the zip once
more, so a 1 GB bundle takes a few minutes.

**Names.** On by default: user names and aliases, the PC name, the Windows
account and home folders, experiment / inbox folder names, raw file, sample
and condition names, e-mail and IP addresses are replaced; the same original
gets the same pseudonym in every file, file name and table header
(`--no-anonymise` keeps the names; secrets are removed either way). A name is
rewritten word by word, so the analysis reads it as before:

```
20260914_Isaac_DIA_FLAG-AR-pulldown        ->  exp002            (a folder: as a whole)
Isaac / IJ                                 ->  user01 / user01a
Zanubrutinib_10uM_3h_2.raw                 ->  condB_10uM_3h_2.raw
DMSO_1, pool_126, 9plex, isoDTB, F3        ->  unchanged (control / role words, numbers, doses, method names)
```

`--keep-conditions` keeps every condition word. Protein, gene and peptide
columns of tables are never rewritten. The key (pseudonym → original) is the
`…-KEY-keep-in-the-lab-DO-NOT-SHARE.json` next to the zip; it is not in the
zip. `ionomos bundle translate KEY answer.txt` turns an answer back.

After writing, the zip is searched for every original (file names too). A hit
means no zip: the command fails with `LEAK CHECK FAILED` and the file names.

Not covered, and said in the zip's `README.txt`: free text (the note typed
for the bundle; `notes:` fields are removed), a name inside a longer word, a
plain word of a name standing alone elsewhere (`pulldown`; words with
letters and digits such as `KL6159A` are replaced everywhere), names that
are also gene names (kept in identifier columns, listed in the key file),
folder names above the users folder, pictures and other non-text files (left
out). **Nothing here has been run on the lab's real folders yet.**

**On the developer's side.**

```bash
ionomos bundle inspect Ionomos-bundle-20261001-1203-validate-v0.13.0.zip
#   level, version and build, each job (status, method, FASTA facts, "analysis can be repeated: yes"),
#   what was capped or left out, what was converted (report.parquet); then the name check: every file
#   searched for the real names this computer knows. On the lab's PC (its settings and job list, and the
#   key next to the zip) that is a second, independent look before the zip is shared; here, without the
#   key, there is nothing to check against (--key KEY if you have one)
ionomos bundle unpack Ionomos-bundle-….zip work
#   work/exp001/            the experiment folder: ionomos.json, ionomos_run/, fragpipe/, results/
#   work/config.yaml        the lab's methods and analysis defaults; every folder points into work/_lab/
#   work/_bundle/           BUNDLE.json, README.txt, report.txt, logs/, crashes/, the lab's config.yaml
cp -r work/exp001/results lab_results
ionomos --config work/config.yaml analyze work/exp001
diff -r lab_results work/exp001/results     # the .tsv files should be identical
```

`unpack` checks every file against the SHA-256 in `BUNDLE.json` and never
writes over anything. With the bundled config the lab's `analysis:` defaults
and method definitions apply; without `--config`, Ionomos' defaults do. The
re-run matches the lab's tables byte for byte when the same Ionomos version
is used and the bundle says "analysis can be repeated: yes". It can differ
when: a PSM table was row-sampled; the lab used a gene-set file or site
annotation that is not in the bundle; enrichment libraries differ between
the two computers; or `inspect` says the pseudonyms changed the order of
the sample names (Perseus imputation draws its random numbers sample by
sample in name order; pseudonyms are chosen to keep that order and the
bundle says when they could not).

## Rules that keep it painless

- **Never edit code on the PC.** Update refuses to run if files in the clone
  were changed by hand (it would have to throw the edits away). Edit on the
  Mac, push, update.
- **Everything of yours lives outside `C:\ionomos-src`.** If the clone is
  ever broken, delete the folder and re-run `dev_install.ps1`; nothing is lost.
- The Mac side: `scripts/test_mac.sh` before pushing. GitHub also runs the
  suite on Linux *and Windows* on every push (Actions tab), so Windows-only
  mistakes get caught there first.

## When it's stable: back to the exe

For the lab's real install (no git, no Python on the machine) the frozen exe
is still the plan. GitHub builds it:

```bash
git tag v0.2.0 && git push --tags
```

→ Actions builds `Ionomos-0.2.0-windows.zip` on a Windows machine and attaches
it to a Release on GitHub. Or *Actions → build-exe → Run workflow* for a
one-off build without tagging (zip under "Artifacts"). Install per
[DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md) Way A. The dev install and the exe
install can coexist; they share the same `config.yaml`.

## Optional: Claude Code on the PC too

Installing Claude Code on the PC lets you say "look at the log and tell me
why the drop didn't move" *there*, with it reading the real files. It works
fine alongside this loop — GitHub is still what moves code between the two
machines — but each machine's Claude has its own memory of the project, so
keep the Mac as the place where changes are made.
