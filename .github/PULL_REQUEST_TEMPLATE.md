## What and why

<!-- One paragraph. Link the issue, the ROADMAP item or the decision. -->

## Checklist

- [ ] Docs changed with the code (the docs are the spec; `NAMING_CONVENTION.md` ↔ `naming.py`)
- [ ] `CHANGELOG.md` (Unreleased), `docs/DECISIONS.md` and `docs/ROADMAP.md` in sync
- [ ] Tests added or changed; `pytest` passes locally (`cd ionomos && .venv/bin/pytest`); the JS harness if `report.js` changed
- [ ] A new numerical method has a golden file from the reference tool and the script that made it
- [ ] A new issue code, hold reason or rejection has a help entry (`ionomos/src/ionomos/help/`)
- [ ] Nothing deletes or overwrites user data; no default path with spaces; on-disk names from `names.py`
- [ ] No new dependency on the analysis path (PyYAML only; D18, D40)

## Tested, and not tested

<!-- What ran where: CI, macOS, Windows, the lab PC, real data, on screen. Say plainly what has not been seen. -->

A person merges this pull request, not its author (docs/DECISIONS.md D31).
