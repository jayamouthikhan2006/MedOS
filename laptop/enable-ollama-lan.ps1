# Let the Raspberry Pi use the Qwen 3 model running in Ollama on this Windows laptop.
#
# Run once in PowerShell from the MedOS folder:
#   powershell -ExecutionPolicy Bypass -File laptop\enable-ollama-lan.ps1
#
# What it does:
#   1. Tells Ollama to listen on the network (OLLAMA_HOST=0.0.0.0:11434), not only on this laptop.
#   2. Adds a Windows Firewall rule for port 11434 (asks for administrator permission once).
#   3. Restarts Ollama and makes sure qwen3:4b is downloaded.
# Undo: laptop\disable-ollama-lan.ps1

$ErrorActionPreference = "Stop"
$model = "qwen3:4b"

Write-Host "1/3  Letting Ollama accept connections from the network..." -ForegroundColor Cyan
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
$env:OLLAMA_HOST = "0.0.0.0:11434"

Write-Host "2/3  Allowing port 11434 through Windows Firewall..." -ForegroundColor Cyan
$existing = Get-NetFirewallRule -DisplayName "Ollama for MedOS" -ErrorAction SilentlyContinue
if (-not $existing) {
    # The Pi's hotspot usually shows up as a Public network in Windows, so allow Private and Public.
    $cmd = 'New-NetFirewallRule -DisplayName ''Ollama for MedOS'' -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Private,Public'
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile", "-Command", $cmd
} else {
    Write-Host "     rule already exists"
}

Write-Host "3/3  Restarting Ollama..." -ForegroundColor Cyan
Get-Process | Where-Object { $_.ProcessName -like "ollama*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$dir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$tray = Join-Path $dir "ollama app.exe"
$cli = Join-Path $dir "ollama.exe"
if (Test-Path $tray) { Start-Process $tray }
elseif (Test-Path $cli) { Start-Process $cli -ArgumentList "serve" -WindowStyle Hidden }
else { Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden }
Start-Sleep -Seconds 4

$ollama = if (Test-Path $cli) { $cli } else { "ollama" }
& $ollama pull $model

Write-Host ""
Write-Host "Done. Ollama is reachable from the Pi at:" -ForegroundColor Green
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    ForEach-Object { Write-Host ("   http://{0}:11434   ({1})" -f $_.IPAddress, $_.InterfaceAlias) }
Write-Host ""
Write-Host "In MedOS open Admin > AI assistant and press 'Find on network', or paste one of the addresses above."
