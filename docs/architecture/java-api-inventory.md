# Inventario de rutas de los backends Java y Python

Evidencia estática regenerable con `py -B tools/inventory_java_api.py --output docs/architecture/java-api-inventory.json --markdown docs/architecture/java-api-inventory.md`.

108 rutas Python mantenidas. Python sirve todas las rutas; en modo Java se reenvían 5 rutas de la allowlist a la JVM. Las pendientes mantienen su propietario Django. La migración Java continúa incompleta. Este inventario es evidencia estática; no acredita equivalencia en ejecución.

Los detalles de entradas, respuestas, errores, middleware, llamadas, consumidores y referencias de pruebas están en [el JSON](java-api-inventory.json). `sin restricción observada` significa que el controlador no limita el método; CSRF se aplica a métodos inseguros. Las ramas son comprobaciones estáticas, no una declaración OpenAPI inferida.

| URL | Métodos/ramas observados | Tipo de respuesta | Controlador | Java |
| --- | --- | --- | --- | --- |
| `/` | sin restricción observada | html | `dashboard.dashboard` | Django; implementación Java pendiente |
| `/api/countries/` | sin restricción observada | json | `dashboard.api_country_summary` | Implementado en Java |
| `/api/countries/<slug:country_code>/` | sin restricción observada | delegated-helper; inspect owner | `dashboard.api_country_detail` | Django; implementación Java pendiente |
| `/api/admin-areas/<path:area_id>/` | sin restricción observada | json | `dashboard.api_admin_area_detail` | Django; implementación Java pendiente |
| `/api/new-country-containers/` | sin restricción observada | json | `new_countries.api_new_country_summary` | Django; implementación Java pendiente |
| `/api/new-country-containers/<slug:country_slug>/config-tree/` | sin restricción observada | json | `new_countries.api_new_country_config_tree` | Django; implementación Java pendiente |
| `/api/new-country-containers/<slug:country_slug>/config-nodes/<path:tree_id>/` | sin restricción observada | json | `new_countries.api_new_country_config_node_detail` | Django; implementación Java pendiente |
| `/api/new-country-containers/<slug:country_slug>/` | sin restricción observada | json | `new_countries.api_new_country_container_detail` | Django; implementación Java pendiente |
| `/api/new-countries/<slug:country_id>/` | sin restricción observada | json | `new_countries.api_new_country_detail` | Django; implementación Java pendiente |
| `/api/new-areas/<path:area_id>/` | sin restricción observada | json | `dashboard.api_new_area_detail` | Django; implementación Java pendiente |
| `/api/derived/` | sin restricción observada | json | `dashboard.api_derived_summary` | Implementado en Java |
| `/api/tasks/` | sin restricción observada | json | `tasks.api_task_list` | Django; implementación Java pendiente |
| `/api/tasks/<slug:task_id>/` | sin restricción observada | json | `tasks.api_task_detail` | Django; implementación Java pendiente |
| `/api/assets/<slug:entity_type>/<path:entity_key>/` | sin restricción observada | json | `assets.api_visual_assets` | Django; implementación Java pendiente |
| `/dashboard/population/` | sin restricción observada | json | `dashboard.dashboard_population_data` | Implementado en Java |
| `/dashboard/derived/` | sin restricción observada | json | `dashboard.dashboard_derived_data` | Implementado en Java |
| `/dashboard/country/<slug:country_code>/` | sin restricción observada | delegated-helper; inspect owner | `dashboard.dashboard_country_detail` | Django; implementación Java pendiente |
| `/areas/` | sin restricción observada | html | `dashboard.admin_area_list` | Django; implementación Java pendiente |
| `/areas/table/` | sin restricción observada | html | `dashboard.admin_area_table` | Django; implementación Java pendiente |
| `/configs/` | sin restricción observada | html | `configs.config_list` | Django; implementación Java pendiente |
| `/configs/table/` | sin restricción observada | html | `configs.config_table` | Django; implementación Java pendiente |
| `/configs/bootstrap/` | GET, POST | json | `configs.config_bootstrap` | Django; implementación Java pendiente |
| `/configs/import-toml/` | POST | json, redirect | `configs.config_import_toml` | Django; implementación Java pendiente |
| `/configs/tasks/table/` | sin restricción observada | html | `configs.config_tasks_table` | Django; implementación Java pendiente |
| `/configs/new/` | POST | json, html, redirect | `configs.config_new` | Django; implementación Java pendiente |
| `/configs/all/task/<slug:action>/` | POST | json, redirect | `configs.start_all_config_task` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/export-toml/` | POST | json, redirect | `exports.config_export_toml` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/import-toml/` | POST | json, redirect | `configs.config_import_toml_slug` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/cities/export-toml/` | POST | json, redirect | `exports.config_city_merge_export_toml` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/cities/import-toml/` | POST | json, redirect | `configs.config_city_merge_import_toml` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/export-data-csv/` | sin restricción observada | download | `exports.config_export_data_csv` | Implementado en Java |
| `/configs/<slug:slug>/editor-data/` | sin restricción observada | json | `configs.config_editor_data` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/` | POST | json, html, redirect | `configs.config_edit` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/summary/` | sin restricción observada | json | `configs.config_summary` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/source-entities/` | sin restricción observada | json | `configs.config_source_entities` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/generate/` | POST | json | `configs.config_generate_base` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/ai-login/` | sin restricción observada | redirect | `configs.config_ai_login` | Django; implementación Java pendiente |
| `/configs/<slug:slug>/task/<slug:action>/` | POST | json, redirect | `configs.start_config_task` | Django; implementación Java pendiente |
| `/recipes/` | sin restricción observada | html | `recipes.recipe_list` | Django; implementación Java pendiente |
| `/recipes/new/` | POST | html, redirect | `recipes.recipe_new` | Django; implementación Java pendiente |
| `/recipes/<slug:slug>/` | POST | html, redirect | `recipes.recipe_edit` | Django; implementación Java pendiente |
| `/recipes/<slug:slug>/task/<slug:action>/` | POST | json, redirect | `recipes.start_recipe_task` | Django; implementación Java pendiente |
| `/new-countries/` | sin restricción observada | html | `new_countries.new_country_list` | Django; implementación Java pendiente |
| `/new-countries/import-toml/` | POST | json, redirect | `new_countries.new_country_import_toml` | Django; implementación Java pendiente |
| `/new-countries/export-toml/` | POST | json, redirect | `new_countries.new_country_export_toml` | Django; implementación Java pendiente |
| `/new-countries/source-children/` | sin restricción observada | json | `new_countries.new_country_source_children` | Django; implementación Java pendiente |
| `/new-countries/new/` | POST | html, redirect | `new_countries.new_country_new` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/new/` | POST | html, redirect | `new_countries.new_country_new` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/` | POST | html, redirect | `new_countries.new_country_detail` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/export-excel/` | GET | download | `exports.new_country_detail_export_excel` | Django; implementación Java pendiente |
| `/new-countries/<slug:country_slug>/export-excel/` | GET | redirect | `exports.new_country_detail_export_excel_legacy` | Django; implementación Java pendiente |
| `/new-countries/<slug:country_slug>/` | sin restricción observada | redirect | `new_countries.new_country_detail_legacy` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/` | POST | json, html, redirect | `new_countries.new_country_config_new` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/capital-options/` | POST | json | `new_countries.new_country_config_capital_options` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/created-subdivisions/` | GET | json | `new_countries.new_country_config_created_subdivision_options` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/source-areas/` | GET | json | `new_countries.new_country_config_source_area_options` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/new/created-entities/` | GET | json | `new_countries.new_country_config_created_entity_options` | Django; implementación Java pendiente |
| `/new-countries/<slug:country_slug>/configs/new/` | sin restricción observada | redirect | `new_countries.new_country_config_new_legacy` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/created-entities/` | GET | json | `new_countries.new_country_config_created_entity_options` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/source-areas/` | GET | json | `new_countries.new_country_config_source_area_options` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/capital-options/` | POST | json | `new_countries.new_country_config_capital_options` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/delete/` | POST | json, redirect | `new_countries.new_country_config_delete` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/clone/` | POST | json, redirect | `new_countries.new_country_config_clone` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/` | POST | html, redirect | `new_countries.new_country_config_edit` | Django; implementación Java pendiente |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/delete/` | sin restricción observada | delegated-helper; inspect owner | `new_countries.new_country_config_delete_legacy` | Django; implementación Java pendiente |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/clone/` | sin restricción observada | delegated-helper; inspect owner | `new_countries.new_country_config_clone_legacy` | Django; implementación Java pendiente |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/` | sin restricción observada | redirect | `new_countries.new_country_config_edit_legacy` | Django; implementación Java pendiente |
| `/new-countries/<slug:source_country_code>/<slug:country_slug>/configs/<slug:config_slug>/task/<slug:action>/` | POST | json, redirect | `new_countries.start_new_country_config_task` | Django; implementación Java pendiente |
| `/new-countries/<slug:country_slug>/configs/<slug:config_slug>/task/<slug:action>/` | sin restricción observada | redirect | `new_countries.start_new_country_config_task_legacy` | Django; implementación Java pendiente |
| `/groups/` | sin restricción observada | html | `groups.group_list` | Django; implementación Java pendiente |
| `/groups/import-toml/` | POST | json, redirect | `groups.group_import_toml` | Django; implementación Java pendiente |
| `/groups/source-data/` | POST | json | `groups.group_source_data` | Django; implementación Java pendiente |
| `/groups/countries/<slug:country_code>/data/` | GET | json | `groups.group_country_detail_data` | Django; implementación Java pendiente |
| `/groups/new/` | sin restricción observada | redirect | `groups.group_new` | Django; implementación Java pendiente |
| `/groups/groups/<slug:country_code>/new/` | sin restricción observada | delegated-helper; inspect owner | `groups.group_entry_new` | Django; implementación Java pendiente |
| `/groups/groups/<slug:country_code>/<slug:group_slug>/` | sin restricción observada | delegated-helper; inspect owner | `groups.group_entry_edit` | Django; implementación Java pendiente |
| `/groups/cities/<slug:country_code>/import-toml/` | POST | redirect | `groups.group_city_import_toml` | Django; implementación Java pendiente |
| `/groups/cities/<slug:country_code>/export-toml/` | POST | download, redirect | `exports.group_city_export_toml` | Django; implementación Java pendiente |
| `/groups/cities/<slug:country_code>/<slug:city_slug>/` | sin restricción observada | delegated-helper; inspect owner | `groups.group_city_entry` | Django; implementación Java pendiente |
| `/groups/subdivisions/<slug:country_code>/new/` | sin restricción observada | delegated-helper; inspect owner | `subdivisions.derived_subdivision_new` | Django; implementación Java pendiente |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/data/` | sin restricción observada | json | `subdivisions.derived_subdivision_form_data` | Django; implementación Java pendiente |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/capital-options/` | POST | json | `subdivisions.derived_subdivision_capital_options` | Django; implementación Java pendiente |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/delete/` | POST | json, redirect | `subdivisions.derived_subdivision_delete` | Django; implementación Java pendiente |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/clone/` | POST | json, redirect | `subdivisions.derived_subdivision_clone` | Django; implementación Java pendiente |
| `/groups/subdivisions/<slug:country_code>/<slug:subdivision_slug>/` | GET | delegated-helper; inspect owner | `subdivisions.derived_subdivision_edit` | Django; implementación Java pendiente |
| `/groups/<slug:slug>/export-toml/` | POST | json, redirect | `exports.group_export_toml` | Django; implementación Java pendiente |
| `/groups/<slug:slug>/import-toml/` | POST | json, redirect | `groups.group_import_toml_slug` | Django; implementación Java pendiente |
| `/groups/<slug:slug>/` | sin restricción observada | redirect | `groups.group_edit` | Django; implementación Java pendiente |
| `/subdivisions/` | sin restricción observada | html | `subdivisions.derived_subdivision_list` | Django; implementación Java pendiente |
| `/subdivisions/countries/<slug:country_code>/data/` | GET | json | `subdivisions.derived_subdivision_country_detail_data` | Django; implementación Java pendiente |
| `/subdivisions/import-toml/` | POST | json, redirect | `subdivisions.derived_subdivision_import_toml` | Django; implementación Java pendiente |
| `/subdivisions/<slug:slug>/export-toml/` | POST | json, redirect | `exports.derived_subdivision_export_toml` | Django; implementación Java pendiente |
| `/subdivisions/<slug:country_code>/build/` | POST | json, redirect | `subdivisions.derived_subdivision_build` | Django; implementación Java pendiente |
| `/derived/` | sin restricción observada | html | `dashboard.nuevo_area_list` | Django; implementación Java pendiente |
| `/derived/<slug:country_id>/` | sin restricción observada | html | `dashboard.nuevo_area_detail` | Django; implementación Java pendiente |
| `/derived/<slug:country_id>/table/` | sin restricción observada | html | `dashboard.nuevo_area_table` | Django; implementación Java pendiente |
| `/map/<slug:source>/<path:area_id>/` | sin restricción observada | html | `assets.area_map_detail` | Django; implementación Java pendiente |
| `/identity/<slug:kind>/entity/<slug:entity_type>/<path:entity_key>/` | sin restricción observada | delegated-helper; inspect owner | `assets.visual_identity_entity_detail` | Django; implementación Java pendiente |
| `/identity/<slug:kind>/<path:filename>/` | sin restricción observada | delegated-helper; inspect owner | `assets.visual_identity_detail` | Django; implementación Java pendiente |
| `/countries/` | sin restricción observada | html | `dashboard.stats_view` | Django; implementación Java pendiente |
| `/stats/` | sin restricción observada | redirect | `RedirectView.as_view(pattern_name='ciudades_del_mundo:countries', permanent=False)` | Django; implementación Java pendiente |
| `/stats/data/` | sin restricción observada | json | `dashboard.stats_data` | Django; implementación Java pendiente |
| `/delete/` | POST | html, redirect | `dashboard.data_delete` | Django; implementación Java pendiente |
| `/tasks/` | sin restricción observada | html | `tasks.task_list` | Django; implementación Java pendiente |
| `/tasks/table/` | sin restricción observada | html | `tasks.task_table` | Django; implementación Java pendiente |
| `/tasks/<slug:task_id>/` | sin restricción observada | html | `tasks.task_detail` | Django; implementación Java pendiente |
| `/tasks/<slug:task_id>/status/` | sin restricción observada | json | `tasks.task_status` | Django; implementación Java pendiente |
| `/tasks/<slug:task_id>/cancel/` | POST | json, redirect | `tasks.task_cancel` | Django; implementación Java pendiente |
