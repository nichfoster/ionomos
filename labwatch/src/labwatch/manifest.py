"""
FragPipe manifest + annotation writers, and experiment.yaml loader.

Contract (Phase 2/3):
    load_experiment_yaml(path) -> ExperimentOverrides
        Schema in docs/NAMING_CONVENTION.md. Unknown keys are an error.

    build_fp_manifest(raws: RawSet, data_type: str, raw_dir: Path,
                      overrides: ExperimentOverrides | None) -> str
        One line per raw:  <abs path with forward slashes>\t<experiment>\t<bioreplicate>\t<DDA|DIA>
        experiment   = RawName.sample   (unless overridden per file)
        bioreplicate = RawName.rep      (unless overridden; TMT default 1 per SOP)
        Fractions share (experiment, bioreplicate) — FragPipe merges them.

    write_tmt_annotations(overrides, dest_dir) -> list[Path]
        One annotation.txt per plex:  <channel>\t<sample_name>
        Where FragPipe 24.0 headless expects this file is an OPEN QUESTION
        (docs/WORKFLOWS.md).

Reuse: reference/prior-work/proteomics-qc-pkg/fragpipe_runner.build_manifest
"""
from __future__ import annotations


def build_fp_manifest(raws, data_type, raw_dir, overrides=None) -> str:
    raise NotImplementedError("Phase 2")
