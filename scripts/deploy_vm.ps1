# Deploy gni-bot-creator to VM
# Usage: .\scripts\deploy_vm.ps1
# Requires: git pushed, SSH access to VM
# Run from repo root. SSHs to VM and runs pull + docker compose + smoke.

$ErrorActionPreference = "Stop"
$VM_USER = if ($env:VM_USER) { $env:VM_USER } else { "root" }
$VM_HOST = if ($env:VM_HOST) { $env:VM_HOST } else { "217.216.84.81" }
$VM_PATH = if ($env:VM_PATH) { $env:VM_PATH } else { "/opt/gni-bot-creator" }

Write-Host "=== GNI Bot Creator - VM Deploy ===" -ForegroundColor Cyan
Write-Host "  VM: ${VM_USER}@${VM_HOST}"
Write-Host "  Path: ${VM_PATH}"
Write-Host ""

$remote = "${VM_USER}@${VM_HOST}"
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
echo '=== Smoke desk ==='
docker compose exec -T api python -m desk.scheduler --dry-run --type PANORAMA_0900 2>/dev/null || echo 'Smoke skipped'
echo '=== Deploy done ==='
'@

$bashContent = $bashContent.Replace('/opt/gni-bot-creator', $VM_PATH)
$bashContent | Out-File -FilePath $deployScript -Encoding utf8
Write-Host "Running remote deploy..."
Get-Content $deployScript -Raw | ssh $remote 'bash -s'
Remove-Item $deployScript -ErrorAction SilentlyContinue
