# SPDX-License-Identifier: AGPL-3.0-or-later
"""Django settings for Docker using PostgreSQL and Redis on the host machine."""
from os import environ

from SBtools.settings.common import *  # noqa

SECRET_KEY = environ['SECRET_KEY']

DEBUG = False

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': environ.get('POSTGRES_DB', 'sponsorblock'),
        'USER': environ.get('POSTGRES_USER', 'sponsorblock'),
        'PASSWORD': environ['DB_PASSWORD'],
        'HOST': environ.get('POSTGRES_HOST', 'host.docker.internal'),
        'PORT': environ.get('POSTGRES_PORT', '5432'),
    }
}

SESSION_COOKIE_SECURE = environ.get('SESSION_COOKIE_SECURE', 'true').lower() in ('1', 'true', 'yes')
CSRF_COOKIE_SECURE = environ.get('CSRF_COOKIE_SECURE', 'true').lower() in ('1', 'true', 'yes')
SECURE_SSL_REDIRECT = environ.get('SECURE_SSL_REDIRECT', 'true').lower() in ('1', 'true', 'yes')

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

STATIC_ROOT = environ['STATIC_ROOT']

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': environ.get('REDIS_URL', 'redis://host.docker.internal:6379/1'),
    }
}

MIDDLEWARE = [
    'django.middleware.cache.UpdateCacheMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.cache.FetchFromCacheMiddleware',
]

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}
