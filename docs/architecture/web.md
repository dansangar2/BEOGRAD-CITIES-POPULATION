# Web workflows and interface contracts

Detailed repository reference. Start with `AGENTS.md` and read only the relevant section.


The current HTTP boundary is `web/handlers/` plus `web/http.py`. Queries take
data, return data and raise `ports.query_errors.DataNotFoundError`; handlers
translate missing data to 404. `web/views.py` exports public HTTP callbacks only:
internal imports and patches target owners, with no attribute forwarding.
SQL mutations are composed in handlers through the mutation services, which do
not import web. See [the final refactor guide](../refactorizacion.md) for commands.

## Active HTTP ownership

Python mode serves the Django routes below. In Java mode Django retains the UI,
CSRF, locale, tasks and pending endpoints; `web/java_routing.py` forwards only
`JAVA_ROUTE_NAMES` to native Java. A Java error never retries with a Python owner.
See [backend operation](python-http-retirement.md). Territorial reconciliation
is available through CLI strategies; its HTTP flow remains pending.

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

- views: `ciudades_del_mundo/web/handlers/`; legacy imports use `web/views.py`
- forms/SQL payloads: `ciudades_del_mundo/web/forms/` and `web/queries/`
- routes: `ciudades_del_mundo/web/urls.py`
- background task registry: `ciudades_del_mundo/web/tasks.py`
- database-busy middleware: `ciudades_del_mundo/web/middleware.py`
- templates: `ciudades_del_mundo/templates/ciudades_del_mundo/`
- CSS/JS sources: `ciudades_del_mundo/static/ciudades_del_mundo/src/`; bundles
  `app.css` and `app.js` are built by `tools/build_web_assets.py`
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
- `/groups/` and `/subdivisions/` country-card panels must lazy-load their
  country tables only after the user opens a country card. `/groups/` uses
  `/groups/countries/<country>/data/`; `/subdivisions/` uses
  `/subdivisions/countries/<country>/data/`. The initial page HTML should
  include cards, action shells and empty table bodies, not all row data. The
  `/groups/` right-hand `Nuevas divisiones` table is a list of SQL
  `DerivedSubdivision` definitions for the source country; do not populate it
  from scraped `AdminArea` municipalities/localities or from materialized
  `NuevoAdminArea` child rows. Level filters in that panel come from those
  definitions, not from built hierarchy rows.
- Dashboard/statistical country bars and country selectors display names from
  root database rows (`AdminArea.level=0` and root `NuevoAdminArea`) while
  keeping `country_code` as the submitted/filter value. Display labels are
  passed through Django `gettext`, so country/subdivision names that need
  localization (for example `Brazil` -> `Brasil` in Spanish) must be present in
  the `locale/*/LC_MESSAGES/django.po` catalogs and compiled.
- Dashboard/statistical data loads dynamically with visible loading spinners.
  Current web data should be consumed through the local API endpoints:
  `/api/countries/`, `/api/countries/<country_code>/`,
  `/api/new-country-containers/`, `/api/new-country-containers/<country_slug>/`
  and `/api/derived/`.
  The older `/dashboard/population/`, `/dashboard/derived/` and
  `/dashboard/country/<country_code>/` JSON endpoints remain for compatibility.
  `/api/countries/` returns root-country population and area donut data and uses
  only root `AdminArea.level=0` rows. Canonical selection prefers a row whose
  code equals the country code, then an infosection root, then the national QID,
  then a sole remaining root. Only ambiguous roots without a canonical match
  are summed into one synthetic country entry; child subdivisions are not added
  to a canonical country's totals. `/api/derived/` returns
  the derived-country dashboard bar data. `/api/countries/<country_code>/`
  returns the clicked-country dashboard
  detail: general country data, one-level subdivision table rows, first-order
  population/area comparison rows and first-order share-card data. Country table
  rows include `detail_url`, `level`, parent id/level and `parent_detail_url`;
  when a row's parent is more than one level above it, the frontend opens the
  parent detail panel first and then the clicked row so both recuadros are
  visible in `/countries/`. The detail
  table is API-paginated, has a client-selectable page size, marks the active
  sort column with arrows, includes population/area percentages relative to the
  country, and changing its dashboard level selector reloads only that table panel.
  The country table level selector is rendered by `renderCountryTablePanel`
  in `.country-level-controls`, directly above the `Tabla de datos` heading,
    and only when more than one useful level option exists. If no `level` query
    parameter is selected, `/api/countries/<country_code>/` uses the first
    non-root level (NV1 for normal countries). Selector options are the translated
    entity-type names for every level whose total visible row count in that
    country is greater than 0; massive commune/locality levels remain selectable
    and are handled by API pagination rather than hidden. The shared country
    browser defaults to 20 rows per page and caps requested `page_size` at 100.
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
  The default dashboard contract's first-order share cards show each area's direct children at the next level with
  mini pie percentages when the country has fewer than 150 rows at that next
  level; do not filter those child rows by entity-type name.
  `/countries/` is an API-driven country card browser with an adaptive
  desktop grid, a fixed square flag slot, area and population per
  country; clicking a card loads the
  basic country panel plus a direct-child subdivision panel from
  `/api/countries/<country_code>/?include_children=0`. Each direct subdivision row can open another
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
  sourced in `ciudades_del_mundo/static/ciudades_del_mundo/src/js/charts.js`
  and assembled into `app.js`
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
  variables where possible. Theme hardening belongs in shared CSS: Select2,
  custom search selects, modal dialogs, image previews and task toasts must
  inherit `--surface`, `--ink`, `--muted`, `--line` and `--accent`; decorative
  complex-theme overlays must stay below interactive overlays so modal details
  and previews render consistently in every style.
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
  Incremental file offsets count consumed UTF-8 bytes, with blocks capped at
  64 KiB. Incomplete trailing characters remain for the next request; truncated
  logs reset when the cursor exceeds the current size. Missing/unreadable files
  retain the DB-tail fallback and its historical character offsets. The additive
  `output_has_more` flag keeps polling until terminal task logs are drained.
  Existing status, delta, offset and reset fields keep their meanings.
  The final eligibility check, subprocess spawn and handle registration share
  the manager lock with cancellation/replacement. A cancelling child retains its
  concurrency slot until `wait()` completes, including replacement by the same
  key. Log streaming still avoids per-line SQL writes to protect SQLite.
  `/tasks/` loads its task table dynamically from `/tasks/table/` with a visible
  spinner and client-side pagination/sorting. Dynamic task-table rows are
  clickable through `data-row-href`; do not add a separate `Abrir` action button
  in those tables. Active tasks loaded after a server restart are marked
  cancelled/interrupted because their subprocess cannot be reattached. Local
  tasks can continue after the browser closes, but cannot continue when
  the PC or Django process is powered off; use an always-on machine or external
  worker if true offline/background execution is required.
- Any web control that creates or starts a `WebTask` should follow the
  `/configs/` pattern: template form marked `data-config-task-form`, submit
  button marked `data-config-action`, AJAX request asking for JSON, and a view
  response with `task_id`, `status_url`, `detail_url`, `status` and `label`.
  Keep direct POSTs as non-AJAX fallbacks, but do not use `data-loading-submit`
  for new task-start forms. This applies to imports, builds and exports from
  `/new-countries/`, `/groups/`, `/subdivisions/`, `/recipes/` and
  `/derived/<id>/`. `/subdivisions/import-toml/` still supports the editor's
  immediate AJAX import when `queue_task` is absent; list pages send
  `queue_task=1` so the TOML import is enqueued and the current page stays put.
- country dashboard/API chart and table data should use
  `_visible_admin_areas()` so rows with `AdminArea.city_merge_status == 3` stay
  hidden from `/api/countries/`, `/api/countries/<country_code>/`, `/stats/`
  data and related dashboard counts.
- Always translate every new user-facing text before considering the change done.
  Wrap static template text with `{% trans %}` / `{% blocktrans %}` or Python
  text with `gettext`, update the relevant `locale/*/LC_MESSAGES/django.po`
  entries, then run `py manage.py compile_local_messages` so Django can load the
  `.mo` catalogs.

## Country UI hierarchy detail

`/countries/` area detail panels group direct child rows by scraped level. This
keeps mixed-parent cases readable, such as a province that has both L3 communes
and L4 urban places attached directly because CityPopulation links the L4 table
to the province instead of to each commune.

The country level/page controls are siblings before the detail grid, not a
full-width grid item between the general panel and the child panel. This keeps
both panels on the same row on desktop, including countries with several useful
levels. Split child groups stack in the second column. At widths up to 920px the
shared country/new-country renderer uses one column. The layout depends on the
payload and available levels, not on a hard-coded country level such as NV1/NV2.

### Lazy branches and summary queries

- The initial `/countries/` shell requests `/api/countries/` for country
  metadata, totals and SQL-registered assets. It does not fetch country detail
  or area-detail endpoints for every card.
- `loadStatsCountryDetail` requests `include_children=0`, including level and
  page changes. `_country_detail_payload` returns only the selected level/page
  and leaves each first-order card's `children` list empty. Omission of this
  parameter preserves the dashboard's existing optional share-card children;
  do not remove that default response contract to optimize `/countries/`.
- Clicking an area requests `/api/admin-areas/<area_id>/` and returns that
  area's direct browser children. The next branch is fetched on its own click.
  Follow persisted parents and semantic linking rules; do not flatten a country
  hierarchy or skip a territorial layer to make navigation appear faster.
- Country detail summaries are scoped to the selected country. Child counts
  use only identity/name/level metadata needed for the existing same-name
  collapse rules and are reused within the response. Computing a count must
  not eagerly serialize grandchildren or change the established counts.
- `_admin_country_summary_records` batches country codes, root candidates and
  reviewed names. `_canonical_country_from_candidates` preserves canonical
  root priority and synthetic totals. `dynamic_country_names` preserves language,
  review and country scope without one translation query per country.
- Summary assets use `get_stored_visual_assets_for_entities` through
  `_stored_visual_assets_payloads`: one readiness check, one scoped asset query
  and one translation query when assets exist. `_visual_assets_from_rows` is
  shared with the single-entity reader to preserve image selection and payload
  fields. The batch path has no TOML, folder or external discovery fallback.

### Cached summary and external dependencies

`initStatsCountries` reads the existing summary from `sessionStorage` for the
same API URL and renders it immediately, then requests fresh SQL data on every
initialization. With no usable cache, it shows the shared loading spinner.
If the fresh payload changes, it updates the stored summary and cards; if it is
identical, it keeps the current DOM and image nodes. If refresh fails, an already
visible cached summary remains; without cache, the normal error state appears.
Cached values can therefore be older than SQL until refresh succeeds. This is
a per-tab browser cache of a successful response, not an authoritative data
store or a seed fallback, and it does not preload descendant branches.

`base.html` exposes `vendor_styles` and `vendor_scripts` blocks. `stats.html`
leaves both empty because this browser does not use jQuery, Select2 or Leaflet.
Other views retain their existing dependencies and initialization order. Do not
make the country summary wait for unused external CDN libraries.

### Theme rendering and motion

Theme backgrounds for Retro, Rainbow, Paper, Spain and legacy 8bits live in a
fixed `body::before` layer within an isolated body stacking context. The body
retains viewport minimum height, so short and long pages have continuous
background coverage. Decorative layers must not intercept clicks or create
horizontal overflow.

Complex background motion animates `transform` on that layer instead of
`background-position` across the scrolling document. Retro scan lines use a
separate transparent transform animation without full-screen blend mode.
Panel shadows and card backgrounds remain static; the sticky topbar has no
backdrop blur. Preserve these constraints instead of restoring per-panel shadow
animations or repainting gradients every frame. `prefers-reduced-motion: reduce`
stops both theme pseudo-element animations while retaining the selected theme.
Edit ordered CSS/JS sources, rebuild bundles and bump both template asset
versions as specified in `AGENTS.md`.

For reproducible checks and the limits of the local performance measurements,
see [country browser performance checks](operations.md#country-browser-performance-checks).
