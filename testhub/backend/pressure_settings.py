"""Local, single-machine profile for validating the k6 adapter."""
import os
from pathlib import Path

from .settings import *  # noqa: F401,F403

PLATFORM_ROOT = Path(os.environ.get('PRESSURE_PLATFORM_ROOT', BASE_DIR.parent))
RUNTIME_ROOT = PLATFORM_ROOT / 'runtime'
PERF_PRIVATE_ROOT = str(RUNTIME_ROOT / 'private')
MEDIA_ROOT = str(RUNTIME_ROOT / 'media')
Path(PERF_PRIVATE_ROOT).mkdir(parents=True, exist_ok=True)
Path(MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
SECRET_KEY = (Path(PERF_PRIVATE_ROOT) / 'django-secret.txt').read_text().strip()
SIMPLE_JWT = {**SIMPLE_JWT, 'SIGNING_KEY': SECRET_KEY}
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + [
    'apps.users', 'apps.projects', 'apps.core', 'apps.api_testing',
    'apps.perf_testing.apps.PerfTestingConfig',
]
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': str(Path(PERF_PRIVATE_ROOT) / 'testhub.sqlite3'),
    'OPTIONS': {'timeout': 30},
}}
ROOT_URLCONF = 'backend.pressure_urls'
MIGRATION_MODULES = {
    app: f'backend.pressure_migrations.{app}'
    for app in ('users', 'projects', 'core', 'api_testing', 'perf_testing')
}
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
CELERY_TASK_ALWAYS_EAGER = True
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
PERF_MAX_CONCURRENT_EXECUTIONS = 1
PERF_MAX_CONCURRENCY = 1000
PERF_MAX_DURATION = 7200
PERF_CATALOG_ALLOWED_ORIGINS = [
    value.strip() for value in os.environ.get('PERF_CATALOG_ALLOWED_ORIGINS', '').split(',') if value.strip()
]
APP_USE_HTTPS = False
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
CORS_ALLOWED_ORIGINS = ['http://127.0.0.1:58101', 'http://localhost:58101']
MCP_ENABLED = False
ANALYTICS_ENABLED = False
SILENCED_SYSTEM_CHECKS = ['models.W046', 'fields.W163']
