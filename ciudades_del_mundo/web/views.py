from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.shortcuts import render

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.models import AdminArea, Escanho, NuevoAdminArea


def dashboard(request):
    countries = (
        AdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    context = {
        "admin_area_count": AdminArea.objects.count(),
        "nuevo_area_count": NuevoAdminArea.objects.count(),
        "seat_count": Escanho.objects.count(),
        "countries": countries,
        "configs": PythonScrapingConfigRepository().list_configs(),
    }
    return render(request, "ciudades_del_mundo/dashboard.html", context)


def admin_area_list(request):
    areas = AdminArea.objects.select_related("parent").order_by("country_code", "level", "name")
    country = request.GET.get("country")
    level = request.GET.get("level")
    q = request.GET.get("q")

    if country:
        areas = areas.filter(country_code=country)
    if level not in (None, ""):
        areas = areas.filter(level=level)
    if q:
        areas = areas.filter(name__icontains=q)

    paginator = Paginator(areas, 50)
    context = {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "countries": AdminArea.objects.values_list("country_code", flat=True).distinct().order_by("country_code"),
        "selected_country": country or "",
        "selected_level": level or "",
        "query": q or "",
    }
    return render(request, "ciudades_del_mundo/admin_area_list.html", context)
