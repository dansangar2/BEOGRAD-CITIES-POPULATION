"""CityPopulation scrapers with a keyword dispatcher and shared Utils.

This module provides three small entry points and delegates shared work to
`ciudades_del_mundo.utils.scraping_utils.Utils`:

- extraer_guardar_datos(url, code, province='', forceAdminTable=False, getAdmins=False)
- extraer_guardar_datos_basic(url, code)
- extraer_por_keyword(keyword, url, code, **kwargs)

Keep this file minimal: all HTML fetching/parsing and DB saves are done in Utils.
"""

from typing import Optional
import os
import sys

# Minimal Django setup so this file can be executed as a script
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ciudades_del_mundo.settings')
import django
django.setup()

from ciudades_del_mundo.models import Municipality
from ciudades_del_mundo.utils.scraping_utils import Utils


def extraer_guardar_datos(url: str, code: str, province: str = '', forceAdminTable: bool = False, getAdmins: bool = False) -> None:
    """Extract rows from admin-style tables and save Municipality objects.

    This function delegates common work to Utils and keeps only the table
    traversal logic here.
    """
    html = Utils.get_html_selenium(url)
    soup = Utils.soup_from_html(html)

    # Prefer adminareas table when present
    admin_section = soup.select_one('section#adminareas')
    if admin_section and not forceAdminTable:
        # Use the new parser to iterate rows
        # We'll track parent names by level using the first encountered id per level
        parent_name_by_level = {}
        for row in Utils.parse_admin_table(soup, base_level=1):
            lvl = row['level']
            _id = row['id']
            name = row['name']
            size = row['size']
            pop = row['population']
            lastDate = row['lastDate']

            # set parent/province: if level > 1, try to use the most recent name at level-1
            provincef = province
            if lvl > 1:
                provincef = parent_name_by_level.get(lvl - 1, provincef)

            # Save municipality (create a fake first_td-like object with id and text methods)
            class _FakeTD:
                def __init__(self, _id, text):
                    self._id = _id
                    self._text = text

                def get(self, k):
                    if k == 'id':
                        return self._id
                    return None

                def get_text(self, strip=True):
                    return self._text

            first_td = _FakeTD(_id, name)
            Utils.create_and_save_municipality(code, first_td, lastDate, provincef, size, pop)

            # update parent mapping for deeper levels
            parent_name_by_level[lvl] = name
        return

    # fallback to older citysection parsing when admin not found
    table = soup.find('section', id='citysection')
    if not table:
        print(f"❌ No se encontró ninguna tabla en la página en: {url}.")
        return


def extraer_guardar_datos_basic(url: str, code: str) -> None:
    """Extract data from an 'infosection' style page and save a Municipality."""
    html = Utils.get_html_selenium(url)
    soup = Utils.soup_from_html(html)
    table = soup.find('div', class_='infosection')
    if not table:
        print("❌ No se encontró ninguna sección 'infosection' en la página.")
        return

    ident = table.get('id')
    name_tag = table.find('p', attrs={'class': 'infoname'})
    name = name_tag.get_text(strip=True) if name_tag else ''
    data = table.find_all('p', attrs={'class': 'infotext'})

    try:
        popu = int(data[0].find('span', class_='val').get_text(strip=True).replace(',', '')) if data and data[0].find('span', class_='val') else 0
    except Exception:
        popu = 0
    try:
        size = float(data[1].find('span', class_='val').get_text(strip=True).split(' ')[0]) if len(data) > 1 and data[1].find('span', class_='val') else 0.0
    except Exception:
        size = 0.0

    lastDate = None
    try:
        lastDate = soup.find('section', id='adminareas').find_all('th', attrs={'data-coltype': 'pop'})[-1].get('data-coldate')
    except Exception:
        lastDate = None

    municipio = Municipality(
        id=f"{code}-{ident}",
        nombre=name,
        lastDate=lastDate,
        codeCountry=code,
        province=name,
        population=popu,
        size=size,
    )
    municipio.save()


def extraer_por_keyword(keyword: str, url: str, code: str, **kwargs) -> None:
    """Dispatch to the appropriate extraction function using keyword.

    - 'admin' -> extraer_guardar_datos
    - 'basic' -> extraer_guardar_datos_basic
    default -> extraer_guardar_datos
    """
    key = (keyword or '').lower()
    if key == 'basic':
        return extraer_guardar_datos_basic(url, code)
    return extraer_guardar_datos(url, code, **kwargs)


if __name__ == '__main__':
    print('Iniciando extracción... (usa extraer_por_keyword para dispatch)')