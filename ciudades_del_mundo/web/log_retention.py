"""Retention helpers for local web/task diagnostic logs."""

from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings


LOG_RETENTION_DAYS = 90
LOG_DIRECTORIES = (
    ".web_task_logs",
    ".web_scrape_block_errors",
    ".web_task_progress",
    ".web_scrape_resume",
)


def cleanup_old_logs(*, base_dir: Path | None = None, days: int = LOG_RETENTION_DAYS) -> int:
    """Delete local diagnostic log files older than the retention window."""
    root = Path(base_dir or settings.BASE_DIR)
    cutoff = time.time() - max(1, int(days or LOG_RETENTION_DAYS)) * 24 * 60 * 60
    removed = 0

    for dirname in LOG_DIRECTORIES:
        directory = root / dirname
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            try:
                if path.is_file():
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                elif path.is_dir() and path != directory and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                continue

    return removed

