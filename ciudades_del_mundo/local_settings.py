"""Local Django UI, with Java routes enabled by the combined supervisor."""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from ciudades_del_mundo.settings import *  # noqa: F403

DEBUG = False
DATABASE_PATH = Path(os.environ.get('BEOGRAD_DATABASE', ''))
if not DATABASE_PATH.is_absolute() or not DATABASE_PATH.is_file():
    raise ImproperlyConfigured('BEOGRAD_DATABASE must be an absolute existing SQLite file')
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(DATABASE_PATH), 'OPTIONS': {'timeout': 30}}}
ROOT_URLCONF = 'ciudades_del_mundo.local_urls'
MEDIA_ROOT = Path(os.environ.get('BEOGRAD_MEDIA_ROOT') or BASE_DIR / 'media')  # noqa: F405
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
BEOGRAD_JAVA_ENABLED = os.environ.get('BEOGRAD_JAVA_ENABLED') == '1'
BEOGRAD_JAVA_PORT = int(os.environ.get('BEOGRAD_JAVA_PORT', '8001'))
if BEOGRAD_JAVA_ENABLED:
    MIDDLEWARE = [*MIDDLEWARE, 'ciudades_del_mundo.web.java_routing.JavaApiRoutingMiddleware']  # noqa: F405
