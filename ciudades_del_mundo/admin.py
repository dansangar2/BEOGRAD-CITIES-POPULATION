"""Admin site configuration for scraped and derived area models."""

from django.contrib import admin
from .models import AdminArea, NuevoAdminArea


@admin.register(AdminArea)
class AdminAreaAdmin(admin.ModelAdmin):
    """Back-office listing for original scraped administrative areas."""

    # Muestra el PK nuevo, el country_code y el code de la subdivisión
    def parent_pk(self, obj):
        return obj.parent_id  # mostrará "spain_AND", etc.
    parent_pk.short_description = "Parent ID"

    list_display = (
        "id", "country_code", "code", "name", "level","entity_type",
        "parent_pk", "pop_latest", "representatives", "pop_latest_date", "updated_at",
    )
    list_filter  = ("country_code", "level","entity_type")
    search_fields = ("id", "code", "name")   # antes incluía 'entity_id'
    ordering = ("country_code", "level", "name")


@admin.register(NuevoAdminArea)
class NuevoAdminAreaAdmin(admin.ModelAdmin):
    """Back-office listing for derived or fictional administrative areas."""

    def parent_pk(self, obj):
        return obj.parent_id
    parent_pk.short_description = "Parent ID"

    list_display = (
        "id", "country_code", "code", "name", "level", "entity_type",
        "parent_pk", "pop_latest", "representatives", "updated_at",
    )
    list_filter = ("country_code", "level", "entity_type")
    search_fields = ("id", "code", "name")
    ordering = ("country_code", "level", "name")
