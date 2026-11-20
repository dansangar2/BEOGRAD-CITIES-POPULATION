"""HTTP transport for explicitly requested CityPopulation config discovery."""
from urllib.request import Request, urlopen


def read_discovery_html(url: str, *, timeout: int, user_agent: str) -> str:
    request = Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")
