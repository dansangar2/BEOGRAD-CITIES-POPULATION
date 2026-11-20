"""Test/check settings that cannot open the user's local SQLite database."""
import os
os.environ.setdefault('BEOGRAD_DJANGO_SECRET_KEY', 'isolated-test-key-with-no-runtime-use')
from ciudades_del_mundo.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
        "OPTIONS": {"timeout": 30},
    }
}

TEST_RUNNER = "ciudades_del_mundo.tests.offline_runner.OfflineDiscoverRunner"
