#!/usr/bin/env bash
# Build a single-file labwatch binary on macOS (validates the PyInstaller spec; the real target is Windows).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-labwatch/.venv/bin/python}
$PY -m pip install -q pyinstaller
$PY -m PyInstaller --noconfirm --clean --distpath dist/exe --workpath build/pyi deploy/labwatch.spec
echo "==> dist/exe/labwatch"; ls -la dist/exe/labwatch
