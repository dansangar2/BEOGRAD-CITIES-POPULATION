"""Select SQL configurations and derive their workflow presentation."""

from __future__ import annotations


from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import tasks as _queries_tasks
from ciudades_del_mundo.web import view_support as _view_support


def _try_materialize_configured_city_rows(config_record: ScrapingConfig) -> None:
    """Best-effort DB refresh for [[cities]] edited after a country was populated."""
    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            materialize_configured_cities_for_record(config_record)
        else:
            with write_lock:
                materialize_configured_cities_for_record(config_record)
    except (OperationalError, ProgrammingError, ValueError):
        return


def _config_export_asset_map(country_code: str) -> dict[tuple[str, str, str], dict[str, str]]:
    """Return visual assets keyed by (entity_type, entity_key, kind)."""
    if not country_code or not visual_asset_tables_exist():
        return {}
    assets: dict[tuple[str, str, str], dict[str, str]] = {}
    try:
        queryset = VisualAsset.objects.filter(
            country_code=country_code,
            entity_type="admin_area",
            kind__in=("flag", "coat"),
        ).only(
            "entity_type",
            "entity_key",
            "kind",
            "status",
            "wikidata_id",
            "commons_filename",
            "local_path",
        )
        for asset in queryset:
            assets[(asset.entity_type, str(asset.entity_key), asset.kind)] = {
                "status": asset.status or "",
                "wikidata_id": asset.wikidata_id or "",
                "commons_filename": asset.commons_filename or "",
                "local_path": asset.local_path or "",
            }
    except (OperationalError, ProgrammingError):
        return {}
    return assets


def _config_summaries(limit: int | None = None) -> list[dict]:
    row_counts = _config_row_counts()
    if scraping_config_table_exists():
        records = ScrapingConfig.objects.order_by("slug")
        if limit:
            records = records[:limit]
        return [_config_summary_from_record(record, row_counts=row_counts) for record in records]
    return []


def _filtered_config_summaries(params) -> list[dict]:
    rows = _config_summaries()
    for row in rows:
        _decorate_config_workflow_flags(row)
    query = str(params.get("q") or "").strip().casefold()
    status = str(params.get("status") or "").strip().casefold()
    if query:
        rows = [
            row
            for row in rows
            if query in " ".join(
                [
                    str(row.get("country_label") or ""),
                    str(row.get("country_code") or ""),
                ]
            ).casefold()
        ]
    if status == "unpopulated":
        rows = [row for row in rows if not row.get("rows") and row.get("status_filter") != "populated"]
    elif status:
        rows = [row for row in rows if str(row.get("status_filter") or "pending") == status]
    return rows


def _config_row_status(row: dict) -> str:
    """Return the normalized filter/sort status for one config row."""
    if row.get("error"):
        return "failed"
    status = str(row.get("task_display_status") or "").strip().casefold()
    if status in {"validating", "validated", "populating", "populated", "clearing"}:
        return status
    if status in {"failed", "invalid"}:
        return "failed"
    if status in {"running", "queued"}:
        return status
    return "pending"


def _can_validate_config_status(status: str) -> bool:
    """Validation is available from pending/failed states only."""
    return str(status or "pending").strip().casefold() in {"pending", "failed"}


def _can_scrape_config_status(status: str) -> bool:
    """Population is available from pending/failed/validated states, and re-population from populated."""
    return str(status or "pending").strip().casefold() in {"pending", "failed", "validated", "populated"}


def _can_validate_config_row(row: dict, status: str) -> bool:
    """Return whether the row can run validation in its current workflow state."""
    return _can_validate_config_status(status)


def _can_scrape_config_row(row: dict, status: str) -> bool:
    """Return whether the row can run population in its current workflow state."""
    return _can_scrape_config_status(status)


def _can_clear_config_row(row: dict, status: str) -> bool:
    """Clearing is available when data exists and no config operation is active."""
    if str(status or "").strip().casefold() in _view_support.ACTIVE_CONFIG_OPERATION_STATUSES:
        return False
    try:
        return int(row.get("rows") or 0) > 0
    except (TypeError, ValueError):
        return False


def _config_task_key_for_row(row: dict) -> str:
    task = row.get("active_task") or row.get("clear_task") or row.get("scrape_task") or row.get("validate_task")
    return str(getattr(task, "key", "") or "")


def _decorate_config_workflow_flags(row: dict) -> dict:
    status = _config_row_status(row)
    row["status_filter"] = status
    error_info = task_error_info(row.get("active_task"), validation_error=row.get("error") or "")
    row["error_code"] = error_info["code"]
    row["error_severity"] = error_info.get("severity", "")
    row["error_message"] = error_info.get("message", "")
    row["error_description"] = error_info["description"]
    row["can_validate"] = _can_validate_config_row(row, status)
    row["can_scrape"] = _can_scrape_config_row(row, status)
    row["can_resume"] = False
    row["can_clear"] = _can_clear_config_row(row, status)
    task = row.get("active_task")
    row["can_stop"] = status in _view_support.ACTIVE_CONFIG_OPERATION_STATUSES and bool(
        task and getattr(task, "is_active", False)
    )
    return row


def _config_workflow_json_payload(row: dict) -> dict:
    """Return the config workflow fields that are safe to expose as JSON."""
    task = row.get("active_task")
    validate_task = row.get("validate_task")
    scrape_task = row.get("scrape_task")
    clear_task = row.get("clear_task")
    return {
        "status_filter": row.get("status_filter") or _config_row_status(row),
        "task_status": row.get("task_display_status") or _config_task_display_status(task),
        "task_is_active": bool(getattr(task, "is_active", False)) if task else False,
        "task_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}) if task else "",
        "validate_task_status": _config_task_display_status(validate_task),
        "validate_task_is_active": bool(getattr(validate_task, "is_active", False)) if validate_task else False,
        "scrape_task_status": _config_task_display_status(scrape_task),
        "scrape_task_is_active": bool(getattr(scrape_task, "is_active", False)) if scrape_task else False,
        "clear_task_status": _config_task_display_status(clear_task),
        "clear_task_is_active": bool(getattr(clear_task, "is_active", False)) if clear_task else False,
        "can_validate": bool(row.get("can_validate")),
        "can_scrape": bool(row.get("can_scrape")),
        "can_clear": bool(row.get("can_clear")),
        "can_resume": bool(row.get("can_resume")),
        "can_stop": bool(row.get("can_stop")),
        "error_code": row.get("error_code") or "",
        "error_severity": row.get("error_severity") or "",
        "error_message": row.get("error_message") or "",
        "error_description": row.get("error_description") or "",
    }


def _eligible_config_slugs_for_bulk(action: str) -> list[str]:
    rows = []
    for row in _config_summaries():
        _decorate_config_workflow_flags(row)
        rows.append(row)
    if action == "validate":
        return [str(row["slug"]) for row in rows if row.get("can_validate")]
    if action == "scrape":
        return [str(row["slug"]) for row in rows if row.get("can_scrape")]
    if action == "scrape-unpopulated":
        return [
            str(row["slug"])
            for row in rows
            if row.get("can_scrape") and not row.get("rows") and str(row.get("status_filter") or "") != "populated"
        ]
    return []


def _config_summary_for_slug(
    slug: str,
    *,
    row_counts: dict[str, int] | None = None,
) -> dict:
    row_counts = row_counts or _config_row_counts()
    record = _config_record(slug)
    if record:
        return _config_summary_from_record(record, row_counts=row_counts)

    error = _(
        "No existe la configuración SQL '%(slug)s'. Ejecuta "
        "'py manage.py sync_scraping_configs %(slug)s' si debe importarse desde seeds TOML."
    ) % {"slug": slug}
    country_code = slug
    raw_name = slug
    validate_task = task_manager.latest_for_key(f"validate-config:{slug}")
    scrape_task = task_manager.latest_for_key(f"scrape:{slug}")
    clear_task = task_manager.latest_for_key(f"clear-config:{slug}")
    active_task = _latest_config_task(
        slug,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
    )
    bulk_task, bulk_status = _latest_bulk_config_progress(slug)
    if bulk_task and (not active_task or bulk_task.created_at > active_task.created_at):
        active_task = bulk_task
        task_display_status = bulk_status
    else:
        task_display_status = _config_task_display_status(active_task)
    if error:
        task_display_status = "failed"
    return {
        "slug": slug,
        "name": raw_name,
        "country_code": country_code,
        "country_label": _queries_browser._display_name(raw_name, country_code, country_code=country_code),
        "pages": 0,
        "cities": 0,
        "rows": row_counts.get(country_code, 0),
        "is_valid": False,
        "active_task": active_task,
        "task_display_status": task_display_status,
        "validate_task": validate_task,
        "scrape_task": scrape_task,
        "clear_task": clear_task,
        "error": error,
    }


def _latest_bulk_config_progress(slug: str, *, record: ScrapingConfig | None = None):
    """Return the latest bulk task/progress affecting a config row, if any."""
    candidates = [
        task_manager.latest_for_key("scrape:all"),
        task_manager.latest_for_key("scrape:unpopulated"),
        task_manager.latest_for_key("validate-config:all"),
    ]
    if record is not None:
        updated_at = getattr(record, "updated_at", None)
        candidates = [
            task for task in candidates
            if task and (not updated_at or not task.created_at or task.created_at >= updated_at)
        ]
    else:
        candidates = [task for task in candidates if task]
    candidates.sort(key=lambda task: task.created_at, reverse=True)
    for task in candidates:
        progress = read_task_config_progress(task.id)
        item = progress.get(slug) if isinstance(progress, dict) else None
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status:
            terminal_status = _queries_tasks._terminal_config_progress_status(task)
            if terminal_status and not getattr(task, "is_active", False):
                status = _queries_tasks._progress_status_after_terminal_task(status, terminal_status)
            return task, status
    return None, ""


def _config_task_display_status(task) -> str:
    """Return the normalized per-config state for the ESTADO column."""
    if not task:
        return "pending"
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
    if status in {"running", "queued"}:
        if key.startswith("scrape:"):
            return "populating"
        if key.startswith("validate-config:"):
            return "validating"
        if key.startswith("clear-config:"):
            return "clearing"
    if status == "cancelled":
        return "pending"
    if status == "failed":
        return "failed"
    return status or "pending"


def _config_task_if_current(task, record: ScrapingConfig):
    """Ignore task statuses launched before the last config edit.

    Saved config content invalidates the previous validation/scrape result
    because that result belongs to older content. This keeps /configs/ from
    showing a stale "correcto" after a user modifies the configuration.
    """
    if not task:
        return None
    updated_at = getattr(record, "updated_at", None)
    created_at = getattr(task, "created_at", None)
    if updated_at and created_at and created_at < updated_at:
        return None
    return task


def _latest_terminal_config_task(*tasks, ignored_task=None):
    terminal = [
        task
        for task in tasks
        if task
        and not _queries_tasks._task_is_ignored(task, ignored_task)
        and str(getattr(task, "status", "") or "") in {"succeeded", "failed"}
    ]
    if not terminal:
        return None
    return max(terminal, key=lambda task: task.created_at)


def _stable_config_status(
    record: ScrapingConfig | None,
    *,
    rows: int,
    validate_task=None,
    scrape_task=None,
    clear_task=None,
    ignored_task=None,
) -> str:
    """Return the durable state after ignoring a cancelled/current active task."""
    if record and record.validation_error and not record.is_valid:
        return "failed"

    latest_terminal = _latest_terminal_config_task(
        validate_task,
        scrape_task,
        clear_task,
        ignored_task=ignored_task,
    )
    if not latest_terminal:
        # Existing rows can belong to an older source configuration. Keep them,
        # but do not present that older scrape as validating the current config.
        if record and rows > 0:
            previous_scrape = task_manager.latest_for_key(f"scrape:{record.slug}")
            if previous_scrape and _config_task_if_current(previous_scrape, record) is None:
                return "pending"
        return "populated" if rows > 0 else "pending"

    key = str(getattr(latest_terminal, "key", "") or "")
    status = str(getattr(latest_terminal, "status", "") or "")
    if status == "failed":
        return "failed"
    if key.startswith("scrape:"):
        return "populated" if rows > 0 else "pending"
    if key.startswith("validate-config:"):
        return "validated"
    if key.startswith("clear-config:"):
        return "pending"
    return "pending"


def _cancelled_config_status(
    slug: str,
    record: ScrapingConfig | None,
    *,
    rows: int,
    validate_task=None,
    scrape_task=None,
    clear_task=None,
    task=None,
) -> str:
    """Map a cancelled task back to the workflow state it should reveal."""
    stable = _stable_config_status(
        record,
        rows=rows,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
        ignored_task=task,
    )
    key = str(getattr(task, "key", "") or "")
    if key.startswith("scrape:"):
        progress_status = _queries_tasks._task_progress_status_for_slug(task, slug)
        if progress_status == "populating" or _queries_tasks._task_runs_direct_scrape(task):
            return "validated"
    return stable


def _config_task_display_status_for_row(
    slug: str,
    record: ScrapingConfig | None,
    *,
    rows: int,
    active_task=None,
    validate_task=None,
    scrape_task=None,
    clear_task=None,
) -> str:
    """Return the row state following the explicit config lifecycle."""
    if active_task:
        task_status = str(getattr(active_task, "status", "") or "")
        display_status = _config_task_display_status(active_task)
        if task_status in {"running", "queued"}:
            if str(getattr(active_task, "key", "") or "").startswith("scrape:"):
                progress_status = _queries_tasks._task_progress_status_for_slug(active_task, slug)
                if progress_status in {"validating", "populating"}:
                    return progress_status
                stable_before_scrape = _stable_config_status(
                    record,
                    rows=rows,
                    validate_task=validate_task,
                    scrape_task=scrape_task,
                    clear_task=clear_task,
                    ignored_task=active_task,
                )
                if not _queries_tasks._task_runs_direct_scrape(active_task) and stable_before_scrape != "validated":
                    return "validating"
            return display_status
        if task_status == "cancelled":
            return _cancelled_config_status(
                slug,
                record,
                rows=rows,
                validate_task=validate_task,
                scrape_task=scrape_task,
                clear_task=clear_task,
                task=active_task,
            )
        if task_status == "failed":
            return "failed"
        if task_status == "succeeded":
            return display_status

    return _stable_config_status(
        record,
        rows=rows,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
    )


def _config_summary_from_record(record: ScrapingConfig, *, row_counts: dict[str, int] | None = None) -> dict:
    row_counts = row_counts or _config_row_counts()
    slug = record.slug
    country_code = record.country_code or slug
    raw_name = record.name or slug
    validate_task = _config_task_if_current(task_manager.latest_for_key(f"validate-config:{slug}"), record)
    scrape_task = _config_task_if_current(task_manager.latest_for_key(f"scrape:{slug}"), record)
    clear_task = _config_task_if_current(task_manager.latest_for_key(f"clear-config:{slug}"), record)
    active_task = _latest_config_task(
        slug,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
    )
    rows = row_counts.get(country_code, 0)
    bulk_task, bulk_status = _latest_bulk_config_progress(slug, record=record)
    if bulk_task and (not active_task or bulk_task.created_at > active_task.created_at):
        active_task = bulk_task
        task_display_status = bulk_status
    else:
        task_display_status = _config_task_display_status_for_row(
            slug,
            record,
            rows=rows,
            active_task=active_task,
            validate_task=validate_task,
            scrape_task=scrape_task,
            clear_task=clear_task,
        )
    return {
        "slug": slug,
        "name": raw_name,
        "country_code": country_code,
        "country_label": _queries_browser._display_name(raw_name, country_code, country_code=country_code),
        "pages": record.pages_count,
        "cities": record.cities_count,
        "rows": rows,
        "is_valid": bool(record.is_valid),
        "active_task": active_task,
        "task_display_status": task_display_status,
        "validate_task": validate_task,
        "scrape_task": scrape_task,
        "clear_task": clear_task,
        "error": record.validation_error if not record.is_valid else "",
    }


def _config_row_counts() -> dict[str, int]:
    return {
        row["country_code"]: row["total"]
        for row in area_selectors.visible_admin_areas().values("country_code").annotate(total=Count("id"))
    }


def _latest_config_task(slug: str, *, validate_task=None, scrape_task=None, clear_task=None):
    tasks = [task for task in (validate_task, scrape_task, clear_task) if task]
    if not tasks:
        return None
    return max(tasks, key=lambda task: task.created_at)


def _normalize_config_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    if not _view_support.CONFIG_SLUG_RE.fullmatch(slug):
        raise ValueError(_("El slug de configuracion solo puede usar letras, numeros, guion y guion bajo."))
    return slug


def _config_record(slug: str) -> ScrapingConfig | None:
    if scraping_config_table_exists():
        return ScrapingConfig.objects.filter(slug=slug).first()
    return None


def _config_exists(slug: str) -> bool:
    return _config_record(slug) is not None


def _config_country_code_for_slug(slug: str) -> str:
    record = _config_record(slug)
    if not record:
        return slug
    if record.country_code:
        return record.country_code
    try:
        data = tomllib.loads(record.content)
    except tomllib.TOMLDecodeError:
        return slug
    return str(data.get("country_code") or slug or "")


def _validate_config_text(slug: str, content: str) -> None:
    try:
        data = tomllib.loads(content)
        pages = parse_pages(
            data.get("pages"),
            slug=slug,
            schema_version=int(data.get("scrape_schema_version", 1)),
        )
        if not pages:
            raise ValueError(_("La configuracion debe definir al menos una pagina."))
        RepresentationConfig.from_mapping(data.get("representation"))
        parse_cities(data.get("cities"))
        parse_entity_merges(data.get("entity_merges", data.get("merge_entities")))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    except Exception as exc:
        raise ValueError(_("Configuracion invalida: %(error)s") % {"error": exc}) from exc


import tomllib
from django.db import OperationalError, ProgrammingError
from django.db.models import Count
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.domain import RepresentationConfig, parse_cities, parse_entity_merges, parse_pages
from ciudades_del_mundo.models import ScrapingConfig, VisualAsset
from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.services.scraping_configs import scraping_config_table_exists
from ciudades_del_mundo.services.visual_assets import visual_asset_tables_exist
from ciudades_del_mundo.services.configured_city_materializer import materialize_configured_cities_for_record
from ciudades_del_mundo.web.task_codes import task_error_info
from ciudades_del_mundo.web.task_progress import read_task_config_progress
from ciudades_del_mundo.web.tasks import task_manager
