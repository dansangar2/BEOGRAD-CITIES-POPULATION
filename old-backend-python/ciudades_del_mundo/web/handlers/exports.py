"""HTTP endpoints for exports."""

from __future__ import annotations
from ciudades_del_mundo.services.nuevo_admin_builder import refresh_nuevo_admin_most_populated
from ciudades_del_mundo.domain.nuevo_admin_export import Workbook
from ciudades_del_mundo.web.http import missing_data_as_404

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.models import AdminArea, DerivedCountry
from ciudades_del_mundo.web import http as http
from ciudades_del_mundo.web.handlers import new_countries as _handlers_new_countries
from ciudades_del_mundo.web.queries import new_countries as _queries_new_countries
from collections import Counter
from datetime import datetime, timezone
import json

from ciudades_del_mundo.application import export_metrics as _application_export_metrics

import csv
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import SubdivisionGroup
from ciudades_del_mundo.infrastructure.excel import SimpleXlsxWriter
from ciudades_del_mundo.services.scraping_configs import export_city_merges_to_toml, export_scraping_configs_to_toml
from ciudades_del_mundo.services.derived_config_seeds import export_derived_subdivisions_to_toml, export_subdivision_groups_to_toml
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import configs as _queries_configs
from ciudades_del_mundo.web.queries import exports as _queries_exports
from ciudades_del_mundo.web.queries import groups as _queries_groups


@missing_data_as_404
def config_export_data_csv(request, slug):
    """Download all persisted AdminArea rows for this config/country as CSV."""
    slug = _queries_configs._normalize_config_slug(slug)
    config_record = _queries_configs._config_record(slug)
    if config_record is None:
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})

    country_code = _queries_configs._config_country_code_for_slug(slug)
    filename = f"{country_code or slug}_admin_areas.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")

    writer = csv.writer(response)
    headers = [
        "id",
        "country_code",
        "code",
        "name",
        "level",
        "parent_id",
        "parent_code",
        "parent_name",
        "parent_level",
        "children_count",
        "entity_type",
        "raw_entity_type",
        "city_merge_status",
        "area_km2",
        "density",
        "pop_latest",
        "pop_latest_date",
        "last_census_year",
        "url",
        "data_wd",
        "annotations",
        "most_populate_city_id",
        "flag_status",
        "flag_wikidata_id",
        "flag_commons_filename",
        "flag_local_path",
        "coat_status",
        "coat_wikidata_id",
        "coat_commons_filename",
        "coat_local_path",
        "created_at",
        "updated_at",
    ]
    writer.writerow(headers)

    areas = list(
        AdminArea.objects.filter(country_code=country_code)
        .select_related("parent", "most_populate_city")
        .annotate(children_count=Count("children"))
        .order_by("level", "parent_id", "name", "code")
    )
    assets_by_key = _queries_configs._config_export_asset_map(country_code)

    for area in areas:
        flag = assets_by_key.get(("admin_area", str(area.id), "flag"), {})
        coat = assets_by_key.get(("admin_area", str(area.id), "coat"), {})
        writer.writerow(
            [
                area.id,
                area.country_code,
                area.code,
                area.name,
                area.level,
                area.parent_id or "",
                area.parent.code if area.parent else "",
                area.parent.name if area.parent else "",
                area.parent.level if area.parent else "",
                area.children_count,
                area.entity_type or "",
                area.raw_entity_type or "",
                area.city_merge_status,
                area.area_km2 if area.area_km2 is not None else "",
                area.density if area.density is not None else "",
                area.pop_latest if area.pop_latest is not None else "",
                area.pop_latest_date.isoformat() if area.pop_latest_date else "",
                area.last_census_year if area.last_census_year is not None else "",
                area.url or "",
                area.data_wd or "",
                area.annotations or "",
                area.most_populate_city_id or "",
                flag.get("status", ""),
                flag.get("wikidata_id", ""),
                flag.get("commons_filename", ""),
                flag.get("local_path", ""),
                coat.get("status", ""),
                coat.get("wikidata_id", ""),
                coat.get("commons_filename", ""),
                coat.get("local_path", ""),
                area.created_at.isoformat(sep=" ", timespec="seconds") if area.created_at else "",
                area.updated_at.isoformat(sep=" ", timespec="seconds") if area.updated_at else "",
            ]
        )
    return response


@missing_data_as_404
def config_export_toml(request, slug):
    """Export one SQL-backed config to ciudades_del_mundo/subdivisions/<slug>.toml."""
    wants_json = http._wants_json(request)
    slug = _queries_configs._normalize_config_slug(slug)
    config_record = _queries_configs._config_record(slug)
    if config_record is None:
        error = _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    try:
        exported = export_scraping_configs_to_toml(force=True, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        if exported:
            message = _("Configuración exportada a subdivisions/%(slug)s.toml.") % {"slug": slug}
            if wants_json:
                return JsonResponse({"ok": True, "message": message, "exported": True})
            messages.success(request, message)
        else:
            message = _("El fichero subdivisions/%(slug)s.toml ya estaba actualizado.") % {"slug": slug}
            if wants_json:
                return JsonResponse({"ok": True, "message": message, "exported": False})
            messages.info(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


@missing_data_as_404
def config_city_merge_export_toml(request, slug):
    """Export only [[cities]] unification blocks to ciudades_del_mundo/cities_merge/<slug>.toml."""
    wants_json = http._wants_json(request)
    slug = _queries_configs._normalize_config_slug(slug)
    config_record = _queries_configs._config_record(slug)
    if config_record is None:
        error = _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    try:
        export_city_merges_to_toml(force=True, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        message = _("TOML exportado correctamente.")
        if wants_json:
            return JsonResponse({"ok": True, "message": message, "exported": True})
        messages.success(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


@missing_data_as_404
def new_country_detail_export_excel(request, source_country_code="", country_slug=""):
    """Download the visible new-country config hierarchy as an Excel workbook."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country = _queries_new_countries.get_derived_country(country_slug)
    _handlers_new_countries._validate_new_country_source_or_404(country, source_country_code)
    rows = _queries_new_countries._new_country_config_rows(
        list(country.configs.order_by("name", "slug")),
        include_assigned_subdivisions=True,
    )
    workbook, export_rows, summary = _new_country_detail_export_workbook(country, rows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = _application_export_metrics._safe_excel_filename(f"{country.slug}_{stamp}.xlsx")
    source_code = query_parameters.normalize_country_key(country.source_country_code) or "_"
    output_path = Path(settings.BASE_DIR) / "excels" / "new-countries" / source_code / filename
    SimpleXlsxWriter().write(workbook, output_path)
    log_path = _write_new_country_detail_export_log(
        country,
        request=request,
        output_path=output_path,
        rows=export_rows,
        summary=summary,
    )
    response = HttpResponse(
        output_path.read_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Export-Log-Path"] = str(log_path)
    return response


@missing_data_as_404
def new_country_detail_export_excel_legacy(request, country_slug):
    """Redirect old new-country export URLs to the source-qualified route."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country = _queries_new_countries.get_derived_country(country_slug)
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return redirect(_queries_new_countries._new_country_detail_export_excel_url(country))
    return new_country_detail_export_excel(request, country_slug=country_slug)


@missing_data_as_404
def derived_subdivision_export_toml(request, slug: str):
    """Export derived subdivision SQL definitions to one TOML file for the country."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    country_code = query_parameters.normalize_country_key(slug)
    if not country_code:
        error = _("No se pudo identificar el pais de la exportacion.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    try:
        exported_count = export_derived_subdivisions_to_toml(
            force=True,
            country_codes=[country_code],
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a toast/form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        if exported_count:
            message = _("Subdivisiones exportadas a subdivision_groups/subdivisions/%(country)s.toml.") % {
                "country": country_code
            }
        else:
            message = _("No hay subdivisiones SQL para exportar en %(country)s.") % {"country": country_code}
        if wants_json:
            status = 200 if exported_count else 404
            return JsonResponse({"ok": bool(exported_count), "message": message, "error": message}, status=status)
        if exported_count:
            messages.success(request, message)
        else:
            messages.warning(request, message)
    return redirect("ciudades_del_mundo:derived_subdivision_list")


@missing_data_as_404
def group_export_toml(request, slug):
    """Export the source country's SQL-backed subdivision groups to one TOML bundle."""
    wants_json = http._wants_json(request)
    route_slug = str(slug or "").strip().lower()
    group = SubdivisionGroup.objects.filter(slug=route_slug).first()
    if group:
        country_codes = sorted(_queries_groups._subdivision_group_record_country_codes(group))
        country_code = country_codes[0] if country_codes else "unknown"
        export_slugs = [group.slug]
        redirect_url = reverse("ciudades_del_mundo:group_edit", kwargs={"slug": group.slug})
    else:
        country_code = query_parameters.normalize_country_key(route_slug)
        country_groups = _queries_groups._subdivision_groups_for_country(country_code)
        export_slugs = [item.slug for item in country_groups]
        redirect_url = reverse("ciudades_del_mundo:group_list")
        if not export_slugs:
            error = _("No hay grupos SQL para exportar en '%(country)s'.") % {"country": country_code}
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=404)
            messages.error(request, error)
            return redirect("ciudades_del_mundo:group_list")
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect_url
    try:
        exported = export_subdivision_groups_to_toml(force=True, slugs=export_slugs)
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        if exported:
            message = _("Grupos exportados a subdivision_groups/groups/%(country)s.toml.") % {"country": country_code}
        else:
            message = _("El fichero subdivision_groups/groups/%(country)s.toml ya estaba actualizado.") % {"country": country_code}
        if wants_json:
            return JsonResponse({"ok": True, "message": message, "exported": bool(exported)})
        messages.success(request, message)
    return redirect_url


@missing_data_as_404
def group_city_export_toml(request, country_code):
    """Export the city panel scaffold as an empty country TOML bundle."""
    country_code = query_parameters.normalize_country_key(country_code)
    if request.method != "POST":
        return redirect("ciudades_del_mundo:group_list")
    lines = [f"source_country_code = {_queries_browser._toml_string(country_code)}", "", "[cities]"]
    response = HttpResponse("\n".join(lines).rstrip() + "\n", content_type="application/toml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{country_code}_cities.toml"'
    return response


def _write_new_country_detail_export_log(
    country: DerivedCountry,
    *,
    request,
    output_path: Path,
    rows: list[dict],
    summary: dict,
) -> Path:
    source_code = query_parameters.normalize_country_key(country.source_country_code) or "_"
    log_path = Path(settings.BASE_DIR) / ".web_export_logs" / "new-countries" / source_code / f"{country.slug}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "route": request.get_full_path() if request is not None else "",
        "country_slug": country.slug,
        "country_name": country.name,
        "source_country_code": source_code,
        "xlsx_path": str(output_path),
        "rows": len(rows),
        "levels": list(summary.get("levels") or []),
        "total_area_km2": summary.get("total_area_km2"),
        "total_population": summary.get("total_population"),
        "legal_subdivision_count": summary.get("legal_subdivision_count"),
        "row_kinds": dict(Counter(row.get("row_kind") or "" for row in rows)),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return log_path


def _new_country_detail_export_workbook(
    country: DerivedCountry,
    rows: list[dict],
) -> tuple[Workbook, list[dict], dict]:
    materialized_root = _queries_exports._new_country_detail_export_root(country, rows)
    if materialized_root is not None:
        refresh_nuevo_admin_most_populated(materialized_root.country_code)
        return _queries_exports._new_country_detail_materialized_export_workbook(country, materialized_root)

    export_rows, summary = _queries_exports._new_country_detail_export_rows(country, rows)
    hierarchy_rows, merged_cells = _queries_exports._new_country_detail_export_sheet_rows_and_merges(export_rows)
    sheets = [
        _application_export_metrics._new_country_detail_export_sheet(
            _("Jerarquia"),
            hierarchy_rows,
            table_name="Jerarquia",
            merged_cells=merged_cells,
        ),
    ]
    return (
        Workbook(
            sheets=tuple(sheets),
            properties={"title": f"{country.name} - new country export"},
        ),
        export_rows,
        summary,
    )
