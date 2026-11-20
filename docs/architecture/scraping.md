# Scraping configuration and linking

Detailed repository reference. Start with `AGENTS.md` and read only the relevant section.

Use the [general scraping and repair workflow](scraping-workflow.md) for the
sequence of diagnosis, territorial design, SQL configuration, offline checks
and verification of existing data through the detail API.

## Current Scraping Memory

The active `/configs/<slug>/` editor and runtime importer use the
CityPopulation block model with `scrape_schema_version = 2`. The attempted
Wikimedia/Wikidata V3 data-import layer has been removed: there is no
`/configs/old/<slug>/` route, no `old-*` runtime rows, no
`scrape_wikimedia_subdivisions` command, and no V3 config parser/importer.
Wikimedia/Commons remains in the project only for visual assets and the
best-effort parent repair used by the CityPopulation flow.

Active CityPopulation configs use `scrape_schema_version = 2` at the document
root of `ScrapingConfig.content`, before any TOML table. A declaration inside
`[visual_assets.flag]` is syntactically valid but is not the global schema
setting: loaders default to schema 1 when that root key is absent. Validate the
parsed scope and effective page levels as well as syntax.
Runtime still reads SQL only; TOML files in
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
and every other config fragment. The normal `/configs/<slug>/` editor save is
allowed to call `ciudades_del_mundo.services.configured_city_materializer` as a
best-effort follow-up: if that SQL config has pending `[[cities]]` and the
country already has `AdminArea` rows, it writes the unified city rows and marks
their source rows without running a scrape. TOML import actions remain
configuration-only; use `py manage.py materialize_city_merges <slug>` when an
already-saved SQL config needs this DB materialization explicitly.
For `[[cities]]`, do not reuse the source parent subdivision code as the
configured city `id` when that parent must remain selectable in the city
builder or derived-subdivision selectors. Use a distinct synthetic code, and set
`parent_type` when the parent label/type must be preserved during materializer
repairs; Morocco uses this for prefecture parents such as Casablanca,
Marrakech, Rabat and Salé.

## Area units and territorial scope

`ScrapedAdminArea.area_km2`, persisted `AdminArea.area_km2` and explicit area
overrides use square kilometres. Convert source units during HTML parsing,
before cross-page linking, `sum_to_root`, city aggregation or SQL persistence.
One square kilometre is 100 hectares; divide hectare values by 100 once.

The owners are
[`CityPopulationClient`](../../ciudades_del_mundo/infrastructure/scraping/city_population_client.py)
and the [section parser](../../ciudades_del_mundo/infrastructure/scraping/citypopulation_sections.py):

- Visible `rarea` cells use the enclosing table's `thead`, preferring its
  `rarea` header. `hect`, `hectare`, `hectares` and `ha` select divisor 100;
  km2 or an absent unit retain divisor 1. An explicit legacy `area_divisor`
  takes precedence over detection.
- `data-area` values are already in km2 and retain priority over visible cells.
  Hidden `noviz` area cells already have their own hectare conversion; do not
  divide either representation a second time.
- Textual `infosection` areas with an explicit hectare unit are converted too.
  `tfoot` and `tbody.admin0` national totals use the same table-unit rule.
  The section parser supplies the divisor explicitly when copying an admin0
  row, because the detached copy no longer has its table header.

Preserve the source's geographic scope independently of its unit: land area
and total area including water are different metrics even when both use km2.
The Canadian source's land area can be smaller than the USA's without implying
a hectare error. Likewise, adding French overseas departments or Kosovo must
follow the SQL configuration's territorial selection, not a conventional
country-area ranking. Repeated roles on consecutive hierarchy levels do not
justify adding the same territory more than once.

Mayotte illustrates the propagation risk: 37,424 hectares are 374.24 km2.
Its department, repeated department, islands and communes must be normalized
before the department contributes to France. Locality rows already read from
normalized or hidden cells must stay unchanged. Do not repair every level of
an affected country with a blanket division by 100.

[`test_scraping_area_units.py`](../../ciudades_del_mundo/tests/test_scraping_area_units.py)
covers the three schema-v2 systems, body/footer/admin0 totals, source priority,
infosections, legacy divisors, density calculation and simulated scraping into
isolated SQL, including a repeated overseas branch added once to the country.
It also rejects pre-fix parsed checkpoints; see
[checkpoint compatibility](operations.md#management-commands) and the
[historical repair record](operations.md#hectare-repair-record-2026-10-22).

## Country-wide level alignment

The territorial objective assigns a fixed level to each territorial role across
every branch of a country. CityPopulation's page layout, row status and omitted
layers must not shift equivalent roles to different levels. Before configuring
blocks, map the intended roles to levels and verify the final linked tree for
ordinary regions and special cases such as city-states and statutory cities.

If the same real entity fills consecutive administrative roles, represent those
roles as an explicit chain with distinct IDs, using the existing `repeat`, root
and configuration extension mechanisms. Equal names, populations or Wikidata QIDs
across levels do not make that chain an accidental duplicate. Preserve its
parent links and metrics; do not sum repeated populations across levels. An
absent final locality layer stays empty: do not invent settlements to make every
branch reach the same depth. Additional source subdivisions need their actual
role mapped explicitly; a municipal district is not automatically a locality.

Austria's intended roles are L0 country, L1 state, L2 district or capital city
acting at district level, L3 city/commune and L4 locality when available:

- `Vorarlberg (L1) > Bregenz district (L2) > Bregenz city/commune (L3) > Bregenz locality (L4)`.
- `Wien (L1) > Wien district/capital city (L2) > Wien city (L3)`; no locality is
  fabricated below it. Repeating Wien at L2 and L3 is required by the target
  roles, even though CityPopulation presents a different arrangement.

Vienna's 23 municipal districts belong below the L3 city, at L4, retaining
their original IDs and district type. They are not regional L2 districts or
invented localities. The expected direct-child counts are `1 > 1 > 23`.
This is a country-specific mapping of the general rule, not a level template
to impose on every country.

The AI dossier generated by `prepare_ai_autoconfig` must carry this rule, and
the country-specific objective in `autoconfig_specs/<slug>.txt` records its
concrete level mapping. Runtime configuration remains SQL-only.

## Austria: Vienna blocks

The versioned [Austria seed](../../ciudades_del_mundo/subdivisions/austria.toml)
expresses the [territorial objective](../../ciudades_del_mundo/autoconfig_specs/austria.txt)
through generic block controls. Its Vienna chain uses these codes:

| Role | Level | Code | Parent code | Configuration |
| --- | --- | --- | --- | --- |
| State | L1 | `9` | Country root | Administrative block |
| District/capital city | L2 | `90001` | `9` | First `wien` block, root type `Capital City` |
| City | L3 | `90001_city` | `90001` | Second `wien` block, root type `City` |
| Municipal districts | L4 | `901` through `923` | `90001_city` | Explicit `parent_overrides` |

Both `wien` blocks use `source = "cities"`, persist only the infosection and
set distinct `root_code`, `root_parent_code` and `force_highest_level` values.
They intentionally reuse a URL in separate `[[pages]]` blocks. Parsed entities
are reused by URL within a block, so combining these two roles into one block
would lose the independent root settings. `cities = false` on those blocks
does not remove district rows supplied by the administrative block.

The explicit override selects only codes `901` through `923`, reparents them
to `90001_city` and sets L4 while preserving source IDs and district types.
Ordinary `localities/*` pages instead start at L3 with
`infosection = false`, `major_subdivision = true` and `cities = true`, retaining
the city/commune L3 > locality L4 chain. These are different territorial roles
at the same depth; the municipal districts are not renamed to localities.

Check the full administrative, regional and locality blocks together. The
expected Vienna direct-child chain is `1 > 1 > 23`; correct levels in the TOML
alone do not prove these counts in SQL or the browser. Explicitly synchronize
the seed and SQL when applying configuration changes, and separately verify
any authorized existing-data repair through the detail API. This section
describes the recipe, not evidence of a new scrape or database repair.

## Cuba: Havana city

The [territorial objective](../../ciudades_del_mundo/autoconfig_specs/cuba.txt)
keeps L0 country and L1 provinces and the special territorial division. The
ordinary branch is `province L1 > municipality L2 > locality L3`, for example
`Pinar del Rio province > Pinar del Rio municipality > Pinar del Rio locality`.
Havana follows the requested chain
`La Habana (Prov) L1 > La Habana (Cd) L2 > La Habana (Municipios) L3`.
Keep original source types, including `Municipality` for Havana's municipalities
and `City` for localities, rather than renaming rows to fit the role labels.
Isla de la Juventud retains its distinct L1 (`1878`) and L2 (`4001`) rows even
though both have QID `Q115027`.

The saved `/cuba/ciudaddelahabana/` HTML contains only the city table
`table#ts`. Requiring `major_subdivision` causes `SCR-BLOCK-SECTION`, reported
as `SCR-BLOCK-001` for block 4. Disabling that section alone is insufficient:
the old `parent_level = 2` cannot identify a single municipality for the whole
city and leaves an invalid parent during final linking (`SCR-LINK-001`).

The durable fix is the following block in
`ciudades_del_mundo/subdivisions/cuba.toml`, explicitly imported into SQL:

```toml
[[pages]]
source = "cities"
path = ["ciudaddelahabana"]
parent_level = 1
force_highest_level = 2
include = { cities = true, infosection = false, major_subdivision = false }

[[parent_overrides]]
codes = ["2301", "2302", "2303", "2304", "2305", "2306", "2307", "2308", "2309", "2310", "2311", "2312", "2313", "2314", "2315"]
parent_code = "2300001"
level = 3
```

The city `cuba_2300001` occupies L2 under the L1 province `cuba_1874`. The
province has that one direct child, and the city has the 15 municipalities at
L3. The explicit code selector reuses existing `parent_overrides` after global
linking; it changes only those municipalities' levels and parents. The province
and city both use QID `Q1563` while retaining their distinct IDs and source
populations. No intermediate entity or extra locality is fabricated. This uses
generic configuration controls; no Cuba branch was added to application code.
Import/export commands are in
[README](../../README.md#sincronizar-toml-semilla-por-pais-con-sql).

The local data repair used the 18 saved pages from run `ea92ed5e308f` on
2026-10-15, with network connections blocked. The full in-memory pipeline
produced 1,156 entities: 1 at L0, 16 at L1, 154 at L2 and 985 at L3, with no
orphans or cycles. L2 contains 153 ordinary municipalities plus Havana city;
L3 contains 970 localities plus Havana's 15 municipalities. All existing IDs,
names, populations, source types and QIDs were preserved. The hierarchy update
changed 16 existing rows: the city moved from L3 to L2 and its 15 municipalities
moved from L2 under the province to L3 under the city.
These are observed counts for those snapshots, not assumptions about future
source updates.

Temporary `verify_repair.py` and `apply_repair.py` under
`.web_ai_autoconfig/cuba/` served that one-time operation. The latter saved the
SQL recipe, inserted `cuba_2300001`, updated the most-populated-city links of
`cuba_cuba` and `cuba_1874`, and copied the existing flag and coat through the
shared-QID asset service. It used the SQLite lock, a transaction and a check
against the original configuration. The prior TOML and affected values were
saved there as `original.toml` and `repair_before.json`. That initial repair
predated the requested province > city > municipalities hierarchy. These files
depend on that original state and execution context; they are not maintained
entry points or runtime dependencies. Future imports use the TOML block above.

The final TOML was explicitly imported through `sync_scraping_configs`.
The hierarchy update then used a locked transaction, retained the previous
affected values in `hierarchy_requested_before_rows.json`, and recalculated
the three affected most-populated-city links. After commit, a fresh SQL
connection and the detail API confirmed `cuba_1874` with one child at L2 and
`cuba_2300001` with 15 children at L3. The ordinary Pinar del Rio branch still
returned 11 municipalities at L2 and 9 localities under its namesake municipality
at L3. The city retains flag/coat URLs associated with `Q1563`; browser image
downloads were not checked. The TOML and SQL content were compared.
Configuration import alone does not replay the existing-data repair.

## Page blocks and level controls

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
parser initially placed them at a shallower level. The linker can resolve
children from different lower levels, but a technical link alone does not
satisfy the territorial objective: configure explicit intermediate roles when
required for country-wide alignment. `sum_to_root = true` is labelled in the UI as `Sumar al
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
`_apply_forced_parent_levels`, `_rewire_parent_codes`, duplicate scoring and
prefix repair must use level/code/QID/name/scope maps or prefix maps instead of
scanning the full entity list, or the target forced-parent level, per row.
Portugal has roughly 21k rows with many `parent_level = 2` city rows and Spain
has roughly 37k rows; both should normalize in seconds, not appear stuck for
minutes.
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
`.web_ai_autoconfig/`, `.web_export_logs/`, `.web_task_progress/` and
`.web_scrape_resume/`. New web task logs are grouped by task key/country, for example
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
  `ciudades_del_mundo/services/scraping_config_extensions.py`, attached by
  Django composition adapters, and applied inside
  `application/scrape_admin_areas.py` after page scraping. Use them instead of
  hardcoded view/model fallbacks when a country needs synthetic containers,
  level shifts or additive country totals. The configured-city materializer
  also applies these extensions to existing SQL rows when `[[cities]]` exists.

Runtime extension fields:

- `[[synthetic_entities]]`: creates deterministic rows such as France
  `METRO`/`OVERSEAS`. Supports `code`, `name`, `level`, `parent_code`,
  `entity_type`, `data_wd`, `last_census_year`, explicit metrics, `copy_metrics_from` and
  `metric_source_codes`.
- `[[parent_overrides]]`: reparents or relevels already scraped rows by
  `codes`, `names` or `match_levels`, with optional `exclude_codes`. An optional
  `code_pattern` must match the entire code (`re.fullmatch`). Derive the parent
  with `parent_code_template` and regex captures, or set a literal `parent_code`;
  these two parent fields cannot coexist. Rules run in document order, so later
  exceptions can replace earlier parents. A level change affects matched rows,
  not their entire subtree.
- `[[root_metric_sources]]`: adds area/population metrics from extra scraped
  pages to the level-0 country row by matching `code`, `name` or URL `path`.
- Root-level `exclude_entity_codes`: removes exact codes after synthetic rows
  and parent overrides, before root metric sources. It is scoped to that SQL
  configuration and does not remove descendants automatically. Reparent children
  explicitly or final link validation will reject unresolved references.

For example, use TOML literal strings to preserve regex backslashes:

```toml
[[parent_overrides]]
code_pattern = '([0-9]{8})x.*'
parent_code_template = '\1'
level = 4
```

This maps `12345678x0001` to parent `12345678` at L4. The parent must exist in
the final entity set. Invalid patterns, missing patterns for templates and
invalid capture references are rejected when loading the extension.

`[[pages]]` fields:

- `source`: `cities`, `admin` or `citiesadmin` for schema v2. Older concrete
  scraper formats remain compatibility paths, not new configuration choices.
- `path`: string or array; prefer array even for one path.
- `lowest_level`: first level parsed on that page; legacy alias `level`.
- `area_km2`: optional area override for every row from that page; legacy
  aliases `size`, `custom_size`.
- `area_overrides`: optional map by scraped `id`, `code` or `name`; legacy
  aliases `size_overrides`, `custom_sizes`.
- `preserve_source_codes`, `wikidata_overrides`, `code_overrides`: page identity
  controls described below.

### Source identity controls

Use `preserve_source_codes = true` on a page when distinct CityPopulation codes
represent separate population rows. Duplicate matching then uses code plus
level, preserving homonymous communes, distinct localities sharing a QID and
repeated territorial roles. Repeated occurrences of the same code and level
still deduplicate; incompatible cross-level code reuse receives a deterministic
parent-scoped alias. The flag belongs to scraped entities, not a new SQL field.
Without it, ordinary same-level QID/name matching remains available.

Rows whose original or normalized type contains `(part)` also deduplicate by
code and level. For example, the two Slovenian Čeče settlement parts keep their
own municipality and population despite sharing a name and QID. Re-reading the
same part does not create another population row.

`wikidata_overrides = { "source_code" = "Q123" }` replaces a parsed row's QID.
`code_overrides = { "source_code" = "stored_code" }` rewrites both row codes and
parent references on that page. QID overrides use the code before this rewrite.
These controls act during HTML parsing; saving TOML alone does not rewrite
existing SQL rows. Keep mappings consistent across all affected page blocks.

For `table#ts` parent lookup, the closest preceding administrative section wins
over homonymous earlier sections or locality peers. A national `table#tl` with
no infosection/footer can supply L0 from `tbody.admin0`; that total is excluded
from ordinary subdivision parsing. When it has no ID, the parser supplies the
country code as its root identity.

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

Source units are physically attached below the configured city at `level + 1`,
with descendant levels shifted accordingly, including when `keep_communes` is
false. Public selectors normally filter their `SOURCE` status. A known parent
chain takes precedence over coincidental code prefixes; prefix inference is
reserved for rows without a known parent. Unrelated villages must not move
under a city merely because their codes begin with a district code.

`materialize_city_merges <slug>` reapplies every `[[cities]]` block to existing
SQL rows, applies runtime extensions and recomputes most-populated-city links.
It does not skip a city just because its `UNIFIED` row already exists. It does
not run HTML parsing or fetch new source populations. Check repeatability and
the detail API after an authorized data repair; this command is a SQL mutation.

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
   rows and only missing pages are fetched. Cache compatibility includes
   `SCRAPE_RESUME_VERSION` as well as the SQL configuration hash; version 2
   rejects pre-fix hectare metrics even when the configuration is unchanged.
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

## Scraping notes added 2026-06-08 - hierarchy/assets corrections

- Hungary/Budapest: keep `keep_communes = true` in `hungary.toml` so the configured city is inserted as the L2 complete city and the Budapest city districts are shifted below it as L3 children.
- Slovakia/Bratislava and Košice: use the full city codes (`528000`, `599000`) for the configured city rows and keep the district rows below them with `keep_communes = true`. Do not use the older synthetic parent + `child_id` pattern here, because that leaves the complete city and districts at the wrong relative level.
- Aruba: keep regions above urban areas. The intended hierarchy is L1 census regions, L2 cities/urban areas, L3 zones.
- Curaçao: `/curacao/cities/` has unstable/no ids for several place rows and collisions with geozone/neighborhood ids. Keep geozones complete from `/curacao/admin/`; use synthetic L1 city/place containers and `parent_overrides` for geozones instead of persisting the cities-page rows directly.
- Saint-Barthélemy: Gustavia (`25266`) must be parented to Centre (`8446`).
- For territories whose scraped root page does not reliably seed a flag, add `[visual_assets.flag]` fallbacks in the TOML. Belgium also needs explicit `[[visual_assets.admin_areas]]` entries for `BE-VLG`, `BE-WAL` and `04000` region flags.
