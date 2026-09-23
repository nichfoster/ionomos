"""
Headless FragPipe runner.

Contract (Phase 2):
    run(fragpipe_exe, workflow, manifest, workdir, threads, ram_gb,
        timeout_minutes, config_tools_folder=None, config_diann=None,
        log_path=None) -> None
        raises FragPipeError on non-zero exit, timeout, or missing launcher.

Port of reference/prior-work/proteomics-qc-pkg/fragpipe_runner.py — take
build_command() and run_fragpipe() nearly verbatim; drop build_manifest()
(lives in manifest.py now).

CONFIRM-ON-INSTALL: launcher path for FragPipe 24.0
(C:/FragPipe/FragPipe-24.0/fragpipe/bin/fragpipe.exe vs fragpipe.bat).
"""
from __future__ import annotations


class FragPipeError(RuntimeError):
    pass


def run(**kwargs) -> None:
    raise NotImplementedError("Phase 2")
