"""Program status/warning/error code catalog backed by the database.

The web layer can show concise codes while this module resolves the user-facing
message/description from SQL.  It intentionally uses raw SQL so the feature can
be adopted without coupling views or commands to Django model classes.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import OperationalError, ProgrammingError, connection


PROGRAM_MESSAGE_CODE_TABLE = "ciudades_del_mundo_program_message_code"


@dataclass(frozen=True)
class ProgramMessageCode:
    code: str
    severity: str
    message: str
    description: str


DEFAULT_PROGRAM_MESSAGE_CODES: dict[str, ProgramMessageCode] = {
    "SCR-SUCCESS-001": ProgramMessageCode(
        "SCR-SUCCESS-001",
        "success",
        "Scraping completado",
        "La tarea terminó correctamente y los datos quedaron populados.",
    ),
    "SCR-ASSET-W001": ProgramMessageCode(
        "SCR-ASSET-W001",
        "warning",
        "Scraping completado con recursos visuales sin asignar",
        "La importación de datos terminó bien, pero algunos escudos o banderas encontrados en Wikidata no pudieron vincularse automáticamente.",
    ),
    "SCR-VAL-001": ProgramMessageCode(
        "SCR-VAL-001",
        "error",
        "Configuración inválida",
        "La validación de la configuración falló. Revisa el TOML, los campos obligatorios y la estructura de páginas.",
    ),
    "SCR-DATA-001": ProgramMessageCode(
        "SCR-DATA-001",
        "error",
        "Error scrapeando datos",
        "El scrapeo de datos de CityPopulation falló antes de completar la importación.",
    ),
    "SCR-ASSET-001": ProgramMessageCode(
        "SCR-ASSET-001",
        "error",
        "Error en búsqueda masiva Wikidata",
        "La búsqueda masiva de escudos o banderas en Wikidata falló o no pudo completarse.",
    ),
    "SCR-ASSET-002": ProgramMessageCode(
        "SCR-ASSET-002",
        "error",
        "Recursos visuales sin asignar",
        "La importación de datos terminó, pero el modo estricto encontró recursos visuales de Wikidata sin asignar y bloqueó la tarea.",
    ),
    "SCR-ASSET-003": ProgramMessageCode(
        "SCR-ASSET-003",
        "error",
        "Falta migración de assets",
        "La fase de recursos visuales no pudo ejecutarse porque falta la tabla o migración de assets.",
    ),
    "SCR-DB-001": ProgramMessageCode(
        "SCR-DB-001",
        "error",
        "Error de base de datos",
        "SQLite/Django rechazó una consulta o la base de datos estaba ocupada. Suele requerir consultas por lotes o reintentar.",
    ),
    "SCR-NET-001": ProgramMessageCode(
        "SCR-NET-001",
        "error",
        "Error de red",
        "Una petición externa falló por timeout, HTTP 502 u otro problema de red.",
    ),
    "SCR-CLEAR-001": ProgramMessageCode(
        "SCR-CLEAR-001",
        "error",
        "Error limpiando datos",
        "La limpieza previa o manual de datos falló.",
    ),
    "SCR-CANCEL-001": ProgramMessageCode(
        "SCR-CANCEL-001",
        "warning",
        "Tarea cancelada",
        "La tarea fue cancelada antes de terminar.",
    ),
    "SCR-UNKNOWN": ProgramMessageCode(
        "SCR-UNKNOWN",
        "error",
        "Error no clasificado",
        "La tarea falló por un error no clasificado. Abre el registro para ver el traceback completo.",
    ),
}


def program_message_code_table_exists() -> bool:
    try:
        return PROGRAM_MESSAGE_CODE_TABLE in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False


def get_program_message_catalog() -> dict[str, ProgramMessageCode]:
    catalog = dict(DEFAULT_PROGRAM_MESSAGE_CODES)
    if not program_message_code_table_exists():
        return catalog
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    SELECT code, severity, message, description
                    FROM {PROGRAM_MESSAGE_CODE_TABLE}
                    WHERE is_active = 1
                """
            )
            for code, severity, message, description in cursor.fetchall():
                code = str(code or "").strip().upper()
                if not code:
                    continue
                catalog[code] = ProgramMessageCode(
                    code=code,
                    severity=str(severity or "info").strip().lower() or "info",
                    message=str(message or ""),
                    description=str(description or ""),
                )
    except (OperationalError, ProgrammingError):
        return catalog
    return catalog


def get_program_message_code(code: str) -> ProgramMessageCode | None:
    code = str(code or "").strip().upper()
    if not code:
        return None
    return get_program_message_catalog().get(code)


def program_message_payload(code: str) -> dict[str, str]:
    item = get_program_message_code(code)
    if not item:
        return {"code": "", "severity": "", "message": "", "description": ""}
    return {
        "code": item.code,
        "severity": item.severity,
        "message": item.message,
        "description": item.description,
    }
