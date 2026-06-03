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

    class CityMergeStatus(models.IntegerChoices):
        NONE = 0, "No unificada"
        SOURCE = 1, "Fuente de ciudad unificada"
        UNIFIED = 2, "Ciudad unificada"

    id               = models.CharField(max_length=128, primary_key=True)
    country_code     = models.CharField(max_length=64, db_index=True)
    code             = models.CharField(max_length=64)  # código de subdivisión (scrapeado)
    name             = models.CharField(max_length=255)
    level            = models.IntegerField(choices=Level.choices)
    city_merge_status = models.IntegerField(
        choices=CityMergeStatus.choices,
        default=CityMergeStatus.NONE,
        db_index=True,
    )

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
            models.Index(fields=["parent", "city_merge_status"], name="adminarea_parent_merge_idx"),
            models.Index(fields=["entity_type"]),  # útil para filtrar por tipo
        ]

    def __str__(self):
        return f"{self.id} — {self.name} (L{self.level}, {self.entity_type or '-'})"

    def get_children(self):
        return self.children.all()

    def get_children_with_merge_sources(self):
        return self.children.filter(
            city_merge_status__in=[
                self.CityMergeStatus.NONE,
                self.CityMergeStatus.SOURCE,
            ]
        )

    def get_children_with_unified_cities(self):
        return self.children.filter(
            city_merge_status__in=[
                self.CityMergeStatus.NONE,
                self.CityMergeStatus.UNIFIED,
            ]
        )

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

    class ProvinceStatus(models.TextChoices):
        NORMAL = "normal", "Normal"
        DEPENDENCY = "dependency", "Dependencia"
        TERRITORY = "territory", "Territorio"

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
    population_index = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=1,
        help_text=(
            "Multiplicador de poblacion aplicado a esta subdivision y a sus descendientes "
            "para el computo de representacion."
        ),
    )
    province_status  = models.CharField(
        max_length=20,
        choices=ProvinceStatus.choices,
        default=ProvinceStatus.NORMAL,
    )
    depends_on       = models.ForeignKey(
        "self",
        to_field="id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dependent_areas",
        help_text="Provincia con la que comparte representacion si el estado es dependencia.",
    )
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
    capital_names_by_language = models.JSONField(default=dict, blank=True)

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
        ordering = ["country_code", "level", "name", "id"]
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
            models.CheckConstraint(
                check=Q(population_index__gte=0),
                name="nuevo_area_population_index_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["country_code", "level"]),
            models.Index(fields=["level"]),
            models.Index(fields=["parent"]),
            models.Index(fields=["name"]),
            models.Index(fields=["entity_type"]),
            models.Index(fields=["province_status"]),
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




class WebTask(models.Model):
    """Persistent background task launched from the web UI/API."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.CharField(max_length=32, primary_key=True)
    key = models.CharField(max_length=255, db_index=True)
    label = models.CharField(max_length=255)
    args = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    created_at = models.DateTimeField(db_index=True)
    started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True, db_index=True)
    returncode = models.IntegerField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    log_path = models.CharField(max_length=500, null=True, blank=True)
    output = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["key", "-created_at"], name="webtask_key_created_idx"),
            models.Index(fields=["status", "-created_at"], name="webtask_status_created_idx"),
        ]

    @property
    def command_display(self) -> str:
        return " ".join(["py", "manage.py", *(str(arg) for arg in (self.args or []))])

    @property
    def output_text(self) -> str:
        return "".join(str(line) for line in (self.output or []))

    @property
    def is_active(self) -> bool:
        return self.status not in {self.Status.SUCCEEDED, self.Status.FAILED, self.Status.CANCELLED}

    def __str__(self):
        return f"{self.id} — {self.label} ({self.status})"


class ScrapingConfig(models.Model):
    """CityPopulation scraping configuration migrated from TOML to SQL."""

    slug = models.SlugField(max_length=128, primary_key=True)
    country_code = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField()
    content_hash = models.CharField(max_length=64, db_index=True)
    source_path = models.CharField(max_length=500, blank=True, default="")
    pages_count = models.PositiveIntegerField(default=0)
    cities_count = models.PositiveIntegerField(default=0)
    has_representation = models.BooleanField(default=False)
    is_valid = models.BooleanField(default=True, db_index=True)
    validation_error = models.TextField(blank=True, default="")
    imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        indexes = [
            models.Index(fields=["country_code", "slug"], name="scrconf_country_slug_idx"),
            models.Index(fields=["is_valid", "slug"], name="scrconf_valid_slug_idx"),
        ]

    def __str__(self):
        return f"{self.slug} ({self.country_code})"
