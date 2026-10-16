---
name: Bug report
about: Something went wrong in a search, an analysis, the report or the app
labels: bug
---

**What happened, and what you expected**


**Where**
- [ ] `ionomos analyze` / `demo` / `compare` / `benchmark` on my own computer
- [ ] the watcher or the app on an instrument PC
- [ ] the report in the browser

**Version and system**
`ionomos --version`:
Windows / macOS / Linux, Python version:
Search engine and version (FragPipe 24.0, DIA-NN 2.x, …):

**Steps or command**
```
ionomos analyze …
```

**Diagnostics** (see docs/DEV_LOOP.md)
- Watcher or app: paste the app's **Copy diagnostics** text, or
  `ionomos diagnose --anonymise`. Names are replaced and secrets hidden; read
  it before posting.
- Analysis: the first lines of the table's header, and the "Warnings" and
  "How far to trust this" parts of the report (or `results/analysis.json`).
- If a bundle (`Report a problem…` / `ionomos bundle`) would help, say so
  here and don't attach it: the maintainer will ask where to share it. Never
  post its `KEY-keep-in-the-lab` file, or data you may not share.
