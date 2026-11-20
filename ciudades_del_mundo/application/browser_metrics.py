"""application.browser_metrics: extracted from the operational web adapter."""

from __future__ import annotations




def _ratio_percent(value, total) -> float:
    try:
        if not total:
            return 0
        return round((float(value or 0) / float(total)) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0



def _density(population, area) -> float | None:
    try:
        if not population or not area:
            return None
        return round(float(population) / float(area), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None



def _number_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None



def _percent(value, total) -> str:
    if value in (None, "") or total in (None, "", 0):
        return ""
    try:
        return f"{(float(value) / float(total)) * 100:.2f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return ""

