# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: AGPL-3.0-or-later
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    DJANGO_SETTINGS_MODULE=SBtools.settings.docker

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --retries=10 -r requirements.txt

COPY manage.py ./
COPY docker-entrypoint.sh ./
COPY browser ./browser
COPY SBtools ./SBtools
RUN chmod +x docker-entrypoint.sh \
    && sed -i 's/\r$//' docker-entrypoint.sh

# Bake static files into the image (placeholders only — not used at runtime)
RUN SECRET_KEY=docker-build-collectstatic-only \
    DB_PASSWORD=docker-build-placeholder \
    STATIC_ROOT=/app/staticfiles \
    python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
