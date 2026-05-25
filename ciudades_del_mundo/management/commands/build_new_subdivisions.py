# ciudades_del_mundo/management/commands/build_new_subdivisions.py

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal, InvalidOperation
from collections import defaultdict

from ciudades_del_mundo.models import NuevoAdminArea, AdminArea
import importlib
import pkgutil
from ciudades_del_mundo.infrastructure.scraping.python_config_repository import PythonScrapingConfigRepository
from ciudades_del_mundo.services.nuevo_admin_builder import (
    create_nuevo_area_from_spec,
    refresh_nuevo_admin_most_populated,
    _add_capital_name_overrides,
    _label_city_preference_and_names,
    _resolve_adminarea_in_qs,
    _round_area,
    _norm,
)
from ciudades_del_mundo.services.nuevo_admin_representatives import (
    assign_nuevo_admin_representatives,
    representation_config_from_mapping,
)
import ciudades_del_mundo.historical_divisions as subdivisions_pkg
import ciudades_del_mundo.new_subdivisions as new_subdivisions_pkg


CONFIGS: dict[str, list[dict]] = {}
CONFIG_LOAD_ERRORS: dict[str, Exception] = {}
REPRESENTATIONS: dict[str, object] = {}
MUNICIPAL_LEVEL: dict[str, int] = {}
SOURCE_COUNTRIES: dict[str, str] = {}
POPULATION_INDEXES: dict[str, object] = {}
PROVINCE_STATUSES: dict[str, object] = {}
LEGAL_SUBDIVISION_LEVELS: dict[str, int | None] = {}
SCRAPING_CONFIG_REPOSITORY = PythonScrapingConfigRepository()


# Cargar módulos de configuración
def _load_config_package(package):
    for module_info in pkgutil.iter_modules(package.__path__):
        mod_name = module_info.name
        full_name = f"{package.__name__}.{module_info.name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as exc:
            CONFIG_LOAD_ERRORS[mod_name] = exc
            continue

        divs = getattr(mod, "DIVISIONS", None)
        if divs:
            CONFIGS[mod_name] = divs

        representation = getattr(mod, "REPRESENTATION", None)
        if representation is None:
            representation = getattr(mod, "ESCANHOS", None)
        parsed_representation = representation_config_from_mapping(representation)
        if parsed_representation:
            REPRESENTATIONS[mod_name] = parsed_representation

        munl = getattr(mod, "MUNICIPAL_LEVEL", None)
        if munl is not None:
            MUNICIPAL_LEVEL[mod_name] = munl

        source_country = getattr(mod, "SOURCE_COUNTRY", None)
        if source_country:
            SOURCE_COUNTRIES[mod_name] = source_country

        for attr in ("POPULATION_INDEXES", "POPULATION_INDEX", "POPULATION_MULTIPLIERS", "INDICES_POBLACION"):
            population_indexes = getattr(mod, attr, None)
            if population_indexes is not None:
                POPULATION_INDEXES[mod_name] = population_indexes
                break

        for attr in ("PROVINCE_STATUSES", "PROVINCE_STATUS", "ESTADOS_PROVINCIA"):
            province_statuses = getattr(mod, attr, None)
            if province_statuses is not None:
                PROVINCE_STATUSES[mod_name] = province_statuses
                break


def _source_country_for(country_id: str) -> str:
    configured = SOURCE_COUNTRIES.get(country_id)
    if configured:
        return configured
    if country_id.startswith("spanish_") or country_id.startswith("spain_"):
        return "spain"
    if country_id.startswith("morocco_"):
        return "morocco"
    return country_id


def _legal_subdivision_level(country_code: str) -> int | None:
    if country_code in LEGAL_SUBDIVISION_LEVELS:
        return LEGAL_SUBDIVISION_LEVELS[country_code]

    try:
        config = SCRAPING_CONFIG_REPOSITORY.get(country_code)
    except Exception:
        LEGAL_SUBDIVISION_LEVELS[country_code] = None
        return None

    level = config.legal_subdivision_level
    LEGAL_SUBDIVISION_LEVELS[country_code] = level
    return level


def _dat_to_spec(dat, source_country: str):
    if dat is None:
        return None
    if not isinstance(dat, dict):
        raise CommandError(
            f"'dat' debe ser un dict {{nivel: [nombres]}} o {{nivel: {{pais: [nombres]}}}}, "
            f"recibido {type(dat).__name__}."
        )

    spec = {}
    for level, value in dat.items():
        if isinstance(value, dict):
            spec[level] = value
        else:
            spec[level] = {source_country: value}
    return spec


def _iter_dat_countries(dat, source_country: str):
    if not dat:
        return
    for value in dat.values():
        if isinstance(value, dict):
            yield from value.keys()
        else:
            yield source_country


def _iter_spec_countries(spec):
    if not spec:
        return
    for level, value in spec.items():
        if level == "restar":
            for restar_value in (value or {}).values():
                if isinstance(restar_value, dict):
                    yield from restar_value.keys()
            continue
        if isinstance(value, dict):
            yield from value.keys()


def _iter_recipe_source_countries(recipe, source_country: str):
    if isinstance(recipe, list):
        for child in recipe:
            yield from _iter_recipe_source_countries(child, source_country)
        return

    yield from _iter_dat_countries(recipe.get("dat"), source_country)
    yield from _iter_spec_countries(recipe.get("spec"))
    for child in recipe.get("childs") or []:
        yield from _iter_recipe_source_countries(child, source_country)


def _parse_population_index(value, *, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CommandError(f"El indice de poblacion para '{label}' debe ser numerico.") from exc
    if parsed < 0:
        raise CommandError(f"El indice de poblacion para '{label}' no puede ser negativo.")
    return parsed


def _normalize_province_status(value) -> str:
    normalized = _norm(str(value or "normal"))
    aliases = {
        "normal": NuevoAdminArea.ProvinceStatus.NORMAL,
        "provincia": NuevoAdminArea.ProvinceStatus.NORMAL,
        "dependency": NuevoAdminArea.ProvinceStatus.DEPENDENCY,
        "dependencia": NuevoAdminArea.ProvinceStatus.DEPENDENCY,
        "dependent": NuevoAdminArea.ProvinceStatus.DEPENDENCY,
        "territory": NuevoAdminArea.ProvinceStatus.TERRITORY,
        "territorio": NuevoAdminArea.ProvinceStatus.TERRITORY,
    }
    if normalized not in aliases:
        raise CommandError(
            f"Estado de provincia no soportado: {value!r}. "
            "Usa normal, dependencia o territorio."
        )
    return aliases[normalized]


def _recipe_population_index(recipe: dict) -> Decimal | None:
    for key in ("population_index", "population_multiplier", "indice_poblacion"):
        if key in recipe:
            return _parse_population_index(recipe[key], label=recipe.get("name", "?"))
    return None


def _recipe_province_status(recipe: dict) -> str | None:
    for key in ("province_status", "estado_provincia", "status"):
        if key in recipe:
            return _normalize_province_status(recipe[key])
    return None


def _recipe_depends_on(recipe: dict) -> str | None:
    for key in ("depends_on", "depende_de", "dependency_of"):
        value = recipe.get(key)
        if value:
            return str(value)
    return None


def _recipe_city_merge_status_preference(recipe: dict):
    for key in ("prefer_city_merge_status", "city_merge_status", "merge_status"):
        if key in recipe:
            return recipe[key]
    return None


def _iter_level_mapping(raw_config, *, config_name: str):
    if not raw_config:
        return
    if not isinstance(raw_config, dict):
        raise CommandError(f"{config_name} debe ser un dict {{nivel: {{nombre: valor}}}}.")

    for raw_level, entries in raw_config.items():
        try:
            level = int(raw_level)
        except (TypeError, ValueError) as exc:
            raise CommandError(f"{config_name} tiene un nivel invalido: {raw_level!r}.") from exc

        if not isinstance(entries, dict):
            raise CommandError(
                f"{config_name}[{raw_level!r}] debe ser un dict {{nombre: valor}}."
            )

        for label, value in entries.items():
            yield level, str(label), value


_load_config_package(subdivisions_pkg)
_load_config_package(new_subdivisions_pkg)


class Command(BaseCommand):
    help = (
        "Construye nuevas subdivisiones en NuevoAdminArea a partir de AdminArea "
        "para un país dado, respetando jerarquías definidas con 'childs' y "
        "asignando escaños según REPRESENTATION/ESCANHOS.\n"
        "- Si el país (nodo raíz) no existe en NuevoAdminArea, se crea.\n"
        "- Si existe, se actualiza.\n"
        "- Antes de crear subdivisiones, se borran TODAS las subdivisiones "
        "relacionadas con ese país, dejando solo el nodo raíz.\n"
        "- Las recetas con 'spec' o 'dat' agregan datos desde AdminArea.\n"
        "- Las recetas sin 'spec' ni 'dat' se crean como contenedores y, al final, "
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

    def _assign_container_capitals(self, obj: NuevoAdminArea, capitals, source_countries):
        if not capitals:
            return

        cap_objs = []
        capital_name_overrides: dict[str, dict[str, str]] = {}
        countries = list(dict.fromkeys(source_countries or []))
        for raw_capital in capitals:
            if not raw_capital:
                continue
            label, capital_preference, names_by_language = _label_city_preference_and_names(raw_capital)
            label = label.strip()
            if not label:
                continue

            for country_code in countries:
                legal_level = _legal_subdivision_level(country_code)
                candidate_q = AdminArea.objects.filter(country_code=country_code)
                if legal_level is not None:
                    candidate_q = candidate_q.filter(level=legal_level)

                candidate = _resolve_adminarea_in_qs(
                    candidate_q,
                    label,
                    city_merge_status_preference=capital_preference,
                )
                if candidate:
                    cap_objs.append(candidate)
                    _add_capital_name_overrides(
                        capital_name_overrides,
                        candidate,
                        names_by_language,
                    )
                    break

        if cap_objs:
            obj.capitals.set(cap_objs)
            obj.capital_names_by_language = capital_name_overrides

            top_cap = None
            for c in cap_objs:
                if c.pop_latest is None:
                    continue
                if top_cap is None or c.pop_latest > top_cap.pop_latest:
                    top_cap = c

            if top_cap is not None:
                obj.most_populate_city = top_cap
                obj.save(update_fields=["most_populate_city", "capital_names_by_language"])
            else:
                obj.save(update_fields=["capital_names_by_language"])
        else:
            obj.capitals.clear()
            obj.capital_names_by_language = {}
            obj.save(update_fields=["capital_names_by_language"])

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
        source_country = _source_country_for(country_id)

        recipes = CONFIGS.get(country_id)
        if not recipes:
            if country_id in CONFIG_LOAD_ERRORS:
                exc = CONFIG_LOAD_ERRORS[country_id]
                raise CommandError(
                    f"No se pudo cargar la configuración para el país '{country_id}': "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
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
                if isinstance(r, list):
                    yield from _iter_full_codes(r, parent_full_code, prefix_path)
                    continue

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
            "population_index": Decimal("1"),
            "province_status": NuevoAdminArea.ProvinceStatus.NORMAL,
            "depends_on": None,
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

        NuevoAdminArea.objects.filter(country_code=root.country_code).update(representatives=None)

        self.stdout.write(self.style.WARNING(
            f"Eliminadas {deleted_areas} subdivisiones antiguas para '{root.country_code}'."
        ))

        created: list[NuevoAdminArea] = []
        pending_dependencies: list[tuple[str, str]] = []
        self._build_tree(
            recipes=recipes,
            parent_id=root.id,
            country_id=country_id,
            source_country=source_country,
            m2m_field=m2m_field,
            code_prefix=code_prefix,
            created=created,
            pending_dependencies=pending_dependencies,
            inherited_province_status=None,
        )

        self._resolve_pending_dependencies(country_id, pending_dependencies)
        self._apply_population_index_config(country_id, POPULATION_INDEXES.get(country_id))
        self._apply_province_status_config(country_id, PROVINCE_STATUSES.get(country_id))
        self._validate_province_dependencies(country_id)
        self._assign_escanhos(country_id)
        most_populated_updated = refresh_nuevo_admin_most_populated(root.country_code)

        self.stdout.write(self.style.SUCCESS(f"Creado(s): {len(created)} subdivisión(es)."))
        self.stdout.write(self.style.SUCCESS(
            f"Ciudad mas poblada actualizada en {most_populated_updated} area(s)."
        ))
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
        source_country,
        m2m_field,
        code_prefix,
        created,
        pending_dependencies,
        inherited_province_status,
    ):
        for idx, r in enumerate(recipes, start=1):
            if isinstance(r, list):
                self._build_tree(
                    recipes=r,
                    parent_id=parent_id,
                    country_id=country_id,
                    source_country=source_country,
                    m2m_field=m2m_field,
                    code_prefix=code_prefix,
                    created=created,
                    pending_dependencies=pending_dependencies,
                    inherited_province_status=inherited_province_status,
                )
                continue

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
            if spec is None:
                spec = _dat_to_spec(r.get("dat"), source_country)

            if spec is not None:
                source_countries = list(dict.fromkeys(_iter_spec_countries(spec)))
                if not source_countries:
                    source_countries = [source_country]
                capital_levels = {}
                for cc in source_countries:
                    legal_level = _legal_subdivision_level(cc)
                    if legal_level is not None:
                        capital_levels[cc] = legal_level

                try:
                    obj = create_nuevo_area_from_spec(
                        parent_country_id=parent_id,
                        new_name=name,
                        include_spec=spec,
                        entity_type=r.get("entity_type"),
                        forced_area_km2=r.get("forced_area_km2"),
                        m2m_field=m2m_field,
                        new_code=full_code,  # <- guardamos code jerárquico
                        capitals=capitals,
                        capital_level_by_country=capital_levels,
                        auto_set_most_populated=r.get("auto_set_most_populated", True),
                        city_merge_status_preference=_recipe_city_merge_status_preference(r),
                    )
                except ValueError as exc:
                    raise CommandError(str(exc)) from exc
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
                        "population_index": Decimal("1"),
                        "province_status": NuevoAdminArea.ProvinceStatus.NORMAL,
                        "depends_on": None,
                    },
                )

            province_status = self._apply_recipe_metadata(
                obj,
                r,
                pending_dependencies,
                inherited_province_status=inherited_province_status,
            )
            created.append(obj)

            childs = r.get("childs") or []
            if childs:
                child_inherited_province_status = self._province_status_for_childs(
                    province_status,
                    inherited_province_status,
                )
                self._build_tree(
                    recipes=childs,
                    parent_id=obj.id,
                    country_id=country_id,
                    source_country=source_country,
                    m2m_field=m2m_field,
                    code_prefix=code_prefix,
                    created=created,
                    pending_dependencies=pending_dependencies,
                    inherited_province_status=child_inherited_province_status,
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

                source_countries = list(_iter_recipe_source_countries(r, source_country))
                if not source_countries:
                    source_countries = [source_country]
                self._assign_container_capitals(obj, capitals, source_countries)

    def _apply_recipe_metadata(
        self,
        obj: NuevoAdminArea,
        recipe: dict,
        pending_dependencies: list[tuple[str, str]],
        *,
        inherited_province_status: str | None = None,
    ) -> str | None:
        update_fields: list[str] = []

        population_index = _recipe_population_index(recipe)
        if population_index is not None:
            obj.population_index = population_index
            update_fields.append("population_index")

        depends_on_label = _recipe_depends_on(recipe)
        province_status = _recipe_province_status(recipe)
        if depends_on_label and province_status is None:
            province_status = NuevoAdminArea.ProvinceStatus.DEPENDENCY
        elif province_status is None:
            province_status = inherited_province_status

        if province_status is not None:
            obj.province_status = province_status
            update_fields.append("province_status")
            if province_status != NuevoAdminArea.ProvinceStatus.DEPENDENCY:
                obj.depends_on = None
                update_fields.append("depends_on")

        if update_fields:
            obj.save(update_fields=list(dict.fromkeys(update_fields)))

        if depends_on_label:
            pending_dependencies.append((obj.id, depends_on_label))

        return province_status

    def _province_status_for_childs(
        self,
        province_status: str | None,
        inherited_province_status: str | None,
    ) -> str | None:
        if province_status == NuevoAdminArea.ProvinceStatus.DEPENDENCY:
            return inherited_province_status
        return province_status

    def _resolve_nuevo_area(self, country_id: str, level: int, label: str) -> NuevoAdminArea:
        qs = NuevoAdminArea.objects.filter(country_code=country_id, level=level).order_by("code")

        obj = qs.filter(id=label).first()
        if obj:
            return obj

        obj = qs.filter(code__iexact=label).first()
        if obj:
            return obj

        obj = qs.filter(name__iexact=label).first()
        if obj:
            return obj

        normalized_label = _norm(label)
        for candidate in qs.only("id", "code", "name", "level", "country_code"):
            if normalized_label in {_norm(candidate.id), _norm(candidate.code), _norm(candidate.name)}:
                return candidate

        raise CommandError(
            f"No se encontro NuevoAdminArea nivel {level} con id, codigo o nombre '{label}' "
            f"en '{country_id}'."
        )

    def _set_area_status(
        self,
        *,
        country_id: str,
        area: NuevoAdminArea,
        province_status: str,
        depends_on_label: str | None = None,
    ):
        area.province_status = province_status
        update_fields = ["province_status"]

        if province_status != NuevoAdminArea.ProvinceStatus.DEPENDENCY:
            area.depends_on = None
            update_fields.append("depends_on")

        area.save(update_fields=update_fields)

        if province_status == NuevoAdminArea.ProvinceStatus.DEPENDENCY and depends_on_label:
            target = self._resolve_nuevo_area(country_id, area.level, depends_on_label)
            if target.id == area.id:
                raise CommandError(f"La dependencia '{area.name}' no puede depender de si misma.")
            area.depends_on = target
            area.save(update_fields=["depends_on"])

    def _resolve_pending_dependencies(
        self,
        country_id: str,
        pending_dependencies: list[tuple[str, str]],
    ):
        for area_id, depends_on_label in pending_dependencies:
            area = NuevoAdminArea.objects.get(id=area_id)
            self._set_area_status(
                country_id=country_id,
                area=area,
                province_status=NuevoAdminArea.ProvinceStatus.DEPENDENCY,
                depends_on_label=depends_on_label,
            )

    def _apply_population_index_config(self, country_id: str, raw_config):
        for level, label, raw_value in _iter_level_mapping(
            raw_config,
            config_name="POPULATION_INDEXES",
        ):
            area = self._resolve_nuevo_area(country_id, level, label)
            area.population_index = _parse_population_index(raw_value, label=label)
            area.save(update_fields=["population_index"])

    def _status_config_value(self, value) -> tuple[str, str | None]:
        if isinstance(value, dict):
            depends_on_label = (
                value.get("depends_on")
                or value.get("depende_de")
                or value.get("dependency_of")
            )
            raw_status = (
                value.get("status")
                or value.get("province_status")
                or value.get("estado_provincia")
                or (NuevoAdminArea.ProvinceStatus.DEPENDENCY if depends_on_label else None)
            )
            return _normalize_province_status(raw_status), (
                str(depends_on_label) if depends_on_label else None
            )

        if isinstance(value, (list, tuple)):
            if not value:
                raise CommandError("El estado de provincia no puede ser una lista vacia.")
            raw_status = value[0]
            depends_on_label = str(value[1]) if len(value) > 1 and value[1] else None
            return _normalize_province_status(raw_status), depends_on_label

        return _normalize_province_status(value), None

    def _apply_province_status_config(self, country_id: str, raw_config):
        for level, label, raw_value in _iter_level_mapping(
            raw_config,
            config_name="PROVINCE_STATUSES",
        ):
            area = self._resolve_nuevo_area(country_id, level, label)
            province_status, depends_on_label = self._status_config_value(raw_value)
            self._set_area_status(
                country_id=country_id,
                area=area,
                province_status=province_status,
                depends_on_label=depends_on_label,
            )

    def _validate_province_dependencies(self, country_id: str):
        areas = list(NuevoAdminArea.objects.filter(country_code=country_id))
        areas_by_id = {area.id: area for area in areas}

        for area in areas:
            if area.province_status != NuevoAdminArea.ProvinceStatus.DEPENDENCY:
                continue

            if not area.depends_on_id:
                raise CommandError(
                    f"La dependencia '{area.name}' debe indicar de que provincia depende."
                )
            if area.depends_on_id == area.id:
                raise CommandError(f"La dependencia '{area.name}' no puede depender de si misma.")

            target = areas_by_id.get(area.depends_on_id)
            if target is None:
                raise CommandError(
                    f"La dependencia '{area.name}' apunta a '{area.depends_on_id}', "
                    "pero esa provincia no existe en el mismo pais."
                )
            if target.level != area.level:
                raise CommandError(
                    f"La dependencia '{area.name}' debe depender de otra provincia del mismo nivel."
                )
            if target.province_status == NuevoAdminArea.ProvinceStatus.TERRITORY:
                raise CommandError(
                    f"La dependencia '{area.name}' no puede depender del territorio '{target.name}'."
                )

            seen: set[str] = set()
            current = area
            while current.province_status == NuevoAdminArea.ProvinceStatus.DEPENDENCY:
                if current.id in seen:
                    raise CommandError(f"Dependencia circular detectada en '{area.name}'.")
                seen.add(current.id)
                current = areas_by_id.get(current.depends_on_id)
                if current is None:
                    break

    def _assign_escanhos(self, country_id: str):
        cfg = REPRESENTATIONS.get(country_id)
        if not cfg:
            return

        try:
            updated = assign_nuevo_admin_representatives(country_id, cfg)
        except NuevoAdminArea.DoesNotExist:
            raise CommandError(f"No existe NuevoAdminArea con id='{country_id}' para asignar escaños.")
        except ValueError as exc:
            raise CommandError(str(exc))

        if updated == 0:
            raise CommandError(
                f"No hay subdivisiones actualizables en level={cfg.level} para asignar escaños."
            )
        return


