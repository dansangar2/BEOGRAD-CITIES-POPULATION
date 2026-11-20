"""HTTP endpoints for configs."""

from __future__ import annotations
import tomllib
from ciudades_del_mundo.web.http import missing_data_as_404, query_params

from ciudades_del_mundo.services import scraping_config_tasks as scraping_config_tasks
from ciudades_del_mundo.web import http as http

from django.contrib import messages
from django.db import OperationalError, ProgrammingError
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.services.scraping_configs import bundled_city_merge_toml_paths, bundled_toml_config_paths, ensure_initial_scraping_configs, export_scraping_configs_to_toml, scraping_config_bootstrap_status, scraping_config_table_exists, sync_city_merges_from_toml, sync_scraping_configs_from_toml, upsert_scraping_config
from ciudades_del_mundo.web.task_codes import task_error_info
from ciudades_del_mundo.web.tasks import task_manager
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.forms import city_merges as _forms_city_merges
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import config_assets as _queries_config_assets
from ciudades_del_mundo.web.queries import configs as _queries_configs
from ciudades_del_mundo.web.queries import discovery as _queries_discovery
from ciudades_del_mundo.web.queries import tasks as _queries_tasks
from ciudades_del_mundo.web import view_support as _view_support


@missing_data_as_404
def config_list(request):
    """List SQL-backed scraping configs with operational actions."""
    try:
        bootstrap_status = ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError):
        bootstrap_status = scraping_config_bootstrap_status()
    context = {
        "config_table_url": reverse("ciudades_del_mundo:config_table"),
        "config_tasks_table_url": reverse("ciudades_del_mundo:config_tasks_table"),
        "config_validate_all_url": reverse("ciudades_del_mundo:start_all_config_task", kwargs={"action": "validate"}),
        "config_scrape_unpopulated_url": reverse("ciudades_del_mundo:start_all_config_task", kwargs={"action": "scrape-unpopulated"}),
        "config_scrape_all_url": reverse("ciudades_del_mundo:start_all_config_task", kwargs={"action": "scrape"}),
        "config_import_toml_url": reverse("ciudades_del_mundo:config_import_toml"),
        "config_bootstrap_url": reverse("ciudades_del_mundo:config_bootstrap"),
        "config_bootstrap_status": bootstrap_status.as_dict(),
    }
    return render(request, "ciudades_del_mundo/config_list.html", context)


@missing_data_as_404
def config_bootstrap(request):
    """Synchronously populate the SQL config table from bundled TOML files."""
    if request.method not in {"POST", "GET"}:
        return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
    try:
        status = ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError) as exc:
        return JsonResponse(
            {
                "ok": False,
                "ready": False,
                "error": _("La tabla de configuraciones todavía no está disponible: %(error)s") % {"error": exc},
            },
            status=503,
        )
    return JsonResponse({"ok": status.ready, **status.as_dict()})


@missing_data_as_404
def config_table(request):
    """Render the asynchronous SQL-backed config table partial."""
    try:
        ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError):
        pass
    rows = _queries_configs._filtered_config_summaries(query_params(request))
    return render(
        request,
        "ciudades_del_mundo/partials/config_table.html",
        {
            "rows": rows,
            "page_size": http._page_size(request, default=25),
        },
    )


@missing_data_as_404
def config_tasks_table(request):
    """Render the asynchronous recent config/task history table partial."""
    tasks = _queries_tasks._filtered_tasks(query_params(request), limit=100)
    return render(
        request,
        "ciudades_del_mundo/partials/config_tasks_table.html",
        _queries_tasks._task_table_context(tasks, page_size=http._page_size(request, default=10), compact=True),
    )


@missing_data_as_404
def config_new(request):
    """Create a new SQL-backed scraping config."""
    wants_json = http._wants_json(request)
    default_content = (
        "scrape_schema_version = 2\n\n"
        "[[pages]]\n"
        'source = "cities"\n'
        'path = [""]\n'
    )
    slug = ""
    content = default_content
    if request.method == "POST":
        try:
            slug = _queries_configs._normalize_config_slug(request.POST.get("slug", ""))
            if _queries_configs._config_exists(slug):
                raise ValueError(_("Ya existe una configuración para '%(slug)s'.") % {"slug": slug})
            content = _forms_scraping._config_content_from_request(request, slug, default_content, existing=False)
            _queries_configs._validate_config_text(slug, content)
            saved_config = upsert_scraping_config(slug, content)
            _forms_scraping._persist_manual_asset_overrides(slug, content)
            _queries_configs._try_materialize_configured_city_rows(saved_config)
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
        else:
            message = _("Configuración '%(slug)s' creada.") % {"slug": slug}
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "slug": slug,
                        "message": message,
                        "redirect_url": reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}),
                        "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                    }
                )
            messages.success(request, message)
            return redirect("ciudades_del_mundo:config_edit", slug=slug)
    return render(request, "ciudades_del_mundo/config_form.html", _forms_scraping._config_form_context("new", slug, content))


@missing_data_as_404
def config_edit(request, slug):
    """Edit a SQL-backed scraping config without starting scrape work automatically."""
    slug = _queries_configs._normalize_config_slug(slug)
    config_record = _queries_configs._config_record(slug)
    if config_record is None:
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    wants_json = http._wants_json(request)
    active_scrape = task_manager.latest_for_key(f"scrape:{slug}")
    content = config_record.content

    if request.method == "POST":
        try:
            content = _forms_scraping._config_content_from_request(request, slug, content, existing=True)
            _queries_configs._validate_config_text(slug, content)
            try:
                previous_legal_level = tomllib.loads(config_record.content).get("LEGAL_SUBDIVISION")
            except tomllib.TOMLDecodeError:
                previous_legal_level = None
            saved_data = tomllib.loads(content)
            saved_config = upsert_scraping_config(slug, content, source_path=config_record.source_path)
            _forms_scraping._persist_manual_asset_overrides(slug, content)
            _queries_configs._try_materialize_configured_city_rows(saved_config)
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
        else:
            message = _("Configuración '%(slug)s' guardada.") % {"slug": slug}
            if wants_json:
                legal_level_payload = {}
                if previous_legal_level != saved_data.get("LEGAL_SUBDIVISION"):
                    legal_level_payload["city_builder"] = _forms_city_merges._config_city_builder_payload(
                        saved_config.country_code, saved_data, enabled=True,
                    )
                return JsonResponse(
                    {
                        "ok": True,
                        "slug": slug,
                        "message": message,
                        "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                        **legal_level_payload,
                    }
                )
            messages.success(request, message)
            return redirect("ciudades_del_mundo:config_edit", slug=slug)

    return render(request, "ciudades_del_mundo/config_form.html", _forms_scraping._config_form_context("edit", slug, content, active_scrape))


@missing_data_as_404
def config_import_toml_slug(request, slug):
    """Import one ciudades_del_mundo/subdivisions/<slug>.toml seed into SQL."""
    wants_json = http._wants_json(request)
    slug = _queries_configs._normalize_config_slug(slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if not scraping_config_table_exists():
        error = _("La tabla de configuraciones todavía no está disponible. Ejecuta 'py manage.py migrate'.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if not bundled_toml_config_paths([slug]):
        error = _("No existe el fichero subdivisions/%(slug)s.toml.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            imported = sync_scraping_configs_from_toml(force=True, only_if_empty=False, slugs=[slug])
        else:
            with write_lock:
                imported = sync_scraping_configs_from_toml(force=True, only_if_empty=False, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo importar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    if imported:
        message = _("Configuración importada desde subdivisions/%(slug)s.toml.") % {"slug": slug}
    else:
        message = _("El fichero subdivisions/%(slug)s.toml no modificó la configuración.") % {"slug": slug}
    if wants_json:
        return JsonResponse({"ok": True, "message": message, "imported": bool(imported)})
    messages.success(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


@missing_data_as_404
def config_city_merge_import_toml(request, slug):
    """Import ciudades_del_mundo/cities_merge/<slug>.toml into existing SQL config [[cities]] blocks."""
    wants_json = http._wants_json(request)
    slug = _queries_configs._normalize_config_slug(slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if _queries_configs._config_record(slug) is None:
        error = _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    if not scraping_config_table_exists():
        error = _("La tabla de configuraciones todavía no está disponible. Ejecuta 'py manage.py migrate'.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if not bundled_city_merge_toml_paths([slug]):
        error = _("No se pudo importar el TOML.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            sync_city_merges_from_toml(force=True, slugs=[slug])
        else:
            with write_lock:
                sync_city_merges_from_toml(force=True, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo importar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    message = _("TOML importado correctamente.")
    if wants_json:
        return JsonResponse({"ok": True, "message": message, "imported": True})
    messages.success(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


@missing_data_as_404
def config_import_toml(request):
    """Import bundled subdivision TOML files into SQL in a background task."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if not _queries_browser._web_task_table_exists():
        error = _(
            "La tabla de tareas no existe todavía. Ejecuta 'py manage.py migrate' y recarga la página."
        )
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    if not scraping_config_table_exists():
        error = _("La tabla de configuraciones todavía no está disponible. Ejecuta 'py manage.py migrate'.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    label = _("Importar TOML de subdivisión")
    try:
        task = task_manager.start(
            key="config-import:toml",
            label=label,
            args=["sync_scraping_configs", "--force"],
        )
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise

    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "task_id": task.id,
                "label": label,
                "status": task.status,
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "summary_url": "",
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
            }
        )
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


@missing_data_as_404
def start_all_config_task(request, action):
    """Start a bulk validation or validate-then-scrape task for SQL configs."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if not _queries_browser._web_task_table_exists():
        error = _(
            "La tabla de tareas no existe todavía. Ejecuta 'py manage.py migrate' y recarga la página."
        )
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if action in {"scrape", "scrape-unpopulated"}:
        bootstrap_ready, bootstrap_error = scraping_config_tasks._ensure_bulk_configs_available_for_task()
        if not bootstrap_ready and bootstrap_error:
            if wants_json:
                return JsonResponse({"ok": False, "error": bootstrap_error}, status=503)
            messages.error(request, bootstrap_error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if action == "validate":
        eligible_slugs = _queries_configs._eligible_config_slugs_for_bulk("validate")
        if not eligible_slugs:
            error = _("No hay configuraciones pendientes de validar.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = "validate-config:all"
        label = _("Validar configuraciones pendientes (%(count)s)") % {"count": len(eligible_slugs)}
        args = ["validate_subdivision_configs", *eligible_slugs]
    elif action == "scrape-unpopulated":
        eligible_slugs = _queries_configs._eligible_config_slugs_for_bulk("scrape-unpopulated")
        if not eligible_slugs:
            error = _("No hay configuraciones disponibles para popular.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = "scrape:unpopulated"
        label = _("Popular configuraciones no populadas (%(count)s)") % {"count": len(eligible_slugs)}
        args = ["validate_and_scrape_configs", *eligible_slugs, "--no-download-assets", "--page-workers=4", "--country-workers=1"]
    elif action == "scrape":
        eligible_slugs = _queries_configs._eligible_config_slugs_for_bulk("scrape")
        if not eligible_slugs:
            error = _("No hay configuraciones disponibles para popular.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = "scrape:all"
        label = _("Popular configuraciones disponibles (%(count)s)") % {"count": len(eligible_slugs)}
        args = ["repopulate_configs", *eligible_slugs, "--no-download-assets", "--page-workers=4", "--country-workers=1"]
    else:
        error = _("Acción de configuración no soportada.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    try:
        task = task_manager.start(key=key, label=label, args=args)
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise

    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "task_id": task.id,
                "label": label,
                "status": task.status,
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "summary_url": "",
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
            }
        )
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


@missing_data_as_404
def start_config_task(request, slug, action):
    """Start validation or scraping for one SQL config."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    try:
        slug = _queries_configs._normalize_config_slug(slug)
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    config_ready, config_error = scraping_config_tasks._ensure_config_available_for_task(slug, import_from_toml=action == "scrape")
    if not config_ready:
        error = config_error or _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    if not _queries_browser._web_task_table_exists():
        error = _(
            "La tabla de tareas no existe todavía. Ejecuta 'py manage.py migrate' y recarga la página."
        )
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    row = _queries_configs._decorate_config_workflow_flags(_queries_configs._config_summary_for_slug(slug))
    current_status = row.get("status_filter") or _queries_configs._config_row_status(row)

    if action == "stop":
        task = row.get("active_task")
        if not task or not getattr(task, "is_active", False):
            error = _("No hay una tarea activa para parar en esta configuración.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        task = task_manager.cancel(task.id) or task
        label = _("Parar tarea: %(slug)s") % {"slug": slug}
        if wants_json:
            next_row = _queries_configs._decorate_config_workflow_flags(_queries_configs._config_summary_for_slug(slug))
            return JsonResponse(
                {
                    "ok": True,
                    "task_id": task.id,
                    "label": label,
                    "status": next_row.get("status_filter") or next_row.get("task_display_status") or "pending",
                    "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                    "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                    "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
                }
            )
        messages.info(request, _("Parada solicitada para '%(label)s'.") % {"label": task.label})
        return redirect("ciudades_del_mundo:task_detail", task_id=task.id)

    if action == "validate":
        if not row.get("can_validate"):
            error = _("Esta configuración ya está validada o populada. Modifica la configuración SQL antes de volver a validar.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = f"validate-config:{slug}"
        label = _("Validar configuración: %(slug)s") % {"slug": slug}
        args = ["validate_subdivision_configs", slug]
    elif action == "scrape":
        if not row.get("can_scrape"):
            error = _("Solo puedes popular una configuración Por validar, Validada, Populada o en Fallo.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = f"scrape:{slug}"
        if current_status == "populated":
            label = _("Re-popular datos: %(slug)s") % {"slug": slug}
            args = ["scrape_subdivisions_with_assets", slug, "--no-download-assets", "--page-workers=4"]
        else:
            label = _("Popular datos: %(slug)s") % {"slug": slug}
            if current_status == "validated":
                args = ["scrape_subdivisions_with_assets", slug, "--no-download-assets", "--page-workers=4"]
            else:
                args = ["validate_and_scrape_configs", slug, "--no-download-assets", "--page-workers=4"]
    elif action == "asset-corrections":
        if int(row.get("rows") or 0) <= 0:
            error = _("Primero debes popular el país antes de guardar correcciones de escudos y banderas.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
        try:
            config_record = _queries_configs._config_record(slug)
            if config_record is None:
                raise ValueError(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
            content = _forms_scraping._config_content_from_request(request, slug, config_record.content, existing=True)
            _queries_configs._validate_config_text(slug, content)
            saved_config = upsert_scraping_config(slug, content, source_path=config_record.source_path)
            _forms_scraping._persist_manual_asset_overrides(slug, content)
            _queries_configs._try_materialize_configured_city_rows(saved_config)
            export_scraping_configs_to_toml(force=True, slugs=[slug])
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
        except Exception as exc:  # noqa: BLE001 - keep AJAX responses JSON.
            error = _("No se pudieron guardar/exportar las correcciones: %(error)s") % {"error": exc}
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=500)
            messages.error(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
        key = f"asset-corrections:{slug}"
        label = _("Guardar correcciones de assets: %(slug)s") % {"slug": slug}
        args = ["apply_config_asset_overrides", slug]
    elif action == "clear":
        if not row.get("can_clear"):
            error = _("No hay datos")
            if current_status in _view_support.ACTIVE_CONFIG_OPERATION_STATUSES:
                error = _("No puedes limpiar mientras hay una operación activa en esta configuración.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = f"clear-config:{slug}"
        label = f"{_('Limpiar')}: {slug}"
        args = ["clear_config_data_with_assets", row.get("country_code") or slug]
    else:
        error = _("Acción de configuración no soportada.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    try:
        task = task_manager.start(key=key, label=label, args=args)
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "task_id": task.id,
                "label": label,
                "status": task.status,
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
            }
        )
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


@missing_data_as_404
def config_summary(request, slug):
    """Return an updated row summary for one config after an async task."""
    slug = _queries_configs._normalize_config_slug(slug)
    row = _queries_configs._config_summary_for_slug(slug)
    task = row.get("active_task")
    validate_task = row.get("validate_task")
    scrape_task = row.get("scrape_task")
    clear_task = row.get("clear_task")
    task_display_status = row.get("task_display_status") or _queries_configs._config_task_display_status(task)
    status_filter = _queries_configs._config_row_status({**row, "task_display_status": task_display_status})
    workflow_row = _queries_configs._decorate_config_workflow_flags({**row, "task_display_status": task_display_status})
    error_info = task_error_info(task, validation_error=row.get("error") or "")
    return JsonResponse(
        {
            "slug": row["slug"],
            "name": row["name"],
            "country_code": row["country_code"],
            "country_label": row["country_label"],
            "pages": row["pages"],
            "cities": row["cities"],
            "rows": row["rows"],
            "task_status": task_display_status,
            "status_filter": status_filter,
            "task_is_active": task.is_active if task else False,
            "task_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}) if task else "",
            "validate_task_status": _queries_configs._config_task_display_status(validate_task),
            "validate_task_is_active": validate_task.is_active if validate_task else False,
            "scrape_task_status": _queries_configs._config_task_display_status(scrape_task),
            "scrape_task_is_active": scrape_task.is_active if scrape_task else False,
            "clear_task_status": _queries_configs._config_task_display_status(clear_task),
            "clear_task_is_active": clear_task.is_active if clear_task else False,
            "can_validate": bool(workflow_row.get("can_validate")),
            "can_scrape": bool(workflow_row.get("can_scrape")),
            "can_clear": bool(workflow_row.get("can_clear")),
            "can_resume": bool(workflow_row.get("can_resume")),
            "can_stop": bool(workflow_row.get("can_stop")),
            "error": row.get("error") or "",
            "error_code": error_info["code"],
            "error_severity": error_info.get("severity", ""),
            "error_message": error_info.get("message", ""),
            "error_description": error_info["description"],
        }
    )


@missing_data_as_404
def config_editor_data(request, slug):
    """Return config content/manual data after the page shell has loaded."""
    slug = _queries_configs._normalize_config_slug(slug)
    record = _queries_configs._config_record(slug)
    if not record:
        if http._wants_json(request):
            return JsonResponse({"ok": False, "error": _("No existe la configuracion '%(slug)s'.") % {"slug": slug}}, status=404)
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    workflow = _queries_configs._decorate_config_workflow_flags(_queries_configs._config_summary_for_slug(slug))
    asset_editor_enabled = False
    content = record.content or ""
    return JsonResponse(
        {
            "ok": True,
            "content": content,
            "manual": _forms_scraping._parse_config_editor_data(slug, content, asset_editor_enabled=asset_editor_enabled),
            # Keep the editor-data endpoint fully JSON-serializable: workflow rows
            # contain WebTask model instances used by server-rendered templates.
            "workflow": _queries_configs._config_workflow_json_payload(workflow),
        }
    )


@missing_data_as_404
def config_source_entities(request, slug):
    """Return populated source entities for the manual city-unification UI."""
    slug = _queries_configs._normalize_config_slug(slug)
    if not _queries_configs._config_exists(slug):
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    country_code = _queries_configs._config_country_code_for_slug(slug)
    if request.GET.get("asset_options") == "1":
        return JsonResponse(
            _queries_config_assets._asset_assignment_options_payload(
                country_code,
                level=request.GET.get("level"),
                entity_id=request.GET.get("entity_id"),
            )
        )
    entities = _forms_scraping._source_entities_for_config(country_code)
    level_rows = _forms_scraping._source_level_filter_options(entities)
    entity_type_rows = _forms_scraping._source_entity_type_filter_options(entities)
    parent_rows = _forms_scraping._source_parent_filter_options(entities)
    parent_rows_by_level = {
        row["value"]: _forms_scraping._source_parent_filter_options(entities, level=row["value"])
        for row in level_rows
    }
    return JsonResponse(
        {
            "entities": entities,
            "levels": level_rows,
            "entity_types": entity_type_rows,
            "parents": parent_rows,
            "parents_by_level": parent_rows_by_level,
        }
    )


@missing_data_as_404
def config_generate_base(request, slug):
    """Generate a draft TOML config by discovering links on CityPopulation."""
    if request.method != "POST":
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
    slug = _queries_configs._normalize_config_slug(slug)
    try:
        content, log_lines = _queries_discovery._generate_citypopulation_config(slug)
        _queries_configs._validate_config_text(slug, content)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "content": content, "manual": _forms_scraping._parse_config_editor_data(slug, content), "log": "\n".join(log_lines)})


@missing_data_as_404
def config_ai_login(request, slug):
    """Redirect to the selected AI provider login page without storing credentials."""
    slug = _queries_configs._normalize_config_slug(slug)
    if not _queries_configs._config_exists(slug):
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    if not _view_support.AI_CONFIG_ENABLED or not _view_support.AI_PERSONAL_LOGIN_ENABLED:
        messages.error(request, _("La IA está deshabilitada por configuración."))
        return redirect(f"{reverse('ciudades_del_mundo:config_edit', kwargs={'slug': slug})}?tab=ai")

    provider = str(request.GET.get("provider") or "chatgpt").strip().lower()
    login_url = _view_support.AI_PROVIDER_LOGIN_URLS.get(provider)
    if not login_url:
        messages.error(request, _("Proveedor IA no soportado: %(provider)s") % {"provider": provider})
        return redirect(f"{reverse('ciudades_del_mundo:config_edit', kwargs={'slug': slug})}?tab=ai")
    return redirect(login_url)
