"""URL helpers shared by scraping infrastructure."""

from __future__ import annotations

from urllib.parse import urljoin


def build_page_url(base_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path.rstrip("/") + "/"
    return urljoin(base_url.rstrip("/") + "/", path.strip("/") + "/")
