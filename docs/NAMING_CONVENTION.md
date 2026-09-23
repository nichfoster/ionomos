# Naming convention

The contract between lab members and the watcher. Philosophy: **flexible
folder names, strict raw-file tails.** The watcher looks for keywords in the
folder name rather than enforcing a fixed layout; the *end* of each raw file
name is reserved for replicate/fraction numbers, and what those numbers mean
depends on the method.

Implemented in `ionomos/src/ionomos/naming.py`; `ionomos dry-run <folder>`
shows exactly how a folder will be interpreted without touching it.

## Folder name

Anything you like, as long as the name contains:

| Must contain | How it's found | Examples that work |
|---|---|---|
| **Method** | keyword anywhere in the folder name (case-insensitive): `isoDTB`, `TMT`, `DIA` (aliases configurable, e.g. `DIANN`). If the folder name has none, the **raw file names** are searched too | `20260902-isoDTB_EJQ-2-027`, `THB10ISODTB`, `KL6159A_9plex_TMT`, `EJQ_123_DIA` |
| **User** | your initials or your folder name under `C:\Fragpipe_General\`. Matched as a token (split on `_ - . space ( )`) **or glued to an ID** (`IJD05`, `EJQ123`, `THB10`). Aliases live in `config.yaml` (`IJ` → `Isaac`); the resolver window can add them | `EJQ_isoDTB_…`, `IJD05_isoDTB`, `Taylor Elements TMT run 3` |
| *(optional)* **Date** | `YYYYMMDD`, `YYYY-MM-DD`, `MMDDYYYY`, `MM-DD-YYYY` or `MMDDYY`. If absent, the drop date is recorded | `20260902`, `2026-09-02`, `08172026`, `081726` |

Recommended shape (sorts well, unambiguous): `YYYYMMDD_<initials>_<method>_<whatever>`
e.g. `20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h`.

**Spaces and punctuation are tolerated** — the folder is renamed on the way in
(`20260902-isoDTB_EJQ-2-027 (1uM 3h)` → `20260902-isoDTB_EJQ-2-027-1uM-3h`)
because FragPipe cannot handle spaces in paths. The original name is kept in
`ionomos.json`.

Rejected, with a `<name>.REJECTED.txt` note left next to the folder:

- no method keyword (`IJD05_FLAG_pulldown`)
- two method keywords (`EJQ_isoDTB_and_TMT`)
- no recognisable user (`XYZ99_isoDTB`) — unless `users.default` is set, in which case it's filed there
- two users (`EJQ_Isaac_isoDTB`)
- destination already exists / same name was processed before → add `_redo`

**All of these except the last open the resolver window** (see below) when the
watcher runs with the GUI enabled; the `.REJECTED.txt` note is the fallback.

## Raw file names — the tail is reserved

Separators before the numbers may be `_` or `-`. Optional prefixes are
accepted: `R`, `rep`, `Rep_`, `bio`, `biorep`, `n` for replicates; `F`, `frac`,
`fraction` for fractions (`X_R2_F7`, `X_rep2_frac7`, `X_bio2_F7` all mean rep 2,
fraction 7).

**Xcalibur timestamps are ignored.** When a file of that name already exists,
Xcalibur appends `_YYYYMMDDhhmmss` (`X_DMSO_2_20260508204737.raw`). The tail is
read as if it weren't there (→ DMSO, rep 2); the file keeps its full name. If
that leaves two files with the same condition + replicate (a re-acquisition
next to the original), the resolver window asks which is which — it never guesses.

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

## Raw files dropped without a folder

Dragging just the `.raw` files into the inbox works too. Once they have stopped
changing (same 60 s rule), Ionomos groups them by their shared name and moves
each group into a new folder in the inbox, named after that shared part:

```
CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1.raw  ┐
CS_22rv1_FLAG-AR_MA25-10uM_MA25_1.raw  ├─▶ inbox\CS_22rv1_FLAG-AR_MA25-10uM\
CS_22rv1_FLAG-AR_MA25-10uM_MA25_2.raw  ┘
```

Files sharing at least the first two name parts go together; trailing
replicate/fraction numbers and Xcalibur timestamps are left out of the folder
name; method keywords stay in it. From there it's an ordinary folder drop, so
the same rules apply: initials and a method keyword somewhere in the file
names (or the window asks). A folder is still the better habit — it keeps
unrelated runs apart for sure. Non-raw loose files are left alone.
`watcher.group_loose_files: false` turns this off (`ionomos/src/ionomos/loose.py`).

## When Ionomos can't tell: the resolver window

If the user, method, a file's tail, or the fraction layout can't be worked
out, a small window opens on the proteomics PC:

```
 20260902-isoDTB_XYZ-2-027 (1uM 3h)
 ⚠ no known user in '…'; include your initials or folder name (known: Aman, Chris, EJQ, Isaac)

 User    [ Isaac        ▼]  (type a new name to create a folder)
 Method  [ isoDTB       ▼]   Date [2026-09-02]
 [x] Remember that [XYZ] means this user (future drops won't ask)
 [ ] Accept uneven fractions between replicates

 Files — experiment / replicate / fraction          [Re-read from file names]
 EJQ_PK_…_1_1.raw   [EJQ_PK_EJQ-2-027_isoDTB_1uM_3h] [1] [1]
 …
                                    [Skip (leave in inbox)]  [Accept & queue ⏎]
```

Everything is pre-filled with the best guess; Enter accepts, Esc skips. The
answer is saved as `experiment.yaml` inside the folder (so re-dropping it
never asks again) and, if "remember" is ticked, the initials are added to
`learned_aliases.yaml` so that person is recognised from then on. Skip leaves
the folder in the inbox with a `.REJECTED.txt` note; fixing the folder or
deleting the note triggers a retry.

## `experiment.yaml` (optional)

For things names can't say, or to pre-answer the window. If present it is
validated at intake and its values override what was parsed.

```yaml
method: TMT                 # override the keyword match
user: Isaac                 # override the initials match
date: 2026-09-02
workflow: TMT10-MS3-phospho # file under config workflow_dir
fasta: human_reviewed_2025-01_decoys.fas
allow_uneven_fractions: true   # accept reps with different fraction sets

files:                      # per-file overrides (fraction: -1 = single-shot)
  KL6159A_1_1.raw: {experiment: plex1, bioreplicate: 1, fraction: 1}

tmt:                        # TMT only; one block per plex (= experiment name)
  tag: TMT-10
  channels:                 # channel → sample name as condition_plex_channel: the lab's
    126:  DMSO_1_126        #   annotation script (and the report's conditions) key on it
    127N: DMSO_1_127N
    127C: Drug_1_127C

analysis:                   # results/report.html for this experiment (lab defaults: app tab 7)
  comparisons: ["Drug vs DMSO", "Drug2 vs DMSO"]   # treatment vs control; default: all vs the control
  control: DMSO             # default: recognised by name (DMSO, vehicle, ctrl, WT, ...)
  log2fc: 1                 # also: alpha, use_adjusted, min_valid, normalize, test, top_labels

notes: "24 h treatment, 1 µM"   # copied into ionomos.json for provenance
```

## What the watcher derives

1. folder name → `user`, `method`, `date`, sanitised name
2. `user` → destination `C:\Fragpipe_General\<user>\<safe-name>\`
3. `method` → workflow, FASTA, data type, post-processing (from `config.yaml`)
4. raw names → FragPipe manifest lines (`file  experiment  bioreplicate  DDA|DIA`)
5. all of it → `ionomos.json` in the destination
6. after FragPipe: conditions for the statistics come from the same names —
   DIA `DMSO_1.raw` → condition `DMSO`; TMT sample `Drug_1_128N` → `Drug`;
   isoDTB: each sample prefix is tested on its own (ratios vs 0). Edit
   `analysis:` in `experiment.yaml` and press *Re-run analysis* to change them.

## Still to confirm with the lab

- Is user = folder under `C:\Fragpipe_General\` right? Which initials/aliases to configure?
- Are there ever two methods in one drop? (Currently rejected.)
- TMT: hand-written `annotation.txt` vs `experiment.yaml` — which do people prefer?
