# ciudades_del_mundo/management/commands/build_new_subdivisions.py

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal, InvalidOperation
from collections import defaultdict

from ciudades_del_mundo.models import NuevoAdminArea, Escanho, AdminArea
import importlib
import pkgutil
from ciudades_del_mundo.services.nuevo_admin_builder import create_nuevo_area_from_spec, _round_area
import ciudades_del_mundo.historical_divisions as subdivisions_pkg


CONFIGS: dict[str, list[dict]] = {}
ESCANHOS: dict[str, dict] = {}
MUNICIPAL_LEVEL: dict[str, int] = {}


# Cargar módulos de configuración
for module_info in pkgutil.iter_modules(subdivisions_pkg.__path__):
    mod_name = module_info.name
    full_name = f"{subdivisions_pkg.__name__}.{module_info.name}"
    try:
        mod = importlib.import_module(full_name)
    except Exception:
        continue

    divs = getattr(mod, "DIVISIONS", None)
    if divs:
        CONFIGS[mod_name] = divs

    esca = getattr(mod, "ESCANHOS", None)
    if esca:
        ESCANHOS[mod_name] = esca

    munl = getattr(mod, "MUNICIPAL_LEVEL", None)
    if munl is not None:
        MUNICIPAL_LEVEL[mod_name] = munl


class Command(BaseCommand):
    help = (
        "Construye nuevas subdivisiones en NuevoAdminArea a partir de AdminArea "
        "para un país dado, respetando jerarquías definidas con 'childs' y "
        "asignando escaños según ESCANHOS.\n"
        "- Si el país (nodo raíz) no existe en NuevoAdminArea, se crea.\n"
        "- Si existe, se actualiza.\n"
        "- Antes de crear subdivisiones, se borran TODAS las subdivisiones y escaños "
        "relacionados con ese país, dejando solo el nodo raíz.\n"
        "- Las recetas con 'spec' agregan datos desde AdminArea.\n"
        "- Las recetas sin 'spec' se crean como contenedores y, al final, "
        "toman la suma de sus subdivisiones inferiores."
    )

    def _join_code(self, parent: NuevoAdminArea, raw_code: str) -> str:
        raw_code = (raw_code or "").strip()
        if not raw_code:
            return raw_code

        # Primer nivel bajo el país: no prefijamos
        if parent.level == NuevoAdminArea.Level.COUNTRY:
            return raw_code

        # Si ya viene prefijado, no duplicar
        if parent.code and raw_code.startswith(parent.code + "-"):
            return raw_code

        return f"{parent.code}-{raw_code}" if parent.code else raw_code

    def _assign_container_capitals(self, obj: NuevoAdminArea, capitals):
        if not capitals:
            return

        cap_objs = []
        for label in capitals:
            if not label:
                continue

            q = AdminArea.objects.filter(name__iexact=label).order_by("-level")
            if not q.exists():
                q = AdminArea.objects.filter(code__iexact=label).order_by("-level")

            cap = q.first()
            if cap:
                cap_objs.append(cap)

        if cap_objs:
            obj.capitals.set(cap_objs)

            top_cap = None
            for c in cap_objs:
                if c.pop_latest is None:
                    continue
                if top_cap is None or c.pop_latest > top_cap.pop_latest:
                    top_cap = c

            if top_cap is not None:
                obj.most_populate_city = top_cap
                obj.save(update_fields=["most_populate_city"])
        else:
            obj.capitals.clear()

    def add_arguments(self, parser):
        parser.add_argument(
            "--country-id",
            required=True,
            help=(
                "ID lógico del país/imperio (ej. 'austria_empire'). "
                "Se usará también como id/country_code del país en NuevoAdminArea."
            ),
        )
        parser.add_argument(
            "--m2m-field",
            default="municipios_originales",
            help="Nombre del campo M2M en NuevoAdminArea.",
        )
        parser.add_argument(
            "--code-prefix",
            default="",
            help="Prefijo opcional para code si no se pasa 'code' en la receta.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        country_id = opts["country_id"]
        m2m_field = opts["m2m_field"]
        code_prefix = opts["code_prefix"]

        recipes = CONFIGS.get(country_id)
        if not recipes:
            raise CommandError(f"No hay configuraciones para el país '{country_id}' en CONFIGS.")

        # ---------------------------------------------------------
        # Validador: ahora valida CÓDIGOS JERÁRQUICOS (full_code)
        # ---------------------------------------------------------
        def _join_code_str(parent_full_code: str | None, raw_code: str) -> str:
            raw_code = (raw_code or "").strip()
            if not parent_full_code:
                return raw_code
            if raw_code.startswith(parent_full_code + "-"):
                return raw_code
            return f"{parent_full_code}-{raw_code}"

        def _iter_full_codes(nodes, parent_full_code: str | None = None, prefix_path: str = ""):
            if isinstance(nodes, dict):
                nodes = [nodes]

            for r in nodes:
                if not isinstance(r, dict):
                    raise CommandError(
                        f"Entrada inválida en DIVISIONS: esperaba dict, recibí {type(r).__name__}: {r!r}"
                    )

                name = r.get("name", "?")
                raw = r.get("code") or (code_prefix + name)

                # Bajo el país, parent_full_code=None => full = raw
                full = _join_code_str(parent_full_code, raw) if parent_full_code else raw

                path = f"{prefix_path}/{raw}" if prefix_path else str(raw)
                yield full, path

                childs = r.get("childs") or []
                if childs:
                    yield from _iter_full_codes(childs, full, path)

        seen = defaultdict(list)
        for full_code, path in _iter_full_codes(recipes):
            seen[full_code].append(path)

        dups = {c: paths for c, paths in seen.items() if len(paths) > 1}
        if dups:
            lines = []
            for c, paths in sorted(dups.items()):
                lines.append(f"- {c}: " + " | ".join(paths))
            raise CommandError(
                "Códigos duplicados en DIVISIONS (deben ser únicos por country_code):\n"
                + "\n".join(lines)
            )

        # ---------------------------------------------------------
        # Crear/actualizar nodo raíz
        # ---------------------------------------------------------
        default_name = country_id.replace("-", " ").title()
        municipal_level = MUNICIPAL_LEVEL.get(country_id, 3)

        root_defaults = {
            "country_code": country_id,
            "code": country_id,
            "name": default_name,
            "level": NuevoAdminArea.Level.COUNTRY,
            "entity_type": "Country",
            "parent": None,
            "municipal_level": municipal_level,
        }

        root, created_root = NuevoAdminArea.objects.update_or_create(
            id=country_id,
            defaults=root_defaults,
        )

        if created_root:
            self.stdout.write(self.style.SUCCESS(
                f"Creado nodo raíz NuevoAdminArea id='{country_id}' (municipal_level={municipal_level})."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Actualizado nodo raíz NuevoAdminArea id='{country_id}' (municipal_level={municipal_level})."
            ))

        deleted_areas, _ = (
            NuevoAdminArea.objects
            .filter(country_code=root.country_code)
            .exclude(id=root.id)
            .delete()
        )

        deleted_seats, _ = Escanho.objects.filter(country_code=root.country_code).delete()

        self.stdout.write(self.style.WARNING(
            f"Eliminadas {deleted_areas} subdivisiones antiguas y "
            f"{deleted_seats} registros de escaños para '{root.country_code}'."
        ))

        created: list[NuevoAdminArea] = []
        self._build_tree(
            recipes=recipes,
            parent_id=root.id,
            country_id=country_id,
            m2m_field=m2m_field,
            code_prefix=code_prefix,
            created=created,
        )

        self._assign_escanhos(country_id)

        self.stdout.write(self.style.SUCCESS(f"Creado(s): {len(created)} subdivisión(es)."))
        for o in created:
            self.stdout.write(
                f"- {o.id} :: {o.name} :: code={o.code} :: level={o.level} "
                f":: area={o.area_km2} :: pop={o.pop_latest}"
            )

    def _build_tree(
        self,
        *,
        recipes,
        parent_id,
        country_id,
        m2m_field,
        code_prefix,
        created,
    ):
        for idx, r in enumerate(recipes, start=1):
            if not isinstance(r, dict):
                raise CommandError(
                    f"En CONFIGS['{country_id}'] bajo parent '{parent_id}', "
                    f"la entrada #{idx} no es un dict sino {type(r).__name__}: {r!r}"
                )

            name = r.get("name")
            if not name:
                raise CommandError(
                    f"La entrada #{idx} en CONFIGS['{country_id}'] no tiene 'name'."
                )

            capitals = r.get("capitals")
            if capitals is None and "capital" in r:
                capitals = [r["capital"]]

            parent = NuevoAdminArea.objects.get(id=parent_id)

            recipe_code = r.get("code")
            raw_code = recipe_code or (code_prefix + name)

            full_code = self._join_code(parent, raw_code)

            # id final: <country_code>-<code_jerárquico>
            new_id = f"{parent.country_code}-{full_code}"

            spec = r.get("spec")

            if spec is not None:
                obj = create_nuevo_area_from_spec(
                    parent_country_id=parent_id,
                    new_name=name,
                    include_spec=spec,
                    entity_type=r.get("entity_type"),
                    forced_area_km2=r.get("forced_area_km2"),
                    m2m_field=m2m_field,
                    new_code=full_code,  # <- guardamos code jerárquico
                    capitals=capitals,
                    auto_set_most_populated=r.get("auto_set_most_populated", True),
                )
            else:
                level = r.get("level")
                if level is None:
                    parent_level = parent.level
                    if parent_level is None:
                        level = NuevoAdminArea.Level.ADMIN1
                    else:
                        level = parent_level + 1

                if level < NuevoAdminArea.Level.COUNTRY or level > NuevoAdminArea.Level.ADMIN5:
                    raise CommandError(
                        f"Nivel {level} fuera de rango permitido (0..5) "
                        f"para '{name}' en CONFIGS['{country_id}']."
                    )

                obj, _ = NuevoAdminArea.objects.update_or_create(
                    id=new_id,
                    defaults={
                        "country_code": parent.country_code,
                        "code": full_code,
                        "name": name,
                        "level": level,
                        "entity_type": r.get("entity_type"),
                        "parent": parent,
                    },
                )

            created.append(obj)

            childs = r.get("childs") or []
            if childs:
                self._build_tree(
                    recipes=childs,
                    parent_id=obj.id,
                    country_id=country_id,
                    m2m_field=m2m_field,
                    code_prefix=code_prefix,
                    created=created,
                )

            # contenedores: agregados desde hijos
            if spec is None:
                agg = obj.children.aggregate(
                    total_area=Sum("area_km2"),
                    total_pop=Sum("pop_latest"),
                )
                total_area = _round_area(agg["total_area"])
                total_pop = agg["total_pop"]

                density = None
                if total_area is not None and total_pop is not None:
                    try:
                        if total_area != 0:
                            density = Decimal(total_pop) / Decimal(total_area)
                    except (InvalidOperation, ZeroDivisionError):
                        density = None

                obj.area_km2 = total_area
                obj.pop_latest = total_pop
                obj.density = density
                obj.save(update_fields=["area_km2", "pop_latest", "density"])

                self._assign_container_capitals(obj, capitals)

    def _assign_escanhos(self, country_id: str):
        cfg = ESCANHOS.get(country_id)
        if not cfg:
            return

        nivel = cfg.get("nivel")
        total_seats = cfg.get("escanhos")
        min_per = cfg.get("min", 0)

        if nivel is None or total_seats is None:
            raise CommandError(f"ESCANHOS['{country_id}'] debe tener 'nivel' y 'escanhos'.")

        try:
            root = NuevoAdminArea.objects.get(id=country_id)
        except NuevoAdminArea.DoesNotExist:
            raise CommandError(f"No existe NuevoAdminArea con id='{country_id}' para asignar escaños.")

        cod_pais = root.country_code

        qs = NuevoAdminArea.objects.filter(
            country_code=cod_pais,
            level=nivel,
        )

        n = qs.count()
        if n == 0:
            raise CommandError(
                f"No hay subdivisiones con country_code='{cod_pais}' y level={nivel} para asignar escaños."
            )

        if n * min_per > total_seats:
            raise CommandError(
                f"Imposible asignar escaños: {n} subdivisiones * min={min_per} "
                f"= {n * min_per} > total={total_seats}."
            )

        remaining = total_seats - n * min_per
        objs = list(qs.order_by("code"))
        total_pop = sum((o.pop_latest or 0) for o in objs)

        base = {o.id: min_per for o in objs}
        extra = {o.id: 0 for o in objs}

        if remaining > 0:
            if total_pop <= 0:
                base_extra = remaining // n
                rem = remaining % n
                for idx, o in enumerate(objs):
                    extra[o.id] = base_extra + (1 if idx < rem else 0)
            else:
                shares = []
                used = 0
                for o in objs:
                    pop = o.pop_latest or 0
                    if pop < 0:
                        pop = 0
                    share = remaining * (pop / total_pop)
                    floor_share = int(share)
                    extra[o.id] = floor_share
                    used += floor_share
                    shares.append((share - floor_share, o.id))

                leftover = remaining - used
                shares.sort(reverse=True)
                for frac, oid in shares[:leftover]:
                    extra[oid] += 1

        for o in objs:
            seats = base[o.id] + extra[o.id]
            Escanho.objects.update_or_create(
                country_code=o.country_code,
                subdivision_code=o.code,
                defaults={
                    "country_id": country_id,
                    "subdivision_id": o.id,
                    "seats": seats,
                },
            )
