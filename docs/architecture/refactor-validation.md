# Registro de validación del refactor

Este registro conserva resultados historicos de la fase del refactor. No acredita
el estado del checkout actual ni implica que las pruebas se hayan repetido al
actualizar documentacion. El [alcance de verificacion](operations.md#offline-verification-scope)
describe las comprobaciones disponibles y sus diferencias.

La revisión puntual ejecutó 421 pruebas offline con SQLite en memoria: 30 fallos de
aserción y 7 errores (incluidos subcasos), sin pruebas omitidas. Al comenzar esta
fase, 389 pruebas reproducían exactamente los mismos identificadores de fallo
y error. Las nuevas regresiones no añaden fallos a ese conjunto.

Una instalación nueva desde `requirements.txt` pasó `pip check`. En una copia
temporal sin DB, semillas ni diagnósticos pasaron `check`, los bundles, 64 pruebas
Django y 12 pruebas de comportamiento del frontend con dobles DOM/tiempo/HTTP.
La comparación no restaura fuentes ni modifica datos locales.

No se relajaron expectativas para ocultar fallos previos. Los patches de tests
se migraron a los módulos consumidores y las pruebas antiguas de propagación de
atributos se sustituyeron por aislamiento/restauración del propietario.

## Casos pendientes reproducidos con las vistas anteriores

### Fallos de aserción

- `test_dashboard.DashboardViewTests.test_api_admin_area_detail_returns_direct_children_for_recursive_browser`
- `test_dashboard.DashboardViewTests.test_dashboard_country_detail_adds_second_order_shares_by_level`
- `test_dashboard.DashboardViewTests.test_dashboard_country_detail_keeps_all_types_in_large_level`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='en')`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='fr')`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='de')`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='ru')`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='it')`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='sr')`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='sr-latn')`
- `test_dashboard.DashboardViewTests.test_spain_detail_translates_geography_labels_for_supported_languages (language_code='ar')`
- `test_dashboard.DashboardViewTests.test_spain_detail_uses_spanish_geography_labels`
- `test_dashboard.DashboardViewTests.test_visual_identity_detail_renders_entity_ficha_with_translations`
- `test_visual_assets.VisualAssetSeedingTests.test_assign_assets_from_scraped_data_wd_uses_values_sparql_instead_of_many_entitydata_calls`
- `test_visual_assets.VisualAssetSeedingTests.test_existing_generated_country_svg_is_preferred_and_tolerates_preamble`
- `test_visual_assets.VisualAssetSeedingTests.test_seed_visual_assets_uses_page_wikidata_batch_for_all_levels_by_default`
- `test_visual_assets.VisualAssetSeedingTests.test_svg_download_falls_back_to_thumbnail_png_after_429`
- `test_web_interface.ConfigSourceEntitiesViewTests.test_populated_config_only_shows_clear_when_rows_exist`
- `test_web_interface.ConfigSourceEntitiesViewTests.test_stop_config_task_returns_to_validated_state`
- `test_web_interface.ConfigSourceEntitiesViewTests.test_validate_command_clears_previous_config_error_on_success`
- `test_web_interface.DerivedSectionWebTests.test_derived_subdivision_build_button_queues_country_build`
- `test_web_interface.DerivedSectionWebTests.test_group_return_state_refreshes_stale_list_after_editor_changes`
- `test_web_interface.DerivedSectionWebTests.test_new_country_config_create_reparents_same_code_child_without_duplicate_error`
- `test_web_interface.DerivedSectionWebTests.test_new_country_config_create_supports_parent_and_derived_subdivision_children`
- `test_web_interface.DerivedSectionWebTests.test_new_country_config_delete_reparents_children_without_deleting_configs`
- `test_web_interface.DerivedSectionWebTests.test_new_country_detail_displays_created_subdivision_legal_units_from_stale_materialization`
- `test_web_interface.DerivedSectionWebTests.test_new_country_detail_shows_assigned_source_subdivision_rows`
- `test_web_interface.DerivedSectionWebTests.test_new_country_detail_uses_include_level_for_assigned_created_subdivisions`
- `test_web_interface.WebInterfaceHelperTests.test_can_scrape_matches_config_lifecycle (status='populated')`
- `test_web_interface.WebInterfaceHelperTests.test_user_facing_text_sources_are_not_mojibake (path='ciudades_del_mundo/management/commands/ensure_visual_assets.py')`

### Errores

- `test_visual_assets.SharedVisualAssetTests.test_share_visual_assets_for_country_admin_areas_copies_same_data_wd_assets`
- `test_visual_assets.VisualAssetSeedingTests.test_country_assets_fall_back_to_toml_commons_urls`
- `test_web_interface.ConfigSourceEntitiesViewTests.test_source_entities_use_configured_country_code_for_levels_and_parents`
- `test_scrape_resume.ScrapeResumeStoreAssetPhaseTests.test_tracks_populated_data_and_asset_page_checkpoints`
- `test_scraping_admin.CityPopulationCitiesScraperTests.test_citiesadmin_child_tbody_before_adm_parent_uses_matching_group`
- `test_scraping_admin.CityPopulationCitiesScraperTests.test_overseas_cities_pages_parse_only_contents_table (path_slug='reunion')`
- `test_scraping_admin.CityPopulationCitiesScraperTests.test_overseas_cities_pages_parse_only_contents_table (path_slug='guadeloupe')`

## Comandos de verificación

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
py -B manage.py check --settings=ciudades_del_mundo.tests.isolated_settings
py -B manage.py test ciudades_del_mundo.tests --settings=ciudades_del_mundo.tests.isolated_settings --noinput
py -B -m tools.build_web_assets --check
node --test tests/frontend/behavior.test.cjs
node --test tests/frontend/group_selection.test.cjs
py -B -m tools.verify_clean_copy
node --check ciudades_del_mundo/static/ciudades_del_mundo/app.js
```

`node --check` es opcional si Node está instalado; el ensamblado no requiere Node.
El harness `group_selection.test.cjs` es una comprobacion adicional del editor
inline; no forma parte de los recuentos historicos anteriores ni del comando
`verify_clean_copy` actual.

Los resultados locales detallados de esta ejecución y la copia de comparación
quedan bajo `.web_refactor/` (ignorado por Git), sin convertirse en datos de runtime.
