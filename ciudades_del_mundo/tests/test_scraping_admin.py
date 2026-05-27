import unittest

from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper


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

