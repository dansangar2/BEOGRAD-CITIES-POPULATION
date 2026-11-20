"""Acceptance tests execute real Windows .bat files on a disposable source copy.

Requires a JDK 25 and an already verified backend-java/target/beograd-api.jar.
No real database, existing server or external provider is used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows batch acceptance')
class WindowsDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='beograd Windows isolated ')
        cls.base = Path(cls.temporary.name)
        cls.project = cls.base / 'project with spaces'
        cls.project.mkdir()
        # The public Django UI and native Java API share this disposable project.
        shutil.copytree(ROOT / 'ciudades_del_mundo', cls.project / 'ciudades_del_mundo',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'tests', 'subdivisions', 'new_country_configs', 'autoconfig_specs'))
        shutil.copytree(ROOT / 'locale', cls.project / 'locale')
        shutil.copyfile(ROOT / 'manage.py', cls.project / 'manage.py')
        (cls.project / 'tools').mkdir()
        for name in ('__init__.py', 'java_local.py'):
            shutil.copyfile(ROOT / 'tools' / name, cls.project / 'tools' / name)
        for name in ('levantar_app.bat',):
            shutil.copyfile(ROOT / name, cls.project / name)
        cls.backend = cls.project / 'backend-java'
        (cls.backend / 'src/main/resources').mkdir(parents=True)
        shutil.copyfile(ROOT / 'backend-java/src/main/resources/text-catalog.json', cls.backend / 'src/main/resources/text-catalog.json')
        shutil.copyfile(ROOT / 'backend-java/src/main/resources/application.properties', cls.backend / 'src/main/resources/application.properties')
        (cls.backend / '.mvn/wrapper').mkdir(parents=True)
        shutil.copyfile(ROOT / 'backend-java/.mvn/wrapper/maven-wrapper.jar', cls.backend / '.mvn/wrapper/maven-wrapper.jar')
        shutil.copyfile(ROOT / 'backend-java/.mvn/wrapper/maven-wrapper.properties', cls.backend / '.mvn/wrapper/maven-wrapper.properties')
        for name in ('mvnw', 'mvnw.cmd', 'pom.xml'):
            shutil.copyfile(ROOT / 'backend-java' / name, cls.backend / name)
        for source in (ROOT / 'backend-java/src').rglob('*'):
            if not source.is_file() or source.suffix not in ('.java', '.json', '.properties', '.txt'):
                continue
            target = cls.backend / source.relative_to(ROOT / 'backend-java')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        cls.runtime = cls.backend / '.runtime'
        cls.runtime.mkdir()
        artifact = ROOT / 'backend-java/target/beograd-api.jar'
        if not artifact.is_file():
            raise RuntimeError('Build an executable JAR before Windows acceptance tests')
        shutil.copyfile(artifact, cls.runtime / 'current.jar')
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (cls.runtime / 'current.sha256').write_text(digest, encoding='ascii')
        # The copied artifact represents these unchanged fixture sources.
        from tools import java_local
        from unittest.mock import patch
        with patch.object(java_local, 'BACKEND', cls.backend):
            (cls.runtime / 'build.sources.sha256').write_text(java_local.source_fingerprint(), encoding='ascii')
        cls.env = os.environ.copy()
        cls.env.pop('BEOGRAD_JAVA_ENABLED', None)
        cls.env.update({'PYTHONDONTWRITEBYTECODE': '1', 'BEOGRAD_PYTHON': sys.executable,
                        'BEOGRAD_BACKEND': 'java', 'BEOGRAD_DJANGO_SECRET_KEY': 'isolated-test-only',
                        'CIUDADES_WEB_TASK_CHILD': '1',
                        'BEOGRAD_RUNTIME_DIR': str(cls.runtime),
                        'BEOGRAD_CONFIG': str(cls.base / 'nonexistent config.json'), 'BEOGRAD_DATABASE': str(cls.base / 'synthetic.sqlite3'),
                        'BEOGRAD_MEDIA_ROOT': str(cls.base / 'media')})
        cls.env['BEOGRAD_MAVEN_USER_HOME'] = str(ROOT / 'backend-java/.maven')
        cls.env['BEOGRAD_MAVEN_REPOSITORY'] = str(ROOT / 'backend-java/.maven/repository')
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(cls.env['BEOGRAD_DATABASE'])) as connection:
            for statement in json.loads((ROOT / 'backend-java/src/test/resources/contracts/populated-database.json').read_text(encoding='utf-8')):
                connection.execute(statement)
            for table in ('ciudades_del_mundo_adminarea', 'ciudades_del_mundo_nuevoadminarea'):
                connection.execute(f'UPDATE {table} SET pop_latest=1000 WHERE pop_latest > 1000000000')
            connection.commit()
        guard = cls.base / 'import guard'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text("# Django UI and Java API run together.\n", encoding='utf-8')
        cls.env['PYTHONPATH'] = str(guard)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.process = None
        self.output = None

    def tearDown(self):
        if self.process and self.process.poll() is None:
            self.batch('levantar_app.bat', 'stop', timeout=20)
            self.process.wait(timeout=15)
        if self.output:
            self.output.close()

    def batch(self, filename, *args, env=None, timeout=25):
        # Quoted CALL path is intentionally under a directory containing spaces.
        command = 'call "' + str(self.project / filename) + '"' + ''.join(' "' + str(arg) + '"' for arg in args)
        return subprocess.run(f'cmd.exe /d /s /c "{command}"', cwd=self.base, env=env or self.env, capture_output=True, text=True, timeout=timeout)

    def free_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]

    def launch(self, port=None):
        command = 'call "' + str(self.project / 'levantar_app.bat') + '"' + (f' {port}' if port else '')
        record_path = self.runtime / 'owner.json'
        previous_nonce = json.loads(record_path.read_text())['nonce'] if record_path.exists() else None
        if self.output:
            self.output.close()
        self.output = (self.base / 'launcher.log').open('a', encoding='utf-8')
        self.process = subprocess.Popen(f'cmd.exe /d /s /c "{command}"', cwd=self.base, env=self.env, stdout=self.output,
                                        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                record = json.loads(record_path.read_text())
            except (OSError, ValueError):
                record = None
            if record and record['nonce'] != previous_nonce:
                return record
            if self.process.poll() is not None:
                self.fail((self.base / 'launcher.log').read_text(encoding='utf-8'))
            time.sleep(0.1)
        self.fail('Startup never registered readiness')

    def test_alternative_port_paths_with_spaces_java_api_and_stop(self):
        port = self.free_port()
        record = self.launch(port)
        opener = build_opener(ProxyHandler({}))
        self.assertEqual(record['backend'], 'java')
        self.assertEqual(len(record['children']), 2)
        self.assertEqual(record['internal_port'], port + 1)
        for path, expected in (('/internal/ready/', 200), ('/', 200), ('/countries/', 200), ('/api/countries/', 200),
                               ('/api/countries/xx/?include_children=0', 200), ('/api/derived/', 200),
                               ('/configs/sample/export-data-csv/', 200), ('/admin/login/', 200), ('/media/missing.svg', 404)):
            try:
                response = opener.open(f'http://127.0.0.1:{port}{path}', timeout=5)
            except __import__('urllib.error', fromlist=['HTTPError']).HTTPError as exc:
                response = exc
            with response:
                self.assertEqual(response.status, expected, path)
                if path in ('/api/countries/', '/api/derived/'):
                    self.assertTrue(response.headers.get('X-Request-ID'))
        with opener.open(f'http://127.0.0.1:{record["internal_port"]}/api/countries/', timeout=5) as direct:
            self.assertEqual(direct.status, 200)
        with opener.open(f'http://127.0.0.1:{port}/static/ciudades_del_mundo/app.css', timeout=5) as stylesheet:
            self.assertEqual(stylesheet.status, 200)
        stopped = self.batch('levantar_app.bat', 'stop')
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertEqual(self.process.wait(timeout=15), 0)
        for target in (port, record['internal_port']):
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(('127.0.0.1', target)), 0)

    def test_default_port_entrypoint(self):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', 8000))
            except OSError:
                self.skipTest('Port 8000 belongs to an existing process; it is preserved')
        self.assertEqual(self.launch()['port'], 8000)

    def test_rerunning_restarts_the_owned_java_instance(self):
        port = self.free_port()
        before = self.launch(port)
        previous_process = self.process
        after = self.launch(port)
        self.assertEqual(previous_process.wait(timeout=15), 0)
        self.assertNotEqual(before['nonce'], after['nonce'])
        self.assertNotEqual(before['pid'], after['pid'])
        self.assertEqual(after['port'], port)
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/internal/ready/', timeout=3) as ready:
            self.assertEqual(ready.status, 200)

    def test_changed_sources_are_rebuilt_on_next_launch(self):
        port = self.free_port()
        self.launch(port)
        previous_process = self.process
        resource = self.backend / 'src/main/resources/reload-probe.txt'
        resource.write_text('updated build resource', encoding='utf-8')
        try:
            self.launch(port)
            self.assertEqual(previous_process.wait(timeout=15), 0)
            import zipfile
            with zipfile.ZipFile(self.runtime / 'current.jar') as artifact:
                self.assertEqual(artifact.read('BOOT-INF/classes/reload-probe.txt'), b'updated build resource')
            resource.unlink()
            previous_process = self.process
            self.launch(port)
            self.assertEqual(previous_process.wait(timeout=15), 0)
            with zipfile.ZipFile(self.runtime / 'current.jar') as artifact:
                self.assertNotIn('BOOT-INF/classes/reload-probe.txt', artifact.namelist())
        finally:
            resource.unlink(missing_ok=True)

    def test_help_and_management_actions_share_the_entrypoint(self):
        response = self.batch('levantar_app.bat', '--help')
        self.assertEqual(response.returncode, 0, response.stderr)
        for action in ('prepare', 'build', 'start', 'stop', 'rollback'):
            self.assertIn(action, response.stdout)
        response = self.batch('levantar_app.bat', 'prepare')
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)

    def test_occupied_port_preserves_the_existing_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            response = self.batch('levantar_app.bat', listener.getsockname()[1])
            self.assertNotEqual(response.returncode, 0)
            self.assertIn('unavailable', response.stderr)
            with socket.socket() as client:
                self.assertEqual(client.connect_ex(listener.getsockname()), 0)

    def test_missing_jdk_and_invalid_database_fail_without_creating_files(self):
        env = {**self.env, 'BEOGRAD_JAVA_HOME': str(self.base / 'missing JDK')}
        response = self.batch('levantar_app.bat', self.free_port(), env=env)
        self.assertNotEqual(response.returncode, 0)
        self.assertIn('Missing java.exe', response.stderr)
        missing = self.base / 'missing.sqlite3'
        response = self.batch('levantar_app.bat', self.free_port(), env={**self.env, 'BEOGRAD_DATABASE': str(missing)})
        self.assertNotEqual(response.returncode, 0)
        self.assertFalse(missing.exists())

    def test_readiness_failure_cleans_owned_processes(self):
        bad = self.base / 'bad-schema.sqlite3'
        import sqlite3
        sqlite3.connect(bad).close()
        port = self.free_port()
        response = self.batch('levantar_app.bat', port, env={**self.env, 'BEOGRAD_DATABASE': str(bad), 'BEOGRAD_STARTUP_TIMEOUT': '10'}, timeout=25)
        self.assertNotEqual(response.returncode, 0)
        self.assertFalse((self.runtime / 'owner.json').exists())
        with socket.socket() as sock:
            self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)

    def test_dependency_failure_stops_the_other_process(self):
        port = self.free_port()
        record = self.launch(port)
        # Fault injection against this fixture's private Java endpoint only.
        # Killing the Java-owned wrapper simulates a dependency crash; Windows
        # job cleanup must also terminate its Java descendant.
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(1, False, record['children'][0])
        try:
            self.assertTrue(handle)
            self.assertTrue(kernel.TerminateProcess(handle, 9))
        finally:
            if handle:
                kernel.CloseHandle(handle)
        self.assertNotEqual(self.process.wait(timeout=15), 0)
        for target in (port, record['internal_port']):
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(('127.0.0.1', target)), 0)

    def test_rollback_restores_artifact_and_preserves_database_bytes(self):
        before = hashlib.sha256(Path(self.env['BEOGRAD_DATABASE']).read_bytes()).hexdigest()
        current = self.runtime / 'current.jar'
        original = current.read_bytes()
        shutil.copyfile(current, self.runtime / 'previous.jar')
        shutil.copyfile(self.runtime / 'current.sha256', self.runtime / 'previous.sha256')
        current.write_bytes(b'synthetic artifact corruption')
        (self.runtime / 'build.sources.sha256').unlink(missing_ok=True)
        response = self.batch('levantar_app.bat', 'rollback')
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertEqual(current.read_bytes(), original)
        self.assertEqual(hashlib.sha256(Path(self.env['BEOGRAD_DATABASE']).read_bytes()).hexdigest(), before)
        self.assertEqual(self.launch(self.free_port())['port'] > 0, True)
        self.assertEqual(current.read_bytes(), original)

    def test_automatic_build_failure_does_not_activate_or_start_an_artifact(self):
        original = (self.runtime / 'current.jar').read_bytes()
        # The copied tree contains Java source/tests; only Maven is made to fail.
        (self.backend / 'mvnw.cmd').write_text('@echo off\r\nexit /b 37\r\n', encoding='ascii')
        try:
            response = self.batch('levantar_app.bat', self.free_port(), timeout=60)
        finally:
            shutil.copyfile(ROOT / 'backend-java/mvnw.cmd', self.backend / 'mvnw.cmd')
        self.assertNotEqual(response.returncode, 0)
        self.assertIn('exit code 37', response.stderr)
        self.assertEqual((self.runtime / 'current.jar').read_bytes(), original)
        self.assertFalse((self.runtime / 'owner.json').exists())

    def test_combined_mode_overrides_obsolete_opt_out_and_settings(self):
        self.assertTrue((self.project / 'ciudades_del_mundo').exists())
        self.assertFalse((self.project / 'old-backend-python').exists())
        original = self.env.copy()
        try:
            self.env.update({'BEOGRAD_JAVA_ENABLED': '0', 'DJANGO_SETTINGS_MODULE': 'does.not.exist'})
            record = self.launch(self.free_port())
            self.assertEqual(record['backend'], 'java')
            self.assertEqual(len(record['children']), 2)
        finally:
            self.env = original

    def test_build_success_uses_real_wrapper_and_spaces_before_activation(self):
        previous = hashlib.sha256((self.runtime / 'current.jar').read_bytes()).hexdigest()
        response = self.batch('levantar_app.bat', 'build', timeout=180)
        self.assertEqual(response.returncode, 0, (response.stdout + response.stderr)[-5000:])
        self.assertEqual(hashlib.sha256((self.runtime / 'previous.jar').read_bytes()).hexdigest(), previous)
        self.assertTrue((self.runtime / 'current.jar').is_file())

    def test_second_runtime_on_same_database_is_rejected(self):
        port = self.free_port()
        self.launch(port)
        other = self.base / 'second runtime with spaces'
        other.mkdir()
        for filename in ('current.jar', 'current.sha256', 'build.sources.sha256'):
            shutil.copyfile(self.runtime / filename, other / filename)
        response = self.batch('levantar_app.bat', self.free_port(), env={**self.env, 'BEOGRAD_RUNTIME_DIR': str(other)})
        self.assertNotEqual(response.returncode, 0)
        self.assertIn('Another local operation owns .web_java_owner_', response.stderr)
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/internal/ready/', timeout=3) as ready:
            self.assertEqual(ready.status, 200)

    def test_supervisor_crash_kills_its_descendants_and_stale_stop_is_safe(self):
        port = self.free_port()
        record = self.launch(port)
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(1, False, record['pid'])
        try:
            self.assertTrue(handle)
            self.assertTrue(kernel.TerminateProcess(handle, 9))
        finally:
            if handle:
                kernel.CloseHandle(handle)
        self.assertNotEqual(self.process.wait(timeout=15), 0)
        deadline = time.monotonic() + 5
        for target in (port, record['internal_port']):
            while time.monotonic() < deadline:
                with socket.socket() as sock:
                    if sock.connect_ex(('127.0.0.1', target)) != 0:
                        break
                time.sleep(0.05)
            else:
                self.fail('Owned descendant survived supervisor failure')
        response = self.batch('levantar_app.bat', 'stop')
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertFalse((self.runtime / 'owner.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
