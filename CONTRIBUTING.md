# Contributing to Ionomos

Thank you for looking. Ionomos has one maintainer (@nichfoster) and is
written largely by coding agents under review, so the rules below are what
keep it trustworthy. [docs/VISION.md](docs/VISION.md) says what the project
is for and what it is not.

## Reporting a problem

Open an issue with the template. What makes a problem reproducible:

- **On a PC running the watcher:** the app's **Copy diagnostics**, or
  `ionomos diagnose --anonymise`. Lab names are replaced and secrets are
  `***`. For an analysis or a failed search the text can't explain, a
  **bundle** (**Report a problem…**, or `ionomos bundle 12 --level validate`):
  one zip, saved on your computer, never uploaded by Ionomos. Read
  [docs/DEV_LOOP.md](docs/DEV_LOOP.md) ("Bundles") before sharing one; keep
  its key file in the lab.
- **With `ionomos analyze`:** `ionomos --version`, the command you ran, the
  first lines of the table's header, and `results/analysis.json` if one was
  written. Don't attach unpublished data you may not share.

## The dev loop

```bash
scripts/test_mac.sh --bed                                         # macOS: venv, ruff, pytest, a fake lab
powershell -ExecutionPolicy Bypass -File scripts\test_windows.ps1 -Bed   # Windows, the same
cd ionomos && .venv/bin/pytest                                    # the tests again (Windows: .venv\Scripts\python -m pytest)
cd ionomos/tests/js && npm ci && npm test                         # the report's JavaScript (jsdom)
```

[docs/TESTING.md](docs/TESTING.md) has the layers (unit, end-to-end against a
fake FragPipe, the testbed, the stress run) and
[docs/DEV_LOOP.md](docs/DEV_LOOP.md) the Mac ↔ GitHub ↔ PC loop. Tests that
open real Tk windows skip locally and run in CI; set `IONOMOS_GUI_TESTS=1`
only if you want windows over your screen. CI runs Python 3.11, 3.12 and 3.14
on Linux and Windows.

## The rules

- **The docs are the spec.** Read [README.md](README.md) and the relevant
  page in `docs/` before changing code, and change the doc in the same pull
  request. `docs/NAMING_CONVENTION.md` and `ionomos/src/ionomos/naming.py`
  move together.
- **Decisions are logged.** A choice someone could reasonably make otherwise
  gets an entry in [docs/DECISIONS.md](docs/DECISIONS.md) (`### D<n> — …`,
  date, why, what was not done). Reverse one by adding a new entry. Open
  questions go to [docs/ROADMAP.md](docs/ROADMAP.md).
- **Keep CHANGELOG.md, DECISIONS.md and ROADMAP.md in sync** in the same pull
  request.
- **Agents open pull requests; a person merges them** (D31). Every pull
  request asks @nichfoster for review (`.github/CODEOWNERS`).
- **Never delete or overwrite user data.** Intake moves; a failure leaves
  everything in place (D4, D17, D29). Results go only into folders Ionomos
  owns (D33).
- **No spaces in any default path** (FragPipe refuses them), and anything
  path-related works on Windows and macOS.
- **A numerical method is a port, checked against its reference.** Add a
  golden file made by the reference tool (R, Python) and the script that
  made it, next to it in `ionomos/tests/golden/` (D18).
- **The analysis keeps one dependency** (PyYAML) and never imports Tk
  (D18, D40). An optional extra is acceptable when the feature can't be done
  otherwise (`pyarrow` for Parquet).
- **Every new issue code, hold reason or rejection has a help entry** in
  `ionomos/src/ionomos/help/`; a test fails otherwise (D46).
- **On-disk names come from `ionomos/src/ionomos/names.py`**, including the
  LabWatch-era ones, which stay (D21).
- **Licences:** never bundle an engine or a database whose licence forbids
  it (D36, D79). A port keeps its source's licence notice and citation
  (README.md, License).
- **In tests, never patch `os.name` globally** to fake Windows; stub the
  module's `os` instead.

## Pull requests

Fill in the template. Small, single-purpose pull requests are reviewed
fastest. Write in the docs' voice: plain, specific, no marketing. Say what
was tested and what was not (on real data, on the lab PC, on screen).

## Releases (the maintainer)

Bump `__version__` and `ionomos/pyproject.toml` (and `CITATION.cff`), move the
CHANGELOG's Unreleased section under the version, push, wait for the Windows
CI, then `git tag vX.Y.Z && git push --tags`. The tag builds and tests the
Windows installer (`build-exe.yml`) and the pip package on three platforms
(`publish-pypi.yml`, which uploads to PyPI once trusted publishing is set up).

## Conduct

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
