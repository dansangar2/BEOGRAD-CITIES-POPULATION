"""Backend choice and real Python startup against disposable synthetic SQL."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener

from tools import java_local

ROOT = Path(__file__).resolve().parents[1]


class BackendSelectionTests(unittest.TestCase):
    def test_defaults_file_override_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(java_local, 'ROOT', Path(directory)), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(java_local.selected_backend(), 'java')
            config = Path(directory) / 'backend.toml'
            config.write_text('backend = "python"', encoding='utf-8')
            self.assertEqual(java_local.selected_backend(), 'python')
            with patch.dict(os.environ, {'BEOGRAD_BACKEND': 'java'}):
                self.assertEqual(java_local.selected_backend(), 'java')
            config.write_text('backend = "invalid"', encoding='utf-8')
            with self.assertRaises(java_local.LocalError):
                java_local.selected_backend()

    def test_python_does_not_build_java(self):
        with patch.object(java_local, 'selected_backend', return_value='python'), patch.object(java_local, 'stop'), patch.object(java_local, 'start') as start, patch.object(java_local, 'build') as build, patch.object(java_local, 'source_fingerprint') as fingerprint:
            java_local.restart('8123')
            start.assert_called_once_with(8123, 'python')
            build.assert_not_called()
            fingerprint.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows process ownership')
    def test_real_python_start_api_html_static_and_stop(self):
        with tempfile.TemporaryDirectory(prefix='beograd python isolated ') as directory:
            base = Path(directory)
            database = base / 'synthetic.sqlite3'
            connection = sqlite3.connect(database)
            try:
                fixture = ROOT / 'backend-java/src/test/resources/contracts/populated-database.json'
                for statement in json.loads(fixture.read_text(encoding='utf-8')):
                    connection.execute(statement)
                for table in ('ciudades_del_mundo_adminarea', 'ciudades_del_mundo_nuevoadminarea'):
                    connection.execute(f'UPDATE {table} SET pop_latest=1000 WHERE pop_latest > 1000000000')
                connection.commit()
            finally:
                connection.close()
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = os.environ.copy()
            env.update({'PYTHONDONTWRITEBYTECODE': '1', 'BEOGRAD_BACKEND': 'python',
                        'BEOGRAD_DATABASE': str(database), 'BEOGRAD_PYTHON': sys.executable,
                        'BEOGRAD_DJANGO_SECRET_KEY': 'isolated-test-only',
                        'CIUDADES_WEB_TASK_CHILD': '1',
                        'BEOGRAD_RUNTIME_DIR': str(base / 'runtime'),
                        'BEOGRAD_CONFIG': str(base / 'absent.json'),
                        'BEOGRAD_MEDIA_ROOT': str(base / 'media'),
                        'BEOGRAD_JAVA_HOME': str(base / 'no-jdk')})
            command = [sys.executable, '-B', str(ROOT / 'tools/java_local.py')]
            log = base / 'supervisor.log'
            with log.open('w', encoding='utf-8') as output:
                process = subprocess.Popen([*command, str(port)], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    record = base / 'runtime/owner.json'
                    deadline = time.monotonic() + 40
                    while not record.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.1)
                    self.assertTrue(record.exists(), log.read_text(encoding='utf-8') + (base / 'runtime/process.log').read_text(encoding='utf-8'))
                    self.assertEqual(json.loads(record.read_text())['backend'], 'python')
                    opener = build_opener(ProxyHandler({}))
                    for path in ('/api/countries/', '/api/derived/', '/dashboard/population/', '/dashboard/derived/', '/', '/static/ciudades_del_mundo/app.css'):
                        with self.subTest(path=path), opener.open(f'http://127.0.0.1:{port}{path}', timeout=10) as response:
                            self.assertEqual(response.status, 200)
                    # Changing selection cannot prevent stop from finding the running backend.
                    stopped = subprocess.run([*command, 'stop'], cwd=ROOT, env={**env, 'BEOGRAD_BACKEND': 'java'}, capture_output=True, text=True, timeout=20)
                    self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                    process.wait(timeout=15)
                    self.assertEqual(process.returncode, 0, log.read_text(encoding='utf-8'))
                    self.assertFalse(record.exists())
                finally:
                    if process.poll() is None:
                        subprocess.run([*command, 'stop'], cwd=ROOT, env=env, capture_output=True, timeout=20)
                        process.wait(timeout=15)


if __name__ == '__main__':
    unittest.main()
