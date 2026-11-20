# Architecture and SQL sources of truth

Detailed repository reference. Start with `AGENTS.md` and read only the relevant section.

## HTTP backends

Java and Python are maintained. Java mode uses Django as the public UI origin
and forwards only five migrated routes to the native JVM; pending routes retain
Django owners. Python mode serves Django alone. SQL remains the source of truth;
Java JDBC is read-only. See [current operation](python-http-retirement.md) and
[accumulated state](../estado-actual.md). The archive is not a runtime dependency.

## Project Summary

`BEOGRAD-CITIES-POPULATION` is a local Django project for population and
administrative geography data. It:

- imports administrative divisions and population data from `citypopulation.de`
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

The same source-of-truth rule applies to derived-country and subdivision-group
workflows: `DerivedCountryConfig`, `DerivedSubdivision` and `SubdivisionGroup`
rows are the application data. Their `content` fields may store TOML text, but
runtime views, selectors, builders and API endpoints must read SQL rows and SQL
content, not local seed files under `new_country_configs/` or
`subdivision_groups/`. TOML files are only import/export bridges to and from
SQL.

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
- `requirements.txt`: pinned runtime Python dependencies.
- `tools/`: reproducible web bundle assembly and source-only verification.
- `tests/frontend/`: Node harnesses for the shipped bundle and inline group editor;
  see [verification scope](operations.md#offline-verification-scope).

Django package:

- `ciudades_del_mundo/settings.py`: local Django settings. SQLite DB is
  `BASE_DIR / "db.sqlite3"`.
- `ciudades_del_mundo/urls.py`: root URL routing.
- `ciudades_del_mundo/models.py`: Django models.
- `ciudades_del_mundo/admin.py`: Django admin registrations.
- `ciudades_del_mundo/web/`: feature handlers, form transformations, data queries,
  HTTP helpers, task management and URLs. `web/views.py` exposes explicit public
  HTTP compatibility imports; implementation owners live in the feature modules.
- `ciudades_del_mundo/templates/`: Django templates.
- `ciudades_del_mundo/static/`: JavaScript/CSS sources ordered by `src/manifest.json`
  and generated `app.js`/`app.css`. The group editor also has an inline script in
  its template, outside these bundles.

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
  import `domain/`, but not Django models or concrete adapters. Repository,
  scraper, fetcher and export writer contracts live here; application use cases
  should import those ports instead of declaring boundary protocols locally.
- `application/` orchestrates use cases and may import `domain/`, `ports/` and
  sibling application modules, but not `models`, `services`, `web`,
  `management` or `infrastructure`.
- Composition roots such as management commands and web views wire concrete
  adapters to ports. For scraping, `ScrapeAdminAreas` receives an injected
  `HtmlFetcher` port for concurrent raw HTML prefetching;
  `CityPopulationHtmlFetcher`/`CityPopulationClient` remain infrastructure adapters.
  When the active database is SQLite, command composition roots must inject the
  cross-process `SQLiteWriteLock` into `DjangoUnitOfWork` for scraping writes.
  The lock path is `BASE_DIR/.web_sqlite_write.lock`, but ownership is an OS
  byte-range/flock lock rather than "file exists"; a stale file left by a killed
  process must not block future scrapes. The lock wraps each persistence write
  phase, not the full scrape or read-only calculations, so CLI country workers
  or web tasks allowed by the queue can still download and parse concurrently
  while writes stay serialized in shorter transactions.

Data/config packages:

- `subdivisions/*.toml`: local TOML seed/export files for SQL
  `ScrapingConfig` rows. `sync_scraping_configs` imports them into `/configs/`
  and can export SQL rows back to this directory. Runtime scraping reads SQL
  only after bootstrap; deleting this directory after syncing must not break
  validation, scraping or web actions.
- `ciudades_del_mundo/tests/test_scraping_country_data.py`: self-contained
  scraping contract tests. Do not depend on `country_data/`; the user may
  delete that folder. Spain expectations live inside the test module: 19
  CCAA/autonomous cities, 52 provinces/autonomous cities, 8131 municipalities,
  29509 localities with population >= 20, per-province municipality counts and
  20 hierarchy routes using CityPopulation scraped/coofficial names. The parser
  and validator helpers intentionally live in that test module only.

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
- `new_country_configs/*.toml`: TOML seeds for the `/new-countries/` SQL
  section and `DerivedCountryConfig.content`. The web `Importar TOML` action
  enqueues one `sync_derived_configs new-countries <slug> --force` task per
  seed; each task imports only configuration into SQL and does not scrape or
  build `NuevoAdminArea`.
- `subdivision_groups/groups/<country>.toml`: compact TOML bundles for the
  `/groups/` SQL section. Each country file stores `source_country_code` plus
  one or more top-level assignments such as `INTERNAL_KEY = [...]`. Importing
  the country bundle creates or refreshes one `SubdivisionGroup` SQL row per
  assignment; `SubdivisionGroup.content` remains a compact one-group TOML
  string. Do not add metadata-only fields such as `kind`, `slug`, `entry_slug`,
  `name`, `source_bundle` or `source_python`. The web `Importar TOML` action
  enqueues one `sync_derived_configs groups <country> --force` task per country
  bundle.
- `subdivision_groups/subdivisions/<country>.toml`: country-level derived
  subdivision bundles split away from importable groups. They may contain dictionary
  assignments with keys such as `restar`, `childs` or `spec`; `/groups/`
  import/export must not read this directory, but `/subdivisions/` imports it
  into SQL `DerivedSubdivision` rows. Legacy
  `subdivision_groups/subdivisions/<country>/<entry>.toml` paths are accepted
  only as a compatibility fallback.
- `historical_divisions/*.py` and `new_subdivisions/*.py`: legacy Python
  recipes kept for `build_new_subdivisions` compatibility until the TOML
  builder exists. Do not add new TOML seeds to these legacy Python packages.
- `historical_divisions_old/` and `new_subdivisions_old/`: local backup copies
  of the legacy Python folders; keep them git-ignored.
- `format_html/*.txt`: sample or reference HTML formats.
