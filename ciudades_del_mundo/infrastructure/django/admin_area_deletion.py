"""Fast AdminArea deletion helpers for Django persistence adapters.

The helpers explicitly preserve the relationship effects expected by the
project when deleting source AdminArea rows: dependent foreign keys are set to
NULL and many-to-many through rows are removed before the source rows are
removed in small SQL batches.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Iterable, Sequence

from django.db import connection, transaction

from ciudades_del_mundo.models import AdminArea, NuevoAdminArea


SQLITE_SAFE_DELETE_BATCH_SIZE = 500


@dataclass(frozen=True)
class AdminAreaDeletionProgress:
    """Progress emitted after one raw AdminArea deletion batch."""

    country_code: str
    total: int
    deleted: int
    batch_deleted: int
    batch_number: int
    references_cleared: int


def delete_admin_area_country(
    country_code: str,
    *,
    batch_size: int | None = None,
    on_batch: Callable[[AdminAreaDeletionProgress], None] | None = None,
) -> tuple[int, int]:
    """Delete all AdminArea rows for one source country in safe SQL batches."""
    effective_batch_size = _effective_batch_size(batch_size)
    total = AdminArea.objects.filter(country_code=country_code).count()
    deleted = 0
    batch_number = 0

    while True:
        pk_batch = list(
            AdminArea.objects.filter(country_code=country_code)
            .order_by("-level", "pk")
            .values_list("pk", flat=True)[:effective_batch_size]
        )
        if not pk_batch:
            break

        batch_number += 1
        with transaction.atomic():
            references_cleared = clear_admin_area_references(pk_batch)
            batch_deleted = _raw_delete_admin_area_ids(pk_batch)
        deleted += batch_deleted
        if on_batch:
            on_batch(
                AdminAreaDeletionProgress(
                    country_code=country_code,
                    total=total,
                    deleted=deleted,
                    batch_deleted=batch_deleted,
                    batch_number=batch_number,
                    references_cleared=references_cleared,
                )
            )

    return total, deleted


def delete_admin_area_ids(
    ids: Iterable[str],
    *,
    batch_size: int | None = None,
    country_code: str = "",
    on_batch: Callable[[AdminAreaDeletionProgress], None] | None = None,
) -> int:
    """Delete specific AdminArea primary keys after clearing dependent links."""
    effective_batch_size = _effective_batch_size(batch_size)
    pending_ids = list(dict.fromkeys(str(item) for item in ids if item))
    total = len(pending_ids)
    deleted = 0

    for batch_number, pk_batch in enumerate(_chunks(pending_ids, effective_batch_size), start=1):
        with transaction.atomic():
            references_cleared = clear_admin_area_references(pk_batch)
            batch_deleted = _raw_delete_admin_area_ids(pk_batch)
        deleted += batch_deleted
        if on_batch:
            on_batch(
                AdminAreaDeletionProgress(
                    country_code=country_code,
                    total=total,
                    deleted=deleted,
                    batch_deleted=batch_deleted,
                    batch_number=batch_number,
                    references_cleared=references_cleared,
                )
            )

    return deleted


def clear_admin_area_references(ids: Sequence[str]) -> int:
    """Apply the relevant SET_NULL/M2M cleanup for AdminArea ids."""
    pk_batch = [str(item) for item in ids if item]
    if not pk_batch:
        return 0

    affected = 0
    affected += _set_null_fk(AdminArea, "parent", pk_batch)
    affected += _set_null_fk(AdminArea, "most_populate_city", pk_batch)
    affected += _set_null_fk(NuevoAdminArea, "most_populate_city", pk_batch)
    affected += _delete_m2m_references(AdminArea.capitals.through, pk_batch)
    affected += _delete_m2m_references(NuevoAdminArea.capitals.through, pk_batch)
    affected += _delete_m2m_references(NuevoAdminArea.municipios_originales.through, pk_batch)
    return affected


def _effective_batch_size(batch_size: int | None) -> int:
    effective = int(batch_size or SQLITE_SAFE_DELETE_BATCH_SIZE)
    if effective < 1:
        raise ValueError("batch_size must be greater than zero")
    return effective


def _chunks(values: Sequence[str], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _set_null_fk(model, field_name: str, ids: Sequence[str]) -> int:
    field = model._meta.get_field(field_name)
    table = model._meta.db_table
    column = field.column
    return _execute_in_clause(
        f"UPDATE {_quote(table)} SET {_quote(column)} = NULL WHERE {_quote(column)} IN ({{placeholders}})",
        ids,
    )


def _delete_m2m_references(through_model, ids: Sequence[str]) -> int:
    affected = 0
    table = through_model._meta.db_table
    for column in _admin_area_fk_columns(through_model):
        affected += _execute_in_clause(
            f"DELETE FROM {_quote(table)} WHERE {_quote(column)} IN ({{placeholders}})",
            ids,
        )
    return affected


def _admin_area_fk_columns(through_model) -> list[str]:
    columns = []
    for field in through_model._meta.fields:
        remote_field = getattr(field, "remote_field", None)
        if remote_field and remote_field.model is AdminArea:
            columns.append(field.column)
    return columns


def _raw_delete_admin_area_ids(ids: Sequence[str]) -> int:
    return _execute_in_clause(
        f"DELETE FROM {_quote(AdminArea._meta.db_table)} "
        f"WHERE {_quote(AdminArea._meta.pk.column)} IN ({{placeholders}})",
        ids,
    )


def _execute_in_clause(sql_template: str, ids: Sequence[str]) -> int:
    if not ids:
        return 0
    placeholders = ", ".join(["%s"] * len(ids))
    with connection.cursor() as cursor:
        cursor.execute(sql_template.format(placeholders=placeholders), list(ids))
        return max(int(cursor.rowcount or 0), 0)


def _quote(name: str) -> str:
    return connection.ops.quote_name(name)
