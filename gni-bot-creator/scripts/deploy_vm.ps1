# Deploy to VM — minimal (settings + alembic) or full with whatsapp-bot
# Usage: .\scripts\deploy_vm.ps1              # minimal
#        .\scripts\deploy_vm.ps1 -Full        # full rsync + whatsapp (excludes node_modules)
# Requires: OpenSSH (ssh, scp), optional: rsync (WSL/Git Bash) for fast full deploy

param([switch]$Full)

$VM_USER = if ($env:VM_USER) { $env:VM_USER } else { "root" }
$VM_HOST = if ($env:VM_HOST) { $env:VM_HOST } else { "217.216.84.81" }
$VM_PATH = if ($env:VM_PATH) { $env:VM_PATH } else { "/opt/gni-bot-creator" }
$Key = $env:SSH_KEY
if (-not $Key) {
    $ed25519 = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
    $rsa = Join-Path $env:USERPROFILE ".ssh\id_rsa"
    if (Test-Path $ed25519) { $Key = $ed25519 }
    elseif (Test-Path $rsa) { $Key = $rsa }
    else {
        $id25519 = Join-Path $env:USERPROFILE ".ssh\id_25519"
        if (Test-Path $id25519) { $Key = $id25519 }
    }
}
$SSH_OPTS = if ($Key) { @("-i", $Key) } else { @() }

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$GNIRoot = Split-Path -Parent $RepoRoot  # GNI (parent of gni-bot-creator)

Write-Host "=== Deploy to VM ==="
Write-Host "  VM: ${VM_USER}@${VM_HOST}"
if ($Key) { Write-Host "  Key: $Key" }
Write-Host ""

if ($Full) {
    # Full deploy: sync docker-compose, API wa_bridge (POST /admin/wa/reconnect), whatsapp-bot
    Write-Host "Syncing docker-compose.yml..."
    scp @SSH_OPTS (Join-Path $GNIRoot "docker-compose.yml") "${VM_USER}@${VM_HOST}:${VM_PATH}/"

    # Sync scripts (verify_wa_flow.sh, add_telegram_sources.py, etc.)
    $scriptsDir = Join-Path $GNIRoot "scripts"
    if (Test-Path $scriptsDir) {
        Write-Host "Syncing scripts..."
        ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "mkdir -p ${VM_PATH}/scripts"
        Get-ChildItem $scriptsDir -File | Where-Object { $_.Extension -in ".sh",".py" } | ForEach-Object {
            scp @SSH_OPTS $_.FullName "${VM_USER}@${VM_HOST}:${VM_PATH}/scripts/"
        }
        ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "chmod +x ${VM_PATH}/scripts/*.sh 2>/dev/null || true"
    }

    # Sync collector (telegram ingest, add_telegram_sources dependency)
    $collectorDir = Join-Path $GNIRoot "apps\collector"
    if (Test-Path $collectorDir) {
        Write-Host "Syncing apps/collector..."
        ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "mkdir -p ${VM_PATH}/apps/collector"
        Get-ChildItem $collectorDir -File | ForEach-Object {
            scp @SSH_OPTS $_.FullName "${VM_USER}@${VM_HOST}:${VM_PATH}/apps/collector/"
        }
    }

    # Sync data/sources.yaml
    $sourcesYaml = Join-Path $GNIRoot "data\sources.yaml"
    if (Test-Path $sourcesYaml) {
        Write-Host "Syncing data/sources.yaml..."
        ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "mkdir -p ${VM_PATH}/data"
        scp @SSH_OPTS $sourcesYaml "${VM_USER}@${VM_HOST}:${VM_PATH}/data/"
    }

    # Sync API: wa_bridge, wa_public, monitoring, wa_qr_cache, wa_keepalive, main
    $apiFiles = @(
        "apps\api\routes\wa_bridge.py",
        "apps\api\routes\wa_public.py",
        "apps\api\routes\monitoring.py",
        "apps\api\wa_qr_cache.py",
        "apps\api\wa_keepalive.py",
        "apps\api\main.py"
    )
    foreach ($rel in $apiFiles) {
        $p = Join-Path $GNIRoot $rel
        if (Test-Path $p) {
            Write-Host "Syncing API $rel..."
            $remoteDir = ($rel -replace '\\', '/') -replace '/[^/]+$', ''
            ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "mkdir -p ${VM_PATH}/${remoteDir}"
            scp @SSH_OPTS $p "${VM_USER}@${VM_HOST}:${VM_PATH}/$($rel -replace '\\', '/')"
        }
    }

    # Sync whatsapp-bot (excl. node_modules, dist) — rsync or robocopy+scp fallback
    $waBot = Join-Path $GNIRoot "apps\whatsapp-bot"
    if (Test-Path $waBot) {
        Write-Host "Syncing whatsapp-bot (excl. node_modules, dist)..."
        $src = ($waBot -replace '\\', '/') + "/"
        $dest = "${VM_USER}@${VM_HOST}:${VM_PATH}/apps/whatsapp-bot/"
        $rsyncOk = $false
        try { $null = Get-Command rsync -ErrorAction Stop; & rsync -avz --exclude=node_modules --exclude=dist --exclude=.git $src $dest 2>$null; $rsyncOk = ($LASTEXITCODE -eq 0) } catch {}
        if (-not $rsyncOk) {
            # Fallback: robocopy to temp, scp to VM (works without rsync)
            $tmp = Join-Path $env:TEMP "gni-wabot-$(Get-Random)"
            New-Item -ItemType Directory -Force -Path $tmp | Out-Null
            robocopy $waBot $tmp /E /XD node_modules dist .git /NFL /NDL /NJH /NJS | Out-Null
            ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "mkdir -p ${VM_PATH}/apps/whatsapp-bot"
            Get-ChildItem $tmp -Force | ForEach-Object {
                $name = $_.Name
                if ($name -eq "." -or $name -eq "..") { return }
                scp @SSH_OPTS -r $_.FullName "${VM_USER}@${VM_HOST}:${VM_PATH}/apps/whatsapp-bot/"
            }
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
            Write-Host "  (used robocopy+scp fallback)" -ForegroundColor Gray
        }
    } else {
        Write-Host "Warning: whatsapp-bot not found at $waBot" -ForegroundColor Yellow
    }

    # Sync wa-qr-cloud-ui (Streamlit) from GNI so codex UI changes (429/polling) are deployed
    $waQrUi = Join-Path $GNIRoot "apps\wa-qr-cloud-ui"
    if (Test-Path $waQrUi) {
        Write-Host "Syncing wa-qr-cloud-ui..."
        $src = ($waQrUi -replace '\\', '/') + "/"
        $dest = "${VM_USER}@${VM_HOST}:${VM_PATH}/apps/wa-qr-cloud-ui/"
        $rsyncOk = $false
        try { $null = Get-Command rsync -ErrorAction Stop; & rsync -avz --exclude=.git $src $dest 2>$null; $rsyncOk = ($LASTEXITCODE -eq 0) } catch {}
        if (-not $rsyncOk) {
            $tmp = Join-Path $env:TEMP "gni-waqr-ui-$(Get-Random)"
            New-Item -ItemType Directory -Force -Path $tmp | Out-Null
            robocopy $waQrUi $tmp /E /XD .git /NFL /NDL /NJH /NJS | Out-Null
            ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "mkdir -p ${VM_PATH}/apps/wa-qr-cloud-ui"
            Get-ChildItem $tmp -Force | ForEach-Object {
                $name = $_.Name
                if ($name -eq "." -or $name -eq "..") { return }
                scp @SSH_OPTS -r $_.FullName "${VM_USER}@${VM_HOST}:${VM_PATH}/apps/wa-qr-cloud-ui/"
            }
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
            Write-Host "  (used robocopy+scp fallback)" -ForegroundColor Gray
        }
    } else {
        Write-Host "Warning: wa-qr-cloud-ui not found at $waQrUi" -ForegroundColor Yellow
    }

    Write-Host "Rebuilding all services..."
    ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "cd $VM_PATH; docker compose build; docker compose --profile whatsapp up -d"
} else {
    # Minimal: only settings + alembic
    scp @SSH_OPTS (Join-Path $RepoRoot "apps\api\settings.py") "${VM_USER}@${VM_HOST}:${VM_PATH}/apps/api/settings.py"
    if (Test-Path (Join-Path $RepoRoot "alembic\env.py")) {
        scp @SSH_OPTS (Join-Path $RepoRoot "alembic\env.py") "${VM_USER}@${VM_HOST}:${VM_PATH}/alembic/env.py"
    }
    ssh @SSH_OPTS "${VM_USER}@${VM_HOST}" "cd $VM_PATH; docker compose build worker api; docker compose up -d worker api"
}

Write-Host "Done. API: http://${VM_HOST}:8000/health"
