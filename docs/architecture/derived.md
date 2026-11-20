# Derived hierarchies and allocation

Detailed repository reference. Start with `AGENTS.md` and read only the relevant section.

## Entidades guardadas

El panel de configuración de países derivados reutiliza el modal para crear y
editar entidades. La selección de origen carga grupos por país y el selector de
capitales usa los orígenes y los hijos seleccionados. Al editar se mantienen las
selecciones que el usuario dejó intactas. Una entidad sin orígenes puede agregar
sus hijos y una plantilla manual conserva las métricas introducidas y suma las
que faltan. Los orígenes explícitos conservan prioridad al añadir hijos.

El servicio `new_country_entities.py` opera sobre ramas guardadas. Mover una
rama conserva sus identificadores. Eliminar una entidad promueve sus hijos
conservando sus IDs. Se rechazan padres ajenos y duplicados sin dejar filas
parciales. El servicio `derived_country_group_sync.py` recalcula restas
automáticas de grupos y distingue las restas introducidas manualmente.

El editor conserva sus propietarios Django en ambos modos. Los escenarios
están en [test_new_country_entity_modal.py](../../ciudades_del_mundo/tests/test_new_country_entity_modal.py).

## Derived Hierarchy Recipes

Active declarative recipes live in SQL `DerivedCountryConfig`,
`DerivedSubdivision` and `SubdivisionGroup`. The following TOML files are
explicit import/export and bootstrap bridges:

```text
ciudades_del_mundo/new_country_configs/*.toml
ciudades_del_mundo/subdivision_groups/groups/<country>.toml
ciudades_del_mundo/subdivision_groups/subdivisions/<country>.toml
```

Some bridge files contain normalized metadata plus embedded legacy Python under
`[legacy]` where the declarative builder cannot express the logic. The legacy
`build_new_subdivisions` command uses the matching Python modules:

```text
ciudades_del_mundo/new_subdivisions/*.py
ciudades_del_mundo/historical_divisions/*.py
```

These matching Python files are tracked for compatibility. Backup copies live
in `historical_divisions_old/` and `new_subdivisions_old/` and are git-ignored.
The new declarative base has TOML seeds in
`new_country_configs/*.toml` and importable group seeds in
`subdivision_groups/groups/<country>.toml`, plus SQL models
`DerivedCountryConfig`, `SubdivisionGroup` and `DerivedSubdivision`; legacy
subdivision bundles are kept separately in
`subdivision_groups/subdivisions/<country>.toml` and are imported by
`/subdivisions/`. `build_derived_subdivisions <country> --force` consumes SQL
`DerivedSubdivision` rows and rebuilds `NuevoAdminArea` for the same
`country_code`; the legacy `build_new_subdivisions` command still imports both
Python packages when present and loads modules that define `DIVISIONS`.
Historical modules can also be directly buildable if they expose `DIVISIONS`.
If one of these local packages is absent, the command skips it; building a
specific derived country still requires a local recipe module that exposes
`DIVISIONS`.

Common module globals:

- `ROOT_NAME` or `COUNTRY_NAME`: optional display name for the root
  `NuevoAdminArea`; if omitted, the build command derives one from the recipe
  slug by replacing `_`/`-` with spaces and title-casing.
- `SOURCE_COUNTRY`: source `AdminArea.country_code`; inferred for some names if
  omitted (`spanish_*` and `spain_*` -> `spain`, `morocco_*` -> `morocco`)
- `DIVISIONS`: recipe tree
- `REPRESENTATION` or `ESCANHOS`: representative allocation config
- `MUNICIPAL_LEVEL`: municipal level for the derived root
- `POPULATION_INDEXES`, `POPULATION_INDEX`, `POPULATION_MULTIPLIERS` or
  `INDICES_POBLACION`: population multipliers by level/name
- `PROVINCE_STATUSES`, `PROVINCE_STATUS` or `ESTADOS_PROVINCIA`: province
  statuses by level/name

Recipe node fields:

- `name`: required
- `code`: recommended; if omitted, code is derived from `--code-prefix + name`
- `entity_type`: saved on `NuevoAdminArea`
- `level`: optional only for container nodes without `spec`/`dat`
- `capitals` or `capital`: source capital labels
- `dat`: compact source selector
- `spec`: explicit source selector
- `childs`: child recipe list
- `forced_area_km2`: optional forced area
- `source_population_year`, `population_year`, `year`, `anio`, `ano`, or scalar
  `year_start`: year used to apply global source population indices while
  aggregating `pop_latest`; inherited by child nodes unless overridden
- `population_index`, `population_multiplier`, `indice_poblacion`: inline
  multiplier
- `province_status`, `estado_provincia`, `status`: inline status
- `depends_on`, `depende_de`, `dependency_of`: dependency target
- `prefer_city_merge_status`, `city_merge_status`, `merge_status`: source row
  preference for lookups
- `auto_set_most_populated`: defaults true

`dat` shape:

```python
{"dat": {2: ["Madrid", "Toledo"]}}
```

This is converted to `spec` using `SOURCE_COUNTRY`.

`spec` shape:

```python
{
    2: {"spain": ["Madrid", "Toledo"]},
    "restar": {
        3: {"spain": ["Some Municipality"]},
    },
}
```

Source labels can be:

- string or int label
- exact `AdminArea.id`
- `code`
- `name`
- `"Parent|Child"` to disambiguate by parent
- dict selector with keys like `label`, `name`, `id`, `code`, `parent`,
  `parent_label`, `parent_name`, `state`, `admin1`
- dict selector with `city_merge_status` / `prefer_city_merge_status` /
  `merge_status`
- dict capital selector with `names_by_language`, `names`, `translations`,
  `nombres`, or direct `es`/`spanish` keys

`create_nuevo_area_from_spec` expands selected source areas to municipal source
ids, applies `restar`, detects duplicate source data, aggregates area and
population, sets capitals and links `municipios_originales`.

The repository no longer ships `ciudades_del_mundo/source_population_indices.toml`
as a scraping-config bootstrap file. If a local historical population index file
is reintroduced for derived builds, `services/source_population_indices.py` can
load its top-level country tables. Keep scraping config seeds in
`ciudades_del_mundo/subdivisions/*.toml`, not in a shared population-index file.
Population-index format is grouped by original source country and source
label/id/code/name:

```toml
[spain]
"Madrid" = { 1995 = 0.5, 2000 = 0.8 }
"Castilla-La Mancha" = { 1995 = 0.3 }
```

Each configured year applies from that year until the next configured year. If
the build year is before the first configured year, the configured area has no
active multiplier yet. If a selected source row has no direct match, derived
population aggregation walks up its `AdminArea.parent` chain and uses the first
configured ancestor multiplier. If neither the row nor any ancestor matches,
the source row keeps multiplier `1`.

The current broad historical index data is generated from CityPopulation
administrative/cities pages, with root-country pre-census years extended from
Our World in Data `population.csv` where a country match exists. Treat these as
approximate scaling indices, not authoritative historical census replacement
data.

`ORIGINAL_MUNICIPAL_LEVEL` in `services/nuevo_admin_builder.py` defines default
municipal/source expansion levels per original country. If a country is missing
there, either add it carefully or provide `MUNICIPAL_LEVEL` where appropriate.
Central American source defaults currently include `guatemala=2`,
`honduras=2`, `nicaragua=2`, `elsalvador=3`, `costarica=3` and `belize=1`.
Panama uses level `3` because CityPopulation rows at that level are
corregimientos/townships.
USA uses level `3` for derived municipal/source expansion; level 2 rows are
counties and should not be treated as source cities in derived builders. Some
major USA level-3 city rows are parentless in CityPopulation, so the derived
builder also includes parentless USA cities when their URL state/county context matches the selected
source areas.
The Central America historical recipe uses Costa Rican level-2 cantons plus
level-3 partial district exceptions; do not repeat districts already covered by
selected full cantons.

The SQL config for `panama` defines configured city unifications for the main
Panamanian city districts visible in local `AdminArea` data: Panamá, San
Miguelito, Arraiján, La Chorrera, Colón, David, Santiago, Penonomé, Changuinola
and Chitré. The source townships are selected by numeric code because Panama has
repeated township names across provinces/districts.

`new_subdivisions/nuevo_imperio_romano.py` defines the `nuevo_imperio_romano`
derived country with `ROOT_NAME = "Nuevo Imperio Romano"`, two top-level
prefectures (`Hispania`, `Macaronesia`) and province-level children across
Spain, Portugal, France, Andorra and Gibraltar. Its Barcelona metropolitan
province uses the official AMB 36-municipality list. Its Madrid metropolitan
province uses Madrid plus the Comunidad de Madrid corona metropolitana; source
labels intentionally use CityPopulation names such as `Las Rozas de Madrid` and
`Paracuellos de Jarama`.

## Derived Build Pipeline

Command:

```powershell
py manage.py build_new_subdivisions --country-id <recipe_slug>
```

Implementation:

```text
ciudades_del_mundo/management/commands/build_new_subdivisions.py
ciudades_del_mundo/services/nuevo_admin_builder.py
ciudades_del_mundo/services/nuevo_admin_representatives.py
```

Build flow:

1. Load recipe module from `historical_divisions` or `new_subdivisions`.
2. Validate duplicate hierarchical full codes.
3. Create or update the root `NuevoAdminArea`.
4. Delete all existing non-root rows for that derived `country_code`.
5. Clear representatives for the derived country.
6. Build recipe nodes recursively.
7. Nodes with `spec`/`dat` aggregate from original `AdminArea`; if a source
   population year is active and a local source-population index file exists,
   matching entries multiply source `pop_latest` before the new node's
   population is saved.
8. Container nodes without `spec`/`dat` are created first, then area/population
   are summed from children.
9. Apply inline metadata: population index, province status and pending
   dependencies.
10. Resolve dependency labels to same-level `NuevoAdminArea` rows.
11. Apply module-level `POPULATION_INDEXES`.
12. Apply module-level `PROVINCE_STATUSES`.
13. Validate dependencies.
14. Assign representatives if the recipe has `REPRESENTATION`/`ESCANHOS`.
15. Refresh `most_populate_city` bottom-up.

Important side effects:

- This command rewrites `NuevoAdminArea` rows for the requested derived country.
- It does not scrape fresh `AdminArea`; source data must already exist.
- Representative assignment fails if configured level has no rows.
- Dependencies must point to same-level areas and cannot point to territories.
- Circular dependencies are rejected.

## Partial configured-city selections

`services/composite_city_sources.py` resolves a `SOURCE` district to its
`UNIFIED` ancestor in the same country through SQL parent links, not names.
Explicitly selected districts remain partial selections: choosing one district
does not select the entire city. Recipes without explicit source-district
selections retain their ordinary municipal expansion rules.

Before aggregation, affected cities are partitioned into disjoint source units.
Repeated districts count once; city plus district does not overlap; city minus
district retains only the remaining districts. A subtraction outside the
selection does not introduce a negative contribution, and removing all selected
districts leaves an empty selection. Finer subtractions split source units into
their source children when available. Population and area use the remaining
units, which are stored in `municipios_originales`.

Generated children group selected districts under one derived city using the
owning city's name/type, while retaining only the chosen districts' metrics and
source links. This applies to both source-unit and selected-entity child modes.
Group import/export and materialization preserve explicit IDs and subtractions.
The group editor retains each saved add/subtract operation even before a section
loads its children. Lazy loading must preserve those operations; cancelling the
section modal restores both the selected IDs and their operations. SQL group
TOML keeps `selected_ids` and corresponding `selected_operations` in section
data, so an unopened section must not serialize its saved subtractions as adds.
See `tests/test_composite_city_selections.py` for arithmetic, HTTP selectors,
group serialization and repeat-build coverage; the inline editor is covered by
`tests/frontend/group_selection.test.cjs`. Its command and the limits of clean-copy
verification are documented in [operations](operations.md#offline-verification-scope).

Before building a SQL `DerivedCountryConfig`,
`services/derived_country_group_sync.py` synchronizes moved-group subtractions
across sibling configs and the builder reloads the updated content. Generated
blocks carry `auto_generated = true` and `auto_source_config_slug`. Removing a
move removes its generated subtraction while preserving manual subtractions;
including the same source group in multiple sibling configs is rejected. This
preparation can update sibling SQL configuration content as well as the tree
being built.

## Capitals And Most-Populated Cities

For scraped `AdminArea`:

- Command: `py manage.py assign_admin_capitals`
- Capital mapping is hardcoded in
  `ciudades_del_mundo/management/commands/assign_admin_capitals.py`.
- Service:
  `ciudades_del_mundo/services/adminarea_capitals.py`.
- During scraping, most-populated city is assigned in step 2 from the highest
  available descendant level and saved with the scraped rows. Capital assignment
  can still validate capital descendants.

For derived `NuevoAdminArea`:

- Capitals are resolved while building recipes.
- Capital lookup can use legal subdivision levels from source SQL config
  `LEGAL_SUBDIVISION`.
- `refresh_nuevo_admin_most_populated(country_id)` recalculates bottom-up from
  `municipios_originales` and child results.
- `SOURCE` rows are ignored for most-populated selection.

## Representative Allocation

Shared representation config:

```text
ciudades_del_mundo/domain/scraping_config.py
```

AdminArea persistence assignment:

```text
ciudades_del_mundo/infrastructure/django/admin_area_repository.py
```

NuevoAdminArea assignment:

```text
ciudades_del_mundo/services/nuevo_admin_representatives.py
```

Rules:

- only D'Hondt is implemented
- `total` and `habitant` are mutually exclusive
- minimum and maximum exceptions can match id, code, name or normalized name
- derived allocation applies effective inherited `population_index`
- `territory` rows and descendants are excluded from representation
- `dependency` rows add their weighted population to their dependency owner and
  receive 0 direct seats
- a dependency must have `depends_on` and target a same-level non-territory row
