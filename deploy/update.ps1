<#
Pull the latest Ionomos from GitHub and reinstall — the command-line twin of
the app's "Update from GitHub & restart" button. Stops a running watcher first.

    powershell -ExecutionPolicy Bypass -File C:\ionomos-src\UPDATE.ps1
#>
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = if (Test-Path -LiteralPath "$here\.git") { $here } else { Split-Path -Parent $here }
$lw = "$repo\.venv\Scripts\ionomos.exe"
if (-not (Test-Path -LiteralPath $lw)) { throw "No dev install at $repo (run deploy\dev_install.ps1 first)" }

# stop a running watcher gracefully (a running search is re-queued); it restarts at next logon / Start watcher
& $lw stop --wait 60

& $lw update
if ($LASTEXITCODE -ne 0) { throw "update failed" }
Write-Host "`nNow at: $(git -C $repo log -1 --format='%h %cs %s')" -ForegroundColor Green
Write-Host "Reopen Ionomos (Desktop shortcut) and press Start watcher if you use it without the startup task."
