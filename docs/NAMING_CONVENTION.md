# Naming convention

The contract between lab members and the watcher. Philosophy: **flexible
folder names, strict raw-file tails.** The watcher looks for keywords in the
folder name rather than enforcing a fixed layout; the *end* of each raw file
name is reserved for replicate/fraction numbers, and what those numbers mean
depends on the method.

Implemented in `ionomos/src/ionomos/naming.py`; `ionomos dry-run <folder>`
shows exactly how a folder will be interpreted without touching it, and
`ionomos names test <names…>` shows how any list of names is read.

Everything below is this lab's convention, which is the default. Another lab
can change the file patterns, date formats and condition codes in
`config.yaml` without touching code: see
[Other conventions (config)](#other-conventions-config).

## Folder name

Anything you like, as long as the name contains:

| Must contain | How it's found | Examples that work |
|---|---|---|
| **Method** | keyword anywhere in the folder name (case-insensitive): `isoDTB`, `TMT`, `DIA` (aliases configurable, e.g. `DIANN`). If the folder name has none, the **raw file names** are searched too | `20260902-isoDTB_EJQ-2-027`, `THB10ISODTB`, `KL6159A_9plex_TMT`, `EJQ_123_DIA` |
| **User** | your initials or your folder name under `C:\Fragpipe_General\`. Matched as a token (split on `_ - . space ( )`) **or glued to an ID** (`IJD05`, `EJQ123`, `THB10`). Aliases live in `config.yaml` (`IJ` → `Isaac`); the resolver window can add them | `EJQ_isoDTB_…`, `IJD05_isoDTB`, `Taylor Elements TMT run 3` |
| *(optional)* **Date** | `YYYYMMDD`, `YYYY-MM-DD`, `MMDDYYYY`, `MM-DD-YYYY` or `MMDDYY`, tried in that order (`naming.date_formats`) — the six-digit `MMDDYY` form only when its year `20yy` falls within `[today.year − 25, today.year + 1]` (the last 25 years through next year; a run ID like `113056` is never a date). If absent, the drop date is recorded | `20260902`, `2026-09-02`, `08172026`, `081726` |

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

**Replicate and fraction numbers run 1–999.** A tail number outside that range
rejects the file with a clear `NamingError` (`0` is out of range, as is anything
above 999), and a digit run longer than three is never read as a number at all —
a date-shaped tail cannot become a replicate or fraction. What happens then
depends on the method: an **isoDTB** file must end in a valid
`_<rep>[_<fraction>]` tail, so `X_20260902.raw` or `X_1_1000.raw` is rejected
outright; **DIA and TMT** also accept a bare stem, so a date-like tail is
absorbed into the condition/sample name (`DMSO_20260902.raw` → condition
`DMSO_20260902`, bioreplicate 1 — no phantom number is minted).

The same bound holds wherever a number comes from (D84): `files:` in
`experiment.yaml`, the naming window, the naming history and a queued job's
manifest. FragPipe and DIA-NN keep the replicate in a 32-bit whole number; on
0.5.1 an Xcalibur time stamp (`20260508180610`) went in as a replicate and
DIA-NN's matrix lost both runs that had one. An `experiment.yaml` with such a
number opens the naming window, which shows the files as their names read
and explains why; its answer replaces the old `files:` entries. Without a
window the folder stays in the inbox with a `.REJECTED.txt` note that says the
same. A job already queued with such a number waits, naming the file, until
its `files.<name>.bioreplicate` in the experiment folder's `experiment.yaml`
is 1–999; the search then starts by itself. The naming history skips a
learned number outside 1–999, and a re-analysis whose `experiment.yaml` can't
be read says so in the report instead of ignoring it silently.

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

**Several plexes: a folder each** (D69). FragPipe reads one annotation file
per folder, so plexes that share a folder get FragPipe's own channel names
(`<plex>_<channel>`); the job says so, and the files are filed as dropped.
For your sample names, drop the experiment with a folder per plex:

```
20260127_Aman_TMT_KL6160-2plex\
  plexA\KL6160A_TMT_F1.raw  plexA\KL6160A_TMT_F2.raw
  plexB\KL6160B_TMT_F1.raw  plexB\KL6160B_TMT_F2.raw
  experiment.yaml           tmt: plexes: {plexA: {channels: …}, plexB: {channels: …}}
```

The folder name is the plex (spaces and symbols are cleaned as in file
names: `plex A` → `plex-A`); the layout is kept, and each folder gets its
`annotation.txt`. A raw file name may appear only once in the whole drop.
Subfolders are read only for TMT, and only when no `.raw` is at the top level
or in `raw\`.

### DIA — `<condition>_<biorep>.raw`

```
DMSO_1.raw  DMSO_2.raw  DMSO_3.raw
Drug_1.raw  Drug_2.raw  Drug_3.raw          two conditions × 3 bioreps
Drug_R3.raw                                 also fine
```
`condition` → experiment; `biorep` → bioreplicate. A file with no trailing
number is bioreplicate 1.

**Short condition codes.** A 1–2 letter code glued to the replicate number is
read as condition + replicate, with the lab's codes expanded:

```
KC_DIA_D1.raw  KC_DIA_D2.raw  KC_DIA_D3.raw  →  KC_DIA_DMSO, reps 1–3
KC_DIA_C1.raw  KC_DIA_C2.raw  KC_DIA_C3.raw  →  KC_DIA_Compound, reps 1–3
X_DM4.raw                                   →  X_DM, rep 4 (unknown code kept as it is)
```

The codes live in `config.yaml` → `naming.condition_codes` (default
`D: DMSO`, `C: Compound`). Longer codes count only when they are listed there,
so an instrument setting such as `…_DIA_HCD33.raw` is never read as
"condition HCD, replicate 33". Because a short code can mean different things
in different labs, every drop is shown in the review window (below) before it
is filed.

**Instrument settings are part of the sample name.** A FAIMS compensation
voltage or a collision energy at the end of a name is never a replicate or a
fraction, for every method. The settings recognised are `CV`, `FAIMS`, `HCD`,
`NCE` and `CID` followed by 2–3 digits, with or without `-` / `_`. Three runs
at different CVs are three samples:

```
CS_isoDTB_ELK_3_1-7_DIA_CV-35.raw  →  CS_isoDTB_ELK_3_1-7_DIA_CV-35, rep 1   (not …_DIA_CV rep 35)
CS_isoDTB_ELK_3_1-7_DIA_CV-45.raw  →  CS_isoDTB_ELK_3_1-7_DIA_CV-45, rep 1
X_CV35.raw                         →  X_CV35, rep 1                           (not code CV, rep 35)
DMSO_CV-45_2.raw                   →  DMSO_CV-45, rep 2                       (a replicate after it still counts)
KL_TMT_CV-40.raw                   →  KL_TMT_CV-40                            (not fraction 40)
X_CV_1.raw                         →  X_CV, rep 1                             (one digit: a replicate)
```

isoDTB names need a replicate, so `X_CV-35.raw` is refused there with a hint
(`X_CV-35_1.raw`). A lab that uses one of these words as a condition code
lists it in `naming.condition_codes`, and that meaning wins.

Raw files may sit at the top level or in a `raw\` subfolder. Anything else in
the folder (`.xlsx`, notes, `.mzML`) is carried along untouched.

## Other conventions (config)

Another lab's names are a `config.yaml` change, not a code change (D37).
With no `naming:` block, or with the defaults written out, everything above
applies exactly as described.

```yaml
naming:
  date_formats: [YYYYMMDD, DDMMYYYY]   # tried in order; default [YYYYMMDD, MMDDYYYY, MMDDYY]
  condition_codes: {V: Vehicle, T: Treated}   # DIA short forms; replaces the default D / C list
  methods:                     # how each method's .raw names are read
    DIA: '{condition}_rep{rep}'            # a template (shorthand for files:)
    LFQ:                                   # a method of your own (it also needs a methods.LFQ entry)
      files: '{sample}_R{rep}_F{fraction}'
    SWATH: {like: DIA}                     # a DIA method under another name: DIA's rule, short codes and analysis
    PLATE:                                 # a regex, for names a template can't describe
      pattern: '(?P<sample>[A-H]\d{2})-(?P<rep>\d+)(?:_(?P<fraction>\d+))?'
```

**Templates.** Quote them in YAML (a bare `{` starts a YAML mapping).

| In a template | Means |
|---|---|
| `{sample}` or `{condition}` | the FragPipe experiment (required, once, not inside `[ ]`) |
| `{rep}` (`{replicate}`, `{biorep}`) | the bioreplicate: 1–3 digits, after an optional `R`, `rep`, `bio`, `biorep` or `n` |
| `{fraction}` (`{frac}`) | the fraction: 1–3 digits, after an optional `F`, `frac` or `fraction` |
| `{any}` | text that is skipped (e.g. an instrument setting after the numbers) |
| `[ … ]` | an optional part (not nested) |
| `_` or `-` | either separator |
| letters, digits, `.` | themselves; letters match either case |

A method whose template has no `{rep}` is always bioreplicate 1 (as TMT);
one without `{fraction}` is single-shot. The built-in rules are templates
too, and compile to the same regular expressions as before:

| Method | Built-in template |
|---|---|
| isoDTB | `{sample}_{rep}[_{fraction}]` |
| TMT | `{sample}[_TMT][_{fraction}]` |
| DIA | `{sample}[_{rep}]` (plus the short condition codes) |

**Regular expressions** (`pattern:`) must have a named group `sample` (or
`condition`) and may have `rep` and `fraction`. The whole file name, without
`.raw`, must match; put `(?i)` in front to ignore case.

**The same checks apply to every rule.** A replicate or fraction must be
1–999 (D30); a template never reads more than three digits as a number; the
Xcalibur `_YYYYMMDDhhmmss` stamp is ignored; every replicate needs the same
fractions. A method's keywords stay in `methods.<name>.aliases` (the app's
Methods tab edits them), and one keyword can belong to only one method.

**`like:`** says a method *is* a built-in method under another name (D54).
It borrows that method's rule (and, for `DIA`, its short condition codes;
`condition_codes: false` turns those off), and the method is treated as that
one everywhere else too:

| `like:` | Search | Review window | Analysis |
|---|---|---|---|
| `isoDTB` | expects the label-quant table | no control (each sample is a ratio) | site tables, liganded cysteines, `ICAT light / heavy` SDRF rows |
| `TMT` | writes `annotation.txt` from experiment.yaml `tmt:` | no control (conditions are in the channels) | `tmt-report/abundance_*_MD.tsv`, the annotation file, plexes, `TMT126…` SDRF rows |
| `DIA` | expects the DIA-NN output | asks for the control | `…pg_matrix.tsv`, label-free SDRF rows |

`files:` or `pattern:` next to `like:` changes only how the names are read.
So `DIA_phospho: {like: DIA}` or `TMTpro: {like: TMT, files: '{sample}_F{fraction}'}`
are full DIA / TMT methods with their own workflow, FASTA and keywords. A
method with no `like:` is a method of its own: label-free, read from
`combined_protein.tsv`. To borrow only a built-in rule's shape for such a
method, write the template out (`files: '{sample}_{rep}[_{fraction}]'`).

A method searched by another engine (`engine: diann | maxquant | sage`,
docs/ENGINES.md) is what that engine makes, whatever `like:` says: DIA for
DIA-NN, label-free for MaxQuant and Sage.

`ionomos names test` prints what a method is run as (`method : SWATH
(searched and analysed as DIA)`). To only add another word for DIA, add it
to `methods.DIA.aliases`.

Date formats: `YYYYMMDD`, `MMDDYYYY`, `DDMMYYYY` (separators `-`, `_` or
`.` allowed), and the six-digit `MMDDYY`, `DDMMYY`, `YYMMDD` (only within the
D30 year window). Ambiguous names (`03042026`) take the first format that fits.

A mistake in the block stops the config from loading, with the reason
(`naming.methods.DIA: '{rep}_{fraction}' needs {sample} once, outside [ ]
(it names the experiment)`), so the watcher keeps the last good config and
the app refuses to save it.

### Test your names

```
ionomos names test 2026-09-30__jdoe__DIA__liver WT-a_rep1.raw KO-b_rep2.raw KO-b_2.raw

REJECT  folder 2026-09-30__jdoe__DIA__liver
  renamed to : 2026-09-30_jdoe_DIA_liver
  user       : jdoe
  method     : DIA
  file rule  : {condition}_rep{rep}
  date       : 2026-09-30
  files      :
    WT-a_rep1.raw   sample WT-a · replicate 1 · no fraction   [DIA]
    KO-b_rep2.raw   sample KO-b · replicate 2 · no fraction   [DIA]
    KO-b_2.raw   ✗ 'KO-b_2.raw': DIA files must look like {condition}_rep{rep}.raw
```

Each argument is a folder name, a `.raw` name, or a folder on disk (its name
and its raws). `.raw` names after a folder name are read as that folder's
files. A `.raw` name on its own is read by the method keyword in it, or
`--method`, or else once per method. It uses the live `config.yaml` and
touches nothing. Exit code 0 means every name was read. The app's **Methods**
tab has the same check (**Test names…**), using the settings in the window
whether or not they are saved.

## QC standard runs

Runs of the lab's recurring QC standard (a HeLa or K562 digest) are filed and
searched like any drop, then also trended on the instrument QC page
([QC_TREND.md](QC_TREND.md), D45). Nothing about the name has to change. A
run counts when its `.raw` name or folder name contains one of
`qc_trend.match`, or when its method is listed in `qc_trend.methods`:

- The default words are `hela`, `k562`, `qc_std`, `qcstd` and `_qc_`.
- Case is ignored, and `_ - .` and spaces all count as one separator. So
  `_qc_` matches QC as a word: `…_QC_…`, `QC-HeLa`, `…_qc.raw`, but not
  `QCtest`.
- A folder that is an experiment (two or more samples with two or more
  replicates each) is not a QC standard, even when a name matches. HeLa is
  also a cell line people experiment on.
- `qc_trend.exclude` words win over everything.

| Folder / file | QC run? | Series |
|---|---|---|
| `20260930_EJQ_DIA_HeLa-200ng-QC` / `HeLa_200ng_1.raw` | yes (`hela`) | DIA · HeLa · 200ng |
| `20260930_EJQ_DIA_instrument-check` / `K562-50ng_1.raw` | yes (the file name) | DIA · K562 · 50ng |
| `20260930_EJQ_DIA_QC` / `run_1.raw` | yes (`_qc_`) | DIA · QC |
| `20260930_EJQ_DIA_QCtest` / `run_1.raw` | no (`QC` is not a word here) | – |
| `20260930_EJQ_DIA_HeLa_KO-vs-WT` / `KO_1`, `KO_2`, `WT_1`, `WT_2` | no (an experiment) | – |

The amount (`200ng`, `50ng`, `1ug`) is read from the name, so standards at
different loads are trended separately. An Xcalibur stamp at the end of the
name (`…_20260930143015.raw`) gives the acquisition time; without one, the raw
file's own time is used.

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

## Before filing: the review window

Every drop that parses is shown **before it is filed**, so a wrong reading is
caught before FragPipe runs. This is on by default; turn it off with
`gui.review_drops: false`. The window shows:

- user, method and date;
- each file's condition, replicate and fraction, all editable;
- **What Ionomos will assume**: one line per condition with its role
  (CONTROL, compound, competition of …, or isoDTB's "ratio vs 0") and its
  replicates and fractions, plus warnings for single replicates or a single
  condition;
- for DIA and label-free drops, a **role** list per condition (control,
  compound, competition of a compound, pool / reference, QC standard;
  **automatic** = read from the name), a **?** and **Confirm** on a role read
  from `pre`, `block`, `cold` or `10x`, and the comparisons the analysis will
  run with what uneven groups mean (D65). A changed role is saved to
  `experiment.yaml` → `analysis.roles`;
- a **Control** picker, which is the "vs" side of every volcano. It defaults
  to the same guess the analysis would make: DMSO, vehicle, control and the
  other control keywords, otherwise the alphabetically first condition. A
  different choice is saved to `experiment.yaml` → `analysis.control`.

**Accept & queue** files it. Your corrections are saved to `experiment.yaml`
so they stick. **Not now** leaves it in the inbox with a note. If
`gui.timeout_minutes` is set and nobody answers, a reviewed drop is filed as
read. The window isn't shown again for a folder someone already answered for
(`experiment.yaml` → `resolved_by: gui`). On a PC with no display, drops are
filed as read.

Users and aliases added in the app while the window is open appear in it
within two seconds. A blank user is filled in as soon as an alias matches
(e.g. `KC` → Kosuke). The running watcher re-reads `config.yaml` whenever it
changes, so there's no need to restart it.

## When Ionomos can't tell: the resolver window

If the user, method, a file's tail, or the fraction layout can't be worked
out, the same window opens with the problem at the top:

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

files:                      # per-file overrides; numbers 1–999 (fraction: -1 = single-shot)
  KL6159A_1_1.raw: {experiment: plex1, bioreplicate: 1, fraction: 1}

tmt:                        # TMT only; one block per plex (= experiment name). FragPipe and Sage TMT both use it
  tag: TMT-10
  channels:                 # channel → sample name as condition_plex_channel: the lab's
    126:  DMSO_1_126        #   annotation script (and the report's conditions) key on it
    127N: DMSO_1_127N
    127C: Drug_1_127C
  reference_channel: 126    # the pooled / bridge channel that joins several plexes (IRS; = analysis.tmt_reference)

analysis:                   # results/report.html for this experiment (lab defaults: app tab 7)
  comparisons: ["Drug vs DMSO", "Drug2 vs DMSO"]   # treatment vs control; default: all vs the control
  control: DMSO             # default: recognised by name (DMSO, vehicle, ctrl, WT, ...)
  log2fc: 1                 # also: alpha, use_adjusted, min_valid, normalize, test, top_labels
  enrichment_gmt: sets.gmt  # extra gene sets; a relative path is read from this folder first
  block: replicate          # the design (limma, D42): a block as a fixed effect — the replicate number
                            #   (rep 1 of every condition prepared together; pairs, patients), or
                            #   {DMSO_1: A, Drug_1: A, DMSO_2: B, Drug_2: B} (every sample listed), or
  # block_from: '_(P\d+)_'  #   a regex on the sample names: group "block" if named, else group 1
  covariates:               # optional, one value per sample: numbers -> a slope, text -> a factor
    age: {DMSO_1: 54, DMSO_2: 61, DMSO_3: 47, Drug_1: 49, Drug_2: 66, Drug_3: 58}
  variance_prior: deqms     # limma (default) | deqms: each protein's prior variance from its peptide count
  sdrf:                     # sample metadata for results/sdrf.tsv (lab-wide values: config.yaml analysis.sdrf)
    cell_type: HEK293T      # also: organism, organism_part, disease, instrument, cleavage_agent
  sdrf_factor: [compound]   # an SDRF put in this folder sets the design: which factor value column(s) are the
                            #   condition (default: all, joined); see docs/ENGINES.md
  irs: auto                 # several TMT plexes on one scale: auto | reference | sum | none
  doses:                    # a titration's doses (dose-response curves, D44); default: read from the
    DMSO: 0                 #   condition names (Cmpd_10nM, Cmpd_0p1uM); the control is dose 0
    Cmpd_low: 10 nM         #   units pM, nM, uM / µM, mM, M; a bare number needs dose_unit
    Cmpd_mid: 100 nM
    Cmpd_high: 1 uM
    Cmpd_top: 10 uM
  dose_min_doses: 4         # doses above 0 a compound needs before curves are fitted (default 4)
  times:                    # a time course (D53); default: read from the condition names (Drug_0h, Drug_30min,
    Drug_start: 0           #   Drug_4h, 2d; units s, min, h, d). 3+ time points per series get the time-course
    Drug_early: 30 min      #   tests; a bare number needs time_unit
    Drug_late: 4 h
  time_model: auto          # auto: time is a factor up to 6 time points, a natural spline in hours from 7 (D77);
  time_spline_df: auto      #   factor | spline. The spline's df: auto = 4 (at most the time points - 2)
  roles:                    # what each condition is (D61); default: read from the names. A competition (probe +
    DMSO: control           #   competitor) changes the default comparisons: compound vs control, competition
    Probe: compound         #   vs its compound, competition vs control, and adds results/specific_targets.tsv
    Probe_Comp: competition of Probe   # also: reference (a pool), qc (a QC standard)
  liganded_ratio: 4         # isoDTB: the competition ratio R that calls a cysteine liganded (D52) ...
  liganded_min_replicates: 2   # ... in at least this many replicates
  liganded_direction: high  # high: R = heavy / light (treated sample = light tag) | low: the other way round
  site_annotation: cysdb.csv   # a downloaded site table (CysDB) in this folder: marks known / new sites

notes: "24 h treatment, 1 µM"   # copied into ionomos.json for provenance
```

The sample names in `block:` and `covariates:` are the analysis' samples:
`condition_replicate` for DIA / LFQ (`DMSO_1`), the TMT sample names
(`DMSO_1_126`), a table's column names. Names that aren't samples are ignored
with a note. Keys under `covariates:` are free text (SDRF-style names such as
`characteristics[age]` are fine); a single `{sample: value}` mapping is one
covariate. A design that can't be used (a block equal to the condition, a
sample without a value, more parameters than samples) is explained in a
pop-up and the comparisons use the plain `~0 + condition` model. With three or
more conditions every report also has a moderated F-test ("any change").

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
   An SDRF (`*.sdrf.tsv`) put in the experiment folder beats the names (not
   `sample_conditions`): its `factor value[...]` is the condition (D47).
7. titrations: a condition whose name holds one concentration is a dose of
   the compound named by the rest (`Cmpd_10nM`, `10nM_Cmpd`, `Cmpd10nM` →
   compound `Cmpd`, 10 nM). Units `pM`, `nM`, `uM` / `µM`, `mM`, `M`; write a
   decimal point as `p` (`Cmpd_0p1uM`), since a `.` in a raw file name is
   awkward. The control (DMSO, vehicle, …, or `analysis.control`) is dose 0
   and shared by every compound. With 4 or more doses above 0 (`dose_min_doses`)
   the report gets dose-response curves (`results/dose_response.tsv`). A name
   with two doses (`A_1uM_B_10nM`, a combination) is left out with a warning;
   list the doses in `analysis.doses` instead.

8. time courses: a condition whose name holds one time is a time point of the
   series named by the rest (`Drug_0h`, `Drug_30min`, `Drug_4h`, `T24h`, `2d_KO`
   → series `Drug` / `KO`). Units `s`, `min`, `h`, `d`; a decimal point as `p`
   (`0p5h`). A control with no time in its name is time 0 of the series that
   have none. With 3 or more time points (`time_min_points`) the report gets
   the time-course tests (`results/time_course.tsv`). A name with two times is
   left out with a warning; list the times in `analysis.times` instead.

## Condition names and roles

The analysis reads a role from each condition name (D61, WORKFLOWS.md
"Competition experiments"). Nothing in the folder or raw-file rules above
changes; this is about the condition part of a name.

| Condition name | Role |
|---|---|
| `DMSO`, `Vehicle`, `Mock`, `WT_DMSO` … (the control keywords) | control |
| `Probe_Comp`, `Probe+Comp`, `ProbeComp`, `Probe_competition`, `Probe_excess`, `KL6283A_Comp_KL6159A` | competition of `Probe` / `KL6283A` |
| `Comp` (one compound in the experiment) | competition of that compound |
| `Pool`, `Bridge`, `Reference`, `Norm` | reference |
| `QC`, `HeLa`, `K562`, `Standard`, `Blank` | qc |
| anything else | compound |

A TMT condition is one word (the part before the first `_` of
`ProbeComp_1_128N`), so write a TMT competition as `ProbeComp` or `Comp`.
`Probe_pre`, `Probe_block` and `Probe_10x` are read as a competition only
next to a `Probe` condition, and Ionomos asks. Say it yourself in the
review window or the experiment editor (**Roles**), or with `roles:` in
`experiment.yaml`.

## Still to confirm with the lab

- Which words mark "probe plus competitor" in a condition name? Only `Comp` was seen on the PC (D61).

- Is user = folder under `C:\Fragpipe_General\` right? Which initials/aliases to configure?
- Are there ever two methods in one drop? (Currently rejected.)
- TMT: hand-written `annotation.txt` vs `experiment.yaml` — which do people prefer?

## Learning from confirmed experiments

When a resolver answer passes intake validation, Ionomos records the confirmed
file labels, replicates and fractions in `logs/naming-history.jsonl`. This is a
structured naming log, separate from ordinary diagnostic messages. Future drops
reuse exact filename corrections (ignoring Xcalibur acquisition timestamps) and
sample-label corrections across new replicates/fractions, scoped to the same
user and method. For example, confirming DIA `vehicle_1.raw` as `DMSO`, replicate
1, teaches `vehicle_2.raw` → `DMSO`, replicate 2. Explicit `experiment.yaml`
values take precedence. Conflicting historical examples are not reused.
This does not infer arbitrary new naming grammars or train from unconfirmed error
messages. Remove the history file to reset this memory; normal user aliases
remain in their existing learned-alias file.

## Removing unwanted inbox data

The app's **Inbox** tab lists folders and their raw files and refreshes every two
seconds. **Delete selected** moves the selected item to `inbox/.removed/` under
a unique ID. **Open removed items** lets you recover it manually. The watcher
ignores this hidden folder. This is recoverable removal, not permanent erasure.

The naming resolver also has **Delete** beside each raw file and **Delete from
inbox** for the whole folder. It refreshes its file list every half second,
including changes made in Explorer, and closes if the folder or all raws are
gone. A changed folder restarts intake and its stability checks; answers from
that changed snapshot are not queued. Previously saved GUI overrides for removed
raws are ignored. Whole-folder removal also moves its rejection note.

DIA analysis matches FragPipe's converted `_uncalibrated.mzML` and
`_calibrated.mzML` run names back to the original manifest. To correct a completed
experiment, edit its per-file `experiment` and `bioreplicate` values in
`experiment.yaml`, then use **Re-run analysis**. Missing expected runs and
comparisons with insufficient replicate data appear as report warnings; zero
tested proteins does not mean there were no biological differences.
