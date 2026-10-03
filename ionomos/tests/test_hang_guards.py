"""D73: a hang in the suite reports itself (every thread's stack) and ends the run, instead of eating an outer time
limit with nothing on screen. Checked by running pytest on two tiny hanging tests, with this suite's settings and
conftest and the time limits cut to seconds."""
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _settings() -> dict:
    return tomllib.loads((HERE.parent / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]


def _pytest(folder: Path, test_body: str, timeout: float, env_extra: dict | None = None) -> tuple[int, str, float]:
    import os

    tests = folder / "tests"
    tests.mkdir(parents=True)
    shutil.copy(HERE / "conftest.py", tests / "conftest.py")
    (tests / "test_it.py").write_text(test_body, encoding="utf-8")
    (folder / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        f"faulthandler_timeout = {timeout}\nfaulthandler_exit_on_timeout = true\n", encoding="utf-8")
    env = {**os.environ, **(env_extra or {})}
    started = time.monotonic()
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                       cwd=folder, capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr, time.monotonic() - started


def test_the_suite_ends_a_hung_test_and_says_where_it_hung():
    s = _settings()
    assert float(s["faulthandler_timeout"]) <= 600 and s["faulthandler_exit_on_timeout"] is True


def test_a_hung_test_is_dumped_and_the_run_ends(tmp_path):
    body = ("import threading\n\n"
            "def test_waits_for_ever():\n    threading.Event().wait()\n")
    code, out, took = _pytest(tmp_path, body, timeout=2)
    assert code != 0 and took < 60, out
    assert "Timeout (0:00:02)!" in out and "test_waits_for_ever" in out, out


def test_a_run_that_cannot_exit_after_its_last_test_is_dumped_and_ended(tmp_path):
    """Every test passed, then a thread nobody stops keeps the interpreter from exiting."""
    body = ("import threading\n\n"
            "def test_leaves_a_thread_behind():\n"
            "    threading.Thread(target=threading.Event().wait, name='forgotten').start()\n")
    code, out, took = _pytest(tmp_path, body, timeout=60, env_extra={"IONOMOS_TEST_EXIT_SECONDS": "2"})
    assert "1 passed" in out and code != 0 and took < 60, out
    assert "forgotten" in out and "_shutdown" in out, out
