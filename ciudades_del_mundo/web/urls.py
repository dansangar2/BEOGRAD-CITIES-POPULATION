"""URL routes for the lightweight project dashboard."""

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "ciudades_del_mundo"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/countries/", views.api_country_summary, name="api_country_summary"),
    path("api/countries/<slug:country_code>/", views.api_country_detail, name="api_country_detail"),
    path("api/admin-areas/<path:area_id>/", views.api_admin_area_detail, name="api_admin_area_detail"),
    path("api/derived/", views.api_derived_summary, name="api_derived_summary"),
    path("api/tasks/", views.api_task_list, name="api_task_list"),
    path("api/tasks/<slug:task_id>/", views.api_task_detail, name="api_task_detail"),
    path("api/assets/<slug:entity_type>/<path:entity_key>/", views.api_visual_assets, name="api_visual_assets"),
    path("dashboard/population/", views.dashboard_population_data, name="dashboard_population_data"),
    path("dashboard/derived/", views.dashboard_derived_data, name="dashboard_derived_data"),
    path("dashboard/country/<slug:country_code>/", views.dashboard_country_detail, name="dashboard_country_detail"),
    path("areas/", views.admin_area_list, name="admin_area_list"),
    path("areas/table/", views.admin_area_table, name="admin_area_table"),
    path("configs/", views.config_list, name="config_list"),
    path("configs/table/", views.config_table, name="config_table"),
    path("configs/bootstrap/", views.config_bootstrap, name="config_bootstrap"),
    path("configs/tasks/table/", views.config_tasks_table, name="config_tasks_table"),
    path("configs/new/", views.config_new, name="config_new"),
    path("configs/all/task/<slug:action>/", views.start_all_config_task, name="start_all_config_task"),
    path("configs/<slug:slug>/", views.config_edit, name="config_edit"),
    path("configs/<slug:slug>/summary/", views.config_summary, name="config_summary"),
    path("configs/<slug:slug>/source-entities/", views.config_source_entities, name="config_source_entities"),
    path("configs/<slug:slug>/generate/", views.config_generate_base, name="config_generate_base"),
    path("configs/<slug:slug>/ai-login/", views.config_ai_login, name="config_ai_login"),
    path("configs/<slug:slug>/task/<slug:action>/", views.start_config_task, name="start_config_task"),
    path("recipes/", views.recipe_list, name="recipe_list"),
    path("recipes/new/", views.recipe_new, name="recipe_new"),
    path("recipes/<slug:slug>/", views.recipe_edit, name="recipe_edit"),
    path("recipes/<slug:slug>/task/<slug:action>/", views.start_recipe_task, name="start_recipe_task"),
    path("derived/", views.nuevo_area_list, name="nuevo_area_list"),
    path("derived/<slug:country_id>/", views.nuevo_area_detail, name="nuevo_area_detail"),
    path("derived/<slug:country_id>/table/", views.nuevo_area_table, name="nuevo_area_table"),
    path("map/<slug:source>/<path:area_id>/", views.area_map_detail, name="area_map_detail"),
    path("identity/<slug:kind>/<path:filename>/", views.visual_identity_detail, name="visual_identity_detail"),
    path("countries/", views.stats_view, name="countries"),
    path("stats/", RedirectView.as_view(pattern_name="ciudades_del_mundo:countries", permanent=False), name="stats"),
    path("stats/data/", views.stats_data, name="stats_data"),
    path("delete/", views.data_delete, name="data_delete"),
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/table/", views.task_table, name="task_table"),
    path("tasks/<slug:task_id>/", views.task_detail, name="task_detail"),
    path("tasks/<slug:task_id>/status/", views.task_status, name="task_status"),
    path("tasks/<slug:task_id>/cancel/", views.task_cancel, name="task_cancel"),
]
