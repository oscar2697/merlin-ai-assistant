# Registers MERLIN autostart via Windows Task Scheduler:
#   1) "MERLIN LM Studio"  - at logon: starts the LM Studio server headless
#      and loads google/gemma-4-e4b so the assistant is warm asap.
#   2) "MERLIN Assistant"  - at logon (30s delay): launches the MERLIN GUI.
#
# Run from an ELEVATED PowerShell:
#   powershell -ExecutionPolicy Bypass -File setup_autostart.ps1 -InstallDir "C:\Program Files\MERLIN"

param(
    [string]$InstallDir = "$env:LOCALAPPDATA\MERLIN",
    [string]$Model = "google/gemma-4-e4b"
)

$ErrorActionPreference = "Stop"

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Please run this script from an elevated (Administrator) PowerShell."
    }
}

Assert-Elevated

$lms = Get-Command "lms" -ErrorAction SilentlyContinue
if ($lms) { $lmsPath = $lms.Source } else { $lmsPath = "$env:LOCALAPPDATA\Programs\LM Studio\lms.exe" }
if (-not (Test-Path $lmsPath)) {
    Write-Warning "LM Studio CLI (lms.exe) not found - LM Studio autostart task was NOT registered."
}

# --- Task 1: LM Studio headless load at logon --------------------------------
if (Test-Path $lmsPath) {
    $action1 = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" `
        -Argument "/c `"`"$lmsPath`" server start && `"$lmsPath`" load $Model`""
    Register-ScheduledTask -TaskName "MERLIN LM Studio" `
        -Action $action1 `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
        -Description "Starts LM Studio server and loads $Model for MERLIN." `
        -Force | Out-Null
    Write-Host "Registered task: MERLIN LM Studio (loads $Model at logon)."
}

# --- Task 2: MERLIN GUI at logon (30 s delay for the LLM to warm up) ---------
$merlinExe = Join-Path $InstallDir "Merlin.exe"
if (-not (Test-Path $merlinExe)) {
    Write-Warning "Merlin.exe not found at '$merlinExe'. Pass -InstallDir <path>."
}

$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT30S"
$action2 = New-ScheduledTaskAction -Execute $merlinExe
Register-ScheduledTask -TaskName "MERLIN Assistant" `
    -Action $action2 `
    -Trigger $trigger `
    -Description "Launches the MERLIN assistant GUI at logon." `
    -Force | Out-Null
Write-Host "Registered task: MERLIN Assistant (starts $merlinExe 30s after logon)."
