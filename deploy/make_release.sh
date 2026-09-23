#!/usr/bin/env bash
# Build the FALLBACK (wheel-based) release zip for the Windows PC.
# The preferred route is deploy/build_exe.ps1 on Windows -> Ionomos.exe.
#   deploy/make_release.sh            -> dist/ionomos-<version>-windows.zip
# Contents: ionomos wheel, dependency wheels for Windows (offline install),
# install.ps1, uninstall.ps1, run_ionomos.bat, config.example.yaml, DEPLOY_WINDOWS.md
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-ionomos/.venv/bin/python}
[ -x "$PY" ] || PY=python3
VER=$($PY -c "import tomllib;print(tomllib.load(open('ionomos/pyproject.toml','rb'))['project']['version'])")
OUT=dist/ionomos-$VER-windows
rm -rf "$OUT" "dist/ionomos-$VER-windows.zip"; mkdir -p "$OUT/wheels"

echo "==> building wheel"
$PY -m pip wheel ./ionomos --no-deps -w "$OUT" -q
echo "==> downloading Windows dependency wheels (for offline install)"
$PY -m pip download PyYAML -d "$OUT/wheels" --only-binary=:all: \
    --platform win_amd64 --python-version 3.14 -q \
 || $PY -m pip download PyYAML -d "$OUT/wheels" --only-binary=:all: --platform win_amd64 --python-version 3.13 -q \
 || echo "    (could not fetch Windows wheels; install.ps1 will use PyPI instead)"
cp deploy/install.ps1 deploy/uninstall.ps1 deploy/run_ionomos.bat ionomos/config.example.yaml "$OUT/"
cp docs/DEPLOY_WINDOWS.md docs/NAMING_CONVENTION.md "$OUT/"
(cd dist && zip -qr "ionomos-$VER-windows.zip" "ionomos-$VER-windows")
echo "==> dist/ionomos-$VER-windows.zip"
ls -la "$OUT"
