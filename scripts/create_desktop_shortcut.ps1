# Creates (or refreshes) a Desktop shortcut to the AI Ops Platform launcher.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1
#
# Idempotent: re-running overwrites the same .lnk with current paths, so it
# is safe after moving the repo. The shortcut points at scripts\launch_aiops.cmd,
# which starts the API in mock mode on port 8200 and opens the dashboard.
param(
    [string]$Name = "AI Ops Platform"
)

$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $PSScriptRoot
$target = Join-Path $PSScriptRoot "launch_aiops.cmd"
if (-not (Test-Path $target)) {
    throw "launcher not found next to this script: $target"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "$Name.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $target
$lnk.WorkingDirectory = $root
$lnk.Description = "Start the AI Operations & Workflow Automation Platform (mock mode) and open the operator dashboard"

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($python) { $lnk.IconLocation = "$python,0" }

$lnk.Save()
Write-Host "Shortcut ready: $lnkPath"
Write-Host "  target: $target"
Write-Host "  cwd:    $root"
