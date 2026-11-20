"""HTTP endpoints for tasks."""

from __future__ import annotations
from ciudades_del_mundo.web.http import missing_data_as_404, query_params

from ciudades_del_mundo.web import http as http

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.web.task_codes import task_error_info
from ciudades_del_mundo.web.task_progress import read_task_config_progress
from ciudades_del_mundo.web.tasks import task_manager
from ciudades_del_mundo.web.queries import configs as _queries_configs
from ciudades_del_mundo.web.queries import tasks as _queries_tasks


@missing_data_as_404
def api_task_list(request):
    """Return persisted task history for external/API consumers."""
    limit = min(http._page_size(request, default=50), 200)
    return JsonResponse({"tasks": [_queries_tasks._task_payload(task) for task in task_manager.list(limit=limit)]})


@missing_data_as_404
def api_task_detail(request, task_id):
    """Return one persisted task status and its latest log tail."""
    task = task_manager.get(task_id)
    if not task:
        raise Http404(_("Tarea no encontrada."))
    return JsonResponse(_queries_tasks._task_payload(task, include_output=True))


@missing_data_as_404
def task_status(request, task_id):
    """Return current state for a web-launched task."""
    task = task_manager.get(task_id)
    if not task:
        if http._wants_json(request):
            return JsonResponse({"ok": False, "error": _("No existe la tarea solicitada.")}, status=404)
        raise Http404(_("No existe la tarea solicitada."))
    config_progress = read_task_config_progress(task.id)
    terminal_status = _queries_tasks._terminal_config_progress_status(task)
    if terminal_status:
        slug = _queries_tasks._single_config_slug_from_task_key(task)
        summary_status = ""
        if terminal_status == "cancelled" and slug:
            summary_status = str(_queries_configs._config_summary_for_slug(slug).get("task_display_status") or "")
        for item in config_progress.values():
            if isinstance(item, dict):
                next_status = summary_status or _queries_tasks._progress_status_after_terminal_task(
                    str(item.get("status") or ""), terminal_status
                )
                if next_status:
                    item["status"] = next_status
        if slug and not config_progress:
            config_progress = {slug: {"status": summary_status or terminal_status, "detail": ""}}
    error_info = task_error_info(task)
    if error_info["code"] and isinstance(config_progress, dict):
        for item in config_progress.values():
            if isinstance(item, dict):
                item.setdefault("error_code", error_info["code"])
                item.setdefault("error_severity", error_info.get("severity", ""))
                item.setdefault("error_message", error_info.get("message", ""))
                item.setdefault("error_description", error_info["description"])
    payload = {
        "id": task.id,
        "label": task.label,
        "status": task.status,
        "is_active": task.is_active,
        "returncode": task.returncode,
        "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
        "config_progress": config_progress,
        "error_code": error_info["code"],
        "error_severity": error_info.get("severity", ""),
        "error_message": error_info.get("message", ""),
        "error_description": error_info["description"],
    }
    if "since" in request.GET:
        try:
            offset = int(request.GET.get("since") or 0)
        except (TypeError, ValueError):
            offset = 0
        delta, next_offset, reset = task_manager.output_since_text(task, offset)
        payload.update({
            "output_delta": delta,
            "output_offset": next_offset,
            "output_reset": reset,
            "output_has_more": next_offset < task_manager.output_offset(task),
        })
    else:
        payload["output"] = task_manager.output_tail_text(task)
    return JsonResponse(payload)


@missing_data_as_404
def task_list(request):
    """Render background task history."""
    return render(
        request,
        "ciudades_del_mundo/task_list.html",
        {"task_table_url": reverse("ciudades_del_mundo:task_table")},
    )


@missing_data_as_404
def task_table(request):
    """Render the asynchronous background task table partial."""
    tasks = _queries_tasks._filtered_tasks(query_params(request), limit=200)
    return render(
        request,
        "ciudades_del_mundo/partials/task_table.html",
        _queries_tasks._task_table_context(tasks, page_size=http._page_size(request, default=25), compact=False),
    )


@missing_data_as_404
def task_detail(request, task_id):
    """Render one background task, auto-refreshing while active."""
    task = task_manager.get(task_id)
    if not task:
        raise Http404(_("Tarea no encontrada."))
    task_code = task_error_info(task)
    return render(
        request,
        "ciudades_del_mundo/task_detail.html",
        {
            "task": task,
            "task_output": task_manager.output_text(task),
            "task_output_offset": task_manager.output_offset(task),
            "task_code": task_code,
            "task_status_code": task_code.get("code", ""),
            "task_status_code_description": task_code.get("description", ""),
            "task_status_code_severity": task_code.get("severity", ""),
        },
    )


@missing_data_as_404
def task_cancel(request, task_id):
    """Cancel an active background task."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no permitido.")}, status=405)
        return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
    task = task_manager.cancel(task_id)
    if not task:
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Tarea no encontrada.")}, status=404)
        return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
    message = _("Cancelacion solicitada para '%(label)s'.") % {"label": task.label}
    if wants_json:
        error_info = task_error_info(task)
        return JsonResponse(
            {
                "ok": True,
                "message": message,
                "id": task.id,
                "label": task.label,
                "status": task.status,
                "is_active": task.is_active,
                "returncode": task.returncode,
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "error_code": error_info["code"],
                "error_severity": error_info.get("severity", ""),
                "error_message": error_info.get("message", ""),
                "error_description": error_info["description"],
            }
        )
    messages.info(request, message)
    return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
