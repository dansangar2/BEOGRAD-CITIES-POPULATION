"""Local Python routes and independent database readiness."""
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.urls import path, re_path
from django.views.static import serve
from ciudades_del_mundo.urls import urlpatterns as project_urls


def ready(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()
    return JsonResponse({'status': 'ready'})


urlpatterns = [
    path('internal/ready', ready),
    path('internal/ready/', ready),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    *project_urls,
]
