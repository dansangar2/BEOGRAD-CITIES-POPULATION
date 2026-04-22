# ciudades_del_mundo/models.py
from django.db import models
from django.db.models import Q


class AdminArea(models.Model):
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
        Devuelve el número de escaños para este AdminArea, buscando
        por (country_code, code) en el modelo Escanho.
        """
        es = Escanho.objects.filter(
            country_code=self.country_code,
            subdivision_code=self.code,
        ).first()
        return es.seats if es else None


class NuevoAdminArea(models.Model):
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

        - Si existe un registro directo en Escanho → devuelve esos escaños.
        - Si no existe, suma los escaños de sus hijos (si los hay).
        - Si tampoco hay hijos con escaños → None.
        """
        from .models import Escanho  # import local para evitar problemas de orden

        # 1) Intentar encontrar escaños directos para este código
        e = Escanho.objects.filter(
            country_code=self.country_code,
            subdivision_code=self.code,
        ).first()
        if e:
            return e.seats

        # 2) Si no hay escaños directos, sumar los de los hijos
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


# ---------------------------------------------------------------------------
# NUEVO MODELO DE ESCAÑOS
# ---------------------------------------------------------------------------

class Escanho(models.Model):
    """
    Asigna un número de escaños a una subdivisión concreta de un país
    (tanto para AdminArea como para NuevoAdminArea, se accede por código).

    - country_id: id del país en NuevoAdminArea (ej: "austria-spanish-empire")
    - subdivision_id: id de la subdivisión en NuevoAdminArea (ej: "austria-spanish-empire_CAT")
    - country_code: código del país (mismo que NuevoAdminArea.country_code)
    - subdivision_code: código de la subdivisión (NuevoAdminArea.code o AdminArea.code)
    """

    country_id       = models.CharField(max_length=128, db_index=True)
    subdivision_id   = models.CharField(max_length=128, db_index=True)

    country_code     = models.CharField(max_length=64, db_index=True)
    subdivision_code = models.CharField(max_length=64, db_index=True)

    seats            = models.PositiveIntegerField()

    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "subdivision_code"],
                name="uniq_escanhos_country_subdivision",
            ),
        ]
        indexes = [
            models.Index(fields=["country_code", "subdivision_code"]),
            models.Index(fields=["country_id"]),
            models.Index(fields=["subdivision_id"]),
        ]

    def __str__(self):
        return f"{self.country_code}:{self.subdivision_code} -> {self.seats} escaños"
