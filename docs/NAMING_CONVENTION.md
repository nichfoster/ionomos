# Naming convention

The contract between lab members and the watcher. Philosophy: **flexible
folder names, strict raw-file tails.** The watcher looks for keywords in the
folder name rather than enforcing a fixed layout; the *end* of each raw file
name is reserved for replicate/fraction numbers, and what those numbers mean
depends on the method.

Implemented in `labwatch/src/labwatch/naming.py`; `labwatch dry-run <folder>`
shows exactly how a folder will be interpreted without touching it.

## Folder name

Anything you like, as long as the name contains:

| Must contain | How it's found | Examples that work |
|---|---|---|
| **Method** | keyword anywhere in the name (case-insensitive): `isoDTB`, `TMT`, `DIA` (aliases configurable, e.g. `DIANN`) | `20260902-isoDTB_EJQ-2-027`, `THB10ISODTB`, `KL6159A_9plex_TMT`, `EJQ_123_DIA` |
| **User** | your initials or your folder name under `C:\Fragpipe_General\`, as a separate token (split on `_ - . space ( )`). Aliases live in `config.yaml` (`IJ` → `Isaac`, `EJQ_2` → `EJQ`) | `EJQ_isoDTB_…`, `…_IJ_DIA`, `Taylor Elements TMT run 3` |
| *(optional)* **Date** | a token that is `YYYYMMDD`, `MMDDYYYY` or `MMDDYY`. If absent, the drop date is recorded | `20260902`, `08172026`, `081726` |

Recommended shape (sorts well, unambiguous): `YYYYMMDD_<initials>_<method>_<whatever>`
e.g. `20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h`.

**Spaces and punctuation are tolerated** — the folder is renamed on the way in
(`20260902-isoDTB_EJQ-2-027 (1uM 3h)` → `20260902-isoDTB_EJQ-2-027-1uM-3h`)
because FragPipe cannot handle spaces in paths. The original name is kept in
`labwatch.json`.

Rejected, with a `<name>.REJECTED.txt` note left next to the folder:

- no method keyword (`IJD05_FLAG_pulldown`)
- two method keywords (`EJQ_isoDTB_and_TMT`)
- no recognisable user (`EJQ123_isoDTB` — `EJQ123` is one token; write `EJQ_123_isoDTB`),
  unless `users.default` is set in config, in which case it's filed there
- two users (`EJQ_Isaac_isoDTB`)
- destination already exists / same name was processed before → add `_redo`

## Raw file names — the tail is reserved

Separators before the numbers may be `_` or `-`; optional `R`/`rep` and
`F`/`frac` prefixes are accepted.

### isoDTB — `<sample>_<rep>_<fraction>.raw`

```
EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1_1.raw … _3_7.raw    3 reps × 7 fractions
X_R2_F7.raw                                          same thing, prefixed
X_2.raw                                              unfractionated, rep 2
```
`sample` → FragPipe experiment; `rep` → bioreplicate; fractions merge automatically.
Every rep must have the same fraction set (a missing `_3_7` means the copy was
incomplete → rejected).

### TMT — `<sample>[_TMT]_[F]<fraction>.raw`

```
KL6159A_TMT_F1.raw … KL6159A_TMT_F8.raw     one plex, 8 fractions
KL6159A_F3.raw   KL6159A_3.raw              also fine
KL6159A.raw                                 single fraction
```
Every TMT run holds all replicates in its channels, so **bioreplicate is
always 1** (per the lab SOP). `sample` → experiment (= plex name). Two plexes
in one drop = two different `sample` prefixes. Channel → sample-name mapping
comes from `experiment.yaml` (below) or a FragPipe `annotation.txt` you put in
the folder.

### DIA — `<condition>_<biorep>.raw`

```
DMSO_1.raw  DMSO_2.raw  DMSO_3.raw
Drug_1.raw  Drug_2.raw  Drug_3.raw          two conditions × 3 bioreps
Drug_R3.raw                                 also fine
```
`condition` → experiment; `biorep` → bioreplicate. A file with no trailing
number is bioreplicate 1.

Raw files may sit at the top level or in a `raw\` subfolder. Anything else in
the folder (`.xlsx`, notes, `.mzML`) is carried along untouched.

## `experiment.yaml` (optional) — Phase 2/3

For things names can't say. If present it is validated at intake and its
values override what was parsed.

```yaml
method: TMT                 # override the keyword match
workflow: TMT10-MS3-phospho # file under config workflow_dir (no extension needed)
fasta: human_reviewed_2025-01_decoys.fas

files:                      # per-file overrides
  KL6159A_1_1.raw: {experiment: plex1, bioreplicate: 1}

tmt:                        # TMT only; one block per plex (= experiment name)
  tag: TMT-10
  channels:                 # channel → sample name. KEEP the channel token in
    126:  DMSO_126          #   the sample name (lab SOP; the annotation script keys on it)
    127N: DMSO_127N
    127C: Drug_127C

notes: "24 h treatment, 1 µM"   # copied into labwatch.json for provenance
```

## What the watcher derives

1. folder name → `user`, `method`, `date`, sanitised name
2. `user` → destination `C:\Fragpipe_General\<user>\<safe-name>\`
3. `method` → workflow, FASTA, data type, post-processing (from `config.yaml`)
4. raw names → FragPipe manifest lines (`file  experiment  bioreplicate  DDA|DIA`)
5. all of it → `labwatch.json` in the destination

## Still to confirm with the lab

- Is user = folder under `C:\Fragpipe_General\` right? Which initials/aliases to configure?
- Are there ever two methods in one drop? (Currently rejected.)
- TMT: hand-written `annotation.txt` vs `experiment.yaml` — which do people prefer?
