# Removes the Task Scheduler task and the venv. Leaves config.yaml, inbox, logs and all experiment data alone.
param([string]$Root = "C:\Fragpipe_Auto")
schtasks /Delete /TN "labwatch" /F 2>$null | Out-Null
if (Test-Path -LiteralPath "$Root\venv") { Remove-Item -LiteralPath "$Root\venv" -Recurse -Force }
Write-Host "labwatch task and venv removed. $Root\config.yaml, inbox and logs were kept."
