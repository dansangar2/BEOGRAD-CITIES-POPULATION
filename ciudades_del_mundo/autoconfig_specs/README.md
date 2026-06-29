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

Run this before asking an agent to configure or repair a country:

```powershell
py manage.py prepare_ai_autoconfig france
```

The command writes a dossier under `.web_ai_autoconfig/<slug>/` with this
objective file, the current SQL TOML, recent scrape logs and scraped-page
snapshot paths. It does not modify configuration or scrape the network.
