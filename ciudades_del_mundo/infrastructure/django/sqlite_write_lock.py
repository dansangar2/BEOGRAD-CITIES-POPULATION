from __future__ import annotations

from contextlib import suppress
import os
from pathlib import Path
import socket
import threading
import time

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, close_old_connections, connections


DEFAULT_SQLITE_WRITE_LOCK_TIMEOUT = 20 * 60
DEFAULT_SQLITE_WRITE_LOCK_STALE_AFTER = 6 * 60 * 60
SQLITE_WRITE_LOCK_FILENAME = ".web_sqlite_write.lock"

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - Windows is the normal local development target.
    import fcntl


class SQLiteWriteLockTimeout(TimeoutError):
    """Raised when another scraping process keeps the SQLite write lock too long."""


class SQLiteWriteLock:
    """Small cross-process lock for long SQLite write transactions.

    SQLite already serializes writes internally, but separate web task processes
    can otherwise fail with ``database is locked`` while one scraper commits a
    large country. The path is stable, but the actual ownership is an operating
    system byte-range/flock lock, so an old file left by a killed process never
    blocks later scrapes.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        timeout: float = DEFAULT_SQLITE_WRITE_LOCK_TIMEOUT,
        poll_interval: float = 0.25,
        stale_after: float = DEFAULT_SQLITE_WRITE_LOCK_STALE_AFTER,
    ) -> None:
        self.path = Path(path or Path(settings.BASE_DIR) / SQLITE_WRITE_LOCK_FILENAME)
        self.timeout = max(1.0, float(timeout))
        self.poll_interval = max(0.05, float(poll_interval))
        self.stale_after = max(0.0, float(stale_after))
        self._thread_lock = threading.Lock()
        self._fd: int | None = None

    def __enter__(self):
        self._thread_lock.acquire()
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            while True:
                fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR)
                self._ensure_lock_byte(fd)
                if not self._try_lock_fd(fd):
                    os.close(fd)
                    if time.monotonic() >= deadline:
                        raise SQLiteWriteLockTimeout(
                            f"No se pudo adquirir el bloqueo SQLite en {self.timeout:.0f}s: {self.path}"
                        )
                    time.sleep(self.poll_interval)
                    continue

                self._fd = fd
                os.ftruncate(fd, 0)
                os.write(fd, self._payload().encode("utf-8", errors="replace"))
                close_old_connections()
                return self
        except Exception:
            if self._fd is not None:
                try:
                    self._unlock_fd(self._fd)
                    os.close(self._fd)
                finally:
                    self._fd = None
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._fd is not None:
                try:
                    self._unlock_fd(self._fd)
                    os.close(self._fd)
                finally:
                    self._fd = None
        finally:
            close_old_connections()
            self._thread_lock.release()

    def _payload(self) -> str:
        return f"pid={os.getpid()} host={socket.gethostname()} created={time.time():.3f}\n"

    def _ensure_lock_byte(self, fd: int) -> None:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)

    def _try_lock_fd(self, fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            with suppress(OSError):
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            return False
        with suppress(BlockingIOError, OSError):  # pragma: no cover - see branch above.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        return False

    def _unlock_fd(self, fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            with suppress(OSError):
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        with suppress(OSError):  # pragma: no cover - see branch above.
            fcntl.flock(fd, fcntl.LOCK_UN)


def sqlite_write_lock_if_needed(alias: str = DEFAULT_DB_ALIAS) -> SQLiteWriteLock | None:
    """Return a cross-process write lock only when the active DB is SQLite."""

    try:
        if connections[alias].vendor != "sqlite":
            return None
    except Exception:  # noqa: BLE001 - fallback to no lock if Django is not ready.
        return None
    return SQLiteWriteLock()
