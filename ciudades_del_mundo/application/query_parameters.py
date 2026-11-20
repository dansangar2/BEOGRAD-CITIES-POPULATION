"""Reusable query parameters responsibilities."""
from __future__ import annotations


def clean_values(values):
    return [value for value in values if value not in (None, "")]


def normalize_country_key(value: str | None) -> str:
    return str(value or "").strip().lower()
