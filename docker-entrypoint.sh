#!/bin/sh
set -e
python manage.py migrate --noinput
exec gunicorn SBtools.wsgi:application \
  --bind "0.0.0.0:${GUNICORN_BIND_PORT:-8000}" \
  --workers "${GUNICORN_WORKERS:-3}" \
  --threads "${GUNICORN_THREADS:-1}" \
  --timeout "${GUNICORN_TIMEOUT:-120}"
