"""Disposable process for the actual Windows Python/Java lock interoperability test."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ciudades_del_mundo.tests.isolated_settings")

from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import SQLiteWriteLock, SQLiteWriteLockTimeout

try:
    with SQLiteWriteLock(Path(sys.argv[1]), timeout=1, poll_interval=0.05):
        print("locked", flush=True)
        if sys.argv[2] == "hold":
            sys.stdin.readline()
except SQLiteWriteLockTimeout:
    raise SystemExit(3)
