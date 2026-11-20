# Inventario histórico de rutas para la migración Java

> Estado vigente: Python está restaurado. En modo Java las cinco rutas migradas
> se reenvían desde Django; las pendientes mantienen su propietario Django.
> En modo Python todas conservan su implementación Django. Consulta
> [selección y operación](python-http-retirement.md).
>
> La tabla y el JSON siguientes conservan la fotografía de la retirada HTTP.
> «Deshabilitada» describe aquella etapa, no el estado actual. El generador
> todavía lee `old-backend-python/`; regenerarlo reproduce evidencia histórica.
> La cobertura activa se consulta en `web/java_routing.py`, `JAVA_ROUTE_NAMES`.

Evidencia estática regenerable con `py -B tools/inventory_java_api.py --output docs/architecture/java-api-inventory.json --markdown docs/architecture/java-api-inventory.md`.

La fotografía histórica recoge 107 rutas Python. Durante la retirada, las rutas
pendientes quedaron deshabilitadas y Java sirvió sus cinco rutas implementadas,
además de las comprobaciones operativas y media. Las pruebas Java usan contratos
congelados con nueve idiomas y siete métodos HTTP sin ejecutar Django.

Los detalles de entradas, respuestas, errores, middleware, llamadas, consumidores y referencias de pruebas están en [el JSON](java-api-inventory.json). `sin restricción observada` significa que el controlador no limita el método; CSRF se aplica a métodos inseguros. Las ramas son comprobaciones estáticas, no una declaración OpenAPI inferida.

| URL | Métodos/ramas observados | Tipo de respuesta | Controlador | Java |
| --- | --- | --- | --- | --- |
| `/` | sin restricción observada | html | `dashboard.dashboard` | Deshabilitada |
| `/api/countries/` | sin restricción observada | json | `dashboard.api_country_summary` | Implementado en Java |
| `/api/countries/<slug:country_code>/` | sin restricción observada | delegated-helper; inspect owner | `dashboard.api_country_detail` | Deshabilitada |
| `/api/admin-areas/<path:area_id>/` | sin restricción observada | json | `dashboard.api_admin_area_detail` | Deshabilitada |
| `/api/new-country-containers/` | sin restricción observada | json | `new_countries.api_new_country_summary` | Deshabilitada |
| `/api/new-country-containers/<slug:country_slug>/config-tree/` | sin restricción observada | json | `new_countries.api_new_country_config_tree` | Deshabilitada |
| `/api/new-country-containers/<slug:country_slug>/config-nodes/<path:tree_id>/` | sin restricción observada | json | `new_countries.api_new_country_config_node_detail` | Deshabilitada |
| `/api/new-country-containers/<slug:country_slug>/` | sin restricción observada | json | `new_countries.api_new_country_container_detail` | Deshabilitada |
| `/api/new-countries/<slug:country_id>/` | sin restricción observada | json | `new_countries.api_new_country_detail` | Deshabilitada |
| `/api/new-areas/<path:area_id>/` | sin restricción observada | json | `dashboard.api_new_area_detail` | Deshabilitada |
| `/api/derived/` | sin restricción observada | json | `dashboard.api_derived_summary` | Implementado en Java |
| `/api/tasks/` | sin restricción observada | json | `tasks.api_task_list` | Deshabilitada |
| `/api/tasks/<slug:task_id>/` | sin restricción observada | json | `tasks.api_task_detail` | Deshabilitada |
| `/api/assets/<slug:entity_type>/<path:entity_key>/` | sin restricción observada | json | `assets.api_visual_assets` | Deshabilitada |
| `/dashboard/population/` | sin restricción observada | json | `dashboard.dashboard_population_data` | Implementado en Java |
| `/dashboard/derived/` | sin restricción observada | json | `dashboard.dashboard_derived_data` | Implementado en Java |
| `/dashboard/country/<slug:country_code>/` | sin restricción observada | delegated-helper; inspect owner | `dashboard.dashboard_country_detail` | Deshabilitada |
| `/areas/` | sin restricción observada | html | `dashboard.admin_area_list` | Deshabilitada |
| `/areas/table/` | sin restricción observada | html | `dashboard.admin_area_table` | Deshabilitada |
| `/configs/` | sin restricción observada | html | `configs.config_list` | Deshabilitada |
| `/configs/table/` | sin restricción observada | html | `configs.config_table` | Deshabilitada |
| `/configs/bootstrap/` | GET, POST | json | `configs.config_bootstrap` | Deshabilitada |
| `/configs/import-toml/` | POST | json, redirect | `configs.config_import_toml` | Deshabilitada |
| `/configs/tasks/table/` | sin restricción observada | html | `configs.config_tasks_table` | Deshabilitada |
| `/configs/new/` | POST | json, html, redirect | `configs.config_new` | Deshabilitada |
| `/configs/all/task/<slug:action>/` | POST | json, redirect | `configs.start_all_config_task` | Deshabilitada |
| `/configs/<slug:slug>/export-toml/` | POST | json, redirect | `exports.config_export_toml` | Deshabilitada |
| `/configs/<slug:slug>/import-toml/` | POST | json, redirect | `configs.config_import_toml_slug` | Deshabilitada |
| `/configs/<slug:slug>/cities/export-toml/` | POST | json, redirect | `exports.config_city_merge_export_toml` | Deshabilitada |
| `/configs/<slug:slug>/cities/import-toml/` | POST | json, redirect | `configs.config_city_merge_import_toml` | Deshabilitada |
| `/configs/<slug:slug>/export-data-csv/` | sin restricción observada | download | `exports.config_export_data_csv` | Implementado en Java |
| `/configs/<slug:slug>/editor-data/` | sin restricción observada | json | `configs.config_editor_data` | Deshabilitada |
| `/configs/<slug:slug>/` | POST | json, html, redirect | `configs.config_edit` | Deshabilitada |
| `/configs/<slug:slug>/summary/` | sin restricción observada | json | `configs.config_summary` | Deshabilitada |
| `/configs/<slug:slug>/source-entities/` | sin restricción observada | json | `configs.config_source_entities` | Deshabilitada |
| `/configs/<slug:slug>/generate/` | POST | json | `configs.config_generate_base` | Deshabilitada |
| `/configs/<slug:slug>/ai-login/` | sin restricción observada | redirect | `configs.config_ai_login` | Deshabilitada |
| `/configs/<slug:slug>/task/<slug:action>/` | POST | json, redirect | `configs.start_config_task` | Deshabilitada |
| `/recipes/` | sin restricción observada | html | `recipes.recipe_list` | Deshabilitada |
| `/recipes/new/` | POST | html, redirect | `recipes.recipe_new` | Deshabilitada |
| `/recipes/<slug:slug>/` | POST | html, redirect | `recipes.recipe_edit` | Deshabilitada |
| `/recipes/<slug:slug>/task/<slug:action>/` | POST | json, redirect | `recipes.start_recipe_task` | Deshabilitada |
| `/new-countries/` | sin restricción observada | html | `new_countries.new_country_list` | Deshabilitada |
| `/new-countries/import-toml/` | POST | json, redirect | `new_countries.new_country_import_toml` | Deshabilitada |
| `/new-countries/source-children/` | sin restricción observada | json | `new_countries.new_country_source_children` | Deshabilitada |
| `/new-countries/new/` | POST | html, redirect | `new_countries.new_country_new` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/new/` | POST | html, redirect | `new_countries.new_country_new` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/` | POST | html, redirect | `new_countries.new_country_detail` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/export-excel/` | GET | download | `exports.new_country_detail_export_excel` | Deshabilitada |
| `/new-countries/<slug:country_slug>/export-excel/` | GET | redirect | `exports.new_country_detail_export_excel_legacy` | Deshabilitada |
| `/new-countries/<slug:country_slug>/` | sin restricción observada | redirect | `new_countries.new_country_detail_legacy` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/` | POST | html, redirect | `new_countries.new_country_config_new` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/capital-options/` | POST | json | `new_countries.new_country_config_capital_options` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/created-subdivisions/` | GET | json | `new_countries.new_country_config_created_subdivision_options` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/source-areas/` | GET | json | `new_countries.new_country_config_source_area_options` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/created-entities/` | GET | json | `new_countries.new_country_config_created_entity_options` | Deshabilitada |
| `/new-countries/<slug:country_slug>/configs/new/` | sin restricción observada | redirect | `new_countries.new_country_config_new_legacy` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/created-entities/` | GET | json | `new_countries.new_country_config_created_entity_options` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/source-areas/` | GET | json | `new_countries.new_country_config_source_area_options` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/capital-options/` | POST | json | `new_countries.new_country_config_capital_options` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/delete/` | POST | json, redirect | `new_countries.new_country_config_delete` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/clone/` | POST | json, redirect | `new_countries.new_country_config_clone` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/` | POST | html, redirect | `new_countries.new_country_config_edit` | Deshabilitada |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/delete/` | sin restricción observada | delegated-helper; inspect owner | `new_countries.new_country_config_delete_legacy` | Deshabilitada |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/clone/` | sin restricción observada | delegated-helper; inspect owner | `new_countries.new_country_config_clone_legacy` | Deshabilitada |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/` | sin restricción observada | redirect | `new_countries.new_country_config_edit_legacy` | Deshabilitada |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/task/<slug:action>/` | POST | json, redirect | `new_countries.start_new_country_config_task` | Deshabilitada |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/task/<slug:action>/` | sin restricción observada | redirect | `new_countries.start_new_country_config_task_legacy` | Deshabilitada |
| `/groups/` | sin restricción observada | html | `groups.group_list` | Deshabilitada |
| `/groups/import-toml/` | POST | json, redirect | `groups.group_import_toml` | Deshabilitada |
| `/groups/source-data/` | POST | json | `groups.group_source_data` | Deshabilitada |
| `/groups/countries/<slug:country_code>/data/` | GET | json | `groups.group_country_detail_data` | Deshabilitada |
| `/groups/new/` | sin restricción observada | redirect | `groups.group_new` | Deshabilitada |
| `/groups/groups/<slug:country_code>/new/` | sin restricción observada | delegated-helper; inspect owner | `groups.group_entry_new` | Deshabilitada |
| `/groups/groups/<slug:country_code>/<slug:group_slug>/` | sin restricción observada | delegated-helper; inspect owner | `groups.group_entry_edit` | Deshabilitada |
| `/groups/cities/<slug:country_code>/import-toml/` | POST | redirect | `groups.group_city_import_toml` | Deshabilitada |
| `/groups/cities/<slug:country_code>/export-toml/` | POST | download, redirect | `exports.group_city_export_toml` | Deshabilitada |
| `/groups/cities/<slug:country_code>/<slug:city_slug>/` | sin restricción observada | delegated-helper; inspect owner | `groups.group_city_entry` | Deshabilitada |
| `/groups/subdivisions/<slug:country_code>/new/` | sin restricción observada | delegated-helper; inspect owner | `subdivisions.derived_subdivision_new` | Deshabilitada |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/data/` | sin restricción observada | json | `subdivisions.derived_subdivision_form_data` | Deshabilitada |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/capital-options/` | POST | json | `subdivisions.derived_subdivision_capital_options` | Deshabilitada |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/delete/` | POST | json, redirect | `subdivisions.derived_subdivision_delete` | Deshabilitada |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/clone/` | POST | json, redirect | `subdivisions.derived_subdivision_clone` | Deshabilitada |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/` | GET | delegated-helper; inspect owner | `subdivisions.derived_subdivision_edit` | Deshabilitada |
| `/groups/<slug:slug>/export-toml/` | POST | json, redirect | `exports.group_export_toml` | Deshabilitada |
| `/groups/<slug:slug>/import-toml/` | POST | json, redirect | `groups.group_import_toml_slug` | Deshabilitada |
| `/groups/<slug:slug>/` | sin restricción observada | redirect | `groups.group_edit` | Deshabilitada |
| `/subdivisions/` | sin restricción observada | html | `subdivisions.derived_subdivision_list` | Deshabilitada |
| `/subdivisions/countries/<slug:country_code>/data/` | GET | json | `subdivisions.derived_subdivision_country_detail_data` | Deshabilitada |
| `/subdivisions/import-toml/` | POST | json, redirect | `subdivisions.derived_subdivision_import_toml` | Deshabilitada |
| `/subdivisions/<slug:slug>/export-toml/` | POST | json, redirect | `exports.derived_subdivision_export_toml` | Deshabilitada |
| `/subdivisions/<slug:country_code>/build/` | POST | json, redirect | `subdivisions.derived_subdivision_build` | Deshabilitada |
| `/derived/` | sin restricción observada | html | `dashboard.nuevo_area_list` | Deshabilitada |
| `/derived/<slug:country_id>/` | sin restricción observada | html | `dashboard.nuevo_area_detail` | Deshabilitada |
| `/derived/<slug:country_id>/table/` | sin restricción observada | html | `dashboard.nuevo_area_table` | Deshabilitada |
| `/map/<slug:source>/<path:area_id>/` | sin restricción observada | html | `assets.area_map_detail` | Deshabilitada |
| `/identity/<slug:kind>/entity/<slug:entity_type>/<path:entity_key>/` | sin restricción observada | delegated-helper; inspect owner | `assets.visual_identity_entity_detail` | Deshabilitada |
| `/identity/<slug:kind>/<path:filename>/` | sin restricción observada | delegated-helper; inspect owner | `assets.visual_identity_detail` | Deshabilitada |
| `/countries/` | sin restricción observada | html | `dashboard.stats_view` | Deshabilitada |
| `/stats/` | sin restricción observada | redirect | `RedirectView.as_view(pattern_name='ciudades_del_mundo:countries', permanent=False)` | Deshabilitada |
| `/stats/data/` | sin restricción observada | json | `dashboard.stats_data` | Deshabilitada |
| `/delete/` | POST | html, redirect | `dashboard.data_delete` | Deshabilitada |
| `/tasks/` | sin restricción observada | html | `tasks.task_list` | Deshabilitada |
| `/tasks/table/` | sin restricción observada | html | `tasks.task_table` | Deshabilitada |
| `/tasks/<slug:task_id>/` | sin restricción observada | html | `tasks.task_detail` | Deshabilitada |
| `/tasks/<slug:task_id>/status/` | sin restricción observada | json | `tasks.task_status` | Deshabilitada |
| `/tasks/<slug:task_id>/cancel/` | POST | json, redirect | `tasks.task_cancel` | Deshabilitada |
