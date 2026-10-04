# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds the Ionomos executables.
#   Windows:  dist/Ionomos.exe (windowed: app + `run`)  and  dist/ionomos-cli.exe (console CLI)
#   NB: Windows filenames are case-insensitive, so the console exe must NOT be called ionomos.exe.
#   macOS:    dist/ionomos (console) — for validating the packaging only
# Run via deploy/build_exe.ps1 or deploy/build_app_mac.sh (they set the cwd).
import sys
from pathlib import Path

SRC = Path(SPECPATH).parent / "ionomos" / "src"
ENTRY = str(SRC / "ionomos" / "__main__.py")

a = Analysis(
    [ENTRY],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(SRC / "ionomos" / "downstream" / "assets"), "ionomos/downstream/assets"),  # report.js / report.css
           (str(SRC / "ionomos" / "help" / "*.md"), "ionomos/help"),  # the help content (ionomos help, reports)
           # the assistant's scenario corpus, for `ionomos ask-eval` on the PC (D72)
           (str(SRC / "ionomos" / "assistant" / "scenarios" / "*.json"), "ionomos/assistant/scenarios")],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox", "tkinter.scrolledtext",
                   "yaml", "ionomos.app", "ionomos.resolve", "ionomos.testbed", "ionomos.worker",
                   "ionomos.fragpipe", "ionomos.postprocess", "ionomos.health", "ionomos.stress",
                   "ionomos.setupcheck", "ionomos.tkutil", "ionomos.names", "ionomos.buildinfo", "ionomos.updates",
                   "ionomos.downstream", "ionomos.downstream.analysis",
                   "ionomos.downstream.charts", "ionomos.downstream.isodtb", "ionomos.downstream.quant",
                   "ionomos.downstream.report", "ionomos.downstream.simulate", "ionomos.downstream.stats",
                   "ionomos.downstream.tables", "ionomos.downstream.tmt", "ionomos.downstream.fpa",
                   "ionomos.downstream.qc", "ionomos.downstream.enrich", "ionomos.downstream.export",
                   "ionomos.downstream.rrandom", "ionomos.analysis_tab", "ionomos.manifest",
                   "ionomos.naming_history", "ionomos.inbox", "ionomos.attention", "ionomos.popups",
                   "ionomos.experiment_editor", "ionomos.downstream.doctor", "ionomos.help",
                   "ionomos.assistant", "ionomos.assistant.askui", "ionomos.assistant.evaluate",
                   "ionomos.assistant.runtime", "ionomos.assistant.scenarios", "ionomos.assistant.scenarios.states",
                   "ionomos.bundle", "ionomos.bundle_dialog",
                   "ionomos.forms", "ionomos.accuracy", "ionomos.accuracy_page", "ionomos.notify_tab",
                   "ionomos.notify", "ionomos.downstream.compare", "ionomos.downstream.benchmark",
                   "ionomos.downstream.phospho",  # opt-in phosphosites, KSEA, STRING (D79)
                   "tkinter.colorchooser"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pandas", "numpy", "matplotlib", "scipy", "PIL", "pytest", "ruff", "pyarrow"],  # pyarrow: optional (engines.py)
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Ionomos", console=False, upx=False,
        icon=None, onefile=True)
    EXE(pyz, a.scripts, a.binaries, a.datas, [], name="ionomos-cli", console=True, upx=False,
        icon=None, onefile=True)
else:
    EXE(pyz, a.scripts, a.binaries, a.datas, [], name="ionomos", console=True, upx=False, onefile=True)
