"""Code helpers for SQL-backed derived subdivision records."""

from __future__ import annotations

import re


DERIVED_COUNTRY_ROOT_CODE_OVERRIDES = {
    "spain": "ESP",
}


def derived_code_piece(value: object) -> str:
    """Return the normalized uppercase code fragment used by derived records."""
    text = re.sub(r"[^A-Za-z0-9_]+", "-", str(value or "").strip()).strip("-_")
    return text.upper()


def derived_country_root_code(country_code: str, source_code: object = "") -> str:
    """Return the root code used by the derived-subdivision hierarchy."""
    country_key = str(country_code or "").strip().lower()
    code = derived_code_piece(source_code)
    if code and code.lower() != country_key:
        return code
    override = DERIVED_COUNTRY_ROOT_CODE_OVERRIDES.get(country_key)
    if override:
        return override
    return derived_code_piece(country_code)
