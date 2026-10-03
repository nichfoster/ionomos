"""
The app's Notifications tab (8, D67): config.yaml notify: (D58) in a window, and Send test.

Thin Tk only: fields <-> config is forms.notify_fields / notify_block (checked by notify.settings_from), and
Send test is notify.run_test, the code `ionomos notify-test` runs, on a worker thread with its lines coming
back through App.post. Webhook addresses and the SMTP password are secrets: their fields are masked (•)
unless "Show" is ticked for a moment, nothing here logs a field's value, and the test's lines are the
scrubbed ones notify.py writes.
"""
from __future__ import annotations

import logging
import threading
from tkinter import messagebox, ttk

from ionomos import forms

log = logging.getLogger("ionomos.app")

PAD = {"padx": 6, "pady": 3}
MASK = "•"


class NotifyTab:
    def __init__(self, app):
        self.app = app
        self.busy = False
        self.secret_entries: list[ttk.Entry] = []
        f = ttk.Frame(app.nb, padding=10)
        app.nb.add(f, text="  8  Notifications  ")
        self.frame = f
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(5, weight=1)
        ttk.Label(f, text="A message when a search is done, failed or waiting. Off unless you turn it on: nothing "
                          "leaves this PC otherwise. A message holds the status, job number and time, and with "
                          "names ticked the experiment, user, method, reason, hit counts, report path and PC name; "
                          "never a file or a measured value. Press Save (bottom right), then restart the watcher.",
                  wraplength=900, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        g = ttk.LabelFrame(f, text="When", padding=6)
        g.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Checkbutton(g, text="Send notifications", variable=self.bv("notify.enabled")).grid(
            row=0, column=0, sticky="w", **PAD)
        ev = ttk.Frame(g)
        ev.grid(row=0, column=1, sticky="w", **PAD)
        ttk.Label(ev, text="when a search is").pack(side="left")
        for e, label in (("done", "done"), ("failed", "failed"), ("held", "waiting (no FASTA, disk space …)")):
            ttk.Checkbutton(ev, text=label, variable=self.bv(f"notify.on.{e}", True)).pack(side="left", padx=4)
        ttk.Checkbutton(g, text="Include names (off: only the job number and status are sent)",
                        variable=self.bv("notify.include_names", True)).grid(row=1, column=0, columnspan=2,
                                                                             sticky="w", **PAD)
        t = ttk.Frame(g)
        t.grid(row=2, column=0, columnspan=2, sticky="w", **PAD)
        ttk.Label(t, text="Give up on a channel after (s)").pack(side="left")
        ttk.Entry(t, textvariable=self.v("notify.timeout_seconds"), width=5).pack(side="left", padx=4)
        ttk.Label(t, text="1 to 60; a search never waits for a message", foreground="#666").pack(side="left", padx=6)

        h = ttk.LabelFrame(f, text="Chat and webhooks (the address is a secret: it is masked here and left out of "
                                   "logs and diagnostics)", padding=6)
        h.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=4)
        h.columnconfigure(1, weight=1)
        r = 0
        for name, hint in (("teams", "in Teams: Workflows → \"Post to a channel when a webhook request is received\""),
                           ("slack", "a Slack incoming-webhook address"),
                           ("webhook", "any service that takes a JSON POST")):
            ttk.Label(h, text=forms.HOOK_LABELS[name], font=("", 10, "bold")).grid(row=r, column=0, sticky="w", **PAD)
            ttk.Label(h, text=hint, foreground="#666").grid(row=r, column=1, sticky="w", **PAD)
            ttk.Label(h, text="Address").grid(row=r + 1, column=0, sticky="e", **PAD)
            self._secret(h, f"notify.{name}.url", 46).grid(row=r + 1, column=1, sticky="ew", **PAD)
            ttk.Label(h, text="or variable").grid(row=r + 2, column=0, sticky="e", **PAD)
            ttk.Entry(h, textvariable=self.v(f"notify.{name}.url_env"), width=24).grid(row=r + 2, column=1,
                                                                                    sticky="w", **PAD)
            r += 3
        ttk.Label(h, text="\"or variable\": the NAME of an environment variable that holds the address (it wins over "
                          "the address), to keep it out of config.yaml", foreground="#666", wraplength=420,
                  justify="left").grid(row=r, column=0, columnspan=2, sticky="w", padx=6)

        m = ttk.LabelFrame(f, text="Email (SMTP; no server = no email)", padding=6)
        m.grid(row=2, column=1, sticky="nsew", pady=4)
        m.columnconfigure(1, weight=1)
        rows = (("host", "Server", "e.g. smtp.office365.com"), ("port", "Port", "587 with starttls, 465 with ssl"),
                ("security", "Security", ""), ("username", "User name", "if the server needs a login"),
                ("password", "Password", "a secret: masked, left out of logs"),
                ("password_env", "or variable", "the NAME of an environment variable holding it"),
                ("from", "From", "the address the message is sent from"),
                ("to", "To", "addresses, separated by commas"))
        for k, (key, label, hint) in enumerate(rows):
            ttk.Label(m, text=label).grid(row=k, column=0, sticky="e", **PAD)
            if key == "security":
                w = ttk.Combobox(m, textvariable=self.v("notify.email.security"), state="readonly", width=10,
                                 values=["starttls", "ssl", "none"])
            elif key == "password":
                w = self._secret(m, "notify.email.password", 24)
            else:
                w = ttk.Entry(m, textvariable=self.v(f"notify.email.{key}"), width=6 if key == "port" else 30)
            w.grid(row=k, column=1, sticky="w" if key in ("port", "security") else "ew", **PAD)
            if hint:
                ttk.Label(m, text=hint, foreground="#666", wraplength=200, justify="left").grid(
                    row=k, column=2, sticky="w", **PAD)

        b = ttk.Frame(f)
        b.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.test_btn = ttk.Button(b, text="Send test", command=self.send_test)
        self.test_btn.pack(side="left", padx=2)
        ttk.Checkbutton(b, text="Show addresses and password", variable=self.bv("notify.show"),
                        command=self._show_secrets).pack(side="left", padx=12)
        ttk.Button(b, text="Help", command=lambda: self.app.open_help("faq.notify")).pack(side="left", padx=12)
        ttk.Label(b, text="Send test uses what is in this window, saved or not (same as `ionomos notify-test`)",
                  foreground="#666").pack(side="left", padx=6)
        self.result = ttk.Label(f, text="", font=("", 10, "bold"))
        self.result.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        from ionomos.app import OutputPane

        self.out = OutputPane(f, height=7)
        self.out.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=4)

    def v(self, key, default=""):
        return self.app.v(key, default)

    def bv(self, key, default=False):
        return self.app.bv(key, default)

    def _secret(self, parent, key: str, width: int) -> ttk.Entry:
        e = ttk.Entry(parent, textvariable=self.v(key), width=width, show=MASK)
        self.secret_entries.append(e)
        return e

    def _show_secrets(self):
        show = "" if self.bv("notify.show").get() else MASK
        for e in self.secret_entries:
            e.configure(show=show)

    # ------------------------------------------------ config <-> variables --

    def load_vars(self, d: dict) -> None:
        for k, val in forms.notify_fields(d.get("notify")).items():
            if isinstance(val, bool):
                self.bv(f"notify.{k}").set(val)
            else:
                self.v(f"notify.{k}").set(val)
        self.bv("notify.show").set(False)  # secrets start masked after every load
        self._show_secrets()

    def fields(self) -> dict:
        keys = forms.notify_fields({})
        return {k: (self.bv(f"notify.{k}").get() if isinstance(dflt, bool) else self.v(f"notify.{k}").get())
                for k, dflt in keys.items()}

    def collect(self, d: dict) -> None:
        d["notify"] = forms.notify_block(self.fields(), d.get("notify"))  # raises FormError (a ConfigError)

    # --------------------------------------------------------------- send test --

    def send_test(self) -> bool:
        """notify.run_test on the settings in this window, off the Tk thread. Returns whether it started."""
        from ionomos import notify
        from ionomos.config import ConfigError

        if self.busy:
            return False
        try:
            s = forms.notify_settings(self.fields(), (self.app.data or {}).get("notify"))
        except ConfigError as exc:
            self.result.configure(text="Not sent: a setting isn't valid", foreground="#c62828")
            self.out.write(str(exc), clear=True)
            messagebox.showerror("Send test", str(exc))
            return False
        if not notify.channels(s):
            self.result.configure(text="Nothing to send to", foreground="#c62828")
            self.out.write("Fill in at least one address (Teams, Slack, webhook) or an email server first.",
                           clear=True)
            return False
        self.busy = True
        self.test_btn.configure(state="disabled")
        self.result.configure(text="Sending a test message…", foreground="#1565c0")
        self.out.write("", clear=True)
        log.info("notification test from the app: %s", ", ".join(notify.channels(s)))  # names only, never a value

        def say(line: str) -> None:
            self.app.post(lambda: self.out.write(line))

        def go():
            try:
                code = notify.run_test(s, say)
            except Exception as exc:  # noqa: BLE001 - deliver() never raises; belt and braces
                code = 1
                say(f"could not test: {type(exc).__name__}")
            self.app.post(lambda: self._tested(code))

        threading.Thread(target=go, name="app-notify-test", daemon=True).start()
        return True

    def _tested(self, code: int) -> None:
        self.busy = False
        self.test_btn.configure(state="normal")
        log.info("notification test from the app: %s", "all sent" if code == 0 else "not all sent")
        self.result.configure(text="All sent: check that the message arrived" if code == 0
                              else "Not all sent: see below (jobs are not affected)",
                              foreground="#2e7d32" if code == 0 else "#c62828")
