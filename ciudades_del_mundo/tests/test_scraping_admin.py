import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapingPageConfig, parse_pages
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationAutoScraper,
    CityPopulationCitiesAdminScraper,
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

    def test_parent_lookup_matches_coofficial_names_in_parentheses(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead><tr><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="i03082"><span itemprop="name">Xàbia</span> (<span itemprop="name">Jávea</span>)</td>
                  <td class="rstatus">Municipality</td>
                  <td class="rpop">30,817</td>
                </tr>
                <tr>
                  <td class="rname" id="i03083"><span itemprop="name">Xixona</span></td>
                  <td class="rstatus">Municipality</td>
                  <td class="rpop">7,223</td>
                </tr>
              </tbody>
            </table>
            <table id="ts">
              <thead><tr><th class="radm">Municipality</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr>
                  <td class="rname" id="i03082000202"><span itemprop="name">Alborada</span></td>
                  <td class="rstatus">Locality</td>
                  <td class="radm">Jávea</td>
                  <td class="rpop">30</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="spain/localities/alicante",
            html_format="cities",
            lowest_level=2,
            include_root=False,
            include_tables=("ts",),
            table_levels={"tl": 3, "ts": 4},
        )

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/spain/localities/alicante/",
            country_code="spain",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["03082000202"])
        self.assertEqual(entities[0].parent_code, "03082")


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
        self.assertEqual(entities[0].annotations, "Comparte población con otras divisiones")

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
        self.assertEqual(entities[0].annotations, "Comparte población con otras divisiones")



    def test_status_levels_parse_all_tbody_groups_and_override_levels(self):
        html = """
        <html>
          <body>
            <table id="tl">
              <thead><tr><th class="rpop" data-coldate="2022-01-01">2022</th></tr></thead>
              <tbody class="adm">
                <tr>
                  <td class="rname" id="iF1"><span itemprop="name">Federation</span></td>
                  <td class="rstatus">AReg</td>
                  <td class="rpop">100</td>
                </tr>
              </tbody>
              <tbody>
                <tr>
                  <td class="rname" id="iC1"><span itemprop="name">Canton</span></td>
                  <td class="rstatus">Cant</td>
                  <td class="rpop">50</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        page = ScrapingPageConfig(
            path="bosnia/cities",
            html_format="double",
            lowest_level=0,
            status_levels={"areg": 1, "cant": 2},
        )

        entities = CityPopulationDoubleScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/bosnia/cities/",
            country_code="bosnia",
            page=page,
        )

        self.assertEqual([(entity.code, entity.level) for entity in entities], [("F1", 1), ("C1", 2)])


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

    def test_country_cities_page_persists_tfoot_country_root(self):
        html = """
        <html><body>
          <table id="tl">
            <thead><tr><th class="rpop" data-coldate="2025-01-01" style="display: table-cell">2025</th></tr></thead>
            <tbody>
              <tr>
                <td class="rname" id="iVLA" data-area="13626" data-density="503.8"><span itemprop="name">Vlaams Gewest</span></td>
                <td class="rstatus">Region</td>
                <td class="rpop">6,864,766</td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td class="rname" id="iBEL" data-area="30689" data-density="385.3"><span itemprop="name">Belgium</span></td>
                <td class="rstatus">Kingdom</td>
                <td class="rpop">11,825,551</td>
              </tr>
            </tfoot>
          </table>
        </body></html>
        """
        page = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["cities"],
                    "include": {"cities": False, "infosection": True, "major_subdivision": True},
                }
            ],
            slug="belgium",
            schema_version=2,
        )[0]

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/belgium/cities/",
            country_code="belgium",
            page=page,
        )

        self.assertEqual([(entity.code, entity.name, entity.level, entity.parent_code) for entity in entities], [
            ("belgium", "Belgium", 0, None),
            ("VLA", "Vlaams Gewest", 1, "belgium"),
        ])

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


class CityPopulationCitiesScraperTests(unittest.TestCase):
    def test_country_cities_page_prefers_tfoot_root_and_ignores_major_cities(self):
        html = """
        <html><body>
          <div id="ir161" class="infosection">
            <p class="infoname">Belgium</p>
            <p class="infotext">Capital: Bruxelles</p>
          </div>
          <section id="adminareas">
            <h2>Regions &amp; Provinces</h2>
            <table id="tl">
              <thead><tr><th class="rname">Name</th><th class="rabbr">Abbr.</th><th class="rstatus">Status</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody class="adm"><tr><td class="rname" id="i554" data-wd="Q9337"><span itemprop="name">Vlaams Gewest</span></td><td class="rabbr">VLA</td><td class="rstatus">Reg</td><td class="rpop">6,864,766</td></tr></tbody>
              <tfoot><tr><th class="rname" id="i161" data-wd="Q31">Belgium</th><th class="rabbr">BEL</th><th class="rstatus">Kingd</th><th class="rpop">11,825,551</th><td class="sc"><a href="/en/belgium/admin/">→</a></td></tr></tfoot>
            </table>
          </section>
          <section id="largecities">
            <h2>Major Cities</h2>
            <table id="tlc"><tbody><tr><td class="rname" id="i999"><span itemprop="name">Should Not Parse</span></td><td class="rpop">1</td></tr></tbody></table>
          </section>
          <section id="citysection">
            <h2>Cities &amp; Municipalities</h2>
            <table id="ts">
              <thead><tr><th class="rname">Name</th><th class="radm">Adm.</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody><tr><td class="rname" id="i7014" data-wd="Q13121" data-status="Mun"><span itemprop="name">Aalst</span></td><td class="radm" data-admid="552">OVL</td><td class="rpop">92,131</td></tr></tbody>
            </table>
          </section>
        </body></html>
        """
        pages = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["cities"],
                    "include": {"cities": True, "infosection": True, "major_subdivision": False},
                }
            ],
            slug="belgium",
            schema_version=2,
        )

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/belgium/cities/",
            country_code="belgium",
            page=pages[0],
        )

        by_code = {entity.code: entity for entity in entities}
        self.assertEqual(by_code["belgium"].level, 0)
        self.assertEqual(by_code["belgium"].name, "Belgium")
        self.assertEqual(by_code["belgium"].entity_type, "Kingd")
        self.assertEqual(by_code["belgium"].data_wd, "Q31")
        self.assertIn("7014", by_code)
        self.assertNotIn("554", by_code)
        self.assertNotIn("999", by_code)


    def test_country_cities_grouped_first_table_builds_infosection_adm_child_ts_chain(self):
        html = """
        <html><body>
          <div id="ir161" class="infosection">
            <p class="infoname">Belgium</p>
            <p class="infotext">Capital: Bruxelles</p>
          </div>
          <section id="adminareas">
            <h2>Regions &amp; Provinces</h2>
            <table id="tl">
              <thead><tr><th class="rname">Name</th><th class="rabbr">Abbr.</th><th class="rstatus">Status</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody class="adm">
                <tr><td class="rname" id="i554" data-wd="Q9337"><span itemprop="name">Vlaams Gewest</span></td><td class="rabbr">VLA</td><td class="rstatus">Reg</td><td class="rpop">6,864,766</td></tr>
              </tbody>
              <tbody>
                <tr><td class="rname" id="i552" data-wd="Q1114" data-adm="VLA"><span itemprop="name">Oost-Vlaanderen</span></td><td class="rabbr">OVL</td><td class="rstatus">Prov</td><td class="rpop">1,602,532</td></tr>
              </tbody>
              <tfoot><tr><th class="rname" id="i161" data-wd="Q31">Belgium</th><th class="rabbr">BEL</th><th class="rstatus">Kingd</th><th class="rpop">11,825,551</th></tr></tfoot>
            </table>
          </section>
          <section id="largecities">
            <h2>Major Cities</h2>
            <table id="tlc"><tbody><tr><td class="rname" id="i999"><span itemprop="name">Should Not Parse</span></td><td class="rpop">1</td></tr></tbody></table>
          </section>
          <section id="citysection">
            <h2>Cities &amp; Municipalities</h2>
            <table id="ts">
              <thead><tr><th class="rname">Name</th><th class="radm">Adm.</th><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
              <tbody>
                <tr><td class="rname" id="i7014" data-wd="Q13121" data-status="Mun"><span itemprop="name">Aalst</span></td><td class="radm" data-admid="552">OVL</td><td class="rpop">92,131</td></tr>
              </tbody>
            </table>
          </section>
        </body></html>
        """
        page = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["cities"],
                    "include": {"cities": True, "infosection": True, "major_subdivision": True},
                }
            ],
            slug="belgium",
            schema_version=2,
        )[0]

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/belgium/cities/",
            country_code="belgium",
            page=page,
        )

        by_code = {entity.code: entity for entity in entities}
        self.assertEqual([entity.code for entity in entities], ["belgium", "554", "552", "7014"])
        self.assertEqual(by_code["belgium"].level, 0)
        self.assertEqual(by_code["belgium"].data_wd, "Q31")
        self.assertEqual(by_code["554"].level, 1)
        self.assertEqual(by_code["554"].parent_code, "belgium")
        self.assertEqual(by_code["552"].level, 2)
        self.assertEqual(by_code["552"].parent_code, "554")
        self.assertEqual(by_code["7014"].level, 3)
        self.assertEqual(by_code["7014"].parent_code, "552")
        self.assertNotIn("999", by_code)

    def test_citiesadmin_child_tbody_before_adm_parent_uses_matching_group(self):
        fixture = Path(__file__).resolve().parents[1] / "html" / "france" / "cities" / "mayotte.html"
        html = fixture.read_text(encoding="utf-8")
        page = ScrapingPageConfig(
            path="france/cities/mayotte",
            html_format="citiesadmin",
            lowest_level=1,
            include_sections=("infosection", "major_subdivision", "minor_subdivision", "cities"),
            repeat={"infosection": 2},
        )

        entities = CityPopulationCitiesAdminScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/france/cities/mayotte/",
            country_code="france",
            page=page,
        )

        by_code = {entity.code: entity for entity in entities}
        self.assertEqual(by_code["294"].name, "Grande-Terre")
        self.assertEqual(by_code["294"].level, 3)
        self.assertEqual(by_code["294"].url, "https://www.citypopulation.de/en/france/cities/mayotte/#i294")
        self.assertEqual(by_code["8125"].level, 4)
        self.assertEqual(by_code["8125"].parent_code, "294")
        self.assertEqual(by_code["8125"].url, "https://www.citypopulation.de/en/france/cities/mayotte/#i8125")
        self.assertEqual(by_code["295"].name, "Petite-Terre")
        self.assertEqual(by_code["295"].level, 3)
        self.assertEqual(by_code["8140"].level, 4)
        self.assertEqual(by_code["8140"].parent_code, "295")
        self.assertEqual(by_code["32355"].name, "Bambo-Est")
        self.assertEqual(by_code["32355"].level, 5)
        self.assertEqual(by_code["32355"].parent_code, "8127")
        self.assertEqual(by_code["32355"].url, "https://www.citypopulation.de/en/france/cities/mayotte/#i32355")
        self.assertEqual(len([entity for entity in entities if entity.level == 5]), 72)

    def test_overseas_cities_pages_parse_only_contents_table(self):
        fixtures = (
            ("reunion.html", "reunion", 4, 24),
            ("guadelupe.html", "guadeloupe", 5, 32),
        )
        for filename, path_slug, expected_major_count, expected_city_count in fixtures:
            with self.subTest(path_slug=path_slug):
                fixture = Path(__file__).resolve().parents[1] / "html" / "france" / "cities" / filename
                html = fixture.read_text(encoding="utf-8")
                page = ScrapingPageConfig(
                    path=f"france/cities/{path_slug}",
                    html_format="cities",
                    lowest_level=1,
                    include_sections=("infosection", "major_subdivision", "cities"),
                    repeat={"infosection": 2},
                )

                entities = CityPopulationCitiesScraper().scrape_configured_html(
                    html=html,
                    url=f"https://www.citypopulation.de/en/france/cities/{path_slug}/",
                    country_code="france",
                    page=page,
                )

                self.assertEqual(len([entity for entity in entities if entity.level == 3]), expected_major_count)
                self.assertEqual(len([entity for entity in entities if entity.level == 4]), expected_city_count)
                self.assertNotIn("Ris-Orangis", {entity.name for entity in entities})
                self.assertNotIn("Roinville", {entity.name for entity in entities})

    def test_cities_include_can_emit_only_infosection_root_without_tables(self):
        html = """
        <html><body>
          <div class="infosection"><p class="infoname">France</p>
            <p class="infotext"><span class="val">66,165,815</span> <small>Population [2023]</small></p>
          </div>
          <table id="tl">
            <tbody><tr><td class="rname" id="iIDF"><span itemprop="name">Île-de-France</span></td><td class="rstatus">Region</td></tr></tbody>
          </table>
          <table id="ts">
            <tbody><tr><td class="rname" id="i75056"><span itemprop="name">Paris</span></td><td class="rpop">2,000,000</td></tr></tbody>
          </table>
        </body></html>
        """
        page = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["cities"],
                    "include": {"cities": False, "infosection": True, "major_subdivision": False},
                }
            ],
            slug="france",
            schema_version=2,
        )[0]

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/france/cities/",
            country_code="france",
            page=page,
        )

        self.assertEqual([entity.code for entity in entities], ["france"])
        self.assertEqual(entities[0].name, "France")

    def test_tfoot_country_root_guard_does_not_override_non_root_cities_pages(self):
        html = """
        <html><body>
          <div class="infosection"><p class="infoname">French Guiana</p>
            <p class="infotext"><span class="val">298,554</span> <small>Population [2026]</small></p>
            <p class="infotext"><span class="val">83,534 km²</span> <small>Area</small></p>
          </div>
          <table id="tl">
            <thead><tr><th class="rpop" data-coldate="2026-01-01">2026</th></tr></thead>
            <tbody><tr><td class="rname" id="iC1"><span itemprop="name">Cayenne</span></td><td class="rpop">65,493</td></tr></tbody>
            <tfoot><tr><td class="rname" id="iBAD" data-wd="Q999"><span itemprop="name">Wrong Footer</span></td><td class="rstatus">Total</td><td class="rpop">999</td></tr></tfoot>
          </table>
          <table id="ts"><thead><tr><th class="rname">Name</th><th class="rpop" data-coldate="2026-01-01">2026</th></tr></thead>
            <tbody><tr><td class="rname" id="iL1"><span itemprop="name">Locality</span></td><td class="rpop">123</td></tr></tbody>
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
            table_levels={"tl": 4, "ts": 5},
        )

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/france/cities/guyane/",
            country_code="france",
            page=page,
        )

        by_code = {entity.code: entity for entity in entities}
        self.assertEqual(by_code["GUF"].name, "French Guiana")
        self.assertEqual(by_code["GUF"].level, 2)
        self.assertEqual(by_code["GUF"].parent_code, "OVERSEAS")
        self.assertNotIn("france", by_code)

    def test_tfoot_country_root_guard_keeps_infosection_when_footer_is_not_country_like(self):
        html = """
        <html><body>
          <div class="infosection"><p class="infoname">Testland</p>
            <p class="infotext"><span class="val">1,000</span> <small>Population [2025]</small></p>
          </div>
          <table id="tl">
            <thead><tr><th class="rpop" data-coldate="2025-01-01">2025</th></tr></thead>
            <tbody><tr><td class="rname" id="iR1"><span itemprop="name">Region</span></td><td class="rstatus">Reg</td><td class="rpop">500</td></tr></tbody>
            <tfoot><tr><td class="rname" id="iTOTAL"><span itemprop="name">Subtotal</span></td><td class="rstatus">Total</td><td class="rpop">500</td></tr></tfoot>
          </table>
        </body></html>
        """
        page = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["cities"],
                    "include": {"cities": False, "infosection": True, "major_subdivision": True},
                }
            ],
            slug="testland",
            schema_version=2,
        )[0]

        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html=html,
            url="https://www.citypopulation.de/en/testland/cities/",
            country_code="testland",
            page=page,
        )

        by_code = {entity.code: entity for entity in entities}
        self.assertEqual(by_code["testland"].name, "Testland")
        self.assertNotEqual(by_code["testland"].name, "Subtotal")
