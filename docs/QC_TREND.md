# Instrument QC trending

Most labs inject a QC standard on a schedule: a HeLa or K562 digest, typically
50–200 ng, to check the LC and the mass spectrometer. Ionomos sees every drop
and runs every search, so it can trend those runs without anyone copying
numbers into a spreadsheet (ROADMAP 5C #6, D45).

After each search of a QC-standard run, Ionomos:
- adds the run's numbers to a small store
- rewrites **`qc_trend.html`** in the log folder (`paths.log_dir`, e.g.
  `C:\Fragpipe_Auto\logs\qc_trend.html`)
- when a rule is broken, puts an item on the attention list

Open the page from the app (**Jobs** tab → **Instrument QC**) or with
`ionomos qc-trend --open`. Nothing happens until a QC-standard run is
searched, so the feature is on by default.

## Which runs count

A run counts as a QC-standard run when either of these is true:
- its `.raw` name or its folder name contains one of `qc_trend.match`
- its method is listed in `qc_trend.methods`

The default `match` is `hela`, `k562`, `qc_std`, `qcstd` and `_qc_`. Matching
ignores case. `_`, `-`, `.` and spaces all count as the same separator, so
`_qc_` matches `QC` as a word (`…_QC_…`, `QC-HeLa`, `…_qc.raw`) but not
`QCtest`. Details and examples: [NAMING_CONVENTION.md](NAMING_CONVENTION.md#qc-standard-runs).

Two things stop a match:
- **An experiment is not a QC standard.** HeLa is also a cell line people
  experiment on. A folder with two or more samples, each with two or more
  replicates (`HeLa_DMSO_1…3`, `HeLa_Drug_1…3`), is left out even when a name
  matches. A dedicated QC method (`methods:`) always counts.
- **`exclude`** patterns win over everything.

Runs are grouped into **series**, and each series has its own baseline. A
series is named from these parts:
- instrument (the `instrument` setting)
- method
- standard (the matched word: HeLa, K562, QC …)
- amount, read from the name (`200ng`, `50ng`, `1ug`)

So a 50 ng HeLa and a 200 ng HeLa are never compared with each other.

## What is measured

Every number comes from the tables the search already wrote. Nothing is
recomputed from spectra.

| Metric | DIA (DIA-NN, standalone or in FragPipe) | DDA (FragPipe) | A problem when |
|---|---|---|---|
| Precursors | `report.stats.tsv` `Precursors.Identified` | – | lower |
| Proteins | `stats.tsv` `Proteins.Identified`, else protein groups > 0 in `pg_matrix.tsv` | `combined_protein.tsv` `<exp> Spectral Count` > 0 when the run is its experiment's only run, else distinct `Protein` in `psm.tsv` | lower |
| Peptides, PSMs | – | `psm.tsv`: distinct `Peptide`, rows | lower |
| Signal | `Total.Quantity` | summed PSM `Intensity` | lower (trended as log10) |
| Peak width | `FWHM.RT` (minutes) | – | higher |
| MS1 mass error | `Median.Mass.Acc.MS1` (ppm, before DIA-NN's recalibration, so drift shows) | median of `Observed Mass` vs `Calculated Peptide Mass`, ppm, isotope-error corrected (> 50 ppm counts as a mass offset and is ignored) | either way |
| MS2 mass error | `Median.Mass.Acc.MS2` | – | either way |
| RT shift | the RT of the 200 most intense precursors in `report.tsv` (Q.Value ≤ 1 %) | `Retention` of the 200 most intense peptides (seconds → minutes) | either way |
| Missed cleavages | `Average.Missed.Tryptic.Cleavages` | mean `Number of Missed Cleavages` | higher |
| Mean charge | `Average.Peptide.Charge` | mean `Charge` (the 2+/3+/… mix is kept too) | either way |

The same reader (`downstream/qcmetrics.py`) feeds the **Search quality** tab
of every experiment's report (D55, [WORKFLOWS.md](WORKFLOWS.md)): per raw
file, the mass-error quartiles, missed cleavages, charge states and peptide
length. That tab shows one experiment's runs side by side; this page follows
the QC standard over time.

**DDA searched by Sage** (`engine: sage`) is read from `results.sage.tsv`,
per file: target PSMs of rank 1 at `spectrum_q` ≤ 1 %.

| Metric | Sage `results.sage.tsv` |
|---|---|
| PSMs | rows that pass |
| Peptides | distinct `peptide` at `peptide_q` ≤ 1 % |
| Proteins | distinct `proteins` entries at `protein_q` ≤ 1 % (a count of protein sets, not of razor groups) |
| Signal | summed `ms2_intensity` (matched fragment intensity, not MS1) |
| MS1 mass error | median of `expmass` vs `calcmass`, ppm, isotope-error corrected. It is computed from the masses so that it is signed whatever a Sage version writes in `precursor_ppm` |
| RT shift | `rt` (minutes) of the 200 peptides with the most intense spectra |
| Missed cleavages, mean charge | mean `missed_cleavages`, mean `charge` |

There is no MS2 mass error from Sage: its `fragment_ppm` is an unsigned
average. The column names are from Sage's source, not from a real run.

**RT shift** is the median, over the peptides both share, of each peptide's
RT minus its median RT in the baseline runs. At least 5 shared peptides are
needed. That makes the "fixed peptide set" the standard's own most intense
peptides, so no spiked-in iRT peptides are needed.

**Acquisition time** is what orders the runs:
- the Xcalibur stamp in the file name (`…_20260930143015.raw`), if present
- else the raw file's modification time (the instrument writes the file as it
  acquires, and intake keeps the time when it moves the file)
- else when the folder was filed

The page shows which was used (hover a date).

Reading is bounded. Tables are streamed row by row. A table over
`qc_trend.max_file_mb` (default 4096) is skipped with a note, and at most 5
million rows are read from any one table. A table that can't be read leaves a
note on the run; the other metrics stand.

## How it is judged

1. **Baseline.** By default, the first `baseline_runs` runs of a series (10).
   To pin one instead, set `baseline_from` / `baseline_to`: the runs acquired
   between those dates, for example the weeks after a column change. Each
   metric's mean and standard deviation (SD) come from the baseline runs. The
   SD is at least 2 % of the mean for counts (or a small fixed floor for ppm,
   minutes and charge), so a baseline of near-identical runs can't make noise
   look alarming. Until the baseline is complete, runs say "Building the
   baseline: k of N".
2. **Levey-Jennings chart.** One per metric. Each run is plotted with the
   mean, ±1, ±2 and ±3 SD lines, and the baseline runs are shaded.
3. **Westgard rules** on each run after the baseline:

   | Rule | Broken when | Counts as |
   |---|---|---|
   | 1-2s | one run beyond 2 SD | a warning ("watch") |
   | 1-3s | one run beyond 3 SD | a rejection |
   | 2-2s | two runs in a row beyond 2 SD, same side | a rejection |
   | R-4s | two runs in a row, one above +2 SD and one below −2 SD | a rejection (imprecision) |
   | 10-x | ten runs in a row on the same side of the mean | a rejection (a shift) |
   | CUSUM | the runs since the baseline add up to more than 5 SD on one side (tabular CUSUM, k = 0.5 SD) | a slow drift |

   The CUSUM starts at the end of the baseline. Each run's z is clipped at
   ±3 before it is added, and the sums restart after a run another rule
   rejected. So one failed injection is caught by 1-3s, and doesn't read as
   drift for weeks afterwards.
4. **Direction.** Only a change in the direction that matters is a problem:
   - fewer identifications or less signal
   - broader peaks, more missed cleavages
   - any shift in mass error, RT or charge

   A change for the better (more IDs after a new column) is "watch", with a
   hint to pin a new baseline if it lasts. R-4s is a problem in either
   direction.

Each run gets a **status** and a plain-English **verdict**:

| Status | Meaning | Example verdict |
|---|---|---|
| `ok` | every metric within the baseline | All metrics within the baseline |
| `watch` | a 2 SD warning, or a change for the better | Within limits; watch: Precursors 9% below baseline (1-2s) |
| `warning` | a rule broken in the bad direction | Precursors 18% below baseline (1-3s), Proteins 15% below baseline (2-2s) — check the column and the spray, and how much was injected |
| `baseline` | part of, or still building, the baseline | Building the baseline: 4 of 10 QC runs |
| `nodata` | the search wrote no table with QC numbers for this run | No QC numbers were found for this run (…) |

The job's `ionomos.json` gets the verdict of each of its QC runs, under
`results.qc_trend`.

## When a rule is broken

If the newest run of a series has status `warning`, an attention item is
raised: kind `qc_trend`, severity warning. It lists the likely causes and
what to do:
- open the page
- fix the cause and inject the standard again
- or pin a new baseline if the change was deliberate

It appears in the app's attention list and in `ionomos attention`. **No window
pops up** unless `qc_trend.popup: true`. The next run of that series that is
back within the baseline closes the item by itself. A run with no numbers
(`nodata`) leaves it as it is.

## Where things are

| What | Where |
|---|---|
| The store | `<log_dir>/qc_trend.jsonl`: one JSON object per line, appended. A re-searched run's new line replaces its old one when read, and the file is compacted when mostly superseded. It is Ionomos' own file: experiment folders are only read. |
| The page | `<log_dir>/qc_trend.html`: self-contained (the report's stylesheet inlined, static SVG charts, no script, no network). Rewritten after each QC run, and by `ionomos qc-trend`. |
| The code | `qctrend.py` (matching, store, rules, hook), `downstream/qcmetrics.py` (reading the tables: DIA-NN, FragPipe, Sage), `downstream/qcpage.py` (the page) |

## Commands

```bash
ionomos qc-trend              # rewrite the page from the store (scans users_root first if the store is empty)
ionomos qc-trend --rebuild    # re-read every past QC run under users_root first (read-only), then the page
ionomos qc-trend --open       # ... and open it in the browser
```

`--rebuild` is how to fill the store the first time, or after changing
`match` or `exclude`. It updates the runs it finds and keeps the others, such
as experiments since archived to another drive. It does not raise or close
attention items; only a new search does.

## Settings (`config.yaml`)

```yaml
qc_trend:
  enabled: true              # inert until a QC-standard run is searched
  match: [hela, k562, qc_std, qcstd, _qc_]
  exclude: []                # e.g. [hela-ko] for a HeLa knock-out line that isn't the standard
  methods: []                # e.g. [QC]: every run of a dedicated QC method counts
  instrument: ""             # a label for the page and the series, e.g. Eclipse
  baseline_runs: 10          # the first N runs of a series set its mean and SD (at least 3)
  baseline_from: ""          # or pin the baseline: runs acquired between these dates (YYYY-MM-DD)
  baseline_to: ""
  popup: false               # true: a broken rule opens a window, as a failed search does
  # max_file_mb: 4096        # tables bigger than this are skipped
```

## Not yet

- TMT runs are not trended (a TMT QC standard is rare).
- MaxQuant searches are not trended yet (`engine: maxquant`).
- The RT shift for DIA-NN 2.x reads `report.tsv` only; with only
  `report.parquet`, the other metrics still come from `report.stats.tsv`.
- One `instrument` label per config. Runs from two instruments that share a
  watcher would share a series, unless their standard or amount differs.
  Telling instruments apart (from the raw file header, or a word in the name)
  is left for when a lab needs it.
- The thresholds (2 % SD floor, CUSUM h = 5) haven't met real lab data yet;
  tune them on the first months of real QC runs.
