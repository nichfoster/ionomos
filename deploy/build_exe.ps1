<#
Build Ionomos.exe + ionomos-cli.exe on Windows with PyInstaller, then a release zip.
    powershell -ExecutionPolicy Bypass -File deploy\build_exe.ps1
Needs Python 3.11+ (py launcher) with tcl/tk. Output: dist\Ionomos-<version>-windows\ and .zip
Run this once on any Windows machine (can be the lab PC itself); copy the zip anywhere.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo
$py = $null
foreach ($v in @("3.14","3.13","3.12","3.11")) { try { & py "-$v" -c "import tkinter" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "py -$v"; break } } catch {} }
if (-not $py) { try { & python -c "import tkinter" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "python" } } catch {} }  # GitHub Actions: setup-python puts it on PATH
if (-not $py) { throw "Need Python 3.11+ with tkinter (python.org installer, tick tcl/tk)" }
if (-not (Test-Path "ionomos\.venv-build")) { Invoke-Expression "$py -m venv ionomos\.venv-build" }
$vpy = "ionomos\.venv-build\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -e ".\ionomos[dev]"
$ver = & $vpy -c "import ionomos; print(ionomos.__version__)"
# stamp the build: commit + date end up in `--version`, the app's footer and every problem report
$commit = (git rev-parse --short HEAD 2>$null); if (-not $commit) { $commit = "unknown" }
$built = Get-Date -Format "yyyy-MM-dd"
Set-Content -Path "ionomos\src\ionomos\_build.py" -Encoding UTF8 -Value "COMMIT = `"$commit`"`nBUILT = `"$built`""
Write-Host "==> PyInstaller (ionomos $ver, build $commit)"
& $vpy -m PyInstaller --noconfirm --clean --distpath dist\exe --workpath build\pyi deploy\ionomos.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$out = "dist\Ionomos-$ver-windows"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null
Copy-Item dist\exe\Ionomos.exe, dist\exe\ionomos-cli.exe, docs\DEPLOY_WINDOWS.md, docs\NAMING_CONVENTION.md $out
Set-Content -Path "$out\README.txt" -Value @"
Ionomos $ver  (portable copy - the installer Ionomos-Setup-$ver.exe is the easier way)

1. Copy this folder anywhere WITHOUT spaces in its path (e.g. C:\Ionomos).
2. Double-click Ionomos.exe  ->  it opens on the Setup checklist. Press Auto-setup, then follow the list.

ionomos-cli.exe is the same program for the command line:  ionomos-cli.exe check | status | dry-run <folder>
If Ionomos.exe ever fails to open, look for Ionomos-crash.txt next to it.
Full instructions: DEPLOY_WINDOWS.md
"@
Compress-Archive -Path "$out\*" -DestinationPath "$out.zip" -Force
Write-Host "==> $out.zip"
Get-ChildItem $out

# ---- the installer (Inno Setup 6) -> dist\Ionomos-Setup-<ver>.exe
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "Inno Setup not found; trying winget/choco (the portable zip above is already built)"
    if (Get-Command choco -ErrorAction SilentlyContinue) { choco install innosetup -y --no-progress | Out-Null }
    elseif (Get-Command winget -ErrorAction SilentlyContinue) { winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements | Out-Null }
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($iscc) {
    & $iscc /Q "/DAppVersion=$ver" deploy\ionomos.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "==> dist\Ionomos-Setup-$ver.exe"
} else {
    Write-Host "!! no Inno Setup: only the portable zip was built" -ForegroundColor Yellow
}
Remove-Item "ionomos\src\ionomos\_build.py" -ErrorAction SilentlyContinue
