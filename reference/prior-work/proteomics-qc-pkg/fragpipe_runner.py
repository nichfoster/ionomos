"""
FragPipe runner — builds a one-file manifest and invokes headless FragPipe.

Headless invocation (confirmed against current FragPipe docs):
    Windows:  fragpipe.bat --headless --workflow <wf> --manifest <mf> --workdir <out>
    optional: --ram <GB>  --threads <N>
              --config-tools-folder <folder>     (MSFragger/IonQuant/etc.)
              --config-diann <path to DiaNN.exe> (DIA only)

Manifest (.fp-manifest) format — TAB-separated, one line per LC-MS file:
    <path to LC-MS file>\t<experiment>\t<bioreplicate>\t<data type>
e.g.  C:/data/QC_01.raw    qc    1    DDA

IMPORTANT version/setup notes baked in as guidance:
  * The FASTA path lives INSIDE the .workflow file, not the manifest. A common
    headless failure is "FASTA file path is empty" — that means the workflow
    was saved without a database. Save the workflow from the GUI via
    "Save to custom folder" AFTER setting the FASTA, with decoys added.
  * The FIRST headless run on a machine may need the tools folder (and, for
    DIA, the DIA-NN exe) specified so FragPipe knows where its binaries are.
    Set these in config.yaml IF your runs fail to locate tools; once cached,
    they're optional. They're wired in below but only passed if set.

  >>> CONFIRM-ON-YOUR-INSTALL markers flag the launcher name and the optional
  >>> tool-config paths to verify for your FragPipe version/layout.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path


class FragPipeError(RuntimeError):
    """Raised when a FragPipe headless run fails (non-zero exit or timeout)."""


def build_manifest(
    raw_file: str,
    acquisition: str,
    manifest_path: str,
    experiment: str = "qc",
    bioreplicate: int = 1,
) -> str:
    """Write a single-file .fp-manifest and return its path.

    acquisition must be 'DDA' or 'DIA' (the manifest's data-type column).
    """
    acq = acquisition.upper()
    if acq not in ("DDA", "DIA"):
        raise ValueError(f"acquisition must be DDA or DIA, got {acquisition!r}")

    # FragPipe is happiest with forward slashes even on Windows; normalise.
    raw_norm = str(Path(raw_file)).replace("\\", "/")
    line = f"{raw_norm}\t{experiment}\t{bioreplicate}\t{acq}\n"

    mpath = Path(manifest_path)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    with open(mpath, "w", encoding="utf-8", newline="") as fh:
        fh.write(line)
    return str(mpath)


def build_command(
    fragpipe_exe: str,
    workflow: str,
    manifest: str,
    workdir: str,
    threads: int | None = None,
    ram_gb: int | None = None,
    config_tools_folder: str | None = None,   # CONFIRM-ON-YOUR-INSTALL (optional)
    config_diann: str | None = None,          # CONFIRM-ON-YOUR-INSTALL (DIA only)
) -> list[str]:
    """Assemble the headless FragPipe command as an argv list (no shell)."""
    cmd = [
        fragpipe_exe,
        "--headless",
        "--workflow", workflow,
        "--manifest", manifest,
        "--workdir", workdir,
    ]
    if threads is not None:
        cmd += ["--threads", str(threads)]
    if ram_gb is not None:
        cmd += ["--ram", str(ram_gb)]
    if config_tools_folder:
        cmd += ["--config-tools-folder", config_tools_folder]
    if config_diann:
        cmd += ["--config-diann", config_diann]
    return cmd


def run_fragpipe(
    raw_file: str,
    acquisition: str,
    fragpipe_exe: str,
    workflow: str,
    output_dir: str,
    threads: int | None = None,
    ram_gb: int | None = None,
    timeout_minutes: int = 90,
    config_tools_folder: str | None = None,
    config_diann: str | None = None,
    log_path: str | None = None,
) -> str:
    """Run one headless FragPipe search for a single raw file.

    Creates output_dir, writes a manifest into it, runs FragPipe, and tees
    stdout/stderr to a log file (output_dir/fragpipe_run.log by default).

    Returns the output_dir on success. Raises FragPipeError on failure or
    timeout, so the worker can catch it and write a status='failed' run row.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(
        raw_file=raw_file,
        acquisition=acquisition,
        manifest_path=str(out / "fragpipe.fp-manifest"),
    )

    cmd = build_command(
        fragpipe_exe=fragpipe_exe,
        workflow=workflow,
        manifest=manifest,
        workdir=str(out),
        threads=threads,
        ram_gb=ram_gb,
        config_tools_folder=config_tools_folder,
        config_diann=config_diann,
    )

    log_file = Path(log_path) if log_path else (out / "fragpipe_run.log")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with open(log_file, "w", encoding="utf-8") as log:
        log.write(f"# FragPipe QC run started {started}\n")
        log.write(f"# raw: {raw_file}\n# acquisition: {acquisition}\n")
        log.write(f"# command:\n{' '.join(cmd)}\n\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_minutes * 60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log.write(f"\n# TIMEOUT after {timeout_minutes} min\n")
            raise FragPipeError(
                f"FragPipe timed out after {timeout_minutes} min for {raw_file}"
            ) from exc
        except FileNotFoundError as exc:
            # fragpipe_exe path wrong — most common first-run mistake.
            raise FragPipeError(
                f"FragPipe launcher not found at {fragpipe_exe!r}. "
                f"Check paths.fragpipe_exe in config.yaml."
            ) from exc

    if proc.returncode != 0:
        raise FragPipeError(
            f"FragPipe exited with code {proc.returncode} for {raw_file}. "
            f"See log: {log_file}"
        )
    return str(out)
