"""
The "Report a problem" window: a note, which jobs, two or three boxes, a plain list of what will go
in the zip and its size, then Save. All of the deciding is in bundle.py (Choice, collect, create,
summary_lines); this file only has the widgets, so the logic is tested without a display.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from ionomos import bundle, tkutil

TITLE = "Report a problem"
log = logging.getLogger("ionomos.app")


class BundleDialog:
    """One window -> one zip (and its key file) on the Desktop, shown in Explorer."""

    def __init__(self, root: tk.Misc, config_path: Path, post: Callable[[Callable[[], None]], None], *,
                 note: str = "", job_id: int | None = None, log_dir: Callable[[], Path | None] | None = None,
                 on_status: Callable[[str], None] | None = None, validate: bool = False):
        self.root, self.config_path, self.post = root, Path(config_path), post
        self.log_dir, self.on_status = log_dir, on_status or (lambda msg: None)
        self.jobs = bundle.recent_jobs(self.config_path)
        self._generation = 0
        self.result: bundle.Result | None = None
        self.win = win = tk.Toplevel(root)
        win.title(TITLE)
        win.transient(root)
        f = ttk.Frame(win, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="What happened, and what did you expect?", font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(f, text="A sentence is enough — e.g. \"dropped a folder at 3pm, nothing moved\".",
                  foreground="#666").pack(anchor="w", pady=(0, 6))
        self.text = tk.Text(f, width=76, height=4, wrap="word")
        self.text.pack(fill="x")
        if note:
            self.text.insert("1.0", note)

        ttk.Label(f, text="Jobs to include (none selected = the running, waiting and last failed ones):").pack(
            anchor="w", pady=(10, 2))
        self.job_list = tk.Listbox(f, height=min(6, max(2, len(self.jobs))), selectmode="extended",
                                   exportselection=False)
        for j in self.jobs:
            self.job_list.insert("end", bundle.job_label(j))
        for i in bundle.preselect(self.jobs, job_id):
            self.job_list.selection_set(i)
        self.job_list.pack(fill="x")
        self.job_list.bind("<<ListboxSelect>>", lambda e: self.refresh())

        self.validate = tkutil.BooleanVar(master=win, value=validate)
        self.anonymise = tkutil.BooleanVar(master=win, value=True)
        self.keep_conditions = tkutil.BooleanVar(master=win, value=False)
        self.debug = tkutil.BooleanVar(master=win, value=False)
        for var, label in (
                (self.validate, "Include the search's result tables and Ionomos' results, so the analysis can be "
                                "run again elsewhere (larger)"),
                (self.anonymise, "Replace names with pseudonyms (users, the PC, experiments, raw files, samples); "
                                 "the key file stays in the lab"),
                (self.keep_conditions, "Keep every condition word readable (default: only control words such as DMSO)")):
            ttk.Checkbutton(f, text=label, variable=var, command=self.refresh).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(f, text="Also turn on detailed logging for the next 24 hours (for problems that come and go; "
                                "send another report after it happens again)", variable=self.debug).pack(
            anchor="w", pady=(4, 0))

        ttk.Label(f, text="What will be in the zip:").pack(anchor="w", pady=(10, 2))
        self.preview = tk.Text(f, width=76, height=12, wrap="none", state="disabled")
        self.preview.pack(fill="both", expand=True)
        ttk.Label(f, text="Nothing is sent: the zip is saved on the Desktop for you to copy. Never raw data, FASTA "
                          "files or spectral libraries.", foreground="#666", wraplength=560).pack(anchor="w", pady=(6, 8))
        bb = ttk.Frame(f)
        bb.pack(fill="x")
        ttk.Button(bb, text="Save the zip", command=self.save).pack(side="right")
        ttk.Button(bb, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        self.text.focus_set()
        win.bind("<Escape>", lambda e: win.destroy())
        self.refresh()

    # -- what the boxes say ----------------------------------------------------

    def choice(self) -> bundle.Choice:
        picked = tuple(self.jobs[i]["id"] for i in self.job_list.curselection())
        return bundle.Choice(validate=self.validate.get(), anonymise=self.anonymise.get(),
                             keep_conditions=self.keep_conditions.get(), jobs=picked,
                             note=self.text.get("1.0", "end").strip())

    def _show(self, generation: int, lines: list[str]) -> None:
        if generation != self._generation:
            return  # an older answer: the boxes changed again meanwhile
        try:
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", "\n".join(lines))
            self.preview.configure(state="disabled")
        except tk.TclError:
            pass  # the window was closed

    def refresh(self) -> None:
        """Recount what would go in (file sizes only), off the Tk thread."""
        self._generation += 1
        generation, choice, cfg = self._generation, self.choice(), self.config_path

        def go():
            try:
                lines = bundle.summary_lines(bundle.collect(cfg, choice.targets(), choice.options()))
            except Exception as exc:  # noqa: BLE001 - the list is a preview; Save reports real errors
                lines = [f"could not list the files: {exc}"]
            self.post(lambda: self._show(generation, lines))

        threading.Thread(target=go, daemon=True).start()

    # -- save --------------------------------------------------------------------

    def save(self) -> None:
        from ionomos import health, service

        choice = self.choice()
        if self.debug.get() and self.log_dir is not None:
            try:
                until = health.set_debug(self.log_dir(), 24)
                choice.note += f"\n\n[detailed logging turned on until {until:%Y-%m-%d %H:%M}]"
            except (OSError, TypeError):
                pass
        cfg, root = self.config_path, self.root
        self._generation += 1
        self.win.destroy()
        self.on_status("building the zip…")
        log.info("problem report requested (%s): %s", choice.options().level, choice.note[:200])

        def go():
            try:
                res, msg = bundle.create(cfg, choice.targets(), choice.options()), ""
            except Exception as exc:  # noqa: BLE001
                res, msg = None, f"Could not build the zip: {exc}"

            def done():
                if res is None:
                    self.on_status("no zip was saved")
                    messagebox.showerror(TITLE, msg)
                    return
                self.result = res
                try:
                    root.clipboard_clear()
                    root.clipboard_append(str(res.path))
                except tk.TclError:
                    pass
                service.reveal(res.path)
                self.on_status(f"saved: {res.path.name}")
                messagebox.showinfo(TITLE, res.message + "\n\nIt is selected in the window that just opened. "
                                                         "(Its location is also on the clipboard.)")

            self.post(done)

        threading.Thread(target=go, daemon=True).start()
