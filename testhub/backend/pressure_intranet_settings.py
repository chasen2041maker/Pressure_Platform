"""Linux Docker single-instance intranet profile; runtime stays on the host path."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

if not os.environ.get('PRESSURE_PLATFORM_ROOT'):
    raise ImproperlyConfigured('PRESSURE_PLATFORM_ROOT is required')
_root = Path(os.environ['PRESSURE_PLATFORM_ROOT'])
if not _root.is_absolute() or len(_root.parts) < 3 or _root.resolve() != _root:
    raise ImproperlyConfigured('PRESSURE_PLATFORM_ROOT must be a dedicated absolute path without symlinks')

from .pressure_settings import *  # noqa: E402,F401,F403

DEBUG = False
ALLOWED_HOSTS = [value.strip() for value in os.environ.get('PRESSURE_ALLOWED_HOSTS', '').split(',') if value.strip()]
if not ALLOWED_HOSTS or any(value == '*' or '/' in value or value.startswith('.') for value in ALLOWED_HOSTS):
    raise ImproperlyConfigured('Set explicit PRESSURE_ALLOWED_HOSTS domain names/IPs (no scheme, wildcard or path)')
for _host in ('localhost', '127.0.0.1'):
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)

_origin = os.environ.get('PRESSURE_PUBLIC_ORIGIN', '').rstrip('/')
_url = urlsplit(_origin)
if (_url.scheme not in ('http', 'https') or not _url.hostname or _url.path or _url.query
        or _url.fragment or _url.username or _url.password or _url.hostname not in ALLOWED_HOSTS):
    raise ImproperlyConfigured('PRESSURE_PUBLIC_ORIGIN must match an explicit allowed HTTP(S) host')
APP_USE_HTTPS = _url.scheme == 'https'
SECURE_SSL_REDIRECT = False  # Optional upstream TLS terminator owns redirects.
SECURE_PROXY_SSL_HEADER = None
SESSION_COOKIE_SECURE = APP_USE_HTTPS
CSRF_COOKIE_SECURE = APP_USE_HTTPS
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = [_origin]
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = []  # Frontend and API have the same origin.
CORS_ALLOW_CREDENTIALS = False
MIDDLEWARE = [entry for entry in MIDDLEWARE if entry != 'backend.middleware.DisableCSRFMiddleware']
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
PERF_MAX_CONCURRENT_EXECUTIONS = 1
LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
}
STATIC_ROOT = str(RUNTIME_ROOT / 'static')
