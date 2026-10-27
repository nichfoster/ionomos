#!/usr/bin/env bash
# Build the documentation site (website/, D132) as CI's docs-site workflow does: strict, so a broken link or
# anchor, or a page missing from the nav, fails it.
#
#   scripts/build_site.sh                  # -> build/site/index.html (open it from the disk)
#   scripts/build_site.sh --example DIR    # with the reports of a folder `ionomos example DIR` wrote
#   scripts/build_site.sh --serve          # a live preview at http://127.0.0.1:8000/ionomos/
#
# MkDocs and its theme go into a throwaway venv, .venv-site/ (gitignored), at website/requirements.txt's pins;
# never into ionomos/.venv: they are not a dependency of Ionomos. PYTHON picks the interpreter (default python3).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
venv="$root/.venv-site"
req="$root/website/requirements.txt"
serve=0
while [ $# -gt 0 ]; do
  case "$1" in
    --example)
      [ -d "${2:-}" ] || { echo "--example needs a folder that 'ionomos example' wrote" >&2; exit 2; }
      IONOMOS_SITE_EXAMPLE="$(cd "$2" && pwd)"; export IONOMOS_SITE_EXAMPLE; shift 2 ;;
    --serve) serve=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done
if ! cmp -s "$req" "$venv/requirements.txt"; then
  "${PYTHON:-python3}" -m venv "$venv"
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$req"
  cp "$req" "$venv/requirements.txt"
fi
cd "$root"
if [ "$serve" = 1 ]; then
  exec "$venv/bin/mkdocs" serve --strict -f website/mkdocs.yml
fi
"$venv/bin/mkdocs" build --strict -f website/mkdocs.yml
echo "built: $root/build/site/index.html"
