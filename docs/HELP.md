# The help for users: where it lives and how to edit it

Ionomos has one set of help texts for the people who use it (lab members, not
bioinformaticians). They are shown in three places:

| Where | What people see | How it's made |
|---|---|---|
| **Every report** (`results/report.html`) | **Help** in the top bar; a **?** beside each section title, each QC tab, the cut-offs, the p-value histogram and each issue box, opening its text in place; a Help section at the end with every report topic, the glossary and the issues found in *that* report | `report.py` embeds `help.report_payload(issues)`; `report.js` (the "help" block) draws it. Offline, inside the one file |
| **The help page** (`help.html`) | Everything: getting started, reading the report, glossary, troubleshooting, what Ionomos never does to your data, questions. A search box filters the topics | `help.page()`. The app's **Help** button (bottom bar and Help tab), **More help** in each pop-up (opens at the topic that explains it), `ionomos help --open` |
| **The terminal** | `ionomos help NO_TABLE`, `ionomos help pca`, `ionomos help glossary` print one topic as plain text | `help.text()` |

`help.html` is written to `<log_dir>/help/` on the lab PC (the app-data folder
when there is no lab config, e.g. after `pip install`) and opened from there.
It loads nothing from the internet.

## The files

The content is plain Markdown in
[`ionomos/src/ionomos/help/`](../ionomos/src/ionomos/help/), one file per
section, in page order:

| File | Section id | What it covers |
|---|---|---|
| `getting_started.md` | `start` | naming a folder and its raw files, the inbox, the review window, where results go, DONE / FAILED / REJECTED notes, pop-ups, analysing a table anywhere |
| `reading.md` | `report` | the report, section by section and chart by chart (`report.*`), and each QC tab (`qc.<tab>`, the keys of `QC_TABS` in report.js) |
| `glossary.md` | `glossary` | the words, in plain language (`glossary.*`) |
| `troubleshooting.md` | `trouble` | attention kinds (`attention.<kind>`), intake rejections (`intake.<kind>`), held searches (`search.hold-*`), failed searches, every doctor issue code (`issue.<CODE>`) |
| `safety.md` | `safety` | what Ionomos will never do to your data |
| `faq.md` | `faq` | re-run with other settings, change a condition, leave a sample out, slides, sharing, FragPipe-Analyst, low confidence, … |

## Writing an entry

```markdown
## Only in one condition {#report.onoff}

Features measured in at least 75% of one group's samples and **never** in
the other. See [imputation](#glossary.imputation) and
[a web page](https://example.org).

- a list item
  that continues on an indented line
- another
```

- Each file starts with `# Section title {#section-id}`, then an optional
  intro, then entries. Every heading needs its `{#id}`.
- The id's first part says where the entry is used: `report.`, `qc.` and
  `glossary.` entries go into every report; `issue.CODE` explains a doctor
  issue; `attention.kind` a pop-up; the rest is for the help page.
- Supported: paragraphs, `- ` and `1. ` lists (continue an item on a line
  indented by two spaces), `**bold**`, `*italic*`, `` `code` ``, links to another
  entry `[text](#id)` or to the web `[text](https://…)`. Nothing else (no
  tables, no HTML). Everything is escaped, so `<` and `&` are safe to write.
- Plain words, short sentences. Explain a term the first time or link it to
  the glossary. Say what to do, not only what happened.

## What the tests check (`tests/test_help.py`, `tests/js/test/help.test.mjs`)

- every link points at an entry or section that exists, and ids are unique;
- **every doctor issue code** in the code (`Issue("CODE", …)`), **every
  attention kind**, **every intake rejection kind** and **every held-search
  reason** (`raise Hold(…)` in `fragpipe.py` / `diann.py`, matched by
  `help.HOLD_TOPICS`) has an entry. adding a new one without help fails the tests;
- `help.html` is self-contained and every anchor on it resolves; the list of
  FragPipe failure causes on it comes from `fragpipe.EXPLANATIONS`;
- every help link in a report resolves inside that report (the report
  embeds every entry its entries link to), the report's help payload stays
  under 60 KB, and issue titles from the data are escaped;
- the files ship in the wheel (`pyproject.toml` package-data) and the exe
  (`deploy/ionomos.spec`).

After editing, run `cd ionomos && .venv/bin/pytest tests/test_help.py`, and
regenerate the JS fixture (`.venv/bin/python tests/js/make_fixture.py`)
because the fixture report embeds the help.
