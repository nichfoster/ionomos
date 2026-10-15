"""Build-time check: the frozen exe holds every module of the ionomos package and every data file it reads.

PyInstaller finds modules by following imports. One reached only through a string, or caught by an `excludes`
entry in deploy/ionomos.spec, would be missing, and the lab PC would find out the first time that feature ran
(ModuleNotFoundError inside Ionomos.exe). A data folder added to the package but not to the spec's `datas` would
be missing the same way (help pages, report.js). deploy/build_exe.ps1 runs this on both exes after PyInstaller;
tests/test_packaging.py tests the comparison.

    python deploy/check_frozen.py dist/exe/Ionomos.exe dist/exe/ionomos-cli.exe
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "ionomos" / "src"

# in the package but correctly not in the exe's module archive (a name here covers its submodules too)
NOT_IN_ARCHIVE = {
    "ionomos.__main__",  # the exe's entry script itself (PyInstaller stores it as the script, not as a module)
    "ionomos.runners",  # imported by nothing in the program, only by tests/test_downstream.py (left from 0.4)
}  # ionomos._build (commit and date, written by build_exe.ps1 for the build) is expected while it exists


def expected(src: Path = SRC) -> tuple[set[str], set[str]]:
    """(module names, data paths relative to src with '/') that the ionomos package in `src` consists of."""
    pkg = src / "ionomos"
    modules, data = set(), set()
    for p in pkg.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = p.relative_to(src)
        if p.suffix == ".py":
            parts = list(rel.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            modules.add(".".join(parts))
        else:
            data.add(rel.as_posix())
    left_out = {m for m in modules if any(m == x or m.startswith(x + ".") for x in NOT_IN_ARCHIVE)}
    return modules - left_out, data


def contents(exe: Path) -> tuple[set[str], set[str]]:
    """(module names in the exe's PYZ archive, data file paths in the exe) of a PyInstaller one-file exe."""
    from PyInstaller.archive.readers import PKG_ITEM_BINARY, PKG_ITEM_DATA, PKG_ITEM_PYZ, CArchiveReader

    pkg = CArchiveReader(str(exe))
    modules, data = set(), set()
    for name, entry in pkg.toc.items():
        typecode = entry[-1]
        if typecode == PKG_ITEM_PYZ:
            modules |= set(pkg.open_embedded_archive(name).toc)
        elif typecode in (PKG_ITEM_BINARY, PKG_ITEM_DATA):  # a one-file exe stores `datas` as binaries
            data.add(name.replace("\\", "/"))
    return modules, data


def missing(want: tuple[set[str], set[str]], have: tuple[set[str], set[str]]) -> list[str]:
    """What the package has and the exe lacks, one line each, sorted: empty when the exe is complete."""
    (want_mods, want_data), (have_mods, have_data) = want, have
    return ([f"module {m}" for m in sorted(want_mods - have_mods)]
            + [f"data   {d}" for d in sorted(want_data - have_data)])


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    want = expected()
    bad = 0
    for exe in map(Path, argv):
        gaps = missing(want, contents(exe))
        if gaps:
            bad += 1
            print(f"!! {exe.name} lacks {len(gaps)} part(s) of the ionomos package (fix deploy/ionomos.spec):")
            print("\n".join(f"   {g}" for g in gaps))
        else:
            print(f"ok {exe.name}: all {len(want[0])} ionomos modules and {len(want[1])} data files inside")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
