#!/usr/bin/env bash
# Build a single-file ionomos binary on macOS (validates the PyInstaller spec; the real target is Windows).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-ionomos/.venv/bin/python}
$PY -m pip install -q pyinstaller
$PY -m PyInstaller --noconfirm --clean --distpath dist/exe --workpath build/pyi deploy/ionomos.spec
$PY deploy/check_frozen.py dist/exe/ionomos  # every ionomos module and data file is inside
echo "==> dist/exe/ionomos"; ls -la dist/exe/ionomos
