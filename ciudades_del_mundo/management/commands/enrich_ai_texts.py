from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.ai_text_enrichment import (
    DEFAULT_AI_LANGUAGES,
    AiEnrichmentStats,
    AiTextEnrichmentService,
)
from ciudades_del_mundo.services.ai_text_provider import (
    AiProviderConfigurationError,
    OpenAICompatibleJsonProvider,
)


class Command(BaseCommand):
    help = "Generate SQL-backed dynamic geography translations and visual descriptions with AI."

    def add_arguments(self, parser):
        parser.add_argument("countries", nargs="*", help="Country codes to enrich, for example: spain portugal.")
        parser.add_argument("--all", action="store_true", help="Process every country with stored AdminArea rows.")
        parser.add_argument("--languages", default=",".join(DEFAULT_AI_LANGUAGES), help="Comma-separated languages.")
        parser.add_argument("--limit", type=int, default=100, help="Maximum rows/assets processed per country.")
        parser.add_argument("--infer-entity-types", action="store_true", help="Infer incomplete AdminArea entity types.")
        parser.add_argument("--translate-names", action="store_true", help="Translate country names.")
        parser.add_argument("--translate-area-names", action="store_true", help="Translate individual AdminArea names.")
        parser.add_argument("--translate-entity-types", action="store_true", help="Translate entity type singular/plural labels.")
        parser.add_argument("--describe-assets", action="store_true", help="Generate flag/coat/seal descriptions.")

    def handle(self, *args, **options):
        countries = self._countries(options)
        if not countries:
            raise CommandError("No countries selected.")
        try:
            provider = OpenAICompatibleJsonProvider.from_env()
        except AiProviderConfigurationError as exc:
            raise CommandError(str(exc)) from exc

        service = AiTextEnrichmentService(provider, languages=_parse_languages(options["languages"]))
        actions = self._actions(options)
        for country_code in countries:
            stats = service.enrich_existing_admin_area_entity_types(
                country_code=country_code,
                limit=max(1, int(options.get("limit") or 1)),
            ) if actions["infer_entity_types"] else AiEnrichmentStats()
            if actions["translate_names"] or actions["translate_area_names"] or actions["translate_entity_types"]:
                stats += service.translate_dynamic_texts(
                    country_code=country_code,
                    include_country=actions["translate_names"],
                    include_admin_area_names=actions["translate_area_names"],
                    include_entity_types=actions["translate_entity_types"],
                    limit=max(1, int(options.get("limit") or 1)),
                )
            if actions["describe_assets"]:
                stats += service.describe_missing_visual_assets(
                    country_code=country_code,
                    limit=max(1, int(options.get("limit") or 1)),
                )
            self.stdout.write(self.style.SUCCESS(stats.as_log_line(f"[ai] {country_code}:")))

    def _countries(self, options) -> list[str]:
        if options.get("all"):
            return list(
                AdminArea.objects.values_list("country_code", flat=True)
                .distinct()
                .order_by("country_code")
            )
        return [str(country or "").strip() for country in options.get("countries") or [] if str(country or "").strip()]

    def _actions(self, options) -> dict[str, bool]:
        selected = {
            "infer_entity_types": bool(options.get("infer_entity_types")),
            "translate_names": bool(options.get("translate_names")),
            "translate_area_names": bool(options.get("translate_area_names")),
            "translate_entity_types": bool(options.get("translate_entity_types")),
            "describe_assets": bool(options.get("describe_assets")),
        }
        if any(selected.values()):
            return selected
        selected["infer_entity_types"] = True
        selected["translate_names"] = True
        selected["translate_entity_types"] = True
        selected["describe_assets"] = True
        return selected


def _parse_languages(value: str) -> tuple[str, ...]:
    languages = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    return languages or DEFAULT_AI_LANGUAGES
