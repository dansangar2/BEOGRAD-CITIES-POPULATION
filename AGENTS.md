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

## Current Scraping Memory

The active `/configs/<slug>/` editor and runtime importer use the
CityPopulation block model with `scrape_schema_version = 2`. The attempted
Wikimedia/Wikidata V3 data-import layer has been removed: there is no
`/configs/old/<slug>/` route, no `old-*` runtime rows, no
`scrape_wikimedia_subdivisions` command, and no V3 config parser/importer.
Wikimedia/Commons remains in the project only for visual assets and the
best-effort parent repair used by the CityPopulation flow.

Active CityPopulation configs use `scrape_schema_version = 2` in
`ScrapingConfig.content`. Runtime still reads SQL only; TOML files in
`ciudades_del_mundo/subdivisions/*.toml` are import/export seeds. The web editor
button `Exportar TOML` calls `/configs/<slug>/export-toml/` and writes the
current SQL content back to `ciudades_del_mundo/subdivisions/<slug>.toml`.
The `/configs/` page also exposes `Importar TOML`; it starts a background
`sync_scraping_configs --force` task that overwrites SQL config rows from
`ciudades_del_mundo/subdivisions/*.toml`. That action imports configuration
only and must not scrape, clear or otherwise mutate `AdminArea` rows.
The web `Popular` action bootstraps missing configuration rows before starting
work: for a single missing slug, it imports only
`ciudades_del_mundo/subdivisions/<slug>.toml` into `ScrapingConfig`; for bulk
`Popular todo` / `Popular no populados`, if `ScrapingConfig` is empty, it first
imports bundled TOML seeds and then selects eligible rows. This bootstrap is
configuration-only; validation and scraping still run afterward through the
normal task commands.
Each `/configs/<slug>/` editor also has `Importar TOML`; it reads only
`ciudades_del_mundo/subdivisions/<slug>.toml` and overwrites that SQL
`ScrapingConfig` row. It is a config-only import, not a scrape or data cleanup.
The `Ciudades` subsection in `/configs/<slug>/` has its own city-unification
TOML bridge: `Importar TOML` / `Exportar TOML` read and write only
`ciudades_del_mundo/cities_merge/<slug>.toml`. Those files contain standalone
`[[cities]]` blocks. Importing them replaces only `[[cities]]` in the SQL
`ScrapingConfig.content`; it must preserve `[[pages]]`, assets, scraping fields
and every other config fragment.

Configured `[[pages]] source` values are `cities`, `admin` and `citiesadmin`.
Infrastructure still exposes legacy concrete scrapers for tests/import
compatibility, but new SQL/TOML country configs should use only those three
section-based formats:

- `cities`: `infosection -> major_subdivision -> cities`
- `admin`: `infosection -> major_subdivision -> minor_subdivision`
- `citiesadmin`: `infosection -> major_subdivision -> minor_subdivision -> cities`

One `[[pages]]` row is a block. If `path = [...]` has several routes, the parser
expands it to one page per route with the same configuration and stores
`ScrapingPageConfig.block_index` / `path_index` so application code can validate
and diagnose each original block as a unit. Relative paths are
resolved under `https://www.citypopulation.de/en/` and the SQL config slug.
`source` is only the HTML/parser format (`cities`, `admin` or `citiesadmin`);
it must not imply that the route is `/cities/` or `/admin/`. A default L0 is
inferred only for the country root itself, or exact root routes such as
`<slug>/cities` and `<slug>/admin`; nested routes such as
`<slug>/<region>/admin` need explicit `force_highest_level`/`lowest_level` when
they do not follow the default chain.
The web config generator must use the same rule when deciding whether to omit
`force_highest_level`; selecting `admin` or `cities` in the UI is a parser
choice, not a route template.
`include = { ... }` chooses persisted sections; disabled sections can still be
used as same-page parent context when CityPopulation exposes parent rows in a
table that should not be saved. Explicit `include_root = false` wins over
`include.infosection = true`; use that shape when a page's root/infosection is
only context and should not be required by block validation. `repeat =
{ infosection = 2 }` creates explicit same-entity chains on consecutive levels.
Set `enabled = false` on a `[[pages]]` block to keep the block in SQL/TOML but
skip it completely when expanding the scraping plan; omitted `enabled` means
active. The `/configs/<slug>/` editor exposes this as an icon-only
disable/reactivate toggle; disabled rows are visually dimmed but remain
editable.
Existing edit forms autosave `ScrapingConfig.content` after manual or raw TOML
changes. Edit mode must not show a visible save button or persistent autosave
status text; the visible `Guardar` button remains only for creating a new
config. When manual config saves include both `pages_json` and visible form
controls, backend parsing must prefer the visible POST controls for source,
enabled, forced levels, parent level, include, repeat and sum-to-parent values
so stale hidden JSON cannot undo a user's latest field edits. Frontend
serialization of hidden checkbox/path fields should be silent during
`pages_json` generation; user `change` events save immediately and pending
autosaves are flushed with `sendBeacon`/`keepalive` on page unload so a quick
refresh does not drop a config change. If `app.js` or `app.css` changes editor
behavior, bump the static query-string version in
`templates/ciudades_del_mundo/base.html`; otherwise browsers can keep a stale
config editor even after the backend and static files are fixed.
`force_highest_level` sets the block base level and prevents repeated-anchor
alignment from lowering that forced block. `parent_level`/`force_parent_level`
marks the intended parent level; it is applied before duplicate collapse, so
same-QID/same-name children such as Paris `75056` can attach to a level-3 `751`
parent before the duplicate-looking rows are merged. Forced parent matching may
use QID/code/name plus URL scope and can reparent `cities` rows even when the
parser initially placed them at a shallower level; a parent is allowed to have
direct children from more than one lower level when CityPopulation skips an
intermediate layer. `sum_to_root = true` is labelled in the UI as `Sumar al
padre`; it marks that page's branch so linked parent/root metrics are rolled up
later.
For schema v2, country root pages using `source = "cities"` do not have to end
in `/cities`: a root path like `path = [""]` also starts at L0. A page shaped as
`cities` but served under `/admin` can also start at L0 when it persists the
infosection, which covers small territories whose only useful page is
`/<slug>/admin/`.

`ScrapeAdminAreas` scrapes one block at a time. Prefetch still downloads URLs in
parallel, but only inside the current block; the next block does not start until
the current block is instantiated and validated in memory. V2 block validation
checks configured section annotations for `cities`/`admin`/`citiesadmin`, parent
presence for every row below the block base level against the current block plus
already validated previous blocks. It does not write SQL before this validation,
and it must not require most-populated-city assignment at this stage because that
selection depends on the post-linking hierarchy from step 2. If a
block fails, the application raises `ScrapeBlockValidationError` with
`SCR-BLOCK-001`; `scrape_subdivisions` writes
`.web_scrape_block_errors/<slug>/<slug>_block_<n>_*.txt` containing the involved URLs,
raw scraped HTML and structured problem codes such as `SCR-BLOCK-SECTION` or
`SCR-BLOCK-PARENT`. These files are ignored by the
existing `.web_*` gitignore rule and are intended to be pasted into a later AI
debugging session.

After all blocks pass step 1, step 2 is still in-memory: the use case runs the
existing cross-block linker (`normalize_citypopulation_entities`), entity
merges/configured cities and runtime config extensions, then runs the injected
Wikimedia parent repair before strict final link validation. That repair lives
in `ciudades_del_mundo/services/wikidata_parent_links.py`: for rows still
without a valid parent and with `data_wd`, it reads Wikidata `P131` plus
`parentLabel`, accepts a parent only when that parent already exists in the
same scrape by QID or by a unique normalized parent label, and adjusts the child
level to `parent.level + 1`. `parent_level`/`Forced parent level` is a
preference, not an absolute blocker: if Wikidata only identifies an already
scraped parent at another level, the row may still attach there. If Wikidata
does not expose a parent, the same repair may fall back to a unique
CityPopulation URL-scope parent already scraped, e.g. a path segment such as
`/overijssel/_/...` matching the unique `Overijssel` row. Only after this repair
does the use case validate final links before enrichment or SQL persistence.
For v2 configs, any final row with `level > 0` must have a non-self
`parent_code` that exists in the same final entity set. If not, the use case
raises `ScrapeLinkValidationError` with
`SCR-LINK-001`; `scrape_subdivisions` writes
`.web_scrape_block_errors/<slug>/<slug>_link_*.txt` containing all completed page
HTML and the invalid rows. Do not let v2 scrapes continue with merely a warning
from `on_unlinked_entities`; cases such as Algeria/Saïda must fail before
`save_many`.

Scraping console output is organized around five visible steps:
`paso 1/5` scrapes and validates blocks, `paso 2/5` links blocks in memory,
assigns each area's most-populated city from the highest available descendant
level, `paso 3/5` resolves Wikimedia/Wikidata resources, `paso 4/5` assigns
visual resources, and `paso 5/5` persists or confirms database work. Keep new
progress messages aligned to those steps.

Cross-page linking lives in `ciudades_del_mundo/application/citypopulation_linking.py`.
It is application-layer domain orchestration and must not import infrastructure.
It aligns repeated block anchors, removes page-context country-code rows,
collapses equivalent same-level rows, repairs explicit repeated roots, rewires
locality/city rows to the most specific safe code-prefix parent, disambiguates
CityPopulation code reuse across incompatible identities, attaches parentless
rows whose conflict-safe code still carries a parent scope such as `17_174 ->
17`, attaches `sum_to_root` branches and guards roll-ups against parent cycles.
Large countries enter this linker immediately after all `FOUND ... entities`
scrape logs and before the final SQL save. Keep parent/code lookups indexed:
`_rewire_parent_codes`, duplicate scoring and prefix repair must use code maps
or prefix maps instead of scanning the full entity list per row. Spain has
roughly 37k rows and should normalize in seconds, not appear stuck for minutes.
After duplicate/shortcut cleanup, self-parent links (`parent_code == code`) are
invalid and must be repaired generically by the most specific safe code-prefix
parent before falling back to the country root; this protects cases such as
Spain locality pages where a municipality context row can otherwise survive with
itself as parent.
Prefix repair must not let a
shorter/higher prefix replace an explicit more-specific parent; this protects
cases such as France `011 -> 01034` while still fixing Spain localities such as
`03082 -> 030820...`. It may, however, replace an alphanumeric higher parent
with a numeric code-prefix parent at the same/lower child level when the prefix
parent is territorially scoped and more specific; this covers single-province
communities such as Spain `MAD -> 28 -> 28079` without country-specific code.
If the current one-level parent has the same or better specificity and its name
appears in the child's URL scope, keep that parent instead of rewiring by code
prefix; this protects same-name French arrondissement/commune pairs such as
`011 Belley -> 01034 Belley` while still fixing wrong sibling parents such as
Xàbia/Jávea localities.
URL scope extraction must preserve normalized slash-path segments before
splitting them into words, because CityPopulation often encodes multiword
territories with underscores. For example, `/portugal/admin/castelo_branco/...`
must produce `castelo branco` as a scope token so a valid `05 Castelo Branco`
parent is not replaced by an unrelated numeric prefix such as `16 Viana do
Castelo` for code `1690502`.
Prefix repair also must not treat CityPopulation codes as
globally prefix-safe across unrelated URL branches; France has collisions such
as Réunion `9152` and Essonne communes `91521`, so `cities` prefix candidates
need matching territorial URL-scope tokens before they can reparent a row. If a
candidate block anchor has no URL scope while the child does, it is not a safe
prefix parent; otherwise rows such as Martinique `6527` or Mayotte `8125` can
steal mainland `65270...`/`81250...` rows. The HTML client must resolve `#i...`
anchors against the full page URL, not only the domain, so overseas pages retain
scope such as `/france/cities/mayotte/`.
If a page root is persisted with the configured country code but same-page
direct children still point at CityPopulation's raw root id, normalize that root
alias before block validation instead of adding country-specific fixes; this
covers small countries/territories whose `table#ts` children reference the HTML
root id.
If a country root already exists, an ordinary parentless level-1 row from a
later block may be attached directly to that root before strict link validation;
this is the generic repair for blocks that disable `infosection` and expose a
province/region such as Algeria `20 Saïda` without a parent. Do not handle that
case with country-specific code.
Duplicate collapse must also preserve block isolation: two same-level block
anchors with the same normalized name, such as France overseas `Saint-Pierre` or
`Grande-Terre`, are not equivalent unless they already match by QID/code or by
the same parent/scope. Do not reintroduce a global same-name key for parented
block anchors; it mixes children across pages before cross-block linking.
When collapsing equivalent same-level rows, preserve a valid parent from either
duplicate before discarding one representation; a higher-quality row must not
lose the parent that the other representation already proved.
Matching priority is `data-wd`, CityPopulation code, normalized same-level
name/parent identity and stored code; do not add country-specific branches for
Belgium, Spain, Italy or France if a generic rule or SQL config hint can express
the case.
If a persisted `table#ts` row carries an annotation like
`CityPopulation parent hint: <name>`, the linker may reparent it to the unique
level-1 candidate with that normalized name, preferring candidates under the
row's current parent scope. This is used for Portugal locality rows whose
visible CityPopulation name includes `(... in: Parish)`; rows without that hint
must not be guessed into a parish from municipality-only HTML.

The section parser lives in
`ciudades_del_mundo/infrastructure/scraping/citypopulation_sections.py`. It is
HTML infrastructure only: no Django writes, no SQL decisions and no asset
downloads. Parent lookup indexes all visible `itemprop=name` aliases plus
parenthetical, bracketed and slash-separated names, so co-official CityPopulation
labels such as `Xàbia (Jávea)` can match `radm = Jávea` without country-specific
code. The lower-level row parser also preserves a literal parent hint from
names shaped like `Place (in: Parish)` as an annotation so application linking
can attach that row to the intermediate parent if the country config has already
scraped it. It also detects `cities` pages whose first table is actually grouped
as major+minor and treats that HTML shape like `citiesadmin` for that page. Grouped
`table#tl` bodies do not have to appear parent-first: if plain child `tbody`
blocks appear before or between classed `tbody.adm`/`tbody.admin1` totals, the
parser links the child block to the matching classed major by `data-adm`, row
id, abbreviation/name lookup and then population/area totals. `table#ts` parsing
still reads the actual contents table only; helper sections such as Major Cities
or Major Agglomerations are ignored unless CityPopulation exposes them as the
configured `table#ts`. Some `cities` pages, such as Netherlands province urban
center pages, expose the block parent as the only row in `table#tl > tfoot` and
the children in `table#ts`; when the block disables infosection and requests
`major_subdivision + cities`, the parser treats that `tl/tfoot` row as the
block's major subdivision parent. A root page that persists infosection should
not use this as a duplicate root.
`CityPopulationClient` and `CityPopulationHtmlFetcher` prefer the `lxml` parser
when available but must fall back to Python's built-in `html.parser` when
`lxml` is not installed; scraping should not fail at runtime only because the
optional parser library is missing.

Wikimedia/visual assets were intentionally left on the existing flow. Scraping
continues to pass fetched HTML and parsed `data-wd` QIDs to the existing asset
seeding path; normal operation stores QID, `commons_filename`, `remote_url` and
translations in SQL and renders Wikimedia `Special:FilePath` URLs without
downloading local files. If a concrete QID does not expose P41/P94/P158,
`services.visual_assets.commons_visual_asset_candidate_for_name()` can search
Commons by entity name and kind (`flag`, `coat`, `seal`) as a generic fallback.
The batch command uses that fallback only for levels explicitly requested with
`--subdivision-asset-levels`, or level 1 when no level is requested, to avoid
thousands of Commons searches; it only searches automatically when the QID has
at least one Wikidata visual asset and another requested kind is missing. Set
`visual_assets.commons_fallback_for_empty_qids = true` only for configs where
name-based Commons searches are worth the extra requests. Manual
`ensure_visual_assets --admin-area` can use it for one area at any level.
Unassigned visual resources are warnings, not scraping blockers:
`scrape_subdivisions_with_assets` reports `SCR-ASSET-W001` and keeps the
configuration populated. Wikidata claim selection should prefer current/newer
claims, especially for flags, so modern assets win over historical images when
both exist.

Local diagnostic logs expire after 90 days. `ciudades_del_mundo.web.log_retention`
cleans `.web_task_logs/`, `.web_scrape_block_errors/`, `.web_scrape_pages/`,
`.web_ai_autoconfig/`, `.web_task_progress/` and `.web_scrape_resume/`. New web
task logs are grouped by task key/country, for example
`.web_task_logs/algeria/<task_id>.log`; validation logs are grouped by country,
for example `.web_scrape_block_errors/algeria/algeria_link_*.txt`.
`scrape_subdivisions` also writes page snapshots by country and task/run under
`.web_scrape_pages/<slug>/<task-or-cli-run>/`: `index.jsonl` lists each
completed page and points to its raw HTML plus parsed entity JSON. This is local
diagnostic state only and must not become a runtime scraper input.

AI autoconfiguration support lives in
`ciudades_del_mundo/services/ai_autoconfig_context.py` and the management
command `prepare_ai_autoconfig`. Territorial target files belong in
`ciudades_del_mundo/autoconfig_specs/<slug>.txt` and describe the intended
persisted hierarchy by level, e.g. `Region > Department > Commune > Locality`.
When the user asks to "configurar" or "autoconfigurar" a country, first run or
inspect `py manage.py prepare_ai_autoconfig <slug>`; if no slug is given, use
the recent diagnostics mode (`py manage.py prepare_ai_autoconfig --limit 5`) and
work through the newest countries. The generated Markdown dossier in
`.web_ai_autoconfig/<slug>/` combines the territorial objective, active SQL
TOML, latest validation/task logs, latest scraped-page index and code map. Use
that dossier to decide whether the fix is TOML-only or a generic parser/linker
code change. If the target `.txt` is missing or the country has no concrete
administrative hierarchy to match, do not change scraping behavior.

Offline parser examples for the current v2 behavior live under
`ciudades_del_mundo/html/{belgium,france,italy,spain}/`. They are fixtures for
manual validation and tests, not runtime inputs.

Belgium v2 config detail: the special `bruxelles` block persists
`major_subdivision + cities` with `infosection = false` and
`force_highest_level = 3`, so `21000 Bruxelles-Capitale` stays at L3 and
municipalities such as `21004 Bruxelles` stay at L4. The `places/*` block uses
`parent_level = 4` so submunicipalities can attach to the municipality layer by
QID/code/name instead of to the repeated region context.

Portugal v2 config detail: the intended hierarchy is
`Country > District/Autonomous Region > Municipality > Parish > City/Locality`.
The root `/cities/` page persists only the infosection country. `/admin/` starts
at L1 and persists districts/autonomous regions plus municipalities.
District-level `*/admin/` pages start at L2 and persist municipalities plus
parishes. District `cities` pages persist only `table#ts` as L4 localities while
using `table#tl` as L2 municipality context (`table_levels = { tl = 2, ts = 4 }`
and major subdivision disabled). If a locality name includes
`(... in: Parish)`, the parser records that parent hint and the linker can move
the locality under the scraped L3 parish. If CityPopulation gives only a
municipality column, keep the locality under the municipality; do not invent a
parish by name/code guessing.

Data persistence happens only after block validation and global linking have
completed. `ScrapeAdminAreas` wraps the main `save_many` call in up to 10
consecutive retries for transient write failures and `scrape_subdivisions` logs
`bloque de persistencia guardado completamente` when the SQL persistence block
is done. `scrape_subdivisions_with_assets` assigns visual assets after the
scraped rows exist, because assets are keyed to persisted `AdminArea.id` values.
Do not reintroduce a pre-scrape full delete as the normal path; the repository
updates changed rows, keeps unchanged rows and deletes absent rows after the new
scrape is known.

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
- When the user reports a console scraping error, inspect the latest log for
  that country first. New task logs live under `.web_task_logs/<slug>/` and
  scrape validation logs under `.web_scrape_block_errors/<slug>/`; older flat
  files may still exist and should be checked as fallback.
- At the end of each user request, report token usage and remaining token
  budget when the tool/runtime exposes that information. If exact usage is not
  available, say so instead of inventing numbers.
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
- merge status: `city_merge_status`; `0` is a normal scraped row, `1` is a
  unified city/entity created by configured city unification or merge logic, `2`
  is a source entity used to build that unified city/entity, and `3` is a hidden
  row excluded from public APIs/charts.
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

`DerivedCountry` and `DerivedCountryConfig` in
`ciudades_del_mundo/models.py`:

- SQL base for future TOML-driven derived countries
- `DerivedCountry.slug` groups variants for one conceptual country, such as
  Spain with several historical/political configurations
- `DerivedCountryConfig.content` stores TOML with `kind =
  "derived_country_config"`, `derived_country_code`, group references and SQL
  source-selection fields. The visual `Nueva entidad` flow stores selected
  `AdminArea.id` values under `[selection] include_ids` / `subtract_ids` plus
  `[[selection.items]]` rows with `operation = "add"` or `"subtract"`.
- `/new-countries/` lists containers, `/new-countries/<slug>/` lists configs,
  `Nueva entidad` creates TOML from a SQL AdminArea tree, `Configuracion` edits
  TOML and `Vista` links to built `NuevoAdminArea` data when rows exist for the
  config's `derived_country_code`
- Current builder compatibility still uses legacy Python modules; do not remove
  `new_subdivisions/*.py` until the TOML builder exists

`SubdivisionGroup` in `ciudades_del_mundo/models.py`:

- SQL base for reusable TOML groups of source `AdminArea` selections
- `content` stores compact TOML for source `AdminArea` aggregation, normally
  `source_country_code = "<country>"` plus one top-level assignment like
  `ALBACETE_A_CUENCA = ["Villatoya", ...]`; optional `[[country_groups]]`
  blocks store country-specific section selections. The SQL `slug` remains
  `<country_code>_<group_slug>` so the same internal key can exist in different
  countries. Import/export seed files are grouped by source country as
  `subdivision_groups/groups/<country>.toml`; each top-level assignment in that
  file maps to one SQL row. Group keys must be unique within one source country.
- `/groups/` lists and edits these groups; `Importar TOML` queues one SQL
  import task per `subdivision_groups/groups/<country>.toml` seed
- `/groups/` uses the same source-country card format as `/countries/`. Country
  cards are client-side buttons, not query-string links; clicking one keeps the
  URL at `/groups/` and opens the matching hidden country panel. The selected
  panel is intentionally two separate `panel` boxes in a
  `group-country-detail-grid`: `Agrupaciones` at 40% and `Nuevas divisiones` at
  60%. `/groups/` no longer renders the temporary `Ciudades` box; scraped
  cities now belong in the `/configs/<slug>/` editor subsection. In the narrow
  `Agrupaciones` box, import/export/new actions are icon-only fixed-size SVG
  buttons with `title` and `aria-label` descriptions so the action row stays on
  one line. Local table toolbars in `/groups/` place search, page size and
  a page selector in one compact row when there is enough width; the same page
  selector is repeated at the foot of each table and both selectors must stay
  synchronized by the client pagination code. The `Nuevas divisiones` box lists
  user-created `NuevoAdminArea` entities for that country. Its action row includes
  `Importar TOML`, `Exportar TOML` and `Nueva subdivisión`, wired to the
  `/groups/subdivisions/<country>/...` editor routes for the selected country
  while the SQL section remains `/subdivisions/`. The loading spinner
  overlays both boxes through the `data-group-country-panel`
  wrapper, not the country flag card or only one table; keep the panel content
  in the layout behind the overlay so switching countries does not jump
  abruptly. The left aggregation box intentionally omits source-country
  metadata such as `Pais fuente`, the country label/code and seed counts. Its
  right-table `NuevoAdminArea` rows should be clickable when they can be
  resolved back to a SQL `DerivedSubdivision` definition; the row uses the URL
  route `/groups/subdivisions/<country>/<slug>/` by matching the materialized
  code or a child-code prefix. The left aggregation table has only the columns `Grupo`
  and `Grupos`, where `Grupo` is the
  internal grouping name and `Grupos` is the number of stored names across that
  entry's country blocks. It uses fixed layout/wrapping and must not need
  horizontal scroll. Keep its local search box and client-side pagination
  controls with page sizes 25, 50 and 100. Group rows are clickable via
  `data-row-href` and navigate to edit; do not add a visible `Editar` action
  column or a per-row `Exportar TOML` action to the group table. The panel
  action row keeps `Exportar TOML` immediately next to that country's
  `Importar TOML`, and export writes the source country's bundle. The panel
  contains that country's `Nuevo grupo` link to `/groups/groups/<country_code>/new/`.
  Groups whose source country is not visible remain available through an `Otros
  paises` fallback card. The right box uses the same local-table format as the
  left: search, page-size selector, client pagination and a level filter. Its
  rows are user-created `NuevoAdminArea` entities, mapped from the selected
  source country through `DerivedCountry`/`DerivedCountryConfig` when available,
  with `Nombre de la entidad`, `Tipo (Nivel)`, `Terreno` and `Población`; it
  does not show the old level-summary `Sumar nivel` column.
- `/groups/groups/<country_code>/new/` and `/groups/groups/<country_code>/<group_slug>/`
  edit one country-scoped grouping entry. The editor header shows only the
  internal code, or `NUEVO GRUPO` while creating; do not show the `Agrupaciones`
  eyebrow, country label/code metadata, or a separate visible `Nombre` field.
  The form itself is not a panel so there are no boxes inside boxes: the first
  top-level panel contains the required uppercase internal code input, a
  searchable country select, and the `Añadir` button in its own slot; each
  country block is a sibling top-level panel. Country blocks render in one
  column so their internal tables have horizontal room. The selected country
  always has the first block and that block cannot be removed. The country-add select must be searchable and
  must omit countries already present in the group. Inside every country block,
  render a cascade of searchable selects from level 1 through `N-1`, where `N`
  is the country's effective maximum source level. Do not use raw `max(level)`
  when a tiny residual deepest level exists or when deeper rows are mostly
  localities/municipality seats; for example, Spain's group editor treats
  municipalities as the leaf rows and exposes provinces as the parent section,
  not municipality sections that would list localities. Do not expose the
  maximum level as a select; those leaf rows are chosen from the badge list
  after adding their parent level. The first select loads top-level
  subdivisions and every lower select filters by the parent selected in the previous level through the
  `/groups/source-data/` `section_parent_id` query parameter. Every level row
  has its own `Añadir` button, and subdivisions already added to that country
  block must disappear from the relevant add select. Section options and badge
  labels include the entity type in parentheses, such as `Abla (Municipio)`.
  Keep `ancestor_ids` in the client state: adding an upper entity must remove
  lower section blocks that depend on it, and lower options whose ancestor is
  already selected must be hidden. These selects must not include a blank option
  row; when options exist, they select the first valid value by default. The
  group editor uses its own searchable select widget:
  the native select stays as the source of truth, but the visible control opens a
  dropdown containing `input.group-search-select-input` and filtered option
  buttons. Do not render a separate search input above/beside the select. The
  search is case-insensitive and accent-insensitive, and selecting an option must
  dispatch the native `change` event. Adding a country appends its block to the end of
  `data-group-country-blocks`; do not insert new country blocks above existing
  ones. Adding a subdivision opens a modal editor, while the country block shows
  a table with the selected entity, level, selected child labels and edit/remove
  actions. The modal reuses the city-unification pattern: X/close discards the
  temporary edit, and only `Guardar` commits the section back to the table and
  hidden JSON state. Inside the modal, available direct children stay on the left
  and selected group children on the right. Each badge list should have only one
  visible frame under its label, not a framed pane containing another framed
  list. Clicking a badge moves it between panes. Country blocks can be removed except
  for the original country block. Keep `Guardar` available above and below the
  country blocks with visible spacing. The `/groups/source-data/` JSON endpoint
  is used by this editor and should stay lightweight; child/section option
  lookups use direct `values()` queries and avoid per-row dynamic translation
  lookups for speed. Saving writes one SQL `SubdivisionGroup` row with slug
  `<country_code>_<group_slug>` and compact TOML content: `source_country_code`,
  a top-level compatibility assignment `INTERNAL_NAME = ["municipio", ...]`,
  repeated `[[country_groups]]` blocks and nested
  `[[country_groups.sections]]` rows. Do not add `kind`, `slug`, `entry_slug`,
  `name`, `source_bundle` or `source_python` metadata to group content.
  Backend validation rejects duplicate group keys inside the same source
  country, and the frontend disables `Guardar`/`Importar TOML` with a visible
  warning when the internal code collides. That duplicate warning reserves its
  line under the internal-code input and is positioned outside normal layout
  flow, so showing the error does not resize or vertically misalign the
  code/select/button control grid.
  Compatibility routes `/groups/new/` and `/groups/<slug>/` redirect to the
  country-scoped editor when they can infer the target.
- Importable group TOML lives under `subdivision_groups/groups/<country>.toml`.
  Each file represents one source country and may contain many
  `INTERNAL_KEY = [...]` assignments. Importing it creates or refreshes one
  `SubdivisionGroup` row per assignment, using SQL slugs shaped as
  `<country>_<internal_key>`. Legacy `groups/<country>/<group>.toml` files are
  accepted only as a compatibility fallback when no country bundle exists.
  Dictionary assignments with keys such as `restar` or `childs` describe
  derived subdivisions, not reusable groupings, and belong under
  `subdivision_groups/subdivisions/<country>.toml`.
- The `/groups/groups/<country>/<group>/` editor must load editable group data from
  SQL `SubdivisionGroup` rows, not directly from TOML seed files. When a stored
  legacy entry only has flat municipality names in `names` or
  `include_names`, the view resolves those names against SQL `AdminArea` rows
  for that country, prefers non-locality candidates when duplicate labels exist
  and groups them by the selected entity's parent. This makes Spanish legacy
  lists pick municipality rows and render one province section such as
  `Albacete (Provincia)` with its selected municipalities, instead of one
  municipality section per same-name locality/seat. That legacy flat-name
  hydration must happen in the initial server context from SQL; the browser must
  not post unresolved `names` to `/groups/source-data/` during page load.
- `/groups/` TOML import is grouped by country. The header `Importar TOML`
  imports every `subdivision_groups/groups/<country>.toml` bundle; the
  selected-country panel has its own `Importar TOML` form that posts
  `country_code` and queues only the matching country bundle. That same panel
  shows `Exportar TOML` next to `Importar TOML`; it posts to
  `/groups/<country>/export-toml/` and writes the country bundle from SQL.
  Forms marked `data-loading-submit` show the standard button spinner while
  posting.
- The group edit page exposes per-group `Exportar TOML` and `Importar TOML`
  actions. Export writes the current SQL groups for that source country back to
  `ciudades_del_mundo/subdivision_groups/groups/<country>.toml`; import reads
  that country bundle, refreshes all contained SQL group rows and reloads the
  edited page after a successful AJAX import.
- Groups are intended to be referenced by future `DerivedCountryConfig` TOML
  instead of importing historical Python fragments directly

`DerivedSubdivision` in `ciudades_del_mundo/models.py`:

- SQL base for fictional or historical administrative units that can be built
  into `NuevoAdminArea` with `py manage.py build_derived_subdivisions <pais>
  --force`
- `content` stores TOML with `kind = "derived_subdivision"`, root metadata
  (`internal_name`, `source_country_code`, `name`, `code`, `parent_code`,
  `entity_type`, `level`, `generic_name`, `capitals`, `flag_url`, `coat_url`)
  and repeated `[[include]]` / `[[subtract]]` blocks
- `[[include]]` blocks can reference source subdivisions by names/ids/codes and
  reusable `SubdivisionGroup` keys through `groups`
- `[[subtract]]` blocks are intended for lower-level exclusions from included
  parents; for example a province can include `Albacete (Provincia)` and
  subtract group `ALBACETE_A_CUENCA`, while another subdivision can include that
  same group
- `[[capital_groups]]` can assign capitals made from a `SubdivisionGroup` or
  explicit names; `capital_name` is optional and is stored as a capital display
  override for the grouped municipalities
- `/subdivisions/` lists these definitions by source-country cards, with the
  left table for created historical/fictitious subdivisions and the right table
  for available reusable groups. The header and country panel can import
  country-level seeds from `subdivision_groups/subdivisions/<country>.toml` via
  `sync_derived_configs subdivisions <country> --force`; export writes that
  same country bundle from SQL. `Popular` enqueues
  `build_derived_subdivisions <country> --force`.
- `/groups/subdivisions/<country>/<slug>/` uses the same lower source-selection
  pattern as `/groups/groups/<country>/<group>/`: compact header, save actions
  above and below, a first `group-entry-main-box` for base fields, then two
  visual panels for `Sumar` and `Restar`. The source country is always the
  country segment in the URL and is stored in a hidden field only; the form no
  longer exposes separate `Pais fuente`, `Seccion padre` or `Codigo propio`
  controls. On save, `parent_code` is fixed to the selected source country's
  root code and the full `code` is derived from `internal_name`; do not add a
  visible `Codigo calculado` field back. For example source country Spain uses
  root `ESP`, so `internal_name = "CASTILLA_VIEJA"` saves
  `code = "ESP-CASTILLA_VIEJA"`. GET requests render only
  the shell and spinner; `/groups/subdivisions/<country>/<slug>/data/` hydrates
  base fields, root `flag_url` / `coat_url`, parent options, selected capital
  labels and only the current `[[include]]` / `[[subtract]]` source selections.
  It must not preload all `SubdivisionGroup` rows or return a `group_countries`
  list, except for hydrating already referenced group badges in the current TOML.
  The lower source area queries SQL `AdminArea` lazily through
  `/groups/source-data/` using the same level/section rules as
  `/groups/groups/`; do not expose deeper leaf levels in the derived-subdivision
  UI when the group editor hides them. When this editor passes
  `include_groups=1`, `/groups/source-data/` may also return country-scoped
  `SubdivisionGroup` options as `source_kind = "group"` badges at their resolved
  member level and common parent scope. Resolve those group badge candidates in
  bulk per request and filter by level/parent before building member tooltip
  text; do not call the full `AdminArea` payload/ancestor metadata path once per
  grouped municipality. Like the group editor, the direct-source
  hierarchy includes a root country row before `Nivel 1`; its modal includes the
  country itself as level 0 plus direct level-1 administrations, all loaded from
  SQL, so either can be added as direct source badges. Visible TOML
  editing is not part of this form, but the hidden `content` textarea remains
  the persistence carrier. When the lower visual selection is dirty, POST data
  includes `include_ids_json` / `subtract_ids_json`; the view converts selected
  source rows to `[[include]]` / `[[subtract]]` blocks with DB `ids = [...]` for
  `AdminArea` badges and `groups = [...]` for reusable group badges.
  The lower country selector lives in its own control panel and has one
  `Anadir` action that adds a source-country block. Do not render separate
  `Sumar` and `Restar` country panels. Inside that single block, the hierarchy
  `Anadir` buttons open a transfer modal with `Disponibles` and `Grupo`,
  matching the `/groups/groups/` interaction style, and selected badges can be
  direct `AdminArea` entities or reusable `SubdivisionGroup` references. Group
  badges use a distinct color and a title/tooltip listing the resolved SQL
  members. The transfer modal has one search filter that normalizes case and
  accents, so queries such as `Le`, `le` and `Lé` match the same badges, plus a
  `Grupos` checkbox that filters both modal columns to group badges only. Modal
  Direct `Anadir` modal calls must limit reusable groups to the same level as
  the direct children of the selected hierarchy row, so root country add shows
  only level-1 groups. Descendant/exclusion modal calls must resolve reusable
  groups across all lower descendant levels under the selected included parents.
  Modal blocks and selected-source table rows separate normal `AdminArea` badges from
  group badges by type and level, e.g. `Nivel 3` and `Grupos - Nivel 3`. In the
  modal, separate those sections with a title and neutral thick line inside the
  `Disponibles`/selected badge container; do not use nested boxes/cards for
  each level. When `/groups/source-data/` loads direct children, it keeps only
  the shallowest direct child layer if inconsistent DB rows put multiple levels
  under the same parent. The
  selected-source table groups rows
  by source country, source type, level and parent scope so same-scope items render as badges
  in one row. That table column order is `Incluidos`, `Excluidos`, `Nivel`,
  `Acciones`. The two first columns render badge labels and split the remaining
  width 50/50 after reserving fixed narrow widths for `Nivel` and the row
  buttons. `Editar` opens a same-scope selector for all sibling `AdminArea` rows
  with the same source country, level and parent as the edited row; for example
  Spain L1 rows show all Spanish autonomous communities, while a province under
  Castilla y Leon shows the other provinces under that same parent. That edit
  selector uses `/groups/source-data/` with `source_mode=items` so it returns
  all rows at that level, not only sections that have children. `Excluir` opens
  a lower-level descendant selector for every included item in that table row
  through `/groups/source-data/` with `source_mode=descendants`; selected
  descendant and in-scope group badges are shown in the same modal but separated
  into visual blocks by type and level. On save they are assigned back to their nearest
  included ancestor and serialized into `subtract_ids_json`. A group under an
  included parent such as `Albacete (Provincia)` can therefore be selected as a
  level-3 exclusion; a group with no included ancestor remains an `Incluir`
  source. The `Excluidos` column shows only
  the badges currently selected as exclusions, never the full descendant
  candidate list.
  The editor-level `Importar TOML` action imports the matching
  `subdivision_groups/subdivisions/<country>.toml` bundle into SQL
  synchronously for AJAX requests and reloads the edited page when it succeeds.
  The `/groups/groups/` editor hydrates selected municipalities from SQL in the
  initial context and should not issue an initial browser POST just to resolve
  legacy selected names. Its hierarchy selector shows a root country row before
  the `Nivel 1` select; that row opens the same modal and includes the country
  itself as level 0 plus direct level-1 administrations from SQL. It also should not issue one
  child-list request per selected section while loading; fetch children only
  when opening the section modal.
  Existing group-key TOML can be expanded for display from SQL when possible,
  but new visual saves should prefer `AdminArea` IDs instead of group-key
  appends. In the base-fields box, `Capitales` spans the full row after the
  basic fields. Inside `Capitales`, the Select2 AJAX search/add control uses
  roughly 30% of the width and the same-height horizontal badge rail uses the
  remaining 70%. The Select2 source `<select>`
  must not carry generic `data-select2`, because this field has custom AJAX
  behavior. Selecting a result adds it as a colored removable badge and the
  frontend writes hidden `capitals` inputs; the form accepts no capital, one
  capital or several capitals and stores the TOML shape `capitals = [...]`. The
  badge remove control is a plain `x` without a circular button frame. The
  legacy single `capital` value remains only as a JSON/form compatibility alias.
  `/data/` hydrates only selected capital labels by ID and must not expand all
  possible source city IDs. The frontend must not POST to the capital-options
  endpoint on initial load or source-panel changes; the Select2 AJAX transport
  is the only search trigger. It POSTs the live hidden TOML, any dirty visual
  source JSON and typed capital prefix to
  `/groups/subdivisions/<country>/<slug>/capital-options/` once the user has
  typed at least two letters. That endpoint filters candidate municipalities by
  the normalized starts-with prefix first, then checks whether each candidate is
  inside the current include/subtract selection; do not expand every possible
  source city before applying the prefix. The select displays just the
  municipality name.
  Capital options and `/groups/source-data/`
  badge children use only base source rows plus unified city rows
  (`city_merge_status` `NONE` + `UNIFIED`); original rows consumed by a unified
  city (`SOURCE`) are excluded so derived territory population, area, capitals
  and `municipios_originales` do not double count them. It saves SQL
  `DerivedSubdivision` rows; the country-level build then recalculates terrain,
  population, density, capitals and source `municipios_originales` in
  `NuevoAdminArea`.

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
- `NuevoAdminArea.code` is hierarchical for every derived subdivision below the
  root. The declarative `build_derived_subdivisions` command joins child codes
  with `-` starting at the root code, so a first-level child under Spain root
  `ESP` is `ESP-ARA` and a child below it can be `ESP-ARA-ARA`. If TOML already
  stores the full parent-prefixed code, the builder does not duplicate the
  prefix. Derived subdivision root codes are normalized through
  `ciudades_del_mundo.services.derived_codes`; this preserves a known internal
  root such as Spain `ESP` even when the scraped `AdminArea` root code is the
  country slug `spain`.
- Duplicate `NuevoAdminArea.code` values within one `country_code` are rejected.
- Legal levels are usually domain-specific; do not assume level 3 always means
  municipality. Use SQL config `LEGAL_SUBDIVISION` and recipe
  `MUNICIPAL_LEVEL` or `ORIGINAL_MUNICIPAL_LEVEL`.

## City Merge Status

Constants in `domain/admin_area.py` and model choices:

- `0` / `NONE`: normal row
- `1` / `UNIFIED`: synthetic unified city row
- `2` / `SOURCE`: source row used to build a unified city

Most-populated calculations generally prefer/allow `NONE` and `UNIFIED`, and
ignore `SOURCE` rows. Builder lookups can accept status preferences using
aliases like `none`, `source`, `unified`, `fuente`, `unificada`.
Derived source expansion defaults to `NONE` + `UNIFIED`; only explicit source
preferences such as `prefer_city_merge_status = "source"` opt into `SOURCE`
instead of `UNIFIED`.

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
- `LEGAL_SUBDIVISION`: configured legal level used by some validation,
  representation or derived flows. Scraped `AdminArea` most-populated selection
  no longer targets this level; it uses the highest available descendant level
  in each branch.
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
parent, Portugal localities are imported at L4 and use parish parents only when
CityPopulation exposes `(... in: Parish)`; otherwise they keep the municipality
parent because the HTML has no safe parish value, Morocco urban places are
imported one level below communes but keep the province/prefecture as parent,
Tunisia combines `/admin` with `mun/admin` shifted one level deeper, and
Gibraltar keeps `/cities` as the level-0 seed while importing `/admin` one level
lower for enumeration areas.

Morocco v2 config detail: the intended hierarchy is
`Country > Region > Province/Prefecture > Commune > Urban place`. Regional
`cities` pages such as `/morocco/soussmassa/` expose the province/prefecture in
`major_subdivision` only as context for the urban-place rows. Keep
`include.major_subdivision = false`, `include.cities = true`,
`force_highest_level = 4` and `parent_level = 2` for those regional city pages;
otherwise the same province/prefecture code is persisted again as an L4 row and
SQLite fails on the unique `(country_code, code)` constraint.

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
  visible children; `SOURCE` is numeric `city_merge_status = 2`, while the
  created city is `UNIFIED` / `city_merge_status = 1`
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
   SQL persistence still receives the full cached+fresh entity set before
   `delete_missing` runs, so missing-row deletion never sees a partial country
   scrape. The save, missing-row deletion and representatives update are
   separate short write phases under the SQLite write lock; most-populated city
   is assigned in memory during step 2 and persisted inside `save_many`.
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
12. In a transaction, optionally reset country rows, save all incoming rows
    including precomputed most-populated links, delete missing rows, and assign
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

- saves rows level by level, bulk-creating missing rows and bulk-updating only
  rows whose persisted fields actually changed; unchanged rows keep their
  previous `updated_at`
- SQLite-safe batch size is 500; large existing-row lookups must also be split
  into batches, not sent as one huge `id__in` filter
- updates fields including hierarchy, area, density, population, URL and merge
  status
- `bulk_update` groups rows by the fields that actually changed and computes
  SQLite-safe batch sizes; do not return to updating every persisted column for
  every changed row, because large countries can otherwise look stuck after all
  `FOUND ... entities` logs
- most-populated-city assignment happens in step 2 on the in-memory
  `ScrapedAdminArea` list. It must stay indexed/bottom-up in
  `domain/most_populated.py`, use the highest available descendant level for
  each branch, ignore `SOURCE` rows, and never reintroduce per-area full subtree
  scans: Spain has roughly 37k rows.
- resets and `delete_missing` remove `AdminArea` rows through
  `infrastructure/django/admin_area_deletion.py`, which clears dependent
  `AdminArea`/`NuevoAdminArea` FKs and M2M through rows in SQL batches before
  raw-deleting the source rows
- `delete_missing` also removes `VisualAsset` rows and translations whose
  `entity_type="admin_area"` and `entity_key` is one of the deleted
  `AdminArea.id` values; it must not delete assets for unchanged rows
- `save_many` persists precomputed most-populated links after all levels exist
  so parent rows can point to newly inserted descendants; representatives still
  use `bulk_update` instead of per-row saves
- representative allocation is D'Hondt only

Normal populate commands do not run `clear_config_data` before scraping. They
save the full scraped set incrementally and then rely on `delete_missing` to
remove rows absent from the new scrape. Full pre-cleaning is only for explicit
maintenance actions such as the `/configs/` `Limpiar` button,
`scrape_subdivisions_with_assets --clear-first`, or a config that deliberately
sets `reset_before_import`. The `/configs/` `Re-popular` action follows the
normal incremental path and must not add `--clear-first`.

Be careful with `reset_before_import`, `delete_missing` and scraping runs. These
can modify or delete many `AdminArea` rows in `db.sqlite3`.

## Derived Hierarchy Recipes

The active declarative recipe files are TOML:

```text
ciudades_del_mundo/new_country_configs/*.toml
ciudades_del_mundo/subdivision_groups/groups/<country>.toml
ciudades_del_mundo/subdivision_groups/subdivisions/<country>.toml
```

They contain normalized metadata plus embedded legacy Python under `[legacy]`
where the current builder cannot yet express the logic declaratively. The
current build command still uses the matching legacy Python modules:

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
py manage.py scrape_subdivisions spain france --country-workers=2 --page-workers=4
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
behavior. Duplicate URL pages reuse the first fetched HTML and parsed entities;
page-specific area overrides still run per configured page.

`scrape_subdivisions` also accepts `--country-workers` (env:
`CIUDADES_SCRAPE_COUNTRY_WORKERS`, default `1`) to scrape multiple country
configs concurrently. This overlaps country-level HTML downloads/parsing while
serializing the final SQLite transaction through `DjangoUnitOfWork`'s optional
cross-process OS lock on `.web_sqlite_write.lock`; the file may remain after a
run and must not be treated as a held lock by itself. `repopulate_configs` and
`validate_and_scrape_configs` also accept `--country-workers`; the `/configs/`
bulk actions pass `--country-workers=2`. Keep `--seed-assets-from-pages` on
sequential runs; the command rejects combining asset seeding with
`--country-workers > 1` because Wikimedia asset persistence is intentionally
not parallelized.

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
When Wikidata has the entity QID but no image claim for a requested kind, use
the generic Commons search fallback by entity name and kind before adding a
country-specific override. Keep the batch path bounded: without explicit
`--subdivision-asset-levels`, `scrape_subdivisions_with_assets` only attempts
Commons search fallback for level 1; per-area maintenance can use
`ensure_visual_assets --admin-area <id>` for any level.
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
  population JSON, country display labels and country-detail level filtering.
- `test_web_interface.py`: web helper behavior plus SQL/TOML flows for
  `DerivedCountryConfig` and `SubdivisionGroup`.
- `test_visual_assets.py`: CityPopulation page-HTML visual asset seeding and
  persisted asset metadata, including Commons search fallback for missing
  Wikidata image claims.
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

- if the request is for the current legacy builder, change only the matching
  Python under `new_subdivisions/` or `historical_divisions/`; if a TOML seed
  must also change, update `new_country_configs/`,
  `subdivision_groups/groups/` or `subdivision_groups/subdivisions/` depending
  on whether it is a group or a subdivision bundle
- if the request is for the new declarative base, use `/new-countries/`,
  `DerivedCountryConfig.content` TOML, `/groups/` and `SubdivisionGroup.content`
  instead of adding another parallel ad-hoc store
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
  `/configs/` for SQL-backed scraping config editing, validate/populate tasks
  and the country cities subsection, `/new-countries/` for SQL/TOML
  derived-country containers and configs, `/groups/` for reusable TOML
  subdivision groups and derived subdivisions, `/countries/` for the
  API-driven country browser, `/stats/` as its compatibility redirect,
  `/delete/` for confirmed data deletion and `/tasks/` for
  in-memory task output/history; `/map/<source>/<id>/` shows a map and visual
  identity lookup for one `AdminArea` or `NuevoAdminArea`;
  `/identity/<kind>/entity/<entity_type>/<entity_key>/` shows the readable
  internal detail page with Wikimedia image URL plus stored translations, while
  `/identity/<kind>/<filename>/` remains a legacy resolver for Commons filenames
  and local `visual_assets/...` paths
- `/recipes/` and `/derived/` are legacy compatibility routes for the Python
  recipe builder and built `NuevoAdminArea` table. They are not primary
  top-level navigation entries now that `/new-countries/` and `/groups/` are
  the base for the new model.
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
  The country table level selector is rendered by `renderCountryTablePanel`
  in `.country-level-controls`, directly above the `Tabla de datos` heading,
  and only when more than one useful level option exists. If no `level` query
  parameter is selected, `/api/countries/<country_code>/` uses the first
  non-root level (NV1 for normal countries). Selector options are the translated
  entity-type names for levels whose total visible row count in that country is
  greater than 0 and no more than `COUNTRY_LEVEL_OPTION_MAX_ROWS` (500 by
  default). If a whole level is too large but contains entity-type groups with
  children and at most 500 rows, the API offers one partial option for those
  types (`filter_value` uses `level|EntityType|...`) and stops offering deeper
  levels. Examples expected by the UI are Spain level 1/2 and France level
  1/2/3, while massive commune/locality levels are not offered.
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
  directly. These normal populate paths are incremental and must not call
  `clear_config_data_with_assets` before scraping. `Populado` rows only show
  `Limpiar`. `Limpiar` is available whenever there are previous `AdminArea`
  rows and no operation is active. `Validando`,
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
  pagination controls render as compact numbered buttons (`< 1 2 3 ... >`) and
  click handlers must be bound only to those controls, never delegated broadly
  enough that ordinary table/button clicks can change pages.
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
  discovering useful CityPopulation links for the country, but normal
  `/configs/<slug>/editor-data/` hydration must not fill that Scrapping preview
  or it looks like a duplicated scraping configuration; keep the preview hidden
  in edit mode until the user runs the generator. The old visible
  `Escudos y banderas` correction subsection is replaced by a `Ciudades`
  subsection in Manual. It owns only the visual `[[cities]]` builder; do not
  reintroduce a legacy table that lists all SQL city/municipality rows. The
  builder is hydrated from `manual.city_builder` returned by
  `/configs/<slug>/editor-data/`: it reads `LEGAL_SUBDIVISION`, lists parent
  entities from level `LEGAL_SUBDIVISION - 1`, shows them through the same
  native searchable select widget used by `/groups/groups/...` (the hidden
  source `<select>` plus `.group-search-select`, not Select2), and opens that
  selector from the subsection's `Añadir` button. The widget uses normalized
  matching, so searches ignore accents and case (`tan` / `tán` both match names
  such as `Tanger-Assilah`). `Añadir` and table `Editar` open the same modal
  editor. The modal edits a temporary copy; the X/close controls discard it, and
  only `Guardar` writes the draft back to `state.sections`, updates
  `config_cities_json` and triggers autosave. Do not reintroduce inline
  new/edit blocks for this subsection. Existing table rows must not open the
  modal on row click; only their `Editar` button may do that. Each block
  requires a non-empty unified city name before saving. Lazy-loaded legal
  children from `/groups/source-data/` appear as badges, and the badges moved to
  the right are stored as `communes = [...]` in SQL TOML. The `Disponibles`
  badge panel is a single framed area with compact filters directly under its
  label: normalized name text and an enum of child subdivision types. The table lists configured unified cities, not
  raw SQL municipalities: `Ciudad unificada` and `Subdivisión mayor` are fixed
  at 15% each, `Acciones` keeps edit/remove on one row, and `Elementos` renders
  the selected entity labels instead of a numeric count.
  `[[cities]]` materialization uses `AdminArea.city_merge_status = 1` for the
  new unified city and `2` for source entities used to build it. The section
  stays hidden when the country has no usable legal parent/child data and
  refreshes after successful `Popular` or `Limpiar` task polling. The
  `/configs/<slug>/` page renders the editor shell
  first and hydrates its real data from `/configs/<slug>/editor-data/`; keep
  Select2 initialized before `initConfigEditor`, then keep `initConfigEditor`
  isolated from later `app.js` startup so unrelated JavaScript errors cannot
  leave the `Cargando datos...` overlay stuck. Scrapping's generated TOML
  textarea may be hydrated with current SQL content, but the preview panel
  should only become visible when the user opens Scrapping or runs the
  generator, otherwise it looks like a duplicated configuration block.
  IA is
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
  order; they do not pre-clean existing `AdminArea` rows. `TaskManager` uses a
  real backend queue for web-launched `manage.py` subprocesses. By default it
  runs one subprocess at a time to protect SQLite and low-resource machines; set
  `CIUDADES_WEB_MAX_RUNNING_TASKS=2` or `3` to allow limited parallelism. Extra
  tasks stay in `queued` until a running task finishes. The web bulk buttons
  (`Popular todo` / `Popular no populados`) pass `--country-workers=1`, so they
  also process countries one at a time inside their own queued subprocess unless
  a CLI caller explicitly chooses more parallelism. `TaskManager` persists
  task history/status in the `WebTask` database table, full per-task logs to
  `.web_task_logs/<task-key-or-country>/*.log` and task-progress sidecars to
  `.web_task_progress/*.json` in the repo root; these are covered by the
  `.web_*` gitignore rule and expire after 90 days. Interrupted scraping tasks may also leave technical
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
- Running `build_derived_subdivisions <country> --force` deletes and rebuilds
  non-root `NuevoAdminArea` rows whose `country_code` is that source country,
  based only on SQL `DerivedSubdivision.content`; TOML files are not read at
  build time.
- Generated Excel/CSV files can clutter `excels/`; avoid creating them unless
  the task requires verification.
- Web task history/status is persisted in the `WebTask` database table, but
  running subprocesses are still process-local and cannot continue
  after a development server restart. The task side effects in `db.sqlite3`,
  git-ignored `.web_scrape_resume/*.json` page checkpoints,
  git-ignored local `subdivisions/*.toml` seed/export files,
  git-ignored legacy backup folders `historical_divisions_old/` and
  `new_subdivisions_old/`, or `excels/` remain.
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
