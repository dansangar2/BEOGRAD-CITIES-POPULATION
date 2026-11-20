"""HTTP parsing and error translation shared by handlers."""


from ciudades_del_mundo.web import view_support as _view_support
from functools import wraps

from django.http import Http404

from ciudades_del_mundo.ports.query_errors import DataNotFoundError


def missing_data_as_404(handler):
    @wraps(handler)
    def adapted(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except DataNotFoundError as exc:
            raise Http404(str(exc)) from exc
    return adapted


def query_params(request, *, list_fields=()):
    """Copy HTTP query values into ordinary data before invoking selectors."""
    params = request.GET.dict()
    for field in list_fields:
        params[field] = request.GET.getlist(field)
    return params


def _is_ajax(request) -> bool:
    return _wants_json(request)


def _wants_json(request) -> bool:
    accept = request.headers.get("accept", "")
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in accept


def _querystring_without_page(request) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    return f"{encoded}&" if encoded else ""


def _country_browser_page_size(request) -> int:
    return _page_size(
        request,
        default=_view_support.COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
        max_value=_view_support.COUNTRY_BROWSER_MAX_PAGE_SIZE,
    )


def _page_size(request, *, default: int, max_value: int = 200) -> int:
    try:
        value = int(request.GET.get("page_size") or default)
    except (TypeError, ValueError):
        return default
    return max(10, min(value, max_value))


def _new_country_option_page_size(request) -> int:
    return _page_size(
        request,
        default=_view_support.NEW_COUNTRY_OPTION_DEFAULT_PAGE_SIZE,
        max_value=_view_support.NEW_COUNTRY_OPTION_MAX_PAGE_SIZE,
    )
