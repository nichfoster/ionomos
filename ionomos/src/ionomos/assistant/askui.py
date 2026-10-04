"""
"Ask about this" (ROADMAP Phase 6.1, D72): the logic behind the button in the pop-ups and the attention list.
Everything here is plain Python and tested without windows; popups.py only draws.

    q = default_question(item)                       # a fixed question per kind: no text from the item
    shown = answer_for(load_cfg, q, item.id)         # never raises; Shown(heading, body, sources, ...)
    start(lambda: answer_for(...), deliver)          # the same, on a worker thread; deliver(shown) from there

The window calls start() and gets the answer back through its host's post() queue, which the Tk thread pumps
with root.after: Tk is not thread-safe, so the worker thread never touches a widget, and it holds no reference
to one either (a Tk object freed on another thread can abort the process; see tkutil.py).

What is shown is plain text: the answer as the assistant checked it, the Sources lines Ionomos wrote from its
tools, and a heading that says what kind of answer it is. "Not set up" is a normal state: the body is then
Ionomos's own text (the item's causes and fixes and its help entry), and More help opens the help on setting
the assistant up. The button never changes anything: nothing the model says is acted on. A proposal the
assistant made (D75) is carried in Shown.proposal, and the window opens it in popups.ProposalDialog, where only
the Confirm button applies it (assistant/actions.py).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("ionomos.assistant")

BUTTON = "Ask about this"
TITLE = "Ionomos — Ask about this"
ASKING = "Asking the assistant … a model on this computer can take a minute or two."
NOTE = ("The assistant changes nothing itself. It can propose one change, which opens in a window of its own "
        "with Confirm and Cancel; typing yes here does nothing.")
PROPOSAL_TITLE = "Ionomos — the assistant proposes a change"
PROPOSAL_HEADING = ("The assistant proposes this change. Nothing has changed yet: Confirm makes it, the way the "
                    "app's own buttons do; Cancel drops it.")
PROPOSAL_BUSY = ("Another proposed change is still waiting in its window, so this one was not opened. Decide "
                 "that one, then ask again.")
PROPOSED = "Proposed (nothing has changed yet; confirm or cancel it in the window that opens): {title}"

QUESTIONS = {
    "search_failed": "Why did this search fail, and what should I do?",
    "search_waiting": "Why is this search waiting, and what does it need?",
    "intake_rejected": "Why wasn't this folder taken in, and how do I fix it?",
    "analysis_input": "What does the analysis need me to decide here?",
    "analysis_failed": "What went wrong with the analysis, and what should I do?",
    "qc_trend": "What does this QC warning mean?",
}
DEFAULT_QUESTION = "What does this mean, and what should I do?"

HEADINGS = {
    "grounded": "The assistant's answer. Each paragraph ends with its sources; Ionomos checked that each one exists "
                "and lists what it says under Sources.",
    "fallback": "The assistant had no answer it could back with a source, so none is shown. This is Ionomos's own "
                "explanation.",
    "not_set_up": "The assistant is not set up on this computer. That is normal: everything else works without it. "
                  "This is Ionomos's own explanation.",
    "refused": "The assistant's address is not on this computer, so nothing was sent. This is Ionomos's own "
               "explanation.",
    "unavailable": "The assistant's model is not answering (or is paused while a search runs). This is Ionomos's own "
                   "explanation.",
    "error": "Ionomos could not ask the assistant.",
}
HELP = {"grounded": "faq.assistant", "fallback": "faq.assistant", "not_set_up": "faq.assistant-setup",
        "refused": "faq.assistant-setup", "unavailable": "faq.assistant-setup", "error": "faq.assistant-setup"}


@dataclass
class Shown:
    """What the answer window shows, all plain text."""
    outcome: str                    # grounded | fallback | not_set_up | refused | unavailable | error
    heading: str
    body: str
    sources: list[str] = field(default_factory=list)
    footer: str = ""
    help_topic: str = "faq.assistant"
    proposal: object | None = None  # proposals.Proposal: the window opens popups.ProposalDialog for it (D75)

    @property
    def text(self) -> str:
        """Everything in the order the window shows it."""
        parts = [self.heading, "", self.body]
        if self.proposal is not None:
            parts += ["", PROPOSED.format(title=self.proposal.title)]
        if self.sources:
            parts += ["", "Sources:", *(f"  {s}" for s in self.sources)]
        if self.footer:
            parts += ["", self.footer]
        return "\n".join(parts).strip() + "\n"


def default_question(item) -> str:
    """The question the window starts with. Fixed per kind: nothing from the item's title, message, folder or
    sample names (untrusted) goes into the question; the item is named to the assistant by ask(item_id=...)."""
    return QUESTIONS.get(getattr(item, "kind", ""), DEFAULT_QUESTION)


def render(answer) -> Shown:
    """An assistant.Answer as the window shows it."""
    from ionomos.assistant import tools

    outcome = answer.outcome if answer.outcome in HEADINGS else "fallback"
    who = f"model {tools.clean(answer.model, 80)}, " if answer.grounded and answer.model else ""
    mode = "while a search ran, " if getattr(answer, "mode", "idle") == "searching" else ""
    footer = f"({who}{mode}{answer.seconds:.0f} s) {NOTE}"
    return Shown(outcome, HEADINGS[outcome], answer.text, list(answer.sources) if answer.grounded else [],
                 footer, HELP[outcome], getattr(answer, "proposal", None) if answer.grounded else None)


def failed(exc: BaseException) -> Shown:
    from ionomos.assistant import tools

    return Shown("error", HEADINGS["error"], tools.clean(f"{type(exc).__name__}: {exc}", 600),
                 footer="The details are in the Ionomos log. " + NOTE, help_topic=HELP["error"])


def answer_for(load_cfg: Callable[[], object], question: str, item_id: str | None = None, *,
               ask: Callable | None = None, **kw) -> Shown:
    """Load the config, ask, render. Never raises: a config that won't load or a bug is shown as such."""
    try:
        if ask is None:
            from ionomos import assistant

            ask = assistant.ask
        cfg = load_cfg()
        return render(ask(cfg, question, item_id=item_id, **kw))
    except Exception as exc:  # noqa: BLE001 - shown in the window, never a crash of the GUI
        log.exception("Ask about this failed")
        return failed(exc)


def start(job: Callable[[], Shown], deliver: Callable[[Shown], None]) -> threading.Thread:
    """Run job() on a daemon thread and hand its result to deliver() (from that thread: deliver must only post
    to the Tk thread's queue). A deliver that raises is logged, not re-raised."""
    def go():
        try:
            shown = job()
        except Exception as exc:  # noqa: BLE001
            log.exception("Ask about this failed")
            shown = failed(exc)
        try:
            deliver(shown)
        except Exception:  # noqa: BLE001
            log.exception("could not deliver the assistant's answer")

    t = threading.Thread(target=go, name="ask-about-this", daemon=True)
    t.start()
    return t
