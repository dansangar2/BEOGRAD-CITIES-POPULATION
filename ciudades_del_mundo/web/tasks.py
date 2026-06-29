"""Persistent task registry for long-running local management commands."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import threading
import uuid

from django.conf import settings
from django.db import OperationalError, ProgrammingError, close_old_connections, connection
from django.utils import timezone
from django.utils.translation import gettext as _

from ciudades_del_mundo.models import WebTask
from .log_retention import cleanup_old_logs
from .task_progress import (
    WEB_TASK_ID_ENV,
    WEB_TASK_PROGRESS_PATH_ENV,
    reset_task_progress,
    task_progress_path,
)


TERMINAL_STATUSES = {WebTask.Status.SUCCEEDED, WebTask.Status.FAILED, WebTask.Status.CANCELLED}
MAX_RUNNING_TASKS = 1
MAX_PERSISTED_TASKS = 300
TASK_LOG_DIR_NAME = ".web_task_logs"
OUTPUT_TAIL_LINES = 240
OUTPUT_STATUS_TAIL_CHARS = 12000


def _bounded_max_running_tasks(value: int | str | None) -> int:
    raw = value if value not in (None, "") else os.environ.get("CIUDADES_WEB_MAX_RUNNING_TASKS", MAX_RUNNING_TASKS)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = MAX_RUNNING_TASKS
    return min(3, max(1, parsed))


class TaskManager:
    """Background task manager backed by the Django database.

    The database is the source of truth for task history and status. The current
    Python process only owns live subprocess handles so cancellation still works
    for tasks started by this server instance.
    """

    def __init__(
        self,
        history_path: Path | None = None,
        *,
        max_running_tasks: int | str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._history_path = history_path or (Path(settings.BASE_DIR) / ".web_tasks.json")
        self._log_dir = Path(settings.BASE_DIR) / TASK_LOG_DIR_NAME
        self._max_running_tasks = _bounded_max_running_tasks(max_running_tasks)
        self._recovery_done = False
        self._log_cleanup_done = False

    def start(self, *, key: str, label: str, args: list[str]) -> WebTask:
        """Queue a task, cancelling the active task with the same key first."""
        self._ensure_recovered()
        process_to_terminate = None
        task_ids_to_start: list[str] = []
        with self._lock:
            previous = self.latest_for_key(key)
            if previous and previous.is_active:
                process_to_terminate = self._cancel_locked(previous)

            task_id = uuid.uuid4().hex[:12]
            now = timezone.now()
            task = WebTask.objects.create(
                id=task_id,
                key=key,
                label=label,
                args=[str(arg) for arg in args],
                status=WebTask.Status.QUEUED,
                created_at=now,
                log_path=self._relative_log_path(task_id, key),
            )
            task_ids_to_start = self._dispatch_queued_locked()
            self._trim_history_locked()

        if process_to_terminate and process_to_terminate.poll() is None:
            process_to_terminate.terminate()
        self._start_workers(task_ids_to_start)
        task.refresh_from_db()
        return task

    def cancel(self, task_id: str) -> WebTask | None:
        self._ensure_recovered()
        process = None
        task_ids_to_start: list[str] = []
        with self._lock:
            task = self.get(task_id)
            if not task or not task.is_active:
                return task
            process = self._cancel_locked(task)
            if task.status in TERMINAL_STATUSES:
                task_ids_to_start = self._dispatch_queued_locked()

        if process and process.poll() is None:
            process.terminate()
        self._start_workers(task_ids_to_start)
        return self.get(task_id)

    def get(self, task_id: str) -> WebTask | None:
        self._ensure_recovered()
        if not self._table_ready():
            return None
        return WebTask.objects.filter(id=task_id).first()

    def latest_for_key(self, key: str) -> WebTask | None:
        self._ensure_recovered()
        if not self._table_ready():
            return None
        return WebTask.objects.filter(key=key).order_by("-created_at", "-id").first()

    def list(self, limit: int = 20) -> list[WebTask]:
        self._ensure_recovered()
        if not self._table_ready():
            return []
        return list(WebTask.objects.order_by("-created_at", "-id")[:limit])

    def output_text(self, task: WebTask) -> str:
        """Return the full persisted log when available, otherwise the DB tail."""
        log_path = self._resolve_log_path(task)
        if log_path and log_path.exists():
            try:
                return log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        return task.output_text

    def output_tail_text(self, task: WebTask, max_chars: int = OUTPUT_STATUS_TAIL_CHARS) -> str:
        """Return a lightweight tail for live polling without rereading huge logs."""
        max_chars = max(1000, int(max_chars or OUTPUT_STATUS_TAIL_CHARS))
        log_path = self._resolve_log_path(task)
        if log_path and log_path.exists():
            try:
                with log_path.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - max_chars * 4))
                    chunk = handle.read()
                return chunk.decode("utf-8", errors="replace")[-max_chars:]
            except OSError:
                pass
        return task.output_text[-max_chars:]

    def output_offset(self, task: WebTask) -> int:
        """Return the current persisted log offset used by incremental polling."""
        log_path = self._resolve_log_path(task)
        if log_path and log_path.exists():
            try:
                return int(log_path.stat().st_size)
            except OSError:
                pass
        return len(task.output_text)

    def output_since_text(self, task: WebTask, offset: int) -> tuple[str, int, bool]:
        """Return log content appended after offset plus the new offset."""
        offset = max(0, int(offset or 0))
        log_path = self._resolve_log_path(task)
        if log_path and log_path.exists():
            try:
                size = int(log_path.stat().st_size)
                reset = offset > size
                if reset:
                    offset = 0
                with log_path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                return chunk.decode("utf-8", errors="replace"), size, reset
            except OSError:
                pass
        text = task.output_text
        reset = offset > len(text)
        if reset:
            offset = 0
        return text[offset:], len(text), reset

    def _cancel_locked(self, task: WebTask) -> subprocess.Popen | None:
        task.cancel_requested = True
        self._append_output_locked(task, _("\n[CANCEL] Cancelación solicitada desde la interfaz.\n"))
        process = self._processes.get(task.id)
        if task.status == WebTask.Status.QUEUED or process is None:
            task.status = WebTask.Status.CANCELLED
            task.finished_at = timezone.now()
            task.save(update_fields=["cancel_requested", "status", "finished_at", "output", "log_path", "updated_at"])
            return process
        task.save(update_fields=["cancel_requested", "output", "log_path", "updated_at"])
        return process

    def _dispatch_queued_locked(self) -> list[str]:
        """Promote queued rows while respecting the backend process limit."""
        running = WebTask.objects.filter(status=WebTask.Status.RUNNING, cancel_requested=False).count()
        slots = max(0, self._max_running_tasks - running)
        if slots <= 0:
            return []
        queued = list(
            WebTask.objects.filter(status=WebTask.Status.QUEUED, cancel_requested=False)
            .order_by("created_at", "id")[:slots]
        )
        now = timezone.now()
        task_ids = []
        for task in queued:
            task.status = WebTask.Status.RUNNING
            task.started_at = task.started_at or now
            task.save(update_fields=["status", "started_at", "updated_at"])
            task_ids.append(task.id)
        return task_ids

    def _start_worker(self, task_id: str) -> None:
        thread = threading.Thread(target=self._run, args=(task_id,), daemon=True)
        thread.start()

    def _start_workers(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            self._start_worker(task_id)

    def _run(self, task_id: str) -> None:
        close_old_connections()
        task = self.get(task_id)
        if not task:
            return

        with self._lock:
            task = self.get(task_id)
            if not task or task.status != WebTask.Status.RUNNING:
                return
            if task.cancel_requested:
                task.status = WebTask.Status.CANCELLED
                task.finished_at = timezone.now()
                task.save(update_fields=["status", "finished_at", "updated_at"])
                should_stop = True
            else:
                should_stop = False
        if should_stop:
            with self._lock:
                task_ids_to_start = self._dispatch_queued_locked()
            self._start_workers(task_ids_to_start)
            close_old_connections()
            return

        manage_py = Path(settings.BASE_DIR) / "manage.py"
        command = [sys.executable, "-u", str(manage_py), *(task.args or [])]
        try:
            progress_path = reset_task_progress(task_id)
        except OSError:
            progress_path = task_progress_path(task_id)

        try:
            process = subprocess.Popen(
                command,
                cwd=settings.BASE_DIR,
                env={
                    **os.environ,
                    "CIUDADES_WEB_TASK_CHILD": "1",
                    WEB_TASK_ID_ENV: task_id,
                    WEB_TASK_PROGRESS_PATH_ENV: str(progress_path),
                    "PYTHONUNBUFFERED": "1",
                    "PYTHONIOENCODING": "utf-8",
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            task_ids_to_start = []
            with self._lock:
                task = self.get(task_id)
                if task:
                    task.status = WebTask.Status.FAILED
                    task.finished_at = timezone.now()
                    self._append_output_locked(
                        task,
                        _("[ERROR] No se pudo iniciar la tarea: %(error)s\n") % {"error": exc},
                    )
                    task.save(update_fields=["status", "finished_at", "output", "log_path", "updated_at"])
                task_ids_to_start = self._dispatch_queued_locked()
            self._start_workers(task_ids_to_start)
            close_old_connections()
            return

        with self._lock:
            self._processes[task_id] = process
            task = self.get(task_id)
            cancel_requested = bool(task and task.cancel_requested)

        if cancel_requested and process.poll() is None:
            process.terminate()

        assert process.stdout is not None
        # Important for SQLite: while the child command is importing/scraping, do
        # not write the WebTask row on every log line. Those writes compete with
        # the child process, which is also writing AdminArea/asset rows, and can
        # make the management command fail with "database is locked". The live UI
        # reads the log from the filesystem, so the database only needs a final
        # status/output update when the subprocess finishes.
        output_tail = list(task.output or [])[-OUTPUT_TAIL_LINES:]
        log_task = task
        close_old_connections()

        for line in process.stdout:
            with self._lock:
                output_tail.append(line)
                output_tail = output_tail[-OUTPUT_TAIL_LINES:]
                self._append_log_file_locked(log_task, line)

        returncode = process.wait()
        close_old_connections()
        task_ids_to_start = []
        with self._lock:
            task = self.get(task_id)
            if task:
                task.output = output_tail[-OUTPUT_TAIL_LINES:]
                task.returncode = returncode
                task.finished_at = timezone.now()
                if task.cancel_requested:
                    task.status = WebTask.Status.CANCELLED
                elif returncode == 0:
                    task.status = WebTask.Status.SUCCEEDED
                else:
                    task.status = WebTask.Status.FAILED
                task.save(update_fields=["returncode", "finished_at", "status", "output", "log_path", "updated_at"])
            self._processes.pop(task_id, None)
            self._trim_history_locked()
            task_ids_to_start = self._dispatch_queued_locked()
        self._start_workers(task_ids_to_start)
        close_old_connections()

    def _append_output_locked(self, task: WebTask, text: str) -> None:
        output = [str(line) for line in (task.output or [])]
        output.append(text)
        task.output = output[-OUTPUT_TAIL_LINES:]
        self._append_log_file_locked(task, text)
    def _append_log_file_locked(self, task: WebTask, text: str) -> None:
        log_path = self._ensure_log_path_locked(task)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(text)
        except OSError:
            return

    def _ensure_log_path_locked(self, task: WebTask) -> Path:
        if not task.log_path:
            task.log_path = self._relative_log_path(task.id, task.key)
        return self._resolve_log_path(task) or (self._log_dir / f"{task.id}.log")

    def _resolve_log_path(self, task: WebTask) -> Path | None:
        if not task.log_path:
            return None
        path = Path(task.log_path)
        if path.is_absolute():
            return path
        return Path(settings.BASE_DIR) / path

    def _relative_log_path(self, task_id: str, key: str = "") -> str:
        return str(Path(TASK_LOG_DIR_NAME) / _task_log_group(key) / f"{task_id}.log")

    def _ensure_recovered(self) -> None:
        """Recover stale DB tasks lazily, outside Django app initialization.

        Importing URL/views during Django startup must not query the database.
        Child management-command processes launched by a web task also skip this
        step so they do not mark their own parent task as interrupted while it is
        still running.
        """
        if self._recovery_done or os.environ.get("CIUDADES_WEB_TASK_CHILD") == "1":
            return
        with self._lock:
            if self._recovery_done or os.environ.get("CIUDADES_WEB_TASK_CHILD") == "1":
                return
            if not self._log_cleanup_done:
                cleanup_old_logs()
                self._log_cleanup_done = True
            self._recover_interrupted_tasks()
            self._recovery_done = True

    def _recover_interrupted_tasks(self) -> None:
        if not self._table_ready():
            return
        try:
            interrupted = list(WebTask.objects.exclude(status__in=TERMINAL_STATUSES))
        except (OperationalError, ProgrammingError):
            return
        for task in interrupted:
            task.status = WebTask.Status.CANCELLED
            task.finished_at = timezone.now()
            self._append_output_locked(task, _("\n[INFO] Tarea parada por reinicio del servidor.\n"))
            task.save(update_fields=["status", "finished_at", "output", "log_path", "updated_at"])

    def _trim_history_locked(self) -> None:
        stale_ids = list(
            WebTask.objects.order_by("-created_at", "-id")
            .values_list("id", flat=True)[MAX_PERSISTED_TASKS:]
        )
        if stale_ids:
            WebTask.objects.filter(id__in=stale_ids, status__in=TERMINAL_STATUSES).delete()

    @staticmethod
    def _table_ready() -> bool:
        try:
            return WebTask._meta.db_table in connection.introspection.table_names()
        except (OperationalError, ProgrammingError):
            return False


# Kept as a stable import name for views and commands.
ManagedTask = WebTask

task_manager = TaskManager()


def _task_log_group(key: str) -> str:
    text = str(key or "").strip()
    if ":" in text:
        prefix, value = text.split(":", 1)
        raw_group = value or prefix
    else:
        raw_group = text or "general"
    if raw_group in {"all", "unpopulated"}:
        raw_group = f"_{raw_group}"
    group = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in raw_group)
    return group.strip("._") or "general"
