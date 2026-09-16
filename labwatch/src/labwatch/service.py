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
    and labwatch.exe (console: check/status/dry-run). `console=True` picks the
    console one when it exists so captured output actually comes back.
    """
    if getattr(sys, "frozen", False):
        if os.name == "nt":
            want = "labwatch.exe" if console else "LabWatch.exe"
            other = _sibling(want)
            if other and other.name != Path(sys.executable).name:
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


def stop_process(proc: subprocess.Popen, timeout: float = 5) -> None:
    if proc.poll() is not None:
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
        pid = int(p.read_text().strip())
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
