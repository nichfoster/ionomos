# Naming convention

This is the contract between lab members and the watcher. It is deliberately
short: a user should be able to remember it without looking it up, and the
watcher must be able to derive everything FragPipe needs from it.

**Status: PROPOSED — confirm with the lab before Phase 1.** See open questions
at the bottom.

## Design goals

1. Everything FragPipe's manifest needs (experiment name, bioreplicate, data
   type) must be derivable from names alone for the common case.
2. Match what people already do. Existing folders like
   `20260902-isoDTB_EJQ-2-027` and `20260804-isoDTB_IJ607061` are close to the
   proposal; raw files already use `<prefix>_<rep>_<fraction>.raw`.
3. Never contain spaces or characters FragPipe/Windows choke on.
4. Sort chronologically in Explorer.

## Experiment folder name

```
<DATE>_<USER>_<METHOD>_<EXPID>[_<DESCRIPTION>]
```

| Field | Rule | Example |
|---|---|---|
| `DATE` | `YYYYMMDD`, date the samples were run | `20260902` |
| `USER` | your handle — must match your folder under `C:\Fragpipe_General\` | `EJQ`, `Isaac`, `Chris` |
| `METHOD` | one of `isoDTB`, `TMT`, `DIA`, `DDA` (case-insensitive) | `isoDTB` |
| `EXPID` | your notebook/experiment ID; use `-` inside, never `_` | `EJQ-2-027` |
| `DESCRIPTION` | optional free text, `-` separated | `1uM-3h` |

Fields are separated by `_`. Inside a field use `-`. Allowed characters:
`A-Z a-z 0-9 - .`. No spaces, no parentheses, no `&`, no `+`.

Examples:

```
20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h
20260914_Isaac_DIA_IJD05_FLAG-AR-pulldown
20260126_Aman_TMT_KL6159A-159B_9plex
```

Rejected (with reason written to `labwatch.REJECTED.txt` next to the folder):

```
EJQ123_isoDTB                    → no date, no user
20260902-isoDTB_EJQ-2-027        → fields separated by '-' not '_'
20260902_EJQ_isoDTB_EJQ-2-027 (1uM 3h)   → spaces / parentheses
20260902_ejq_isodtb_EJQ-2-027    → ok: USER and METHOD are case-insensitive but
                                   USER must resolve to an existing user folder
```

## Raw file names

FragPipe needs, per raw file: **experiment** (grouping label), **bioreplicate**
(integer), **data type** (`DDA`/`DIA`). The watcher derives these from the
trailing numeric suffixes of the filename:

```
<SAMPLE>_<REP>_<FRACTION>.raw     fractionated (isoDTB, TMT with offline fractions)
<SAMPLE>_<REP>.raw                single-shot (DIA, unfractionated DDA)
```

- `SAMPLE` is everything before the numeric suffixes. It becomes the FragPipe
  **experiment** name. Files with different `SAMPLE` in one folder become
  different experiments (e.g. `DMSO_1.raw`, `DMSO_2.raw`, `Drug_1.raw`,
  `Drug_2.raw` → two experiments, two bioreplicates each).
- `REP` becomes **bioreplicate**.
- `FRACTION` is ignored by the manifest (FragPipe groups fractions by
  experiment+bioreplicate automatically) but is validated for completeness:
  every replicate must have the same set of fractions, otherwise the job is
  rejected as incomplete.

This is exactly how existing runs are laid out, e.g.
`EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_1_1.raw … _3_7.raw` → experiment
`EJQ_PK_EJQ-2-027_isoDTB_1uM_3h`, bioreplicates 1–3, 7 fractions each.

`.raw` files may be at the top level of the folder or inside a `raw/`
subfolder. Anything else (`.mzML`, `.xlsx`, notes) is left alone and carried
along with the move.

## `experiment.yaml` (optional)

For anything the names can't say. If present in the folder it is validated and
its values override anything parsed from names.

```yaml
# All keys optional.
method: TMT                 # override METHOD from folder name
fasta: 2025-01_human_reviewed_decoys.fas   # filename under config `fasta_dir`; default per method
workflow: tmt10-ms3-phospho # filename (no ext) under config `workflow_dir`; default per method

# Override per-file assignments. Filename → {experiment, bioreplicate}.
files:
  KL6159A_1_1.raw: {experiment: plex1, bioreplicate: 1}

# TMT only. Required unless a FragPipe-style annotation.txt is already in the folder.
# One block per plex (= per experiment name above).
tmt:
  tag: TMT-10               # TMT-6 | TMT-10 | TMT-11 | TMT-16 | TMT-18
  channels:                 # channel → sample name. Keep the channel token in the
    126:  DMSO_126          #   sample name (the lab's TMT SOP requires it, and the
    127N: DMSO_127N         #   annotation-fixing script keys off it).
    127C: Drug_127C
    # ...

# Free-form. Copied verbatim into labwatch.json for provenance.
notes: "24 h treatment, 1 µM"
```

The watcher writes the FragPipe `annotation.txt` (`<channel>\t<sample>`) from
the `tmt.channels` block, one per plex, into the experiment folder before the
run.

## What the watcher does with all this

1. Folder name → `user`, `method`, `exp_id`, `date`, `description`.
2. `user` → destination `C:\Fragpipe_General\<user>\` (must exist; configurable).
3. `method` → default workflow file + FASTA + post-processing steps (from `config.yaml`).
4. Raw file names (+ `experiment.yaml`) → `fragpipe-files.fp-manifest`.
5. Everything above → `labwatch.json` in the experiment folder.

## Open questions for the lab

- Is `USER` = folder name under `C:\Fragpipe_General\` the right identity? Some
  people have multiple folders (`EJQ`, `EJQ_2`; `Taylor_Elements`).
- Do users want the date to be the acquisition date or the drop date? (Proposal: acquisition.)
- Is `DDA` (plain, non-isoDTB label-free) actually used, or can we drop it from v1?
- TMT: is `annotation.txt` something people are comfortable writing by hand
  (it's a 2-column text file), or must it always come from `experiment.yaml`?
- Are there ever mixed methods in one drop (e.g. isoDTB + DIA of the same
  samples)? Proposal: no — one folder = one method = one FragPipe run.
