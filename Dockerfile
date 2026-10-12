FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Normalise Windows line endings on the shell scripts, make them executable,
# and pre-build the static files Django's admin interface needs.
RUN sed -i 's/\r$//' deploy/docker-entrypoint.sh \
 && chmod +x deploy/docker-entrypoint.sh \
 && python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
