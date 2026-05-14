"""Database models for scraped geography and derived political entities."""
from django.db import models
from django.db.models import Q


class AdminArea(models.Model):
    """Administrative area scraped directly from CityPopulation."""

    class Level(models.IntegerChoices):
        COUNTRY = 0, "Country"
        ADMIN1  = 1, "Admin1 / Región / CCAA"
        ADMIN2  = 2, "Admin2 / Provincia"
        ADMIN3  = 3, "Admin3 / Municipio"
        ADMIN4  = 4, "Admin4"
        ADMIN5  = 5, "Admin5"

    id               = models.CharField(max_length=128, primary_key=True)
    country_code     = models.CharField(max_length=64, db_index=True)
    code             = models.CharField(max_length=64)  # código de subdivisión (scrapeado)
    name             = models.CharField(max_length=255)
    level            = models.IntegerField(choices=Level.choices)

    # NUEVO
    entity_type      = models.CharField(max_length=80, null=True, blank=True)
    #        ↑ ej.: "Autonomous Community", "Province", "Municipality", ...

    parent           = models.ForeignKey(
        "self",
        to_field="id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )

    area_km2         = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    density          = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    pop_latest       = models.BigIntegerField(null=True, blank=True)
    representatives  = models.PositiveIntegerField(null=True, blank=True)
    pop_latest_date  = models.DateField(null=True, blank=True)
    last_census_year = models.IntegerField(null=True, blank=True)
    url              = models.URLField(max_length=500, null=True, blank=True)

    # --- NUEVOS CAMPOS ---
    # Varias capitales (auto-relación, no simétrica)
    capitals         = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="capital_of",
        blank=True,
    )

    # Se mantiene como FK (una sola ciudad más poblada)
    most_populate_city = models.ForeignKey(
        "self",
        to_field="id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="most_populated_of",
    )

    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["country_code", "code"], name="uniq_area_country_code"),
        ]
        indexes = [
            models.Index(fields=["country_code", "level"]),
            models.Index(fields=["level"]),
            models.Index(fields=["parent"]),
            models.Index(fields=["name"]),
            models.Index(fields=["entity_type"]),  # útil para filtrar por tipo
        ]

    def __str__(self):
        return f"{self.id} — {self.name} (L{self.level}, {self.entity_type or '-'})"

    # 🔹 Número de escaños asociado a este AdminArea (si existe)
    @property
    def escanhos(self):
        """
        Alias de compatibilidad para el campo representatives.
        """
        return self.representatives


class NuevoAdminArea(models.Model):
    """Derived administrative area assembled from existing `AdminArea` rows."""

    class Level(models.IntegerChoices):
        COUNTRY = 0, "Country"
        ADMIN1  = 1, "Admin1 / Región / CCAA"
        ADMIN2  = 2, "Admin2 / Provincia"
        ADMIN3  = 3, "Admin3 / Municipio"
        ADMIN4  = 4, "Admin4"
        ADMIN5  = 5, "Admin5"

    id               = models.CharField(max_length=128, primary_key=True)
    country_code     = models.CharField(max_length=64, db_index=True)
    code             = models.CharField(max_length=64)
    name             = models.CharField(max_length=255)
    level            = models.IntegerField(choices=Level.choices)

    entity_type      = models.CharField(max_length=80, null=True, blank=True)
    parent           = models.ForeignKey(
        "self",
        to_field="id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )

    area_km2         = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    density          = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    pop_latest       = models.BigIntegerField(null=True, blank=True)
    representatives  = models.PositiveIntegerField(null=True, blank=True)
    # ELIMINADOS:
    # pop_latest_date  = models.DateField(null=True, blank=True)
    # last_census_year = models.IntegerField(null=True, blank=True)
    # url              = models.URLField(max_length=500, null=True, blank=True)

    # --- Capitales / ciudad más poblada (AdminArea original) ---
    capitals = models.ManyToManyField(
        AdminArea,
        related_name="capital_of_new",
        blank=True,
    )

    most_populate_city = models.ForeignKey(
        AdminArea,
        to_field="id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="most_populated_of_new",
    )

    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    # Nivel del AdminArea ORIGINAL que se considera “municipio” (heredable)
    municipal_level  = models.IntegerField(null=True, blank=True)

    # ManyToMany sin through explícito
    municipios_originales = models.ManyToManyField(
        "AdminArea",
        related_name="nuevo_areas",
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "code"],
                name="uniq_nuevo_area_country_code",
            ),
            models.CheckConstraint(
                check=(
                    Q(municipal_level__isnull=True)
                    | (Q(municipal_level__gte=1) & Q(municipal_level__lte=5))
                ),
                name="nuevo_area_municipal_level_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["country_code", "level"]),
            models.Index(fields=["level"]),
            models.Index(fields=["parent"]),
            models.Index(fields=["name"]),
            models.Index(fields=["entity_type"]),
        ]

    def __str__(self):
        return f"{self.id} — {self.name} (L{self.level}, {self.entity_type or '-'})"

    def effective_municipal_level(self):
        node = self
        while node:
            if node.municipal_level is not None:
                return node.municipal_level
            node = node.parent
        return None

    @property
    def escanhos(self) -> int | None:
        """
        Devuelve los escaños asignados a esta subdivisión.

        - Si tiene representatives directo, devuelve ese valor.
        - Si no, suma los escaños de sus hijos (si los hay).
        - Si tampoco hay hijos con escaños → None.
        """
        if self.representatives is not None:
            return self.representatives

        child_seats = 0
        has_children = False
        for child in self.children.all():
            has_children = True
            s = child.escanhos
            if s:
                child_seats += s

        if has_children and child_seats > 0:
            return child_seats

        return None


