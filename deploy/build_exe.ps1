<#
Build LabWatch.exe + labwatch.exe on Windows with PyInstaller, then a release zip.
    powershell -ExecutionPolicy Bypass -File deploy\build_exe.ps1
Needs Python 3.11+ (py launcher) with tcl/tk. Output: dist\LabWatch-<version>-windows\ and .zip
Run this once on any Windows machine (can be the lab PC itself); copy the zip anywhere.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo
$py = $null
foreach ($v in @("3.14","3.13","3.12","3.11")) { try { & py "-$v" -c "import tkinter" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "py -$v"; break } } catch {} }
if (-not $py) { try { & python -c "import tkinter" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "python" } } catch {} }  # GitHub Actions: setup-python puts it on PATH
if (-not $py) { throw "Need Python 3.11+ with tkinter (python.org installer, tick tcl/tk)" }
if (-not (Test-Path "labwatch\.venv-build")) { Invoke-Expression "$py -m venv labwatch\.venv-build" }
$vpy = "labwatch\.venv-build\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -e ".\labwatch[dev]"
$ver = & $vpy -c "import labwatch; print(labwatch.__version__)"
Write-Host "==> PyInstaller (labwatch $ver)"
& $vpy -m PyInstaller --noconfirm --clean --distpath dist\exe --workpath build\pyi deploy\labwatch.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$out = "dist\LabWatch-$ver-windows"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null
Copy-Item dist\exe\LabWatch.exe, dist\exe\labwatch.exe, docs\DEPLOY_WINDOWS.md, docs\NAMING_CONVENTION.md $out
Set-Content -Path "$out\README.txt" -Value @"
LabWatch $ver

1. Copy this folder to C:\Fragpipe_Auto  (or any folder WITHOUT spaces in its path).
2. Double-click LabWatch.exe  ->  the setup wizard opens. Follow the tabs left to right.
3. On the Run & Test tab press Save & Check, then Install startup task, then Start watcher.

labwatch.exe is the same program for the command line:  labwatch.exe check | status | dry-run <folder>
Full instructions: DEPLOY_WINDOWS.md
"@
Compress-Archive -Path "$out\*" -DestinationPath "$out.zip" -Force
Write-Host "==> $out.zip"
Get-ChildItem $out
