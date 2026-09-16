#!/usr/bin/env bash
# Build the FALLBACK (wheel-based) release zip for the Windows PC.
# The preferred route is deploy/build_exe.ps1 on Windows -> LabWatch.exe.
#   deploy/make_release.sh            -> dist/labwatch-<version>-windows.zip
# Contents: labwatch wheel, dependency wheels for Windows (offline install),
# install.ps1, uninstall.ps1, run_labwatch.bat, config.example.yaml, DEPLOY_WINDOWS.md
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-labwatch/.venv/bin/python}
[ -x "$PY" ] || PY=python3
VER=$($PY -c "import tomllib;print(tomllib.load(open('labwatch/pyproject.toml','rb'))['project']['version'])")
OUT=dist/labwatch-$VER-windows
rm -rf "$OUT" "dist/labwatch-$VER-windows.zip"; mkdir -p "$OUT/wheels"

echo "==> building wheel"
$PY -m pip wheel ./labwatch --no-deps -w "$OUT" -q
echo "==> downloading Windows dependency wheels (for offline install)"
$PY -m pip download PyYAML -d "$OUT/wheels" --only-binary=:all: \
    --platform win_amd64 --python-version 3.14 -q \
 || $PY -m pip download PyYAML -d "$OUT/wheels" --only-binary=:all: --platform win_amd64 --python-version 3.13 -q \
 || echo "    (could not fetch Windows wheels; install.ps1 will use PyPI instead)"
cp deploy/install.ps1 deploy/uninstall.ps1 deploy/run_labwatch.bat labwatch/config.example.yaml "$OUT/"
cp docs/DEPLOY_WINDOWS.md docs/NAMING_CONVENTION.md "$OUT/"
(cd dist && zip -qr "labwatch-$VER-windows.zip" "labwatch-$VER-windows")
echo "==> dist/labwatch-$VER-windows.zip"
ls -la "$OUT"
