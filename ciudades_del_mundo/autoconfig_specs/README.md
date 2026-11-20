# AI autoconfig territorial objectives

Place one `<slug>.txt` file here for each country or territory whose
CityPopulation configuration should be checked by an AI agent.

The file should describe the intended persisted hierarchy, one chain per line
when useful. Keep it concrete and level-oriented:

```text
Region/Overseas department > Department > District/Paris > Commune > Locality
Level 1: regions and overseas departments
Level 2: departments, including overseas departments
Level 3: districts, Paris when applicable
Level 4: communes
Level 5: localities/towns only when CityPopulation exposes them
```

Assign each territorial role to the same level in every branch of the country,
including city-states and statutory cities. If one entity fills several roles,
state the required repeated chain explicitly; matching names or Wikidata IDs
across levels do not justify collapsing it. Do not fabricate localities for
branches whose final locality layer is absent. For example, Austria requires
`Wien (state, L1) > Wien (district/capital city, L2) > Wien (city, L3)`.

Describe the scope of extra source subdivisions explicitly. In Austria,
Vienna's 23 municipal districts are preserved under its L3 city at L4, with
their original district type; they are not fictitious localities. Other
countries must define their own role mapping instead of copying that depth.

A reusable objective outline is:

```text
Country/config slug and effective country_code:
Role at each level, starting at L0:
Ordinary branch examples:
Capital/city-state/statutory-city exceptions:
Intentional repeated roles and stable IDs, where already established:
Additional source subdivisions and their intended parent/level:
Final layers absent from the source:
Expected direct-child counts and the source confirming them, when known:
```

Follow the [general scraping workflow](../../docs/architecture/scraping-workflow.md)
for TOML scope, offline block/link validation, national assets and final SQL/API
checks. A correct objective documents the target; it does not itself update
the SQL config or existing `AdminArea` rows.

Run this before asking an agent to configure or repair a country:

```powershell
py manage.py prepare_ai_autoconfig france
```

The command writes a dossier under `.web_ai_autoconfig/<slug>/` with this
objective file, the current SQL TOML, recent scrape logs and scraped-page
snapshot paths. It writes local diagnostics and runs old-log retention, without
modifying configuration or scraping the network. Check `toml_source`: when the
SQL row is unavailable, the dossier can include the local seed as diagnostic
reference. This neither imports it nor enables a runtime seed fallback. See
the [command effects table](../../docs/architecture/operations.md#command-effects)
to distinguish this step from SQL validation, imports and data materialization.
