#!/usr/bin/env bash
# One-shot dev/test setup on macOS: venv, install, lint, full test suite, then a testbed.
#   scripts/test_mac.sh            run everything
#   scripts/test_mac.sh --bed      also build ./labwatch-testbed and print how to drive it
set -euo pipefail
cd "$(dirname "$0")/../labwatch"
PY=${PY:-python3}
if ! $PY -c "import tkinter" 2>/dev/null; then
  echo "note: this python has no tkinter; GUI tests will be skipped."
  echo "      Homebrew: brew install python-tk@$($PY -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
fi
[ -d .venv ] || $PY -m venv .venv
.venv/bin/pip install -q -e ".[dev]"
echo "==> ruff";   .venv/bin/ruff check src tests
echo "==> pytest"; .venv/bin/pytest -q -rs
if [ "${1:-}" = "--bed" ]; then
  echo "==> testbed"; .venv/bin/labwatch testbed init "$(pwd)/../labwatch-testbed"
  echo "activate with:  source $(pwd)/.venv/bin/activate"
fi
