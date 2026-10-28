# Vision: what Ionomos is for

Written 2026-10-15; updated 2026-10-28 at version 0.21.0. Ionomos began in
2026-09 as LabWatch, one lab's drop-folder automation for FragPipe on a
Windows PC ([SCOPE.md](SCOPE.md), "Where it started"). It is now a tool any
lab can install, and its analysis is something anyone can `pip install`. This
page says what that tool is, who it is for, what it promises, and what is left
before 1.0. [ROADMAP.md](ROADMAP.md) Phase 5D holds the checklist.

## In one paragraph

Ionomos turns a proteomics search result into a statistical report that a
bench scientist can read and a reviewer can check: filtering, normalisation,
imputation, limma statistics, sample QC and enrichment, in one offline HTML
page, from FragPipe, DIA-NN, MaxQuant, Sage, Spectronaut, AlphaDIA,
MSstats-format or Proteome Discoverer tables. Its statistics are Python ports
of published R tools (FragPipe-Analyst, limma, MSstatsTMT, MSstatsPTM,
CurveCurator and others), each tested against that tool's own output;
[VALIDATION.md](VALIDATION.md) lists which, and what has not yet been checked
on real data. On a Windows instrument PC it can also do the step before: run
FragPipe (or DIA-NN, MaxQuant, Sage) on folders that lab members drop into an
inbox, with the lab's workflows and FASTAs found, checked and kept current, so
nobody sets up a search by hand. Which methods a lab runs, what its files are
called and how each experiment is analysed are settings, not code (D37, D54;
`experiment.yaml` and the app's Analysis tab).

## Who it is for

| Who | What they do with it | Where to start |
|---|---|---|
| **The lab that runs the PC** (the maintainer) | Installs the watcher on the proteomics PC, pins the lab's workflows and FASTAs, reads the attention list and the instrument QC trend | [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md), [LIBRARY.md](LIBRARY.md), [FASTA.md](FASTA.md), [QC_TREND.md](QC_TREND.md) |
| **A lab member who only drops folders** | Names a folder, drags it into the inbox, opens `results/report.html` when the notification arrives. Never opens a terminal | [NAMING_CONVENTION.md](NAMING_CONVENTION.md), the report's **?** buttons ([HELP.md](HELP.md)) |
| **A core facility** | Runs searches for many groups and hands each a report with its methods, provenance and an SDRF sample sheet; trends the QC standard | [ENGINES.md](ENGINES.md), [QC_TREND.md](QC_TREND.md) |
| **A bench scientist with a table and no bioinformatician** | `pip install ionomos`, `ionomos analyze table.tsv --open`; figures for slides with `ionomos export` | [QUICKSTART.md](QUICKSTART.md) |
| **Another lab that wants the watcher** | Describes its own file names in `config.yaml` (D37) and its own methods (D54), runs the engines it has licences for | [NAMING_CONVENTION.md](NAMING_CONVENTION.md), [WORKFLOWS.md](WORKFLOWS.md) |
| **Someone checking another group's analysis** | Re-runs the analysis from the deposited tables, or checks the authors' result with `ionomos compare` and the report's "How far to trust this" list | [VALIDATION.md](VALIDATION.md) |

## Principles, and where each was decided

Each of these is already a rule in the code and the decision log; this page
only collects them.

- **Plain words.** Help, pop-ups and the report speak to lab members, not
  bioinformaticians. Every issue code, held search and rejected folder has a
  help entry, and a test fails when one is missing (D27, D46). The docs use
  the same voice.
- **Nothing is destroyed.** Intake moves, never copies and deletes; a copy
  across drives is hash-verified before the source goes; a failure leaves
  everything where it was (D4, D17, D29). Results go into a folder Ionomos
  owns (D33). The demo writes only into new folders (D40). A support bundle
  is saved, never sent (D63).
- **Every analysis is checked against a reference.** Each statistical piece
  is a port of a named R or Python tool and is tested against that tool's
  output on shared inputs (D18, D24, D42–D44, D66, D70, D76, D77, D79). A lab
  can check its own experiment against another result (`ionomos compare`)
  and measure the method on known truth (`ionomos benchmark`) (D60).
- **The help is in the report.** Every report carries its own help, glossary
  and troubleshooting, offline (D35, D46).
- **Decisions are logged.** Every choice with a reason is an entry in
  [DECISIONS.md](DECISIONS.md), reversed only by a newer entry. Agents open
  pull requests and a person merges them (D31).
- **No hidden magic.** The analysis always finishes and says what it
  repaired (D28, D60). P-values are never invented for groups too small to
  test (D32). Unknown sample metadata stays "not available" (D38). "How far
  to trust this" is a list of checks with their numbers, not a score (D60).
  The summaries are templated and deterministic (ROADMAP 5C). The local
  assistant cites its sources and can only propose a change that a person
  confirms (D49, D57, D75).
- **Licence-clean and local.** Ionomos never bundles an engine or a database
  whose licence forbids it; each lab downloads and accepts its own (D36, D79).
  The analysis is pure Python with one dependency, so it installs anywhere
  (D18, D40).

## What it is not

- **Not a search engine.** FragPipe (MSFragger, IonQuant, TMT-Integrator),
  DIA-NN, MaxQuant and Sage identify and quantify; Ionomos runs them and
  reads what they write (D6, D36).
- **Not a LIMS.** It keeps a job record next to each experiment and a ledger
  that can be rebuilt from those records (D8, D17). It does not track
  samples, projects, billing or users' permissions.
- **Not a replacement for FragPipe-Analyst.** Its core analysis is a checked
  port of FragPipe-Analyst / FragPipeAnalystR (D24), and its results can be
  compared with a FragPipe-Analyst export. FragPipe-Analyst remains the
  reference implementation, maintained by FragPipe's authors; cite it when
  you use Ionomos ([README.md](../README.md#license)).
- **Not cloud.** There is no server and no account. Data stays on the
  computer. The network is used only for named downloads a person starts or
  switches on: Enrichr gene-set libraries, UniProt FASTAs and FragPipe's stock
  workflows, updates from GitHub, and notifications when the lab turns them
  on (D26, D58, D90). A cloud model for the assistant is refused in the
  current code (D57).
- **Not a general statistics package.** It covers the designs proteomics
  experiments use (D42, D53, D77) and says when a design is outside them.

## How it compares

Fair to the others means saying what each does that Ionomos does not. All of
these are mature, published and more widely used than Ionomos.

| Tool | What it is | What it does that Ionomos does not | Where Ionomos differs |
|---|---|---|---|
| **FragPipe-Analyst** (Hsiao et al. 2024) | R Shiny app, hosted and local; FragPipeAnalystR package | A hosted web server (nothing to install); the reference implementation of the analysis Ionomos ports; maintained with FragPipe itself | A port of its analysis, tested against FragPipeAnalystR 1.1.1 to 1e-8; an offline single-file report; other engines' tables; blocks, covariates, time courses and dose-response (D42, D44, D53) |
| **MSstats** (Choi et al. 2014; MSstatsTMT, MSstatsPTM) | R / Bioconductor | Feature-level linear mixed models from precursor data; converters for most tools; sample-size planning; a large user base | Reads MSstats-format input and ports MSstatsTMT's summary and MSstatsPTM's adjustment; the model is limma's on protein values, not MSstats' mixed model |
| **Perseus** (Tyanova et al. 2016) | Windows GUI, MaxQuant ecosystem | Interactive matrix workflows; a very wide range of analyses (clustering, networks, PTM, plug-ins) | Fewer analyses, run automatically and the same way every time; Perseus-type imputation is one of its options; reads Perseus results in `compare` |
| **Prostar / DAPAR** (Wieczorek et al. 2017) | R / Bioconductor, Shiny | Peptide-level processing; imputation by missing-value type; FDR calibration plots (CP4P) | Protein-level; imputation follows FragPipe-Analyst's options; calibration is measured by `ionomos benchmark` on simulated data |
| **DEP** (Zhang et al. 2018) | R / Bioconductor | A scriptable R workflow and Shiny apps; LFQ-Analyst, FragPipe-Analyst's predecessor, was built on it | No R; the same kind of filter → impute → limma path, plus the report and the checks |
| **MS-DAP** (Koopmans et al. 2023) | R package, Docker | Peptide-level analysis; several DEA methods side by side (MSqRob, MS-EmpiRe, DEqMS, limma); an extensive QC PDF | One DEA path (limma, optional DEqMS); provenance and an audit list in a similar spirit; no container needed |
| **quantms** (Dai et al. 2024) | nf-core Nextflow pipeline | Search and quantification with open engines from raw files; scales on HPC and cloud; PRIDE reanalysis | Runs on one Windows PC with the lab's own engine; starts from a finished search when used as `ionomos analyze`; reads quantms' MSstats output |
| **limpa** (Smyth lab, 2025) | R / Bioconductor | Models missing values with a detection-probability curve instead of imputing; precursor-to-protein summarisation with uncertainty weights | Imputes or leaves out missing values (FragPipe-Analyst's way); limpa is on the roadmap, not built (ROADMAP 5C #4) |

What only Ionomos combines, as far as the 2026-09-30 search found (D36):
the watcher on a Windows instrument PC, the lab's own licensed engine, and a
report that carries its own help, its checks and its provenance. Its niche is
chemoproteomics (isoDTB sites, liganded cysteines, competition experiments,
dose-response; D52, D61, D44), which none of the tools above automates end to
end (ROADMAP Phase 5, "Positioning").

## Versioning and support, from 1.0

- **Semantic versioning** of what users touch: the command line (commands
  and options), `config.yaml` and `experiment.yaml` keys, the files in
  `results/` and the fields of `analysis.json`. Python module internals are
  not an API. A key or option that goes away warns for at least one minor
  release first. The LabWatch-era names keep working (D21).
- **Python versions:** those CI runs (today 3.11 to 3.14) on Windows
  and Linux; the package is also tried on macOS on each tag.
- **Results don't change silently.** A change in a default or a numerical
  method that changes results is a minor version at least, has a CHANGELOG
  entry under "Changed" and a decision, and `compare` can show the
  difference against the previous version's result.
- **Support:** GitHub issues, answered by one maintainer as time allows. No
  guaranteed response time. A bug report with a diagnostics text or a bundle
  ([DEV_LOOP.md](DEV_LOOP.md)) can be reproduced; one without usually
  cannot.
- **The watcher is Windows-only and FragPipe-first.** The analysis is
  supported on Windows, macOS and Linux.

## What "done for v1.0" means

- `pip install ionomos` from PyPI, and the Windows installer, both from the
  same tag. The installer is built on every `v*` tag; on 2026-10-28
  `ionomos` is not on PyPI yet (ROADMAP 5D step 1).
- A real-data example from PRIDE with its report on the documentation site.
  The site is built (`website/`, D132) and goes live once GitHub Pages is
  switched on (ROADMAP 5D step 7).
- At least one real mixed-species benchmark run and written up in
  VALIDATION.md, and the imputation default decided from it (ROADMAP 5C #10).
- One `ionomos compare` against a real FragPipe-Analyst export, one against a
  real MSstats or Perseus export. A first real TMT result was compared by hand
  with its FragPipe-Analyst results table on 2026-10-28 (log2 fold changes
  identical, 134 of FragPipe-Analyst's 143 hits shared; [REAL_RUNS.md](REAL_RUNS.md)).
- Real TMT and isoDTB searches through the watcher on the lab PC, recorded in
  REAL_RUNS.md. TMT, DIA and DDA searches have run through the watcher
  (reported by the maintainer on 2026-10-28; their run records are still to
  be filed). An isoDTB search through the watcher is still to be recorded.
- At least one lab other than the first using it, on its own names and
  engines, and its problems fixed.
- The promises above written into the README, and every open "Not verified"
  line in VALIDATION.md either verified or stated in the report's "How far to
  trust this". Done 2026-10-26 (D133): the README's "Versioning and
  support", with `retired.py` for the warning before a key goes; each open
  line of VALIDATION.md carries its status (stated in the report, with the
  key that says it, or why it is not a report caveat). A new open line needs
  its status too.
- CONTRIBUTING, a code of conduct, issue templates and CITATION.cff in the
  repository (added 2026-10-15).

Not needed for 1.0: more engines, limpa, the assistant with a real model,
translations of the help.
