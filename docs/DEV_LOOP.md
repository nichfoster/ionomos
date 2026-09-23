# The Mac ↔ PC loop (while Ionomos is being prototyped)

Code is written on the Mac (with Claude), run on the proteomics PC. GitHub
sits in the middle. Nothing is ever "uninstalled".

```
 Mac                          GitHub                        Proteomics PC
 ┌──────────────────┐  push   ┌──────────────┐   pull    ┌───────────────────────────┐
 │ edit + tests     │ ──────▶ │ private repo │ ────────▶ │ C:\ionomos-src  (clone)  │
 │ (Claude)         │         │              │           │   └─ .venv  (editable)    │
 └──────────────────┘         └──────────────┘           │ Ionomos app ← runs from  │
        ▲                                                │   the clone directly      │
        │  paste "Copy diagnostics"                      └───────────────────────────┘
        └────────────────────────────────────────────────────────┘
```

**Editable install** is the trick: the PC's Python environment points at the
files in `C:\ionomos-src` instead of a copy. So `git pull` *is* the update.
Config (`C:\Fragpipe_Auto\config.yaml`), the ledger, logs and lab data live
outside the clone and are never touched.

## One-time, on the PC (5 min)

1. Download [`deploy/dev_install.ps1`](../deploy/dev_install.ps1) from GitHub
   (open the file → "Download raw file"), or copy it over. Put it anywhere.
2. Right-click PowerShell → Run, then:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\dev_install.ps1
   ```
   It installs git if missing (asks first), clones the repo to `C:\ionomos-src`
   (a browser window asks you to sign in to GitHub once — it's a private
   repo), makes the Python environment, puts a **Ionomos** shortcut on the
   Desktop, and opens the app.
3. In the app: tab 1 → **Apply layout** → **Create all missing folders**, then
   tab 5 → **Save & Check**. Same as [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md) A3.

## Every day after that

| On the Mac | On the PC |
|---|---|
| Describe the problem to Claude (paste the diagnostics), it fixes + tests, then `git push` | Open Ionomos → tab 5 → the *Development install* box says **UPDATE AVAILABLE** → **Update from GitHub & restart** |
| | Test again |
| | Something's off → **Copy diagnostics** → paste into the chat |

That's it. The Update button stops the watcher, pulls, reinstalls, and reopens
the app (press **Start watcher** again if you don't use the startup task).
From a terminal the same thing is `C:\ionomos-src\UPDATE.ps1`.

**"Copy diagnostics"** puts one text block on the clipboard: version + commit,
`check`, `status --all`, the config, the last 150 log lines, what's in the
inbox and any `.REJECTED.txt` notes. Also saved as
`C:\Fragpipe_Auto\logs\diagnostics-<date>.txt`. Paste the whole thing — that's
everything needed to reproduce a problem on the Mac.

## Rules that keep it painless

- **Never edit code on the PC.** Update refuses to run if files in the clone
  were changed by hand (it would have to throw the edits away). Edit on the
  Mac, push, update.
- **Everything of yours lives outside `C:\ionomos-src`.** If the clone is
  ever broken, delete the folder and re-run `dev_install.ps1`; nothing is lost.
- The Mac side: `scripts/test_mac.sh` before pushing. GitHub also runs the
  suite on Linux *and Windows* on every push (Actions tab), so Windows-only
  mistakes get caught there first.

## When it's stable: back to the exe

For the lab's real install (no git, no Python on the machine) the frozen exe
is still the plan. GitHub builds it:

```bash
git tag v0.2.0 && git push --tags
```

→ Actions builds `Ionomos-0.2.0-windows.zip` on a Windows machine and attaches
it to a Release on GitHub. Or *Actions → build-exe → Run workflow* for a
one-off build without tagging (zip under "Artifacts"). Install per
[DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md) Way A. The dev install and the exe
install can coexist; they share the same `config.yaml`.

## Optional: Claude Code on the PC too

Installing Claude Code on the PC lets you say "look at the log and tell me
why the drop didn't move" *there*, with it reading the real files. It works
fine alongside this loop — GitHub is still what moves code between the two
machines — but each machine's Claude has its own memory of the project, so
keep the Mac as the place where changes are made.
