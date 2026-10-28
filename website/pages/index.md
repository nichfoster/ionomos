# Ionomos

**Ionomos turns a proteomics search result into a statistical report that a
bench scientist can read and a reviewer can check**: filtering,
normalisation, imputation, limma statistics, QC and enrichment, in one offline
HTML page, from FragPipe, DIA-NN, MaxQuant, Sage, Spectronaut, AlphaDIA,
MSstats-format or Proteome Discoverer tables. Its statistics are Python ports
of R tools (FragPipe-Analyst, limma, MSstatsTMT, CurveCurator and
others), each tested against that tool's own output.

## See a report first

[**Example reports**](reports/index.md): three reports Ionomos wrote from
real, public data (FragPipe-Analyst's label-free, DIA and TMT example
datasets). They open in the browser; there is nothing to install.

## Try it on your computer

Windows, macOS or Linux, Python 3.11 or newer, one dependency. Install it as
the [quickstart](quickstart.md) says, then:

```bash
ionomos demo --open                     # a simulated experiment and its report, offline
ionomos example --open                  # the real data above (a 20.9 MB download)
ionomos analyze your_table.tsv --open   # your own table or results folder
```

## Where to go

| Page | What it says |
|---|---|
| [Quickstart](quickstart.md) | Install, the demo, your own table, what the report shows |
| [The real-data example](example.md) | The data behind the example reports, and a walk through them |
| [Engines and tables](engines.md) | What Ionomos reads from DIA-NN, MaxQuant, Sage, Spectronaut, AlphaDIA, MSstats and Proteome Discoverer; an SDRF as the design; TMT across plexes |
| [How accurate it is](validation.md) | What each part is checked against, `ionomos compare` and `ionomos benchmark`, and how far to trust a result |
| [Help](help/getting_started.md) | The help every report carries: reading the report, the glossary, troubleshooting, what Ionomos never does to your data, questions |
| [Vision](vision.md) and [scope](scope.md) | Who it is for, how it compares with FragPipe-Analyst, MSstats, Perseus and others, what it is not |
| [Changelog](changelog.md) | Every release |

On a Windows instrument PC Ionomos can also run the search: lab members drop
a folder into an inbox, and it runs FragPipe (or DIA-NN, MaxQuant, Sage)
headlessly with your lab's workflows and FASTAs, then writes the report. The
methods, the file-naming rules and the analysis settings (`experiment.yaml`,
or the app's Analysis tab) are configuration, so each lab sets its own. That
install is described in
[DEPLOY_WINDOWS.md](https://github.com/nichfoster/ionomos/blob/master/docs/DEPLOY_WINDOWS.md)
on GitHub.

## Source, problems, citing

The code, the issues and every design document are on
[GitHub](https://github.com/nichfoster/ionomos). A problem, or a table it
doesn't read: [open an issue](https://github.com/nichfoster/ionomos/issues)
(attach the header row, not your data). To cite Ionomos, use
[CITATION.cff](https://github.com/nichfoster/ionomos/blob/master/CITATION.cff),
and cite the tools whose methods it ports
([README](https://github.com/nichfoster/ionomos/blob/master/README.md#license)).
Ionomos is GPL-3.0-or-later.
