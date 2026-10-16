# The app's UI: what it is, what we saw, and the plan for daily driving

The app (`ionomos setup`, `Ionomos.exe`) is the lab PC's control panel. The
maintainer's brief (2026-10-15): keep the current look, "a robust lab tool
for real scientists", and make it easier to navigate, with more features,
checks and tests. This file is the spec for that work. It names the rules the
UI already follows, lists every tab and window and who uses them, records the
problems seen on screen, and lays out the plan in four slices, each item with
its GitHub issue.

**The look stays as it is.** Plain ttk with the system theme (`vista` on
Windows), the existing colours (green `#2e7d32`, red `#c62828`, amber
`#b26a00`, grey hints `#666`), dense information, plain words. No new theme,
icons, colours, fonts or restyling. Every item below says how it touches the
look; an item that would change it is out of scope.

## Principles (as the code already has them)

1. **One window, tabs.** Everything happens in one main window
   (`app.py`, a `ttk.Notebook`). Separate windows are only for things that
   need a person at a moment the app chooses: the review/resolver window at a
   drop (D13, D34), the attention pop-ups (`popups.py`), and dialogs a button
   opens.
2. **Plain words.** Labels say what happens ("Retry search", "Remind me in
   an hour"), hints in grey say why, errors say what to do (D46: "say what
   to do, not only what happened"). Help is one Markdown source shown
   everywhere (D46, docs/HELP.md).
3. **Nothing is applied without a click.** Edits stay in the window until
   **Save**. Save validates a candidate file first and backs up the old one
   (`configio`, `config-backups/`). The assistant only proposes; Confirm
   applies (D75). The library panel proposes; Apply changes, then Save,
   applies (D89, D90). The review window shows every drop before it is filed
   (D34).
4. **The logic is outside Tk** (D65, D67, D72). `forms.py`, `roles.py`,
   `accuracy.py`, `setupcheck.py`, `askui.py`, `library.py`, and now
   `uinav.py` hold the logic and have tests that run everywhere. The Tk modules
   copy values in and out.
5. **Off the Tk thread.** Anything that touches disks, FragPipe, the network
   or a big file runs on a daemon thread and comes back through `App.post`
   (the queue pumped by `root.after`). Tk objects are never touched or freed
   off the Tk thread (`tkutil.py`). The drop handler never calls Tk (D82).
6. **Long actions can be stopped or waited out.** Searches: Cancel, Pause
   searches. Library scan: Stop. Checks (D67): buttons are disabled while a
   check runs and a second click is refused. The watcher is a separate
   process, so no dialog in the app holds up intake or a search.
7. **Errors never kill the app.** `report_callback_exception` and the pump
   catch everything, write a crash file, and say where it is.

## Who uses it, and for what

| Who | When | What they touch |
|---|---|---|
| **PC keeper** (the person who set Ionomos up and keeps it running) | once, then after a problem or an update | Setup, Folders, Users, Methods, Advanced, Run & Test, Notifications, Jobs (Pause, Check FragPipe install), updates, Report a problem |
| **Lab member dropping a folder** | each experiment | the review window at the drop, the Inbox, Jobs ("is mine done?"), the pop-ups (failed search, analysis question), the report |
| **Someone re-analysing** | after a search, often days later | Jobs → Re-run analysis / Analysis options…, the Analysis tab (conditions, comparisons, figure style, accuracy), the report |

## Inventory

### Tabs (left to right, since D95: Daily · Setting up · Help; `uinav.TABS`)

| Tab | What it is for | Daily for |
|---|---|---|
| **Today** | What needs you, watcher + queue line, the checks' one line ("Checks: N problems"), recent jobs with Open report / Open folder / Re-run analysis / Analysis options… / Show in Jobs | everyone |
| **Inbox** | What is in the inbox now (the path, "empty" or "N items waiting"; read on a thread while shown); Delete moves an item to the removed-items folder | lab member, PC keeper |
| **Jobs** | Every job (status, time, what FragPipe is doing), Find, details and the likely cause of a failure, Open report / folder / log, Re-run analysis, Analysis options…, Retry, Cancel, Copy details, Zip; Pause searches, Instrument QC, Check FragPipe install (the full preflight, minutes) | everyone |
| **Analysis** | Sub-tabs: Analyse an experiment (the experiment editor, its buttons on top), Lab defaults, Figure style, Check accuracy | re-analysing |
| **✓ Setup** | Check everything (`checks.everything`: setup, FragPipe from disk, the library, health, what needs you), Auto-setup, a Fix button per open item; the list scrolls | PC keeper |
| **Folders** | Paths (inbox, users, workflows, FASTA, logs, ledger, FragPipe), Quick fill, Create all, Find FragPipe | PC keeper, once |
| **Users** | User folders and their initials, learned aliases, the catch-all user | PC keeper |
| **Methods** | Methods (keywords, workflow, FASTA, data type, post-processing), Import workflow…, Test names…, and the library panel (Find on this PC…, Download from UniProt…, Add decoys and contaminants…, Check for updates) | PC keeper |
| **Advanced** | Watcher timings and FragPipe threads/RAM/timeout/disk (checked as you type), the resolver window and pop-ups switch, learned aliases file, config file (open, logs, reset, Undo last save, Restore a previous config…) | PC keeper |
| **Run & Test** | Watcher page: start/stop and status, diagnostics, startup task, updates; Testing page: the testbed, the development-install box; an output pane under both | PC keeper |
| **Notifications** | Teams / Slack / webhook / email when a search ends | PC keeper |
| **Help** | Keyboard shortcuts, the full help page button, the set-up text | PC keeper |

Bottom bar: config path, Change…, the status line, the busy indicator
("working: …" + progress bar while something runs), Help, Report a problem…,
**⚠ N need attention** (when there is something), Update to x.y.z (when there
is one), Reload, Save, Save & Check; below it the build line and the
watcher / FragPipe strip.

### Windows and dialogs

| Window | Opened by | Module | Tested by |
|---|---|---|---|
| Review / resolver window ("check before filing", "needs a hand") | a drop (watcher or app), Run & Test → Resolver window demo | `resolve.py` | `test_resolve.py`, `test_review.py` |
| Attention pop-up (5 kinds) | by itself, ⚠ list, Today → Open | `popups.ItemWindow` | `test_app.py`, `test_assistant_*` |
| Needs attention list | ⚠ badge, Today → The whole list… | `popups.Popups.center` | `test_app.py` |
| Ask about this | pop-up, list | `popups.AskWindow` | `test_assistant_ask_button.py` |
| Assistant proposal (modal) | an answer with a proposal | `popups.ProposalDialog` | `test_assistant_proposals.py` |
| Report a problem | bottom bar, pop-up, Jobs → Zip… | `bundle_dialog.py` | `test_app.py` |
| Check the folder before analysing | Analysis → Folder…, a drop on the window | `folder_check.py` | `test_app.py` (through the Analysis tab) |
| Find on this PC / Choose… / proposals / Download / Prepare | Methods tab | `library_tab.py` | `test_library_tab.py` (Download and Prepare: not driven) |
| Test your names | Methods → Test names… | `app.test_names` | `test_app.py`, the smoke test |
| Restore a previous config | Advanced → Restore a previous config… | `app._pick_backup` | `test_app.py`, the smoke test |
| Save: what will change | Save, Ctrl+S | `app._confirm_changes` | `test_app.py`, the smoke test |
| Change condition prompt | experiment editor | `experiment_editor.py` (simpledialog) | `test_app.py` |
| Colour picker | Figure style → Pick… | `tkinter.colorchooser` | `test_app.py` (patched) |
| Error box | any callback that raises | `app._on_tk_error` | `test_app.py` |
| every `messagebox.showerror` | errors | `tkinter.messagebox` | `test_ui_words.py` (the text names the next step) |

(The job-number prompts of Run & Test are gone, S1.9.)

## What we saw (2026-10-15, Windows 11, 3840×2160 at 150 %, testbed with 4 jobs)

Screenshots of every tab were taken with the testbed config (two searches
done, two failed, two attention items), at the window's natural size and at
the size of a 1366×768 laptop.

1. **The app always opened on the setup checklist**, also on a set-up PC.
   The checklist is 13 rows; the three workflow rows are six lines each, so
   the two rows that needed a click (Watcher running → Start, Starts
   automatically → Install) were the last on the page and **below the fold
   on a laptop-sized window**. Nothing on that tab says what happened today.
2. **"What needs me now" lived in three places**: pop-ups that open by
   themselves, the ⚠ badge in the bottom bar (opens a separate 820×360
   window), and the Jobs tab's colours. The watcher's state (running /
   stopped / not responding) was only on 5 Run & Test.
3. **The daily tabs were in the middle.** Inbox, 6 Jobs and 7 Analysis were
   tabs 7–9 of 11, after five set-up tabs. Inbox has no number and no
   padding (`"Inbox"` beside `"  6  Jobs  "`).
4. **The window is bigger than a laptop screen.** Its natural size was
   2636×1410 pixels at 150 % (about 1760×940 at 100 %): wider and taller than
   a 1366×768 laptop or a 1920×1080 screen at 125 % (1536×864). Nothing
   scrolls. At 1366×768 **the whole bottom bar disappeared** (Save, Help,
   Report a problem, the ⚠ badge), because it was packed after the tabs.
   On the Analysis tab the cut-offs and the **Run analysis / Save choices /
   Open report** buttons were below the window's edge.
5. **A long config path pushed the buttons off** the bottom bar (Help, Report
   a problem and the ⚠ badge went first).
6. **Fixed pixel sizes don't follow display scaling.** The needs-attention
   list (820×360) cut its columns at 150 % ("A FragPipe search faile");
   the folder check (860×640) is fixed too.
7. **Overlapping text on 4 Advanced**: the hint "Needs the watcher to run in
   the logged-in session" was gridded on the same row as the "Pop up a window
   when something needs you" checkbox and covered it.
8. **Button rows disagree.** Save & Check is on the bottom bar and on Run &
   Test; Run & Test has Stop watcher twice; Retry and Open job folder exist on
   Jobs (on the selected row) and on Run & Test (a text prompt asking for a
   job number from a list of 12); the order of the same actions differs
   between Jobs (Open report, Open folder, FragPipe log, Re-run analysis,
   Analysis options…, Retry, Cancel, Copy details, Zip…) and the failed-search
   pop-up (Retry search, FragPipe log, Open folder, Report a problem…, …).
9. **Jobs**: no way to find a job; the method column was cut ("metho",
   "isoDTI"); the "now" column is cut with no way to read it but selecting the
   row; the detail pane is empty until a row is selected.
10. **Clicks to re-run an analysis**: Jobs tab, select the row, Re-run
    analysis (3); or Analysis tab, open the combobox, pick, Run analysis (4),
    and that button was off screen on a laptop.
11. **No keyboard access**: no shortcuts, no F1, Notebook traversal off,
    lists open only on double-click.
12. **Inbox** shows an empty white box with no path and no "empty" text, and
    re-reads the shared folder every 2 s on the Tk thread even when the tab
    is hidden. **Help** is 190 lines of set-up text in a monospace box that
    names tabs by number.
13. **Unsaved edits are lost silently.** Apply aliases, Apply changes (a
    method), Restore a previous config… and Reset all say "remember to Save"
    in the status line; closing the window or Change… loses them without a
    question.
14. **Work on the Tk thread that can freeze the window**: Import workflow…
    copies a FASTA (can be GBs); `on_close` and Update wait for the watcher to
    stop (up to 15 s); Instrument QC builds its page; the status line opens
    the ledger every 2 s; Reset testbed deletes folders.
15. **Not seen on screen by anyone before**: the windows D65, D67, D72 and D75
    list as "not verified on screen". The screenshots above are the first look
    at the Analysis, Notifications and accuracy pages on Windows at 150 %; they
    lay out, apart from items 4 and 6.

Not a UI problem, but found while making the testbed: the fake FragPipe's
isoDTB search failed on `EJQ_PK_EJQ_2_027_…` vs `EJQ-2-027` in a file name
(FileNotFoundError), and the explanation said "ended from outside". Worth a
look by whoever owns `fake_fragpipe.py` / `fragpipe.EXPLANATIONS`.

## The plan

Each item: what, why (which user, which problem above), files, risk to the
look, tests, size (S ≤ half a day, M ≤ two days, L more). **Done** means on
branch `feat/tmt-library` in this change.

### Slice 1 — navigation

#### S1.1 A Today tab (done) — [#109](https://github.com/nichfoster/ionomos/issues/109)
- **What**: a first tab with one line of what needs you and the queue, the
  watcher line, the checklist's one line with a button to it, the open
  attention items (Open = the pop-up with what to do), and the last 15 jobs
  with Open report / Open folder / Re-run analysis / Analysis options… / Show
  in Jobs / Open inbox. Refreshed every 4 s while shown, read on a thread
  (`uinav.snapshot`). A new install still opens on ✓ Setup. A job list that
  can't be read (a damaged `ionomos.db`) is said beside the headline with
  `ionomos repair-ledger`, never shown as an empty, healthy list.
- **Why**: everyone; problems 1, 2, 10 (re-running an analysis is two clicks
  from the first screen).
- **Files**: `uinav.py` (new), `app.py` (`_tab_today`, `refresh_today`,
  `job_action(job=…)`), `help/faq.md` (`faq.app`).
- **Look**: the same widgets as Setup and Jobs (a bold title, LabelFrames,
  Treeviews with the Jobs colours). No new style.
- **Tests**: `test_ui_nav.py` (snapshot, headline, report link, never raises;
  the window test in CI).
- **Size**: M.

#### S1.2 Remembered window size and last tab, fitted to the screen (done) — [#110](https://github.com/nichfoster/ionomos/issues/110)
- **What**: `<config folder>/ionomos-ui.json` (`names.UI_STATE_FILE`) keeps
  the geometry, maximised or not, and the tab by key. On start it is clamped
  to the screen (a window left on a missing second monitor comes back);
  without it, a natural size bigger than the screen is shrunk to fit.
- **Why**: PC keeper and lab members; problems 1, 4.
- **Files**: `uinav.py`, `app.py`, `names.py`.
- **Look**: none.
- **Tests**: `test_ui_nav.py` (round trip, bad files, clamp table, start tab;
  restart in the window test).
- **Size**: S.

#### S1.3 The bottom bar is always on screen (done) — [#111](https://github.com/nichfoster/ionomos/issues/111)
- **What**: the bottom bar is packed before the tabs, and its buttons before
  the config path and the status line, so a small window shrinks the tabs and
  cuts the path, never Save, Help, Report a problem or the ⚠ badge. Also the
  overlapping hint on 4 Advanced (problem 7) and the cut method column on
  Jobs.
- **Why**: everyone on a laptop; problems 4, 5, 7.
- **Files**: `app.py`.
- **Look**: identical at the natural size.
- **Tests**: covered by S4.5 (layout at 1366×768); seen on screen.
- **Size**: S.

#### S1.4 F1 help for the tab you are on, and keyboard shortcuts (done) — [#112](https://github.com/nichfoster/ionomos/issues/112)
- **What**: F1 opens the help page at the current tab's topic
  (`uinav.TABS`); F5 refreshes the tab; Ctrl+S saves; Ctrl+F finds a job;
  Ctrl+0 is Today and Ctrl+1 … Ctrl+8 the tab with that number; Ctrl+Tab /
  Ctrl+Shift+Tab move between tabs; Enter opens the report in a job list.
  Bound on the main window only, so a pop-up keeps its own keys. Listed on
  the Help tab and in `faq.app`.
- **Why**: PC keeper and anyone who uses it daily; problem 11.
- **Files**: `uinav.py`, `app.py`, `help/faq.md`.
- **Look**: none (one text block on the Help tab).
- **Tests**: every tab's topic exists; the help entry lists every key; the
  window test presses F1, Ctrl+6, Ctrl+7.
- **Size**: S.

#### S1.5 Find a job (done) — [#113](https://github.com/nichfoster/ionomos/issues/113)
- **What**: a Find box on 6 Jobs: every word must match the job's number,
  status, user, method, experiment or reason; `12` or `#12` is job 12;
  Escape clears; "N shown" in the summary.
- **Why**: lab members ("where is mine?"), PC keeper; problem 9.
- **Files**: `uinav.job_matches`, `app.py`.
- **Look**: one label and entry in the existing top row.
- **Tests**: a match table in `test_ui_nav.py`; the window test.
- **Size**: S.

#### S1.6 Tabs grouped Daily / Setup / Help, without numbers — [#114](https://github.com/nichfoster/ionomos/issues/114)
- **What**: Today · Inbox · Jobs · Analysis │ Setup · Folders · Users ·
  Methods · Advanced · Run & Test · Notifications │ Help (`nb.insert` moves
  tabs without changing how they are built). Drop the numbers from the labels,
  and rewrite every "tab 3" / "tab 7" / "Jobs tab (6)" in `HELP_TEXT`,
  `setupcheck.py` ("Tab 3 → Find on this PC…"), `library_tab.py` and the help
  Markdown to use names. Ctrl+<n> then follows the new order.
- **Why**: everyone; problem 3.
- **Files**: `app.py`, `uinav.TABS`, `setupcheck.py`, `library_tab.py`,
  `help/*.md`, `docs/*`. Needs the owners of those files.
- **Look**: same tabs, other order; labels lose their numbers.
- **Tests**: `test_ui_nav` order test; a grep test that no text says "tab <n>".
- **Size**: M.

#### S1.7 A status strip on every tab — [#115](https://github.com/nichfoster/ionomos/issues/115)
- **What**: the build line under the bottom bar also shows the watcher state
  and the FragPipe line (what `_refresh_status_once` already computes), in the
  same green/red, so the watcher is visible without going to Run & Test.
- **Why**: PC keeper; problem 2.
- **Files**: `app.py`.
- **Look**: one more short text in an existing grey line.
- **Tests**: window test reads the strip after a fake heartbeat.
- **Size**: S.

#### S1.8 Long tabs scroll; primary buttons above the fold — [#116](https://github.com/nichfoster/ionomos/issues/116)
- **What**: the Setup checklist in a scrolled frame (or its open items
  first); in the experiment editor, Run analysis / Save choices / Open report
  move to the top row or the pane scrolls. One small `tkutil.ScrolledFrame`
  (canvas + ttk.Scrollbar, mouse wheel) used by both.
- **Why**: everyone on a laptop; problems 1, 4.
- **Files**: `tkutil.py`, `app.py`, `experiment_editor.py` (owner first).
- **Look**: a scrollbar appears only when needed.
- **Tests**: S4.5.
- **Size**: M.

#### S1.9 Run & Test split and de-duplicated — [#117](https://github.com/nichfoster/ionomos/issues/117)
- **What**: Run & Test keeps the watcher, startup task, updates and
  diagnostics; the testbed and the development-install box go into a
  "Testing" sub-tab. Remove the job-number prompts (Retry a failed job…,
  Open job folder…): the Jobs tab does both on a selected row. One Stop
  watcher. Save & Check only in the bottom bar.
- **Why**: PC keeper; problem 8.
- **Files**: `app.py`, `tests/test_app.py`.
- **Look**: fewer buttons; same widgets.
- **Tests**: existing testbed tests move to the sub-tab.
- **Size**: M.

#### S1.10 Window sizes in text units, not pixels — [#118](https://github.com/nichfoster/ionomos/issues/118)
- **What**: the attention list and the folder check size themselves from the
  font (`tkfont.Font().measure`, Treeview column widths in characters) or
  from their content, and are clamped to the screen with `uinav.fit_size`.
- **Why**: everyone at 125–150 % scaling; problem 6.
- **Files**: `popups.py`, `folder_check.py`.
- **Look**: same windows, columns no longer cut.
- **Tests**: S4.2 checks every Toplevel fits the screen and its columns their
  headings.
- **Size**: S.

### Slice 2 — robustness

#### S2.1 Every failure says what to do — [#119](https://github.com/nichfoster/ionomos/issues/119)
- **What**: go through the ~58 message boxes in `app.py` (and the tab
  modules): each error names what failed, why if known, and the next step;
  where a help entry exists, the box offers it (`askyesno`: "Open the help
  for this?") or a small ttk error window with OK and More help. A test
  lists every `messagebox.showerror` call and checks its text has a "→" /
  "press" / "pick" step or a help topic.
- **Why**: lab members who hit an error alone; principle 2.
- **Files**: `app.py`, `analysis_tab.py`, `notify_tab.py`, `help/*.md`.
- **Look**: the same message boxes, better words.
- **Tests**: the AST test above; existing error tests keep passing.
- **Size**: M.

#### S2.2 Nothing slow on the Tk thread — [#120](https://github.com/nichfoster/ionomos/issues/120)
- **What**: move to `App.post` threads: Import workflow… (FASTA copy),
  Instrument QC page build, waiting for the watcher to stop in Update and
  Quit, Reset testbed, the 2 s ledger read of the status line, and the
  Inbox's 2 s scan (only while the Inbox tab is shown, as Jobs already does).
- **Why**: everyone; problem 14 ("not responding" windows on a busy PC).
- **Files**: `app.py`.
- **Look**: none.
- **Tests**: a test that patches each call to sleep and checks the Tk loop
  still pumps (`_pump_until` with a deadline).
- **Size**: M.

#### S2.3 One busy indicator — [#121](https://github.com/nichfoster/ionomos/issues/121)
- **What**: `App.run_bg(label, fn, done)`: runs fn on a thread, shows
  "working: label…" with an indeterminate `ttk.Progressbar` in the bottom
  bar, disables the button that started it, refuses a second start (D67's
  rule, in one place). Used by Check FragPipe install, diagnostics, Re-run
  analysis, Import workflow, the checklist.
- **Why**: everyone ("did my click do anything?").
- **Files**: `app.py`.
- **Look**: the stock ttk progress bar, only while working.
- **Tests**: the helper with a slow fn; a second start is refused.
- **Size**: S.

#### S2.4 Undo the last Save — [#122](https://github.com/nichfoster/ionomos/issues/122)
- **What**: "Undo last save" (bottom bar menu or Advanced → Config file, and
  on Today after a Save) restores the newest `config-backups/` copy into the
  window, then Save as usual. Restore a previous config… stays for older
  ones and lists them with their time and what differs.
- **Why**: PC keeper after a wrong change.
- **Files**: `app.py`, `configio.py`.
- **Look**: one button.
- **Tests**: save twice, undo, the window holds the first; nothing written
  until Save.
- **Size**: S.

#### S2.5 "What changed" before Save — [#123](https://github.com/nichfoster/ionomos/issues/123)
- **What**: Save shows the changes in words, one line per key ("Fragpipe →
  Threads: 24 → 28"), from a diff of `configio.dump_config` before and after;
  OK saves, Cancel goes back. Skipped when nothing changed ("nothing to
  save") or with a "don't ask for one change" tick.
- **Why**: PC keeper; nothing applied without seeing it (principle 3).
- **Files**: `configio.py` (diff in words, no Tk), `app.py`.
- **Look**: a plain dialog with a text box.
- **Tests**: the word diff, table-driven; Save with Cancel writes nothing.
- **Size**: M.

#### S2.6 Unsaved changes are not lost silently — [#124](https://github.com/nichfoster/ionomos/issues/124)
- **What**: the window knows when its fields differ from the file (the same
  diff as S2.5); the title gets "•" and Quit, Change… and Reload ask "Save,
  Discard or Cancel".
- **Why**: PC keeper; problem 13.
- **Files**: `app.py`.
- **Look**: a dot in the title.
- **Tests**: change a field, close: asked; save, close: not asked.
- **Size**: S.

### Slice 3 — checks

#### S3.1 One "Check everything" list with Fix buttons — [#125](https://github.com/nichfoster/ionomos/issues/125)
- **What**: on the Setup tab (and `ionomos doctor`), one list made off the Tk
  thread from `setupcheck.run`, `preflight.run(dry=False)` (no FragPipe
  start), `library.update_check`, `health` (heartbeat, `recent_crashes`,
  `log_problems`, disk free), open attention items and paused searches. Each
  row: ✓ / ! / ✗, what, what to do, and a Fix button where the app can do it
  (the checklist's actions, open a tab, open a file). The full preflight with
  dry runs stays a separate button (minutes) with Stop.
- **Why**: PC keeper; one place instead of Setup, Jobs → Check FragPipe
  install, Run & Test → diagnostics and the Methods library.
- **Files**: `setupcheck.py` (the merge, no Tk), `app.py`, `cli.py`.
- **Look**: the checklist's existing rows.
- **Tests**: the merge on a testbed config (no Tk); a row per source.
- **Size**: L.

#### S3.2 A quick check on start — [#126](https://github.com/nichfoster/ionomos/issues/126)
- **What**: the quick part of S3.1 (no FragPipe, no scan) runs when the app
  opens; Today's Setup line says "checks: 2 problems" with a button.
- **Why**: PC keeper notices before a lab member does.
- **Files**: `app.py`.
- **Look**: one line on Today.
- **Tests**: window test with a broken path.
- **Size**: S.

#### S3.3 Fields checked as you type — [#127](https://github.com/nichfoster/ionomos/issues/127)
- **What**: number and path fields show the existing ● dot (as PathRow does)
  and a grey hint when invalid, from the loader's own check, instead of only
  a dialog at Save.
- **Why**: PC keeper.
- **Files**: `app.py`, `forms.py`.
- **Look**: the dots the Folders tab already has.
- **Tests**: `forms` validators table-driven.
- **Size**: M.

### Slice 4 — testing

#### S4.1 A headless Tk harness — [#128](https://github.com/nichfoster/ionomos/issues/128)
- **What**: `tests/tkharness.py`: the hidden `app` fixture (today in
  `test_app.py`), `pump_until`, `no_dialogs` (messagebox, simpledialog and
  filedialog patched to record and fail unless expected), a list of open
  Toplevels, and Tk errors captured from `report_callback_exception`. Same
  gating as now: CI always, locally only with `IONOMOS_GUI_TESTS=1`
  (`tests/conftest.gui_tests`).
- **Why**: every UI change; the tests above repeat this setup.
- **Files**: `tests/tkharness.py`, `tests/conftest.py`, `docs/TESTING.md`.
- **Look**: none.
- **Tests**: itself, by the suites that use it.
- **Size**: S.

#### S4.2 A smoke test that opens every tab and window, hidden — [#129](https://github.com/nichfoster/ionomos/issues/129)
- **What**: for each `uinav.TABS` key: select, F5, F1 (help patched), no Tk
  error. Then open every window in the inventory above with fake data
  (Test names, Resolver demo with an auto-skip, the attention list and each
  pop-up kind, Report a problem, the folder check, the library dialogs,
  Ask, a proposal) and close it; each must fit a 1366×768 screen.
- **Why**: problem 15: most windows have never been looked at.
- **Files**: `tests/test_ui_smoke.py`.
- **Look**: none.
- **Tests**: is one.
- **Size**: M.

#### S4.3 Tests for the untested windows — [#130](https://github.com/nichfoster/ionomos/issues/130)
- **What**: Test names (a name → its reading), the job-number prompts (or
  their removal, S1.9), Change… config, Download and Prepare dialogs (network
  and file work stubbed), the colour picker (patched), Install / Remove
  startup task buttons (service stubbed), Reset testbed.
- **Why**: problem 15.
- **Files**: `tests/test_app.py`, `tests/test_library_tab.py`.
- **Look**: none.
- **Tests**: are these.
- **Size**: M.

#### S4.4 Screenshots of every tab, on demand — [#131](https://github.com/nichfoster/ionomos/issues/131)
- **What**: `scripts/screenshots.py`: opens the app on a testbed config and
  saves a PNG of every tab, sub-tab and open window, at the natural size and
  at 1366×768 (Windows: GDI capture through ctypes, PNG written with zlib, no
  new dependency). Run before a UI PR; CI on Windows uploads them as an
  artifact.
- **Why**: problem 15 (the look checked by a person, not only tests).
- **Files**: `scripts/screenshots.py`, `.github/workflows/*`, `docs/TESTING.md`.
- **Look**: none.
- **Tests**: CI runs it.
- **Size**: S.

#### S4.5 A small-screen layout test — [#132](https://github.com/nichfoster/ionomos/issues/132)
- **What**: at 1366×768 (scaling 1.0) every tab's primary buttons (Save,
  Help, the ⚠ badge, Run analysis, Start watcher, Retry …) are mapped and
  inside the window (`winfo_rooty + winfo_height ≤` the root's).
- **Why**: problems 4 and 5 would have been caught.
- **Files**: `tests/test_ui_smoke.py`.
- **Look**: none.
- **Tests**: is one.
- **Size**: S.

## Done, and how it was checked

**Slice 1 (D93)**: S1.1–S1.5 and the fixes in S1.3 are in `app.py`, `uinav.py`, `names.py`
(`UI_STATE_FILE`), `help/faq.md` (`faq.app`), `deploy/ionomos.spec`.
`tests/test_ui_nav.py` checks the logic everywhere; its window test runs
in CI like `tests/test_app.py` and was run once locally with the window
hidden. The app was opened on a testbed and photographed at 150 % scaling at
its natural size and at 1366×768: the Today tab and the bottom bar look like
the rest of the app.

**Slices 1 (rest) to 4 (D95, issues #114–#132), all done**, with these notes:

| Item | Where | Note |
|---|---|---|
| S1.6 tabs grouped, no numbers | `uinav.TABS`, `App._order_tabs`, every help text | no separators in the tab row (ttk has none); the grouping is the order. `test_no_text_names_a_tab_by_number` |
| S1.7 status strip | `App.strip_lbl`, `uinav.watcher_status` | the same text as Run & Test, in its colour |
| S1.8 scrolling | `tkutil.ScrolledFrame`: the Setup list, the analysis pop-up's editor; the editor's action row on top | the main experiment editor on the Analysis tab does not scroll: its buttons are now above the fold |
| S1.9 Run & Test split | `App.run_nb`: Watcher / Testing pages | `retry_job`, `open_job`, `_pick_job` removed |
| S1.10 text units | `tkutil.text_size`, `fit_window`; popups, folder check, library dialogs, Test names, the pickers | the analysis pop-up is capped at 150×44 characters and scrolls inside |
| S2.1 what to do | every `showerror` in the Tk modules; `App.fail(title, what, how, topic)` | `tests/test_ui_words.py` reads the source (no display) |
| S2.2 off the Tk thread | `run_bg` / threads for the import, the QC page, Reset testbed, Quit, Update, the status tick, the log follow, the Inbox scan | `test_slow_work_never_blocks_the_tk_thread` |
| S2.3 busy indicator | `App.run_bg`, `busy_lbl` + `busy_bar` | |
| S2.4 undo | `undo_last_save`, `restore_config(path)`, `_pick_backup`; `configio.backups`, `latest_backup`, `backup_time` | backups within one second get `-2`, `-3` |
| S2.5 what changed | `configio.diff_words`, `App.save(confirm=True)`, `_confirm_changes` | only the Save button and Ctrl+S ask |
| S2.6 unsaved guard | `App.dirty`, `_guard_unsaved`, the title's dot | |
| S3.1 Check everything | `checks.py`, the Setup tab, `ionomos doctor` | no Stop for the full preflight yet (open) |
| S3.2 quick check on start | `refresh_setup` at start, Today's Checks line | |
| S3.3 fields as you type | `forms.check_setting`, the Advanced tab's `num()` | paths already had dots |
| S4.1 harness | `tests/tkharness.py`, fixtures `hidden_app`, `no_dialogs`, `tk_errors`; docs/TESTING.md | |
| S4.2 smoke test | `tests/test_ui_smoke.py` | the assistant's proposal dialog is not in it (its own tests cover it) |
| S4.3 untested windows | `tests/test_app.py` (Test names, Change…, startup task, Reset testbed, colour picker), the smoke test (Download, Prepare) | |
| S4.4 screenshots | `scripts/screenshots.py`; the Windows CI job uploads `ui-screenshots-3.14` | run here twice: 32 PNGs |
| S4.5 layout at 1366×768 | `test_main_buttons_are_on_screen_at_1366x768` | the root is mapped fully transparent for the measurement |

Checked: ruff; the no-Tk tests everywhere; every GUI test file run once locally with the windows hidden
(`tests.tkharness.hide_all_windows`), CI runs them for real. Not looked at by a person on screen since slice 1:
the screenshots CI uploads are for that.
