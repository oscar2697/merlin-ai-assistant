# Register local Ollama and MERLIN with Windows Task Scheduler.
# Run from an elevated PowerShell (the Inno Setup installer does this for you).

param(
    [string]$InstallDir = "$env:LOCALAPPDATA\MERLIN"
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

$ollama = Get-Command "ollama" -ErrorAction SilentlyContinue
if ($ollama) {
    $ollamaPath = $ollama.Source
} else {
    $ollamaPath = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
}

if (Test-Path $ollamaPath) {
    # Reuse the tray app's local API if it has already started; otherwise start
    # a hidden `ollama serve` process. Models load lazily on MERLIN's first call.
    $startupScript = Join-Path $PSScriptRoot "start_ollama.ps1"
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startupScript`" -OllamaPath `"$ollamaPath`""
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
    Register-ScheduledTask -TaskName "MERLIN Ollama" `
        -Action $action `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
        -Description "Starts the local Ollama API used by MERLIN." `
        -Force | Out-Null
    Write-Host "Registered task: MERLIN Ollama (local API at logon)."
} else {
    Write-Warning "Ollama was not found. Install/configure Ollama to start at logon separately."
}

$merlinExe = Join-Path $InstallDir "Merlin.exe"
if (-not (Test-Path $merlinExe)) {
    Write-Warning "Merlin.exe not found at '$InstallDir'. Pass -InstallDir <path>."
    exit 1
}

$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT30S"
$guiAction = New-ScheduledTaskAction -Execute $merlinExe
Register-ScheduledTask -TaskName "MERLIN Assistant" `
    -Action $guiAction `
    -Trigger $trigger `
    -Description "Launches the MERLIN assistant GUI at logon." `
    -Force | Out-Null
Write-Host "Registered task: MERLIN Assistant (starts 30 seconds after logon)."
