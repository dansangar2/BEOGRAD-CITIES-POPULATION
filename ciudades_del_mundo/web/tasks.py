"""In-process task registry for long-running local management commands."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from django.conf import settings


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@dataclass
class ManagedTask:
    """State tracked for one background command."""

    id: str
    key: str
    label: str
    args: list[str]
    status: str = "queued"
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    returncode: int | None = None
    cancel_requested: bool = False
    output: deque[str] = field(default_factory=lambda: deque(maxlen=240))
    _process: subprocess.Popen | None = field(default=None, repr=False)

    @property
    def command_display(self) -> str:
        return " ".join(["py", "manage.py", *self.args])

    @property
    def output_text(self) -> str:
        return "".join(self.output)

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL_STATUSES


class TaskManager:
    """Small background task manager for the local web dashboard.

    Tasks are process-local and intentionally simple: the development server can
    start, inspect and terminate Django management commands without introducing
    Celery, Redis or a migration-backed job table.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, ManagedTask] = {}
        self._latest_by_key: dict[str, str] = {}

    def start(self, *, key: str, label: str, args: list[str]) -> ManagedTask:
        """Start a task, cancelling the active task with the same key first."""
        with self._lock:
            previous = self.latest_for_key(key)
            if previous and previous.is_active:
                self.cancel(previous.id)

            task = ManagedTask(id=uuid.uuid4().hex[:12], key=key, label=label, args=args)
            self._tasks[task.id] = task
            self._latest_by_key[key] = task.id

            thread = threading.Thread(target=self._run, args=(task.id,), daemon=True)
            thread.start()
            return task

    def cancel(self, task_id: str) -> ManagedTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or not task.is_active:
                return task
            task.cancel_requested = True
            task.output.append("\n[CANCEL] Cancelacion solicitada desde la interfaz.\n")
            process = task._process

        if process and process.poll() is None:
            process.terminate()
        return task

    def get(self, task_id: str) -> ManagedTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def latest_for_key(self, key: str) -> ManagedTask | None:
        task_id = self._latest_by_key.get(key)
        if not task_id:
            return None
        return self._tasks.get(task_id)

    def list(self, limit: int = 20) -> list[ManagedTask]:
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda task: task.created_at,
                reverse=True,
            )
            return tasks[:limit]

    def _run(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return

        with self._lock:
            task.status = "running"
            task.started_at = datetime.now()

        manage_py = Path(settings.BASE_DIR) / "manage.py"
        command = [sys.executable, str(manage_py), *task.args]

        try:
            process = subprocess.Popen(
                command,
                cwd=settings.BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            with self._lock:
                task.status = "failed"
                task.finished_at = datetime.now()
                task.output.append(f"[ERROR] No se pudo iniciar la tarea: {exc}\n")
            return

        with self._lock:
            task._process = process

        assert process.stdout is not None
        for line in process.stdout:
            with self._lock:
                task.output.append(line)

        returncode = process.wait()
        with self._lock:
            task.returncode = returncode
            task.finished_at = datetime.now()
            task._process = None
            if task.cancel_requested:
                task.status = "cancelled"
            elif returncode == 0:
                task.status = "succeeded"
            else:
                task.status = "failed"


task_manager = TaskManager()
