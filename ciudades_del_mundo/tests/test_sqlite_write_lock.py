import tempfile
import unittest
from pathlib import Path

from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import SQLiteWriteLock


class SQLiteWriteLockTests(unittest.TestCase):
    def test_existing_unlocked_file_does_not_block_new_lock(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / ".web_sqlite_write.lock"
            path.write_text("pid=999999 host=old created=0\n", encoding="utf-8")

            with SQLiteWriteLock(path, timeout=0.2, poll_interval=0.01):
                pass
            payload = path.read_text(encoding="utf-8")

            self.assertIn("pid=", payload)
            self.assertNotIn("999999", payload)

    def test_released_lock_can_be_reacquired_even_when_file_remains(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / ".web_sqlite_write.lock"

            with SQLiteWriteLock(path, timeout=0.2, poll_interval=0.01):
                pass
            with SQLiteWriteLock(path, timeout=0.2, poll_interval=0.01):
                pass
            payload = path.read_text(encoding="utf-8")

            self.assertIn("pid=", payload)
