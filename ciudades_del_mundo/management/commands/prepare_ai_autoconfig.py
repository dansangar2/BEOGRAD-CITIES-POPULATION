"""Prepare AI-readable dossiers for scraping configuration repair."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from ciudades_del_mundo.services.ai_autoconfig_context import (
    DEFAULT_CONTEXT_LOG_CHARS,
    DEFAULT_PAGE_INDEX_LINES,
    build_ai_autoconfig_context,
    discover_recent_autoconfig_slugs,
)
from ciudades_del_mundo.web.log_retention import cleanup_old_logs


class Command(BaseCommand):
    help = "Builds AI autoconfig context files from territorial specs, TOML and recent scrape logs."

    def add_arguments(self, parser):
        parser.add_argument(
            "countries",
            nargs="*",
            help=_("Config slugs. If omitted, recent countries with scrape/task logs are used."),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help=_("Maximum recent countries to prepare when no slug is provided. Default: %(default)s."),
        )
        parser.add_argument(
            "--max-log-chars",
            type=int,
            default=DEFAULT_CONTEXT_LOG_CHARS,
            help=_("Maximum characters copied from each large log/spec excerpt. Default: %(default)s."),
        )
        parser.add_argument(
            "--max-page-index-lines",
            type=int,
            default=DEFAULT_PAGE_INDEX_LINES,
            help=_("Maximum scraped-page index lines copied into the dossier. Default: %(default)s."),
        )

    def handle(self, *args, **options):
        cleanup_old_logs()
        countries = list(options.get("countries") or [])
        if not countries:
            countries = discover_recent_autoconfig_slugs(limit=max(1, int(options.get("limit") or 1)))
        if not countries:
            raise CommandError(
                _(
                    "No recent scrape diagnostics were found. Pass one or more config slugs, "
                    "for example: py manage.py prepare_ai_autoconfig portugal"
                )
            )

        for slug in countries:
            result = build_ai_autoconfig_context(
                slug,
                max_log_chars=max(2000, int(options.get("max_log_chars") or DEFAULT_CONTEXT_LOG_CHARS)),
                max_page_index_lines=max(20, int(options.get("max_page_index_lines") or DEFAULT_PAGE_INDEX_LINES)),
            )
            self.stdout.write(self.style.SUCCESS(_("AI autoconfig context: %(path)s") % {"path": result.path}))
            for warning in result.warnings:
                self.stdout.write(self.style.WARNING(_("  warning: %(warning)s") % {"warning": warning}))
