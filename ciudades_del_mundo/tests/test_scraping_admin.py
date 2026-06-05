import unittest

from bs4 import BeautifulSoup

from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationAutoScraper,
    CityPopulationPageType,
    detect_citypopulation_page_profile,
)
from ciudades_del_mundo.infrastructure.scraping.table import CityPopulationStructuredTableScraper


class CityPopulationAdminScraperTests(unittest.TestCase):
    def test_scrape_html_parses_root_visible_population_and_parent_stack(self):
        html = """
        <html>
          <body>
            <div class="infosection mainsection">
              <div class="infoname">Contents: Testland</div>
              <div class="infotext">Country</div>
              <span data-newpop="1000" data-newdate="2024-01-01" data-area="10" data-density="100"></span>
            </div>
            <table id="tl">
              <thead>
                <tr>
                  <th class="rpop" data-coldate="2000-01-01">2000</th>
                  <th class="rpop" data-coldate="2020-01-01" style="display: table-cell">2020</th>
                </tr>
              </thead>
              <tbody class="admin1">
                <tr>
                  <td class="rname" id="iA" data-area="2.5"><span itemprop="name">Alpha</span></td>
                  <td class="rstatus">Region</td>
                  <td class="rpop">50</td>
                  <td class="rpop">150</td>
                </tr>
              </tbody>
              <tbody class="admin2">
                <tr>
                  <td class="rname" id="iAA" data-density="50"><span itemprop="name">Alpha City</span></td>
                  <td class="rstatus">City</td>
                  <td class="rarea">3.00</td>
                  <td class="rpop">20</td>
                  <td class="rpop">300</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """

        scraper = CityPopulationAdminScraper()
        entities = scraper.scrape_html(
            html=html,
            url="https://www.citypopulation.de/en/test/admin/",
            country_code="test",
            level=0,
        )

        self.assertEqual([entity.code for entity in entities], ["test", "A", "AA"])
        root, alpha, alpha_city = entities
        self.assertEqual(root.name, "Testland")
        self.assertEqual(root.entity_type, "Country")
        self.assertEqual(root.pop_latest, 1000)
        self.assertEqual(root.last_census_year, 2024)
        self.assertEqual(alpha.parent_code, "test")
        self.assertEqual(alpha.pop_latest, 150)
        self.assertEqual(alpha.pop_latest_date, "2020-01-01")
        self.assertEqual(alpha_city.parent_code, "A")
        self.assertEqual(alpha_city.area_km2, 3.0)
        self.assertEqual(alpha_city.density, 50.0)


class CityPopulationStructuredTableScraperTests(unittest.TestCase):
    CEUTA_SINGLE_TS_HTML = """
    <html>
      <body>
        <header class="cpage">
          <h1><span itemprop="name">Ceuta</span></h1>
          <p itemprop="description">Autonomous City of Ceuta</p>
        </header>
        <table id="ts">
          <thead>
            <tr>
              <th class="rname">Name</th>
              <th class="rstatus">Status</th>
              <th class="rpop" data-coldate="2025-01-01">2025</th>
              <th class="sc"></th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="rname" id="i51001" data-area="19.0">
                <span itemprop="name">Ceuta</span>
              </td>
              <td class="rstatus">Municipality</td>
              <td class="rpop">83,567</td>
              <td class="sc"><a href="ceuta/51001__ceuta/">Details</a></td>
            </tr>
          </tbody>
        </table>
      </body>
    </html>
    """

    def test_page_profile_detects_structured_single_ts_root_page(self):
        soup = BeautifulSoup(self.CEUTA_SINGLE_TS_HTML, "html.parser")

        profile = detect_citypopulation_page_profile(soup)

        self.assertEqual(profile.page_type, CityPopulationPageType.STRUCTURED_TABLE)
        self.assertTrue(profile.has_cpage_root)
        self.assertTrue(profile.has_ts)
        self.assertFalse(profile.has_tl)
        self.assertTrue(profile.ts_uses_first_child_level)
        self.assertEqual(profile.preferred_html_format, "table")

    def test_single_ts_table_under_root_uses_first_child_level(self):
        entities = CityPopulationStructuredTableScraper().scrape_html(
            html=self.CEUTA_SINGLE_TS_HTML,
            url="https://www.citypopulation.de/en/spain/ceuta/",
            country_code="spain",
            level=1,
        )

        self.assertEqual([entity.code for entity in entities], ["spain", "51001"])
        root, municipality = entities
        self.assertEqual(root.name, "Ceuta")
        self.assertEqual(root.entity_type, "Autonomous City")
        self.assertEqual(municipality.level, 2)
        self.assertEqual(municipality.parent_code, "spain")
        self.assertEqual(municipality.pop_latest, 83567)

    def test_auto_scraper_delegates_single_ts_root_page_to_table_scraper(self):
        entities = CityPopulationAutoScraper().scrape_html(
            html=self.CEUTA_SINGLE_TS_HTML,
            url="https://www.citypopulation.de/en/spain/ceuta/",
            country_code="spain",
            level=1,
        )

        self.assertEqual([entity.code for entity in entities], ["spain", "51001"])
        self.assertEqual(entities[1].level, 2)
        self.assertEqual(entities[1].parent_code, "spain")
