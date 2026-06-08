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

The active scraping configuration is SQL-backed in `ScrapingConfig.content`
using TOML text as the editable content format. Runtime scraping must read SQL
only. Local `ciudades_del_mundo/subdivisions/*.toml` files are seed/export
artifacts used only by `sync_scraping_configs` to instantiate or refresh
`/configs/` rows. Once SQL rows exist, scraping, validation and web actions must
not depend on those files. Do not add old-style Python country modules for
ordinary CityPopulation scraping unless the user explicitly asks.

## Architectural Direction

The project follows a hexagonal and modular architecture. Keep domain rules,
use cases, ports and infrastructure adapters separated so Django, HTTP scraping,
persistence and export details do not leak into core business logic.

Long-term flexibility is a priority. When adding behavior, first look for an
existing extension point such as SQL scraping config content, recipe data, a
service, a use case or a repository/port adapter before adding parallel code
paths. Prefer small, reusable modules and configuration-driven changes that
reduce the amount of future code needed to support new countries, historical
recipes, exports or allocation rules.

## Project Purpose And Scope

This project exists to maintain a local data system around countries, cities,
administrative divisions, historical/fictional political subdivisions,
population, capitals, most-populated cities, representative allocation and
exports derived from that data.

In practical terms, project-related work includes:

- scraping or validating CityPopulation administrative data
- editing SQL `ScrapingConfig` country/territory configs or local `subdivisions/*.toml` bootstrap/export seeds
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
  work. Do not modify already working UI/UX, table sizes, card proportions,
  spacing, layout, routes or behavior unless the user explicitly asks for that
  concrete change.
- For every code/config change, review whether `AGENTS.md` needs an update and
  add the relevant new context before finishing.
- Always translate new user-facing text added to the project. Wrap template
  strings with `{% trans %}` / `{% blocktrans %}`, Python strings with
  `gettext`, and update gettext catalogs when the change is meant to ship.
- For every new feature, add or update user/developer documentation in the
  appropriate place before finishing.
- Avoid touching generated or local artifacts unless the task requires it:
  `__pycache__/`, `.idea/`, `db.sqlite3`, `db.sqlite3-*`, `excels/`.
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
- `db.sqlite3` and `db.sqlite3-*`: local SQLite database and sidecar files.
  Treat as data, not source.
- `excels/`: git-ignored generated CSV/XLSX exports; do not commit exports.
- `locale/`: Django gettext catalogs for the web UI (`django.po` source and
  compiled `django.mo` files).
- `media/`: git-ignored local media files served in development. Visual identity
  should not download Commons/Wikimedia files during normal scraping; persist
  `commons_filename`, `remote_url`, QID and translations in SQL, and let the UI
  render Wikimedia `Special:FilePath` URLs directly. Legacy local paths under
  `media/visual_assets/<kind>/...` are read only as compatibility fallbacks or
  explicit `--repair-local-only` maintenance output, never as the preferred
  display source.
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

Hexagonal dependency rules for the AI-readable core:

- `domain/` stays pure and does not import Django, models, services,
  application or infrastructure.
- `ports/` defines boundary protocols and domain-facing exceptions only. It may
  import `domain/`, but not Django models or concrete adapters.
- `application/` orchestrates use cases and may import `domain/`, `ports/` and
  sibling application modules, but not `models`, `services`, `web`,
  `management` or `infrastructure`.
- Composition roots such as management commands and web views wire concrete
  adapters to ports. For scraping, `ScrapeAdminAreas` receives an injected
  `HtmlFetcher` port for concurrent raw HTML prefetching;
  `CityPopulationHtmlFetcher`/`CityPopulationClient` remain infrastructure adapters.

Data/config packages:

- `subdivisions/*.toml`: local TOML seed/export files for SQL
  `ScrapingConfig` rows. `sync_scraping_configs` imports them into `/configs/`
  and can export SQL rows back to this directory. Runtime scraping reads SQL
  only after bootstrap; deleting this directory after syncing must not break
  validation, scraping or web actions.

Page-level scraping config hints:

- `include_tables = ["ts"]` on a `[[pages]]` entry tells compound scrapers to
  use other page tables only as lookup context and not persist them. This is
  useful for CityPopulation locality/urban-place pages where the first table
  repeats provinces or municipalities already imported elsewhere. The same
  hint also works for hierarchical admin bodies using keys such as
  `include_tables = ["admin2"]`, so `/tunisia/mun/admin/` can use governorates
  as parent context while persisting only municipalities.
- `table_levels = { ts = 4 }` or `table_levels = { admin1 = 1, admin2 = 3 }`
  overrides the semantic level for a table/body without changing the parser
  implementation. Use it when CityPopulation attaches rows to a higher-level
  page parent but the project hierarchy needs a gap, e.g. municipalities at L3
  under L1 governorates, urban centers at L3 under L2 municipalities, or
  sub-municipalities at L5 under L4 municipalities.
- `root_code`, `root_name`, `root_level`, `root_parent_code` and
  `root_entity_type` recode or create a page root from TOML. They are intended
  for rootless admin pages and CityPopulation pages whose internal root code is
  not the project country/territory code.
- `status_levels = { StatusLabel = 2 }` lets a compound table assign levels
  by the row status label when one CityPopulation table mixes administrative
  ranks. Use this before adding country-specific parser code; Bosnia uses it
  for `AReg`, `ADist` and `Cant` rows on `/bosnia/cities/`.
- `[[synthetic_entities]]`, `[[parent_overrides]]` and
  `[[root_metric_sources]]` are runtime config extensions for rare cases such
  as Belgium/Flanders/Wallonia or France Metropolitan/Overseas grouping. Keep
  them data-only; do not add country-specific branches to scraper code.
- Synthetic page-root rewrites must ignore generic URL endings such as
  `/admin/` and use the preceding real page slug. For example,
  `/poland/dolnoslaskie/admin/` belongs to `dolnoslaskie`, not to a generic
  `admin` root.
- When a compound `table`/`double` page assigns `table#ts` parents through an
  `radm` column, blank `radm` cells must resolve to the same-name parent from
  `table#tl` when possible. If `radm` contains multiple parent labels separated
  by `/`, prefer the label matching the child name; if none match, try the first
  label first. Rows resolved through blank or slash-separated parent labels get
  `AdminArea.annotations = "Comparte población con otras divisiones"`. The
  `/countries/` country data table shows an `Anotaciones` column only when at
  least one visible row has an annotation.

- `subdivisions/*.toml`: git-ignored local seed/export artifacts. They are
  allowed for explicit `/configs/` bootstrap through `sync_scraping_configs`,
  but are not runtime scraper inputs or automatic fallbacks. Treat this
  directory as optional after SQL has been seeded.
- `historical_divisions/*.py`: git-ignored local reusable historical recipe
  fragments.
- `new_subdivisions/*.py`: git-ignored local derived hierarchy recipes.
- `format_html/*.txt`: sample or reference HTML formats.

## Main Models

`AdminArea` in `ciudades_del_mundo/models.py`:

- scraped directly from CityPopulation
- string primary key: `<country_code>_<code>`
- unique pair: `country_code + code`
- levels: `0..5`, where `0` is country/root
- hierarchy: `parent` self-FK
- important fields: `entity_type`, `raw_entity_type`, `area_km2`, `density`,
  `pop_latest`, `pop_latest_date`, `last_census_year`, `url`, `annotations`,
  `representatives`
- `raw_entity_type` preserves the scraped/incomplete label when AI or stored
  inference normalizes `entity_type` to a canonical internal singular label
- Scraping requires the DB schema from `0020_dynamic_ai_texts` or later; if
  `ciudades_del_mundo_adminarea.raw_entity_type` is missing, run
  `py manage.py migrate` before scraping instead of editing scraper data paths
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

`ScrapingConfig` in `ciudades_del_mundo/models.py`:

- SQL source of truth for CityPopulation scraping configuration
- primary key: `slug`
- `content` stores the editable TOML text parsed by the domain config parser
- cached metadata fields include `country_code`, `name`, `pages_count`,
  `cities_count`, `has_representation`, `is_valid` and `validation_error`
- `source_path` records the temporary seed/export TOML path when imported from
  the bridge, but runtime loading does not read that path

`DynamicTranslation` in `ciudades_del_mundo/models.py`:

- SQL-backed runtime translation for dynamic geography labels, not static UI
  strings
- subject shape: `subject_type`, `subject_key`, `country_code`, `field`,
  `language`
- common subjects: `country`, `admin_area`, `nuevo_admin_area`, `entity_type`
- common fields: `name`, `singular`, `plural`
- the UI uses only rows with `is_active=True` and `needs_review=False` before
  falling back to country-specific legacy dictionaries and gettext

`EntityTypeInference` in `ciudades_del_mundo/models.py`:

- persisted AI/manual rule for completing scraped `AdminArea.entity_type`
  values such as `Prov`
- keyed by `country_code`, `level`, `raw_entity_type` and optional
  `context_key`
- reviewed active rows are applied on future scrapes without calling AI
  (`raw_entity_type` keeps the original scraped value)

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
  municipality. Use SQL config `LEGAL_SUBDIVISION` and recipe
  `MUNICIPAL_LEVEL` or `ORIGINAL_MUNICIPAL_LEVEL`.

## City Merge Status

Constants in `domain/admin_area.py` and model choices:

- `0` / `NONE`: normal row
- `1` / `SOURCE`: source row used to build a unified city
- `2` / `UNIFIED`: synthetic unified city row

Most-populated calculations generally prefer/allow `NONE` and `UNIFIED`, and
ignore `SOURCE` rows. Builder lookups can accept status preferences using
aliases like `none`, `source`, `unified`, `fuente`, `unificada`.

## Scraping Configs

Active configs live in SQL:

```text
ciudades_del_mundo.models.ScrapingConfig
```

The editable `ScrapingConfig.content` field stores TOML text. Initial rows are
seeded from `ciudades_del_mundo/subdivisions/*.toml` by
`py manage.py sync_scraping_configs`. Runtime code must not read TOML files as a
fallback; these files are only explicit seed/export artifacts for `/configs/`.

The SQL-only runtime loader is:

```text
ciudades_del_mundo/infrastructure/scraping/python_config_repository.py
```

Typed config objects and parsers are in:

```text
ciudades_del_mundo/domain/scraping_config.py
```

Important `ScrapingConfig.content` TOML fields:

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
- `[[synthetic_entities]]`, `[[parent_overrides]]` and
  `[[root_metric_sources]]`: runtime post-processing extensions for exceptional
  CityPopulation layouts. They are parsed from SQL TOML by
  `ciudades_del_mundo/services/scraping_config_extensions.py`, attached only by
  Django command composition roots, and applied inside
  `application/scrape_admin_areas.py` after page scraping. Use them instead of
  hardcoded view/model fallbacks when a country needs synthetic containers,
  level shifts or additive country totals.

Runtime extension fields:

- `[[synthetic_entities]]`: creates deterministic rows such as France
  `METRO`/`OVERSEAS`. Supports `code`, `name`, `level`, `parent_code`,
  `entity_type`, explicit metrics, `copy_metrics_from` and
  `metric_source_codes`.
- `[[parent_overrides]]`: reparents or relevels already scraped rows by
  `codes`, `names` or `match_levels`, with optional `exclude_codes`.
- `[[root_metric_sources]]`: adds area/population metrics from extra scraped
  pages to the level-0 country row by matching `code`, `name` or URL `path`.

`[[pages]]` fields:

- `source`: one of `admin`, `auto`, `table`, `double`, `cities`,
  `infosection`.
- `path`: string or array; prefer array even for one path.
- `lowest_level`: first level parsed on that page; legacy alias `level`.
- `area_km2`: optional area override for every row from that page; legacy
  aliases `size`, `custom_size`.
- `area_overrides`: optional map by scraped `id`, `code` or `name`; legacy
  aliases `size_overrides`, `custom_sizes`.

Skip-level parent patterns are allowed when CityPopulation links child rows to
an ancestor instead of to the immediately previous legal level. Do not rewrite
those parents to force a perfectly adjacent hierarchy. Current examples:
Italy localities are imported one level below communes but keep the province as
parent, Portugal localities are imported one level below parishes but keep the
municipality as parent, Morocco urban places are imported one level below
communes but keep the province/prefecture as parent, Tunisia combines `/admin`
with `mun/admin` shifted one level deeper, and Gibraltar keeps `/cities` as the
level-0 seed while importing `/admin` one level lower for enumeration areas.

Path normalization:

- relative `path` values are prefixed with the SQL config slug if not already
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
3. Scrape HTML into `ScrapedAdminArea` rows. When `page_workers > 1`,
   download independent CityPopulation pages concurrently through the injected
   `HtmlFetcher` port, but emit completion events, seed assets and write SQL in
   the configured page order so extracted data and side effects stay equivalent
   to the sequential pipeline. Duplicate URL/format/level pages reuse the same
   fetched HTML and parsed entity tuple before page-specific area overrides are
   applied. In `--resume` mode, pages already
   recorded in `.web_scrape_resume/` are loaded as cached `ScrapedAdminArea`
   rows and only missing pages are fetched.
   The final SQL save still runs once with the full cached+fresh entity set, so
   `delete_missing` never sees a partial country scrape.
4. When enabled by the command, reuse that same downloaded HTML to seed
   CityPopulation visual assets for the root country and configured
   subdivision levels without fetching the page a second time. If the page
   exposes CityPopulation `data-wd` QIDs, asset seeding uses those exact links;
   otherwise it builds one Wikidata SPARQL country index from the root country
   QID (`?item wdt:P17 wd:<country_qid>`) and matches rows by normalized label
   and parent label before falling back to per-entity Wikidata search.
5. Apply page-level `area_km2` and `area_overrides`.
6. Deduplicate by `(country_code, code)`, keeping first appearance.
7. Normalize synthetic country parent codes when a page root uses a different
   code than `country_code`.
8. Infer missing parent codes from URL path slugs where unique.
9. Apply `entity_merges`.
10. Apply configured `cities`.
11. Apply stored reviewed `EntityTypeInference` rules, and if `--ai-enrich` is
    enabled, ask the configured AI provider to complete still-incomplete
    entity types. AI-generated rules are persisted and applied only when they
    are active and do not require review.
12. In a transaction, optionally reset country rows, save all incoming rows,
    delete missing rows, refresh most-populated assignments, and assign
    representatives when configured.

Scraper implementations:

- `auto`: `CityPopulationAutoScraper`
  - detects the CityPopulation HTML structure with
    `detect_citypopulation_page_profile`
  - delegates to the existing `admin`, `table`, `double` or `infosection`
    scraper without changing SQL persistence behavior
  - use for new configs when CityPopulation page shape is uncertain; keep
    explicit source types when they are already known and tested
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

- `CityPopulationHtmlFetcher.get()` is the concrete `HtmlFetcher` adapter for
  concurrent application prefetch; it creates one short-lived
  `CityPopulationClient` per URL instead of sharing a `requests.Session`
  between threads.
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
- resets and `delete_missing` remove `AdminArea` rows through
  `infrastructure/django/admin_area_deletion.py`, which clears dependent
  `AdminArea`/`NuevoAdminArea` FKs and M2M through rows in SQL batches before
  raw-deleting the source rows
- most-populated-city and representatives persistence use `bulk_update` instead
  of per-row saves
- representative allocation is D'Hondt only

Be careful with `reset_before_import`, `delete_missing` and scraping runs. These
can modify or delete many `AdminArea` rows in `db.sqlite3`.

## Derived Hierarchy Recipes

Derived recipes live in local git-ignored Python modules:

```text
ciudades_del_mundo/new_subdivisions/*.py
ciudades_del_mundo/historical_divisions/*.py
```

The build command imports both packages when present and loads modules that
define `DIVISIONS`. Historical modules can also be directly buildable if they
expose `DIVISIONS`. If one of these local packages is absent, the command skips
it; building a specific derived country still requires a local recipe module
that exposes `DIVISIONS`.

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
USA uses level `3` for derived municipal/source expansion and the SQL config
for `usa` sets `LEGAL_SUBDIVISION = 3`; level 2 rows are counties and should not
be treated as cities for most-populated calculations. Some major USA level-3
city rows are parentless in CityPopulation, so the derived builder also includes
parentless USA cities when their URL state/county context matches the selected
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

When launched from `/configs/`, failed validation rows should keep a link to
the owning `WebTask` detail/log, matching failed population task visibility.
`validate_subdivision_configs` persists the validation result in
`ScrapingConfig.is_valid`/`validation_error`, so a successful rerun clears a
previous `Fallo` before the row becomes `Validado`.

Clean scraped rows for one SQL config:

```powershell
py manage.py clear_config_data spain
```

The `/configs/` `Limpiar` action is shown when the real source country still has
`AdminArea` rows and no operation is active for that config (`Validando`,
`Populando`, `Limpiando` or generic running/queued states). It does not depend
on the visual workflow status being `Populado`; rows with existing data can be
cleared from `Por Validar`, `Validado`, `Populado` or `Fallo` as long as no
operation is active. Active config statuses must never expose `Limpiar`,
regardless of row count. The action is selected by config `slug`, but launches
`clear_config_data` with the real `CountryCode` stored in
`ScrapingConfig.country_code`. The command treats that value as the primary
identifier for deleting `AdminArea.country_code` rows and still accepts a config
`slug` as a compatibility fallback. After a successful clear, every SQL config
sharing that `country_code` has cached validation errors cleared and returns to
`Por Validar`. It does not delete `ScrapingConfig` or `NuevoAdminArea` rows, but
source links from derived areas to deleted `AdminArea` rows are removed/cleared.
The command uses `infrastructure/django/admin_area_deletion.py` to clear
dependent FKs/M2M through rows and raw-delete `AdminArea` rows in small
primary-key batches, which avoids SQLite `too many SQL variables` and is faster
than invoking Django's full delete collector for each batch. It logs one start
line and one `Limpiando: lote=... borradas=.../... relaciones_limpiadas=...`
line per batch; those lines are visible in console and web task logs. The web
clear task is wrapped in an outer transaction, so cancelling it before the
command finishes rolls back the pending delete and the config row returns to its
previous inferible state. Because this fast path bypasses model delete signals,
do not add cleanup behavior that relies on `AdminArea` delete signals for this
command or scraper reset/delete paths; add it to the explicit deletion helper
instead.

TOML/SQL config bootstrap bridge:

```powershell
py manage.py sync_scraping_configs
py manage.py sync_scraping_configs --force
py manage.py sync_scraping_configs --to-toml --output-dir .tmp-config-export
```

`ScrapingConfig` SQL rows are the operational source of truth.
`sync_scraping_configs` imports/exports per-country TOML seed files under
`ciudades_del_mundo/subdivisions/*.toml`. `PythonScrapingConfigRepository` reads
SQL only and does not fall back to TOML files. Keep runtime scraping changes in
SQL; sync from the seed only when intentionally refreshing `/configs/`.

List scrape URLs without network fetch from the CLI only; the web UI no longer exposes a URLs action:

```powershell
py manage.py scrape_subdivisions --list-pages spain
py manage.py scrape_subdivisions --seed-assets-from-pages spain
```

Run scraping, network-dependent and DB-mutating:

```powershell
py manage.py scrape_subdivisions spain
py manage.py scrape_subdivisions spain morocco portugal
py manage.py scrape_subdivisions_with_assets spain --page-workers=4
py manage.py scrape_subdivisions_with_assets spain --resume --page-workers=4
py manage.py scrape_subdivisions_with_assets spain --ai-enrich --page-workers=4
py manage.py scrape_subdivisions_with_assets spain --page-workers=1  # sequential compatibility mode
```

`--page-workers` defaults to `4` for management commands and can be overridden
with `CIUDADES_SCRAPE_PAGE_WORKERS`. Keep the default web action at
`--page-workers=4`; use `1` only when reproducing the old one-page-at-a-time
behavior. Do not parallelize database writes or asset persistence unless tests
prove row order, `delete_missing`, progress logs and visual asset results remain
unchanged. Duplicate URL/format/level pages may reuse parsed entities, but
page-specific area overrides still run per configured page.

`--resume` reuses local per-page checkpoints from `.web_scrape_resume/` for a
manual CLI recovery and only fetches missing configured pages. Normal
non-resume scraping clears any previous checkpoint at start; successful runs
clear the checkpoint at the end. The checkpoint is keyed by SQL
`ScrapingConfig.content_hash` so editing the config invalidates stale cached
pages. Keep all root `.web_*` local web artifacts git-ignored.

AI text enrichment, network/API-dependent and DB-mutating:

```powershell
$env:CIUDADES_AI_API_KEY = "..."
$env:CIUDADES_AI_MODEL = "..."
py manage.py enrich_ai_texts spain --infer-entity-types --translate-names --translate-entity-types --describe-assets
py manage.py enrich_ai_texts spain --translate-area-names --limit 50
```

`--ai-enrich` on `scrape_subdivisions` or `scrape_subdivisions_with_assets`
uses the same provider after scraping. The provider is configured with
`CIUDADES_AI_API_KEY`, `CIUDADES_AI_MODEL`, optional `CIUDADES_AI_BASE_URL` and
optional `CIUDADES_AI_TIMEOUT`. Do not enable AI enrichment by default in web
flows until the user explicitly asks; it can add cost, network latency and
review work. Dynamic geography text belongs in `DynamicTranslation`, not
gettext catalogs. Gettext remains for static UI strings. Flag/coat/seal AI text
belongs in `VisualAssetTranslation.description`/`blazon` with source/model
metadata. `AiTextEnrichmentService.describe_missing_visual_assets` imports the
public `upsert_visual_asset_translation` helper from
`services/visual_assets.py`; keep that wrapper available and use it from other
modules instead of importing the private `_upsert_translation` raw-SQL helper.

The `VisualAsset` and `VisualAssetTranslation` Django model classes must remain declared in `models.py` even though `services/visual_assets.py` performs most reads and writes with raw SQL for batch speed. Migration `0019_visual_assets` owns those tables; if the classes disappear, `makemigrations` will generate a destructive `DeleteModel` migration such as `0022_remove_visualassettranslation_asset_and_more.py`. Do not apply that migration; delete the generated file and restore the model declarations instead.

Seed visual identity metadata, network-dependent but URL-only by default:

```powershell
py manage.py ensure_visual_assets spain
py manage.py ensure_visual_assets --all
py manage.py ensure_visual_assets --country-subdivisions spain --levels 1,2
py manage.py ensure_visual_assets --admin-area spain_cat
py manage.py ensure_visual_assets --repair-local-only  # explicit legacy local-file repair only
py manage.py ensure_visual_assets spain --no-citypopulation-fetch
py manage.py ensure_visual_assets spain --download  # opt-in; avoid during normal scraping
```

`ensure_visual_assets` uses Wikidata/Commons first for country and subdivision
flag/coat/seal images. CityPopulation image scanning is only a fallback for
explicitly labelled or clearly named images; do not treat generic `*_2_3.svg`
language icons from CityPopulation as country flags.
Wikidata image claim parsing must skip `novalue` or malformed claims, prefer
usable preferred/normal claims before deprecated ones, and accept Commons
`Special:FilePath` URLs as filenames; do not regress it to first-claim-only
parsing.
Do not cache Commons SVG/PNG files during normal `Popular` or
`scrape_subdivisions_with_assets` runs. The default flow stores URLs and
metadata only; `--download`/`--download-assets` are explicit maintenance opt-ins
and should be avoided because they can trigger Wikimedia 429s. If legacy local
repair is used, validate file magic against the extension and never save
rasterized PNG bytes as `.svg`.
For root country assets, prefer an explicit TOML/SQL `wikidata_id` before any
network search; this avoids one request per page and prevents translated-name
misses such as `España`. If no configured QID exists, prefer Wikidata search by
country name over CityPopulation page `data-wd` because some page-level QIDs can
point to a non-country entity; subdivision `data-wd` values may still be used as
exact entity matches. SQL config content may declare `[visual_assets.flag]` and
`[visual_assets.coat]` with `wikidata_id`, `commons_filename` or `remote_url`
for explicit country seeding. It may also declare `[[visual_assets.admin_areas]]`
with `name`/`aliases`/`code`/`id`, optional `level`, `kind`, `wikidata_id`,
`commons_filename` or `remote_url` for curated subdivision/city assets whose
Wikidata items lack P41/P94/P158. Do not fix missing assets with view-level
hardcoded fallbacks: put the correct QID/Commons file in SQL config content
(or in the seed before syncing). Western Sahara is a known split-QID case: the
territory item carries the flag, while the coat is represented by a separate
coat-of-arms item. Morocco root assets should be explicitly anchored to its
Wikidata QID in TOML/SQL so translated CityPopulation labels do not make the
resolver miss the national flag/coat.
`scrape_subdivisions_with_assets` now seeds
CityPopulation assets from the already-downloaded scraping page, by default for
the country/root plus all scraped AdminArea subdivision levels. Missing kinds are
resolved server-side through Wikidata/Commons during that scrape-time asset
seeding, storing remote URLs and translations instead of downloading files.
Scrape-time seeding prefers CityPopulation
`data-wd` QIDs when present, then one Wikidata SPARQL country query using
`P17`, `P31`, `P131`, `P41`, `P94` and `P158`; matched QIDs are persisted in
`ciudades_del_mundo_visual_asset.wikidata_id` for the corresponding country or
`AdminArea.id`. Use `--skip-subdivision-assets` or `--subdivision-asset-levels`
to tune page asset seeding, and use `ensure_visual_assets` only for explicit
Wikidata/Commons metadata discovery. Local-file repair/download is legacy and
opt-in only.
For a full country visual-identity run, the target coverage is every
CityPopulation-backed entity stored as `AdminArea`: root country, autonomous
communities/regions, provinces, municipalities, localities and other scraped
levels present in the SQL config. Do not treat the default levels 1 and 2 as the
complete requirement when the user asks for all flags/coats; pass or implement
all required `--subdivision-asset-levels` and keep the resolver able to fall
back from page-associated assets to Wikidata/Commons per persisted entity.
If logs show `CityPopulation sin candidatos asociados` on locality pages, the
page scan alone is insufficient; continue with QID/index/per-entity resolution
instead of marking those entities permanently missing. HTTP 429 responses from
Wikidata/Commons mean the scraper is still doing too much network I/O; reduce
requests with page QIDs, one country SPARQL index and `wbgetentities` batches.
They are not proof that an asset has no candidate.
Spain has special locality depth rules in
`ciudades_del_mundo/subdivisions/spain.toml`: province locality pages use
`lowest_level = 3`, uniprovincial communities use `lowest_level = 2`, and
Ceuta/Melilla locality pages also use `lowest_level = 2` so their localities are
not pushed one level too deep. Do not merge those Spain blocks back into a single
locality block. CityPopulation pages such as `spain/ceuta/` and
`spain/melilla/` expose a synthetic root named like `Ceuta (Autonomous City)` and
only one `table#ts`; the scraper must treat that `ts` table as the first child
level and the post-processor must normalize parenthetical root suffixes before
attaching children to the real `CEU`/`MEL` root.
France uses TOML runtime extensions to keep overseas departments inside the
country instead of modelling them as separate countries: level 0 is France,
level 1 contains `METRO` and `OVERSEAS`, metropolitan regions are level 2 under
`METRO`, metropolitan departments are level 3, overseas departments are level 3
directly under `OVERSEAS`, and districts/communes continue as levels 4/5. The
root France population/area is completed through `[[root_metric_sources]]` from
the overseas `/cities/<territory>/` pages. If the SQL config or its
`subdivisions/france.toml` seed changes, sync the SQL config and update any
France `new_subdivisions` recipes so they account for the
new level-1 containers and `MUNICIPAL_LEVEL = 5`.
`/countries/` and identity panels should prefer `commons_filename`/`remote_url`
over local files. Country cards and the country detail panel in `/countries/`
must render only SQL-registered country visual assets; if a flag, coat or seal
has no usable SQL asset row, the browser shows the built-in placeholder icon and
must not hydrate it from Wikidata, local scratch TOML, CityPopulation, Commons search or
local media folders. Use
`get_visual_assets_for_entity(..., include_fallbacks=False)` for SQL-only
country asset payloads. The frontend for `/countries/` must consume only
`image_url`, `remote_url` or `local_url` values supplied by that payload; do not
construct ad-hoc Commons `Special:FilePath` URLs from `commons_filename` in that
page. Missing flag and shield placeholders should occupy the same visual box as
a real country image so card/detail layout does not jump; the square media slot
must define the size, not the presence of an `img` or placeholder. In country
cards, `.stats-country-flag` must use a fixed width and height from
`--country-flag-box-size`; do not use `min(100%, ...)` or text-dependent sizing.
Use CSS-drawn flag and shield placeholders colored from the active theme
variables, not emoji glyphs. The readable Ficha route is
`/identity/<kind>/entity/<entity_type>/<entity_key>/` (for example
`/identity/flag/entity/country/spain/`), and legacy
`/identity/<kind>/<visual_assets...>/` paths must resolve the stored asset when
possible. Clicking visual identity images in detail panels must open the
existing image preview popup first; do not bypass it by navigating directly to
the Ficha. Country-card flags in `/countries/` are passive media inside the
card and must not open the preview popup. The popup's `Ficha` action may open
the readable identity page in a new window/tab. The Ficha must show only one
description: the active Django language (`LANGUAGE_CODE`) selected from stored
translations. Do not render a "Descripciones multiidioma" table. The
Description field is reserved for curated heraldic/vexillological explanations
(blazon, symbolism, colors, adoption/context), not generic Wikidata entity
descriptions.

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
py manage.py runserver 127.0.0.1:8000
```

Run tests:

```powershell
py manage.py test ciudades_del_mundo.tests
```

Architecture boundary tests live in
`ciudades_del_mundo/tests/test_architecture_boundaries.py` and protect the
hexagonal import direction for `domain/`, `ports/` and `application/`. Update
the test only when the architecture rule changes intentionally.

Compile web translations without requiring GNU gettext:

```powershell
py manage.py compile_local_messages
py manage.py compile_local_messages es en fr de ru
```

`compile_local_messages` reads `.po` files with `utf-8-sig`, so it tolerates a
UTF-8 BOM left by Windows editors or PowerShell before writing `.mo` files.

## Testing Map

Existing tests:

- `test_scraping_config.py`: TOML content parsing, page expansion and real SQL
  config loading.
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
- `test_dashboard.py`: dashboard summary metrics, dynamic root-country
  population JSON and country display labels.
- `test_visual_assets.py`: CityPopulation page-HTML visual asset seeding and
  persisted asset metadata.
- `test_ai_text_enrichment.py`: SQL dynamic translations, entity type
  inference and AI-generated visual asset descriptions.

Testing guidance:

- For SQL config content changes, run
  `py manage.py validate_subdivision_configs <slug>` and a focused test if
  parsing logic changed. If you intentionally changed a `subdivisions/<slug>.toml`
  seed, run `py manage.py sync_scraping_configs <slug> --force`
  before validating SQL.
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

- edit the SQL `ScrapingConfig` row, preferably through `/configs/` or by
  refreshing its `subdivisions/<slug>.toml` seed with
  `py manage.py sync_scraping_configs <slug> --force`
- inspect `domain/scraping_config.py` only if schema behavior changes
- validate with `validate_subdivision_configs <slug>`
- use `scrape_subdivisions --list-pages <slug>` to verify URL expansion
- if the single seed TOML must stay in sync with SQL, export it explicitly with
  `py manage.py sync_scraping_configs <slug> --to-toml --force`
- do not run actual scraping unless asked

If the user asks to fix a scraper:

- inspect the matching file under `infrastructure/scraping/`
- add/update a unit test with representative HTML
- keep parsing logic deterministic and avoid live network tests

If the user asks to build or change an empire/country derived hierarchy:

- edit or create a local git-ignored module under `new_subdivisions/` or
  `historical_divisions/`
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

- scraping config seats: SQL config content `[representation]`
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
- database-busy middleware: `ciudades_del_mundo/web/middleware.py`
- templates: `ciudades_del_mundo/templates/ciudades_del_mundo/`
- CSS/JS: `ciudades_del_mundo/static/ciudades_del_mundo/app.css` and
  `ciudades_del_mundo/static/ciudades_del_mundo/app.js`
- translations: `locale/<language>/LC_MESSAGES/django.po` and compiled
  `django.mo`; supported web languages are `es`, `en`, `fr`, `de`, `ru`, `it`,
  `sr`, `sr-latn` and `ar`. The Serbian Latin gettext directory is
  `locale/sr_Latn/LC_MESSAGES/`.
- translation compiler: `ciudades_del_mundo/management/commands/compile_local_messages.py`
- dynamic geography labels (country names, `AdminArea` names and entity type
  singular/plural labels) should come from `DynamicTranslation` when present
  and reviewed; gettext catalogs are for static UI text only
- Spain-specific display-name translations for geography labels live in
  `ciudades_del_mundo/web/spain_translations.py`; it maps local/admin source
  names and CityPopulation entity types for Spain across the supported UI
  languages, for example `Lleida` -> `Lérida` in Spanish and `Province` ->
  language-specific labels such as `Provincia` or `Provinz`. Spain entity types
  are intentionally translated by the contextual helper, not by generic gettext
  entries, so fake area names such as `Province` are not translated accidentally.
- main sections:
  `/configs/` for SQL-backed scraping config editing and validate/populate
  tasks, `/recipes/` for derived recipe creation/editing/build/export tasks,
  `/derived/` for comparative `NuevoAdminArea` browsing, `/countries/` for the
  API-driven country browser, `/stats/` as its compatibility redirect,
  `/delete/` for confirmed data deletion and `/tasks/` for
  in-memory task output/history; `/map/<source>/<id>/` shows a map and visual
  identity lookup for one `AdminArea` or `NuevoAdminArea`;
  `/identity/<kind>/entity/<entity_type>/<entity_key>/` shows the readable
  internal detail page with Wikimedia image URL plus stored translations, while
  `/identity/<kind>/<filename>/` remains a legacy resolver for Commons filenames
  and local `visual_assets/...` paths
- large tables in `/areas/` and `/derived/<id>/` are loaded asynchronously from
  partial endpoints (`/areas/table/`, `/derived/<id>/table/`), with advanced
  filters and Select2-enhanced selects. Keep non-JS/native-select fallback
  working when editing these pages.
- Dashboard/statistical country bars and country selectors display names from
  root database rows (`AdminArea.level=0` and root `NuevoAdminArea`) while
  keeping `country_code` as the submitted/filter value. Display labels are
  passed through Django `gettext`, so country/subdivision names that need
  localization (for example `Brazil` -> `Brasil` in Spanish) must be present in
  the `locale/*/LC_MESSAGES/django.po` catalogs and compiled.
- Dashboard/statistical data loads dynamically with visible loading spinners.
  Current web data should be consumed through the local API endpoints:
  `/api/countries/`, `/api/countries/<country_code>/` and `/api/derived/`.
  The older `/dashboard/population/`, `/dashboard/derived/` and
  `/dashboard/country/<country_code>/` JSON endpoints remain for compatibility.
  `/api/countries/` returns root-country population and area donut data and uses
  only `AdminArea.level=0` rows, not summed child subdivisions when a single
  root exists; if CityPopulation provides multiple `level=0` rows for one
  `country_code`, the dashboard collapses them into one synthetic country entry
  so the donut never lists subdivisions as countries. `/api/derived/` returns
  the derived-country dashboard bar data. `/api/countries/<country_code>/`
  returns the clicked-country dashboard
  detail: general country data, one-level subdivision table rows, first-order
  population/area comparison rows and first-order share-card data. Country table
  rows include `detail_url`, `level`, parent id/level and `parent_detail_url`;
  when a row's parent is more than one level above it, the frontend opens the
  parent detail panel first and then the clicked row so both recuadros are
  visible in `/countries/`. The detail
  table is client-paginated, has a client-selectable page size, marks the active
  sort column with arrows, includes population/area percentages relative to the
  country, and changing its level selector reloads only that table panel.
  First-order comparison rows are shown as a compact client-sortable table below
  the two donuts; its header is sticky inside the table scroll area and the table
  should avoid horizontal scroll on desktop by keeping numeric columns narrow.
  On `/`, every dashboard/detail table except the country detail section titled
  `Tabla de datos` must avoid horizontal scroll: color/numeric columns stay
  intrinsic-width and nowrap; only name/type columns may wrap, and desktop views
  should prefer wrapping over clipping. `Datos generales del pais`, `Tabla de
  datos` and `Subdivisiones de primer orden` must use equal-width columns on
  desktop. Compact share-table sort headers keep the full label and place the active
  sort arrow immediately to the right of the label; the arrow must never wrap
  below the text.
  Share summary tables also remain client-sortable with sticky headers.
  First-order share cards show each area's direct children at the next level with
  mini pie percentages when the country has fewer than 150 rows at that next
  level; do not filter those child rows by entity-type name.
  `/countries/` is now an API-driven country card browser with a 10-column
  desktop grid, a fixed maximum square flag slot, area and population per
  country; clicking a card loads the
  basic country panel plus a direct-child subdivision panel from
  `/api/countries/<country_code>/`. Each direct subdivision row can open another
  card below using `/api/admin-areas/<area_id>/`, showing that area's visual
  identity on the left and its direct-child table on the right recursively until
  the area has no visible children. Direct-child panel titles are generated from
  the pluralized child entity types, joined with `y` when multiple types are
  present. Country-card flags are not independently clickable; clicks on the
  flag slot behave like clicks on the surrounding country card. Those child
  tables are row-clickable, compact, and act as the legend
  for direct-child population/area donut charts; they include a text search and
  sort buttons on every data column, with `Tipo` kept as a compact standalone
  sortable column. If an area has no children, do not show the direct-child title;
  show a centered `Sin datos` empty state instead. Omit the area donut when no
  child has area data. For mixed CityPopulation hierarchies where a first-level
  area has a same-name direct country child at a deeper level with real
  descendants, the recursive browser should prefer that deeper row over a flat
  same-name child with no descendants so rows such as Ceuta/Melilla remain
  drillable without duplicating shares. `/stats/` redirects to `/countries/` for
  compatibility and `/stats/data/` still returns the older statistics chart
  payload for compatibility. Frontend chart rendering is
  centralized in `ciudades_del_mundo/static/ciudades_del_mundo/app.js`
  as `window.CiudadesCharts`, using `[data-chart-widget]` containers for reusable
  bar and donut charts; dashboard donut items dispatch
  `ciudades:chart-item-click` to load the country detail panel. Donut tooltips
  are appended to `document.body` and positioned with viewport coordinates so
  they are not clipped by cards, panels or scroll containers. Do not add native
  SVG `<title>` elements to donut slices because browsers show delayed default
  tooltips; use `aria-label` plus the custom chart tooltip instead. The
  population/area country table is combined into one searchable, sortable table
  without filtering either dataset. Country colors are shared across both donuts
  and the combined table; the colored set is the union of the top 10 countries by
  population and the top 10 by area. Countries outside that colored set are kept
  in the table with no marker and are aggregated into the `Otros paises` segment
  inside each donut. Country detail
  visual identity uses persisted `ciudades_del_mundo_visual_asset` rows with
  Wikimedia/Commons URLs as the normal display source. The browser must not run
  ad-hoc Wikidata searches, but it may render stored remote URLs directly.
  Stored CityPopulation language icons such as wrong `*_2_3.svg` flags are
  ignored in API payloads so the browser shows the placeholder instead of
  another country's flag. Flag and coat thumbnails, plus the `Ficha` action,
  open a new window on the readable internal route
  `/identity/<kind>/entity/<entity_type>/<entity_key>/`. The page shows a
  single language-selected description; do not show all translation rows.
- The base web layout has a client-side style selector next to the language
  selector. The language picker is custom markup with CSS-drawn flag spans
  because native selects and emoji fonts may not render flags consistently. The
  style selector stores `light`, `dark`, `dracula`, `retro`, `retro-blue`,
  `retro80`, `retro80-green`, `retro80-cyan`, `retro80-red`, `8bits`,
  `rainbow`, `paper` or `spain` in `localStorage` under
  `ciudades_del_mundo_theme` and applies the choice through
  `html[data-theme]`; the `Efectos complejos` checkbox stores
  `ciudades_del_mundo_theme_effects` and toggles `html[data-theme-effects]`.
  The themed dashboards must stay responsive at intermediate aspect ratios:
  avoid fixed card/media heights that clip retro/complex effects, prefer
  `clamp()`/`auto-fit` grids and internal scrolling for logs or long tables.
  `rainbow` and `paper` are complex styles, while country-specific styles such
  as `spain` are special styles. Future country styles such as France or Morocco
  should use background images of representative cities/monuments and cards
  based on that country's flag colors. The rainbow complex effect is intentionally
  faster and layered, using multiple subtle gradients plus `rainbow-shift`; keep
  it smooth enough to avoid abrupt color jumps. Keep theme-specific colors in CSS
  variables where possible.
- map pages do not use stored geometry. They geocode by area name in the
  browser using OpenStreetMap/Nominatim through Leaflet, and try to resolve
  flag/coat-of-arms/locator-map images from Wikidata/Wikimedia Commons in
  browser-side JavaScript. The same browser-side Wikidata lookup also displays
  translated labels, country, parent region and capital claims when available;
  the server context additionally passes local registered capitals and
  most-populated city names for related-place translation lookups. Treat all
  Wikidata/Wikimedia results as best-effort external lookups.
- `/configs/` has dynamic config and recent-task tables loaded from
  `/configs/table/` and `/configs/tasks/table/`. Those partials return the
  filtered row set once and use client-side pagination, so changing pages should
  not refetch data. The config table is searchable. Country labels use the
  current UI language via `_display_name`; config names/slugs are not edit links,
  and editing is done through the explicit `Editar` button. Validate and
  populate actions are launched asynchronously from that list, show stacked
  toasts in the top-right corner, enter from the right, poll
  `/tasks/<id>/status/`, disable the clicked button immediately, refresh the
  config row from `/configs/<slug>/summary/` when finished, and refresh visible
  task tables. The visible config lifecycle is exactly `Por Validar`,
  `Validando`, `Validado`, `Populando`, `Populado`, `Limpiando` and `Fallo`;
  there is no config state for cancelled/stopping work. `Validar` is available
  from `Por Validar` and `Fallo`. `Popular` is available from `Por Validar`,
  `Fallo` and `Validado`: pending/failed rows launch `validate_and_scrape_configs`
  so they first show `Validando`, then `Populando`; validated rows launch
  `scrape_subdivisions_with_assets --no-download-assets --page-workers=4`
  directly. `Populado` rows only show `Limpiar`. `Limpiar` is available whenever
  there are previous `AdminArea` rows and no operation is active. `Validando`,
  `Populando` and `Limpiando` only show `Parar`; do not keep disabled
  `Validar`/`Popular` busy buttons visible in active states. The client-side
  optimistic transition after pressing `Validar` or pending `Popular` must hide
  those forms immediately, before the first poll/row refresh comes back. Cancelling returns
  the row to the previous inferible state (`Populando` returns to `Validado`, and
  `Limpiando` rolls its transaction back so the data remains). Task
  tables on `/configs/` and `/tasks/` also poll periodically so
  running/completed states update without a manual reload. The
  `validating` and `populating` config status badges are links to the active
  task detail but must not show underline on hover; they render `Validando` or
  `Populando` with a JS-driven fixed-width animated three-dot suffix so the pill
  border does not resize during the animation; do not use CSS keyframes that
  restart on every row re-render. Do not remove row refreshes entirely to
  protect that animation: while a populate task is active, refresh the config row
  from `/configs/<slug>/summary/` at the end of the three-dot cycle or an
  equivalent low-frequency cadence so the badge can become `Populado` and the
  `Popular` button can re-enable without waiting for a full page reload. Local
  pagination click handlers must be bound only to the pagination controls, never
  delegated broadly enough that ordinary table/button clicks can change pages.
  Dynamic client-paginated tables can opt into column sorting with
  `data-client-sort` headers and row `data-*` sort values. Rows with
  `data-row-href` behave like navigation targets: normal click opens in the
  current tab, Ctrl/Cmd-click and middle-click open the row URL in a new tab,
  and embedded controls such as links/buttons keep their own behavior. Other
  clickable content that loads a detail panel in place (dashboard chart rows,
  country/stat cards, stats rows and visual asset thumbnails) must also treat
  middle-click as "open this detail URL in a new tab". Do not bind
  middle-click globally to ordinary action buttons; only content with a real
  detail URL or anchor-styled buttons should open separate pages. A config row's
  `Validar` button must also stay disabled while its
  `validate-config:<slug>` task is active, including after the paginated table is
  rendered again. Success/failure
  toasts remain visible for 0.5s, then exit to the top-right while lower toasts
  move up. The toast stack is a fixed
  three-slot viewport: never show more than 3 visible task toasts, keep each
  toast at a fixed size, animate movement with `transform` instead of changing
  layout height, and let queued toasts enter into the bottom slot from the right
  after the outgoing toast is clipped above the viewport. Config task toasts
  should reserve their output/log area from creation so success/error output does
  not resize the toast, and the toast log preview should not display a scrollbar.
  Config task toasts have CSS enter/exit animations and must respect
  `prefers-reduced-motion`.
- All visible web data loads should show the shared circular
  `.loading-spinner`: async tables, API charts, country/detail panels, maps,
  Wikidata visual identity lookups, country-card flag rendering, config source
  entity lookups and config generation should not silently wait behind plain
  text.
- `/configs/<slug>/` is a tabbed editor: Manual, Archivo, Scrapping and IA.
  Manual builds the TOML text stored in `ScrapingConfig.content` from page rows
  plus an optional dual-table city unification selector based on already scraped
  `AdminArea` rows. Source entity lookups for that selector must use the stored
  config `country_code` when it differs from the slug, so level and parent
  filters are populated from the real `AdminArea` country. The
  available-entities table has filters for level, name and parent.
  Level options are rendered in the initial HTML and refreshed by the dynamic
  endpoint; the initial table rows for the first level are also rendered into
  the page so the table is populated before the full entity payload finishes
  loading. The level selector has no `Todos` option and defaults to the first
  available level so the table is always filtered. Level labels are built from
  the entity types present at that level, for example Spain level 1 appears as
  `Comunidad Autónoma/Ciudad Autónoma` and Mexico level 1 can appear as
  `Estado/Distrito Federal`; parent options are rebuilt from the selected
  level's direct parents, excluding level-0 root parents, so level 1 has no
  parent filter options. Archivo edits raw `ScrapingConfig.content` TOML with
  server-side validation before saving. Scrapping can generate a draft TOML by
  discovering useful CityPopulation links for the country. IA is
  controlled by `settings.AI_CONFIG_ENABLED`, lets the user choose a provider
  login route, and must not store personal AI credentials. External AI generation
  remains a future integration point until a provider flow is configured.
- web-launched tasks run `manage.py` subcommands in local subprocesses. A new
  task with the same key cancels/replaces the active one. Saving a SQL config
  content row or editable recipe from the UI also cancels/replaces the matching
  active scrape/build task if there is one. Web-launched populate tasks use
  `validate_and_scrape_configs` when the row still needs validation and
  `scrape_subdivisions_with_assets --no-download-assets --page-workers=4` when
  the row is already `Validado`, so independent CityPopulation HTML downloads
  overlap while the child command still emits page-complete events in config
  order. `TaskManager` starts web tasks immediately and does not throttle them
  with a backend queue. Only the browser notification boxes/toasts are visually
  queued when more than three would be visible at once. `TaskManager` persists
  task history/status in the `WebTask` database table, full per-task logs to
  `.web_task_logs/*.log` and task-progress sidecars to
  `.web_task_progress/*.json` in the repo root; these are covered by the
  `.web_*` gitignore rule. Interrupted scraping tasks may also leave technical
  `.web_scrape_resume/*.json` checkpoints until a fresh successful scrape clears
  them, but the web UI no longer exposes a `Reanudar` action. `/tasks/<id>/`
  renders the complete stored log on initial load and then polls
  `/tasks/<id>/status/?since=<offset>` for appended log fragments, so the detail
  page keeps the full console without sending the whole log every second.
  `/tasks/` loads its task table dynamically from `/tasks/table/` with a visible
  spinner and client-side pagination/sorting. Dynamic task-table rows are
  clickable through `data-row-href`; do not add a separate `Abrir` action button
  in those tables. Active tasks loaded after a server restart are marked
  cancelled/interrupted because their subprocess cannot be reattached. Local
  tasks can continue after the browser closes, but cannot continue when
  the PC or Django process is powered off; use an always-on machine or external
  worker if true offline/background execution is required.
- country dashboard/API chart and table data should use
  `_visible_admin_areas()` so rows with `AdminArea.city_merge_status == 3` stay
  hidden from `/api/countries/`, `/api/countries/<country_code>/`, `/stats/`
  data and related dashboard counts.
- Always translate every new user-facing text before considering the change done.
  Wrap static template text with `{% trans %}` / `{% blocktrans %}` or Python
  text with `gettext`, update the relevant `locale/*/LC_MESSAGES/django.po`
  entries, then run `py manage.py compile_local_messages` so Django can load the
  `.mo` catalogs.

## Local Development Notes

- Python 3.13+ or another version with `tomllib` is expected.
- README mentions dependencies: Django, requests, BeautifulSoup, lxml, openpyxl.
- There may be no `requirements.txt`; do not invent dependency management unless
  asked.
- Django version in generated settings comment is 5.2.1.
- Web i18n is enabled with `LocaleMiddleware`; `LANGUAGE_CODE = "es"`,
  `LANGUAGES = es/en/fr/de/ru/it/sr/sr-latn/ar`, and `TIME_ZONE = "UTC"`.
- SQLite is configured for local concurrent use with a 30s timeout plus
  `busy_timeout`, `journal_mode=WAL` and `synchronous=NORMAL` in
  `CiudadesDelMundoConfig.ready()`. The web middleware returns a controlled 503
  for `database is locked` instead of crashing the UI.
- The local project may contain files with Spanish text. Do not "fix" encoding
  or mojibake-looking terminal output unless the task is specifically about
  encoding.
- When fixing encoding, verify source bytes with an explicit UTF-8 read because
  PowerShell `Get-Content` can display UTF-8 files as mojibake. Avoid writing
  non-ASCII text through PowerShell here-strings unless you use ASCII
  `\uXXXX` escapes or another UTF-8-safe path.

## Known Risk Areas

- Running scraping can delete DB rows not found in the new scrape because of
  `delete_missing`.
- Running `build_new_subdivisions` deletes and rebuilds all non-root derived
  rows for the requested country.
- Generated Excel/CSV files can clutter `excels/`; avoid creating them unless
  the task requires verification.
- Web task history/status is persisted in the `WebTask` database table, but
  running subprocesses are still process-local and cannot continue
  after a development server restart. The task side effects in `db.sqlite3`,
  git-ignored `.web_scrape_resume/*.json` page checkpoints,
  git-ignored local `subdivisions/*.toml` seed/export files,
  git-ignored local `historical_divisions/*.py` and `new_subdivisions/*.py` or
  `excels/` remain.
- The web delete page performs confirmed bulk deletes for one source
  `AdminArea.country_code` or one derived `NuevoAdminArea.country_code`.
- CityPopulation layouts can vary by page; prefer small parser tests with saved
  HTML snippets over broad parser rewrites.
- `area_overrides` and configured city merges can affect downstream density,
  most-populated city and representative results.
- `SOURCE` vs `UNIFIED` rows matter; changing merge status logic can alter
  hierarchy expansion and most-populated selection.

## Country UI hierarchy detail

`/countries/` area detail panels group direct child rows by scraped level. This
keeps mixed-parent cases readable, such as a province that has both L3 communes
and L4 urban places attached directly because CityPopulation links the L4 table
to the province instead of to each commune.

## Scraping notes added 2026-06-08 - hierarchy/assets corrections

- Hungary/Budapest: keep `keep_communes = true` in `hungary.toml` so the configured city is inserted as the L2 complete city and the Budapest city districts are shifted below it as L3 children.
- Slovakia/Bratislava and Košice: use the full city codes (`528000`, `599000`) for the configured city rows and keep the district rows below them with `keep_communes = true`. Do not use the older synthetic parent + `child_id` pattern here, because that leaves the complete city and districts at the wrong relative level.
- Aruba: keep regions above urban areas. The intended hierarchy is L1 census regions, L2 cities/urban areas, L3 zones.
- Curaçao: `/curacao/cities/` has unstable/no ids for several place rows and collisions with geozone/neighborhood ids. Keep geozones complete from `/curacao/admin/`; use synthetic L1 city/place containers and `parent_overrides` for geozones instead of persisting the cities-page rows directly.
- Saint-Barthélemy: Gustavia (`25266`) must be parented to Centre (`8446`).
- For territories whose scraped root page does not reliably seed a flag, add `[visual_assets.flag]` fallbacks in the TOML. Belgium also needs explicit `[[visual_assets.admin_areas]]` entries for `BE-VLG`, `BE-WAL` and `04000` region flags.
