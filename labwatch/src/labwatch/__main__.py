"""`python -m labwatch` and the PyInstaller entry point."""
import multiprocessing
import sys

from labwatch.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main(sys.argv[1:]))
