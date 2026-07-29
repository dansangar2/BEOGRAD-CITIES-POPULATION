"""Admin site configuration for scraped and derived area models."""

from django.contrib import admin
from .models import (
    AdminArea,
    DerivedCountry,
    DerivedCountryConfig,
    DerivedSubdivision,
    NuevoAdminArea,
    SubdivisionGroup,
)


@admin.register(AdminArea)
class AdminAreaAdmin(admin.ModelAdmin):
    """Back-office listing for original scraped administrative areas."""

    # Muestra el PK nuevo, el country_code y el code de la subdivisión
    def parent_pk(self, obj):
        return obj.parent_id  # mostrará "spain_AND", etc.
    parent_pk.short_description = "Parent ID"

    list_display = (
        "id", "country_code", "code", "name", "level", "city_merge_status", "entity_type",
        "parent_pk", "pop_latest", "representatives", "pop_latest_date", "updated_at",
    )
    list_filter  = ("country_code", "level", "city_merge_status", "entity_type")
    search_fields = ("id", "code", "name")   # antes incluía 'entity_id'
    ordering = ("country_code", "level", "name")


@admin.register(NuevoAdminArea)
class NuevoAdminAreaAdmin(admin.ModelAdmin):
    """Back-office listing for derived or fictional administrative areas."""

    def parent_pk(self, obj):
        return obj.parent_id
    parent_pk.short_description = "Parent ID"

    def depends_on_pk(self, obj):
        return obj.depends_on_id
    depends_on_pk.short_description = "Depende de"

    list_display = (
        "id", "country_code", "code", "name", "level", "entity_type",
        "parent_pk", "pop_latest", "population_index", "province_status",
        "depends_on_pk", "representatives", "updated_at",
    )
    list_filter = ("country_code", "level", "entity_type", "province_status")
    search_fields = ("id", "code", "name")
    ordering = ("country_code", "level", "name")
    raw_id_fields = ("parent", "depends_on", "most_populate_city")


@admin.register(DerivedCountry)
class DerivedCountryAdmin(admin.ModelAdmin):
    """Back-office listing for derived-country containers."""

    list_display = ("slug", "name", "source_country_code", "updated_at")
    list_filter = ("source_country_code",)
    search_fields = ("slug", "name", "description")
    ordering = ("name", "slug")


@admin.register(DerivedCountryConfig)
class DerivedCountryConfigAdmin(admin.ModelAdmin):
    """Back-office listing for TOML-backed derived-country variants."""

    list_display = ("slug", "name", "country", "source_country_code", "derived_country_code", "is_active", "updated_at")
    list_filter = ("country", "source_country_code", "is_active")
    search_fields = ("slug", "name", "content", "derived_country_code")
    ordering = ("country", "name", "slug")


@admin.register(SubdivisionGroup)
class SubdivisionGroupAdmin(admin.ModelAdmin):
    """Back-office listing for reusable subdivision groups."""

    list_display = ("slug", "name", "source_country_code", "updated_at")
    list_filter = ("source_country_code",)
    search_fields = ("slug", "name", "description", "content")
    ordering = ("name", "slug")


@admin.register(DerivedSubdivision)
class DerivedSubdivisionAdmin(admin.ModelAdmin):
    """Back-office listing for fictional or historical subdivision definitions."""

    list_display = ("slug", "internal_name", "name", "source_country_code", "entity_type", "code", "updated_at")
    list_filter = ("source_country_code", "entity_type")
    search_fields = ("slug", "internal_name", "name", "description", "content", "code")
    ordering = ("source_country_code", "name", "slug")
