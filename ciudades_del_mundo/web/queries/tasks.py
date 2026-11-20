"""Present task history, progress and lifecycle payloads."""

from __future__ import annotations


from pathlib import Path
from django.urls import reverse
from ciudades_del_mundo.web.task_codes import task_error_info
from ciudades_del_mundo.web.task_progress import read_task_config_progress
from ciudades_del_mundo.web.tasks import task_manager


def _task_payload(task, *, include_output: bool = False) -> dict:
    error_info = task_error_info(task)
    payload = {
        "id": task.id,
        "key": task.key,
        "label": task.label,
        "status": task.status,
        "is_active": task.is_active,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "returncode": task.returncode,
        "command": task.command_display,
        "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
        "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
        "error_code": error_info["code"],
        "error_severity": error_info.get("severity", ""),
        "error_message": error_info.get("message", ""),
        "error_description": error_info["description"],
    }
    if include_output:
        payload["output"] = task_manager.output_tail_text(task)
    return payload


def _task_started_payload(task, *, label: str = "", summary_url: str = "") -> dict:
    """Return the compact JSON contract consumed by the shared task popup."""
    task_id = getattr(task, "id", "")
    return {
        "ok": True,
        "task_id": task_id,
        "label": label or getattr(task, "label", ""),
        "status": getattr(task, "status", "queued"),
        "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task_id}),
        "summary_url": summary_url,
        "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task_id}),
    }


def _task_table_context(tasks, *, page_size: int, compact: bool = True) -> dict:
    """Return shared context for task tables, including a cheap polling signature."""
    snapshot = "|".join(
        f"{task.id}:{task.status}:{task.started_at or task.created_at}:{task.finished_at or ''}"
        for task in tasks
    )
    return {
        "tasks": tasks,
        "page_size": page_size,
        "compact": compact,
        "has_active_tasks": any(task.is_active for task in tasks),
        "task_table_snapshot": snapshot,
    }


def _terminal_config_progress_status(task) -> str:
    """Return per-config terminal status inferred from the owning task."""
    if not task or getattr(task, "is_active", False):
        return ""
    key = str(getattr(task, "key", "") or "")
    status = str(getattr(task, "status", "") or "")
    if status == "succeeded":
        if key.startswith("scrape:"):
            return "populated"
        if key.startswith("validate-config:"):
            return "validated"
        if key.startswith("clear-config:"):
            return "pending"
        if key.startswith("asset-corrections:"):
            return "populated"
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        return "failed"
    return ""


def _progress_status_after_terminal_task(current_status: str, terminal_status: str) -> str:
    """Map stale progress-file statuses once the parent WebTask is terminal."""
    current_status = str(current_status or "").strip().casefold()
    terminal_status = str(terminal_status or "").strip().casefold()
    active_statuses = {"validating", "populating", "clearing", "running", "queued"}
    if terminal_status == "cancelled":
        if current_status == "populating":
            return "validated"
        if current_status in {"validating", "clearing", "running", "queued", ""}:
            return "pending"
        return current_status
    if terminal_status == "failed":
        return "failed" if current_status in active_statuses else current_status
    if terminal_status in {"validated", "populated"}:
        finishable = active_statuses | {"succeeded", "validated", "populated"}
        return terminal_status if current_status in finishable else current_status
    if terminal_status == "pending":
        return "pending" if current_status in active_statuses | {"pending"} else current_status
    return current_status


def _single_config_slug_from_task_key(task) -> str:
    key = str(getattr(task, "key", "") or "")
    for prefix in ("scrape:", "validate-config:", "clear-config:"):
        if key.startswith(prefix):
            slug = key.split(":", 1)[1]
            return "" if slug in {"all", "unpopulated"} else slug
    return ""


def _filtered_tasks(params, *, limit: int):
    """Return task history filtered by the selected UI state, if present."""
    tasks = task_manager.list(limit=limit)
    status = str(params.get("status") or "").strip().casefold()
    if not status:
        return tasks
    return [task for task in tasks if str(getattr(task, "status", "") or "").casefold() == status]


def _task_is_ignored(task, ignored_task) -> bool:
    return bool(task and ignored_task and getattr(task, "id", None) == getattr(ignored_task, "id", None))


def _task_progress_status_for_slug(task, slug: str) -> str:
    if not task or not slug:
        return ""
    progress = read_task_config_progress(getattr(task, "id", ""))
    item = progress.get(slug) if isinstance(progress, dict) else None
    if not isinstance(item, dict):
        return ""
    return str(item.get("status") or "").strip().casefold()


def _task_runs_direct_scrape(task) -> bool:
    args = list(getattr(task, "args", None) or [])
    return bool(args and str(args[0]) == "scrape_subdivisions_with_assets")


def _queue_derived_seed_import_tasks(
    paths: list[Path],
    *,
    section: str,
    key_prefix: str,
    label_template: str,
) -> list[tuple[object, str]]:
    queued_tasks = []
    for path in paths:
        slug = _derived_seed_task_identifier(path, section=section)
        label = label_template % {"slug": slug}
        task = task_manager.start(
            key=f"{key_prefix}:{slug.replace('/', '_')}",
            label=label,
            args=["sync_derived_configs", section, slug, "--force"],
        )
        queued_tasks.append((task, label))
    return queued_tasks


def _derived_seed_task_identifier(path: Path, *, section: str) -> str:
    if section not in {"groups", "subdivisions"}:
        return path.stem
    parent = path.parent.name
    return f"{parent}/{path.stem}" if parent and path.parent.parent.name == section else path.stem
