param(
    [Parameter(Mandatory = $true)]
    [string]$OllamaPath
)

# Ollama's tray app may already have started its API at logon. Reuse it rather
# than launching a second server process.
for ($attempt = 0; $attempt -lt 5; $attempt++) {
    try {
        Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 |
            Out-Null
        exit 0
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (Test-Path $OllamaPath) {
    Start-Process -FilePath $OllamaPath -ArgumentList "serve" -WindowStyle Hidden
} else {
    Write-Error "Ollama executable not found: $OllamaPath"
    exit 1
}
