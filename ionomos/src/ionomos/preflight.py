"""
The FragPipe preflight (`ionomos preflight`, the app's Check FragPipe install): everything that can be found
out about a search before data is dropped, short of searching (D59).

    checks = run(cfg)                       # [Check]; status ok | warn | fail | info
    print(text(checks))

What it does, in order:
    1. the installation, read from disk       fragpipe.install_report (launcher, Java, tools, Python, DIA-NN)
    2. the launcher is started                `fragpipe.bat --help`: it answers with its version, Java and .NET
    3. the PC                                 paths without spaces, free disk, RAM and threads against the
                                              settings, long paths, write permission
    4. each FragPipe method                   workflow reads as a workflow and names tools that are installed,
                                              FASTA with decoys FragPipe accepts
    5. a dry run of each FragPipe method      `--headless --dry-run` on a scratch copy: FragPipe makes all its
                                              own checks and lists the commands it would run, then stops

Nothing here touches an experiment folder. Steps 2 and 5 start FragPipe (no window, no search, a time limit)
with their output in files under <log_dir>/preflight/<time>/; each run gets a new folder, nothing is deleted.
The dry run needs a file to name in the manifest: a 64-byte placeholder .raw it makes itself, or a real file
given with raw=. FragPipe's dry run configures the steps and stops before running any (FragpipeRun.run), so
the file is not opened; that is from FragPipe's source and has not been seen on the PC yet.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ionomos import fragpipe, names
from ionomos.config import Config

HELP_TIMEOUT = 90.0       # seconds for `--help`: a JVM start
DRY_RUN_TIMEOUT = 300.0   # seconds for one method's dry run: FragPipe loads every tool's version first
PATH_BUDGET = 60          # a users folder longer than this leaves little of Windows' 260 characters
MARK = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}


@dataclass
class Check:
    key: str
    title: str
    status: str  # ok | warn | fail | info
    detail: str = ""
    fix: str = ""


def _status(ok: bool | None) -> str:
    return {True: "ok", None: "warn", False: "fail"}[ok]


def scratch_dir(cfg: Config) -> Path:
    """A new folder for this preflight's files: <log_dir>/preflight/<time>/ (Ionomos' own, never user data)."""
    base = Path(cfg.log_dir) / names.PREFLIGHT_DIR
    d = base / datetime.now().strftime("%Y%m%d-%H%M%S")
    n = 1
    while d.exists():
        n += 1
        d = base / f"{datetime.now():%Y%m%d-%H%M%S}-{n}"
    d.mkdir(parents=True)
    return d


def _start(cmd: list[str], env: dict[str, str], out_file: Path, cwd: Path, timeout: float) -> tuple[int | None, str, bool]:
    """Run `cmd` with its output in a file (never a pipe), kill its process tree at the time limit.
    -> (exit code or None, output text, timed out)."""
    timed_out = False
    code: int | None = None
    try:
        with open(out_file, "wb") as out:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(cwd),
                                    env={**os.environ, **env} if env else None, **fragpipe._popen_kwargs())
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                fragpipe.kill_tree(proc)
    except OSError as exc:
        return None, f"could not start: {exc}", False
    return code, fragpipe.read_tail_text(out_file, 2_000_000), timed_out


# ------------------------------------------------------------ the launcher --


def probe_launcher(exe: Path, scratch: Path, timeout: float = HELP_TIMEOUT) -> dict:
    """Start the launcher with --help. FragPipe prints its version, the OS, Java and .NET lines and its usage,
    then exits with code 1 (FragPipeMain: yes, 1), without opening a window.
    -> {'ok', 'version', 'java', 'dotnet', 'code', 'timed_out', 'text'}"""
    env = fragpipe.launcher_env(exe)
    code, text, timed_out = _start([str(exe), "--help"], env, scratch / "launcher_help.log", scratch, timeout)
    ver = re.search(r"^FragPipe v(\S+)", text, re.MULTILINE)
    java = re.search(r"^Java Info: (.*)$", text, re.MULTILINE)
    dotnet = re.search(r"^\.NET Core Info: (.*)$", text, re.MULTILINE)
    return {"ok": bool(ver) and "--headless" in text and not timed_out, "version": ver.group(1) if ver else "",
            "java": java.group(1).strip() if java else "", "dotnet": dotnet.group(1).strip() if dotnet else "",
            "code": code, "timed_out": timed_out, "text": text, "java_home": env.get("JAVA_HOME", "")}


def _launcher_checks(cfg: Config, scratch: Path, start: bool) -> tuple[list[Check], Path | None, dict, list]:
    out: list[Check] = []
    try:
        exe = fragpipe.resolve_launcher(cfg)
    except fragpipe.Hold as exc:
        return [Check("launcher", "FragPipe launcher", "fail", str(exc), "Tab 1 → Find FragPipe, then Save.")], None, {}, []
    rows = fragpipe.install_report(cfg)
    for ok, label, detail in rows:
        fix = ""
        if ok is not True and label in ("MSFragger", "IonQuant", "diaTracer", "Thermo .raw reader"):
            fix = "FragPipe GUI → Config tab → Download / Update (accept the licences)."
        out.append(Check(f"install:{label}", label if label.startswith("FragPipe") or label[0].isupper() else f"FragPipe {label}",
                         _status(ok), detail, fix))
    if exe.suffix.lower() == ".bat" and fragpipe.bundled_java_home(exe) is None and fragpipe.fragpipe_root(exe):
        out.append(Check("java", "Java for fragpipe.bat", "warn",
                         "no jre folder in the FragPipe installation: fragpipe.bat will need JAVA_HOME or java on PATH",
                         "Reinstall FragPipe with its installer (it brings its own Java)."))
    probe: dict = {}
    if not start:
        return out, exe, probe, rows
    probe = probe_launcher(exe, scratch)
    if probe["ok"]:
        out.append(Check("starts", "FragPipe starts", "ok",
                         f"FragPipe {probe['version']} answered --help (exit code {probe['code']}, which is its normal one)"))
        m = re.match(r"(\d+)", probe["java"])
        old = bool(m) and int(m.group(1)) < 11
        out.append(Check("java-version", "Java", "fail" if old else "ok",
                         probe["java"] + (f"  (JAVA_HOME={probe['java_home']})" if probe["java_home"] else "")
                         + ("  — FragPipe needs Java 11 or newer" if old else ""),
                         "Reinstall FragPipe so its own Java is used." if old else ""))
        if probe["dotnet"] and probe["dotnet"] != "N/A":
            out.append(Check("dotnet", ".NET", "ok", probe["dotnet"]))
        else:
            out.append(Check("dotnet", ".NET", "info",
                             "FragPipe found no `dotnet` command. Its documentation doesn't ask for .NET on Windows; "
                             "if a search stops on a .NET message, install the runtime that message names"))
    else:
        hints = fragpipe.explain(probe["text"])
        why = (f"no answer within {HELP_TIMEOUT:.0f} s" if probe["timed_out"] else
               hints[0] if hints else f"exit code {probe['code']}, output: " + " | ".join(
                   ln.strip() for ln in probe["text"].splitlines() if ln.strip())[-300:] or "no output")
        out.append(Check("starts", "FragPipe starts", "fail", f"`{exe.name} --help` did not answer as FragPipe: {why}",
                         f"See {scratch / 'launcher_help.log'}. Tab 1 → Find FragPipe; the launcher is bin\\fragpipe.bat."))
    return out, exe, probe, rows


# ------------------------------------------------------------------ the PC --


def _long_paths_enabled() -> bool | None:
    """Windows' LongPathsEnabled switch (read only); None when it can't be read or this isn't Windows."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return None


def _pc_checks(cfg: Config, exe: Path | None) -> list[Check]:
    from ionomos import health, setupcheck

    out: list[Check] = []
    paths = {"launcher": exe, "users folder": cfg.users_root, "workflows": cfg.workflow_dir, "FASTA folder": cfg.fasta_dir,
             "logs": cfg.log_dir, "tools folder": cfg.config_tools_folder or None, "DIA-NN": cfg.config_diann or None,
             "Python folder": cfg.config_python or None}
    spaced = [f"{k} ({v})" for k, v in paths.items() if v and re.search(r"\s", str(v))]
    odd = [f"{k} ({v})" for k, v in paths.items() if v and not str(v).isascii()]
    if spaced:
        out.append(Check("spaces", "Paths without spaces", "fail" if os.name == "nt" else "warn", "; ".join(spaced),
                         "FragPipe refuses a path with a space: move these (tab 1)."))
    else:
        out.append(Check("spaces", "Paths without spaces", "ok", "launcher, tools, workflows, FASTA and data folders"))
    if odd:
        out.append(Check("ascii", "Plain characters in paths", "warn", "; ".join(odd),
                         "Some FragPipe tools fail on accented or non-Latin characters in a path."))

    free = health.disk_free_gb(cfg.users_root)
    if free is not None:
        low = free < max(cfg.min_free_gb, 1)
        out.append(Check("disk", "Free disk space", "warn" if low else "ok",
                         f"{free:.0f} GB free on the data drive; a search is held below {cfg.min_free_gb:g} GB plus the "
                         f"size of its raw files (FragPipe writes about as much again beside them)",
                         "Free space, or archive finished experiments to D:." if low else ""))
    total, avail = health.memory_gb()
    if total is not None:
        if cfg.ram_gb and cfg.ram_gb > total:
            out.append(Check("ram", "Memory for FragPipe", "fail",
                             f"RAM (GB) is set to {cfg.ram_gb} but the PC has {total:.0f} GB",
                             "Advanced → RAM (GB): about three quarters of the PC's memory."))
        elif cfg.ram_gb and cfg.ram_gb > 0.9 * total:
            out.append(Check("ram", "Memory for FragPipe", "warn",
                             f"RAM (GB) is {cfg.ram_gb} of the PC's {total:.0f} GB: little is left for Windows and DIA-NN",
                             "Advanced → RAM (GB): about three quarters of the PC's memory."))
        else:
            out.append(Check("ram", "Memory for FragPipe", "ok",
                             (f"{cfg.ram_gb} GB of {total:.0f} GB" if cfg.ram_gb else f"FragPipe decides (PC has {total:.0f} GB)")
                             + (f"; {avail:.0f} GB free right now" if avail is not None else "")))
    cpus = os.cpu_count() or 0
    if cpus and cfg.threads > cpus:
        out.append(Check("threads", "Threads", "warn", f"Threads is {cfg.threads} but the PC has {cpus} logical CPUs",
                         "Advanced → Threads: a few fewer than the PC has."))
    elif cpus:
        out.append(Check("threads", "Threads", "ok", f"{cfg.threads or 'FragPipe decides'} of {cpus} logical CPUs"))

    n = len(str(cfg.users_root))
    lp = _long_paths_enabled()
    detail = (f"the users folder path is {n} characters; FragPipe's files sit about 150-200 characters deeper "
              f"(<user>\\<experiment>\\fragpipe\\<sample>_<replicate>\\interact-<raw file>.pep.xml)")
    if lp is not None:
        detail += f"; Windows long paths are {'on' if lp else 'off'} (not every FragPipe tool uses them when on)"
    out.append(Check("longpaths", "Room for long paths", "warn" if n > PATH_BUDGET else "ok", detail,
                     "Keep the users folder near the drive's root and experiment / raw file names short." if n > PATH_BUDGET else ""))

    for label, folder in (("users folder", cfg.users_root), ("logs", cfg.log_dir)):
        ok, why = setupcheck.writable(Path(folder))
        out.append(Check(f"write:{label}", f"Can write to the {label}", "ok" if ok else "fail",
                         str(folder) if ok else f"{folder}: {why}",
                         "" if ok else "Pick a folder this Windows account can write to (tab 1)."))
    return out


# ------------------------------------------------------------- the methods --


def _installed(rows: list[tuple[bool | None, str, str]]) -> dict[str, bool]:
    have = {label: ok is True for ok, label, _d in rows}
    return {"MSFragger": have.get("MSFragger", False), "IonQuant": have.get("IonQuant", False),
            "diaTracer": have.get("diaTracer", False), "DIA-NN": have.get("DIA-NN", False),
            "Python": have.get("Python for FragPipe", False)}


def _is_fragpipe_method(cfg: Config, key: str) -> bool:
    from ionomos import diann, maxquant, sage

    return not (diann.uses_diann(cfg, key) or maxquant.uses_maxquant(cfg, key) or sage.uses_sage(cfg, key))


def needs_tmt(props: dict[str, str]) -> bool:
    return props.get("tmtintegrator.run-tmtintegrator", "").strip().lower() == "true"


def _method_checks(cfg: Config, key: str, rows: list, probe: dict) -> tuple[list[Check], bool]:
    """Static checks of one method. -> (checks, ready for a dry run)."""
    out: list[Check] = []
    title = f"{key}"
    lines = fragpipe.describe_method(cfg, key)
    if not _is_fragpipe_method(cfg, key):
        worst = "fail" if any(o is False for o, _ in lines) else "warn" if any(o is None for o, _ in lines) else "ok"
        return [Check(f"method:{key}", f"{title}: set up", worst, "; ".join(t for _o, t in lines)
                      + "  (not a FragPipe method: no dry run)")], False
    m = cfg.methods[key]
    wf = fragpipe._find_file(m.workflow, cfg.workflow_dir, ".workflow") if m.workflow else None
    if wf is None:
        return [Check(f"method:{key}", f"{title}: workflow", "fail", lines[0][1],
                      "Tab 3 → select the method → Import workflow… (from a run that worked).")], False
    try:
        props = fragpipe.read_properties(wf.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return [Check(f"method:{key}", f"{title}: workflow", "fail", f"{wf}: {exc}")], False
    needs = fragpipe.workflow_needs(props)
    looks = sum(1 for k in props if ".run-" in k)
    saved = props.get("workflow.saved-with-ver", "")
    info = fragpipe.inspect_workflow(wf)
    summary = ", ".join(f"{k}={v}" for k, v in info.get("settings", {}).items()
                        if k != "description" and not (k == "label type" and not needs_tmt(props)))
    if looks < 3:
        out.append(Check(f"method:{key}:workflow", f"{title}: workflow", "fail",
                         f"{wf.name} has {len(props)} settings and almost no <tool>.run-<tool> switches: it doesn't "
                         f"look like a workflow saved by FragPipe",
                         "Tab 3 → Import workflow… → the fragpipe.workflow of a run that worked."))
    else:
        ver = probe.get("version", "")
        mismatch = bool(ver and saved) and saved.split(".")[0].split("-")[0] != ver.split(".")[0]
        out.append(Check(f"method:{key}:workflow", f"{title}: workflow", "warn" if mismatch else "ok",
                         f"{wf.name}: {len(props)} settings [{summary}]"
                         + (f" — saved with FragPipe {saved}, running {ver}: open and save it again in this FragPipe"
                            if mismatch else ""),
                         "FragPipe GUI → load the workflow → Save, then Import workflow… again." if mismatch else ""))
    have = _installed(rows)
    missing = [t for t, needed in needs.items() if needed and not have.get(t, False)]
    used = [t for t, needed in needs.items() if needed]
    if rows and len(rows) > 2:  # the install could be read
        out.append(Check(f"method:{key}:tools", f"{title}: tools it runs", "fail" if missing else "ok",
                         (", ".join(used) or "none of the checked tools")
                         + (f" — not installed: {', '.join(missing)}" if missing else " — all found"),
                         "FragPipe GUI → Config tab: Download / Update the missing tool; for Python, the Python "
                         "section there." if missing else ""))
    for ok, text_ in lines[1:]:  # the FASTA line(s)
        out.append(Check(f"method:{key}:fasta", f"{title}: FASTA", _status(ok), text_,
                         "" if ok else "Tab 3: pick a FASTA with decoys (FragPipe's Database tab → Add decoys)."))
    ready = looks >= 3 and all(c.status != "fail" for c in out if c.key.endswith((":fasta", ":workflow")))
    return out, ready


def dry_run(cfg: Config, key: str, exe: Path, scratch: Path, raw: Path | None = None,
            timeout: float = DRY_RUN_TIMEOUT) -> Check:
    """`fragpipe --headless --dry-run` for one method, on files of its own under scratch/<method>/."""
    m = cfg.methods[key]
    title = f"{key}: FragPipe dry run"
    dest = scratch / fragpipe.fp_experiment(key)
    dest.mkdir(parents=True, exist_ok=True)
    wf = fragpipe._find_file(m.workflow, cfg.workflow_dir, ".workflow")
    fasta = fragpipe._find_file(m.fasta, cfg.fasta_dir) if m.fasta else None
    if raw is not None:
        raws = sorted(p for p in ([raw] if raw.is_file() else raw.rglob("*")) if p.is_file()
                      and p.suffix.lower() in (".raw", ".mzml", ".d"))[:2]
        if not raws:
            return Check(f"method:{key}:dry", title, "fail", f"no .raw / .mzML file at {raw}")
    else:
        placeholder = dest / "raw" / "preflight_1.raw"
        placeholder.parent.mkdir(exist_ok=True)
        placeholder.write_bytes(b"\0" * 64)
        raws = [placeholder]
    lines = [(str(p), "preflight", i + 1, m.data_type) for i, p in enumerate(raws)]
    spec = fragpipe.RunSpec(job_id=0, method=key, dest=dest, exe=exe, workflow_src=wf, fasta=fasta, manifest_lines=lines,
                            threads=cfg.threads, ram_gb=cfg.ram_gb, timeout_minutes=0,
                            config_tools_folder=cfg.config_tools_folder, config_diann=cfg.config_diann,
                            kind=cfg.kind(key), config_python=cfg.config_python, env=fragpipe.launcher_env(exe))
    try:
        fragpipe.write_inputs(spec)
    except OSError as exc:
        return Check(f"method:{key}:dry", title, "fail", f"could not write the dry run's files in {dest}: {exc}")
    cmd = spec.command()
    cmd.insert(2, "--dry-run")
    log = spec.run_dir / "dry_run_console.log"
    started = time.monotonic()
    code, text_, timed_out = _start(cmd, spec.env, log, spec.run_dir, timeout)
    took = time.monotonic() - started
    facts = fragpipe.console_facts(text_)
    if timed_out:
        return Check(f"method:{key}:dry", title, "fail", f"no end within {timeout:.0f} s; stopped. Log: {log}",
                     "Run it again; if it repeats, send the log (Report a problem…).")
    if code == 0 and facts["dry_run"]:
        versions = dict(re.findall(r"^([A-Za-z][\w .\-]{1,40}) version (.+)$", text_, re.MULTILINE))
        needs = fragpipe.workflow_needs(fragpipe.read_properties(spec.workflow.read_text(encoding="utf-8", errors="replace")))
        unset = [t for t, needed in needs.items() if needed and t != "Python" and versions.get(t, "").strip() in ("N/A", "")]
        shown = ", ".join(f"{t} {versions[t].strip()}" for t in ("FragPipe", "MSFragger", "IonQuant", "DIA-NN", "diaTracer")
                          if versions.get(t, "").strip() not in ("", "N/A"))
        detail = (f"FragPipe accepted the workflow, FASTA and file list and would run {facts['commands'] or '?'} "
                  f"commands ({took:.0f} s). {shown}")
        if unset and versions:
            return Check(f"method:{key}:dry", title, "warn", detail + f" — but it reports no version for {', '.join(unset)}, "
                         f"which the workflow runs", "FragPipe GUI → Config tab: check these tools.")
        return Check(f"method:{key}:dry", title, "ok", detail + ("" if raw else "  (file list: a placeholder .raw)"))
    hints = fragpipe.explain(text_)
    said = facts["error_lines"] or [ln.strip() for ln in text_.splitlines() if ln.strip()][-3:]
    return Check(f"method:{key}:dry", title, "fail",
                 f"FragPipe refused (exit code {code}): " + " | ".join(said)[-500:] + f"  Log: {log}",
                 hints[0] if hints else "Read the log's first ERROR line; fix it in the FragPipe GUI or the method (tab 3).")


# --------------------------------------------------------------------- run --


def run(cfg: Config, dry: bool = True, methods: list[str] | None = None, raw: Path | None = None,
        start: bool = True) -> list[Check]:
    """Every check. start=False reads the disk only (no FragPipe process); dry=False skips the dry runs."""
    try:
        scratch = scratch_dir(cfg) if start else Path(cfg.log_dir)
    except OSError as exc:
        return [Check("scratch", "Preflight folder", "fail", f"could not make a folder under {cfg.log_dir}: {exc}",
                      "The log folder must exist and be writable (tab 1).")]
    checks, exe, probe, rows = _launcher_checks(cfg, scratch, start)
    checks += _pc_checks(cfg, exe)
    for key in (methods or list(cfg.methods)):
        if key not in cfg.methods:
            checks.append(Check(f"method:{key}", key, "fail", "no such method in config.yaml"))
            continue
        mchecks, ready = _method_checks(cfg, key, rows, probe)
        checks += mchecks
        if not (dry and start) or exe is None or not _is_fragpipe_method(cfg, key):
            continue
        if not ready:
            checks.append(Check(f"method:{key}:dry", f"{key}: FragPipe dry run", "info",
                                "skipped until the workflow and FASTA above are in order"))
        elif probe and not probe.get("ok"):
            checks.append(Check(f"method:{key}:dry", f"{key}: FragPipe dry run", "info",
                                "skipped: the launcher did not start (above)"))
        else:
            checks.append(dry_run(cfg, key, exe, scratch, raw))
    if start:
        checks.append(Check("files", "This preflight's files", "info", str(scratch)))
    return checks


def text(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f" {MARK[c.status]} {c.title:<30} {c.detail}")
        if c.fix and c.status in ("fail", "warn"):
            lines.append(f"   {'':<30} → {c.fix}")
    bad = [c for c in checks if c.status == "fail"]
    warn = [c for c in checks if c.status == "warn"]
    if bad:
        lines.append(f"\n{len(bad)} thing(s) to fix before a search can work (✗ above)"
                     + (f", {len(warn)} to look at (!)" if warn else "") + ".")
    elif warn:
        lines.append(f"\nNothing blocks a search. {len(warn)} thing(s) to look at (! above).")
    else:
        lines.append("\nFragPipe is ready for a search as far as this can tell without running one.")
    return "\n".join(lines)


def main(cfg: Config, dry: bool = True, methods: list[str] | None = None, raw: Path | None = None,
         out=sys.stdout) -> int:
    checks = run(cfg, dry=dry, methods=methods, raw=raw)
    print(text(checks), file=out)
    return 1 if any(c.status == "fail" for c in checks) else 0
