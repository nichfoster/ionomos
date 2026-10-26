# Ionomos

**Ionomos turns a proteomics search result into a statistical report that a
bench scientist can read and a reviewer can check**: filtering,
normalisation, imputation, limma statistics, QC and enrichment, in one offline
HTML page, from FragPipe, DIA-NN, MaxQuant, Sage, Spectronaut, AlphaDIA,
MSstats-format or Proteome Discoverer tables. Its statistics are Python ports
of published R tools (FragPipe-Analyst, limma, MSstatsTMT, CurveCurator and
others), each tested against that tool's own output
([docs/VALIDATION.md](docs/VALIDATION.md) says how far, and what is not yet
checked on real data). On a Windows instrument PC it can also run the search:
lab members drop a folder into an inbox, and Ionomos files it, runs FragPipe
(or DIA-NN, MaxQuant, Sage) headlessly with the lab's checked workflows and
FASTAs, and writes the report.

What it is for, who it is for, how it compares with FragPipe-Analyst, MSstats
and others, and the road to publishing: [docs/VISION.md](docs/VISION.md).
Analysis only, on any computer: [docs/QUICKSTART.md](docs/QUICKSTART.md).
**Cite:** see [CITATION.cff](CITATION.cff), and cite the tools it ports
([License](#license)). Ionomos started as one lab's tool and was called
*LabWatch* up to 0.4.0; existing installs upgrade in place.

## On the lab PC

The watcher, as it runs in the lab where it started: lab members drag an
experiment folder into an inbox; Ionomos files it into the owner's directory,
runs FragPipe (isoDTB / TMT / DIA) headlessly, reproduces the lab's R
post-processing, and writes statistics, volcano plots and a report.

**Install / update / report a problem / uninstall on the PC:** see the table
at the top of [docs/DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md) — it's one
installer, one button, one button, and Settings → Apps.

```
Eclipse PC                    shared folder                 Proteomics PC
┌──────────────┐   ethernet   ┌──────────────────┐  drag   ┌─────────────────────────────┐
│ instrument   │ ──────────▶  │ Proteomics_File_ │ ──────▶ │ C:\Fragpipe_Auto\inbox\     │
│ writes .raw  │  user copies │ Sharing\         │  user   │   └─ <named experiment dir> │
└──────────────┘              └──────────────────┘         │            │ ionomos        │
                                                           │            ▼                 │
                                                           │ C:\Fragpipe_General\<user>\  │
                                                           │   └─ <experiment>\           │
                                                           │        ├─ raw\               │
                                                           │        ├─ fragpipe\  (out)   │
                                                           │        └─ ionomos.json      │
                                                           │            │                 │
                                                           │            ▼                 │
                                                           │ FragPipe headless → post-proc│
                                                           └─────────────────────────────┘
```

## Status

**Version 0.18.1 (2026-10-06); beta.** The watcher, the headless searches,
the analysis and the app are built and tested in CI on Windows and Linux. On
the lab PC, real DIA searches and analyses have run ([docs/REAL_RUNS.md](docs/REAL_RUNS.md));
no real isoDTB or TMT search has run under Ionomos yet, and no real-data
benchmark exists: every accuracy number is from simulated data or from R on
shared inputs ([docs/VALIDATION.md](docs/VALIDATION.md)). Not yet on PyPI
(install from GitHub, below). What is built, what is next and what needs the
lab: [docs/ROADMAP.md](docs/ROADMAP.md).

## Layout

| Path | What |
|---|---|
| [`docs/`](docs/) | Design docs — read these first |
| [`ionomos/`](ionomos/) | The watcher package (Python 3.11+, installed on the proteomics PC) |
| [`deploy/`](deploy/) | `dev_install.ps1` (PC runs from a git clone; updates = one button), `build_exe.ps1` (PyInstaller → `Ionomos.exe`, also run by GitHub Actions), `install.ps1` fallback |
| [`.github/workflows/`](.github/workflows/) | CI: tests on Linux + Windows every push; exe build + Release on `v*` tags; the pip package built and tried on Windows / macOS / Linux on `v*` tags (uploads to PyPI once trusted publishing is set up) |
| [`CITATION.cff`](CITATION.cff), [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/) | How to cite, contribute and report a problem |
| [`scripts/`](scripts/) | One-shot dev/test setup for macOS and Windows |
| [`tools/inventory/`](tools/inventory/) | PowerShell inventory collector to run on the lab PC (fixed version) |
| [`reference/pc-inventory/`](reference/pc-inventory/) | Raw output of the inventory runs (2026-09-15) |
| [`reference/lab-sops/`](reference/lab-sops/) | The lab's how-to docs for isoDTB and TMT in FragPipe (+ extracted text) |
| [`reference/lab-scripts/`](reference/lab-scripts/) | The R post-processing scripts ionomos will replace/wrap |
| [`reference/prior-work/proteomics-qc-pkg/`](reference/prior-work/proteomics-qc-pkg/) | Earlier QC-trending pipeline; its FragPipe runner and stability watcher are reused |

## Docs index

| Doc | Purpose |
|---|---|
| [HELP.md](docs/HELP.md) | The help lab members see (every report's **?** and Help section, the app's Help button, `ionomos help`): where it lives and how to edit it |
| [ASSISTANT.md](docs/ASSISTANT.md) | The local assistant (`ionomos ask`): what it reads, how answers are checked against their sources, how to set it up, and what is and is not tested yet |
| [VISION.md](docs/VISION.md) | What Ionomos is for and who it is for, its principles (with decision numbers), what it is not, how it compares with FragPipe-Analyst, MSstats, Perseus and others, the road to publishing, and what v1.0 means |
| [QUICKSTART.md](docs/QUICKSTART.md) | **New here?** `pip install ionomos`, `ionomos demo`, then `ionomos analyze` on your own table: what it reads and what the report shows |
| [EXAMPLE.md](docs/EXAMPLE.md) | The real-data example (`ionomos example`): FragPipe-Analyst's published LFQ, DIA and TMT datasets downloaded from Zenodo (CC BY 4.0) and analysed; a walk through the three reports, and what Ionomos does differently |
| [SCOPE.md](docs/SCOPE.md) | Problem statement, in/out of scope today and what moved, users, constraints, and the v1 lab plan it started from |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, job state machine, on-disk layout |
| [NAMING_CONVENTION.md](docs/NAMING_CONVENTION.md) | The folder/file naming spec users must follow + the optional `experiment.yaml` |
| [WORKFLOWS.md](docs/WORKFLOWS.md) | What isoDTB, TMT and DIA runs each need (from the lab SOPs) and how they map to headless FragPipe |
| [PROTEOMICS_PC.md](docs/PROTEOMICS_PC.md) | Facts about the target machine from the inventory, and what's still unknown |
| [DECISIONS.md](docs/DECISIONS.md) | Decision log (why polling, why folder-level, why Python not R, …) |
| [ROADMAP.md](docs/ROADMAP.md) | Phased build plan and open questions to resolve with the lab |
| [DEV_LOOP.md](docs/DEV_LOOP.md) | **Start here for prototyping:** Mac ↔ GitHub ↔ PC loop, updates and diagnostics; the anonymised **bundle** the lab saves for troubleshooting and validation, and how to inspect, unpack and re-analyse it |
| [DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md) | Step-by-step install of the finished tool on the proteomics PC |
| [TESTING.md](docs/TESTING.md) | Test suite + testbed on macOS and Windows |
| [VALIDATION.md](docs/VALIDATION.md) | How accurate the analysis is and how to check it: what it is verified against, `ionomos compare` (against a FragPipe-Analyst, limma, MSstats, Perseus or R result), `ionomos benchmark` (simulated data, or a mixed-species run on the instrument), messy tables, and the "How far to trust this" list |
| [FIRST_REAL_RUN.md](docs/FIRST_REAL_RUN.md) | The checklist for the first real FragPipe runs on the PC: `ionomos preflight`, a small isoDTB search, what to send back |
| [REAL_RUNS.md](docs/REAL_RUNS.md) | What has actually run on the PC (searches, analyses, a FragPipe-Analyst comparison), what went wrong, and what each problem became |
| [QC_TREND.md](docs/QC_TREND.md) | Instrument QC: how runs of the lab's QC standard (HeLa, K562) are recognised, measured, judged (Levey-Jennings, Westgard rules) and shown in `logs/qc_trend.html` |
| [ENGINES.md](docs/ENGINES.md) | Results from other engines Ionomos can analyse (DIA-NN, MaxQuant, Sage, Spectronaut, AlphaDIA, MSstats / MSstatsTMT format, Proteome Discoverer) and what it reads from each; an SDRF as the design; TMT across plexes |
| [LIBRARY.md](docs/LIBRARY.md) | The workflow and FASTA library (`ionomos library`): where it looks on the PC, what it says about each workflow and FASTA, how it ranks them per method and pins the chosen ones |
| [UI.md](docs/UI.md) | The app's UI: its rules, every tab and window and who uses it, what was seen on screen, and the plan for daily driving (#109–#132) |
| [FASTA.md](docs/FASTA.md) | Getting, preparing and refreshing FASTAs (`ionomos fasta`): UniProt downloads, decoys and contaminants, how old a FASTA is |
| [ATLAS.md](docs/ATLAS.md) | The hand-off of finished isoDTB (and TMT) searches to the lab's cysteine atlas (`ionomos atlas`): the contract both sides implement, word for word, what is copied and how (with the decoys' `.pin` files and the sample design), the states a hand-off goes through, the drive sweep that sends the PC's other FragPipe runs (`ionomos atlas sweep`), and the atlas's site lookup in the report |
| [SECURITY_NOTES.md](docs/SECURITY_NOTES.md) | The threat model, per component: what each part that faces an inbox, a drop, an engine's output, a bundle, a webhook or the network protects against, what it does not, and the test that attacks it (reporting a problem: SECURITY.md) |

## Try the analysis on any computer

```bash
pip install ionomos        # until it is on PyPI: pip install "git+https://github.com/nichfoster/ionomos#subdirectory=ionomos"
ionomos demo --open        # a simulated experiment and its report, offline
ionomos example --open     # real published data (20.9 MB download): label-free, DIA and TMT reports
ionomos analyze path/to/your/table.tsv --open
ionomos compare path/to/experiment old_result.tsv   # does it agree with your other analysis?
ionomos benchmark --grid quick     # sensitivity and false discoveries on simulated data
```

Windows, macOS or Linux; no lab setup, no Tk. See [docs/QUICKSTART.md](docs/QUICKSTART.md).

## Commands

`ionomos --help` lists every command with its options; `ionomos <command> --help` the details.

- **Any computer** (the pip install): `demo`, `example`, `analyze`, `check-folder`, `export`, `compare`,
  `benchmark`, `spectronaut-columns`, `help`.
- **The lab PC**: `setup` (the app; also what a bare `ionomos` opens), `init`, `check`, `doctor`, `preflight`, `run`,
  `stop`, `status`, `attention`, `dry-run`, `names test`, `retry`, `cancel`, `pause`, `resume`,
  `repair-ledger`, `qc-trend`, `library`, `fasta`, `tmt`, `notify-test`, `diagnose`, `bundle`, `ask`,
  `ask-eval`, `update`, `atlas`.
- **Testing**: `testbed` (a fake lab with a fake FragPipe; docs/TESTING.md).

## Quick start (dev, on this Mac)

```bash
scripts/test_mac.sh --bed        # venv + lint + tests + a fake lab in ./ionomos-testbed
ionomos/.venv/bin/ionomos setup   # the app, pointed at any config you like
```

On the PC: [docs/DEV_LOOP.md](docs/DEV_LOOP.md) while prototyping,
[docs/DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md) for the finished tool.

## Versioning and support

What Ionomos promises from 1.0 (the full text:
[docs/VISION.md](docs/VISION.md#versioning-and-support-from-10)):

- **Semantic versioning of what you touch:** the command line (commands and
  options), the `config.yaml` and `experiment.yaml` keys, the files in
  `results/` and the fields of `analysis.json`. Python module internals are
  not an API. A key or option that goes away warns for at least one minor
  release first (`ionomos check` and the analysis' notes say so already). The
  LabWatch-era names keep working.
- **Python versions:** those CI runs (today 3.11 to 3.14) on Windows and
  Linux; the package is also tried on macOS on each tag.
- **Results don't change silently:** a change in a default or a numerical
  method that changes results is a minor version at least, with a CHANGELOG
  entry under "Changed" and a decision, and `ionomos compare` can show the
  difference against the previous version's result.
- **Support:** GitHub issues, answered by one maintainer as time allows, with
  no guaranteed response time. A report with the diagnostics text or a bundle
  ([docs/DEV_LOOP.md](docs/DEV_LOOP.md)) can be reproduced; one without
  usually cannot.
- **The watcher is Windows-only and FragPipe-first.** The analysis is
  supported on Windows, macOS and Linux.

## Cite

[CITATION.cff](CITATION.cff) (GitHub's "Cite this repository" reads it).
There is no paper yet ([docs/VISION.md](docs/VISION.md#the-road-to-publishing)).
Please also cite the tools whose methods Ionomos ports, below.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md): the dev loop, tests, and why the docs and
the decision log come with every change. [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)). The analysis in
`ionomos/src/ionomos/downstream/fpa.py`, `qc.py` and `enrich.py` is a Python
translation of [FragPipeAnalystR](https://github.com/Nesvilab/FragPipeAnalystR)
and [FragPipe-Analyst](https://github.com/MonashProteomics/FragPipe-Analyst)
(GPL-3); please cite Hsiao et al., *J. Proteome Res.* 2024,
doi:10.1021/acs.jproteome.4c00294, and limma (Ritchie et al., *Nucleic Acids Res.* 2015).
The dose-response curves in `downstream/doseresponse.py` are a Python
translation of [CurveCurator](https://github.com/kusterlab/curve_curator)
(Apache-2.0, © 2023 Florian P. Bayer; the optimiser was changed from scipy's
L-BFGS-B to a pure-Python Levenberg-Marquardt, and q-values / pEC50 intervals
were added); please cite Bayer et al., *Nat. Commun.* 14, 7902 (2023),
doi:10.1038/s41467-023-43696-z.
