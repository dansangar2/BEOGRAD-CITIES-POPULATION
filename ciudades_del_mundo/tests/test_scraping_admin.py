import unittest

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapingPageConfig
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationAutoScraper,
    CityPopulationCitiesScraper,
    CityPopulationDoubleScraper,
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


class CityPopulationDoubleScraperTests(unittest.TestCase):
    def test_config_can_use_first_table_only_as_parent_context(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead>
                <tr><th class="rpop" data-coldate="2024-01-01">2024</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td class="rname" id="iP1"><span itemprop="name">Rabat</span></td>
                  <td class="rstatus">Prefecture</td>
                  <td class="rpop">515,619</td>
                </tr>
              </tbody>
            </table>
            <table id="ts">
              <thead>
                <tr><th class="rpop" data-coldate="2024-01-01">2024</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td class="rname" id="iU1"><span itemprop="name">Rabat</span></td>
                  <td class="rstatus">Urban Commune</td>
                  <td class="radm" data-admid="P1">Rabat</td>
                  <td class="rpop">509,916</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="morocco/rabatsalekenitra",
            html_format="double",
            lowest_level=2,
            include_tables=("ts",),
            table_levels={"ts": 4},
        )

        entities = CityPopulationDoubleScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/morocco/rabatsalekenitra/",
            country_code="morocco",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["U1"])
        self.assertEqual(entities[0].level, 4)
        self.assertEqual(entities[0].parent_code, "P1")



    def test_parent_lookup_matches_unique_prefix_names(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead><tr><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iC1"><span itemprop="name">Breitenbrunn am Neusiedler See</span></td>
                  <td class="rstatus">Commune</td>
                  <td class="rpop">1,000</td>
                </tr>
              </tbody>
            </table>
            <table id="ts">
              <thead><tr><th class="radm">Commune</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iL1"><span itemprop="name">Breitenbrunn am Neusiedler See</span></td>
                  <td class="rstatus">Locality and Commune</td>
                  <td class="radm">Breitenbrunn</td>
                  <td class="rpop">1,000</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="austria/localities/eisenstadt",
            html_format="table",
            lowest_level=2,
            include_root=False,
            include_tables=("ts",),
            table_levels={"tl": 3, "ts": 4},
        )

        entities = CityPopulationDoubleScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/austria/localities/eisenstadt/",
            country_code="austria",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["L1"])
        self.assertEqual(entities[0].level, 4)
        self.assertEqual(entities[0].parent_code, "C1")

    def test_parent_lookup_matches_short_names_when_tl_has_translations(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead><tr><th class="rpop" data-coldate="2024-01-01">2024</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iD1"><span itemprop="name">České Budějovice</span> [<span itemprop="name">Budweis</span>]</td>
                  <td class="rstatus">District</td>
                  <td class="rpop">202,172</td>
                </tr>
              </tbody>
            </table>
            <table id="ts">
              <thead><tr><th class="radm">District</th><th class="rpop" data-coldate="2024-01-01">2024</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iL1"><span itemprop="name">Adamov</span></td>
                  <td class="rstatus">Village</td>
                  <td class="radm">České Budějovice</td>
                  <td class="rpop">1,000</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="czechrep/jihoceskykraj",
            html_format="table",
            lowest_level=1,
            include_root=False,
            include_tables=("ts",),
            table_levels={"tl": 2, "ts": 3},
        )

        entities = CityPopulationDoubleScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/czechrep/jihoceskykraj/",
            country_code="czechrep",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["L1"])
        self.assertEqual(entities[0].level, 3)
        self.assertEqual(entities[0].parent_code, "D1")

    def test_blank_parent_cell_uses_same_name_parent_and_adds_annotation(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead><tr><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iP1"><span itemprop="name">Shared Place</span></td>
                  <td class="rstatus">Commune</td>
                  <td class="rpop">1,000</td>
                </tr>
              </tbody>
            </table>
            <table id="ts">
              <thead><tr><th class="radm">Commune</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iL1"><span itemprop="name">Shared Place</span></td>
                  <td class="rstatus">Locality</td>
                  <td class="radm"></td>
                  <td class="rpop">1,000</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="example/localities/shared",
            html_format="table",
            lowest_level=2,
            include_root=False,
            include_tables=("ts",),
            table_levels={"tl": 3, "ts": 4},
        )

        entities = CityPopulationDoubleScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/example/localities/shared/",
            country_code="example",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["L1"])
        self.assertEqual(entities[0].parent_code, "P1")
        self.assertEqual(entities[0].annotations, "La localidad se reparte por varias subdivisiones superiores")

    def test_slash_parent_cell_prefers_same_name_parent_and_adds_annotation(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead><tr><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iP1"><span itemprop="name">Alpha</span></td>
                  <td class="rstatus">Commune</td>
                  <td class="rpop">1,000</td>
                </tr>
                <tr>
                  <td class="rname" id="iP2"><span itemprop="name">Beta</span></td>
                  <td class="rstatus">Commune</td>
                  <td class="rpop">900</td>
                </tr>
              </tbody>
            </table>
            <table id="ts">
              <thead><tr><th class="radm">Commune</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iL1"><span itemprop="name">Beta</span></td>
                  <td class="rstatus">Locality</td>
                  <td class="radm">Alpha / Beta</td>
                  <td class="rpop">100</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="example/localities/slash",
            html_format="table",
            lowest_level=2,
            include_root=False,
            include_tables=("ts",),
            table_levels={"tl": 3, "ts": 4},
        )

        entities = CityPopulationDoubleScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/example/localities/slash/",
            country_code="example",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["L1"])
        self.assertEqual(entities[0].parent_code, "P2")
        self.assertEqual(entities[0].annotations, "La localidad se reparte por varias subdivisiones superiores")

    def test_slash_parent_cell_with_data_admid_keeps_parent_and_adds_annotation(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead><tr><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iP1"><span itemprop="name">Alpha</span></td>
                  <td class="rstatus">Commune</td>
                  <td class="rpop">1,000</td>
                </tr>
                <tr>
                  <td class="rname" id="iP2"><span itemprop="name">Beta</span></td>
                  <td class="rstatus">Commune</td>
                  <td class="rpop">900</td>
                </tr>
              </tbody>
            </table>
            <table id="ts">
              <thead><tr><th class="radm">Commune</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="iL1"><span itemprop="name">Beta</span></td>
                  <td class="rstatus">Locality</td>
                  <td class="radm" data-admid="P1">Alpha / Beta</td>
                  <td class="rpop">100</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="example/localities/slash-data-admid",
            html_format="table",
            lowest_level=2,
            include_root=False,
            include_tables=("ts",),
            table_levels={"tl": 3, "ts": 4},
        )

        entities = CityPopulationDoubleScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/example/localities/slash-data-admid/",
            country_code="example",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["L1"])
        self.assertEqual(entities[0].parent_code, "P1")
        self.assertEqual(entities[0].annotations, "La localidad se reparte por varias subdivisiones superiores")


class CityPopulationConfiguredPageHintsTests(unittest.TestCase):
    def test_admin_table_levels_can_anchor_rootless_pages_at_real_levels(self):
        html = """
        <html><body><table id="tl">
          <thead><tr><th class="rpop" data-coldate="2024-01-01">2024</th></tr></thead>
          <tbody class="admin1">
            <tr><td class="rname" id="iR1"><span itemprop="name">Region</span></td><td class="rstatus">Region</td><td class="rpop">100</td></tr>
          </tbody>
          <tbody class="admin2">
            <tr><td class="rname" id="iD1"><span itemprop="name">Department</span></td><td class="rstatus">Department</td><td class="rpop">50</td></tr>
          </tbody>
        </table></body></html>
        """
        page = ScrapingPageConfig(
            path="france/reg/admin",
            html_format="admin",
            lowest_level=0,
            include_root=False,
            table_levels={"admin1": 2, "admin2": 3},
        )

        entities = CityPopulationAdminScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/france/reg/admin/",
            country_code="france",
            page=page,
        )

        self.assertEqual([(entity.code, entity.level, entity.parent_code) for entity in entities], [
            ("R1", 2, None),
            ("D1", 3, "R1"),
        ])


    def test_admin_include_tables_can_use_parent_rows_as_context(self):
        html = """
        <html><body><table id="tl">
          <thead><tr><th class="rpop" data-coldate="2024-01-01">2024</th></tr></thead>
          <tbody class="admin1">
            <tr><td class="rname" id="iG1"><span itemprop="name">Governorate</span></td><td class="rstatus">Governorate</td><td class="rpop">100</td></tr>
          </tbody>
          <tbody class="admin2">
            <tr><td class="rname" id="iM1"><span itemprop="name">Municipality</span></td><td class="rstatus">Municipality</td><td class="rpop">50</td></tr>
          </tbody>
        </table></body></html>
        """
        page = ScrapingPageConfig(
            path="tunisia/mun/admin",
            html_format="admin",
            lowest_level=0,
            root_code="tunisia",
            root_name="Tunisia",
            table_levels={"admin1": 1, "admin2": 3},
            include_tables=("admin2",),
        )

        entities = CityPopulationAdminScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/tunisia/mun/admin/",
            country_code="tunisia",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["tunisia", "M1"])
        self.assertEqual(entities[1].level, 3)
        self.assertEqual(entities[1].parent_code, "G1")

    def test_cities_page_root_can_be_recoded_without_losing_metrics(self):
        html = """
        <html><body>
          <div class="infosection"><p class="infoname">French Guiana</p>
            <p class="infotext"><span class="val">298,554</span> <small>Population [2026]</small></p>
            <p class="infotext"><span class="val">83,534 km²</span> <small>Area</small></p>
          </div>
          <table id="ts"><thead><tr><th class="rname">Name</th><th class="rpop" data-coldate="2026-01-01">2026</th></tr></thead>
            <tbody><tr><td class="rname" id="iC1"><span itemprop="name">Commune</span></td><td class="rpop">123</td></tr></tbody>
          </table>
        </body></html>
        """
        page = ScrapingPageConfig(
            path="france/cities/guyane",
            html_format="cities",
            lowest_level=2,
            root_code="GUF",
            root_parent_code="OVERSEAS",
            root_entity_type="Overseas Department",
            table_levels={"ts": 5},
        )

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/france/cities/guyane/",
            country_code="france",
            page=page,
        )

        root, commune = entities
        self.assertEqual(root.code, "GUF")
        self.assertEqual(root.level, 2)
        self.assertEqual(root.parent_code, "OVERSEAS")
        self.assertEqual(root.entity_type, "Overseas Department")
        self.assertEqual(root.pop_latest, 298554)
        self.assertEqual(root.area_km2, 83534.0)
        self.assertEqual(commune.level, 5)
        self.assertEqual(commune.parent_code, "GUF")
