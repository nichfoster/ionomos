"""
Which build is this? One line that goes at the top of every report and `ionomos --version`:

    Ionomos 0.5.0 (build 3f2a9c1, 2026-09-23, Windows installer)

Frozen builds get _build.py written by deploy/build_exe.ps1 (commit + date);
a git checkout asks git; a plain pip install knows only its version.
"""
from __future__ import annotations

import sys

from ionomos import __version__


def info() -> dict:
    d = {"version": __version__, "commit": "", "built": "", "kind": "package"}
    try:
        from ionomos import _build  # type: ignore[attr-defined]

        d["commit"], d["built"] = _build.COMMIT, _build.BUILT
    except ImportError:
        pass
    if getattr(sys, "frozen", False):
        from ionomos.service import is_installed_build

        d["kind"] = "installed" if is_installed_build() else "portable exe"
    else:
        from ionomos.service import source_checkout

        repo = source_checkout()
        if repo is not None:
            d["kind"] = "git checkout"
            if not d["commit"]:
                from ionomos.service import _git

                code, out = _git(repo, "log", "-1", "--format=%h %cs", timeout=5)
                if code == 0 and out:
                    d["commit"], _, d["built"] = out.partition(" ")
    d["platform"] = sys.platform
    return d


def one_line() -> str:
    d = info()
    extra = ", ".join(x for x in (f"build {d['commit']}" if d["commit"] else "", d["built"], d["kind"]) if x)
    return f"Ionomos {d['version']} ({extra})"
