"""
Applying a proposal of the assistant once a person pressed Confirm (ROADMAP Phase 6.2, D75). The only part of
the assistant that changes anything, and only the proposal window's Confirm button calls it
(popups.ProposalDialog.confirm; tests/test_assistant_proposals.py reads the source of the whole package to check).

    done = apply(cfg, proposal, confirmed=True)      # Done(ok, message, path, backup)
    decide(proposal, confirmed=False)                # Cancel: only the audit record

apply() does not trust the proposal it is handed. It builds it again from its tool and arguments, against the
ledger and experiment.yaml as they are now, with the same checks as when it was proposed, and goes on only if the
result is the same proposal (same job, same file, same change). Then it does what the app's own buttons do:

    a retry         worker.request_retry       (the Jobs tab's and the pop-up's Retry, `ionomos retry`)
    an analysis     manifest.save_analysis     (the experiment editor's Save: checked, old file in experiment-backups/)

Every decision, Confirm or Cancel, applied or not, is one record in the assistant's audit log, linked to the
question's record by the proposal's id.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ionomos.assistant import audit, proposals, tools

log = logging.getLogger("ionomos.assistant")


@dataclass
class Done:
    ok: bool
    message: str
    path: str = ""          # experiment.yaml that was written
    backup: str = ""        # the copy of the version it replaced


def apply(cfg, proposal: proposals.Proposal, *, confirmed: bool, audit_path=None) -> Done:
    """Make the change a person confirmed. Never raises; the Done says what happened, and is audited."""
    if confirmed is not True:
        raise ValueError("a proposal is applied only after its Confirm button was pressed")
    done = _apply(cfg, proposal)
    decide(proposal, True, done, audit_path)
    return done


def _apply(cfg, proposal: proposals.Proposal) -> Done:
    from ionomos import ledger as ledger_mod
    from ionomos.downstream.analysis import AnalysisError
    from ionomos.manifest import OverridesError, save_analysis
    from ionomos.worker import request_retry

    try:
        if ledger_mod.integrity(cfg.database) != "ok":
            return Done(False, "The job list cannot be read; nothing was changed.")
        led = ledger_mod.Ledger(cfg.database)
    except Exception as exc:  # noqa: BLE001
        return Done(False, f"The job list cannot be opened ({tools.clean(exc, 200)}); nothing was changed.")
    try:
        ctx = tools.Context(cfg, led)
        try:
            again = proposals.build(ctx, proposal.tool, proposal.arguments)
        except proposals.Refused as exc:
            return Done(False, f"Not done: {tools.clean(exc, 300)}. Nothing was changed.")
        if again.id != proposal.id or again.analysis != proposal.analysis or again.job_id != proposal.job_id:
            return Done(False, "Not done: the job or its experiment.yaml changed after the assistant proposed this, "
                               "so it is not the change you saw. Nothing was changed; ask again.")
        if again.action == "retry":
            ok, msg = request_retry(led, again.job_id, cfg.log_dir)
            return Done(ok, msg[0].upper() + msg[1:] + ("." if ok else "; nothing was changed."))
        job = led.get(again.job_id)
        try:
            path, backup = save_analysis(Path(job.dest_dir), again.analysis, proposals._lab(ctx))
        except (AnalysisError, OverridesError, ValueError) as exc:
            return Done(False, f"Not done: {tools.clean(exc, 300)}. Nothing was changed.")
        except OSError as exc:
            return Done(False, f"experiment.yaml could not be written ({tools.clean(exc, 200)}).")
        if path is None:
            return Done(True, "experiment.yaml already said this; nothing needed changing.")
        where = f" The version it replaced is in {backup.parent.name}/{backup.name}." if backup else ""
        return Done(True, f"Saved in job {job.id}'s experiment.yaml.{where} Re-run the analysis to use it "
                          f"(Jobs tab → Re-run analysis, or ionomos analyze {job.id}).", str(path),
                    str(backup or ""))
    finally:
        led.close()


def decide(proposal: proposals.Proposal, confirmed: bool, done: Done | None = None, audit_path=None) -> None:
    """The audit record of a decision: Confirm (with what happened) or Cancel / closing the window."""
    audit.append({"event": "proposal_decision", "proposal": proposal.id, "tool": proposal.tool,
                  "args_sha256": proposal.args_sha256, "job": proposal.job_id, "title": proposal.title,
                  "confirmed": bool(confirmed), "applied": bool(done and done.ok and confirmed),
                  "message": done.message if done else ""}, audit_path)
    log.info("assistant proposal %s (%s): %s%s", proposal.id, proposal.title,
             "confirmed" if confirmed else "cancelled", f" - {done.message}" if done else "")
