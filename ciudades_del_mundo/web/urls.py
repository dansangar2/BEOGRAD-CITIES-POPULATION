from django.urls import path

from . import views

app_name = "ciudades_del_mundo"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("areas/", views.admin_area_list, name="admin_area_list"),
]
