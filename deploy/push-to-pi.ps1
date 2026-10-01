# Copy MedOS to the Raspberry Pi and install it. Run from PowerShell in the MedOS folder:
#   powershell -ExecutionPolicy Bypass -File deploy\push-to-pi.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\push-to-pi.ps1 -PiHost 172.24.90.132 -User pi
# You type the Pi's password ONCE. The script then sets up an SSH key so the rest needs no password.

param(
    [string]$PiHost = "172.24.90.132",
    [string]$User = "pi",
    [switch]$NoInstall
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$target = "$User@$PiHost"
$sshOpts = @("-o", "StrictHostKeyChecking=accept-new")

Write-Host "1/4  Setting up passwordless login (type the Pi password when asked)..." -ForegroundColor Cyan
$key = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
New-Item -ItemType Directory -Force (Join-Path $env:USERPROFILE ".ssh") | Out-Null
if (-not (Test-Path $key)) { ssh-keygen -q -t ed25519 -N '""' -f $key | Out-Null }
$pub = Get-Content "$key.pub" -Raw
$pub = $pub.Trim()
ssh @sshOpts $target "mkdir -p ~/.ssh && chmod 700 ~/.ssh && grep -qxF '$pub' ~/.ssh/authorized_keys 2>/dev/null || echo '$pub' >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys"

Write-Host "2/4  Packing the project (without the laptop's database and 460 MB speech model)..." -ForegroundColor Cyan
$pkg = Join-Path $env:TEMP "medos-pi.tar.gz"
tar -czf $pkg -C $root `
    --exclude=".venv" --exclude="__pycache__" --exclude=".git" --exclude=".claude" --exclude=".pytest_cache" `
    --exclude="data/medos.db*" --exclude="data/.secret" --exclude="data/whisper" --exclude="data/certs" `
    --exclude="design-system" .
"{0:N1} MB" -f ((Get-Item $pkg).Length / 1MB)

Write-Host "3/4  Copying to the Pi..." -ForegroundColor Cyan
scp @sshOpts $pkg "${target}:/tmp/medos-pi.tar.gz"
ssh $target "mkdir -p ~/medos && tar -xzf /tmp/medos-pi.tar.gz -C ~/medos && rm /tmp/medos-pi.tar.gz && find ~/medos -name '*.sh' -exec sed -i 's/\r$//' {} + && chmod +x ~/medos/deploy/*.sh"

if ($NoInstall) { Write-Host "Copied to ~/medos on the Pi. Skipping install." -ForegroundColor Green; exit }

Write-Host "4/4  Installing on the Pi (5-15 minutes: Python packages, speech models)..." -ForegroundColor Cyan
ssh -t $target "cd ~/medos && sudo bash deploy/install.sh --display"

$laptopIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like ($PiHost -replace '\.\d+$', '.*') } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Done. Open  http://$PiHost  (or http://medos.local) from any device." -ForegroundColor Green
if ($laptopIp) { Write-Host "For the Qwen model on this laptop, set the AI address in Admin > AI assistant to  http://${laptopIp}:11434" -ForegroundColor Green }
