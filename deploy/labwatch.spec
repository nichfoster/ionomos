# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds the LabWatch executables.
#   Windows:  dist/LabWatch.exe (windowed: app + `run`)  and  dist/labwatch-cli.exe (console CLI)
#   NB: Windows filenames are case-insensitive, so the console exe must NOT be called labwatch.exe.
#   macOS:    dist/labwatch (console) — for validating the packaging only
# Run via deploy/build_exe.ps1 or deploy/build_app_mac.sh (they set the cwd).
import sys
from pathlib import Path

SRC = Path(SPECPATH).parent / "labwatch" / "src"
ENTRY = str(SRC / "labwatch" / "__main__.py")

a = Analysis(
    [ENTRY],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox", "tkinter.scrolledtext",
                   "yaml", "labwatch.app", "labwatch.resolve", "labwatch.testbed", "labwatch.worker",
                   "labwatch.fragpipe", "labwatch.postprocess"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pandas", "numpy", "matplotlib", "scipy", "PIL", "pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    EXE(pyz, a.scripts, a.binaries, a.datas, [], name="LabWatch", console=False, upx=False,
        icon=None, onefile=True)
    EXE(pyz, a.scripts, a.binaries, a.datas, [], name="labwatch-cli", console=True, upx=False,
        icon=None, onefile=True)
else:
    EXE(pyz, a.scripts, a.binaries, a.datas, [], name="labwatch", console=True, upx=False, onefile=True)
