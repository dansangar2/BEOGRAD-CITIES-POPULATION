"""Deterministic log and subprocess lifecycle regressions; no child is launched."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import hashlib
import threading
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase

from ciudades_del_mundo.infrastructure.wikidata_countries import COUNTRY_WIKIDATA_IDS, wikidata_country_id
from ciudades_del_mundo.models import WebTask
from ciudades_del_mundo.web.tasks import TaskManager


class LogCursorTests(SimpleTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "task.log"
        self.manager = TaskManager()
        self.task = WebTask(id="log", log_path=str(self.path), output=["fallback\u00e9"], status="running")

    def drain(self):
        offset, blocks = 0, []
        for _ in range(100):
            text, next_offset, reset = self.manager.output_since_text(self.task, offset)
            self.assertFalse(reset)
            if next_offset == offset:
                return "".join(blocks), offset
            self.assertLessEqual(next_offset - offset, 5)
            blocks.append(text)
            offset = next_offset
        self.fail("Cursor did not converge")

    def test_bounded_utf8_blocks_have_no_loss_or_duplicates(self):
        expected = "ab\u20ac\u00e9\U0001f642fin" * 3
        self.path.write_bytes(expected.encode())
        with patch("ciudades_del_mundo.web.tasks.OUTPUT_CHUNK_BYTES", 5):
            text, offset = self.drain()
        self.assertEqual(text, expected)
        self.assertEqual(offset, len(expected.encode()))

    def test_growth_between_size_check_and_read_returns_consumed_byte_offset(self):
        self.path.write_bytes(b"abc")
        import os
        original = os.fstat
        calls = []
        def grow(fd):
            stat = original(fd)
            if not calls:
                with self.path.open("ab") as handle:
                    handle.write("\u00e9defgh".encode())
            calls.append(True)
            return stat
        with patch("ciudades_del_mundo.web.tasks.os.fstat", side_effect=grow), patch("ciudades_del_mundo.web.tasks.OUTPUT_CHUNK_BYTES", 5):
            first, offset, reset = self.manager.output_since_text(self.task, 0)
            self.assertEqual((first, offset, reset), ("abc\u00e9", 5, False))
            rest, end, reset = self.manager.output_since_text(self.task, offset)
        self.assertEqual(first + rest, "abc\u00e9defgh")
        self.assertEqual(end, len("abc\u00e9defgh".encode()))

    def test_partial_utf8_write_is_retried_from_its_first_byte(self):
        self.path.write_bytes(b"a\xe2\x82")
        self.assertEqual(self.manager.output_since_text(self.task, 0), ("a", 1, False))
        with self.path.open("ab") as handle:
            handle.write(b"\xac!")
        self.assertEqual(self.manager.output_since_text(self.task, 1), ("\u20ac!", 5, False))

    def test_truncation_between_size_check_and_read_resets_immediately(self):
        import os
        self.path.write_bytes(b"old content")
        original, calls = os.fstat, []

        def truncate(fd):
            stat = original(fd)
            if not calls:
                self.path.write_bytes(b"new")
            calls.append(True)
            return stat

        with patch("ciudades_del_mundo.web.tasks.os.fstat", side_effect=truncate):
            self.assertEqual(self.manager.output_since_text(self.task, 8), ("new", 3, True))

    def test_truncation_missing_file_and_read_failure_use_consistent_cursors(self):
        self.path.write_bytes("\u00e9".encode())
        self.assertEqual(self.manager.output_since_text(self.task, 20), ("\u00e9", 2, True))
        self.path.unlink()
        self.assertEqual(self.manager.output_since_text(self.task, 30), ("fallback\u00e9", 9, True))
        self.path.write_bytes(b"unread")
        with patch.object(Path, "open", side_effect=OSError("offline")):
            self.assertEqual(self.manager.output_since_text(self.task, 8), ("\u00e9", 9, False))

    def test_terminal_invalid_bytes_are_consumed_and_fallback_is_bounded(self):
        self.path.write_bytes(b"x\xe2")
        self.task.status = "cancelled"
        self.assertEqual(self.manager.output_since_text(self.task, 0), ("x\ufffd", 2, False))
        self.path.unlink()
        with patch("ciudades_del_mundo.web.tasks.OUTPUT_CHUNK_BYTES", 5):
            self.assertEqual(self.drain(), ("fallback\u00e9", 9))


class TaskLifecycleTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_override = self.settings(BASE_DIR=Path(self.temp.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.manager = TaskManager(max_running_tasks=1)
        self.manager._recovery_done = True
        self.started = []
        self.manager._start_worker = self.started.append
        self.process = Mock(stdout=StringIO("child output\n"))
        self.process.poll.return_value = None
        self.process.wait.return_value = -15

    def start(self, key="test:first"):
        return self.manager.start(key=key, label=key, args=["check"])

    def run_task(self, task, prepare=None):
        with patch("ciudades_del_mundo.web.tasks.close_old_connections"), patch("ciudades_del_mundo.web.tasks.reset_task_progress", side_effect=prepare, return_value=Path(self.temp.name)/"progress"), patch("ciudades_del_mundo.web.tasks.subprocess.Popen", return_value=self.process) as spawn:
            self.manager._run(task.id)
        return spawn

    def test_cancel_during_preparation_prevents_spawn(self):
        task = self.start()
        def cancel(_):
            self.manager.cancel(task.id)
            return Path(self.temp.name)/"progress"
        spawn = self.run_task(task, cancel)
        spawn.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")

    def test_replacement_during_preparation_prevents_old_spawn(self):
        task = self.start()
        replacements = []
        def replace(_):
            replacements.append(self.start())
            return Path(self.temp.name)/"progress"
        self.run_task(task, replace).assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(self.started, [task.id, replacements[0].id])

    def test_running_cancel_retains_slot_until_wait_and_replacement_starts_once(self):
        task = self.start()
        replacements = []
        def output():
            replacements.append(self.start())
            self.process.terminate.assert_called_once()
            replacements[0].refresh_from_db()
            self.assertEqual(replacements[0].status, "queued")
            self.assertEqual(self.started, [task.id])
            yield "ending\n"
        self.process.stdout = output()
        self.run_task(task).assert_called_once()
        task.refresh_from_db()
        replacements[0].refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(replacements[0].status, "running")
        self.assertEqual(self.started, [task.id, replacements[0].id])
        self.assertEqual(self.manager._processes, {})

    def test_queued_cancel_and_limit(self):
        first, second, third = self.start(), self.start("test:second"), self.start("test:third")
        self.manager.cancel(second.id)
        self.assertEqual(self.started, [first.id])
        second.refresh_from_db()
        self.assertEqual(second.status, "cancelled")
        self.process.wait.return_value = 0
        self.run_task(first)
        self.assertEqual(self.started, [first.id, third.id])

    def test_spawn_and_registration_are_atomic_for_cancellation(self):
        task = self.start()
        attempted, observed = threading.Event(), threading.Event()
        handles = []

        def cancellation_probe():
            attempted.set()
            with self.manager._lock:
                handles.append(self.manager._processes.get(task.id))
            observed.set()

        probe = threading.Thread(target=cancellation_probe)

        def spawn(*args, **kwargs):
            # A cancellation arriving inside Popen must wait for registration.
            self.assertTrue(self.manager._lock._is_owned())
            probe.start()
            self.assertTrue(attempted.wait(2))
            return self.process

        def output():
            self.assertTrue(observed.wait(2))
            self.assertEqual(handles, [self.process])
            self.manager.cancel(task.id)
            yield "cancelled\n"

        self.process.stdout = output()
        with patch("ciudades_del_mundo.web.tasks.close_old_connections"), patch("ciudades_del_mundo.web.tasks.reset_task_progress", return_value=Path(self.temp.name)/"progress"), patch("ciudades_del_mundo.web.tasks.subprocess.Popen", side_effect=spawn):
            self.manager._run(task.id)
        probe.join(2)
        self.assertFalse(probe.is_alive())
        self.process.terminate.assert_called_once()
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")

    def test_spawn_failure_releases_slot_for_the_next_task(self):
        first, second = self.start(), self.start("test:second")
        with patch("ciudades_del_mundo.web.tasks.close_old_connections"), patch("ciudades_del_mundo.web.tasks.reset_task_progress", return_value=Path(self.temp.name)/"progress"), patch("ciudades_del_mundo.web.tasks.subprocess.Popen", side_effect=OSError("simulated")):
            self.manager._run(first.id)
        first.refresh_from_db()
        self.assertEqual(first.status, "failed")
        self.assertEqual(self.started, [first.id, second.id])

    def test_terminal_http_log_reports_remaining_blocks(self):
        from ciudades_del_mundo.web.handlers.tasks import task_status
        task = self.start()
        task.status = "succeeded"
        task.save()
        path = Path(self.temp.name)/task.log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"abcdefghij")
        with patch("ciudades_del_mundo.web.handlers.tasks.task_manager", self.manager), patch("ciudades_del_mundo.web.tasks.OUTPUT_CHUNK_BYTES", 5):
            first = json.loads(task_status(RequestFactory().get("/", {"since": 0}), task.id).content)
            last = json.loads(task_status(RequestFactory().get("/", {"since": first["output_offset"]}), task.id).content)
        self.assertFalse(first["is_active"])
        self.assertTrue(first["output_has_more"])
        self.assertFalse(last["output_has_more"])
        self.assertEqual(first["output_delta"] + last["output_delta"], "abcdefghij")


class WikidataCatalogTests(SimpleTestCase):
    def test_exact_keys_and_unknown_fallback(self):
        from ciudades_del_mundo.web.queries.browser import _wikidata_country_id
        self.assertEqual(len(COUNTRY_WIKIDATA_IDS), 232)
        # Fingerprint of the catalog extracted from the original browser query.
        self.assertEqual(hashlib.sha256(json.dumps(COUNTRY_WIKIDATA_IDS, sort_keys=True).encode()).hexdigest(),
                         "a87295c02a4746e4e25a081a926d56eccc3ebd2a5f86dc2e7f4e77de8ce397ec")
        for code, identifier in COUNTRY_WIKIDATA_IDS.items():
            self.assertEqual(_wikidata_country_id(code), identifier)
        for code in ("unknown", "Spain", " spain", ""):
            self.assertEqual(wikidata_country_id(code), "")
        self.assertEqual(wikidata_country_id("spain"), "Q29")
