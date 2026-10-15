# Vision: what Ionomos is for, and the road to publishing it

Written 2026-10-15, at version 0.18.1 (plus the unreleased workflow / FASTA
library and TMT work). Ionomos began in 2026-09 as LabWatch, one lab's
drop-folder automation for FragPipe on a Windows PC ([SCOPE.md](SCOPE.md),
"Where it started"). It is now also an analysis anyone can `pip install`, and
the plan is to publish it as a tool for other labs. This page says what that
tool is, who it is for, what it promises, and what is left before a 1.0 and a
paper. [ROADMAP.md](ROADMAP.md) Phase 5D holds the checklist.

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
nobody sets up a search by hand.

## Who it is for

| Who | What they do with it | Where to start |
|---|---|---|
| **The lab that runs the PC** (the maintainer) | Installs the watcher on the proteomics PC, pins the lab's workflows and FASTAs, reads the attention list and the instrument QC trend | [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md), [LIBRARY.md](LIBRARY.md), [FASTA.md](FASTA.md), [QC_TREND.md](QC_TREND.md) |
| **A lab member who only drops folders** | Names a folder, drags it into the inbox, opens `results/report.html` when the notification arrives. Never opens a terminal | [NAMING_CONVENTION.md](NAMING_CONVENTION.md), the report's **?** buttons ([HELP.md](HELP.md)) |
| **A core facility** | Runs searches for many groups and hands each a report with its methods, provenance and an SDRF sample sheet; trends the QC standard | [ENGINES.md](ENGINES.md), [QC_TREND.md](QC_TREND.md) |
| **A bench scientist with a table and no bioinformatician** | `pip install ionomos`, `ionomos analyze table.tsv --open`; figures for slides with `ionomos export` | [QUICKSTART.md](QUICKSTART.md) |
| **Another lab that wants the watcher** | Describes its own file names in `config.yaml` (D37) and its own methods (D54), runs the engines it has licences for | [NAMING_CONVENTION.md](NAMING_CONVENTION.md), [WORKFLOWS.md](WORKFLOWS.md) |
| **A reviewer checking a paper's analysis** | Re-runs the analysis from the deposited tables, or checks the authors' result with `ionomos compare` and the report's "How far to trust this" list | [VALIDATION.md](VALIDATION.md) |

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

Citations above are the tools' main papers; check them before quoting in a
manuscript.

## The road to publishing

In order. ROADMAP 5D has each step with an owner.

1. **A PyPI release.** The workflow exists and builds and tries the package
   on Windows, macOS and Linux for every `v*` tag (`publish-pypi.yml`); it
   uploads once the maintainer sets up trusted publishing (D40). On
   2026-10-15 `ionomos` is still not on PyPI.
2. **A real-data example.** The demo is simulated (D40). A public dataset
   from PRIDE, analysed and published with its report, shows what a real
   table looks like in Ionomos. The best candidate is a mixed-species
   benchmark (human / yeast / *E. coli* in known ratios), because it serves
   the benchmark too ([VALIDATION.md](VALIDATION.md), "A real benchmark
   sample"). Its tables are downloaded, not bundled: the demo stays offline
   and small.
3. **The evidence.** What a paper can show today: the parity table in
   VALIDATION.md (limma, FragPipeAnalystR, MSstatsTMT, MSstatsPTM,
   CurveCurator, iq, KSEAapp, R's splines; 1e-8 or closer), the simulated
   benchmarks, and the fuzz of messy tables. What it cannot show yet, and
   needs: a real mixed-species benchmark, an `ionomos compare` against a real
   FragPipe-Analyst export and a real MSstats or Perseus export, and real
   TMT and isoDTB searches through the watcher (REAL_RUNS.md).
4. **A JOSS paper.** JOSS (criteria read 2026-10-15) asks for: an OSI
   licence (GPL-3.0-or-later: done), a public repository with issues
   (done), an automated install (pip: done once on PyPI), tests and CI
   (done), documentation with a statement of need, examples and community
   guidelines (CONTRIBUTING.md, issue templates), a repository public for
   **more than six months** with development across that time (created
   2026-09-16, so not before 2027-03-17), **evidence of research use** (a
   preprint, a paper or documented adoption; plans do not count), and a
   `paper.md` of 750–1,750 words with the sections Summary, Statement of
   need, State of the field, Software design, Research impact statement and
   **AI usage disclosure**. Most of the code was written by coding agents
   under review (D31); the disclosure must say so and say how the output was
   checked (the reference tests, human-merged pull requests).
5. **A preprint and a J. Proteome Res. technical note.** A technical note is
   at most 4,500 words, must show an advantage over existing methods, and
   needs the raw data deposited (ProteomeXchange preferred). The
   mixed-species benchmark and one real experiment from a pilot lab are the
   material. A bioRxiv preprint goes up when the note is submitted.
6. **A documentation site.** Today the docs are Markdown on GitHub. A site
   (GitHub Pages) should hold QUICKSTART, ENGINES, VALIDATION, the help
   pages that `ionomos help` already renders, and the real-data example's
   report, so a reader can see a report before installing.
7. **Name and logo.** Keep "Ionomos" unless a clash is found (the PyPI name
   was free on 2026-09-30); check for trademark and tool-name clashes before
   the paper. There is no logo; the app and the report use none. The GitHub
   description still says the tool is for one lab's PC.
8. **Versioning and support promises** (below), written down before 1.0.

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
  same tag.
- A real-data example from PRIDE with its report on the documentation site.
- At least one real mixed-species benchmark run and published in
  VALIDATION.md, and the imputation default decided from it (ROADMAP 5C #10).
- One `ionomos compare` against a real FragPipe-Analyst export, one against a
  real MSstats or Perseus export.
- Real TMT and isoDTB searches through the watcher on the lab PC, recorded in
  REAL_RUNS.md.
- At least one lab other than the first using it, on its own names and
  engines, and its problems fixed.
- The promises above written into the README, and every open "Not verified"
  line in VALIDATION.md either verified or stated in the report's "How far to
  trust this".
- CONTRIBUTING, a code of conduct, issue templates and CITATION.cff in the
  repository (added 2026-10-15).

Not needed for 1.0: more engines, limpa, the assistant with a real model,
translations of the help.
