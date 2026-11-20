"""SQL mutations composed by handlers; no HTTP or presentation dependencies."""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterable

from ciudades_del_mundo.application import query_parameters
from ciudades_del_mundo.models import DerivedSubdivision, NuevoAdminArea, SubdivisionGroup
import re
import tomllib


def _rename_subdivision_group_content_references(
    *,
    country_code: str,
    old_key: str,
    new_key: str,
    exclude_slugs: set[str] | None = None,
    group_country_codes: Callable[[SubdivisionGroup], Iterable[str]],
) -> int:
    country_code = query_parameters.normalize_country_key(country_code)
    exclude_slugs = {str(slug) for slug in (exclude_slugs or set()) if str(slug)}
    if not country_code or not old_key or not new_key or old_key == new_key:
        return 0
    pattern = re.compile(rf"((?<![A-Za-z0-9_])|(?<=[\\][nr])){re.escape(old_key)}(?![A-Za-z0-9_])")
    updated = 0
    for group in SubdivisionGroup.objects.order_by("slug"):
        if group.slug in exclude_slugs:
            continue
        content = group.content or ""
        if old_key not in content:
            continue
        if country_code not in group_country_codes(group):
            continue
        new_content, replacements = pattern.subn(new_key, content)
        if not replacements:
            continue
        group.content = new_content
        group.save(update_fields=["content", "updated_at"])
        updated += 1
    return updated


def _rename_derived_subdivision_group_references(
    *, country_code: str, old_key: str, new_key: str,
    replace_references: Callable[..., bool], render_content: Callable[[dict], str],
) -> int:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code or not old_key or not new_key or old_key == new_key:
        return 0
    updated = 0
    records = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    for record in records:
        try:
            data = tomllib.loads(record.content or "")
        except tomllib.TOMLDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if not replace_references(data, old_key=old_key, new_key=new_key):
            continue
        record.content = render_content(data)
        record.save(update_fields=["content", "updated_at"])
        updated += 1
    return updated


def _delete_materialized_rows_for_new_country_config(
    *, country_code: str, code_candidates: Collection[str], materialized_id: str | None = None,
) -> int:
    if not country_code or not code_candidates:
        return 0
    qs = NuevoAdminArea.objects.filter(country_code=country_code).exclude(parent__isnull=True)
    root_ids = set(qs.filter(code__in=code_candidates).values_list("id", flat=True))
    if materialized_id is not None:
        root_ids.add(materialized_id)
    if not root_ids:
        return 0
    all_rows = NuevoAdminArea.objects.filter(country_code=country_code)
    return _delete_materialized_branches(all_rows, root_ids)


def _delete_materialized_rows_for_derived_subdivision(*, country_code: str, code_candidates: Collection[str]) -> int:
    if not country_code or not code_candidates:
        return 0
    qs = NuevoAdminArea.objects.filter(country_code=country_code)
    root_ids = set(qs.filter(code__in=code_candidates).values_list("id", flat=True))
    if not root_ids:
        return 0
    return _delete_materialized_branches(qs, root_ids)


def _delete_materialized_branches(qs, root_ids: set[str]) -> int:
    """Delete only the selected branches, guarding repeated/cyclic ancestors."""
    ids_to_delete = set(root_ids)
    frontier = set(root_ids)
    while frontier:
        child_ids = set(qs.filter(parent_id__in=frontier).values_list("id", flat=True))
        child_ids -= ids_to_delete
        if not child_ids:
            break
        ids_to_delete.update(child_ids)
        frontier = child_ids
    deleted, _details = NuevoAdminArea.objects.filter(id__in=ids_to_delete).delete()
    return int(deleted or 0)


def create_derived_subdivision_clone(values: dict) -> DerivedSubdivision:
    return DerivedSubdivision.objects.create(**values)
