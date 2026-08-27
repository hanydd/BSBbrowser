# SPDX-License-Identifier: AGPL-3.0-or-later
from os import environ
from SBtools.settings.common import * # noqa

SECRET_KEY = environ['SECRET_KEY']

DEBUG = False

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'sponsorblock',
        'USER': 'sponsorblock',
        'PASSWORD': environ['DB_PASSWORD'],
        'HOST': '',
    }
}

if environ.get('ANALYTICS_DB_NAME'):
    DATABASES['analytics'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': environ['ANALYTICS_DB_NAME'],
        'USER': environ.get('ANALYTICS_DB_USER', 'sponsorblock'),
        'PASSWORD': environ.get('ANALYTICS_DB_PASSWORD', environ['DB_PASSWORD']),
        'HOST': environ.get('ANALYTICS_DB_HOST', ''),
        'PORT': environ.get('ANALYTICS_DB_PORT', '5432'),
    }

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True

STATIC_ROOT = environ['STATIC_ROOT']

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'unix:///var/run/redis/redis-server.sock',
    }
}
