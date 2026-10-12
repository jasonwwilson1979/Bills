#!/bin/sh
set -e

echo "==> BillFlow: applying migrations"
python manage.py migrate --noinput

echo "==> BillFlow: collecting static files"
python manage.py collectstatic --noinput

# Pre-generate the persistent secret key BEFORE gunicorn forks its workers,
# so every worker shares the same key (sessions/CSRF stay valid).
echo "==> BillFlow: ensuring persistent secret key"
python manage.py shell -c "from django.conf import settings; assert settings.SECRET_KEY, 'SECRET_KEY missing'"

# Give the BILLFLOW_ADMIN_EMAIL account Django-admin access (no-op when unset)
echo "==> BillFlow: ensuring admin access"
python manage.py ensure_admin

# In-container port (the container side of the host:container mapping).
# Default 8000; set BILLFLOW_CONTAINER_PORT in the environment to change it.
BIND_PORT="${BILLFLOW_CONTAINER_PORT:-8000}"
echo "==> BillFlow: starting gunicorn on 0.0.0.0:$BIND_PORT"
exec gunicorn billflow.wsgi:application \
    --bind "0.0.0.0:$BIND_PORT" \
    --workers 3 \
    --access-logfile - \
    --error-logfile -
