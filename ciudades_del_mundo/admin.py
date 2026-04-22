from django.contrib import admin
from .models import AdminArea, Escanho, NuevoAdminArea


@admin.register(AdminArea)
class AdminAreaAdmin(admin.ModelAdmin):
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
    def parent_pk(self, obj):
        return obj.parent_id
    parent_pk.short_description = "Parent ID"

    list_display = (
        "id", "country_code", "code", "name", "level", "entity_type",
        "parent_pk", "pop_latest", "updated_at",
    )
    list_filter = ("country_code", "level", "entity_type")
    search_fields = ("id", "code", "name")
    ordering = ("country_code", "level", "name")


@admin.register(Escanho)
class EscanhoAdmin(admin.ModelAdmin):
    list_display = (
        "country_code", "subdivision_code", "seats", "country_id",
        "subdivision_id", "updated_at",
    )
    list_filter = ("country_code",)
    search_fields = ("country_code", "subdivision_code", "country_id", "subdivision_id")
    ordering = ("country_code", "subdivision_code")
