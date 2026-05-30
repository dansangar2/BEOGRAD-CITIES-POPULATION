"""URL routes for the lightweight project dashboard."""

from django.urls import path

from . import views

app_name = "ciudades_del_mundo"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("areas/", views.admin_area_list, name="admin_area_list"),
    path("configs/", views.config_list, name="config_list"),
    path("configs/new/", views.config_new, name="config_new"),
    path("configs/<slug:slug>/", views.config_edit, name="config_edit"),
    path("configs/<slug:slug>/task/<slug:action>/", views.start_config_task, name="start_config_task"),
    path("recipes/", views.recipe_list, name="recipe_list"),
    path("recipes/new/", views.recipe_new, name="recipe_new"),
    path("recipes/<slug:slug>/", views.recipe_edit, name="recipe_edit"),
    path("recipes/<slug:slug>/task/<slug:action>/", views.start_recipe_task, name="start_recipe_task"),
    path("derived/", views.nuevo_area_list, name="nuevo_area_list"),
    path("derived/<slug:country_id>/", views.nuevo_area_detail, name="nuevo_area_detail"),
    path("stats/", views.stats_view, name="stats"),
    path("delete/", views.data_delete, name="data_delete"),
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/<slug:task_id>/", views.task_detail, name="task_detail"),
    path("tasks/<slug:task_id>/cancel/", views.task_cancel, name="task_cancel"),
]
