"""A stand-in search engine that misbehaves on purpose (tests/test_engine_faults.py).

Run as `python standin_engine.py [anything]`: what it does comes from the environment, so the same program
stands in for FragPipe's launcher, DIA-NN, MaxQuant or Sage whatever arguments the runner passes.

    IONOMOS_STANDIN       comma-separated steps, done in order:
        say=<text>            print a line (a backslash-n in it: a line break; no commas)
        garbage               print lines of printable nonsense (no error word in them)
        binary                write raw bytes: NULs, 0xFF, half a UTF-8 letter, colour codes, no line end
        table=<rel>           write a whole three-column table at <out>/<rel>
        partial=<rel>         a table cut off in the middle of a row
        empty=<rel>           a zero-byte file
        header=<rel>          a table with its header line only
        grandchild=<secs>     start a process that sleeps <secs> s and outlives this one; its pid goes to
                              <out>/grandchild.pid
        lockchild=<rel>       a grandchild that opens <out>/<rel> and holds a Windows lock on it (POSIX: holds
                              it open) for 60 s; its pid goes to <out>/grandchild.pid
        flood=<bytes>         print that many bytes as fast as it can (0 = for ever)
        marker                create <out>/standin.started (the tests' sign that it is running)
        hang                  sleep for ever
        sleep=<secs>          sleep
        exit=<n>              exit with code n (any int; Windows takes it as unsigned 32-bit)
        signal=<n>            POSIX: end itself with signal n; Windows: exit 1
    IONOMOS_STANDIN_OUT   the folder the outputs go to (the engine's work folder)
    IONOMOS_STANDIN_HELP  the steps instead when it is started with --help (a launcher answering, then a dry run)

No step = print one line and exit 0. Stdlib only; never imports Ionomos.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

GARBAGE = "qz@#; ~~ 0x1F 77% |||| \\\\ &&& {}{}{} ...... zzzz"
SLEEPER = "import sys, time; time.sleep(float(sys.argv[1]))"
LOCKER = r"""
import os, sys, time
path, secs = sys.argv[1], float(sys.argv[2])
fh = open(path, "r+b")
if os.name == "nt":
    import msvcrt
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, max(1, os.path.getsize(path)))
open(path + ".locked", "w").close()
time.sleep(secs)
"""


def _out() -> Path:
    out = Path(os.environ.get("IONOMOS_STANDIN_OUT") or ".")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write(rel: str, data: bytes) -> Path:
    p = _out() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _spawn(code: str, *args: str) -> int:
    """A process of our own that is not waited for: it outlives us."""
    child = subprocess.Popen([sys.executable, "-c", code, *args], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (_out() / "grandchild.pid").write_text(str(child.pid), encoding="utf-8")
    return child.pid


def main() -> int:
    which = "IONOMOS_STANDIN_HELP" if "--help" in sys.argv and "IONOMOS_STANDIN_HELP" in os.environ else "IONOMOS_STANDIN"
    steps = [s for s in os.environ.get(which, "").split(",") if s]
    out = sys.stdout.buffer
    if not steps:
        print("stand-in engine: nothing to do", flush=True)
        return 0
    for step in steps:
        name, _, arg = step.partition("=")
        if name == "say":
            print(arg.replace("\\n", "\n"), flush=True)  # a written \n is a line break
        elif name == "garbage":
            for i in range(50):
                print(f"{i:03d} {GARBAGE[i % 7:]}{GARBAGE[: i % 11]}", flush=True)
        elif name == "binary":
            out.write(bytes(range(256)) + b"\x00\x00\xff\xfe\xc3" + b"\x1b[31m\x1b[0m" + b"\xe2\x82" * 40)
            out.flush()
        elif name == "table":
            _write(arg, b"Protein\tA_1\tB_1\nP1\t1.0\t2.0\nP2\t3.0\t4.0\n")
        elif name == "partial":
            _write(arg, b"Protein\tA_1\tB_1\nP1\t1.0\t2.0\nP2\t3.")
        elif name == "empty":
            _write(arg, b"")
        elif name == "header":
            _write(arg, b"Protein\tA_1\tB_1\n")
        elif name == "grandchild":
            _spawn(SLEEPER, arg or "60")
        elif name == "lockchild":
            p = _out() / arg
            if not p.exists():
                _write(arg, b"Protein\tA_1\tB_1\nP1\t1.0\t2.0\n")
            _spawn(LOCKER, str(p), "60")
            end = time.monotonic() + 30
            while not Path(str(p) + ".locked").exists() and time.monotonic() < end:
                time.sleep(0.02)
        elif name == "flood":
            want, done = int(arg or 0), 0
            chunk = (b"loop " * 200 + b"\n") * 100
            while not want or done < want:
                out.write(chunk)
                done += len(chunk)
            out.flush()
        elif name == "marker":
            (_out() / "standin.started").write_text(str(os.getpid()), encoding="utf-8")
        elif name == "hang":
            while True:
                time.sleep(1)
        elif name == "sleep":
            time.sleep(float(arg))
        elif name == "exit":
            sys.stdout.flush()
            code = int(arg)
            if os.name == "nt":  # ExitProcess takes 32 bits; os._exit a C int
                code &= 0xFFFFFFFF
                code -= (1 << 32) if code >= (1 << 31) else 0
            os._exit(code)
        elif name == "signal":
            sys.stdout.flush()
            if os.name == "nt":
                os._exit(1)
            os.kill(os.getpid(), int(arg))
            time.sleep(5)
        else:
            print(f"stand-in engine: unknown step {step!r}", flush=True)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
