# Data models and persistence

Detailed repository reference. Start with `AGENTS.md` and read only the relevant section.

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

- SQL base for TOML-driven derived countries
- `DerivedCountry.slug` groups variants for one conceptual country, such as
  Spain with several historical/political configurations
- `DerivedCountryConfig.content` stores TOML with `kind =
  "derived_country_config"`, `derived_country_code`, group references and SQL
  source-selection fields. The visual `Nueva entidad` flow stores selected
  `AdminArea.id` values under `[selection] include_ids` / `subtract_ids` plus
  `[[selection.items]]` rows with `operation = "add"` or `"subtract"`. It also
  writes a first `[[entities]]` block. With `Usar subdivisiones` off, the form
  shows a dynamically loaded created-entity table from
  `/new-countries/<source>/<country>/configs/<code-or-new>/created-entities/`;
  selected same-country `DerivedCountryConfig` rows become direct children of
  the config being saved. Saving rewrites those children' `parent_config_slug`
  and recalculates their `level` plus descendant levels in cascade. A new
  top-level container with selected child configs stays level 1; the selected
  children become level 2. The visual `Nueva entidad` form must not show a
  `Nivel` field; level is derived automatically from the hierarchy and written
  only into generated TOML. Saving a `DerivedCountryConfig` calculates and
  persists `area_km2`, `pop_latest` and `density` in the first `[[entities]]`
  block. The created-entity selector must display terrain, population and
  density from the materialized `NuevoAdminArea` row when available, or from
  those stored TOML metrics before the new country is built; its visible
  columns include compact checkbox selection, name, generated level, type,
  terrain, population and density. It must not recalculate source selections or
  child sums while listing rows. New-country
  metric display text uses the shared number formatter: no thousands grouping
  and comma decimals. With `Usar subdivisiones` on, the source tree is replaced
  by saved `DerivedSubdivision` rows from `/subdivisions/`, including rows whose `source_country_code`
  belongs to another source country, stored as `derived_subdivisions` with
  `use_selected_entities_as_children = true`. When
  those selections span countries, emit one `[[entities.include]]` block per
  `country_code` so the TOML keeps each created subdivision's source context.
  The created-subdivision selector is loaded from the JSON endpoint
  `/new-countries/<source>/<country>/configs/new/created-subdivisions/` and is
  rendered as a separated searchable/paginated table, not a plain checkbox list
  or a preloaded HTML dataset. It also exposes a source-country filter populated
  from countries that have created `DerivedSubdivision` rows, because created
  subdivisions from any country can be selected. The country filter uses the
  shared Select2 initializer and must stay searchable with normalized
  accent-insensitive and case-insensitive matching. The real-source subdivision
  tree is not shown on this form. Its visible columns are the
  compact checkbox selection, name, source country, type without legacy level
  suffixes, terrain, population and density using materialized `NuevoAdminArea`
  metrics when present and TOML preview metrics otherwise. Its toolbar keeps a
  single row spanning the same 100% table width as the rows below it: the search
  field takes the remaining space, the source-country filter is wide enough for
  country names, the rows selector stays compact and pagination keeps the right
  slot. The toolbar pagination slot remains visible even when there is only one
  page.
  During selection, the created-subdivision table's visible `Nombre` column may
  show the original stored subdivision name including parenthetical text. Once a
  created subdivision is assigned and rendered inside the new-country hierarchy,
  its visible `Nombre` column should strip parenthetical suffixes from stored
  names while keeping the original text searchable. Each `[[entities.include]]` block that
  references `derived_subdivisions = [...]` must persist the level assigned to
  those children inside the new-country hierarchy, normally the parent entity
  level plus one; do not leave that level as a display-only calculation.
  `Capitales` on this form uses the same searchable badge picker as
  `/groups/subdivisions/`: selected `AdminArea.id` values are posted as repeated
  `capitals` inputs and rendered into TOML `capitals = [...]`. The
  new-country capital-options endpoint searches the source country's capital
  candidates and, when created subdivisions or created child entities are
  selected, constrains options to those selections' resolved source IDs even
  when they belong to other source countries. The frontend capital AJAX payload
  must include current `selected_child_config_slugs` from the checked created
  entity rows without reinitializing the Select2 widget on each row toggle. The
  edit route `/new-countries/<source>/<country>/configs/<config>/capital-options/`
  may infer saved direct child configs from `<config>` when the payload omits
  child slugs, so existing configs such as `por` can still search capitals from
  their stored children.
  Capital search must match the displayed/localized name and
  slash/parenthetical aliases, not only raw `AdminArea.name`; for example a row
  stored as `Donostia` must be found when
  the UI displays or the user types
  `San Sebastián`. With `Usar subdivisiones` enabled and no created
  subdivision checked, the endpoint must return no capital options rather than
  falling back to the whole source country.
  `/new-countries/<source>/<country>/configs/<code>/` uses the same visual form
  as the create route; editing hydrates it from SQL TOML and saving re-renders
  TOML from the visual fields. If `Codigo` changes, update same-country child
  configs that reference the old value through `parent_config_slug`. Configs do
  not have a separate `view/` route; their only own page is the edit form.
  `/new-countries/` cards must represent one `DerivedCountry` container, not
  one config row, so values such as `ESP_PERFECTA` do not appear duplicated.
  The container detail table at
  `/new-countries/<source_country_code>/<country_slug>/` is not an action-button
  table: render it as a collapsible hierarchy by config levels (`NV 1` rows with
  `NV 2+` children), make the row click open the config edit page, remove
  `Crear`, `Ver`, `Excel` and `Configuracion` from that table. The remaining
  config-row actions are POST-only `Clonar` and `Borrar`; cloning duplicates the
  SQL `DerivedCountryConfig`, keeps selections/capitals, changes the visible
  local code to `COD-COPIA` and uses a route-safe technical slug such as
  `cod_copia`. Deleting a config must not delete its SQL child configs; direct
  children are reassigned to the deleted config's parent and their levels plus
  hierarchy-derived visible codes, including descendants, are recalculated in
  cascade. Source `AdminArea` selections and selected `DerivedSubdivision` rows
  assigned to a config are displayed as read-only child rows with their assigned
  `NV` and a clean, color-differentiated local code only; do
  not show technical IDs such as `derived-subdivision::<pais>::<codigo>`,
  parent codes or visible source levels on those assigned rows. The table must
  fit the panel width without horizontal
  scrolling; use `Terreno`, `Poblacion` and `Densidad` as metric columns, never
  a visible `km^2` header. The `Acciones` column must stay wide enough for
  `Clonar` and `Borrar` on the same row. Config metrics come from the materialized
  `NuevoAdminArea` entity or the stored first-entity TOML metrics. The container
  detail header has one page-level `Exportar Excel` action at
  `/new-countries/<source_country_code>/<country_slug>/export-excel/`; when a
  materialized `NuevoAdminArea` root exists, it exports the complete built tree
  instead of only visible config rows. The `Jerarquia` sheet is a wide hierarchy
  path table, not a flat entity list: the first block is the new country with
  `Nombre`, `Poblacion`, `%`, `Ranking poblacion`, `Terreno`, `%`, `Ranking
  terreno`, `Densidad`, `Capital`, `Poblacion capital`, `% capital`, `Ranking
  capital`, `Ciudad mas poblada`, `Poblacion ciudad mas poblada`, `% ciudad mas
  poblada`, `Ranking ciudad mas poblada` and `Num municipios`; following blocks
  repeat those columns for `NV1`, `NV2` and deeper levels. Each row represents
  one branch path; repeated country/parent blocks across consecutive child paths
  are vertically merged cell-by-cell so names and metrics are not repeated. All
  children of one parent are grouped before the next sibling. `Num municipios`
  comes from accumulated `municipios_originales`, rolling child source units up
  to parents; the fallback export resolves most-populated city from those source
  units when no materialized relation is available. When fallback rows mix
  several original countries, legal source units must use each source country's
  own effective municipal/legal level from SQL `LEGAL_SUBDIVISION` or
  `ORIGINAL_MUNICIPAL_LEVEL`; level `0` is valid and a selected lower-level
  source row must roll up to its level-0 root instead of counting level-1
  children. Only fall back to the
  config/assigned-row export when no built root exists. The XLSX contains only
  the `Jerarquia` data sheet, centers cells and enables inferred column widths;
  keep summary/audit data in the JSONL log, not in an extra workbook sheet. The
  action writes the XLSX copy to
  `excels/new-countries/<source_country_code>/`, and appends an audit JSONL line under
  `.web_export_logs/new-countries/<source_country_code>/<country_slug>.jsonl`
  with row, level and legal-subdivision counts.
  Do not reintroduce per-row Excel actions in this table.
- `/new-countries/` keeps a two-step browser: first render populated source
  country cards with the same visual card format as `/countries/`; clicking one
  opens a second panel listing that source's unique `DerivedCountry` containers.
  That second panel's container grid must occupy the full panel width, like the
  initial source-country grid. When a container is selected, the embedded detail
  places the level selector above the detail grid, then uses the same desktop
  two-column `stats-country-detail-grid` shape used by drill-down levels:
  country data on the left and the level-1 subdivisions panel on the right. The
  level-1 subdivisions panel must be a direct grid panel, not a separate
  side-stack, so it keeps the same proportions and stretched sizing as the
  boxes opened for level 2 and deeper drill-down levels.
  Do not flatten the page into one global container grid, and do not render one
  card per individual `DerivedCountryConfig`. Container cards show flag when a
  built root has one, population and terrain from the built root when
  available, otherwise the sum of the top-level config metrics. Clicking a
  container must use the shared country-browser detail renderer through
  `/api/new-country-containers/<country_slug>/`; that endpoint delegates to the
  built `NuevoAdminArea` payload when a root exists and otherwise returns a
  config-tree payload with the same `country`, `levels`, `table.rows` and
  `first_order` shape used by `/countries/`. For unbuilt countries, include
  child configs, assigned source rows and assigned `DerivedSubdivision` rows in
  that navigable tree; selected created subdivisions must expand to their
  source municipal units as child rows. For unbuilt containers, the `Nivel`
  selector must be computed from a cheap per-level summary of config rows plus
  assigned source/created-subdivision rows; do not materialize every source row
  just to decide whether a level is selectable. A requested level that is not in
  the allowed summary, such as an oversized municipality/source-unit level, must
  resolve to an allowed level before rows are expanded. The unbuilt container
  info panel should show the first available configured capital, falling back
  from top-level configs to child configs when needed, and `Numero de
  subdivisiones` should count legal source units when selections expose them,
  falling back to config count only when no legal units are available. When materialized
  `municipios_originales` are present, project those source IDs back to each
  original country's effective legal level before using them, because old
  materializations may contain deeper rows such as Gibraltar enumeration areas;
  use TOML expansion as a fallback. Any
  card or table row with children should expose a detail URL until the entity is
  a leaf. Built subdivision drill-down uses `/api/new-areas/<area_id>/`;
  unbuilt config-tree drill-down uses
  `/api/new-country-containers/<country_slug>/config-nodes/<tree_id>/`, and
  assigned source `AdminArea` rows with children drill into
  `/api/admin-areas/<area_id>/`. Do not
  reintroduce `Crear`, `Ver`, per-config build forms or per-config Excel forms
  on this listing. The only visual difference from `/countries/` inside
  editable new-country cards is compact action links for full-container Excel
  export and container configuration. Changes to country-browser charts, tables,
  level selectors or share panels should be made in the shared renderer/API
  contract used by both `/countries/` and `/new-countries/` unless the user
  explicitly scopes the change to one view. In that shared country browser, the
  level selector updates the table, first-order charts and card/table summaries
  together and exposes every available non-root level, including commune or
  municipality leaf levels with no children. Do not reintroduce row-count limits
  for selectable levels; large levels are handled by API pagination with 20 rows
  by default and 100 as the maximum page size. Selecting Spain provinces, for
  example, should replace the 19 region cards with the 52 province rows/cards,
  and selecting municipalities/communes should page those rows rather than hide
  the level.
  The derived-country container detail URL is
  `/new-countries/<source_country_code>/<country_slug>/`; the old
  `/new-countries/<country_slug>/` route redirects there when the container has
  a source country. That detail page shows editable container `name` and
  `description` fields immediately; the user-visible `Codigo` is read-only
  there because the slug is route identity. Container create and edit pages
  share `templates/ciudades_del_mundo/_new_country_data_fields.html`: code and
  name share the first row, description is compact and full-width on
  desktop, and the detail table uses `.new-country-subentities-table-wrap` to
  keep space after the base-data panel. The contextual create URL remains
  `/new-countries/<source_country_code>/new/` for source-scoped creation flows;
  do not use `/new-countries/new/?source_country_code=...`. In new-country
  forms, `slug`
  is presented to users as `Codigo`, and `source_country_code` is hidden/fixed
  from the selected source country rather than shown as an editable `Pais fuente`
  field. The visible `Codigo` inputs are normalized by
  `app.js` to uppercase ASCII-style IDs without spaces; `-` and `_` are allowed
  in the visible code, while backend routes use `_` in the technical slug for
  compatibility. The `Descripcion`
  field on the country container form uses the compact
  `.description-textarea` override because the global `textarea` rule is for
  large TOML editors. Config URLs are source-qualified:
  `/new-countries/<source_country_code>/<country_slug>/configs/new/` and
  `/new-countries/<source_country_code>/<country_slug>/configs/<config_slug>/`;
  old config URLs without `<source_country_code>` redirect there. The visual
  `Nueva entidad` form follows the `/groups/subdivisions/<country>/<slug>/`
  editor shape: the `<form>` itself is not a panel, `Crear` appears above and
  below, base identity fields live in a `panel group-entry-main-box`, and the
  source-subdivision or derived-subdivision selector lives in its own `panel`
  below it. `Codigo derivado` and `Activa` are not user-facing in this module;
  hierarchical codes are derived from `parent_config_slug`: top-level entities
  use their local `Codigo` and child entities get the parent config code as
  prefix (`ARA-VAL`), while the edit form still displays only the child's local
  code (`VAL`). If a parent is created or edited with the same local code as a
  selected existing child, rename only the selected child's technical config
  slug (`ara` -> `ara_ara`) and reparent it; the child keeps local `Codigo`
  `ARA`, so the hierarchy displays `ARA-ARA` without a duplicate-config error.
  Creation/editing sets the config active and derives `derived_country_code`
  from the containing `DerivedCountry.slug`.
- `build_new_subdivisions --country-id <id>` first keeps legacy Python recipe
  compatibility, but if no recipe exists it resolves a SQL
  `DerivedCountryConfig` by `derived_country_code`, slug or container slug and
  builds `NuevoAdminArea` through
  `ciudades_del_mundo.services.derived_country_builder`. When several configs
  share the same derived country code, the SQL builder materializes them
  together and orders them by `parent_config_slug`, so child configs are built
  under their selected parent division.
- Legacy `new_subdivisions/*.py` remain supported; do not add new Python
  recipes when the requested hierarchy can be expressed in SQL
  `DerivedCountryConfig.content`, `/groups/` or `/subdivisions/`.

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
  Legacy SQL rows with an empty `source_country_code` infer their country from
  explicit `source_country_code` fields first, then from the `source_python`
  path/module slug, and only then from `[legacy].python_source` by parsing
  `DIVISIONS` / `spec` keys. Do not infer group country by regex over arbitrary
  legacy Python dictionary keys, because embedded keys such as `spain`,
  `restar`, `name` or `code` can make a Portugal legacy group appear under
  Spain.
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
  plus saved SQL `DerivedSubdivision` definitions that have not been materialized
  yet. Pending definition rows link to their editor and show `-` for `Terreno`
  and `Población` until `Popular` / `build_derived_subdivisions` rebuilds them.
  The table columns are `Nombre de la entidad`, `Tipo`, `Terreno`,
  `Población` and `Acciones`; that action cell must fit both compact POST
  actions, `Clonar` and `Borrar`, on the same row. `Clonar` duplicates the SQL
  `DerivedSubdivision` definition using the same `COD-COPIA` convention as
  `/subdivisions/`. `Borrar` is a
  POST-only `X` button with browser confirmation as the non-JS fallback, but
  normal browser use is AJAX: the delete view returns JSON, the clicked row is
  removed from the current table and client-side filters/pagination are
  re-rendered without a full page reload. The action removes the SQL
  `DerivedSubdivision` definition and also removes any directly materialized
  `NuevoAdminArea` row plus descendants for that definition. It does not show
  the old level-summary `Sumar nivel` column.
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
  block must disappear from the relevant add select. Section options include
  the entity type in parentheses while the user is managing/selecting rows,
  such as `Abla (Municipio)`, but once an item is selected its visible
  table/badge label must be clean, without parentheses or visible level text.
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
  After saving or importing a group editor, the `Volver` link preserves the
  `/groups/` country/table/row return state and marks the list stale so a
  browser-history return reloads `/groups/` before reopening the panel and
  scrolling to the row.
  The group SQL `name` must stay equal to the uppercase internal key such as
  `ALICANTE_A_MURCIA`; do not title-case or prettify it. When editing a
  country-scoped group and changing that internal key, rename the existing
  `SubdivisionGroup.slug` row instead of creating a second row, and rewrite
  same-country `DerivedSubdivision.content` references in `groups = [...]` and
  `capital_groups.group`. The same rename also rewrites same-country
  `SubdivisionGroup.content` references, including legacy `[legacy].python_source`
  bundles, so the old virtual group does not remain selectable after the modern
  row has been renamed.
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
  Task-starting forms must use the shared popup behavior, not a page redirect.
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
  --force`; pass `--continue-on-error` to build every resolvable row and log
  stale definitions that cannot currently resolve instead of aborting the whole
  country. Pass one or more `--slug <slug-or-internal-name>` values to rebuild
  only selected `DerivedSubdivision` rows; with `--force`, this replaces only
  those materialized rows instead of clearing the whole country. Partial rebuild
  must delete the target materialized row, its parent-chain descendants and any
  stale same-country `NuevoAdminArea.code` prefix descendants before recreating
  it, because `NuevoAdminArea.parent` uses `SET_NULL` and generated child codes
  otherwise collide with orphaned rows. The web `Popular` action uses tolerant
  full-country mode by default.
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
- When `build_derived_subdivisions` resolves a `[[subtract]]` name or group and
  several source `AdminArea` rows share that label, it prefers the candidate
  whose municipal-level expansion is already inside the parent territory included
  for that source country. This prevents groups such as Fenolleda from subtracting
  same-name municipalities in another French department while leaving the intended
  Pyrénées-Orientales rows inside the derived area.
- For mixed-depth CityPopulation sources such as France, a commune may be a
  terminal leaf at a shallower level than the nominal municipal level because an
  intermediate arrondissement layer is skipped. `nuevo_admin_builder` must treat
  those terminal lower-level leaves as effective atomic units when resolving
  names and expanding included parents/macros, while keeping normal descendant
  expansion for rows that do have children. The French `Berry` seed must include
  `Cher` and `Indre` plus `CREUSE_A_BERRY`; `CREUSE_A_BERRY` alone excludes
  Bourges and cannot satisfy the capital.
- Some imported legacy `DerivedSubdivision` bundles store metadata on the root
  row and put the actual source selection inside a single `[[children]]` block
  with the same name/level, sometimes with a different local code. The builder
  must fold that self-child wrapper into the parent `NuevoAdminArea` so rows
  such as `Artois` > `Artois` are not materialized. If the wrapper record also
  has other real children, keep those children and refresh parent totals from
  the unique source units plus child rows.
- `[[capital_groups]]` can assign capitals made from a `SubdivisionGroup` or
  explicit names; `capital_name` is optional and is stored as a capital display
  override for the grouped municipalities
- `/subdivisions/` lists these definitions by source-country cards, with the
  left table for created historical/fictitious subdivisions and the right table
  for available reusable groups. The header and country panel can import
  country-level seeds from `subdivision_groups/subdivisions/<country>.toml` via
  `sync_derived_configs subdivisions <country> --force`; export writes that
  same country bundle from SQL. `Popular` enqueues
  `build_derived_subdivisions <country> --force --continue-on-error`. The created-subdivision table
  has compact POST actions for `Clonar` and `Borrar`; cloning copies the SQL
  `DerivedSubdivision` TOML, keeps selections/capitals/children, changes the
  visible `code` to `COD-COPIA` and uses a route-safe `internal_name`/slug such
  as `COD_COPIA` / `cod_copia`.
  The `/subdivisions/` country cards are driven only by SQL
  `DerivedSubdivision` rows. Imported `AdminArea` countries, reusable
  `SubdivisionGroup` rows or TOML seeds alone must not create a country card
  there; the global import action remains the entry point for seed-only
  countries.
  Legacy dictionary rows imported from
  `subdivision_groups/subdivisions/<country>.toml` infer `source_country_code`
  per row when all included blocks point to a single explicit country. This
  prevents entries such as Oran embedded in Spain's legacy bundle from being
  saved under Spain when their `spec` includes only Algeria. Mixed-country
  rows keep the bundle country. A forced import that reclasifies a legacy row
  also removes the old bundle-country slug with the same `internal_name`, e.g.
  `spain_oran`, before creating the corrected row.
  The `/groups/` country panel may still show stale `NuevoAdminArea` rows after
  a `DerivedSubdivision` definition has been edited but before `Popular`
  rebuilds it. Keep those rows editable by resolving the link to the SQL
  definition through code first and a unique normalized definition name/internal
  name fallback second; code matching must include both the current requested
  code and the root-prefixed `internal_name`, so duplicate visible names such as
  `CATALUNHA_A` / `CATALUNHA_B` remain separately clickable even if one row was
  materialized before a code edit. When the row matches the definition itself,
  show name/type/level from the SQL definition, not the stale materialized row.
  Do not require the already-materialized code or level to match the edited TOML.
  If the SQL `DerivedSubdivision.updated_at` is newer than the matched
  materialized `NuevoAdminArea.updated_at`, `/groups/` must display terrain and
  population from the TOML preview instead of stale materialized metrics until
  `Popular` rebuilds persisted `NuevoAdminArea` rows.
  Navigating from a `/groups/` row to `/groups/subdivisions/<country>/<slug>/`
  stores a short-lived browser `sessionStorage` return target. The editor saves
  with AJAX when possible; changing `internal_name` or the calculated `code` on
  an existing `DerivedSubdivision` must update that same SQL row and preserve
  its `slug`/route instead of creating a second definition. Its `Volver` link
  returns to `/groups/` (or browser history when the
  previous page was `/groups/`) with the source country panel reopened, the
  correct client-pagination page selected and the edited row scrolled into view.
- `/groups/subdivisions/<country>/<slug>/` uses the same lower source-selection
  pattern as `/groups/groups/<country>/<group>/`: compact header, save actions
  above and below, a first `group-entry-main-box` for base fields, then two
  visual panels for `Sumar` and `Restar`. The source country is always the
  country segment in the URL and is stored in a hidden field only; the main box
  no longer shows the numeric `Nivel` control, but keeps its value in a hidden
  POST field until level editing moves to its final location. The form no longer
  exposes separate `Pais fuente`, `Seccion padre` or `Codigo propio` controls.
  On save, `parent_code` is fixed to the selected source country's root code and
  the full `code` is derived from `internal_name`; do not add a visible `Codigo
  calculado` field back. For example source country Spain uses
  root `ESP`, so `internal_name = "CASTILLA_VIEJA"` saves
  `code = "ESP-CASTILLA_VIEJA"`. GET requests render only
  the shell and spinner; `/groups/subdivisions/<country>/<slug>/data/` hydrates
  base fields, root `flag_url` / `coat_url`, parent options, selected capital
  labels and only the current `[[include]]` / `[[subtract]]` source selections.
  It must not preload all `SubdivisionGroup` rows or return a `group_countries`
  list, except for hydrating already referenced group badges in the current TOML.
  Existing `[[subtract]] groups = [...]` badges must resolve flat group member
  names inside the municipal scope already included for that source country; the
  editor should show the same scoped members and parent as the builder will
  subtract, not same-name rows from another parent.
  The lower source area queries SQL `AdminArea` lazily through
  `/groups/source-data/` using the same level/section rules as
  `/groups/groups/`; do not expose deeper leaf levels in the derived-subdivision
  UI when the group editor hides them. When this editor passes
  `include_groups=1`, `/groups/source-data/` may also return country-scoped
  `SubdivisionGroup` options as `source_kind = "group"` badges at their resolved
  member level and common parent scope. When it passes
  `include_derived_subdivisions=1`, `/groups/source-data/` may also return
  same-country lower-level `DerivedSubdivision` options as `source_kind =
  "derived_subdivision"` badges, filtered by the editor's `current_level`,
  `current_subdivision`, `current_subdivision_country` and selected parent
  municipal scope; only exclude the current subdivision key inside that current
  country, because another source country may have the same internal key. Resolve those
  special badge candidates in bulk per request and filter by level/parent before
  building member tooltip text; do not call the full `AdminArea`
  payload/ancestor metadata path once per grouped municipality. Like the group editor, the direct-source
  hierarchy includes a root country row before `Nivel 1`; its modal includes the
  country itself as level 0 plus direct level-1 administrations, all loaded from
  SQL, so either can be added as direct source badges. A selected source-country
  root, e.g. Gibraltar, must persist as a `level = 0` `ids = [...]` include or
  subtract block for that source country and hydrate back as a selected root
  badge; do not drop it because normal source levels start at 1. Visible TOML
  editing is not part of this form, but the hidden `content` textarea remains
  the persistence carrier. When the lower visual selection is dirty, POST data
  includes `include_ids_json` / `subtract_ids_json`; the view converts selected
  source rows to `[[include]]` / `[[subtract]]` blocks with DB `ids = [...]` for
  `AdminArea` badges, `groups = [...]` for reusable group badges and
  `derived_subdivisions = [...]` for already-created `DerivedSubdivision`
  badges. The lower selected-source table has exactly three columns
  (`Incluidos`, `Excluidos`, `Acciones`); empty rows must span three columns and
  the actions column must fit `Editar`, `Excluir` and `Quitar` without
  overlapping.
  Saving through AJAX keeps the editor on the same page but records fresh
  `/groups/` return state; after a save or import, `Volver` reloads a stale
  browser-history copy of `/groups/` before reopening the country panel and
  scrolling to the edited row.
  The lower country selector lives in its own control panel and has one
  `Anadir` action that adds a source-country block. Do not render separate
  `Sumar` and `Restar` country panels. Inside that single block, the hierarchy
  `Anadir` buttons open a transfer modal with `Disponibles` and `Grupo`,
  matching the `/groups/groups/` interaction style, and selected badges can be
  direct `AdminArea` entities, reusable `SubdivisionGroup` references or
  lower-level `DerivedSubdivision` references. Group badges use a distinct
  color, derived-subdivision badges use a third color, and both expose a
  title/tooltip listing the resolved SQL members. The transfer modal has one search filter that normalizes case and
  accents, so queries such as `Le`, `le` and `Lé` match the same badges, plus
  combinable type checkboxes for normal `AdminArea` entities, reusable groups
  and lower-level `DerivedSubdivision` references. Clicking modal badges must
  not refocus the search input or scroll the page; preserve focus only after
  actual filter input. Modal options returned by `/groups/source-data/` use per-request caches for
  `SubdivisionGroup` TOML entries and derived-subdivision municipal expansions;
  compact available group/derived-subdivision options must keep full
  `member_ids` for `Excluir`, but should not ship full `member_names` lists.
  Direct `Anadir` modal calls must limit reusable groups to the same level as
  the direct children of the selected hierarchy row, so root country add shows
  only level-1 groups. Descendant/exclusion modal calls must resolve reusable
  groups across all lower descendant levels under the selected included parents.
  Modal blocks separate normal `AdminArea` badges from group badges by type and
  level, e.g. `Nivel 3` and `Grupos - Nivel 3`. In the modal, separate those
  sections with a title and neutral thick line inside the `Disponibles`/selected
  badge container; do not use nested boxes/cards for each level. When
  `/groups/source-data/` loads direct children, it keeps only the shallowest
  direct child layer if inconsistent DB rows put multiple levels under the same
  parent, except direct unified city rows (`city_merge_status = 1`), which must
  remain available even when they are deeper than sibling administrative rows.
  The selected-source table groups rows by source country, level and
  parent scope, not by source type, so normal entities, reusable groups and
  lower-level derived-subdivision references from the same parent render as
  badges in one row while special badges keep their distinct colors. Normal
  `AdminArea` badges keep the default color, `SubdivisionGroup` badges use the
  group color and `DerivedSubdivision` badges use a third color. Visible
  selected labels must omit parenthetical type hints, `L3`/level text and
  technical IDs; a selected `DerivedSubdivision` reference shows only its local
  code. That table column order is `Incluidos`, `Excluidos`, `Acciones`. The
  two first columns render badge labels and split the remaining width 50/50
  after reserving a fixed narrow width for row buttons. `Editar` opens a selector for the same source country, level and
  parent scope as the edited row. For example, Spain L1 rows show all Spanish
  autonomous communities, while a province row under Castilla y Leon shows the
  other provinces under that same parent. That edit selector uses
  `/groups/source-data/` with `source_mode=items` so it returns all rows at that
  level, not only sections that have children. `Excluir` opens
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
  the normalized starts-with prefix first, then checks displayed/localized names
  and slash/parenthetical aliases before rejecting candidates. If the raw prefix
  misses a valid localized match, it may inspect candidates from the current
  include selection, but must not expand every possible source city in the
  country. The select displays just the
  municipality name.
  Ordinary municipal selections prefer base source rows plus unified city rows
  (`city_merge_status` `NONE` + `UNIFIED`). Explicit source-district selections
  retain the selected `SOURCE` IDs, including in group membership and capital
  source resolution, without promoting them to the entire city. The
  [partial-city normalization](derived.md#partial-configured-city-selections)
  prevents overlapping population, area and `municipios_originales`. It saves SQL
  `DerivedSubdivision` rows; the country-level build then recalculates terrain,
  population, density, capitals and source `municipios_originales` in
  `NuevoAdminArea`.
  Root `DerivedSubdivision` TOML may set
  `use_selected_entities_as_children = true/false`. If the key is absent, old
  records do not auto-generate child nodes. If it is false, the builder creates
  child `NuevoAdminArea` rows from the source municipal/comunal units in
  `municipios_originales`. If it is true, the builder materializes the selected
  `[[include]]` badges themselves as child nodes (`AdminArea`,
  `SubdivisionGroup` or referenced `DerivedSubdivision`), and referenced
  lower-level `DerivedSubdivision` children default to false so they can expose
  their own source units and preserve fictitious intermediate levels. The web
  editor no longer exposes this boolean in the main form; it keeps the current
  value as a hidden POST field until that control is moved elsewhere. `flag_url`
  and `coat_url` split that full row evenly. AJAX saves return a lightweight payload and should not recalculate or
  replace the already-mounted include/subtract visual source blocks; use the
  `/data/` endpoint for full hydration.

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

## Territorial reconciliation records

Migration `0030` adds `TerritorialReference`, `TerritorialEvidence` and
`TerritorialRun`. They separate reviewed/versioned reference data, frozen HTML
and parser observations, and a proposed plan with hashes, differences, diagnostics
and metrics. Importing them does not replace `ScrapingConfig` or rebuild areas.
Applying a reviewed plan uses the existing SQLite lock and transaction, checking
that inputs and the approved plan hash still match. See
[the reconciliation contract](territorial-reconciliation.md). This documentation
update does not apply the migration to the local database.

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
Derived source expansion defaults to `NONE` + `UNIFIED`. Explicit source
preferences such as `prefer_city_merge_status = "source"` opt into `SOURCE`;
an exact selected source-district ID also preserves that district rather than
expanding to the unified city. See
[partial-city selections](derived.md#partial-configured-city-selections).

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
