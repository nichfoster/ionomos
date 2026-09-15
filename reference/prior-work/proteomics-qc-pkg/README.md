# Proteomics QC pipeline

Automated FragPipe QC: a new `.raw` file dropped into a watched folder is
searched with a pinned FragPipe workflow, parsed for QC metrics, stored, and
shown on a trending dashboard.

```
incoming/dda/ ─┐
incoming/dia/ ─┴─→ watcher → queue → FragPipe → parse → SQLite → dashboard
```

## What it tracks

- Protein and peptide counts per run, trended over time (DDA and DIA separately)
- For 2–5 configured monitor peptides: intensity and retention time per run,
  with runs where a peptide was **not detected** flagged distinctly

## Setup (Windows workstation)

1. Install Python 3.11+ and `pip install -r requirements.txt`.
2. In the FragPipe GUI, build your two QC workflows. **Set the FASTA database
   (with decoys) before saving**, then use *Save to custom folder*. Save one
   DDA and one DIA (DIA-NN) workflow.
3. Edit `config.yaml`: set every path under `paths:`, and put your real
   monitor-peptide sequences under `monitor_peptides:`.
4. Create the two incoming folders and the output/results folder.

## Run

Pipeline (watcher + worker), in one terminal — or via `run_qc.bat`:
```
python main.py
```
Dashboard, in a second terminal:
```
streamlit run dashboard.py
```

To start the pipeline automatically, point Windows Task Scheduler at
`run_qc.bat` (run at log on).

## ⚠️ Confirm-on-your-install checklist (do this first)

The pipeline is written to documented current FragPipe/DIA-NN formats, but
these are version-sensitive. Verify against one real run before trusting trends.
Search the code for `CONFIRM-ON-YOUR-INSTALL`:

- **`fragpipe_runner.py`** — launcher name (`fragpipe.bat`) and, only if the
  first headless run can't find its tools, the optional `config_tools_folder`
  / `config_diann` paths in `config.yaml`.
- **`parsers/dda_parser.py`** — the `psm.tsv` column names
  (`Peptide`, `Charge`, `Intensity`, `Retention`). The parser raises a clear
  error naming any mismatched column, so a wrong name fails loudly rather than
  silently reporting peptides as absent.
- **`parsers/dia_parser.py`** — the DIA-NN `report.tsv` column names and the
  report file name. Also confirm the RT unit (min vs sec) so the dashboard
  axis label is right.

A good first test: point each parser at one existing FragPipe output folder
(no live search needed) and confirm counts and monitor peptides come out right.

## Files

| File | Role |
|---|---|
| `config.yaml` | All paths, watcher timing, monitor peptides |
| `main.py` / `run_qc.bat` | Entry point + Windows launcher |
| `watcher.py` | Folder monitoring, size-stability, folder→acquisition routing |
| `queue_worker.py` | Sequential job queue: run → parse → store |
| `fragpipe_runner.py` | Manifest builder + headless FragPipe invocation |
| `parsers/` | DDA and DIA parsers → one normalized `RunRecord` |
| `store.py` | SQLite storage |
| `dashboard.py` | Streamlit trending dashboard |

## Design notes

- **Sequential by design.** One search at a time. At your load (≤2/day) this is
  the right engineering, not a compromise.
- **Identified-only.** Monitor-peptide numbers come straight from FragPipe
  output. A not-detected peptide is recorded honestly (a `detected=0` row), not
  zeroed or dropped — so the dashboard can flag it.
- **Flexible monitor list.** Change `monitor_peptides` anytime; results are
  stored per-sequence, so the set can evolve without breaking history or schema.
- **Failures are visible.** A failed search writes a `status='failed'` row.
