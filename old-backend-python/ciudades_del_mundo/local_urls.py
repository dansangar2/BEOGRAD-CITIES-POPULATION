"""Local deployment routes, including legacy media with DEBUG disabled."""
import json
from urllib.request import ProxyHandler, build_opener
from urllib.error import URLError
from django.conf import settings
from django.http import JsonResponse
from django.urls import path, re_path
from django.views.static import serve
from ciudades_del_mundo.urls import urlpatterns as project_urls


def ready(request):
    try:
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{settings.BEOGRAD_JAVA_PORT}/internal/ready', timeout=2) as response:
            available = response.status == 200 and json.load(response).get('status') == 'ready'
    except (URLError, OSError, ValueError):
        available = False
    return JsonResponse({'status': 'ready' if available else 'unavailable'}, status=200 if available else 503)


urlpatterns = [
    path('internal/ready/', ready),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    *project_urls,
]
