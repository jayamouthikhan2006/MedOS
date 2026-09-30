# Undo enable-ollama-lan.ps1: Ollama goes back to listening only on this laptop.
#   powershell -ExecutionPolicy Bypass -File laptop\disable-ollama-lan.ps1

[Environment]::SetEnvironmentVariable("OLLAMA_HOST", $null, "User")
$cmd = 'Remove-NetFirewallRule -DisplayName ''Ollama for MedOS'' -ErrorAction SilentlyContinue'
Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile", "-Command", $cmd
Get-Process | Where-Object { $_.ProcessName -like "ollama*" } | Stop-Process -Force -ErrorAction SilentlyContinue
$tray = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
if (Test-Path $tray) { Start-Process $tray }
Write-Host "Ollama is private to this laptop again."
