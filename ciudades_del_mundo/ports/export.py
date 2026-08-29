"""Ports used by application export use cases."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ciudades_del_mundo.domain.nuevo_admin_export import NuevoAdminExportData, Workbook


class NuevoAdminAreaExportRepository(Protocol):
    def get_export_data(
        self,
        country_id: str,
        max_level: int | None = None,
    ) -> NuevoAdminExportData:
        ...


class WorkbookWriter(Protocol):
    def write(self, workbook: Workbook, path: Path) -> None:
        ...
