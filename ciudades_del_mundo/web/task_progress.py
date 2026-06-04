"""Filesystem-backed per-config progress for web-launched tasks.

The live task log already avoids frequent database writes while long scraping
commands run.  This module follows the same rule for per-country progress: the
child management command writes a small JSON file and the UI reads it through
``task_status``.  Keeping this outside SQLite prevents the progress indicator
from competing with the scraping writes.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile
import time
from typing import Any

from django.conf import settings
from django.utils import timezone


TASK_PROGRESS_DIR_NAME = ".web_task_progress"
WEB_TASK_ID_ENV = "CIUDADES_WEB_TASK_ID"
WEB_TASK_PROGRESS_PATH_ENV = "CIUDADES_WEB_TASK_PROGRESS_PATH"


def task_progress_dir() -> Path:
    return Path(settings.BASE_DIR) / TASK_PROGRESS_DIR_NAME


def task_progress_path(task_id: str) -> Path:
    safe_task_id = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in {"-", "_"})
    return task_progress_dir() / f"{safe_task_id}.json"


def current_task_id() -> str:
    return str(os.environ.get(WEB_TASK_ID_ENV) or "").strip()


def current_task_progress_path() -> Path | None:
    explicit = str(os.environ.get(WEB_TASK_PROGRESS_PATH_ENV) or "").strip()
    if explicit:
        return Path(explicit)
    task_id = current_task_id()
    return task_progress_path(task_id) if task_id else None


def reset_task_progress(task_id: str) -> Path:
    path = task_progress_path(task_id)
    payload = {
        "task_id": str(task_id),
        "updated_at": timezone.now().isoformat(),
        "configs": {},
    }
    _write_json_atomic(path, payload)
    return path


def read_task_config_progress(task_id: str) -> dict[str, dict[str, Any]]:
    path = task_progress_path(task_id)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    configs = payload.get("configs")
    return configs if isinstance(configs, dict) else {}


def write_config_progress(slug: str, status: str, *, detail: str = "") -> None:
    path = current_task_progress_path()
    if not path:
        return

    slug = str(slug or "").strip()
    status = str(status or "").strip()
    if not slug or not status:
        return

    now = timezone.now().isoformat()
    payload = _read_json(path)
    if not isinstance(payload, dict):
        payload = {}
    configs = payload.get("configs")
    if not isinstance(configs, dict):
        configs = {}

    configs[slug] = {
        "status": status,
        "detail": str(detail or ""),
        "updated_at": now,
    }
    payload["task_id"] = payload.get("task_id") or current_task_id()
    payload["updated_at"] = now
    payload["configs"] = configs
    _write_json_atomic(path, payload)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    last_error: OSError | None = None
    for _ in range(4):
        tmp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_name = handle.name
                handle.write(serialized)
                handle.write("\n")
            os.replace(tmp_name, path)
            return
        except OSError as exc:
            last_error = exc
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            time.sleep(0.05)
    if last_error:
        raise last_error
