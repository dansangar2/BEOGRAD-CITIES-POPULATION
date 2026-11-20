"""Explicit compatibility exports for the established public HTTP API.

Internal code and tests import owners directly; attributes are never forwarded.
"""

from .handlers.assets import (
    api_visual_assets,
    area_map_detail,
    visual_identity_detail,
    visual_identity_entity_detail,
)

from .handlers.configs import (
    config_ai_login,
    config_bootstrap,
    config_city_merge_import_toml,
    config_edit,
    config_editor_data,
    config_generate_base,
    config_import_toml,
    config_import_toml_slug,
    config_list,
    config_new,
    config_source_entities,
    config_summary,
    config_table,
    config_tasks_table,
    start_all_config_task,
    start_config_task,
)

from .handlers.dashboard import (
    admin_area_list,
    admin_area_table,
    api_admin_area_detail,
    api_country_detail,
    api_country_summary,
    api_derived_summary,
    api_new_area_detail,
    dashboard,
    dashboard_country_detail,
    dashboard_derived_data,
    dashboard_population_data,
    data_delete,
    nuevo_area_detail,
    nuevo_area_list,
    nuevo_area_table,
    stats_data,
    stats_view,
)

from .handlers.exports import (
    config_city_merge_export_toml,
    config_export_data_csv,
    config_export_toml,
    derived_subdivision_export_toml,
    group_city_export_toml,
    group_export_toml,
    new_country_detail_export_excel,
    new_country_detail_export_excel_legacy,
)

from .handlers.groups import (
    group_city_entry,
    group_city_import_toml,
    group_country_detail_data,
    group_edit,
    group_entry_edit,
    group_entry_new,
    group_import_toml,
    group_import_toml_slug,
    group_list,
    group_new,
    group_source_data,
)

from .handlers.new_countries import (
    api_new_country_config_node_detail,
    api_new_country_config_tree,
    api_new_country_container_detail,
    api_new_country_detail,
    api_new_country_summary,
    new_country_config_capital_options,
    new_country_config_clone,
    new_country_config_clone_legacy,
    new_country_config_created_entity_options,
    new_country_config_created_subdivision_options,
    new_country_config_delete,
    new_country_config_delete_legacy,
    new_country_config_edit,
    new_country_config_edit_legacy,
    new_country_config_new,
    new_country_config_new_legacy,
    new_country_config_source_area_options,
    new_country_detail,
    new_country_detail_legacy,
    new_country_import_toml,
    new_country_list,
    new_country_new,
    new_country_source_children,
    start_new_country_config_task,
    start_new_country_config_task_legacy,
)

from .handlers.recipes import (
    recipe_edit,
    recipe_list,
    recipe_new,
    start_recipe_task,
)

from .handlers.subdivisions import (
    derived_subdivision_build,
    derived_subdivision_capital_options,
    derived_subdivision_clone,
    derived_subdivision_country_detail_data,
    derived_subdivision_delete,
    derived_subdivision_edit,
    derived_subdivision_form_data,
    derived_subdivision_import_toml,
    derived_subdivision_list,
    derived_subdivision_new,
)

from .handlers.tasks import (
    api_task_detail,
    api_task_list,
    task_cancel,
    task_detail,
    task_list,
    task_status,
    task_table,
)

__all__ = [name for name in globals() if not name.startswith("_")]
