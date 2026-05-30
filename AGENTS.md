# AI Working Context

This file is the first stop for AI agents working in this repository. Read it
before scanning the whole codebase. Use it as a map, then inspect only the files
that are relevant to the current task.

Keep this file updated when architecture, commands, config formats or recurring
workflow rules change.

When the user requests any code or configuration change, also update this file
in the same task with any durable new information: new files, commands,
workflows, config shapes, conventions, side effects or risk notes. Treat
`AGENTS.md` as the repo memory for future AI work, not as a changelog of every
small line edit.

When creating a new feature, document it in the same task. At minimum, update
the relevant repo documentation or in-code command/help text so future users and
agents know what the feature does, how to use it, which files own it and any
important side effects. Also update `AGENTS.md` with durable context when the
feature changes project workflow or architecture.

## Project Summary

`BEOGRAD-CITIES-POPULATION` is a local Django project for population and
administrative geography data. It:

- scrapes administrative divisions and population data from `citypopulation.de`
- stores scraped geography in `AdminArea`
- builds derived, fictional, historical or political hierarchies in
  `NuevoAdminArea`
- assigns capitals, most-populated cities and representatives
- exports derived hierarchies to CSV and Excel

The active scraping configuration is TOML-based. Do not add old-style Python
country modules for ordinary CityPopulation scraping unless the user explicitly
asks.

## Architectural Direction

The project follows a hexagonal and modular architecture. Keep domain rules,
use cases, ports and infrastructure adapters separated so Django, HTTP scraping,
persistence and export details do not leak into core business logic.

Long-term flexibility is a priority. When adding behavior, first look for an
existing extension point such as TOML config, recipe data, a service, a use case
or a repository/port adapter before adding parallel code paths. Prefer small,
reusable modules and configuration-driven changes that reduce the amount of
future code needed to support new countries, historical recipes, exports or
allocation rules.

## Project Purpose And Scope

This project exists to maintain a local data system around countries, cities,
administrative divisions, historical/fictional political subdivisions,
population, capitals, most-populated cities, representative allocation and
exports derived from that data.

In practical terms, project-related work includes:

- scraping or validating CityPopulation administrative data
- editing `subdivisions/*.toml` country/territory configs
- editing historical or fictional subdivision recipes
- changing Django models, migrations, repositories, services, commands, tests,
  templates or static files in this repo
- building or exporting `AdminArea` / `NuevoAdminArea` data
- fixing commands, parsers, representative allocation, capital assignment,
  Excel/CSV output or the small web UI

If the user requests something unrelated to this project, ignore the request for
repo work: do not edit files, do not run commands and do not spend time solving
the unrelated task here. A brief response saying the request is outside this
project's scope is enough.

## Immediate Agent Rules

- Read this file first, then `README.md` only if more context is needed.
- Check `git status --short` before editing. The tree may contain user changes.
- Keep changes scoped. Do not refactor unrelated code while doing data/config
  work.
- For every code/config change, review whether `AGENTS.md` needs an update and
  add the relevant new context before finishing.
- For every new feature, add or update user/developer documentation in the
  appropriate place before finishing.
- Avoid touching generated or local artifacts unless the task requires it:
  `__pycache__/`, `.idea/`, `db.sqlite3`, `excels/`.
- Do not run network scraping unless the user asks or the task clearly requires
  fresh CityPopulation data.
- Prefer `rg` and `rg --files` for search if available. In this Windows shell it
  may be missing; use `Get-ChildItem` and `Select-String` as fallback.
- Use PowerShell command examples in this repo.
- Use `py manage.py ...` from the repository root.

## Repository Shape

Top-level:

- `manage.py`: Django command entry point.
- `README.md`: human project overview and common commands.
- `AGENTS.md`: this agent context.
- `db.sqlite3`: local SQLite database. Treat as data, not source.
- `excels/`: generated CSV/XLSX exports.
- `ciudades_del_mundo/`: Django project and app package.

Django package:

- `ciudades_del_mundo/settings.py`: local Django settings. SQLite DB is
  `BASE_DIR / "db.sqlite3"`.
- `ciudades_del_mundo/urls.py`: root URL routing.
- `ciudades_del_mundo/models.py`: Django models.
- `ciudades_del_mundo/admin.py`: Django admin registrations.
- `ciudades_del_mundo/web/`: dashboard/listing views and URLs.
- `ciudades_del_mundo/templates/`: Django templates.
- `ciudades_del_mundo/static/`: app CSS.

Layered code:

- `domain/`: pure dataclasses, enums and domain logic.
- `application/`: use cases and orchestration.
- `ports/`: protocols/interfaces.
- `infrastructure/`: Django repositories, scrapers, HTTP client and XLSX writer.
- `services/`: derived hierarchy, capitals and representative allocation logic.
- `management/commands/`: CLI operations.
- `tests/`: unit tests.

Data/config packages:

- `subdivisions/*.toml`: active scraping configs.
- `source_population_indices.toml`: global source `AdminArea` population
  multipliers used by derived builds when a population year is provided.
- `historical_divisions/*.py`: reusable historical recipe fragments.
- `new_subdivisions/*.py`: derived hierarchy recipes.
- `format_html/*.txt`: sample or reference HTML formats.

## Main Models

`AdminArea` in `ciudades_del_mundo/models.py`:

- scraped directly from CityPopulation
- string primary key: `<country_code>_<code>`
- unique pair: `country_code + code`
- levels: `0..5`, where `0` is country/root
- hierarchy: `parent` self-FK
- important fields: `entity_type`, `area_km2`, `density`, `pop_latest`,
  `pop_latest_date`, `last_census_year`, `url`, `representatives`
- capital relation: `capitals` ManyToMany to `AdminArea`
- most-populated relation: `most_populate_city` FK to `AdminArea`
- merge status: `city_merge_status`
- compatibility property: `escanhos` returns `representatives`

`NuevoAdminArea` in `ciudades_del_mundo/models.py`:

- derived/custom hierarchy built from original `AdminArea` rows
- root id is the requested `country_id`
- non-root ids use `<country_code>-<hierarchical_code>`
- `country_code` is the derived country/empire id, not necessarily an original
  CityPopulation country
- unique pair: `country_code + code`
- hierarchy: `parent` self-FK
- `municipios_originales` M2M links back to source `AdminArea` rows
- `capitals` M2M links to original `AdminArea` rows
- `most_populate_city` FK links to original `AdminArea`
- `capital_names_by_language` stores display-name overrides for capitals
- `municipal_level` can be inherited from parent and controls source expansion
- representative-specific fields:
  `population_index`, `province_status`, `depends_on`, `representatives`
- `ProvinceStatus` values: `normal`, `dependency`, `territory`
- compatibility property: `escanhos` returns direct or aggregated
  representatives

## Important ID And Level Rules

- `ScrapedAdminArea.id` is computed as `f"{country_code}_{code}"`.
- In the domain layer, `parent_code` is a source code, not a full Django id.
- `DjangoAdminAreaRepository` maps `parent_code` to
  `<country_code>_<parent_code>` only when that code is known.
- `NuevoAdminArea.code` is hierarchical for children. The build command joins
  child codes with `-`.
- First level under the derived root is not prefixed by the root code.
- Children below level 1 are prefixed with parent code unless already prefixed.
- Duplicate `NuevoAdminArea.code` values within one `country_code` are rejected.
- Legal levels are usually domain-specific; do not assume level 3 always means
  municipality. Use TOML `LEGAL_SUBDIVISION` and recipe `MUNICIPAL_LEVEL` or
  `ORIGINAL_MUNICIPAL_LEVEL`.

## City Merge Status

Constants in `domain/admin_area.py` and model choices:

- `0` / `NONE`: normal row
- `1` / `SOURCE`: source row used to build a unified city
- `2` / `UNIFIED`: synthetic unified city row

Most-populated calculations generally prefer/allow `NONE` and `UNIFIED`, and
ignore `SOURCE` rows. Builder lookups can accept status preferences using
aliases like `none`, `source`, `unified`, `fuente`, `unificada`.

## Scraping Configs

Active configs live in:

```text
ciudades_del_mundo/subdivisions/<slug>.toml
```

The loader is:

```text
ciudades_del_mundo/infrastructure/scraping/python_config_repository.py
```

Typed config objects and parsers are in:

```text
ciudades_del_mundo/domain/scraping_config.py
```

Important TOML fields:

- `name`: optional display name.
- `country_code`: optional override; defaults to slug.
- `base_url`: optional; defaults to `https://www.citypopulation.de/en/`.
- `reset_before_import`: if true, deletes current rows for that country before
  saving.
- `LEGAL_SUBDIVISION`: level used for most-populated branch calculations.
- `[representation]`: seat allocation rules.
- `[[pages]]`: page groups to scrape.
- `[[cities]]`: configured city aggregation/collapse rules.
- `[[entity_merges]]` or `[[merge_entities]]`: same-level sibling merge rules.

`[[pages]]` fields:

- `source`: one of `admin`, `table`, `double`, `cities`, `infosection`.
- `path`: string or array; prefer array even for one path.
- `lowest_level`: first level parsed on that page; legacy alias `level`.
- `area_km2`: optional area override for every row from that page; legacy
  aliases `size`, `custom_size`.
- `area_overrides`: optional map by scraped `id`, `code` or `name`; legacy
  aliases `size_overrides`, `custom_sizes`.

Path normalization:

- relative `path` values are prefixed with the TOML slug if not already
  prefixed
- absolute `http://` or `https://` paths are preserved
- final URLs always end with `/`

`[representation]` fields:

- `level`: level where representatives are assigned
- `system`: currently `dhondt`
- `total`: fixed total seats, mutually exclusive with `habitant`
- `habitant`: seats are calculated as ceil(population / habitant)
- `min`, `max`: default minimum/maximum seats
- `min_exceptions`, `max_exceptions`: keyed by id, code, name or normalized name

`[[cities]]` fields:

- `city`: new city name
- `id`: new city code
- `level`: new city level
- `type`: new `entity_type`
- `district_types`: source entity types used as city inputs
- `from`: map `{level: [parent labels]}`
- `communes`: optional list of child labels to aggregate
- `keep_communes`: if false, source communes are marked `SOURCE` and not kept as
  visible children
- `child_id`, `child_level`, `child_type`: optional synthetic child row

`[[entity_merges]]` fields:

- `entity_types` or `district_types`: source types to merge
- `type` or `entity_type`: new entity type
- `strip_numeric_suffix`: defaults true; merges names like `Metro 1` and
  `Metro 2` into `Metro`
- `keep_sources`: parsed but current merge behavior marks source rows as
  `SOURCE`

## Scraping Pipeline

Main use case:

```text
ciudades_del_mundo/application/scrape_admin_areas.py
```

`ScrapeAdminAreas.run(config)` flow:

1. For each configured page, select scraper by `page.html_format`.
2. Build URL with `build_page_url`.
3. Scrape HTML into `ScrapedAdminArea` rows.
4. Apply page-level `area_km2` and `area_overrides`.
5. Deduplicate by `(country_code, code)`, keeping first appearance.
6. Normalize synthetic country parent codes when a page root uses a different
   code than `country_code`.
7. Infer missing parent codes from URL path slugs where unique.
8. Apply `entity_merges`.
9. Apply configured `cities`.
10. In a transaction, optionally reset country rows, save all incoming rows,
    delete missing rows, refresh most-populated assignments, and assign
    representatives when configured.

Scraper implementations:

- `admin`: `CityPopulationAdminScraper`
  - reads `table#tl`
  - uses nested `tbody.adminN` sections
  - can parse root from `infosection mainsection` or table footer
- `double`: `CityPopulationDoubleScraper`
  - reads stacked `table#tl` and `table#ts`
  - uses `radm` cells or parent names for table#ts parent assignment
  - detects hectare units and divides area by 100
- `table`: `CityPopulationStructuredTableScraper`
  - parses a root via admin/cpage/tfoot logic
  - delegates hierarchical table parsing to the double scraper
- `cities`: `CityPopulationCitiesScraper`
  - root via admin or infosection
  - delegates hierarchical table parsing to the double scraper
- `infosection`: `CityPopulationInfoSectionScraper`
  - returns only the root/infosection row

Shared scraping helpers:

- `CityPopulationClient.get()` uses `requests.Session`, 30s timeout, 2 attempts.
- 404 raises `ScrapingPageNotFoundError`.
- 403, Cloudflare and human-verification pages raise HTTP errors.
- visible population columns are detected from `th.rpop` and
  `display: table-cell`.
- parsing prefers the latest visible population column.

## Persistence Details

`DjangoAdminAreaRepository`:

- saves rows level by level with `bulk_create(update_conflicts=True)`
- SQLite-safe batch size is 500
- updates fields including hierarchy, area, density, population, URL and merge
  status
- `delete_missing` removes existing country rows not present in the latest
  scrape
- representative allocation is D'Hondt only

Be careful with `reset_before_import`, `delete_missing` and scraping runs. These
can modify or delete many `AdminArea` rows in `db.sqlite3`.

## Derived Hierarchy Recipes

Derived recipes live in:

```text
ciudades_del_mundo/new_subdivisions/*.py
ciudades_del_mundo/historical_divisions/*.py
```

The build command imports both packages and loads modules that define
`DIVISIONS`. Historical modules can also be directly buildable if they expose
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

Global source population indices live in
`ciudades_del_mundo/source_population_indices.toml` and are loaded by
`services/source_population_indices.py`. Format is grouped by original source
country and source label/id/code/name:

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
USA uses level `3` for derived municipal/source expansion and
`subdivisions/usa.toml` sets `LEGAL_SUBDIVISION = 3`; level 2 rows are counties
and should not be treated as cities for most-populated calculations. Some major
USA level-3 city rows are parentless in CityPopulation, so the derived builder
also includes parentless USA cities when their URL state/county context matches
the selected source areas.
The Central America historical recipe uses Costa Rican level-2 cantons plus
level-3 partial district exceptions; do not repeat districts already covered by
selected full cantons.

`subdivisions/panama.toml` defines configured city unifications for the main
Panamanian city districts visible in local `AdminArea` data: Panamá, San
Miguelito, Arraiján, La Chorrera, Colón, David, Santiago, Penonomé,
Changuinola and Chitré. The source townships are selected by numeric code
because Panama has repeated township names across provinces/districts.

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
   population year is active, matching entries from `source_population_indices.toml`
   multiply source `pop_latest` before the new node's population is saved.
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

## Capitals And Most-Populated Cities

For scraped `AdminArea`:

- Command: `py manage.py assign_admin_capitals`
- Capital mapping is hardcoded in
  `ciudades_del_mundo/management/commands/assign_admin_capitals.py`.
- Service:
  `ciudades_del_mundo/services/adminarea_capitals.py`.
- It validates capital descendants and recalculates most-populated city at the
  lowest available level.

For derived `NuevoAdminArea`:

- Capitals are resolved while building recipes.
- Capital lookup can use legal subdivision levels from source TOML
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

## Export Pipeline

CSV command:

```powershell
py manage.py export_nuevoadmin_csv --country-id <derived_id>
```

Excel command:

```powershell
py manage.py export_nuevoadmin_excel --country-id <derived_id>
```

Important files:

- `ciudades_del_mundo/management/commands/export_nuevoadmin_csv.py`
- `ciudades_del_mundo/management/commands/export_nuevoadmin_excel.py`
- `ciudades_del_mundo/application/export_nuevo_admin_areas.py`
- `ciudades_del_mundo/infrastructure/django/nuevo_admin_area_export_repository.py`
- `ciudades_del_mundo/infrastructure/excel/simple_xlsx_writer.py`
- `ciudades_del_mundo/domain/nuevo_admin_export.py`

Excel details:

- `export_nuevoadmin_excel` refreshes derived most-populated city before export.
- Default output directory is `excels/`.
- `SimpleXlsxWriter` is a custom no-third-party XLSX writer using `zipfile`.
- Workbook rows are one path from root to leaf.
- Top-level and child rows are sorted alphabetically by name, not code.
- Each level block includes `Lx_ranking_poblacion_pais`, ranking areas by
  population against every area in the derived country at that same level.
- The workbook writes only the main path table; it does not append secondary
  child-summary tables to the right of the main columns.
- The main sheet name is `NuevoAdminArea`.
- Main table name starts as `TablaPrincipal`.

## Management Commands

Validation:

```powershell
py manage.py validate_subdivision_configs
py manage.py validate_subdivision_configs spain morocco
```

List scrape URLs without network fetch:

```powershell
py manage.py scrape_subdivisions --list-pages spain
```

Run scraping, network-dependent and DB-mutating:

```powershell
py manage.py scrape_subdivisions spain
py manage.py scrape_subdivisions spain morocco portugal
```

Assign capitals on scraped rows, DB-mutating:

```powershell
py manage.py assign_admin_capitals
```

Build derived hierarchy, DB-mutating:

```powershell
py manage.py build_new_subdivisions --country-id spanish_federal_republic
py manage.py build_new_subdivisions --country-id spanish_federal_republic --population-year 1999
py manage.py build_new_subdivisions --country-id nuevo_imperio_romano
```

Export derived hierarchy, file-generating:

```powershell
py manage.py export_nuevoadmin_csv --country-id spanish_federal_republic
py manage.py export_nuevoadmin_excel --country-id spanish_federal_republic
```

Run web app:

```powershell
py manage.py runserver
```

Run tests:

```powershell
py manage.py test ciudades_del_mundo.tests
```

## Testing Map

Existing tests:

- `test_scraping_config.py`: TOML parsing, page expansion, real config loading.
- `test_scraping_admin.py`: admin HTML parser root/population/parent stack.
- `test_application_pipeline.py`: scraping use case, dedupe, area overrides,
  persistence calls.
- `test_configured_cities.py`: city aggregation and source row shifting.
- `test_entity_merges.py`: same-level merge behavior.
- `test_hierarchy_and_most_populated.py`: parent stack and most-populated rules.
- `test_nuevo_admin_builder.py`: derived builder edge cases such as USA
  parentless city URL matching and numeric-code fallback.
- `test_representation.py`: D'Hondt and legacy representation config aliases.
- `test_nuevo_admin_excel_export.py`: export row ordering.

Testing guidance:

- For TOML/config changes, run `py manage.py validate_subdivision_configs <slug>`
  and a focused test if parsing logic changed.
- For scraper/parser logic, add or update HTML-string unit tests before running
  live network scraping.
- For derived recipes, run the specific
  `py manage.py build_new_subdivisions --country-id <id>` only when DB mutation
  is acceptable.
- For export logic, prefer unit tests around
  `build_nuevo_admin_workbook` before generating files.
- For shared behavior, run `py manage.py test ciudades_del_mundo.tests`.

## Common Task Routing

If the user asks to add or fix a country scraping config:

- edit `ciudades_del_mundo/subdivisions/<slug>.toml`
- inspect `domain/scraping_config.py` only if schema behavior changes
- validate with `validate_subdivision_configs <slug>`
- use `scrape_subdivisions --list-pages <slug>` to verify URL expansion
- do not run actual scraping unless asked

If the user asks to fix a scraper:

- inspect the matching file under `infrastructure/scraping/`
- add/update a unit test with representative HTML
- keep parsing logic deterministic and avoid live network tests

If the user asks to build or change an empire/country derived hierarchy:

- edit a module under `new_subdivisions/` or `historical_divisions/`
- check source country and municipal level
- watch for duplicate hierarchical codes
- prefer explicit `code` values
- use `dat` for simple source-country selections and `spec` for multi-country
  or subtraction logic

If the user asks about capitals:

- scraped `AdminArea` capital map is in `assign_admin_capitals.py`
- derived recipe capitals are usually in `DIVISIONS` entries
- display-name translations for derived capitals can be supplied as dict labels

If the user asks about seats/escanhos:

- scraping config seats: TOML `[representation]`
- derived seats: recipe `REPRESENTATION` or `ESCANHOS`
- logic lives in `services/nuevo_admin_representatives.py`

If the user asks about Excel/CSV output:

- Excel builder is in `application/export_nuevo_admin_areas.py`
- Excel writer is custom and XML-based in `infrastructure/excel/`
- generated files go to `excels/` by default

If the user asks about web UI:

- views: `ciudades_del_mundo/web/views.py`
- routes: `ciudades_del_mundo/web/urls.py`
- background task registry: `ciudades_del_mundo/web/tasks.py`
- templates: `ciudades_del_mundo/templates/ciudades_del_mundo/`
- CSS: `ciudades_del_mundo/static/ciudades_del_mundo/app.css`
- main sections:
  `/configs/` for TOML scraping config editing and scrape/validate/list-URL
  tasks, `/recipes/` for derived recipe creation/editing/build/export tasks,
  `/derived/` for comparative `NuevoAdminArea` browsing, `/stats/` for
  statistical charts, `/delete/` for confirmed data deletion and `/tasks/` for
  in-memory task output/history
- web-launched tasks run `manage.py` subcommands in local subprocesses. A new
  task with the same key cancels/replaces the active one. Saving a TOML config
  or editable recipe from the UI also cancels/replaces the matching active
  scrape/build task if there is one.

## Local Development Notes

- Python 3.13+ or another version with `tomllib` is expected.
- README mentions dependencies: Django, requests, BeautifulSoup, lxml, openpyxl.
- There may be no `requirements.txt`; do not invent dependency management unless
  asked.
- Django version in generated settings comment is 5.2.1.
- Language/timezone settings are default-ish: `LANGUAGE_CODE = "en-us"`,
  `TIME_ZONE = "UTC"`.
- The local project may contain files with Spanish text. Do not "fix" encoding
  or mojibake-looking terminal output unless the task is specifically about
  encoding.

## Known Risk Areas

- Running scraping can delete DB rows not found in the new scrape because of
  `delete_missing`.
- Running `build_new_subdivisions` deletes and rebuilds all non-root derived
  rows for the requested country.
- Generated Excel/CSV files can clutter `excels/`; avoid creating them unless
  the task requires verification.
- Web task history is process-local and disappears when the development server
  restarts. The task side effects in `db.sqlite3`, `subdivisions/*.toml`,
  `new_subdivisions/*.py` or `excels/` remain.
- The web delete page performs confirmed bulk deletes for one source
  `AdminArea.country_code` or one derived `NuevoAdminArea.country_code`.
- CityPopulation layouts can vary by page; prefer small parser tests with saved
  HTML snippets over broad parser rewrites.
- `area_overrides` and configured city merges can affect downstream density,
  most-populated city and representative results.
- `SOURCE` vs `UNIFIED` rows matter; changing merge status logic can alter
  hierarchy expansion and most-populated selection.
