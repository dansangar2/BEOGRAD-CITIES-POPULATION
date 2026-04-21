# ciudades_del_mundo/management/commands/export_nuevoadmin_csv.py

import csv
from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from ciudades_del_mundo.models import NuevoAdminArea, Escanho


import csv
from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.models import NuevoAdminArea, Escanho


import csv
from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.models import NuevoAdminArea, Escanho


class Command(BaseCommand):
    help = (
        "Exporta un CSV jerárquico de NuevoAdminArea para un país ficticio, con una fila "
        "por ruta País → Subnivel1 → Subnivel2 → ... , y bloques de columnas por cada nivel:\n"
        "- Tamaño (2 decimales) y población\n"
        "- % respecto al total del país y de la subdivisión superior\n"
        "- Ranking por nivel de país y entre hermanas\n"
        "- Capital(es) y ciudad más poblada, con población y % en columnas separadas\n"
        "- Escaños directos/agregados\n"
        "- Sin repetir valores para niveles superiores (ideal para combinar celdas en Excel)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--country-id",
            required=True,
            help=(
                "ID lógico del país/imperio en NuevoAdminArea "
                "(ej. 'first-spanish-empire'). Coincide con el id del nodo raíz."
            ),
        )
        parser.add_argument(
            "--max-level",
            type=int,
            default=None,
            help=(
                "Nivel máximo de NuevoAdminArea a incluir. "
                "Si se omite, se incluyen todos los niveles > nivel del nodo raíz."
            ),
        )
        parser.add_argument(
            "--name",
            default=None,
            help=(
                "Nombre base del fichero CSV (sin extensión). "
                "Si se omite, se usará: <country_code>_<YYYYMMDD_HHMMSS>"
            ),
        )

    def handle(self, *args, **opts):
        country_id = opts["country_id"]
        max_level = opts["max_level"]
        name = opts["name"]

        # ------------------------------------------------------------
        # 1) Nodo raíz (país ficticio)
        # ------------------------------------------------------------
        try:
            root = (
                NuevoAdminArea.objects
                .select_related("parent")
                .get(id=country_id)
            )
        except NuevoAdminArea.DoesNotExist:
            raise CommandError(
                f"No existe NuevoAdminArea con id='{country_id}'. "
                f"Ejecuta antes el comando de construcción de subdivisiones."
            )

        country_code = root.country_code
        root_level = root.level or 0

        # ------------------------------------------------------------
        # 2) Todas las subdivisiones a partir del root (filtradas por max_level)
        # ------------------------------------------------------------
        qs = (
            NuevoAdminArea.objects
            .filter(country_code=country_code)
            .exclude(id=root.id)
            .select_related("parent", "most_populate_city")
            .prefetch_related("capitals", "municipios_originales")
        )

        if max_level is not None:
            qs = qs.filter(level__gt=root_level, level__lte=max_level)
        else:
            qs = qs.filter(level__gt=root_level)

        areas = list(qs)
        if not areas:
            self.stdout.write(
                self.style.WARNING(
                    f"No se encontraron subdivisiones para country_code='{country_code}' "
                    f"por debajo de '{country_id}'."
                )
            )
            return

        # ------------------------------------------------------------
        # 3) Hijos por parent_id (para recorrer la jerarquía)
        # ------------------------------------------------------------
        children_by_parent: dict[str, list[NuevoAdminArea]] = defaultdict(list)
        for a in areas:
            children_by_parent[a.parent_id].append(a)

        # ------------------------------------------------------------
        # 4) Escaños directos por subdivisión (tabla Escanho)
        # ------------------------------------------------------------
        escanhos_qs = Escanho.objects.filter(country_id=country_id)
        seats_direct: dict[str, int] = {}
        for e in escanhos_qs:
            seats_direct[e.subdivision_id] = e.seats

        # Nivel de representación (si todos los escaños están en un mismo nivel)
        rep_level = None
        first_e = escanhos_qs.first()
        if first_e:
            try:
                rep_area = NuevoAdminArea.objects.get(id=first_e.subdivision_id)
                rep_level = rep_area.level
            except NuevoAdminArea.DoesNotExist:
                rep_level = None

        # ------------------------------------------------------------
        # 5) Escaños totales (directos + descendientes) bottom-up
        # ------------------------------------------------------------
        seats_total: dict[str, int] = {}

        areas_desc = sorted(areas, key=lambda a: (a.level or 0), reverse=True)
        for a in areas_desc:
            total = seats_direct.get(a.id, 0)
            for child in children_by_parent.get(a.id, []):
                total += seats_total.get(child.id, 0)
            seats_total[a.id] = total

        # ------------------------------------------------------------
        # 6) Totales y rankings por nivel (respecto al país)
        # ------------------------------------------------------------
        areas_by_level: dict[int, list[NuevoAdminArea]] = defaultdict(list)
        for a in areas:
            areas_by_level[a.level].append(a)

        rank_area_country: dict[str, int] = {}
        rank_pop_country: dict[str, int] = {}
        total_area_by_level: dict[int, float] = {}
        total_pop_by_level: dict[int, int] = {}

        for level, lvl_areas in areas_by_level.items():
            total_area = sum(
                float(a.area_km2) for a in lvl_areas if a.area_km2 is not None
            )
            total_pop = sum(
                int(a.pop_latest) for a in lvl_areas if a.pop_latest is not None
            )
            total_area_by_level[level] = total_area
            total_pop_by_level[level] = total_pop

            # Ranking por área
            sorted_by_area = sorted(
                lvl_areas,
                key=lambda a: float(a.area_km2) if a.area_km2 is not None else 0.0,
                reverse=True,
            )
            for idx, a in enumerate(sorted_by_area, start=1):
                rank_area_country[a.id] = idx

            # Ranking por población
            sorted_by_pop = sorted(
                lvl_areas,
                key=lambda a: int(a.pop_latest) if a.pop_latest is not None else 0,
                reverse=True,
            )
            for idx, a in enumerate(sorted_by_pop, start=1):
                rank_pop_country[a.id] = idx

        # ------------------------------------------------------------
        # 7) Rankings respecto a subdivisión superior (hermanas)
        # ------------------------------------------------------------
        rank_area_parent_level: dict[str, int] = {}
        rank_pop_parent_level: dict[str, int] = {}

        for parent_id, childs in children_by_parent.items():
            if not childs:
                continue

            # Área entre hermanas
            s_area = sorted(
                childs,
                key=lambda a: float(a.area_km2) if a.area_km2 is not None else 0.0,
                reverse=True,
            )
            for idx, a in enumerate(s_area, start=1):
                rank_area_parent_level[a.id] = idx

            # Población entre hermanas
            s_pop = sorted(
                childs,
                key=lambda a: int(a.pop_latest) if a.pop_latest is not None else 0,
                reverse=True,
            )
            for idx, a in enumerate(s_pop, start=1):
                rank_pop_parent_level[a.id] = idx

        # ------------------------------------------------------------
        # 8) Niveles incluidos y totales de país
        # ------------------------------------------------------------
        levels_sorted = sorted({a.level for a in areas})

        # Totales de país = suma de subniveles inmediatamente por debajo del root
        level_children_root = root_level + 1
        lvl_root_children = [a for a in areas if a.level == level_children_root]
        country_area_total = sum(
            float(a.area_km2) for a in lvl_root_children if a.area_km2 is not None
        )
        country_pop_total = sum(
            int(a.pop_latest) for a in lvl_root_children if a.pop_latest is not None
        )
        country_area_total_str = (
            f"{country_area_total:.2f}" if country_area_total else ""
        )
        country_pop_total_str = str(country_pop_total) if country_pop_total else ""

        # ------------------------------------------------------------
        # 9) Preparar nombre de fichero
        # ------------------------------------------------------------
        if not name:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"{country_code}_{stamp}"
        else:
            base_name = name

        filename = f"{base_name}.csv"

        # ------------------------------------------------------------
        # 10) Construir todas las rutas desde el root (una fila por ruta)
        # ------------------------------------------------------------
        paths: list[list[NuevoAdminArea]] = []

        def dfs(node: NuevoAdminArea, path: list[NuevoAdminArea]):
            new_path = path + [node]
            childs = children_by_parent.get(node.id, [])
            if max_level is not None:
                childs = [c for c in childs if c.level <= max_level]

            if not childs:
                paths.append(new_path)
            else:
                for child in sorted(
                    childs,
                    key=lambda c: (c.level or 0, c.code),
                ):
                    dfs(child, new_path)

        root_children = children_by_parent.get(root.id, [])
        if max_level is not None:
            root_children = [c for c in root_children if c.level <= max_level]

        for top in sorted(
            root_children,
            key=lambda c: (c.level or 0, c.code),
        ):
            dfs(top, [])

        if not paths:
            self.stdout.write(
                self.style.WARNING(
                    "No se han encontrado rutas desde el país hasta el nivel solicitado."
                )
            )
            return

        # ------------------------------------------------------------
        # 11) Cabeceras: país + bloque por nivel
        #      Para L1 no incluimos pct_area_superior / pct_pob_superior
        #      Capital / ciudad más poblada con columnas separadas
        # ------------------------------------------------------------
        header = [
            "pais_nombre",
            "pais_area_total_km2",
            "pais_pob_total",
        ]

        columns_by_level: dict[int, list[str]] = {}

        for level in levels_sorted:
            rel = level - root_level  # nivel relativo (1,2,3,...)
            prefix = f"L{rel}"
            cols: list[str] = []
            cols.append(f"{prefix}_tipo")
            cols.append(f"{prefix}_nombre")
            cols.append(f"{prefix}_area_km2")
            cols.append(f"{prefix}_pct_area_pais")          # % sobre total país (nivel)
            if rel > 1:
                cols.append(f"{prefix}_pct_area_superior")  # % sobre subdivisión superior
            cols.append(f"{prefix}_rank_area_pais")
            cols.append(f"{prefix}_rank_area_superior")
            cols.append(f"{prefix}_pob")
            cols.append(f"{prefix}_pct_pob_pais")
            if rel > 1:
                cols.append(f"{prefix}_pct_pob_superior")
            cols.append(f"{prefix}_rank_pob_pais")
            cols.append(f"{prefix}_rank_pob_superior")
            cols.append(f"{prefix}_num_subdivisiones_hijas")  # hijas o municipios
            # Capital: nombre(s), población total, % sobre población de la subdivisión
            cols.append(f"{prefix}_capitales_nombres")
            cols.append(f"{prefix}_capitales_pob")
            cols.append(f"{prefix}_capitales_pct_pob_sub")
            # Ciudad más poblada: nombre, población, % sobre población de la subdivisión
            cols.append(f"{prefix}_ciudad_mas_poblada_nombre")
            cols.append(f"{prefix}_ciudad_mas_poblada_pob")
            cols.append(f"{prefix}_ciudad_mas_poblada_pct_pob_sub")
            # Escaños
            cols.append(f"{prefix}_escanhos")

            columns_by_level[level] = cols
            header.extend(cols)

        # ------------------------------------------------------------
        # 12) Generar CSV
        #     - País solo en la primera fila
        #     - Cada Lx_* solo en la primera fila donde cambie esa subdivisión
        # ------------------------------------------------------------
        prev_country_written = False
        prev_area_by_level: dict[int, NuevoAdminArea] = {}

        with open(filename, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(header)

            for path in paths:
                path_by_level = {a.level: a for a in path}

                # País: solo en la primera fila de datos
                if not prev_country_written:
                    row = [
                        root.name,
                        country_area_total_str,
                        country_pop_total_str,
                    ]
                    prev_country_written = True
                else:
                    row = ["", "", ""]

                for level in levels_sorted:
                    cols_for_level = columns_by_level[level]
                    rel = level - root_level
                    a = path_by_level.get(level)

                    if not a:
                        # Nivel no presente en esta ruta
                        row.extend([""] * len(cols_for_level))
                        continue

                    # Si es la misma subdivisión que en la fila anterior en este nivel,
                    # no repetimos datos (para que puedas unir celdas en Excel)
                    prev_a = prev_area_by_level.get(level)
                    if prev_a is not None and prev_a.id == a.id:
                        row.extend([""] * len(cols_for_level))
                        continue

                    prev_area_by_level[level] = a

                    # ---- Datos de la subdivisión ----
                    entity_type = a.entity_type or ""
                    name_sub = a.name

                    # Área (2 decimales)
                    area_val = float(a.area_km2) if a.area_km2 is not None else None
                    area_str = f"{area_val:.2f}" if area_val is not None else ""

                    # % área respecto al país (por nivel), como porcentaje (0–100)
                    total_area_level = total_area_by_level.get(level, 0.0)
                    pct_area_pais = None
                    if area_val is not None and total_area_level:
                        pct_area_pais = (area_val / total_area_level) * 100.0
                    pct_area_pais_str = (
                        f"{pct_area_pais:.2f}" if pct_area_pais is not None else ""
                    )

                    # % área respecto a la subdivisión superior (solo rel > 1)
                    pct_area_superior = None
                    if rel > 1 and a.parent and a.parent.area_km2 is not None and area_val is not None:
                        parent_area = float(a.parent.area_km2)
                        if parent_area:
                            pct_area_superior = (area_val / parent_area) * 100.0
                    pct_area_superior_str = (
                        f"{pct_area_superior:.2f}"
                        if pct_area_superior is not None and rel > 1
                        else ""
                    )

                    # Rankings área
                    rank_area_pais = rank_area_country.get(a.id)
                    rank_area_sup = rank_area_parent_level.get(a.id)
                    rank_area_pais_str = str(rank_area_pais) if rank_area_pais else ""
                    rank_area_sup_str = str(rank_area_sup) if rank_area_sup else ""

                    # Población
                    pop_val = int(a.pop_latest) if a.pop_latest is not None else None
                    pop_str = str(pop_val) if pop_val is not None else ""

                    # % población respecto al país (por nivel)
                    total_pop_level = total_pop_by_level.get(level, 0)
                    pct_pob_pais = None
                    if pop_val is not None and total_pop_level:
                        pct_pob_pais = (pop_val / total_pop_level) * 100.0
                    pct_pob_pais_str = (
                        f"{pct_pob_pais:.2f}" if pct_pob_pais is not None else ""
                    )

                    # % población respecto a subdivisión superior (solo rel > 1)
                    pct_pob_superior = None
                    if rel > 1 and a.parent and a.parent.pop_latest is not None and pop_val is not None:
                        parent_pop = int(a.parent.pop_latest)
                        if parent_pop:
                            pct_pob_superior = (pop_val / parent_pop) * 100.0
                    pct_pob_superior_str = (
                        f"{pct_pob_superior:.2f}"
                        if pct_pob_superior is not None and rel > 1
                        else ""
                    )

                    # Rankings población
                    rank_pob_pais = rank_pop_country.get(a.id)
                    rank_pob_sup = rank_pop_parent_level.get(a.id)
                    rank_pob_pais_str = str(rank_pob_pais) if rank_pob_pais else ""
                    rank_pob_sup_str = str(rank_pob_sup) if rank_pob_sup else ""

                    # Número de hijas:
                    #   - si tiene hijas NuevoAdminArea → nº hijas
                    #   - si no tiene hijas → nº de municipios_originales
                    direct_children = children_by_parent.get(a.id, [])
                    if direct_children:
                        num_children = len(direct_children)
                    else:
                        num_children = a.municipios_originales.count()
                    num_children_str = str(num_children) if num_children else ""

                    # Capitales:
                    #   - nombres separados por " | "
                    #   - población total de capitales
                    #   - % sobre población de la subdivisión
                    capitals_qs = list(a.capitals.all())
                    capitals_names = ""
                    capitals_pop_total = 0
                    if capitals_qs:
                        capitals_names = " | ".join(sorted(c.name for c in capitals_qs))
                        for cap in capitals_qs:
                            if cap.pop_latest is not None:
                                capitals_pop_total += int(cap.pop_latest)
                    capitals_pop_str = (
                        str(capitals_pop_total) if capitals_pop_total else ""
                    )
                    capitals_pct_sub = None
                    if pop_val and capitals_pop_total:
                        capitals_pct_sub = (capitals_pop_total / pop_val) * 100.0
                    capitals_pct_sub_str = (
                        f"{capitals_pct_sub:.2f}" if capitals_pct_sub is not None else ""
                    )

                    # Ciudad más poblada:
                    #   - nombre
                    #   - población
                    #   - % sobre población de la subdivisión
                    most_city_name = ""
                    most_city_pop_val = None
                    if a.most_populate_city:
                        mc = a.most_populate_city
                        most_city_name = mc.name
                        if mc.pop_latest is not None:
                            most_city_pop_val = int(mc.pop_latest)
                    most_city_pop_str = (
                        str(most_city_pop_val) if most_city_pop_val is not None else ""
                    )
                    most_city_pct_sub = None
                    if pop_val and most_city_pop_val:
                        most_city_pct_sub = (most_city_pop_val / pop_val) * 100.0
                    most_city_pct_sub_str = (
                        f"{most_city_pct_sub:.2f}"
                        if most_city_pct_sub is not None
                        else ""
                    )

                    # Escaños a mostrar
                    if rep_level is not None and a.level == rep_level:
                        seats_show = seats_direct.get(a.id, 0)
                    else:
                        seats_show = seats_total.get(a.id, 0)
                    seats_str = str(seats_show) if seats_show else ""

                    # Bloque de columnas según el nivel (L1 sin %_superior)
                    block: list[str] = []
                    block.append(entity_type)
                    block.append(name_sub)
                    block.append(area_str)
                    block.append(pct_area_pais_str)
                    if rel > 1:
                        block.append(pct_area_superior_str)
                    block.append(rank_area_pais_str)
                    block.append(rank_area_sup_str)
                    block.append(pop_str)
                    block.append(pct_pob_pais_str)
                    if rel > 1:
                        block.append(pct_pob_superior_str)
                    block.append(rank_pob_pais_str)
                    block.append(rank_pob_sup_str)
                    block.append(num_children_str)
                    block.append(capitals_names)
                    block.append(capitals_pop_str)
                    block.append(capitals_pct_sub_str)
                    block.append(most_city_name)
                    block.append(most_city_pop_str)
                    block.append(most_city_pct_sub_str)
                    block.append(seats_str)

                    # Por seguridad, igualamos longitud al esquema de cabecera
                    if len(block) != len(cols_for_level):
                        block += [""] * (len(cols_for_level) - len(block))

                    row.extend(block)

                writer.writerow(row)

        self.stdout.write(self.style.SUCCESS(f"CSV generado: {filename}"))