"""
Testbed — a fake lab you can build anywhere (Mac, Windows) to try the whole
tool without a real FragPipe or real raw files.

    labwatch testbed init [DIR]         build DIR (default ./labwatch-testbed): Fragpipe_Auto/,
                                        Fragpipe_General/, config.yaml, samples/, fake FragPipe
    labwatch testbed list [DIR]         the sample drops and what each one exercises
    labwatch testbed drop NAME [DIR]    copy samples/NAME into the inbox (--slow = file by file,
                                        like a real drag from a USB drive)
    labwatch testbed reset [DIR]        empty inbox, user folders, ledger, logs; keep samples
    labwatch testbed gui-demo           open the resolver window with sample data (no drop needed)

Then, in another terminal:   labwatch --config DIR/Fragpipe_Auto/config.yaml run

Sample .raw files are a few KB of random bytes — labwatch never reads raw
contents, only names and sizes.
"""
from __future__ import annotations

import os
import random
import shutil
import stat
import sys
import time
from pathlib import Path

import yaml


def default_root() -> Path:
    """Where the testbed goes when no DIR is given.

    Windows: C:/labwatch-testbed, never under the profile folder (usernames often
    contain spaces, and the config loader rejects spaces on Windows). Elsewhere:
    ./labwatch-testbed.
    """
    if os.name == "nt":
        return Path("C:/labwatch-testbed")
    return Path("labwatch-testbed")


DEFAULT_DIR = str(default_root())
USERS = ["EJQ", "Isaac", "Chris", "Aman", "Taylor_Elements"]


def _iso(prefix: str, reps=(1, 2, 3), fracs=(1, 2, 3)) -> list[str]:
    return [f"{prefix}_{r}_{f}.raw" for r in reps for f in fracs]


# name -> (folder name, files at top level, files in raw/, other files, experiment.yaml dict|None, what it shows)
SAMPLES: dict[str, dict] = {
    "iso_good": dict(
        folder="20260902-isoDTB_EJQ-2-027",
        raws=_iso("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"), others=["EJQ-2-027_notes.xlsx"],
        shows="clean isoDTB drop: 3 reps x 3 fractions, user EJQ, date from name -> queued",
    ),
    "iso_spaces": dict(
        folder="20260902 isoDTB EJQ-2-027 (1uM 3h)",
        raws=_iso("EJQ PK isoDTB 1uM 3h", reps=(1, 2), fracs=(1, 2)),
        shows="spaces/parentheses in folder AND file names -> sanitised, queued",
    ),
    "dia_good": dict(
        folder="20260914_Isaac_DIA_FLAG-AR-pulldown",
        raw_sub=["DMSO_1.raw", "DMSO_2.raw", "DMSO_3.raw", "Drug_1.raw", "Drug_2.raw", "Drug_3.raw"],
        shows="DIA with raws in a raw/ subfolder: two conditions x 3 bioreps -> queued",
    ),
    "tmt_good": dict(
        folder="20260126_Aman_TMT_KL6159A-9plex",
        raws=[f"KL6159A_TMT_F{i}.raw" for i in range(1, 5)],
        yaml={"tmt": {"tag": "TMT-10", "channels": {"126": "DMSO_126", "127N": "DMSO_127N", "127C": "Drug_127C",
                                                       "128N": "Drug_128N"}}},
        shows="TMT, 4 fractions, biorep 1, channel map in experiment.yaml -> queued",
    ),
    "glued_initials": dict(
        folder="IJD05_isoDTB_FLAGpull",
        raws=_iso("IJD05_FLAG", reps=(1, 2), fracs=(1, 2)),
        shows="initials glued to an ID (IJD05) resolve via alias IJD -> Isaac -> queued",
    ),
    "method_in_files": dict(
        folder="EJQ_2027_pulldown",
        raws=_iso("EJQ_isoDTB_2027", reps=(1,), fracs=(1, 2)),
        shows="no method in folder name, but the raw file names say isoDTB -> queued",
    ),
    "gui_unknown_user": dict(
        folder="XYZ99_isoDTB_run",
        raws=_iso("XYZ99", reps=(1, 2), fracs=(1, 2)),
        shows="unknown initials -> resolver window (pick user, tick 'remember XYZ')",
    ),
    "gui_no_method": dict(
        folder="EJQ_2027_mystery",
        raws=["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"],
        shows="no method anywhere -> resolver window (pick DIA; files re-parse as cond_biorep)",
    ),
    "gui_bad_tail": dict(
        folder="EJQ_isoDTB_odd-names",
        raws=["Sample_1_1.raw", "Sample_1_2.raw", "Sample_extra.raw"],
        shows="one file has no rep/fraction tail -> resolver window (type its rep/frac)",
    ),
    "gui_incomplete": dict(
        folder="EJQ_isoDTB_partial-copy",
        raws=_iso("EJQ_partial")[:-1],
        shows="rep 3 is missing fraction 3 -> resolver window ('accept uneven' or skip)",
    ),
    "reject_no_raws": dict(
        folder="EJQ_isoDTB_empty",
        raws=[], others=["notes.txt"],
        shows="no .raw files -> watcher waits forever (min_raw_files); nothing happens",
    ),
    "reject_two_methods": dict(
        folder="EJQ_isoDTB_then_TMT",
        raws=["S_1_1.raw"],
        shows="two method keywords -> resolver window (pick one)",
    ),
}


def _write_raw(p: Path, size: int) -> None:
    p.write_bytes(random.randbytes(size))


def build_sample(dest_parent: Path, name: str, size: int = 4096) -> Path:
    spec = SAMPLES[name]
    d = dest_parent / spec["folder"]
    d.mkdir(parents=True, exist_ok=True)
    for f in spec.get("raws", []):
        _write_raw(d / f, size)
    if spec.get("raw_sub"):
        (d / "raw").mkdir(exist_ok=True)
        for f in spec["raw_sub"]:
            _write_raw(d / "raw" / f, size)
    for f in spec.get("others", []):
        (d / f).write_text("just a note\n", encoding="utf-8")
    if spec.get("yaml"):
        (d / "experiment.yaml").write_text(yaml.safe_dump(spec["yaml"]), encoding="utf-8")
    return d


# ---------------------------------------------------------------------- init --

FAKE_FRAGPIPE_PY = '''"""Fake FragPipe for the testbed. Accepts the real headless flags, sleeps, writes plausible output."""
import argparse, sys, time
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--headless", action="store_true")
ap.add_argument("--workflow"); ap.add_argument("--manifest"); ap.add_argument("--workdir")
ap.add_argument("--threads"); ap.add_argument("--ram")
ap.add_argument("--config-tools-folder"); ap.add_argument("--config-diann")
a, _ = ap.parse_known_args()
wd = Path(a.workdir or ".")
wd.mkdir(parents=True, exist_ok=True)
print("FAKE FragPipe: workflow", a.workflow, "manifest", a.manifest, "workdir", wd)
lines = Path(a.manifest).read_text(encoding="utf-8").splitlines() if a.manifest and Path(a.manifest).is_file() else []
print(f"FAKE FragPipe: {len(lines)} raw files")
for i in range(3):
    print("working...", i + 1, "/ 3"); sys.stdout.flush(); time.sleep(1)
(wd / "fragpipe.workflow").write_text(Path(a.workflow).read_text(encoding="utf-8") if a.workflow and Path(a.workflow).is_file() else "", encoding="utf-8")
(wd / "combined_modified_peptide_label_quant.tsv").write_text("Peptide Sequence\\tLight Modified Peptide\\tStart\\tProtein\\n", encoding="utf-8")
(wd / "log_fake.txt").write_text("done\\n", encoding="utf-8")
print("FAKE FragPipe: done")
'''


def init(root: Path, slow_defaults: bool = False) -> Path:
    root = Path(root).resolve()
    auto = root / "Fragpipe_Auto"
    general = root / "Fragpipe_General"
    for d in (auto / "inbox", auto / "workflows", auto / "fasta", auto / "logs", root / "samples"):
        d.mkdir(parents=True, exist_ok=True)
    for u in USERS:
        (general / u).mkdir(parents=True, exist_ok=True)
    for wf in ("isoDTB.workflow", "TMT10-MS3.workflow", "DIA.workflow"):
        p = auto / "workflows" / wf
        if not p.exists():
            p.write_text(f"# placeholder workflow for the testbed ({wf})\ndatabase.db-path=FAKE.fas\n", encoding="utf-8")
    (auto / "fasta" / "human_reviewed_decoys.fas").write_text(">sp|FAKE|FAKE_HUMAN fake\nMKV\n", encoding="utf-8")

    fake_py = auto / "fake_fragpipe.py"
    fake_py.write_text(FAKE_FRAGPIPE_PY, encoding="utf-8")
    if os.name == "nt":
        exe = auto / "fragpipe.bat"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\n', encoding="utf-8")
    else:
        exe = auto / "fragpipe.sh"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def s(p: Path) -> str:  # forward slashes, as recommended in config
        return str(p).replace("\\", "/")

    cfg = {
        "paths": {
            "inbox": s(auto / "inbox"), "users_root": s(general), "fragpipe_exe": s(exe),
            "workflow_dir": s(auto / "workflows"), "fasta_dir": s(auto / "fasta"),
            "database": s(auto / "labwatch.db"), "log_dir": s(auto / "logs"),
        },
        "watcher": {"poll_seconds": 1 if not slow_defaults else 10, "stable_seconds": 3 if not slow_defaults else 60,
                    "min_raw_files": 1},
        "fragpipe": {"threads": 4, "ram_gb": 4, "timeout_minutes": 5},
        "gui": {"enabled": True, "timeout_minutes": 0},
        "users": {"aliases": {"Isaac": ["IJ", "IJD"], "EJQ": ["EJQ_2"]}, "default": ""},
        "methods": {
            "isoDTB": {"aliases": ["isodtb", "iso-dtb"], "workflow": "isoDTB.workflow",
                       "fasta": "human_reviewed_decoys.fas", "data_type": "DDA",
                       "postprocess": ["isodtb_sites"], "isodtb_mod_mass": "561.3387"},
            "TMT": {"aliases": ["tmt"], "workflow": "TMT10-MS3.workflow", "fasta": "human_reviewed_decoys.fas",
                    "data_type": "DDA", "postprocess": ["tmt_annotation"]},
            "DIA": {"aliases": ["dia", "diann", "dia-nn"], "workflow": "DIA.workflow",
                    "fasta": "human_reviewed_decoys.fas", "data_type": "DIA", "postprocess": []},
        },
    }
    cfg_path = auto / "config.yaml"
    cfg_path.write_text("# labwatch TESTBED config - generated by `labwatch testbed init`\n" + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    for name in SAMPLES:
        build_sample(root / "samples", name)
    (root / "README.txt").write_text(_readme(root, cfg_path), encoding="utf-8")
    return cfg_path


def _readme(root: Path, cfg_path: Path) -> str:
    return f"""labwatch testbed at {root}

Terminal 1 (the watcher):
    labwatch --config "{cfg_path}" run

Terminal 2 (you, being a lab member):
    labwatch testbed list "{root}"
    labwatch testbed drop iso_good "{root}"
    labwatch testbed drop gui_unknown_user "{root}"      # resolver window opens in terminal 1
    labwatch --config "{cfg_path}" status
    labwatch testbed reset "{root}"

Or drag folders from  {root / 'samples'}  into  {root / 'Fragpipe_Auto' / 'inbox'}  with Finder/Explorer.
Results land in  {root / 'Fragpipe_General'}/<user>/ .
"""


# --------------------------------------------------------------------- drop --


def drop(root: Path, name: str, slow: bool = False, delay: float = 0.4) -> Path:
    root = Path(root).resolve()
    src = root / "samples" / SAMPLES[name]["folder"]
    if not src.is_dir():
        build_sample(root / "samples", name)
    dst = root / "Fragpipe_Auto" / "inbox" / src.name
    if dst.exists():
        raise SystemExit(f"already in inbox: {dst}")
    if not slow:
        shutil.copytree(src, dst)
        return dst
    # file by file, with pauses: exercises the stability wait
    dst.mkdir()
    files = sorted(p for p in src.rglob("*") if p.is_file())
    for i, p in enumerate(files, 1):
        rel = p.relative_to(src)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst / rel)
        print(f"  copied {i}/{len(files)} {rel}")
        time.sleep(delay)
    return dst


def reset(root: Path) -> None:
    root = Path(root).resolve()
    auto = root / "Fragpipe_Auto"
    for d in (auto / "inbox", root / "Fragpipe_General"):
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for f in [auto / "labwatch.db", auto / "learned_aliases.yaml", *(auto / "logs").glob("labwatch.log*")]:
        if f.exists():
            f.unlink()
    for u in USERS:
        (root / "Fragpipe_General" / u).mkdir(parents=True, exist_ok=True)


def gui_demo() -> int:
    from labwatch.intake import Draft, DraftFile, Kind
    from labwatch.resolve import TkResolver, gui_available

    ok, why = gui_available()
    if not ok:
        print(f"GUI not available: {why}")
        return 1
    import tkinter as tk

    files = [DraftFile(f"EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_{r}_{f}.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h", str(r), str(f))
             for r in (1, 2, 3) for f in range(1, 8)]
    files.append(DraftFile("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra", "1", "",
                           error="must end in _rep_fraction"))
    d = Draft(folder="20260902-isoDTB_XYZ-2-027 (1uM 3h)",
              problem="no known user in '20260902-isoDTB_XYZ-2-027 (1uM 3h)'; include your initials or folder name "
                      "(known: Aman, Chris, EJQ, Isaac)",
              kind=Kind.USER, user="", method="isoDTB", date="2026-09-02",
              known_users=USERS, known_methods=["isoDTB", "TMT", "DIA"], files=files)
    root = tk.Tk()
    root.withdraw()
    res = TkResolver(root, remember=lambda u, a: print(f"(would remember alias {a!r} -> {u})"))
    ov = res.resolve(d)
    root.destroy()
    print("answer:", "skipped" if ov is None else yaml.safe_dump(ov.to_dict(), sort_keys=False))
    return 0


# ---------------------------------------------------------------------- cli --


def add_parser(sub):
    p = sub.add_parser("testbed", help="build/drive a fake lab for testing", description=__doc__,
                       formatter_class=__import__("argparse").RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="tb_cmd", required=True)
    i = s.add_parser("init", help="create the testbed")
    i.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    i.add_argument("--slow-defaults", action="store_true", help="use production poll/stable timings")
    ls = s.add_parser("list", help="list sample drops")
    ls.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    d = s.add_parser("drop", help="copy a sample into the inbox")
    d.add_argument("name", choices=sorted(SAMPLES))
    d.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    d.add_argument("--slow", action="store_true", help="copy file by file with pauses")
    r = s.add_parser("reset", help="empty inbox/users/ledger/logs")
    r.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    s.add_parser("gui-demo", help="open the resolver window with sample data")
    return p


def main(args) -> int:
    if args.tb_cmd == "init":
        cfg = init(Path(args.dir), args.slow_defaults)
        print((Path(args.dir).resolve() / "README.txt").read_text(encoding="utf-8"))
        print(f"config: {cfg}")
        return 0
    if args.tb_cmd == "list":
        w = max(len(n) for n in SAMPLES)
        for n, spec in SAMPLES.items():
            print(f"  {n:<{w}}  {spec['folder']:<40}  {spec['shows']}")
        return 0
    if args.tb_cmd == "drop":
        dst = drop(Path(args.dir), args.name, slow=args.slow)
        print(f"dropped {dst}")
        return 0
    if args.tb_cmd == "reset":
        reset(Path(args.dir))
        print("testbed reset")
        return 0
    if args.tb_cmd == "gui-demo":
        return gui_demo()
    return 2
