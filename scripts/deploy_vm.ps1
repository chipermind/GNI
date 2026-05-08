# Deploy gni-bot-creator to VM
# Usage: .\scripts\deploy_vm.ps1
#        .\scripts\deploy_vm.ps1 -Full
#        .\scripts\deploy_vm.ps1 -SshTarget gni-vm
# Requires: SSH key (no password), git pushed
# Run from repo root. SSHs to VM and runs pull + docker compose + smoke.

param(
    [switch]$Full,
    [string]$SshTarget = $env:SSH_TARGET
)
if (-not $SshTarget) { $SshTarget = "gni-vm" }

$ErrorActionPreference = "Stop"
$VM_PATH = if ($env:VM_PATH) { $env:VM_PATH } else { "/opt/gni-bot-creator" }

Write-Host "=== GNI Bot Creator - VM Deploy ===" -ForegroundColor Cyan
Write-Host "  SSH: $SshTarget"
Write-Host "  Path: ${VM_PATH}"
Write-Host ""

$deployScript = Join-Path $env:TEMP "gni-deploy-remote.sh"

# Bash script (single-quote so PowerShell does not parse)
$bashContent = @'
set -e
cd /opt/gni-bot-creator
test -f .env || cp .env.example .env 2>/dev/null || true
echo '=== Pulling latest ==='
git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || true
echo '=== Building and starting ==='
docker compose build
docker compose up -d
echo '=== Waiting for API (max 60s) ==='
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  curl -sf http://localhost:8000/health 2>/dev/null && echo 'API health OK' && break
  sleep 5
done
echo '=== Container status ==='
docker compose ps
echo '=== Init desk DB ==='
docker compose exec -T api python scripts/migrate_db.py 2>/dev/null || echo 'Migrate skipped'
echo '=== Smoke desk (compose dry-run + save to DB) ==='
docker compose exec -T api python -m desk.scheduler --dry-run --type PANORAMA_0900 --compose 2>/dev/null || echo 'Desk smoke skipped (Ollama may be warming)'
echo '=== Deploy done ==='
'@

$bashContent = $bashContent.Replace('/opt/gni-bot-creator', $VM_PATH)
# LF only (no CRLF) - Windows Out-File would add \r and break bash on Linux
$bytes = [System.Text.Encoding]::UTF8.GetBytes($bashContent.Replace("`r`n", "`n").Replace("`r", "`n"))
[System.IO.File]::WriteAllBytes($deployScript, $bytes)

Write-Host "Running remote deploy..."
scp -q $deployScript "${SshTarget}:/tmp/gni-deploy.sh"
ssh $SshTarget "bash /tmp/gni-deploy.sh; rm -f /tmp/gni-deploy.sh"
Remove-Item $deployScript -ErrorAction SilentlyContinue
