<#
Clean slate: remove every trace of Ionomos *the program* - and of LabWatch, its
old name (<= 0.4.0) - from this PC so you can install again from scratch.
Normally you don't need this: Settings -> Apps -> Ionomos -> Uninstall does it.
NEVER touches lab data or settings:

    KEPT     C:\Fragpipe_Auto\config.yaml, inbox\, workflows\, fasta\, logs\, ionomos.db
    KEPT     C:\Fragpipe_General\  (everyone's experiments)
    REMOVED  the installed program (C:\Ionomos, via its uninstaller), startup tasks,
             running watchers, Ionomos.exe / ionomos-cli.exe and LabWatch.exe / labwatch-cli.exe,
             the Way-B venv (C:\Fragpipe_Auto\venv), run_ionomos.bat,
             the dev install (C:\ionomos-src, with -DevInstall),
             build leftovers in a repo (dist\, build\, ionomos\.venv-build, with -Repo <path>),
             %APPDATA%\Ionomos + %APPDATA%\labwatch (remembered config path, app log), Desktop shortcuts

    powershell -ExecutionPolicy Bypass -File clean_slate.ps1
    powershell -ExecutionPolicy Bypass -File clean_slate.ps1 -DevInstall            # also delete C:\ionomos-src
    powershell -ExecutionPolicy Bypass -File clean_slate.ps1 -Repo C:\path\to\repo  # also delete build output there
    powershell -ExecutionPolicy Bypass -File clean_slate.ps1 -Config                # ALSO delete config.yaml + ionomos.db (data still kept)
#>
param(
    [string]$Root = "C:\Fragpipe_Auto",
    [string]$DevDir = "C:\ionomos-src",
    [string]$Repo = "",
    [switch]$DevInstall,
    [switch]$Config
)
$ErrorActionPreference = "Continue"
function Gone($p) { if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue; Write-Host "  removed $p" } }

Write-Host "==> stopping Ionomos" -ForegroundColor Cyan
foreach ($t in @("Ionomos", "labwatch")) { schtasks /Delete /TN $t /F 2>$null | Out-Null }
foreach ($img in @("Ionomos.exe", "ionomos-cli.exe", "LabWatch.exe", "labwatch-cli.exe", "labwatch.exe")) { taskkill /IM $img /T /F 2>$null | Out-Null }
foreach ($pidFile in @("$Root\logs\ionomos.pid", "$Root\logs\labwatch.pid")) {
    if (Test-Path -LiteralPath $pidFile) { $p = Get-Content -LiteralPath $pidFile; if ($p) { taskkill /PID $p /T /F 2>$null | Out-Null }; Gone $pidFile }
}
# python watchers started from a dev install
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" 2>$null |
    Where-Object { $_.CommandLine -match "ionomos|labwatch" } | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }

Write-Host "==> removing program files (data is kept)" -ForegroundColor Cyan
foreach ($un in @("C:\Ionomos\unins000.exe")) {
    if (Test-Path $un) { $p = Start-Process -FilePath $un -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -PassThru; $p.WaitForExit(); Start-Sleep 3; Write-Host "  uninstalled $(Split-Path $un)" }
}
foreach ($f in @("Ionomos.exe", "ionomos-cli.exe", "Ionomos-crash.txt", "LabWatch.exe", "labwatch-cli.exe", "labwatch.exe",
                 "LabWatch-crash.txt", "run_ionomos.bat", "run_labwatch.bat", "README.txt",
                 "DEPLOY_WINDOWS.md", "NAMING_CONVENTION.md", "venv")) { Gone "$Root\$f" }
foreach ($d in @("Ionomos", "labwatch")) { Gone (Join-Path $env:APPDATA $d) }
foreach ($l in @("Ionomos.lnk", "LabWatch.lnk")) { Gone (Join-Path ([Environment]::GetFolderPath("Desktop")) $l) }
if ($DevInstall) { Gone $DevDir }
if ($Repo) { foreach ($d in @("dist", "build", "ionomos\.venv-build", "ionomos\.venv")) { Gone (Join-Path $Repo $d) } }
if ($Config) { Gone "$Root\config.yaml"; Gone "$Root\ionomos.db"; Gone "$Root\labwatch.db"; Gone "$Root\learned_aliases.yaml" }

Write-Host ""
Write-Host "Clean. Kept: $Root\{inbox,workflows,fasta,logs}, C:\Fragpipe_General" -NoNewline
if (-not $Config) { Write-Host ", $Root\config.yaml and ionomos.db" } else { Write-Host "" }
Write-Host "Reinstall: run Ionomos-Setup-<version>.exe (docs\DEPLOY_WINDOWS.md), or docs\DEV_LOOP.md for a dev install."
