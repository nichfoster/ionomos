<#
ionomos installer for the proteomics PC (Windows 10/11, PowerShell 5.1+).

Run from the unzipped release folder, in a PowerShell window *as the account
that will run the watcher* (the shared lab account):

    powershell -ExecutionPolicy Bypass -File .\install.ps1

What it does (idempotent — safe to re-run to upgrade):
  1. finds Python 3.11+ (via the `py` launcher) and checks tkinter
  2. creates C:\Fragpipe_Auto\{inbox,workflows,fasta,logs} and C:\Fragpipe_General
  3. creates C:\Fragpipe_Auto\venv and installs ionomos from the bundled wheel(s)
  4. writes C:\Fragpipe_Auto\config.yaml from config.example.yaml (only if missing)
  5. copies run_ionomos.bat and registers a Task Scheduler task "ionomos"
     that starts at logon
  6. runs `ionomos check`

Parameters:
  -Root       install location           (default C:\Fragpipe_Auto)
  -UsersRoot  where experiments land     (default C:\Fragpipe_General)
  -NoTask     skip Task Scheduler registration
#>
param(
    [string]$Root = "C:\Fragpipe_Auto",
    [string]$UsersRoot = "C:\Fragpipe_General",
    [switch]$NoTask
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

if ($Root -match " " -or $UsersRoot -match " ") {
    throw "Paths must not contain spaces (FragPipe cannot handle them): '$Root' / '$UsersRoot'"
}

# ---------------------------------------------------------------- 1. python
Step "Finding Python 3.11+"
$py = $null
foreach ($v in @("3.14", "3.13", "3.12", "3.11")) {
    try {
        $out = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $py = $out.Trim(); $pyver = $v; break }
    } catch {}
}
if (-not $py) {
    throw "No Python 3.11+ found via the 'py' launcher. Install from https://www.python.org/downloads/windows/ (tick 'tcl/tk and IDLE' and 'py launcher'), then re-run."
}
Ok "Python $pyver at $py"
& $py -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "tkinter is missing for this Python. The resolver window will be disabled (folders with problems get a .REJECTED.txt instead)."
    Warn "To fix: re-run the Python installer -> Modify -> tick 'tcl/tk and IDLE'."
} else { Ok "tkinter present (resolver window will work)" }

# --------------------------------------------------------------- 2. folders
Step "Creating folders"
foreach ($d in @("$Root", "$Root\inbox", "$Root\workflows", "$Root\fasta", "$Root\logs", "$UsersRoot")) {
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d | Out-Null; Ok "created $d" }
    else { Ok "exists  $d" }
}

# ------------------------------------------------------------------ 3. venv
Step "Installing ionomos into $Root\venv"
$venv = "$Root\venv"
if (-not (Test-Path -LiteralPath "$venv\Scripts\python.exe")) {
    & $py -m venv $venv
    Ok "venv created"
}
$vpy = "$venv\Scripts\python.exe"
$wheels = Get-ChildItem -LiteralPath $here -Filter "ionomos-*.whl" | Sort-Object Name -Descending
if (-not $wheels) { throw "No ionomos-*.whl next to install.ps1. Build a release with deploy\make_release.sh on the Mac." }
$wheel = $wheels[0].FullName
$deps = Join-Path $here "wheels"
& $vpy -m pip install --upgrade pip --quiet
if (Test-Path -LiteralPath $deps) {
    Ok "installing from bundled wheels (offline)"
    & $vpy -m pip install --no-index --find-links $deps --upgrade $wheel
} else {
    Ok "installing (needs internet for PyYAML)"
    & $vpy -m pip install --upgrade $wheel
}
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
$ver = & "$venv\Scripts\ionomos.exe" --version
Ok "$ver installed"

# ---------------------------------------------------------------- 4. config
Step "Config"
$cfg = "$Root\config.yaml"
if (-not (Test-Path -LiteralPath $cfg)) {
    $text = Get-Content -LiteralPath (Join-Path $here "config.example.yaml") -Raw
    $r = $Root.Replace("\", "/"); $u = $UsersRoot.Replace("\", "/")
    $text = $text.Replace("C:/Fragpipe_Auto", $r).Replace("C:/Fragpipe_General", $u)
    Set-Content -LiteralPath $cfg -Value $text -Encoding UTF8
    Ok "wrote $cfg  <-- EDIT THIS: fragpipe_exe path, user aliases, workflow names"
} else { Ok "kept existing $cfg" }
Copy-Item -LiteralPath (Join-Path $here "run_ionomos.bat") -Destination "$Root\run_ionomos.bat" -Force
Ok "copied run_ionomos.bat"

# ------------------------------------------------------- 5. task scheduler
if (-not $NoTask) {
    Step "Registering Task Scheduler task 'ionomos' (runs at logon, interactive so the resolver window can show)"
    $user = "$env:USERDOMAIN\$env:USERNAME"
    schtasks /Create /TN "ionomos" /TR "`"$Root\run_ionomos.bat`"" /SC ONLOGON /RU "$user" /IT /RL LIMITED /F | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "task registered for $user. Start it now with:  schtasks /Run /TN ionomos" }
    else { Warn "schtasks failed — register manually (see docs/DEPLOY_WINDOWS.md)" }
}

# ----------------------------------------------------------------- 6. check
Step "ionomos check"
& "$venv\Scripts\ionomos.exe" --config $cfg check
Write-Host ""
Write-Host "Done. Next: edit $cfg, put your .workflow files in $Root\workflows, then:" -ForegroundColor Cyan
Write-Host "    $venv\Scripts\ionomos.exe --config $cfg check"
Write-Host "    schtasks /Run /TN ionomos          (or double-click $Root\run_ionomos.bat)"
