"""Build AI-readable context bundles for scraping configuration repair."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from django.conf import settings
from django.db import OperationalError, ProgrammingError

from ciudades_del_mundo.models import ScrapingConfig
from ciudades_del_mundo.services.scraped_page_logs import latest_page_run_dir
from ciudades_del_mundo.services.scraping_configs import scraping_config_table_exists


AUTOCONFIG_SPEC_DIR = Path("ciudades_del_mundo") / "autoconfig_specs"
AI_AUTOCONFIG_DIR_NAME = ".web_ai_autoconfig"
DEFAULT_CONTEXT_LOG_CHARS = 60000
DEFAULT_PAGE_INDEX_LINES = 160


@dataclass(frozen=True)
class AiAutoconfigContext:
    """Path and source summary for one generated context bundle."""

    slug: str
    path: Path
    spec_path: Path | None = None
    latest_error_log: Path | None = None
    latest_task_log: Path | None = None
    latest_page_run: Path | None = None
    toml_source: str = ""
    warnings: tuple[str, ...] = ()


def build_ai_autoconfig_context(
    slug: str,
    *,
    base_dir: Path | None = None,
    output_dir: Path | None = None,
    max_log_chars: int = DEFAULT_CONTEXT_LOG_CHARS,
    max_page_index_lines: int = DEFAULT_PAGE_INDEX_LINES,
) -> AiAutoconfigContext:
    """Write a Markdown dossier for a country and return its path."""

    root = Path(base_dir or settings.BASE_DIR)
    clean_slug = _safe_name(slug)
    warnings: list[str] = []

    spec_path = find_autoconfig_spec(clean_slug, base_dir=root)
    if spec_path is None:
        warnings.append(
            f"No territorial objective file found for {clean_slug}. "
            f"Create {AUTOCONFIG_SPEC_DIR.as_posix()}/{clean_slug}.txt before changing hierarchy levels."
        )

    toml_content, toml_source = _load_config_toml(clean_slug, root)
    if not toml_content:
        warnings.append(f"No SQL ScrapingConfig content found for {clean_slug}.")

    latest_error = _latest_file(root / ".web_scrape_block_errors" / clean_slug, "*.txt")
    latest_task = _latest_file(root / ".web_task_logs" / clean_slug, "*.log")
    latest_pages = latest_page_run_dir(clean_slug, base_dir=root)
    if latest_error is None:
        warnings.append(f"No scrape validation error log found for {clean_slug}.")
    if latest_task is None:
        warnings.append(f"No web task log found for {clean_slug}.")
    if latest_pages is None:
        warnings.append(f"No scraped-page run log found for {clean_slug}.")

    context_root = output_dir or (root / AI_AUTOCONFIG_DIR_NAME / clean_slug)
    context_root.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = context_root / f"{clean_slug}_context_{timestamp}.md"
    text = _render_context(
        clean_slug,
        root=root,
        spec_path=spec_path,
        toml_content=toml_content,
        toml_source=toml_source,
        latest_error=latest_error,
        latest_task=latest_task,
        latest_pages=latest_pages,
        warnings=tuple(warnings),
        max_log_chars=max(2000, int(max_log_chars or DEFAULT_CONTEXT_LOG_CHARS)),
        max_page_index_lines=max(20, int(max_page_index_lines or DEFAULT_PAGE_INDEX_LINES)),
    )
    path.write_text(text, encoding="utf-8")
    return AiAutoconfigContext(
        slug=clean_slug,
        path=path,
        spec_path=spec_path,
        latest_error_log=latest_error,
        latest_task_log=latest_task,
        latest_page_run=latest_pages,
        toml_source=toml_source,
        warnings=tuple(warnings),
    )


def find_autoconfig_spec(slug: str, *, base_dir: Path | None = None) -> Path | None:
    """Find the territorial target file for a config slug."""

    root = Path(base_dir or settings.BASE_DIR)
    candidates = (
        root / AUTOCONFIG_SPEC_DIR / f"{slug}.txt",
        root / "autoconfig_specs" / f"{slug}.txt",
        root / f"{slug}.txt",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def discover_recent_autoconfig_slugs(*, base_dir: Path | None = None, limit: int = 5) -> list[str]:
    """Return recent country slugs with diagnostic logs, newest first."""

    root = Path(base_dir or settings.BASE_DIR)
    entries: list[tuple[float, str]] = []
    for file_path in (root / ".web_scrape_block_errors").glob("*/*.txt"):
        entries.append((_mtime(file_path), file_path.parent.name))
    for file_path in (root / ".web_task_logs").glob("*/*.log"):
        entries.append((_mtime(file_path), file_path.parent.name))
    for run_dir in (root / ".web_scrape_pages").glob("*/*"):
        if run_dir.is_dir():
            entries.append((_mtime(run_dir), run_dir.parent.name))

    seen: set[str] = set()
    slugs: list[str] = []
    for _, slug in sorted(entries, reverse=True):
        clean_slug = _safe_name(slug)
        if not clean_slug or clean_slug in seen or clean_slug.startswith("_"):
            continue
        seen.add(clean_slug)
        slugs.append(clean_slug)
        if len(slugs) >= max(1, int(limit or 1)):
            break
    return slugs


def _render_context(
    slug: str,
    *,
    root: Path,
    spec_path: Path | None,
    toml_content: str,
    toml_source: str,
    latest_error: Path | None,
    latest_task: Path | None,
    latest_pages: Path | None,
    warnings: tuple[str, ...],
    max_log_chars: int,
    max_page_index_lines: int,
) -> str:
    lines = [
        f"# AI autoconfig context: {slug}",
        "",
        "Purpose: use this dossier to decide whether a scrape failure is caused by TOML configuration or reusable scraper/linker code.",
        "",
        "Operating rules for the AI agent:",
        "- First compare the territorial objective file with the current TOML levels and included sections.",
        "- Inspect the latest validation/task logs before editing code or config.",
        "- Prefer SQL `ScrapingConfig.content` changes for country-specific hierarchy choices.",
        "- Change parser/linker code only when the failure is generic and cannot be expressed in TOML.",
        "- If the objective file is missing or the country has no concrete target hierarchy, do not change scraping behavior.",
        "- After editing config/code, validate with focused commands and update documentation/AGENTS when workflow changes.",
        "",
        "## Sources",
        f"- territorial_objective: {_display_path(spec_path, root)}",
        f"- toml_source: {toml_source or '(missing)'}",
        f"- latest_error_log: {_display_path(latest_error, root)}",
        f"- latest_task_log: {_display_path(latest_task, root)}",
        f"- latest_page_run: {_display_path(latest_pages, root)}",
        "",
    ]
    if warnings:
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")

    lines.extend(["## Territorial Objective", ""])
    if spec_path:
        lines.extend(_fenced(_read_text(spec_path, max_chars=max_log_chars), "text"))
    else:
        lines.append("(missing)")
    lines.append("")

    lines.extend(["## Current SQL TOML", ""])
    if toml_content:
        lines.extend(_fenced(toml_content, "toml"))
    else:
        lines.append("(missing)")
    lines.append("")

    lines.extend(["## Latest Validation Error Log", ""])
    if latest_error:
        lines.extend(_fenced(_read_text(latest_error, max_chars=max_log_chars), "text"))
    else:
        lines.append("(missing)")
    lines.append("")

    lines.extend(["## Latest Task Log", ""])
    if latest_task:
        lines.extend(_fenced(_read_text(latest_task, max_chars=max_log_chars), "text"))
    else:
        lines.append("(missing)")
    lines.append("")

    lines.extend(["## Latest Scraped Pages", ""])
    if latest_pages:
        index_path = latest_pages / "index.jsonl"
        lines.append(f"Run directory: `{_display_path(latest_pages, root)}`")
        lines.append("")
        if index_path.exists():
            lines.extend(_fenced(_read_last_lines(index_path, max_page_index_lines), "json"))
            lines.append("")
            lines.append("Open the referenced `html_file` and `entities_file` paths under the run directory when the error log needs page-level detail.")
        else:
            lines.append("No index.jsonl found in the latest page run.")
    else:
        lines.append("(missing)")
    lines.append("")

    lines.extend(
        [
            "## Code Map",
            "- `ciudades_del_mundo/domain/scraping_config.py`: parses TOML page blocks and level/include hints.",
            "- `ciudades_del_mundo/services/scraping_configs.py`: loads and exports SQL `ScrapingConfig.content`.",
            "- `ciudades_del_mundo/infrastructure/scraping/citypopulation_sections.py`: parses CityPopulation HTML sections.",
            "- `ciudades_del_mundo/application/scrape_admin_areas.py`: validates blocks and final links before persistence.",
            "- `ciudades_del_mundo/application/citypopulation_linking.py`: links pages/blocks and repairs parent codes.",
            "- `ciudades_del_mundo/management/commands/scrape_subdivisions.py`: CLI composition and diagnostic log writers.",
            "",
            "## Suggested Verification",
            f"- `py manage.py validate_subdivision_configs {slug}`",
            f"- `py manage.py scrape_subdivisions {slug} --list-pages`",
            f"- `py manage.py scrape_subdivisions {slug}` only when fresh network scraping is intentionally required.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _load_config_toml(slug: str, root: Path) -> tuple[str, str]:
    try:
        if scraping_config_table_exists():
            record = ScrapingConfig.objects.filter(slug=slug).only("content").first()
            if record is not None:
                return record.content, f"SQL ScrapingConfig.content for `{slug}`"
    except (OperationalError, ProgrammingError):
        pass

    seed_path = root / "ciudades_del_mundo" / "subdivisions" / f"{slug}.toml"
    if seed_path.is_file():
        return seed_path.read_text(encoding="utf-8"), f"seed TOML `{_display_path(seed_path, root)}`"
    return "", ""


def _latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.is_dir():
        return None
    files = [path for path in directory.glob(pattern) if path.is_file()]
    if not files:
        return None
    return max(files, key=_mtime)


def _read_text(path: Path, *, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return (
        text[:head]
        + f"\n\n[... truncated {len(text) - max_chars} chars; open {_display_path(path, Path(settings.BASE_DIR))} for full content ...]\n\n"
        + text[-tail:]
    )


def _read_last_lines(path: Path, max_lines: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join([f"[... truncated {len(lines) - max_lines} older lines ...]", *lines[-max_lines:]])


def _fenced(text: str, language: str) -> list[str]:
    return [f"```{language}", text.rstrip(), "```"]


def _display_path(path: Path | None, root: Path) -> str:
    if path is None:
        return "(missing)"
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _safe_name(value: str) -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_", "."})
    return safe.strip("._") or "config"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
