<#
One-shot dev/test setup on Windows: venv, install, lint, full test suite, then a testbed.
    powershell -ExecutionPolicy Bypass -File scripts\test_windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\test_windows.ps1 -Bed    # also build a testbed
Needs Python 3.11+ installed with the py launcher (python.org installer, tick tcl/tk).
#>
param([switch]$Bed)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\ionomos")
$py = $null
foreach ($v in @("3.14","3.13","3.12","3.11")) { try { & py "-$v" -c "1" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "py -$v"; break } } catch {} }
if (-not $py) { throw "Python 3.11+ not found via py launcher" }
if (-not (Test-Path .venv)) { Invoke-Expression "$py -m venv .venv" }
.\.venv\Scripts\pip install -q -e ".[dev]"
Write-Host "==> ruff";   .\.venv\Scripts\ruff check src tests
Write-Host "==> pytest"; .\.venv\Scripts\pytest -q -rs
if ($Bed) {
    Write-Host "==> testbed"
    # default = C:\ionomos-testbed (no spaces; the repo may live under C:\Users\<First Last>\)
    .\.venv\Scripts\ionomos testbed init
    Write-Host "activate with:  .\.venv\Scripts\Activate.ps1"
}
