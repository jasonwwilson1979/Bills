<#
.SYNOPSIS
  Deploy BillFlow from this Windows machine to an Ubuntu server over SSH.

.DESCRIPTION
  Mode 'standalone': installs a Python venv + gunicorn (systemd service) and
  nginx on the server. Data lives in /opt/billflow/data, secrets in
  /opt/billflow/.env - both survive re-deploys.

  Mode 'docker': installs Docker and runs the app via docker compose with a
  persistent volume for the SQLite database.

  Both modes are idempotent: re-running deploys new code while preserving the
  database and .env. Requires ssh/scp/tar (built into Windows 10/11).

.EXAMPLE
  .\deploy\deploy-to-ubuntu.ps1 -Server root@203.0.113.10 -Mode standalone -Domain bills.example.com
.EXAMPLE
  .\deploy\deploy-to-ubuntu.ps1 -Server root@203.0.113.10 -Mode docker
.EXAMPLE
  .\deploy\deploy-to-ubuntu.ps1 -Server root@203.0.113.10 -Mode standalone -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,                    # user@host  (e.g. root@203.0.113.10)

    [ValidateSet('standalone', 'docker')]
    [string]$Mode = 'standalone',

    [string]$Domain = '',               # e.g. bills.example.com (recommended)
    [string]$ProjectDir = '',
    [string]$RemoteDir = '/opt/billflow',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# ---------- checks ----------
if (-not $ProjectDir) {
    # $PSScriptRoot is only available once the script body starts (not in
    # param defaults on Windows PowerShell 5.1).
    $ProjectDir = Split-Path -Parent $PSScriptRoot
    if (-not $ProjectDir) { $ProjectDir = (Get-Location).Path }
}

# ---------- checks ----------
if ($Server -notlike '*@*') { $Server = "root@$Server" }
if (-not (Test-Path (Join-Path $ProjectDir 'manage.py'))) {
    throw "manage.py not found under '$ProjectDir' - pass -ProjectDir if the script was moved."
}
foreach ($cmd in @('ssh', 'scp', 'tar')) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "Required command '$cmd' not found. Windows 10/11 include tar; enable the OpenSSH client for ssh/scp."
    }
}

$appHost = $Server.Split('@')[1]

function Invoke-Remote {
    param([string]$Description, [string]$ScriptText)
    Write-Host ""
    Write-Host "==> $Description" -ForegroundColor Cyan
    if ($DryRun) {
        Write-Host "[dry-run] ssh $Server '<remote script below>'" -ForegroundColor DarkGray
        Write-Host $ScriptText -ForegroundColor DarkGray
        return
    }
    # Send the script base64-encoded so quotes and line endings survive the trip.
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($ScriptText))
    ssh $Server "echo $b64 | base64 -d | bash -s"
    if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE): $Description" }
}

function Invoke-Scp {
    param([string]$Local, [string]$RemotePath, [string]$Description)
    Write-Host ""
    Write-Host "==> $Description" -ForegroundColor Cyan
    if ($DryRun) {
        Write-Host "[dry-run] scp $Local ${Server}:${RemotePath}" -ForegroundColor DarkGray
        return
    }
    scp $Local "${Server}:${RemotePath}"
    if ($LASTEXITCODE -ne 0) { throw "scp failed: $Description" }
}

# ---------- 1) package the project ----------
Write-Host ""
Write-Host "==> Packaging project ($ProjectDir)" -ForegroundColor Cyan
$tarball = Join-Path $env:TEMP ("billflow-deploy-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".tar.gz")
$tarArgs = @(
    '-czf', $tarball, '-C', $ProjectDir,
    '--exclude', 'db.sqlite3', '--exclude', '.env', '--exclude', '.git',
    '--exclude', 'staticfiles', '--exclude', '__pycache__', '--exclude', '*.pyc',
    '--exclude', 'secret_key'
)
if ($DryRun) {
    Write-Host "[dry-run] tar $($tarArgs -join ' ')" -ForegroundColor DarkGray
} else {
    & tar @tarArgs
    if ($LASTEXITCODE -ne 0) { throw 'tar failed while packaging the project.' }
    $sizeKb = [math]::Round((Get-Item $tarball).Length / 1KB)
    Write-Host "    Created $tarball ($sizeKb KB)"
}

# ---------- 2) upload ----------
Invoke-Scp -Local $tarball -RemotePath '/tmp/billflow-deploy.tar.gz' -Description 'Uploading project to server'

# ---------- 3) install / update on the server ----------
if ($Mode -eq 'standalone') {
    $remoteScript = @'
set -e
sudo mkdir -p __REMOTE_DIR__
sudo tar -xzf /tmp/billflow-deploy.tar.gz -C __REMOTE_DIR__
sudo find __REMOTE_DIR__ -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
sudo bash __REMOTE_DIR__/deploy/ubuntu-server-setup.sh --domain '__DOMAIN__'
rm -f /tmp/billflow-deploy.tar.gz
echo '--- BillFlow deploy complete ---'
'@
} else {
    $remoteScript = @'
set -e
if ! command -v docker >/dev/null 2>&1; then
    echo "==> Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi
sudo mkdir -p __REMOTE_DIR__
sudo tar -xzf /tmp/billflow-deploy.tar.gz -C __REMOTE_DIR__
sudo find __REMOTE_DIR__ -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
cd __REMOTE_DIR__
if [ ! -f .env ]; then
    SK=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null || openssl rand -hex 32)
    printf 'DJANGO_SECRET_KEY=%s\nDJANGO_DEBUG=False\nDJANGO_SECURE_PROXY_SSL_HEADER=1\nGOOGLE_CLIENT_ID=\nGOOGLE_CLIENT_SECRET=\n' "$SK" > .env
    chmod 600 .env
    echo "Created __REMOTE_DIR__/.env - EDIT IT to add GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET."
fi
sudo docker compose up -d --build
rm -f /tmp/billflow-deploy.tar.gz
sudo docker compose ps
echo '--- BillFlow deploy complete ---'
'@
}
$remoteScript = $remoteScript.Replace('__REMOTE_DIR__', $RemoteDir).Replace('__DOMAIN__', $Domain)

Invoke-Remote -Description "Installing and starting BillFlow ($Mode)" -ScriptText $remoteScript

# ---------- 4) tidy up ----------
if (-not $DryRun) { Remove-Item $tarball -ErrorAction SilentlyContinue }

# ---------- 5) summary ----------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " BillFlow deployed to $Server ($Mode mode)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
if ($Mode -eq 'standalone') {
    Write-Host " App:            http://$appHost/  (nginx -> port 8000)"
    Write-Host " Service:        ssh $Server 'systemctl status billflow'"
    Write-Host " Logs:           ssh $Server 'journalctl -u billflow -f'"
    Write-Host " Secrets:        ssh $Server 'nano $RemoteDir/.env'  (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)"
    Write-Host " Apply secrets:  ssh $Server 'sudo systemctl restart billflow'"
} else {
    Write-Host " App:            http://${appHost}:8000/"
    Write-Host " Secrets:        edit $RemoteDir/.env on the server (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET), then:"
    Write-Host "                 ssh $Server 'cd $RemoteDir && sudo docker compose up -d'"
    Write-Host " Logs:           ssh $Server 'cd $RemoteDir && sudo docker compose logs -f'"
    Write-Host " Optional HTTPS: set BILLFLOW_DOMAIN=$Domain in $RemoteDir/.env, then:"
    Write-Host "                 ssh $Server 'cd $RemoteDir && sudo docker compose --profile caddy up -d'"
}
if ($Domain) {
    Write-Host ""
    Write-Host " Register this redirect URI in Google Cloud Console (OAuth client):"
    Write-Host "   https://$Domain/accounts/google/login/callback/"
}
Write-Host ""
if ($DryRun) { Write-Host "This was a DRY RUN - nothing was changed." -ForegroundColor Yellow }
