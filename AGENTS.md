# Agent working context

## Active HTTP backends

- Java and Python are maintained HTTP backends. Java has priority and is the
  selector default; the checked-in selection may differ. Root `backend.toml`
  selects `backend = "java"` or `"python"`;
  `BEOGRAD_BACKEND` overrides it. Invalid values fail; never silently fall back.
- In Java mode `levantar_app.bat` starts the Django UI on the selected public
  port and Java on the next port; Django forwards only the explicit migrated
  route allowlist. Closing/reopening restarts both and rebuilds changed Java.
  Python mode starts Django alone. Python HTTP owners
  live under `ciudades_del_mundo/web/`; `backend-python/manage.py` is the
  Python entry point, with the same maintained status as `backend-java/`.
  Keep one active Python implementation, shared with the Django UI.
  Java never proxies to Python;
  pending routes retain their Django owners, without fallback on Java failures.
- Every HTTP improvement/fix must be implemented and verified in both backends,
  prioritizing Java and preserving shared response contracts. Record any remaining
  coverage differences explicitly; Java's route migration is still incomplete.
- Maven tests use synthetic frozen SQL/HTTP contracts without importing Django.
  Windows supervisor tooling uses only Python's standard library.
- See [backend selection and operation](docs/architecture/python-http-retirement.md).

Read this index first. Inspect only the modules relevant to the task; do not
read whole datasets, caches, logs, exports or Git history.

## Scope and invariants

- Django application for CityPopulation geography/population, `AdminArea`,
  derived `NuevoAdminArea` hierarchies, assets, AI enrichment and CSV/XLSX exports.
- SQL is the runtime source of truth: `ScrapingConfig`, `DerivedCountryConfig`,
  `DerivedSubdivision` and `SubdivisionGroup`, including their TOML `content`.
  Local TOML files are explicit import/export and bootstrap bridges only.
  Never introduce a runtime fallback to seed files.
- Keep global TOML keys (schema version, country code and national QID) at the
  document root, before tables. Valid TOML alone does not prove correct levels.
- Preserve IDs, model/table/field names, hierarchy levels, population, merges,
  translations, URL names, response contracts and export formats.
- Keep working UI layout and interactions unless the user requests their change.
  New visible strings must use gettext and the shipped catalogs.
- Do not modify `db.sqlite3`, its sidecars, generated exports, `.idea/` or
  `__pycache__/` as incidental refactoring work. Never validate with real
  scraping or paid AI calls without authorization.
- Off-project requests do not authorize repository work.

## Workflow and documentation

- Check `git status --short` before editing and preserve user changes.
- Prefer `rg` / `rg --files`; use PowerShell fallbacks if unavailable.
- Use `py manage.py ...` from the repository root. Disable incidental bytecode
  writes with `$env:PYTHONDONTWRITEBYTECODE='1'` when verifying tracked code.
- Work in verifiable blocks. Report only checks actually executed and their
  limitations. Report exact token usage/budget if exposed; otherwise say it
  is unavailable.
- Project skills live in [.agents/skills](.agents/skills/):
  [code generation](.agents/skills/beograd-code-generation/SKILL.md),
  [scraping configs](.agents/skills/beograd-scraping-configs/SKILL.md) and
  [derived data](.agents/skills/beograd-derived-data/SKILL.md).
  Read the relevant entry before changing that feature.
- Ordinary code/config tasks: identify durable documentation or memory changes
  and briefly mention them; defer edits until `documentacion` or an explicit
  documentation request. Explicit workflow-rule changes or requests targeting
  `AGENTS.md` authorize its immediate update. An explicit documentation task
  updates this index and the relevant thematic/user documentation together.
- Keep this file concise: rules and architecture index, not a changelog.
  Put scraping/config pitfalls in thematic docs, user commands in `README.md`,
  reusable engineering rules in the relevant local skill when available.
- For documentation-only work, verify local links and referenced commands against
  their owners. Keep historical test results separate from checks run now; see
  [verification scope](docs/architecture/operations.md#offline-verification-scope).

## Dependency direction and module index

- `backend-java/`: native Spring Boot/JDBC backend. In combined Java mode,
  `web/java_routing.py` forwards only `JAVA_ROUTE_NAMES` from Django to Java.
  Java SQLite access is read-only; Django retains pending routes and mutations.
  See [Java scope and Windows operation](docs/architecture/java-api.md) and
  [route inventory](docs/architecture/java-api-inventory.md). Pending Java routes
  retain their Django owners in combined Java mode; Python mode serves all routes
  in Django. Do not report the Java migration complete.
- `tools/java_local.py` and root Java `.bat` scripts: explicit preparation,
  verified builds, owned Windows processes and artifact rollback. No automatic
  SQL imports/migrations. Use disposable data for Windows acceptance tests.

- `ciudades_del_mundo/domain/`: pure business types/rules; no Django, adapters,
  application, services or HTTP.
- `ports/`: domain-facing protocols/errors; no concrete adapters or Django.
- `application/`: use cases/rules using domain and ports. No models, Django,
  services, web, management or infrastructure imports, including relative ones.
- `infrastructure/`: Django repositories, scraping/HTTP, XLSX and AI providers.
- `services/`: existing SQL-oriented orchestration and composition adapters.
- `web/handlers/`: feature-specific HTTP handlers (dashboard, configs,
  new countries, groups, subdivisions, recipes, assets, tasks, exports).
- `web/forms/`: existing POST/TOML transformations by feature.
- `web/queries/`: data-only selectors/payloads; accept data, not HTTP requests.
  No HTTP responses, handler imports or SQL mutations here.
- `web/http.py`: HTTP query parsing and `DataNotFoundError` to 404 translation.
- `infrastructure/django/area_selectors.py`: country/level/merge filters and ORM queries.
- `services/derived_config_mutations.py` and `services/scraping_config_tasks.py`:
  SQL mutations and explicit seed bootstrap; no web imports. Handlers compose them.
- `application/browser_metrics.py` and `application/export_metrics.py`: numeric
  rules, legal-unit rollups and export layout calculations without Django.
- `application/group_references.py`: framework-free reference renaming rules.
- `services/composite_city_sources.py`: disjoint source-district selections for
  derived cities; never promote a selected district to the whole unified city.
- Saved derived entities use `services/new_country_entities.py` for branch
  mutations and `services/derived_country_group_sync.py` for automatic group
  subtractions. Preserve saved IDs, manual selections and the distinction
  between explicit metrics and totals inherited from children.
- `web/views.py`: explicit public HTTP compatibility exports only. URLs, internal
  callers and patches target owners; no helper re-exports or attribute forwarding.
- `static/ciudades_del_mundo/src/`: ordered JS/CSS sources and `manifest.json`.
  Edit sources, then run `py -B tools/build_web_assets.py`; verify with
  `py -B tools/build_web_assets.py --check`. Bundles keep the existing lexical
  scope/cascade. Bump both versions in `templates/ciudades_del_mundo/base.html`
  whenever shipped bundle behavior changes.
- `ports/ai.py`, `infrastructure/ai/json_provider.py`,
  `application/ai_responses.py`, `services/ai_text_enrichment.py`: provider
  contract, transport, validation, SQL enrichment. Keep AI outside domain.
- Territorial CLI: `application/ai/local_territorial.py` is the default symbolic
  engine for `--strategy ai`; `--ai-engine provider` explicitly selects remote
  inference. `traditional` remains the scraping default. Reviewed SQL references,
  frozen evidence and an approved plan hash are required; HTTP integration is
  pending. See the territorial reference below before changing this workflow.
- `tests/test_architecture_boundaries.py`: recursive core import/protocol checks.
  Also rejects duplicate function definitions within each web Python scope.
- `infrastructure/wikidata_countries.py`: external country identifier catalog.
- `tests/test_task_lifecycle.py`: bounded log cursors and simulated task races.
- `tests/test_country_browser_loading.py`: lazy country branches, bounded summary
  queries, canonical roots and batch assets/translations. Keep cached summary
  rendering and compositor-only theme motion; see
  [country browser contracts](docs/architecture/web.md#country-ui-hierarchy-detail).
- `tests/isolated_settings.py`: SQLite in memory; offline test runner rejects
  external socket connections. Use it for refactoring checks/tests.
- `tests/frontend/behavior.test.cjs`: actual bundle behavior with DOM/time/HTTP
  doubles; run `node --test tests/frontend/behavior.test.cjs` (Node 20+; no npm).
- `tests/frontend/group_selection.test.cjs`: inline group-editor script;
  preserves add/subtract operations through lazy loading and modal cancellation.
  Run it explicitly; `verify_clean_copy` currently runs only the bundle harness.
- `requirements.txt`: verified runtime dependencies. `tools/__init__.py` makes
  tooling imports explicit. `py -B -m tools.verify_clean_copy --python <interpreter>`
  checks a source-only temporary copy with installed dependencies, no DB or seeds.

## Recurring behavioral constraints

- Preserve SQLite write locks, transaction boundaries, queued subprocess tasks,
  cancellation/replacement and interruption recovery. Optimize only measured work.
- Selectors must request SQL data by country plus selected level/page/search;
  country-only requests may return metadata but must not load every source row.
  Preserve stored selections on untouched empty initial payloads.
  `/countries/` renders its cached summary before revalidating SQL; request
  `include_children=0` for country drill-down and load area children on click.
- CityPopulation uses schema v2 block validation and in-memory global linking
  before persistence. Follow generic matching/config extension points, not
  country-specific parser branches. Assets can warn without blocking scraping.
- Normalize hectares to km2 before linking, density calculations and territorial
  sums; preserve already-normalized values and the source's land/water scope.
  See [area units and territorial scope](docs/architecture/scraping.md#area-units-and-territorial-scope)
  and `tests/test_scraping_area_units.py`. Invalidate parsed checkpoints when
  their stored metric semantics change.
- Preserve distinct source population rows, including settlement parts sharing
  a QID. Use page identity controls and exact runtime exclusions from SQL; see
  [source identity controls](docs/architecture/scraping.md#source-identity-controls).
- Keep territorial roles at the same levels across every branch of a country,
  regardless of CityPopulation's page layout or omitted layers. When one entity
  fills several roles, preserve explicit repetitions on consecutive levels;
  do not collapse them solely because name/QID matches. Follow the territorial
  objective; never invent localities to fill an absent final layer. See
  [country-wide level alignment](docs/architecture/scraping.md#country-wide-level-alignment).
- For console scrape errors, inspect the newest country task/validation logs
  first. For country configuration requests, use `prepare_ai_autoconfig` and
  the territorial objective before changing behavior.
- Deliver country-specific scraping fixes in the country TOML and explicitly
  synchronize SQL. Use generic configuration controls; temporary repair scripts
  may apply existing-data repairs but must not become runtime dependencies. See
  [Cuba's configuration example](docs/architecture/scraping.md#cuba-havana-city).
- Follow the [general scraping workflow](docs/architecture/scraping-workflow.md).
  Use the [command effects table](docs/architecture/operations.md#command-effects)
  to distinguish offline diagnostics, SQL validation and data mutations.
  Check complete affected branches, semantic child counts and national/root
  assets. When a repair includes existing data, verify committed SQL and the
  detail API; instructions, config edits and offline previews alone are not
  an applied data repair.
- Keep active `_legacy` routes/imports until references and regressions prove
  removal safe. Do not rename persisted entities for aesthetics.

## Detailed references

- [Accumulated implementation state and verification limits](docs/estado-actual.md)
- [Territorial AI references, CLI, validation and pending HTTP integration](docs/architecture/territorial-reconciliation.md)
- [Refactor structure, validation and limits](docs/refactorizacion.md)
- [Architecture and SQL sources](docs/architecture/architecture.md)
- [Models, IDs, merges and persistence](docs/architecture/data.md)
- [Scraping blocks, linking and country pitfalls](docs/architecture/scraping.md)
- [General scraping and repair workflow](docs/architecture/scraping-workflow.md)
- [Derived hierarchies, capitals and representatives](docs/architecture/derived.md)
- [Exports](docs/architecture/exports.md)
- [Web contracts, selectors and task workflows](docs/architecture/web.md)
- [Commands, testing map and operational risks](docs/architecture/operations.md)
