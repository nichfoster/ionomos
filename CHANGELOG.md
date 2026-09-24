# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added an `.obvious` onboarding contract — agent guidance (`.obvious/obvious.md`),
  codebase map, repo config, and a local-dev skill — generated from a verified
  dev stack (ruff clean, 350 passed / 23 skipped, testbed end-to-end with the
  fake FragPipe). (#1)

### Changed

- Dev installs now pin `pip>=26.2` (`deploy/dev_install.ps1` and the `dev`
  extra). (#3)

### Fixed

- Intake hash-verifies cross-volume copies (per-file SHA-256 over source and
  destination) before deleting the source, so a same-size corrupted copy can no
  longer silently destroy the original experiment. (#2)
- Post-move intake failures now file a minimal queued record at the destination:
  the folder is already moved, so the reconciliation sweeps re-adopt it as a job
  instead of the experiment being silently lost from all tracking. (#4)
- Cross-volume source cleanup retries transient Windows locks
  (antivirus/indexer) with bounded backoff; a lock that survives the retries
  writes a truthful `.REJECTED.txt` at the source — the filed copy is complete
  and verified, so keep it and delete the partial inbox folder — instead of the
  partial folder being re-offered and filed as an incomplete experiment. (#4)
- Desktop-shortcut creation passes the PowerShell command as base64
  `-EncodedCommand` (UTF-16LE), so install paths containing apostrophes no
  longer break shortcut creation. (#3)
- `experiment.yaml` override path fields (`user:`, `files.*.experiment:`) are
  validated before any path join: path-like values are refused (surfaced as the
  existing inbox `.REJECTED.txt` note) instead of being able to file data
  outside `users_root`. (#5)
- Installer execution requires a verified SHA-256 digest for the exact version:
  digest-less releases are never downloaded or installed, and a Downloads-found
  installer is verified against the release whose version it claims before it
  is run. (#6)
