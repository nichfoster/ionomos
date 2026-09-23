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
    "fp_fail": dict(
        folder="20260910_Chris_DIA_crash-test_FAKEFAIL",
        raws=["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"],
        shows="filed fine, then the fake FragPipe fails on purpose -> job failed, FAILED.txt; Retry re-runs it",
    ),
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
        yaml={"tmt": {"tag": "TMT-10", "channels": {"126": "DMSO_1_126", "127N": "DMSO_1_127N", "127C": "DMSO_1_127C",
                                                       "128N": "Drug_1_128N", "128C": "Drug_1_128C",
                                                       "129N": "Drug_1_129N"}}},
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

def fake_fragpipe(argv: list[str]) -> int:
    """Fake FragPipe (`labwatch fake-fragpipe ...`): same headless flags, same checks, plausible output.

    Fails like the real one when the workflow's database.db-path doesn't exist or a
    manifest file is missing, and on purpose when a raw path contains FAKEFAIL.
    Run time: LABWATCH_FAKE_FP_SECONDS (default 4).
    """
    import argparse

    ap = argparse.ArgumentParser(prog="fragpipe (fake)")
    ap.add_argument("--headless", action="store_true")
    for flag in ("--workflow", "--manifest", "--workdir", "--threads", "--ram", "--config-tools-folder",
                 "--config-diann"):
        ap.add_argument(flag)
    a, _ = ap.parse_known_args(argv)
    say = lambda *x: print("FAKE FragPipe:", *x, flush=True)  # noqa: E731
    if not (a.headless and a.workflow and a.manifest and a.workdir):
        say("usage: --headless --workflow W --manifest M --workdir D")
        return 2
    wf_text = Path(a.workflow).read_text(encoding="utf-8") if Path(a.workflow).is_file() else ""
    db = next((ln.split("=", 1)[1].strip() for ln in wf_text.splitlines() if ln.startswith("database.db-path")), "")
    if not db:
        say("ERROR: FASTA file path is empty")
        return 1
    if not Path(db).is_file():
        say(f"ERROR: FASTA file not found: {db}")
        return 1
    rows = [ln.split("\t") for ln in Path(a.manifest).read_text(encoding="utf-8").splitlines() if ln.strip()]
    for r in rows:
        if not Path(r[0]).is_file():
            say(f"ERROR: file in manifest does not exist: {r[0]}")
            return 1
    say(f"{len(rows)} files, workflow {Path(a.workflow).name}, database {Path(db).name}")
    # LABWATCH_FAKE_FP_MODE simulates real failure modes: oom | msfragger | step-fail-exit0 | silent-exit0
    mode = os.environ.get("LABWATCH_FAKE_FP_MODE", "")
    if mode == "oom":
        print("Exception in thread \"main\" java.lang.OutOfMemoryError: Java heap space", flush=True)
        return 1
    if mode == "msfragger":
        print("MSFragger jar not found. Please download MSFragger in the Config tab.", flush=True)
        return 1
    if mode == "step-fail-exit0":
        print(f"MSFragger [Work dir: {a.workdir}]", flush=True)
        print("Process 'MSFragger' finished, exit code: 137", flush=True)
        Path(a.workdir).mkdir(parents=True, exist_ok=True)
        (Path(a.workdir) / "partial.txt").write_text("x", encoding="utf-8")
        return 0
    if mode == "silent-exit0":
        return 0
    total = float(os.environ.get("LABWATCH_FAKE_FP_SECONDS", "4"))
    for step in ("MSFragger", "MSBooster", "Percolator", "ProteinProphet", "IonQuant"):
        print(f"{step} [Work dir: {a.workdir}]", flush=True)  # FragPipe's own format
        time.sleep(total / 5)
        if step == "IonQuant" and any("FAKEFAIL" in r[0] for r in rows):
            say("ERROR: IonQuant crashed (this sample fails on purpose)")
            print(f"Process '{step}' finished, exit code: 1", flush=True)
            return 1
        print(f"Process '{step}' finished, exit code: 0", flush=True)
    wd = Path(a.workdir)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "fragpipe.workflow").write_text(wf_text, encoding="utf-8")
    (wd / "fragpipe-files.fp-manifest").write_text(Path(a.manifest).read_text(encoding="utf-8"), encoding="utf-8")
    (wd / f"log_{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt").write_text("fake FragPipe log\n", encoding="utf-8")
    _fake_results(wd, rows, Path(a.workflow).name)
    say("done")
    return 0


def _fake_results(wd: Path, rows: list[list[str]], workflow_name: str) -> None:
    """Realistic result tables with planted hits (labwatch.downstream.simulate), so reports have content."""
    import zlib

    from labwatch.downstream import simulate

    seed = zlib.crc32("".join(r[0] for r in rows).encode())
    if any(len(r) > 3 and r[3] == "DIA" for r in rows):
        (wd / "diann-output" / "report.tsv").parent.mkdir(parents=True, exist_ok=True)
        (wd / "diann-output" / "report.tsv").write_text("Run\tProtein.Group\tPrecursor.Quantity\n", encoding="utf-8")
        simulate.dia_pg_matrix(wd / "diann-output" / "report.pg_matrix.tsv", [(r[0], r[1]) for r in rows], seed)
    elif "tmt" in workflow_name.lower():
        ann = Path(rows[0][0]).parent / "annotation.txt"
        names = []
        if ann.is_file():
            names = [ln.split("\t")[1].strip() for ln in ann.read_text(encoding="utf-8").splitlines() if "\t" in ln]
        names = names or ["DMSO_1_126", "DMSO_1_127N", "DMSO_1_127C", "Drug_1_128N", "Drug_1_128C", "Drug_1_129N"]
        simulate.tmt_abundance(wd / "tmt-report" / "abundance_gene_MD.tsv", names, seed)
    else:
        exps: dict[str, list[int]] = {}
        for r in rows:
            exps.setdefault(r[1], [])
            if int(r[2]) not in exps[r[1]]:
                exps[r[1]].append(int(r[2]))
        simulate.isodtb_label_quant(wd / "combined_modified_peptide_label_quant.tsv",
                                    {e: sorted(v) for e, v in exps.items()}, seed)


def write_fake_launcher(folder: Path) -> Path:
    """fragpipe.bat / fragpipe.sh in `folder` that runs `labwatch fake-fragpipe` (works frozen or from a venv)."""
    from labwatch.service import labwatch_command

    cmd = labwatch_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / "fragpipe.bat"
        quoted = " ".join(f'"{c}"' for c in cmd)
        exe.write_text(f"@echo off\r\n{quoted} fake-fragpipe %*\r\n", encoding="utf-8")
    else:
        exe = folder / "fragpipe.sh"
        quoted = " ".join(f"'{c}'" for c in cmd)
        exe.write_text(f'#!/bin/sh\nexec {quoted} fake-fragpipe "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


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
    (auto / "fasta" / "human_reviewed_decoys.fas").write_text(
        ">sp|FAKE1|FAKE1_HUMAN fake protein 1\nMKVLAAGIVGLLLAC\n>sp|FAKE2|FAKE2_HUMAN fake protein 2\nMSTNPKPQRKTKRNT\n"
        ">rev_sp|FAKE1|FAKE1_HUMAN\nCALLLGVIGAALVKM\n>rev_sp|FAKE2|FAKE2_HUMAN\nTNRKTKRQPKPNTSM\n", encoding="utf-8")

    exe = write_fake_launcher(auto)
    (auto / "fake_fragpipe.py").unlink(missing_ok=True)  # older testbeds

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
        "fragpipe": {"threads": 4, "ram_gb": 4, "timeout_minutes": 5, "min_free_gb": 0.1},
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
    st = s.add_parser("stress", help="many messy drops + chaos against a real watcher/worker; checks invariants")
    st.add_argument("--n", type=int, default=60, help="number of drops (default 60)")
    st.add_argument("--seed", type=int, default=1)
    st.add_argument("--dir", default=None, help="where to build it (default: a temp folder, deleted afterwards)")
    st.add_argument("--keep", action="store_true", help="keep the temp folder to inspect")
    st.add_argument("--no-chaos", action="store_true")
    st.add_argument("--fuzz", type=int, default=3000, help="random names through the parsers (0 = skip)")
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
    if args.tb_cmd == "stress":
        import logging

        from labwatch import stress

        logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
        bad = stress.fuzz_names(args.fuzz, args.seed) if args.fuzz else []
        print(f"fuzz: {args.fuzz} random names through the parsers — {len(bad)} failure(s)")
        for b in bad[:10]:
            print("  -", b)
        print(f"stress: {args.n} drops, seed {args.seed}{'' if not args.no_chaos else ', no chaos'} …", flush=True)
        rep = stress.run(args.n, args.seed, Path(args.dir) if args.dir else None, keep=args.keep,
                         chaos=not args.no_chaos)
        print(rep.text())
        return 0 if rep.ok and not bad else 1
    if args.tb_cmd == "reset":
        reset(Path(args.dir))
        print("testbed reset")
        return 0
    if args.tb_cmd == "gui-demo":
        return gui_demo()
    return 2
