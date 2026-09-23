<#
Ionomos "development install" for the proteomics PC — run ONCE.

    powershell -ExecutionPolicy Bypass -File dev_install.ps1

Afterwards Ionomos runs straight from a copy of the GitHub repository, so
picking up a fix pushed from the Mac is one button in the app
("Update from GitHub & restart") or one command (`ionomos update`).
No exe to rebuild, nothing to uninstall.

What it does (safe to re-run):
  1. checks for git (offers to install it with winget) and Python 3.11+ with tkinter
  2. clones https://github.com/nichfoster/ionomos into C:\ionomos-src
     (or pulls if it is already there)
  3. creates C:\ionomos-src\.venv and installs ionomos into it in editable mode
  4. puts a "Ionomos" shortcut on the Desktop (opens the app, no console window)
  5. opens the app so you can do the folder setup

Parameters:
  -Dir   where the code lives   (default C:\ionomos-src — no spaces!)
  -Repo  git URL               (default the lab repo above)
#>
param(
    [string]$Dir = "C:\ionomos-src",
    [string]$Repo = "https://github.com/nichfoster/ionomos.git",
    [switch]$NoOpen
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

if ($Dir -match " ") { throw "The install folder must not contain spaces: '$Dir'" }

# ------------------------------------------------------------------ 1. tools
Step "git"
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Warn "git is not installed."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $ans = Read-Host "    Install it now with winget? [Y/n]"
        if ($ans -eq "" -or $ans -match "^[Yy]") {
            winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("Path", "User")
            $git = Get-Command git -ErrorAction SilentlyContinue
        }
    }
    if (-not $git) { throw "Install git from https://git-scm.com/download/win (defaults are fine), then re-run this script." }
}
Ok (git --version)

Step "Python 3.11+ with tkinter"
$py = $null
foreach ($v in @("3.14", "3.13", "3.12", "3.11")) {
    try {
        $out = & py "-$v" -c "import tkinter, sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $py = $out.Trim(); $pyver = $v; break }
    } catch {}
}
if (-not $py) {
    try { $out = & python -c "import tkinter, sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0) { $py = $out.Trim(); $pyver = "(python on PATH)" } } catch {}
}
if (-not $py) {
    throw "No Python 3.11+ with tkinter found. Install from https://www.python.org/downloads/windows/ — tick 'Add python.exe to PATH', then Customize -> tick 'tcl/tk and IDLE' and 'py launcher'. Re-run."
}
Ok "Python $pyver at $py"

# ------------------------------------------------------------------ 2. code
Step "Code -> $Dir"
if (Test-Path -LiteralPath "$Dir\.git") {
    git -C $Dir pull --ff-only
    Ok "updated existing checkout"
} else {
    Write-Host "    (public repository: no GitHub sign-in needed)"
    git clone $Repo $Dir
    Ok "cloned"
}
Ok ("now at: " + (git -C $Dir log -1 --format="%h %cs %s"))

# ------------------------------------------------------------------ 3. venv
Step "Python environment -> $Dir\.venv"
$venv = "$Dir\.venv"
if (-not (Test-Path -LiteralPath "$venv\Scripts\python.exe")) { & $py -m venv $venv; Ok "venv created" }
$vpy = "$venv\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -e "$Dir\ionomos[dev]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed (internet needed the first time)" }
Ok (& "$venv\Scripts\ionomos.exe" --version)

# -------------------------------------------------------------- 4. shortcut
Step "Desktop shortcut"
$pyw = "$venv\Scripts\pythonw.exe"
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Ionomos.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = "-m ionomos setup"
$s.WorkingDirectory = $Dir
$s.Description = "Ionomos setup & control (development install)"
$s.Save()
Ok $lnk
Copy-Item -LiteralPath "$Dir\deploy\update.ps1" -Destination "$Dir\UPDATE.ps1" -Force  # easy to find
Ok "$Dir\UPDATE.ps1  (same as the app's Update button)"

# ------------------------------------------------------------------ 5. go
Write-Host ""
Write-Host "Done. Double-click 'Ionomos' on the Desktop any time." -ForegroundColor Cyan
Write-Host "To pick up fixes pushed from the Mac: Run & Test tab -> 'Update from GitHub & restart'."
if (-not $NoOpen) { Start-Process -FilePath $pyw -ArgumentList "-m ionomos setup" -WorkingDirectory $Dir }
