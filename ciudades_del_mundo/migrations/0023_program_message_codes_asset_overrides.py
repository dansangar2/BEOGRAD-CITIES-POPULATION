from __future__ import annotations

from django.db import migrations


PROGRAM_CODES = [
    ("SCR-SUCCESS-001", "success", "Scraping completado", "La tarea terminó correctamente y los datos quedaron populados."),
    ("SCR-ASSET-W001", "warning", "Scraping completado con recursos visuales sin asignar", "La importación de datos terminó bien, pero algunos escudos o banderas encontrados en Wikidata no pudieron vincularse automáticamente."),
    ("SCR-VAL-001", "error", "Configuración inválida", "La validación de la configuración falló. Revisa el TOML, los campos obligatorios y la estructura de páginas."),
    ("SCR-DATA-001", "error", "Error scrapeando datos", "El scrapeo de datos de CityPopulation falló antes de completar la importación."),
    ("SCR-ASSET-001", "error", "Error en búsqueda masiva Wikidata", "La búsqueda masiva de escudos o banderas en Wikidata falló o no pudo completarse."),
    ("SCR-ASSET-002", "error", "Recursos visuales sin asignar", "La importación de datos terminó, pero el modo estricto encontró recursos visuales de Wikidata sin asignar y bloqueó la tarea."),
    ("SCR-ASSET-003", "error", "Falta migración de assets", "La fase de recursos visuales no pudo ejecutarse porque falta la tabla o migración de assets."),
    ("SCR-DB-001", "error", "Error de base de datos", "SQLite/Django rechazó una consulta o la base de datos estaba ocupada. Suele requerir consultas por lotes o reintentar."),
    ("SCR-NET-001", "error", "Error de red", "Una petición externa falló por timeout, HTTP 502 u otro problema de red."),
    ("SCR-CLEAR-001", "error", "Error limpiando datos", "La limpieza previa o manual de datos falló."),
    ("SCR-CANCEL-001", "warning", "Tarea cancelada", "La tarea fue cancelada antes de terminar."),
    ("SCR-UNKNOWN", "error", "Error no clasificado", "La tarea falló por un error no clasificado. Abre el registro para ver el traceback completo."),
]


def seed_program_codes(apps, schema_editor):
    now_sql = "CURRENT_TIMESTAMP"
    with schema_editor.connection.cursor() as cursor:
        for code, severity, message, description in PROGRAM_CODES:
            cursor.execute(
                f"""
                    INSERT INTO ciudades_del_mundo_program_message_code (
                        code, severity, message, description, is_active, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 1, {now_sql}, {now_sql})
                    ON CONFLICT(code) DO UPDATE SET
                        severity = excluded.severity,
                        message = excluded.message,
                        description = excluded.description,
                        is_active = 1,
                        updated_at = {now_sql}
                """,
                [code, severity, message, description],
            )


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0022_adminarea_annotations"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE TABLE IF NOT EXISTS ciudades_del_mundo_program_message_code (
                    code varchar(64) NOT NULL PRIMARY KEY,
                    severity varchar(24) NOT NULL DEFAULT 'info',
                    message varchar(255) NOT NULL DEFAULT '',
                    description text NOT NULL DEFAULT '',
                    is_active bool NOT NULL DEFAULT 1,
                    created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ciudades_del_mundo_program_message_code_severity_idx
                    ON ciudades_del_mundo_program_message_code(severity);
            """,
            reverse_sql="DROP TABLE IF EXISTS ciudades_del_mundo_program_message_code;",
        ),
        migrations.RunSQL(
            sql="""
                CREATE TABLE IF NOT EXISTS ciudades_del_mundo_config_asset_override (
                    id integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                    config_slug varchar(120) NOT NULL,
                    country_code varchar(120) NOT NULL DEFAULT '',
                    level integer NULL,
                    entity_id varchar(64) NOT NULL DEFAULT '',
                    entity_code varchar(255) NOT NULL DEFAULT '',
                    entity_name varchar(255) NOT NULL DEFAULT '',
                    kind varchar(32) NOT NULL,
                    wikidata_id varchar(64) NOT NULL,
                    created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ciudades_del_mundo_config_asset_override_slug_idx
                    ON ciudades_del_mundo_config_asset_override(config_slug);
                CREATE UNIQUE INDEX IF NOT EXISTS ciudades_del_mundo_config_asset_override_unique_idx
                    ON ciudades_del_mundo_config_asset_override(config_slug, entity_id, kind);
            """,
            reverse_sql="DROP TABLE IF EXISTS ciudades_del_mundo_config_asset_override;",
        ),
        migrations.RunPython(seed_program_codes, reverse_code=migrations.RunPython.noop),
    ]
