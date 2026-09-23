"""
Process + OS glue for the GUI: where the config lives, launching the watcher
as a child process, Task Scheduler on Windows, opening folders, PID files.

Config path precedence (used by the app when no --config is given):
    LABWATCH_CONFIG env  >  <appdata>/config_path.txt  >  ./config.yaml
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "labwatch"
TASK_NAME = "labwatch"


# ------------------------------------------------------------- app data ----


def appdata_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path.home() / ".config"
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def remembered_config_path() -> Path | None:
    p = appdata_dir() / "config_path.txt"
    if p.is_file():
        s = p.read_text(encoding="utf-8").strip()
        if s:
            return Path(s)
    return None


def remember_config_path(path: Path) -> None:
    (appdata_dir() / "config_path.txt").write_text(str(Path(path).resolve()), encoding="utf-8")


def default_config_path() -> Path:
    env = os.environ.get("LABWATCH_CONFIG")
    if env:
        return Path(env)
    r = remembered_config_path()
    if r:
        return r
    if getattr(sys, "frozen", False):  # exe: config next to it
        return Path(sys.executable).parent / "config.yaml"
    return Path("config.yaml")


# ------------------------------------------------------------ launching ----


def _sibling(name: str) -> Path | None:
    p = Path(sys.executable).parent / name
    return p if p.is_file() else None


def labwatch_command(console: bool = False) -> list[str]:
    """argv prefix that runs the labwatch CLI, whether frozen or from a venv.

    Frozen builds ship two exes: LabWatch.exe (windowed: the app, and `run`)
    and labwatch-cli.exe (console: check/status/dry-run). `console=True` picks the
    console one when it exists so captured output actually comes back.
    """
    if getattr(sys, "frozen", False):
        if os.name == "nt":
            want = "labwatch-cli.exe" if console else "LabWatch.exe"
            other = _sibling(want)
            if other and other.name.lower() != Path(sys.executable).name.lower():
                return [str(other)]
        return [sys.executable]
    return [sys.executable, "-m", "labwatch.cli"]


def _creationflags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def start_watcher(config: Path, no_gui: bool = False) -> subprocess.Popen:
    """Launch `labwatch run` as a child; stdout/stderr go to <log_dir>/labwatch.log via the app itself."""
    cmd = [*labwatch_command(), "--config", str(config), "run"]
    if no_gui:
        cmd.append("--no-gui")
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_creationflags())


STOP_FILE = "STOP"


def request_stop(log_dir: Path, pid: int | None = None, proc: subprocess.Popen | None = None,
                 timeout: float = 20) -> str:
    """Stop a watcher gracefully (it kills its FragPipe, re-queues the job, releases its lock), else force it.

    The watcher polls <log_dir>/STOP every second. If it hasn't exited after
    `timeout` s, the whole process tree is killed (taskkill /T on Windows —
    plain terminate() there would orphan FragPipe's Java processes).
    """
    import time

    from labwatch import health

    log_dir = Path(log_dir)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / STOP_FILE).write_text("stop requested\n", encoding="utf-8")
    except OSError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = proc.poll() is None if proc is not None else health.is_locked(log_dir)
        if not alive:
            clear_stop(log_dir)
            return "stopped"
        time.sleep(0.5)
    target = proc.pid if proc is not None else (pid or health.holder_pid(log_dir))
    if target:
        kill_pid(target)
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    clear_stop(log_dir)
    return f"forced (did not stop within {timeout:.0f}s)"


def clear_stop(log_dir: Path) -> None:
    try:
        (Path(log_dir) / STOP_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def stop_process(proc: subprocess.Popen, timeout: float = 5) -> None:
    """Hard stop of a child process tree (fallback; prefer request_stop)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        kill_pid(proc.pid)  # taskkill /T: takes FragPipe's java children with it
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        proc.kill()


def run_cli(args: list[str], config: Path | None = None, timeout: float = 120) -> tuple[int, str]:
    """Run a labwatch CLI command and capture its combined output."""
    cmd = [*labwatch_command(console=True)]
    if config is not None:
        cmd += ["--config", str(config)]
    cmd += args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_creationflags(),
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 1, "timed out"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def pid_file(log_dir: Path) -> Path:
    return Path(log_dir) / "labwatch.pid"


def write_pid(log_dir: Path) -> None:
    try:
        pid_file(log_dir).write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def clear_pid(log_dir: Path) -> None:
    try:
        pid_file(log_dir).unlink()
    except OSError:
        pass


def running_pid(log_dir: Path) -> int | None:
    """PID from the pid file if that process is still alive."""
    p = pid_file(log_dir)
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if _alive(pid):
        return pid
    clear_pid(log_dir)
    return None


def _alive(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                           creationflags=_creationflags())
        return str(pid) in (r.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=_creationflags())
    else:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


# ------------------------------------------------------- task scheduler ----


def task_status() -> str:
    """'installed' | 'missing' | 'n/a' (non-Windows)."""
    if os.name != "nt":
        return "n/a"
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True,
                       creationflags=_creationflags())
    return "installed" if r.returncode == 0 else "missing"


def install_task(config: Path) -> tuple[bool, str]:
    """Register a logon task that runs the watcher interactively (so the resolver window can show)."""
    if os.name != "nt":
        return False, "Task Scheduler is Windows-only. On macOS use a LaunchAgent or just leave the app running."
    cmd = " ".join(f'"{c}"' if " " in c else c for c in [*labwatch_command(), "--config", str(config), "run"])
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", cmd, "/SC", "ONLOGON", "/RU", user,
                        "/IT", "/RL", "LIMITED", "/F"], capture_output=True, text=True, creationflags=_creationflags())
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def remove_task() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "n/a"
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True,
                       creationflags=_creationflags())
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def run_task() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "n/a"
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True, text=True,
                       creationflags=_creationflags())
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


# ------------------------------------------------------------- desktop ----


def open_path(p: Path) -> None:
    """Open a folder/file with the OS file manager or default app."""
    p = Path(p)
    if os.name == "nt":
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


def create_desktop_shortcut() -> tuple[bool, str]:
    """Windows: a .lnk on the Desktop pointing at the exe (GUI mode)."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False, "shortcuts are created only for the Windows executable"
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    lnk = desktop / "LabWatch.lnk"
    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        f"$s.TargetPath='{sys.executable}';$s.WorkingDirectory='{Path(sys.executable).parent}';"
        "$s.Description='LabWatch setup & control';$s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       creationflags=_creationflags())
    return r.returncode == 0, str(lnk) if r.returncode == 0 else (r.stderr or r.stdout)


# ------------------------------------------------- dev install (git checkout) ----
#
# While prototyping, the PC runs labwatch straight from a git clone with an
# editable install (deploy/dev_install.ps1). Then "update" = git pull, and no
# exe has to be rebuilt. These helpers detect that situation and drive it.


def source_checkout() -> Path | None:
    """Repo root if this package is imported from a git checkout (editable install); else None."""
    if getattr(sys, "frozen", False):
        return None
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() and (parent / "labwatch" / "pyproject.toml").is_file():
            return parent
    return None


def _git(repo: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=_creationflags())
    except FileNotFoundError:
        return 127, "git is not installed (https://git-scm.com/download/win)"
    except subprocess.TimeoutExpired:
        return 1, "git timed out (no network?)"
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def git_describe(repo: Path) -> str:
    """'<branch> @ <short sha> (<date>)' or '?'."""
    code, out = _git(repo, "log", "-1", "--format=%h %cs", timeout=10)
    if code != 0:
        return "?"
    _, branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", timeout=10)
    sha, _, date = out.partition(" ")
    return f"{branch} @ {sha} ({date})"


def updates_available(repo: Path) -> tuple[int | None, str]:
    """(number of commits behind origin, message). None = could not check."""
    code, out = _git(repo, "fetch", "--quiet", timeout=60)
    if code != 0:
        return None, out or "fetch failed"
    code, out = _git(repo, "rev-list", "--count", "HEAD..@{u}", timeout=10)
    if code != 0:
        return None, out
    try:
        return int(out.strip()), ""
    except ValueError:
        return None, out


def update_source(repo: Path, log=print) -> tuple[bool, str]:
    """git pull --ff-only, then reinstall the package (editable) so new deps/entry points land.

    Refuses if there are local edits (someone changed code on the PC by hand):
    those should be committed or reverted deliberately, never clobbered.
    """
    code, dirty = _git(repo, "status", "--porcelain", "--untracked-files=no", timeout=10)
    if code != 0:
        return False, dirty
    if dirty.strip():
        return False, ("local changes on this machine would be overwritten:\n" + dirty +
                       "\nRun 'git stash' (or 'git checkout .') in " + str(repo) + " first.")
    before = _git(repo, "rev-parse", "--short", "HEAD", timeout=10)[1]
    log(f"git pull ({repo})")
    code, out = _git(repo, "pull", "--ff-only", timeout=180)
    log(out)
    if code != 0:
        return False, out
    after = _git(repo, "rev-parse", "--short", "HEAD", timeout=10)[1]
    if before != after:
        _, changes = _git(repo, "log", "--oneline", f"{before}..{after}", timeout=10)
        log("changes:\n" + changes)
    log("pip install -e (a few seconds)")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", str(repo / "labwatch")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_creationflags())
    if r.returncode != 0:
        return False, (r.stdout or "") + (r.stderr or "")
    return True, f"updated {before} -> {after}" if before != after else "already up to date"


def restart_app() -> None:
    """Start a fresh copy of the app (so updated code is loaded) and exit this one."""
    cmd = [*labwatch_command(), "setup"]
    subprocess.Popen(cmd, creationflags=_creationflags(), close_fds=True)
    os._exit(0)


# ------------------------------------------------------------ diagnostics ----


def _raw_paths(config_path: Path) -> dict:
    """paths: section of a config, tolerating a config that doesn't validate."""
    try:
        import yaml

        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        return {k: Path(v) for k, v in (raw.get("paths") or {}).items() if isinstance(v, str) and v}
    except Exception:  # noqa: BLE001
        return {}


def _capture(fn, *args) -> str:
    import io
    import traceback
    from contextlib import redirect_stderr, redirect_stdout

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            fn(*args)
    except SystemExit as exc:
        buf.write(f"(exit {exc.code})")
    except Exception:  # noqa: BLE001 - diagnostics must never crash
        buf.write(traceback.format_exc())
    return buf.getvalue()


def _problem_jobs(db: Path, limit_failed: int = 5):
    """Running, waiting and the most recent failed jobs, newest last."""
    from labwatch.ledger import Ledger, integrity

    if integrity(db) != "ok":
        return []
    led = Ledger(db)
    try:
        jobs = led.list()
    finally:
        led.close()
    running = [j for j in jobs if j.status == "running"]
    waiting = [j for j in jobs if j.status == "queued" and (j.reason or "").startswith("waiting:")]
    failed = [j for j in jobs if j.status == "failed"][-limit_failed:]
    return running + waiting + failed


def diagnostics(config_path: Path, log_lines: int = 150) -> str:
    """One text block with everything needed to debug a report from the PC."""
    import datetime
    import json
    import os as _os
    import platform

    from labwatch import __version__, fragpipe, health
    from labwatch import ledger as ledger_mod

    out: list[str] = []
    a = out.append

    def section(title: str, body: str):
        a("")
        a(f"=== {title} " + "=" * max(0, 60 - len(title)))
        a((body or "").rstrip() or "(nothing)")

    a(f"labwatch {__version__}  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    a(f"python {sys.version.split()[0]}  {platform.platform()}  exe={sys.executable}")
    src = source_checkout()
    if getattr(sys, "frozen", False):
        install = "frozen exe"
    elif src:
        install = f"git checkout {src}  {git_describe(src)}"
    else:
        install = "pip package"
    a(f"install: {install}")
    a(f"config: {config_path}  (exists={Path(config_path).is_file()})")

    paths = _raw_paths(config_path)
    log_dir = paths.get("log_dir")
    inbox = paths.get("inbox")
    db = paths.get("database")

    # --- system
    total, avail = health.memory_gb()
    sysl = [f"cpus: {_os.cpu_count()}   RAM: {total:.0f} GB total" if total else f"cpus: {_os.cpu_count()}"]
    if avail:
        sysl[0] += f", {avail:.0f} GB free"
    seen = set()
    for key in ("users_root", "inbox", "log_dir"):
        pth = paths.get(key)
        if pth is None:
            continue
        anchor = Path(pth).anchor or "/"
        if anchor in seen:
            continue
        seen.add(anchor)
        free = health.disk_free_gb(pth)
        sysl.append(f"disk {anchor}: {free:.0f} GB free" if free is not None else f"disk {anchor}: ?")
    section("system", "\n".join(sysl))

    # --- watcher state
    st = [f"startup task: {task_status()}"]
    if log_dir:
        from labwatch.worker import paused

        locked = health.is_locked(log_dir)
        hb, healthy = health.heartbeat_summary(log_dir)
        st.append(f"watcher: {'RUNNING pid ' + str(health.holder_pid(log_dir)) if locked else 'not running'}")
        st.append(f"heartbeat: {hb}" + ("" if healthy or not locked else "   <-- NOT RESPONDING"))
        st.append(f"searches paused: {paused(log_dir)}")
    section("watcher", "\n".join(st))

    from labwatch import cli

    class _A:  # minimal args namespace for cmd_check/cmd_status
        config = str(config_path)
        all = True

    section("check", _capture(cli.cmd_check, _A()))
    section("status --all", _capture(cli.cmd_status, _A()))

    # --- problem jobs in detail
    if db:
        for j in _problem_jobs(db):
            dest = Path(j.dest_dir)
            body = [f"status: {j.status}  attempts: {j.attempts}  user: {j.user}  method: {j.method}",
                    f"folder: {dest}", f"reason: {j.reason or '-'}"]
            try:
                rec = json.loads((dest / "labwatch.json").read_text(encoding="utf-8"))
                run = rec.get("run") or {}
                for k in ("started_at", "finished_at", "exit_code", "progress", "workflow_source", "fasta"):
                    if run.get(k) is not None:
                        body.append(f"{k}: {run[k]}")
                if run.get("command"):
                    body.append("command: " + " ".join(run["command"]))
                for w in run.get("warnings") or []:
                    body.append(f"warning: {w}")
            except (OSError, ValueError):
                body.append("(labwatch.json unreadable)")
            console = dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG
            text = fragpipe.read_tail_text(console, 60_000)
            for h in fragpipe.explain(text):
                body.append(f"likely cause: {h}")
            if j.status == "running":
                body.append(f"progress: {fragpipe.progress(console)}")
            if text:
                body.append(f"--- last 30 lines of {console.name}")
                body += text.splitlines()[-30:]
            section(f"job {j.id} {j.inbox_name}", "\n".join(body))

    cfg_text = ""
    try:
        cfg_text = Path(config_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        cfg_text = f"(could not read: {exc})"
    section("config.yaml", cfg_text)

    if log_dir and log_dir.is_dir():
        lf = log_dir / "labwatch.log"
        section("recent warnings/errors", "\n".join(health.log_problems(lf, 25)))
        if lf.is_file():
            lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
            section(f"log tail ({lf}, last {log_lines} of {len(lines)} lines)", "\n".join(lines[-log_lines:]))
        else:
            section("log", f"no {lf}")
        for c in health.recent_crashes(log_dir, 3):
            section(f"crash report {c.name}", c.read_text(encoding="utf-8", errors="replace")[-6000:])
        exe_crash = Path(sys.executable).parent / "LabWatch-crash.txt"
        if exe_crash.is_file():
            section("LabWatch-crash.txt (app start-up crash)", exe_crash.read_text(encoding="utf-8", errors="replace")[-4000:])
    if db:
        backups = sorted((log_dir / "backups").glob("labwatch-*.db")) if log_dir else []
        size = f"{db.stat().st_size / 1e6:.1f} MB" if db.is_file() else "-"
        section("ledger", f"{db}  {size}  integrity: {ledger_mod.integrity(db)}\n"
                          f"backups: {', '.join(b.name for b in backups[-5:]) or 'none yet'}")
    if inbox and inbox.is_dir():
        notes = sorted(inbox.glob("*.REJECTED.txt"))
        items = sorted(p.name + ("/" if p.is_dir() else "") for p in inbox.iterdir())
        section("inbox", "\n".join(items) or "(empty)")
        for n in notes[-5:]:
            section(f"note {n.name}", n.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out) + "\n"


def save_diagnostics(config_path: Path) -> tuple[str, Path | None]:
    """Build the diagnostics text and also save it next to the log (if a log_dir exists)."""
    import datetime

    text = diagnostics(config_path)
    where: Path | None = None
    log_dir = _raw_paths(config_path).get("log_dir")
    try:
        if log_dir and log_dir.is_dir():
            where = log_dir / f"diagnostics-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"
            where.write_text(text, encoding="utf-8")
    except OSError:
        where = None
    return text, where


def save_diagnostics_zip(config_path: Path, dest: Path | None = None) -> Path:
    """A .zip with the report, config, logs, crash reports and the problem jobs' FragPipe logs.

    Never includes raw data or FragPipe result tables — only small text files,
    with each log capped at its last 3 MB.
    """
    import datetime
    import tempfile
    import zipfile

    from labwatch import fragpipe, health

    stamp = f"{datetime.datetime.now():%Y%m%d-%H%M%S}"
    paths = _raw_paths(config_path)
    log_dir = paths.get("log_dir")
    if dest is None:
        base = log_dir if log_dir and log_dir.is_dir() else Path(tempfile.gettempdir())
        dest = base / f"diagnostics-{stamp}.zip"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cap = 3_000_000

    def add(z: zipfile.ZipFile, src: Path, arc: str):
        try:
            if src.is_file():
                data = src.read_bytes()
                z.writestr(arc, data[-cap:] if len(data) > cap else data)
        except OSError:
            pass

    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", diagnostics(config_path))
        add(z, Path(config_path), "config.yaml")
        if log_dir and log_dir.is_dir():
            for f in sorted(log_dir.glob("labwatch.log*"))[:3]:
                add(z, f, f"logs/{f.name}")
            for f in health.recent_crashes(log_dir, 5):
                add(z, f, f"crashes/{f.name}")
            add(z, log_dir / health.HEARTBEAT_NAME, "logs/heartbeat.json")
        db = paths.get("database")
        if db:
            for j in _problem_jobs(db, limit_failed=5):
                d = Path(j.dest_dir)
                arc = f"jobs/{j.id}-{j.inbox_name}"
                for rel in ("labwatch.json", "FAILED.txt", "DONE.txt", "experiment.yaml",
                            f"{fragpipe.RUN_DIR}/{fragpipe.CONSOLE_LOG}", f"{fragpipe.RUN_DIR}/{fragpipe.MANIFEST_NAME}"):
                    add(z, d / rel, f"{arc}/{rel}")
                wf = next((d / fragpipe.RUN_DIR).glob("*.workflow"), None) if (d / fragpipe.RUN_DIR).is_dir() else None
                if wf:
                    add(z, wf, f"{arc}/{fragpipe.RUN_DIR}/{wf.name}")
    return dest
