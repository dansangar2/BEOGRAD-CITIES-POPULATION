from __future__ import annotations

from ciudades_del_mundo.domain.nuevo_admin_export import (
    NuevoAdminAreaSummary,
    NuevoAdminCitySummary,
    NuevoAdminExportData,
)
from ciudades_del_mundo.models import NuevoAdminArea


class DjangoNuevoAdminAreaExportRepository:
    def get_export_data(
        self,
        country_id: str,
        max_level: int | None = None,
    ) -> NuevoAdminExportData:
        root = (
            NuevoAdminArea.objects
            .select_related("parent", "most_populate_city")
            .prefetch_related("capitals")
            .get(id=country_id)
        )
        root_level = root.level or 0

        qs = (
            NuevoAdminArea.objects
            .filter(country_code=root.country_code, level__gt=root_level)
            .exclude(id=root.id)
            .select_related("parent", "most_populate_city")
            .prefetch_related("capitals", "municipios_originales")
            .order_by("level", "code")
        )
        if max_level is not None:
            qs = qs.filter(level__lte=max_level)

        return NuevoAdminExportData(
            root=_to_summary(root),
            areas=tuple(_to_summary(area) for area in qs),
        )


def _to_summary(area: NuevoAdminArea) -> NuevoAdminAreaSummary:
    return NuevoAdminAreaSummary(
        id=area.id,
        country_code=area.country_code,
        code=area.code,
        name=area.name,
        level=area.level,
        entity_type=area.entity_type,
        parent_id=area.parent_id,
        area_km2=area.area_km2,
        pop_latest=area.pop_latest,
        representatives=area.representatives,
        capitals=tuple(
            NuevoAdminCitySummary(
                id=capital.id,
                name=capital.name,
                pop_latest=capital.pop_latest,
            )
            for capital in area.capitals.all()
        ),
        most_populated_city=(
            NuevoAdminCitySummary(
                id=area.most_populate_city.id,
                name=area.most_populate_city.name,
                pop_latest=area.most_populate_city.pop_latest,
            )
            if area.most_populate_city
            else None
        ),
        source_units_count=area.municipios_originales.count(),
    )
