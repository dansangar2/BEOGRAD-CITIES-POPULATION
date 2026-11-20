# Commands, tests and operational risks

Detailed repository reference. Start with `AGENTS.md` and read only the relevant section.

For a country configuration or repair, follow the
[general scraping workflow](scraping-workflow.md). It distinguishes diagnostics,
configuration validation, offline hierarchy checks, data repair and final API
verification; these steps have different database and network effects.

## Command effects

Commands below run from the repository root. Replace `<slug>` with the SQL
configuration slug. Network-free commands can still write SQL or local files;
they are not interchangeable with tests using isolated settings.

| Command | SQL effect | Local files | External network |
| --- | --- | --- | --- |
| `py manage.py prepare_ai_autoconfig <slug>` | Reads configuration; does not repair rows | Writes a dossier and runs old-log retention | No |
| `py manage.py validate_subdivision_configs <slug>` | Updates `is_valid` and `validation_error`; does not rebuild `AdminArea` | Writes progress when a task progress path is configured | No |
| `py manage.py scrape_subdivisions <slug> --list-pages` | Reads the active configuration; does not persist scraped rows | No plan file; prints to the console | No |
| `py manage.py sync_scraping_configs <slug> --force` | Imports the seed into `ScrapingConfig`; does not repair `AdminArea` | Reads the country's TOML | No |
| `py manage.py sync_scraping_configs <slug> --to-toml --force` | Reads configuration | Writes or overwrites the country's seed TOML | No |
| `py manage.py materialize_city_merges <slug>` | Reapplies configured cities, runtime extensions and most-populated-city links to existing data | May use the SQLite lock file | No |
| `py manage.py scrape_subdivisions_with_assets <slug>` | Scrapes and persists data, then resolves assets | Writes diagnostics/checkpoints; image downloads are opt-in | Yes |
| `py manage.py migrate` | Applies schema migrations to the configured database | May use SQLite sidecars | No |
| `py manage.py import_territorial_reference <file> --reviewed-by <reviewer>` | Imports a reviewed/versioned reference | Reads JSON | No |
| `py manage.py import_territorial_evidence <slug> <file>` | Imports frozen evidence and parser observations | Reads JSON containing HTML | No |
| `py manage.py scrape_subdivisions <slug> --strategy ai --evidence-id <id>` | Persists a proposal, comparison and diagnostics; does not apply areas | May use SQLite lock/sidecars | No with default local engine; explicit provider uses network |
| `py manage.py scrape_subdivisions <slug> --strategy ai --discover` | Persists discovered evidence and proposal | May use SQLite lock/sidecars | Yes, even with local inference |
| `py manage.py scrape_subdivisions <slug> --strategy ai --territorial-run <id>` | Reads the stored review | Console output | No |
| Same review command plus `--plan-hash <hash>` | Applies the reviewed plan transactionally after checking inputs | SQLite lock/sidecars | No |
| `py -B tools/verify_territorial_scraping.py` | Temporary disposable SQLite only | Temporary acceptance files | Blocked; local symbolic engine |
| `py -B tools/verify_territorial_scraping.py --engine provider` | Temporary disposable SQLite only | Temporary acceptance files | Blocked; simulated provider transport |

Territorial proposal and application are separate actions. The traditional
strategy remains the default; the alternative uses local symbolic inference
unless `--ai-engine provider` is explicitly selected. See
[inputs, hashes and limits](territorial-reconciliation.md).

For local HTTP startup, use [the backend guide](python-http-retirement.md).
Java `build` runs Maven only; Django and frontend checks remain separate.
`tools/inventory_java_api.py` reads the archived Python snapshot and generates
historical evidence, not the current route ownership matrix.

`prepare_ai_autoconfig` records `toml_source`. Its diagnostic reader can include
a local seed when the SQL row is unavailable; that reference neither imports
the seed nor changes the SQL-only runtime loader. Dossiers and old-log retention
are described in the [scraping workflow](scraping-workflow.md#2-diagnosticar-la-tarea-y-su-configuracion-efectiva).

Configuration validation checks the loadable schema and runtime extensions;
listing pages checks URL/level expansion. Neither executes the HTML parser or
proves final hierarchy semantics. Use saved HTML with network blocked for
parser/linker checks, and a fresh SQL read plus the detail API after an
authorized existing-data repair. Importing a seed alone does not apply that
repair. `materialize_city_merges` operates on `[[cities]]` and existing source
rows; it does not replay all page blocks or fetch new population data.

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
behavior. Within one block, duplicate URLs reuse fetched HTML and parsed
entities; page-specific area overrides still run per configured page. Put
uses of one URL with different root/parser/level controls in separate
`[[pages]]` blocks and verify the prefetch path, not just individual parsers.

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
pages. It also checks `SCRAPE_RESUME_VERSION` in
[`services/scrape_resume.py`](../../ciudades_del_mundo/services/scrape_resume.py).
Version 2 rejects parsed checkpoints created before hectare normalization,
even with the same configuration hash; those pages are fetched and parsed
again. Bump this version when incompatible parsed-entity semantics change.
This invalidation does not repair previously persisted SQL rows. Keep all
root `.web_*` local web artifacts git-ignored.

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
- `test_scraping_area_units.py`: hectare/km2 conversion, machine/hidden area
  precedence, footer/admin0 totals, infosections, density, isolated SQL
  persistence, repeated overseas rollups and rejection of pre-fix checkpoints.
- `test_application_pipeline.py`: scraping use case, dedupe, area overrides,
  persistence calls.
- `test_configured_cities.py`: city aggregation and source row shifting.
- `test_configured_city_parent_scopes.py`: known parent chains take precedence
  over accidental code prefixes, with and without `keep_communes`.
- `test_configured_city_materializer.py`: reapplication to existing SQL cities,
  runtime extensions, most-populated links and repeatability without scraping.
- `test_source_code_identity.py` and `test_partial_settlement_linking.py`:
  distinct population rows, shared QIDs, code collisions and page identity controls.
- `test_runtime_entity_exclusions.py` and `test_parent_override_patterns.py`:
  SQL-scoped exact exclusions, full-code patterns, templates and validation.
- `test_composite_city_selections.py`: partial-city arithmetic, group source IDs,
  selectors and generated city children across repeated SQL builds.
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
- `test_country_browser_loading.py`: country/area loading by branch, retained
  dashboard share details, bounded summary queries, canonical root priority,
  semantic counts and batched SQL assets/reviewed translations.
- `test_web_modularity.py`: ordered bundles, compatibility owners and matching
  template asset versions.
- `tests/frontend/behavior.test.cjs`: shipped bundle behavior with DOM/time/HTTP
  doubles, including immediate cached country summaries, background refresh,
  unchanged card preservation and no-cache loading behavior.
- `tests/frontend/group_selection.test.cjs`: the actual inline group-editor
  script; saved add/subtract operations survive unopened sections, lazy child
  loading and modal cancellation. Run this harness separately from the bundle.
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
- For shared behavior, use the isolated full-suite command below.

## Offline verification scope

Use the installed project interpreter from the repository root. Set
`$env:PYTHONDONTWRITEBYTECODE='1'` to avoid incidental tracked bytecode changes.

```powershell
py -B manage.py check --settings=ciudades_del_mundo.tests.isolated_settings
py -B manage.py test ciudades_del_mundo.tests --settings=ciudades_del_mundo.tests.isolated_settings --noinput
py -B -m tools.build_web_assets --check
node --test tests/frontend/behavior.test.cjs
node --test tests/frontend/group_selection.test.cjs
py -B -m tools.verify_clean_copy --python .venv/Scripts/python.exe
```

`isolated_settings` points Django at SQLite in memory. Its `OfflineDiscoverRunner`
blocks socket `connect` and `connect_ex` while tests run. Merely selecting these
settings for `check` or another management command does not install that socket
guard. Test HTTP/AI providers with doubles; production commands keep their own
database and network effects as described above.

`tools.verify_clean_copy` uses a temporary source copy, the selected interpreter's
already-installed dependencies and isolated Django settings. It runs `pip check`,
bundle verification, Django `check`, nine selected Django test modules and
`tests/frontend/behavior.test.cjs`, then checks that no `db.sqlite3` was created
in the copy. It neither creates a virtual environment nor installs packages.
Python dependencies must already be installed from `requirements.txt`, and Node
20+ must be available. See the script's `commands` list for the exact test modules.
The full Django suite, composite-city tests and `group_selection.test.cjs` require
separate runs; passing the clean-copy command does not establish their results.

For documentation-only changes, check local links, anchors and command references
against their source files. Code tests need not be rerun. Counts and failures in
[the refactor validation record](refactor-validation.md) and the dated country
browser measurements below describe previous runs, not the current checkout's
test status. The hectare repair record below is historical too. Report new
checks with their actual scope and result.

## Hectare repair record (2026-10-22)

This records the implementation session before its documentation update; it
does not claim that tests or SQL checks were rerun while editing documentation.
The generic [unit conversion](scraping.md#area-units-and-territorial-scope)
was fixed in the parser without country-specific TOML overrides or runtime
dependencies on repair scripts.

The authorized offline SQL repair committed 59 rows under the existing SQLite
write lock and a transaction: seven national roots, 30 first-level rows,
21 Mayotte rows at levels 1 through 4, and France's root. It recalculated
densities while preserving IDs, hierarchy, population and other scalar fields
apart from `updated_at`. Malta's subdivisions and Mayotte's 71 locality rows
were already converted and were left unchanged. No materialized derived areas
referenced the repaired source IDs. A local temporary JSON backup preserved
the original rows and planned areas; it is not a runtime input.

National areas verified after commit, using the measurements already imported:

| Country / territory | Corrected area (km2) |
| --- | ---: |
| American Samoa | 198.02 |
| Antigua and Barbuda | 441.60 |
| Bahrain | 780.03 |
| France, including its five overseas departments | 633109.24 |
| Malta | 315.15 |
| Monaco | 2.03 |
| San Marino | 61.19 |
| Vatican City | 0.44 |

Historical verification in that implementation session:

- The nine new area-unit tests passed. A regression selection of 98 tests
  passed across `test_scraping_area_units`, `test_admin0_root`,
  `test_application_pipeline`, `test_architecture_boundaries`,
  `test_admin_area_repository`, `test_scraping_config`, `test_serbia_config`
  and `test_country_browser_loading`, using isolated settings.
- A separate selection of 100 tests reported four unrelated errors: missing
  `html/france/cities/mayotte.html`, `reunion.html` and `guadelupe.html`
  fixtures, plus the resume asset-phase test calling the absent
  `ScrapeResumeStore.data_populated` method. These are historical observations,
  not a current-checkout failure inventory.
- Django `check` and scoped `git diff --check` passed. Read-only post-commit
  SQL and all eight country-detail API handlers confirmed the national areas.
  Mayotte retained counts `1 > 1 > 2 > 17 > 71`, with 374.24 km2 at each of
  levels 1 through 4; the locality-area sum remained 364.75 km2. Canada, USA,
  Netherlands and Serbia root areas were unchanged. No live scraping or paid
  AI calls were used.

## Country browser performance checks

Use the focused offline commands in the
[README country browser description](../../README.md#interfaz-web-local).
The Django settings use SQLite in memory and reject external socket connections;
frontend behavior tests need Node 20+ and no npm install. A documentation-only
change does not require running the application or those code tests again.

For a running development server, measure `/countries/`, `/api/countries/`,
country detail and area detail separately. Record response duration and payload
size, and use the browser Network panel to check which endpoints are requested.
The initial page must not request every country's detail or descendants.
On drill-down, distinguish response time, DOM rendering and remote image loads.
Do not attribute an intermittent delay to CSS or a particular SQL query without
measuring that stage. Inspect query counts/plans with a read-only connection or
an isolated database; do not scrape, enrich with paid AI or rebuild real data as
a performance check.

Check a cold browser session and a reload with a saved summary separately.
Artificially delay the summary API: cached cards must remain usable before the
response completes, then fresh SQL changes must replace them. An unchanged
refresh must preserve the same card/image DOM nodes. Also check a failed
refresh, empty summary, missing cache, and countries with different first levels
or several child-level panels. Test desktop and narrow widths, short and long
content, theme effects on/off and reduced motion; fixed decoration must neither
catch pointer events nor add horizontal scrolling.

Validation recorded on 2026-10-15:

- A live summary response took 24.6 seconds, followed by a response around
  0.4 seconds. The outlier was reproduced but not traced to one SQL statement;
  query batching does not establish that all future server/lock/I/O delays are
  eliminated.
- The 22-country summary profile fell from 114 to 9 SQL statements, including
  three SQLite connection-initialization PRAGMAs. The isolated regression checks
  six data queries on an initialized connection, unchanged after adding 30
  countries. The complete 22-country response matched the previous JSON,
  including totals, IDs, image payloads and translations.
- In Chrome, cached cards appeared at about 38ms after navigation while the
  summary request was deliberately held for five seconds. The later identical
  response kept the existing card nodes. A cold session still waits for SQL;
  this result is not a cold-load or database latency guarantee.
- Local two-second scroll samples in headless Chrome with GPU disabled improved
  from roughly 14 to 54 FPS for Retro with complex effects, and roughly 7.5 to
  60 FPS for Rainbow. These are diagnostic samples on one machine, not promised
  frame rates on every browser/device. Four viewport widths (1424, 1008, 784
  and 500 CSS pixels) passed synthetic layout/background checks. CDP checks also
  verified reduced-motion behavior and live Austria-to-Tirol branch requests.
- The focused Django run passed 33 tests; the frontend run passed 15. The wider
  33-test visual asset suite still had four failures and two errors, reproduced
  with the original single-entity asset reader: SPARQL/seeding expectations,
  Windows path separators, a missing timestamp fixture and an unmocked network
  fallback blocked by the offline runner. An earlier wider web-helper run also
  reported unrelated populated-status and source-text expectations. These runs
  must not be reported as a passing full project suite.

## Local Development Notes

- Python 3.13+ is the documented project environment; `tomllib` is required.
- `requirements.txt` pins Django, requests and BeautifulSoup. Install with the
  project interpreter using `-m pip install -r requirements.txt`; see
  [local setup](../../README.md#desarrollo-local).
- `lxml` is optional; the parser falls back to `html.parser`. The XLSX writer
  uses the standard library and does not require `openpyxl`.
- Use `requirements.txt` for the runtime Django version, not the generated
  settings-file header.
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
  rows for the requested country. If the requested `country_id` has no legacy
  Python recipe, it may resolve and build the matching SQL `DerivedCountryConfig`
  instead.
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
