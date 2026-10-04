"""
Pop-up windows for everything that needs a person (attention.py items).

    host = PopupHost(root, log_dir=..., config_path=..., open_path=..., lab_settings=...)
    pops = Popups(root, host, is_app=True)     # the app; is_app=False in the watcher
    pops.start()                               # polls every few seconds
    pops.center()                              # the "needs attention" list

Who shows them: the app when it is open (it touches <log_dir>/app.alive), otherwise
the watcher's own window (`ionomos run` with the GUI enabled). One window at a
time; an item pops up once (again only if its content changes or it was snoozed).

Each window says what happened, the most likely causes, what to do, the details
(log tail / traceback), and has the buttons that fix it:

    analysis_input / analysis_failed   the experiment editor (conditions, samples left out,
                                       comparisons, control) + Run analysis; closes by itself
                                       when the re-analysis has nothing left to ask
    search_failed                      Retry search, FragPipe log, folder, Report a problem
    search_waiting                     Open Ionomos (setup checklist), folder
    intake_rejected                    Open the inbox, the note
    every item                         Ask about this (the local assistant, read-only: AskWindow), More help (the
                                       help page at this problem), Remind me in an hour, Dismiss

"Ask about this" is also in the list. The answer is made on a worker thread (assistant/askui.py holds the
logic) and comes back through host.post(), which _pump runs on the Tk thread; "not set up" shows Ionomos's own
text and the help, as a normal state (D72).

A change the assistant proposes (D75) opens in a ProposalDialog: what Ionomos says it would do, built from the
checked arguments (assistant/proposals.py), with Cancel (focused) and Confirm. Only Confirm applies it
(assistant/actions.apply: the same Retry and experiment.yaml save as the app's own buttons). One at a time.
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from ionomos import attention
from ionomos.assistant import askui

log = logging.getLogger("ionomos.popups")

KIND_TITLE = {"analysis_input": "Your analysis needs a decision", "analysis_failed": "The analysis had a problem",
              "search_failed": "A FragPipe search failed", "search_waiting": "A search is waiting",
              "intake_rejected": "A dropped folder couldn't be taken in"}
SEV_COLOUR = {"error": "#c62828", "input": "#b26a00", "warning": "#555"}


@dataclass
class PopupHost:
    root: tk.Misc
    log_dir: Callable[[], Path | None]
    config_path: Callable[[], Path | None]
    open_path: Callable[[str], None]
    lab_settings: Callable[[], dict] = lambda: {}
    popups_enabled: Callable[[], bool] = lambda: True
    report_problem: Callable[..., None] | None = None      # the app's dialog (note, job id); None = zip straight to the Desktop
    open_setup: Callable[[], None] | None = None           # the app's checklist tab; None = start the app
    on_change: Callable[[list], None] = lambda items: None  # badge updates
    database: Callable[[], Path | None] = lambda: None
    _q: queue.Queue = field(default_factory=queue.Queue)

    def post(self, fn) -> None:
        """Thread-safe: run fn on the Tk thread (pumped by Popups)."""
        self._q.put(fn)


class Popups:
    def __init__(self, root, host: PopupHost, is_app: bool, every_ms: int = 4000):
        self.root = root
        self.host = host
        self.is_app = is_app
        self.every_ms = every_ms
        self.window: ItemWindow | None = None
        self._center: tk.Toplevel | None = None
        self._stopped = False
        self._asks: dict[int, AskWindow] = {}  # open "Ask about this" windows, by token (the worker thread
        self._ask_seq = 0                      # knows only the token, never a widget)
        self._proposal: ProposalDialog | None = None  # the one proposal window (D75)

    def start(self, first_ms: int = 1500) -> None:
        self.root.after(first_ms, self._tick)
        self.root.after(100, self._pump)

    def stop(self) -> None:
        self._stopped = True

    def _pump(self) -> None:
        try:
            while True:
                fn = self.host._q.get_nowait()
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("pop-up callback failed")
        except queue.Empty:
            pass
        if not self._stopped:
            self.root.after(100, self._pump)

    def _tick(self) -> None:
        try:
            self.check()
        except Exception:  # noqa: BLE001 - never stop polling
            log.exception("attention check failed")
        if not self._stopped:
            self.root.after(self.every_ms, self._tick)

    def due(self) -> list[attention.Item]:
        log_dir = self.host.log_dir()
        its = attention.items(log_dir)
        return [i for i in its if i.due() and i.shown == 0 and (
            (i.kind in attention.POPUP_KINDS and i.severity in ("input", "error")) or i.data.get("popup") is True)]

    def check(self) -> None:
        """One pass: refresh the badge, open the next window if one is due and nothing is open."""
        log_dir = self.host.log_dir()
        if log_dir is None:
            return
        if self.is_app:
            attention.touch_app_alive(log_dir)
        its = attention.items(log_dir)
        self._auto_close(log_dir, its)
        its = attention.items(log_dir)
        self.host.on_change(its)
        if self.window is not None and self.window.alive():
            return
        if not self.is_app and attention.app_is_open(log_dir):
            return  # the app is open: it shows them
        if not self.host.popups_enabled():
            return
        due = self.due()
        if due:
            self.show(due[0])

    def _auto_close(self, log_dir: Path, its: list[attention.Item]) -> None:
        """A rejected folder that is no longer in the inbox, or an experiment folder that's gone, is done."""
        for it in its:
            if it.kind == "intake_rejected" and it.data.get("folder") and not Path(it.data["folder"]).exists():
                attention.resolve(log_dir, it.id)
            elif it.kind.startswith("analysis") and it.dest and not Path(it.dest).exists():
                attention.resolve(log_dir, it.id)

    def show(self, item: attention.Item) -> ItemWindow:
        log_dir = self.host.log_dir()
        if log_dir is not None:
            attention.mark_shown(log_dir, item.id)
        if self.window is not None and self.window.alive():
            self.window.close()
        self.window = ItemWindow(self, item)
        return self.window

    # -------------------------------------------------------------- the list --

    def center(self) -> tk.Toplevel:
        if self._center is not None and self._center.winfo_exists():
            self._center.lift()
            self._fill_center()
            return self._center
        win = tk.Toplevel(self.root)
        win.title("Ionomos — needs attention")
        win.geometry("820x360")
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        cols = (("what", 190), ("title", 420), ("since", 140))
        tree = ttk.Treeview(f, columns=[c for c, _ in cols], show="headings", selectmode="browse")
        for c, w in cols:
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w", stretch=c == "title")
        for sev, colour in SEV_COLOUR.items():
            tree.tag_configure(sev, foreground=colour)
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<Double-1>", lambda e: self._open_selected())
        b = ttk.Frame(f)
        b.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(b, text="Open", command=self._open_selected).pack(side="left", padx=3)
        ttk.Button(b, text="Dismiss", command=self._dismiss_selected).pack(side="left", padx=3)
        ttk.Button(b, text=askui.BUTTON, command=self._ask_selected).pack(side="left", padx=3)
        ttk.Button(b, text="Refresh", command=self._fill_center).pack(side="left", padx=3)
        ttk.Label(b, text="Items close by themselves once the problem is fixed (a retry, a clean re-analysis).",
                  foreground="#666").pack(side="left", padx=10)
        self._center, self._center_tree = win, tree
        self._fill_center()
        return win

    def _fill_center(self) -> None:
        tree = self._center_tree
        tree.delete(*tree.get_children())
        for it in attention.items(self.host.log_dir()):
            tree.insert("", "end", iid=it.id, tags=(it.severity,),
                        values=(KIND_TITLE.get(it.kind, it.kind), it.title, it.created.replace("T", " ")))

    def _open_selected(self) -> None:
        sel = self._center_tree.selection()
        if sel:
            it = attention.get(self.host.log_dir(), sel[0])
            if it is not None:
                self.show(it)

    def _ask_selected(self) -> AskWindow | None:
        sel = self._center_tree.selection()
        it = attention.get(self.host.log_dir(), sel[0]) if sel else None
        return self.ask_about(it) if it is not None else None

    # ------------------------------------------------------- ask about this --

    def ask_about(self, item: attention.Item) -> AskWindow:
        """Open an "Ask about this" window for an item; it asks at once with the item's default question."""
        self._ask_seq += 1
        w = self._asks[self._ask_seq] = AskWindow(self, item, self._ask_seq)
        return w

    def _ask_done(self, token: int, shown: askui.Shown) -> None:
        """On the Tk thread (through host.post): the answer for the window with this token, if still open."""
        w = self._asks.get(token)
        if w is not None and w.alive():
            w.show(shown)

    def propose(self, proposal, parent=None) -> ProposalDialog | None:
        """On the Tk thread: open the window for a change the assistant proposed. One at a time: while one is
        open, another is not shown (and is audited as not confirmed)."""
        from ionomos.assistant import actions

        if self._proposal is not None and self._proposal.alive():
            actions.decide(proposal, False, actions.Done(False, "not shown: another proposal was waiting"))
            return None
        self._proposal = ProposalDialog(self, proposal, parent)
        return self._proposal

    def load_config(self):
        from ionomos.config import ConfigError, load

        path = self.host.config_path()
        if path is None:
            raise ConfigError("Ionomos has no config.yaml yet (the app's Setup tab makes one)")
        return load(path, check_paths=False)

    def _dismiss_selected(self) -> None:
        sel = self._center_tree.selection()
        if sel:
            attention.dismiss(self.host.log_dir(), sel[0])
            self._fill_center()
            self.host.on_change(attention.items(self.host.log_dir()))


# ---------------------------------------------------------------- one window --


class ItemWindow:
    def __init__(self, pops: Popups, item: attention.Item):
        self.pops = pops
        self.host = pops.host
        self.item = item
        self.editor = None
        root = pops.root
        win = self.win = tk.Toplevel(root)
        win.title(f"Ionomos — {KIND_TITLE.get(item.kind, 'needs attention')}")
        try:
            win.attributes("-topmost", True)
            win.after(1500, lambda: win.winfo_exists() and win.attributes("-topmost", False))
        except tk.TclError:
            pass
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        colour = SEV_COLOUR.get(item.severity, "#555")
        tk.Frame(f, height=4, background=colour).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(f, text=KIND_TITLE.get(item.kind, ""), foreground=colour).grid(row=1, column=0, sticky="w")
        ttk.Label(f, text=item.title, font=("", 13, "bold"), wraplength=860, justify="left").grid(row=2, column=0, sticky="w")
        ttk.Label(f, text=item.message, wraplength=860, justify="left").grid(row=3, column=0, sticky="w", pady=(4, 6))
        row = 4
        text = ""
        if item.causes:
            text += "Most likely:\n" + "".join(f"  • {c}\n" for c in item.causes)
        if item.fixes:
            text += "What to do:\n" + "".join(f"  → {c}\n" for c in item.fixes)
        if text and not item.kind.startswith("analysis"):
            ttk.Label(f, text=text.rstrip(), wraplength=860, justify="left").grid(row=row, column=0, sticky="w")
            row += 1
        if item.kind.startswith("analysis") and item.dest:
            from ionomos.experiment_editor import EditorHost, ExperimentEditor

            host = EditorHost(post=self.host.post, open_path=self.host.open_path, config_path=self.host.config_path,
                              log_dir=self.host.log_dir, lab_settings=self.host.lab_settings)
            self.editor = ExperimentEditor(f, host, on_done=self._analysis_done, compact=True)
            self.editor.frame.grid(row=row, column=0, sticky="nsew")
            f.rowconfigure(row, weight=1)
            self.editor.show_issues((item.data or {}).get("issues") or [])
            self.editor.load(Path(item.dest), (item.data or {}).get("method"), item.job_id)
            row += 1
        if item.details and not item.kind.startswith("analysis"):
            det = ttk.LabelFrame(f, text="Details", padding=4)
            det.grid(row=row, column=0, sticky="nsew", pady=(6, 0))
            f.rowconfigure(row, weight=1)
            t = scrolledtext.ScrolledText(det, height=10, wrap="none", font=("Consolas", 9))
            t.pack(fill="both", expand=True)
            t.insert("1.0", item.details)
            t.see("end")
            t.configure(state="disabled")
            row += 1
        self.msg = ttk.Label(f, text="", foreground="#2e7d32", wraplength=860)
        self.msg.grid(row=row, column=0, sticky="w", pady=(6, 0))
        row += 1
        b = ttk.Frame(f)
        b.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        self._buttons(b)
        ttk.Button(b, text="Dismiss", command=self.dismiss).pack(side="right", padx=3)
        ttk.Button(b, text="Remind me in an hour", command=self.snooze).pack(side="right", padx=3)
        ttk.Button(b, text="More help", command=self.more_help).pack(side="right", padx=3)
        ttk.Button(b, text=askui.BUTTON, command=self.ask_about).pack(side="right", padx=3)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())
        win.update_idletasks()
        w = min(max(win.winfo_reqwidth(), 720), win.winfo_screenwidth() - 40)
        h = min(win.winfo_reqheight(), win.winfo_screenheight() - 80)
        win.geometry(f"{w}x{h}+{max((win.winfo_screenwidth() - w) // 2, 0)}+{max((win.winfo_screenheight() - h) // 4, 0)}")
        win.deiconify()
        win.lift()
        try:
            win.focus_force()
            win.bell()
        except tk.TclError:
            pass

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    # ------------------------------------------------------------- buttons --

    def _buttons(self, b) -> None:
        it = self.item
        if it.kind == "search_failed" and it.job_id is not None:
            ttk.Button(b, text="Retry search", command=self.retry).pack(side="left", padx=3)
        if it.kind in ("search_failed", "analysis_failed") and it.dest:
            ttk.Button(b, text="FragPipe log", command=self.open_log).pack(side="left", padx=3)
        if it.kind == "analysis_failed" and self.editor is not None:
            ttk.Button(b, text="Re-run analysis", command=lambda: self.editor.run(confirm=False)).pack(side="left", padx=3)
        if it.kind == "search_waiting":
            ttk.Button(b, text="Open the setup checklist", command=self.open_setup).pack(side="left", padx=3)
        if it.kind == "qc_trend" and it.data.get("page"):
            ttk.Button(b, text="Open QC trend", command=lambda: self.host.open_path(it.data["page"])).pack(
                side="left", padx=3)
        if it.kind == "intake_rejected":
            ttk.Button(b, text="Open the inbox", command=lambda: self.host.open_path(str(Path(it.dest).parent))).pack(
                side="left", padx=3)
            if it.data.get("note"):
                ttk.Button(b, text="The note", command=lambda: self.host.open_path(it.data["note"])).pack(side="left", padx=3)
        elif it.dest:
            ttk.Button(b, text="Open folder", command=lambda: self.host.open_path(it.dest)).pack(side="left", padx=3)
        if it.severity == "error":
            ttk.Button(b, text="Report a problem…", command=self.report).pack(side="left", padx=3)

    def retry(self) -> None:
        from ionomos.ledger import Ledger
        from ionomos.worker import request_retry

        db = self.host.database()
        try:
            led = Ledger(db)
            try:
                job = led.get(self.item.job_id)
                if job is None or job.status != "failed":
                    self.msg.configure(text=f"Job {self.item.job_id} is {job.status if job else 'gone'}; nothing to retry.",
                                       foreground="#b26a00")
                    return
                request_retry(led, job.id, self.host.log_dir())  # every Retry is this one (D75)
            finally:
                led.close()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Retry", f"Could not re-queue the job: {exc}", parent=self.win)
            return
        attention.resolve(self.host.log_dir(), self.item.id)
        self.close()

    def open_log(self) -> None:
        from ionomos import names

        p = self.item.data.get("console_log") or str(names.console_log(Path(self.item.dest)))
        if Path(p).is_file():
            self.host.open_path(p)
        else:
            self.msg.configure(text="There is no FragPipe log for this experiment (FragPipe never started?).",
                               foreground="#b26a00")

    def open_setup(self) -> None:
        if self.host.open_setup is not None:
            self.host.open_setup()
        else:
            from ionomos import service

            try:
                service.launch_app()
            except Exception as exc:  # noqa: BLE001
                self.msg.configure(text=f"Open the Ionomos app from the Start menu ({exc})", foreground="#b26a00")

    def report(self) -> None:
        note = f"{self.item.title}\n{self.item.message}\n\n{self.item.details[-3000:]}"
        if self.host.report_problem is not None:
            self.host.report_problem(note, self.item.job_id)
            return
        from ionomos import bundle, service

        try:  # no app open (the watcher's own window): the zip for this job, with the default choices
            target = [str(self.item.job_id)] if self.item.job_id else [self.item.dest] if self.item.dest else []
            res = bundle.create(self.host.config_path(), target, bundle.Options(level="diagnose", note=note))
            service.reveal(res.path)
            self.msg.configure(text=res.message)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Report a problem", str(exc), parent=self.win)

    def more_help(self) -> None:
        """The help page, opened at the entry that explains this item (help.topic_for_item)."""
        from ionomos import help as helpdoc

        try:
            helpdoc.open_help(helpdoc.topic_for_item(self.item), log_dir=self.host.log_dir(), opener=self.host.open_path)
        except OSError as exc:
            self.msg.configure(text=f"Could not open the help: {exc}", foreground="#c62828")

    def ask_about(self) -> AskWindow:
        return self.pops.ask_about(self.item)

    def snooze(self) -> None:
        attention.snooze(self.host.log_dir(), self.item.id, 60)
        self.close()

    def dismiss(self) -> None:
        attention.dismiss(self.host.log_dir(), self.item.id)
        self.close()

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.host.on_change(attention.items(self.host.log_dir()))

    def _analysis_done(self, out) -> None:
        from ionomos.downstream import doctor

        if not doctor.popups(out.issues):
            self.msg.configure(text="All set — the report is open. This window closes in a moment.")
            self.win.after(2500, self.close)
        else:
            self.msg.configure(text="Some things still need a look (listed above).", foreground="#b26a00")


# ------------------------------------------------------------ ask about this --


class AskWindow:
    """The local assistant's answer about one item: a question (pre-filled, editable), Ask, the answer as plain
    text with its sources, More help, Close. Asking runs on a worker thread; nothing here changes anything."""

    def __init__(self, pops: Popups, item: attention.Item, token: int):
        self.pops, self.host, self.item, self.token = pops, pops.host, item, token
        self.shown: askui.Shown | None = None
        self.proposal: ProposalDialog | None = None
        self.pending = False
        win = self.win = tk.Toplevel(pops.root)
        win.title(askui.TITLE)
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)
        ttk.Label(f, text=f"{KIND_TITLE.get(item.kind, 'Needs attention')}: {item.title}", wraplength=720,
                  justify="left", font=("", 11, "bold")).grid(row=0, column=0, sticky="w")
        q = ttk.Frame(f)
        q.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        q.columnconfigure(0, weight=1)
        self.question = ttk.Entry(q)  # no Tk variable: nothing here may be freed on the worker thread
        self.question.insert(0, askui.default_question(item))
        self.question.grid(row=0, column=0, sticky="ew")
        self.question.bind("<Return>", lambda e: self.ask())
        self.ask_btn = ttk.Button(q, text="Ask", command=self.ask)
        self.ask_btn.grid(row=0, column=1, padx=(6, 0))
        self.status = ttk.Label(f, text="", foreground="#555", wraplength=720, justify="left")
        self.status.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.answer = scrolledtext.ScrolledText(f, height=20, width=96, wrap="word")
        self.answer.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        self.answer.configure(state="disabled")
        b = ttk.Frame(f)
        b.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(b, text="Close", command=self.close).pack(side="right", padx=3)
        ttk.Button(b, text="More help", command=self.more_help).pack(side="right", padx=3)
        ttk.Label(b, text=askui.NOTE, foreground="#666", wraplength=520, justify="left").pack(side="left")
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())
        win.lift()  # above the pop-up it was opened from, which may still be topmost
        try:
            win.focus_force()
        except tk.TclError:
            pass
        self.ask()

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def ask(self) -> None:
        if self.pending:
            return
        self.pending = True
        question = self.question.get().strip() or askui.default_question(self.item)
        self.ask_btn.configure(state="disabled")
        self.status.configure(text=askui.ASKING)
        # the worker thread holds only these: the long-lived Popups and host, the token, plain values
        pops, token, item_id, load = self.pops, self.token, self.item.id, self.pops.load_config
        askui.start(lambda: askui.answer_for(load, question, item_id),
                    lambda shown: pops.host.post(lambda: pops._ask_done(token, shown)))

    def show(self, shown: askui.Shown) -> None:
        self.shown, self.pending = shown, False
        self.answer.configure(state="normal")
        self.answer.delete("1.0", "end")
        self.answer.insert("1.0", shown.text)  # plain text: nothing in it is a link, an image or markup
        self.answer.configure(state="disabled")
        self.ask_btn.configure(state="normal")
        self.status.configure(text="")
        if shown.proposal is not None:  # the change opens in its own window; nothing typed here confirms it
            self.proposal = self.pops.propose(shown.proposal, self.win)
            if self.proposal is None:
                self.status.configure(text=askui.PROPOSAL_BUSY, foreground="#b26a00")

    def more_help(self) -> None:
        from ionomos import help as helpdoc

        topic = self.shown.help_topic if self.shown is not None else "faq.assistant"
        try:
            helpdoc.open_help(topic, log_dir=self.host.log_dir(), opener=self.host.open_path)
        except OSError as exc:
            self.status.configure(text=f"Could not open the help: {exc}", foreground="#c62828")

    def close(self) -> None:
        self.pops._asks.pop(self.token, None)
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ------------------------------------------------------ a proposed change --


class ProposalDialog:
    """A change the assistant proposed (D75), as Ionomos describes it from the checked arguments: what Confirm does
    and the difference in experiment.yaml. Cancel has the focus; Cancel, Escape and closing the window change
    nothing. Confirm applies it through assistant/actions.apply (rebuilt and checked again first), once."""

    def __init__(self, pops: Popups, proposal, parent=None):
        from ionomos.assistant import proposals

        self.pops, self.proposal = pops, proposal
        self.decided = False
        self.done = None
        win = self.win = tk.Toplevel(parent if parent is not None else pops.root)
        win.title(askui.PROPOSAL_TITLE)
        if parent is not None:
            try:
                win.transient(parent)
            except tk.TclError:
                pass
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text=askui.PROPOSAL_HEADING, wraplength=720, justify="left", font=("", 11, "bold")).grid(
            row=0, column=0, sticky="w")
        self.body = scrolledtext.ScrolledText(f, height=18, width=100, wrap="none", font=("Consolas", 9))
        self.body.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.body.insert("1.0", proposals.text(proposal))  # plain text, every line written by Ionomos
        self.body.configure(state="disabled")
        self.status = ttk.Label(f, text="", wraplength=720, justify="left")
        self.status.grid(row=2, column=0, sticky="w", pady=(6, 0))
        b = ttk.Frame(f)
        b.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.cancel_btn = ttk.Button(b, text="Cancel", command=self.cancel)
        self.cancel_btn.pack(side="right", padx=3)
        self.confirm_btn = ttk.Button(b, text="Confirm", command=self.confirm)
        self.confirm_btn.pack(side="right", padx=3)
        win.protocol("WM_DELETE_WINDOW", self.cancel)
        win.bind("<Escape>", self.on_escape)
        win.lift()
        self.cancel_btn.focus_set()  # Return or space presses Cancel, never Confirm
        try:
            win.grab_set()  # modal: the question box behind it cannot be used meanwhile
        except tk.TclError:
            pass

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def confirm(self) -> None:
        """The only place a proposal is applied."""
        from ionomos.assistant import actions

        if self.decided:
            return
        self.decided = True
        self.confirm_btn.configure(state="disabled")
        try:
            cfg = self.pops.load_config()
        except Exception as exc:  # noqa: BLE001
            self.done = actions.Done(False, f"Ionomos could not read its settings ({exc}); nothing was changed.")
            actions.decide(self.proposal, True, self.done)
        else:
            self.done = actions.apply(cfg, self.proposal, confirmed=True)
        self.status.configure(text=self.done.message, foreground="#2e7d32" if self.done.ok else "#c62828")
        self.cancel_btn.configure(text="Close", command=self.close)
        try:
            self.pops.host.on_change(attention.items(self.pops.host.log_dir()))
        except Exception:  # noqa: BLE001 - the badge is a nicety
            log.exception("could not refresh the attention badge")

    def on_escape(self, _event=None) -> None:
        self.cancel()

    def cancel(self) -> None:
        from ionomos.assistant import actions

        if not self.decided:
            self.decided = True
            actions.decide(self.proposal, False)
        self.close()

    def close(self) -> None:
        if not self.decided:  # closed some other way: as Cancel
            self.cancel()
            return
        try:
            self.win.grab_release()
            self.win.destroy()
        except tk.TclError:
            pass
