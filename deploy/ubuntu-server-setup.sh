#!/usr/bin/env bash
# ============================================================
# BillFlow - standalone Ubuntu server setup (idempotent)
#
# Usage (on the server, from the project directory):
#   sudo bash deploy/ubuntu-server-setup.sh --domain bills.example.com
#   sudo bash deploy/ubuntu-server-setup.sh --with-certbot --domain bills.example.com
#
# Sets up: system packages, a 'billflow' system user, a python venv,
# gunicorn (systemd) + nginx reverse proxy (+ optional HTTPS via certbot).
#
# Preserved across deploys (never overwritten):
#   .env   <- secrets / configuration
#   data/  <- SQLite database
#   venv/  <- python virtualenv
# Re-running this script after a code deploy is safe.
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
DATA_DIR="$APP_DIR/data"
VENV_DIR="$APP_DIR/venv"
SERVICE="billflow"
APP_USER="billflow"
BIND_PORT="8000"

DOMAIN=""
EMAIL=""
WITH_CERTBOT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --domain) DOMAIN="${2:-}"; shift 2;;
        --email)  EMAIL="${2:-}"; shift 2;;
        --with-certbot) WITH_CERTBOT=1; shift;;
        *) echo "Unknown option: $1" >&2; exit 1;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo (needs root to install packages and services)." >&2
    exit 1
fi

# Repair Windows CRLF line endings on any shell script (except this one,
# which bash is currently reading).
find "$APP_DIR" -name '*.sh' ! -name "$(basename "${BASH_SOURCE[0]}")" -exec sed -i 's/\r$//' {} +

echo "==> Installing system packages (python3-venv, nginx)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx >/dev/null
if [ "$WITH_CERTBOT" = "1" ]; then
    apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
fi

echo "==> Creating service user + directories"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$DATA_DIR"

echo "==> Setting up python virtualenv"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Writing environment file (first run only)"
if [ ! -f "$ENV_FILE" ]; then
    SECRET_KEY="$("$VENV_DIR/bin/python" -c 'import secrets; print(secrets.token_urlsafe(50))')"
    if [ -n "$DOMAIN" ]; then
        ALLOWED="$DOMAIN,localhost,127.0.0.1"
        ORIGINS="https://$DOMAIN"
    else
        ALLOWED="*"
        ORIGINS=""
        echo "    NOTE: no --domain given -> DJANGO_ALLOWED_HOSTS='*' (fine behind a proxy / for IP access)."
    fi
    cat > "$ENV_FILE" <<ENVFILE
DJANGO_SECRET_KEY=$SECRET_KEY
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=$ALLOWED
DJANGO_CSRF_TRUSTED_ORIGINS=$ORIGINS
DJANGO_DB_PATH=$DATA_DIR/db.sqlite3
DJANGO_SECURE_PROXY_SSL_HEADER=1
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
ENVFILE
    chmod 600 "$ENV_FILE"
    echo "    Created $ENV_FILE - EDIT IT to add GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET."
fi

echo "==> Running migrations + collecting static files"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
sudo -u "$APP_USER" env DJANGO_DB_PATH="$DATA_DIR/db.sqlite3" DJANGO_DEBUG=False \
    "$VENV_DIR/bin/python" "$APP_DIR/manage.py" migrate --noinput
sudo -u "$APP_USER" env DJANGO_DB_PATH="$DATA_DIR/db.sqlite3" DJANGO_DEBUG=False \
    "$VENV_DIR/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

echo "==> Installing systemd service"
cat > "/etc/systemd/system/$SERVICE.service" <<UNITFILE
[Unit]
Description=BillFlow (Django via gunicorn)
After=network.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/gunicorn billflow.wsgi:application --bind 127.0.0.1:$BIND_PORT --workers 3 --access-logfile - --error-logfile -
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITFILE
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

echo "==> Installing nginx reverse proxy"
if [ -n "$DOMAIN" ]; then SERVER_NAME="$DOMAIN"; else SERVER_NAME="_"; fi
cat > /etc/nginx/sites-available/billflow <<NGINXFILE
server {
    listen 80;
    server_name $SERVER_NAME;
    client_max_body_size 10m;

    location /static/ {
        alias $APP_DIR/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:$BIND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXFILE
# Avoid clashing with the stock "Welcome to nginx" default site.
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/billflow /etc/nginx/sites-enabled/billflow
nginx -t
systemctl reload nginx

if [ "$WITH_CERTBOT" = "1" ]; then
    if [ -z "$DOMAIN" ]; then
        echo "==> Skipping certbot (no --domain given)"
    else
        echo "==> Requesting HTTPS certificate for $DOMAIN"
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
            ${EMAIL:+-m "$EMAIL"} --redirect \
            || echo "    certbot did not complete - check that DNS points at this server, then run: sudo certbot --nginx -d $DOMAIN"
    fi
fi

if [ "$WITH_CERTBOT" = "1" ] && [ -n "$DOMAIN" ]; then
    PUBLIC_URL="https://$DOMAIN"
else
    PUBLIC_URL="http://$SERVER_NAME"
fi
echo ""
echo "============================================================"
echo " BillFlow is deployed"
echo "   App:       $PUBLIC_URL"
echo "   Service:   systemctl status $SERVICE"
echo "   Logs:      journalctl -u $SERVICE -f"
if [ -n "$DOMAIN" ]; then
    echo "   Google OAuth callback to register in Google Cloud Console:"
    echo "     https://$DOMAIN/accounts/google/login/callback/"
fi
echo "   Secrets:   nano $ENV_FILE  (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)"
echo "   After editing secrets:  systemctl restart $SERVICE"
echo "============================================================"
