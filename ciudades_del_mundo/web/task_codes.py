"""Web task status-code helpers."""

from __future__ import annotations



from ciudades_del_mundo.services.status_codes import get_program_message_catalog, program_message_payload

WIKIDATA_BULK_MARKERS = (
    "b\u00fasqueda masiva wikidata",
    "b\u00c3\u0192\u00c2\u00basqueda masiva wikidata",
    "busqueda masiva wikidata",
    "consultas bulk",
)


def _task_output_tail_text(task) -> str:
    from .tasks import task_manager

    return task_manager.output_tail_text(task)


def extract_status_code(text: str) -> str:
    text = str(text or "")
    catalog = get_program_message_catalog()
    for code in catalog:
        if code and code in text:
            return code
    lowered = text.casefold()
    if "quedan" in lowered and "recursos visuales" in lowered and "sin asignar" in lowered:
        return "SCR-ASSET-W001" if "warning" in lowered or "aviso" in lowered else "SCR-ASSET-002"
    if "tabla de assets visuales" in lowered:
        return "SCR-ASSET-003"
    if any(marker in lowered for marker in WIKIDATA_BULK_MARKERS):
        return "SCR-ASSET-001"
    if "too many sql variables" in lowered or "database is locked" in lowered or "sqlite" in lowered:
        return "SCR-DB-001"
    if "timed out" in lowered or "timeout" in lowered or "http error" in lowered or "bad gateway" in lowered:
        return "SCR-NET-001"
    return ""


def task_code_info(task, *, validation_error: str = "") -> dict:
    status = str(getattr(task, "status", "") or "").strip().casefold() if task else ""
    key = str(getattr(task, "key", "") or "").strip().casefold() if task else ""
    code = extract_status_code(validation_error)
    output = ""
    if task and (not code or status in {"succeeded", "failed", "cancelled"}):
        try:
            output = _task_output_tail_text(task)
        except Exception:
            output = ""
        code = code or extract_status_code(output)

    if not code and status == "cancelled":
        code = "SCR-CANCEL-001"
    if not code and validation_error:
        code = "SCR-VAL-001"
    if not code and status == "failed":
        if key.startswith("validate-config:"):
            code = "SCR-VAL-001"
        elif key.startswith("clear-config:"):
            code = "SCR-CLEAR-001"
        elif key.startswith("asset-corrections:"):
            code = "SCR-ASSET-W001"
        elif key.startswith("scrape:"):
            code = "SCR-DATA-001"
        else:
            code = "SCR-UNKNOWN"
    if not code and status == "succeeded" and key.startswith("scrape:"):
        code = "SCR-SUCCESS-001"

    payload = program_message_payload(code) if code else {"code": "", "severity": "", "message": "", "description": ""}
    return {
        "code": payload.get("code", ""),
        "severity": payload.get("severity", ""),
        "message": payload.get("message", ""),
        "description": payload.get("description", ""),
    }


def task_error_info(task, *, validation_error: str = "") -> dict:
    # Callers still name the fields error_code/error_description, but the
    # catalog can return success and warning severities too.
    return task_code_info(task, validation_error=validation_error)



