<#
Clean slate: remove every trace of LabWatch *the program* from this PC so you
can install again from scratch. NEVER touches lab data or settings:

    KEPT     C:\Fragpipe_Auto\config.yaml, inbox\, workflows\, fasta\, logs\, labwatch.db
    KEPT     C:\Fragpipe_General\  (everyone's experiments)
    REMOVED  startup task, running watcher, LabWatch.exe / labwatch-cli.exe,
             the Way-B venv (C:\Fragpipe_Auto\venv), run_labwatch.bat,
             the dev install (C:\labwatch-src, with -DevInstall),
             build leftovers in a repo (dist\, build\, labwatch\.venv-build, with -Repo <path>),
             %APPDATA%\labwatch (remembered config path), the Desktop shortcut

    powershell -ExecutionPolicy Bypass -File clean_slate.ps1
    powershell -ExecutionPolicy Bypass -File clean_slate.ps1 -DevInstall            # also delete C:\labwatch-src
    powershell -ExecutionPolicy Bypass -File clean_slate.ps1 -Repo C:\path\to\repo  # also delete build output there
    powershell -ExecutionPolicy Bypass -File clean_slate.ps1 -Config                # ALSO delete config.yaml + labwatch.db (data still kept)
#>
param(
    [string]$Root = "C:\Fragpipe_Auto",
    [string]$DevDir = "C:\labwatch-src",
    [string]$Repo = "",
    [switch]$DevInstall,
    [switch]$Config
)
$ErrorActionPreference = "Continue"
function Gone($p) { if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue; Write-Host "  removed $p" } }

Write-Host "==> stopping LabWatch" -ForegroundColor Cyan
schtasks /Delete /TN "labwatch" /F 2>$null | Out-Null
foreach ($img in @("LabWatch.exe", "labwatch-cli.exe", "labwatch.exe")) { taskkill /IM $img /T /F 2>$null | Out-Null }
$pidFile = "$Root\logs\labwatch.pid"
if (Test-Path -LiteralPath $pidFile) { $p = Get-Content -LiteralPath $pidFile; if ($p) { taskkill /PID $p /T /F 2>$null | Out-Null }; Gone $pidFile }
# python watchers started from a dev install
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" 2>$null |
    Where-Object { $_.CommandLine -match "labwatch" } | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }

Write-Host "==> removing program files (data is kept)" -ForegroundColor Cyan
foreach ($f in @("LabWatch.exe", "labwatch-cli.exe", "labwatch.exe", "LabWatch-crash.txt", "run_labwatch.bat", "README.txt",
                 "DEPLOY_WINDOWS.md", "NAMING_CONVENTION.md", "venv")) { Gone "$Root\$f" }
Gone (Join-Path $env:APPDATA "labwatch")
Gone (Join-Path ([Environment]::GetFolderPath("Desktop")) "LabWatch.lnk")
if ($DevInstall) { Gone $DevDir }
if ($Repo) { foreach ($d in @("dist", "build", "labwatch\.venv-build", "labwatch\.venv")) { Gone (Join-Path $Repo $d) } }
if ($Config) { Gone "$Root\config.yaml"; Gone "$Root\labwatch.db"; Gone "$Root\learned_aliases.yaml" }

Write-Host ""
Write-Host "Clean. Kept: $Root\{inbox,workflows,fasta,logs}, C:\Fragpipe_General" -NoNewline
if (-not $Config) { Write-Host ", $Root\config.yaml and labwatch.db" } else { Write-Host "" }
Write-Host "Reinstall: docs\DEV_LOOP.md (dev install) or docs\DEPLOY_WINDOWS.md (exe)."
