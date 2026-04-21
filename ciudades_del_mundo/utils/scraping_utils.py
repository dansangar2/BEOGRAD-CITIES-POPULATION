import time
import os
import sys
from typing import Optional
from bs4 import BeautifulSoup
import django

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ciudades_del_mundo.settings')
django.setup()



class Utils:
    """Utilidades compartidas para scraping: obtener HTML con Selenium,
    parsear con BeautifulSoup y crear/guardar objetos Municipality.
    """

    @staticmethod
    def get_html_selenium(url: str, wait: float = 1.0) -> str:
        """Inicia un webdriver Chrome, carga la URL y devuelve el page_source."""
        from selenium import webdriver

        driver = webdriver.Chrome()
        driver.get(url)
        time.sleep(wait)
        html = driver.page_source
        driver.quit()
        return html

    @staticmethod
    def soup_from_html(html: str, parser: str = "html.parser") -> BeautifulSoup:
        return BeautifulSoup(html, parser)

    @staticmethod
    def extract_last_visible_population_from_tdspop(tdspop, thspop, fallback_index=1) -> tuple[int, Optional[str]]:
        """Given lists of population tds and header ths, find last visible population value and its date."""
        index2 = fallback_index
        last_td = tdspop[-index2]
        lastDate = thspop[-index2]

        while last_td.get_text(strip=True).replace(",", "") == "...":
            index2 = index2 + 1
            if index2 >= len(tdspop):
                break
            last_td = tdspop[-index2]
            lastDate = thspop[-index2]

        pop = last_td.get_text(strip=True).replace(",", "")
        if pop == "...":
            pop = "0"

        return int(pop) if pop.isdigit() else 0, lastDate.get("data-coldate") if lastDate else None

    @staticmethod
    def parse_admin_table(soup, base_level: int = 1):
        """Parse a section#adminareas table and yield rows with level, id, name, province (parent), size and population.

        Yields dicts: { 'level': int, 'id': str|None, 'name': str, 'province': str, 'size': float, 'population': int, 'lastDate': str|None }
        """
        table = soup.select_one("section#adminareas table#tl, section#adminareas table#ts")
        if not table:
            return

        # header populations
        header_row = table.find('tr')
        thspop = header_row.find_all('th', class_='rpop', attrs={'data-coldate': True}) if header_row else []

        # iterate tbody blocks like tbody.admin1, tbody.admin2 ...
        for tbody in table.select('tbody'):
            classes = tbody.get('class', [])
            k = None
            for c in classes:
                if c.startswith('admin') and c[5:].isdigit():
                    k = int(c[5:])
                    break
            if k is None:
                continue

            target_level = base_level + (k - 1)

            for tr in tbody.select('tr'):
                td_rname = tr.select_one('td.rname')
                if not td_rname:
                    continue

                # id from td id attribute (like iAND) when present
                _id = td_rname.get('id')
                name = td_rname.get_text(strip=True)

                # size
                try:
                    if td_rname.get('data-area') is None:
                        size = 0.0
                    else:
                        size = float(td_rname.get('data-area'))
                except Exception:
                    size = 0.0

                # parent/province: try to infer via current_code_by_level is left to caller; supply empty for now
                province = ''

                tdspop = tr.select('td.rpop')
                try:
                    pop, lastDate = Utils.extract_last_visible_population_from_tdspop(tdspop, thspop, fallback_index=1)
                except Exception:
                    pop, lastDate = 0, None

                yield {
                    'level': target_level,
                    'id': _id,
                    'name': name,
                    'province': province,
                    'size': size,
                    'population': pop,
                    'lastDate': lastDate,
                    'tr': tr,
                }

    @staticmethod
    def create_and_save_municipality(code: str, first_td, lastDate, provincef: str, size: float, pop: int):
        # Import Municipality lazily to avoid requiring Django models at module import time
        try:
            from ciudades_del_mundo.models import Municipality
        except Exception:
            Municipality = None

        municipio = None
        if Municipality:
            municipio = Municipality(
                id = code + "-" + str(first_td.get("id")),
                nombre = first_td.get_text(strip=True),
                lastDate = lastDate,
                codeCountry = code,
                province = provincef,
                population = int(pop),
                size = size,
            )

        print("Creación del municipio en " + str(provincef) + " : \n" + str(municipio) + "\n\n")
        if municipio:
            municipio.save()
