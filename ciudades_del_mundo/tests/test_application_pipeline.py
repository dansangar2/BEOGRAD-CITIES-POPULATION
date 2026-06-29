from decimal import Decimal
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import (
    CachedScrapePage,
    ScrapeAdminAreas,
    ScrapeBlockValidationError,
    ScrapeLinkValidationError,
)
from ciudades_del_mundo.application.citypopulation_linking import normalize_citypopulation_entities
from ciudades_del_mundo.domain import (
    AdminAreaSummary,
    ScrapedAdminArea,
    ScrapingJobConfig,
    ScrapingPageConfig,
    parse_pages,
)
from ciudades_del_mundo.services.wikidata_parent_links import (
    WikidataParentCandidate,
    link_entities_with_wikidata_parent_candidates,
)


class FakeScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(
                code=country_code,
                name="Testland",
                level=0,
                country_code=country_code,
                area_km2=Decimal("1"),
                pop_latest=100,
            ),
            ScrapedAdminArea(
                code="child",
                name="Child",
                level=1,
                country_code=country_code,
                parent_code=country_code,
                area_km2=Decimal("1"),
                pop_latest=50,
            ),
            ScrapedAdminArea(
                code="child",
                name="Duplicate Child",
                level=1,
                country_code=country_code,
                parent_code=country_code,
                area_km2=Decimal("99"),
                pop_latest=999,
            ),
        ]


class FakeHtmlScraper(FakeScraper):
    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=self.scrape(base_url, country_code, page),
            html="<html>page</html>",
            url="https://example.test/en/fake/admin/",
        )


class MissingSectionCitiesScraper:
    html_format = "cities"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code=country_code,
                    name="Testland",
                    level=0,
                    country_code=country_code,
                    pop_latest=100,
                    annotations="CityPopulation section: infosection",
                ),
                ScrapedAdminArea(
                    code="north",
                    name="North",
                    level=1,
                    country_code=country_code,
                    parent_code=country_code,
                    pop_latest=50,
                    annotations="CityPopulation section: major_subdivision",
                ),
            ],
            html="<html><main>missing cities table</main></html>",
            url="https://example.test/en/fake/cities/",
        )


class MissingParentAdminScraper:
    html_format = "admin"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code=country_code,
                    name="Testland",
                    level=0,
                    country_code=country_code,
                    annotations="CityPopulation section: infosection",
                ),
                ScrapedAdminArea(
                    code="P1",
                    name="Province One",
                    level=1,
                    country_code=country_code,
                    annotations="CityPopulation section: major_subdivision",
                ),
                ScrapedAdminArea(
                    code="D1",
                    name="District One",
                    level=2,
                    country_code=country_code,
                    parent_code="P1",
                    annotations="CityPopulation section: minor_subdivision",
                ),
            ],
            html="<html><main>missing parent</main></html>",
            url="https://example.test/en/fake/admin/",
        )


class RootAliasCitiesScraper:
    html_format = "cities"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code=country_code,
                    name="Aliasland",
                    level=0,
                    country_code=country_code,
                    annotations="CityPopulation section: infosection",
                ),
                ScrapedAdminArea(
                    code="child",
                    name="Child",
                    level=1,
                    country_code=country_code,
                    parent_code="748",
                    annotations="CityPopulation section: cities",
                ),
            ],
            html="<html><main>root alias</main></html>",
            url=f"https://example.test/en/{country_code}/cities/",
        )


class UnlinkedV2AdminScraper:
    html_format = "admin"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code="20",
                    name="Saida",
                    level=1,
                    country_code=country_code,
                    entity_type="Province",
                    data_wd="Q233640",
                    url="https://www.citypopulation.de/en/algeria/admin/20__saida/",
                    annotations="CityPopulation section: major_subdivision",
                )
            ],
            html="<html><main>saida province</main></html>",
            url="https://www.citypopulation.de/en/algeria/admin/",
        )


class NetherlandsAdminHierarchyScraper:
    html_format = "admin"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code=country_code,
                    name="Netherlands",
                    level=0,
                    country_code=country_code,
                    data_wd="Q55",
                    annotations="CityPopulation section: infosection",
                ),
                ScrapedAdminArea(
                    code="NL01",
                    name="Drenthe",
                    level=1,
                    country_code=country_code,
                    parent_code=country_code,
                    data_wd="Q772",
                    annotations="CityPopulation section: major_subdivision",
                ),
                ScrapedAdminArea(
                    code="GM1730",
                    name="Tynaarlo",
                    level=2,
                    country_code=country_code,
                    parent_code="NL01",
                    data_wd="Q1000",
                    annotations="CityPopulation section: minor_subdivision",
                ),
            ],
            html="<html><main>netherlands admin</main></html>",
            url="https://www.citypopulation.de/en/netherlands/admin/",
        )


class NetherlandsParentlessUrbanCenterScraper:
    html_format = "cities"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code="BK00261",
                    name="Zuidlaren",
                    level=1,
                    country_code=country_code,
                    entity_type="Urban Center",
                    data_wd="Q228666",
                    annotations="CityPopulation section: cities",
                    url="https://www.citypopulation.de/en/netherlands/drenthe/_/BK00261__zuidlaren/",
                )
            ],
            html="<html><main>netherlands cities</main></html>",
            url="https://www.citypopulation.de/en/netherlands/drenthe/",
        )


class AlgeriaRootCitiesScraper:
    html_format = "cities"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code=country_code,
                    name="Algeria",
                    level=0,
                    country_code=country_code,
                    pop_latest=100,
                    annotations="CityPopulation section: infosection",
                ),
            ],
            html="<html><main>algeria root</main></html>",
            url="https://www.citypopulation.de/en/algeria/cities/",
        )


class AlgeriaParentlessProvinceAdminScraper:
    html_format = "admin"

    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=[
                ScrapedAdminArea(
                    code="20",
                    name="Saida",
                    level=1,
                    country_code=country_code,
                    entity_type="Province",
                    data_wd="Q233640",
                    url="https://www.citypopulation.de/en/algeria/admin/20__saida/",
                    annotations="CityPopulation section: major_subdivision",
                )
            ],
            html="<html><main>saida province</main></html>",
            url="https://www.citypopulation.de/en/algeria/admin/",
        )


class SpainLocalityCodePrefixScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="spain", name="Spain", level=0, country_code="spain"),
            ScrapedAdminArea(code="03", name="Alicante", level=2, country_code="spain", parent_code="VC"),
            ScrapedAdminArea(
                code="03082",
                name="Xàbia",
                level=3,
                country_code="spain",
                parent_code="03",
                data_wd="Q851020",
                url="https://www.citypopulation.de/en/spain/comunitatvalenciana/alicante/03082__xàbia/",
            ),
            ScrapedAdminArea(
                code="03083",
                name="Xixona",
                level=3,
                country_code="spain",
                parent_code="03",
                url="https://www.citypopulation.de/en/spain/comunitatvalenciana/alicante/03083__xixona/",
            ),
            ScrapedAdminArea(
                code="03082000202",
                name="Alborada",
                level=4,
                country_code="spain",
                parent_code="03083",
                url="https://www.citypopulation.de/en/spain/localities/alicante/jávea/03082000202__alborada/",
            ),
        ]


class SpainSingleProvinceCommunityScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="spain", name="Spain", level=0, country_code="spain"),
            ScrapedAdminArea(
                code="MAD",
                name="Madrid",
                level=1,
                country_code="spain",
                parent_code="spain",
                url="https://www.citypopulation.de/en/spain/admin/MAD__madrid/",
            ),
            ScrapedAdminArea(
                code="28",
                name="Madrid",
                level=2,
                country_code="spain",
                parent_code="MAD",
                url="https://www.citypopulation.de/en/spain/admin/madrid/28__madrid/",
            ),
            ScrapedAdminArea(
                code="28079",
                name="Madrid",
                level=2,
                country_code="spain",
                parent_code="MAD",
                entity_type="Municipality",
                annotations="CityPopulation section: cities",
                url="https://www.citypopulation.de/en/spain/madrid/madrid/28079__madrid/",
            ),
        ]


class PortugalParentHintScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="portugal", name="Portugal", level=0, country_code="portugal"),
            ScrapedAdminArea(
                code="01",
                name="Aveiro",
                level=1,
                country_code="portugal",
                parent_code="portugal",
            ),
            ScrapedAdminArea(
                code="1610101",
                name="Agueda",
                level=2,
                country_code="portugal",
                parent_code="01",
            ),
            ScrapedAdminArea(
                code="010121",
                name="Parish A",
                level=3,
                country_code="portugal",
                parent_code="1610101",
            ),
            ScrapedAdminArea(
                code="L1",
                name="Place One",
                level=4,
                country_code="portugal",
                parent_code="1610101",
                annotations="CityPopulation section: cities; CityPopulation parent hint: Parish A",
            ),
        ]


class PortugalMultiwordUrlScopeScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="portugal", name="Portugal", level=0, country_code="portugal"),
            ScrapedAdminArea(
                code="05",
                name="Castelo Branco",
                level=1,
                country_code="portugal",
                parent_code="portugal",
                url="https://www.citypopulation.de/en/portugal/admin/05__castelo_branco/",
            ),
            ScrapedAdminArea(
                code="16",
                name="Viana do Castelo",
                level=1,
                country_code="portugal",
                parent_code="portugal",
                url="https://www.citypopulation.de/en/portugal/admin/16__viana_do_castelo/",
            ),
            ScrapedAdminArea(
                code="1690502",
                name="Castelo Branco",
                level=2,
                country_code="portugal",
                parent_code="05",
                entity_type="Municipality",
                annotations="CityPopulation section: minor_subdivision",
                url="https://www.citypopulation.de/en/portugal/admin/castelo_branco/1690502__castelo_branco/",
            ),
        ]


class OrphanFranceDepartmentScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="france", name="France", level=0, country_code="france"),
            ScrapedAdminArea(
                code="france_guyane",
                name="French Guiana",
                level=1,
                country_code="france",
                parent_code=None,
                data_wd="Q3769",
            ),
            ScrapedAdminArea(
                code="france_guyane__repeat2",
                name="French Guiana",
                level=2,
                country_code="france",
                parent_code="france_guyane",
                data_wd="Q3769",
            ),
        ]




class FranceDepartmentDistrictScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="france", name="France", level=0, country_code="france"),
            ScrapedAdminArea(code="R84", name="Auvergne - Rhône - Alpes", level=1, country_code="france", parent_code="france", entity_type="Region"),
            ScrapedAdminArea(code="01", name="Ain", level=2, country_code="france", parent_code="R84", entity_type="Department"),
            # Scraped from /france/admin/: wrongly emitted at the department level, with no parent.
            ScrapedAdminArea(
                code="011",
                name="Belley",
                level=2,
                country_code="france",
                parent_code=None,
                entity_type="Arrondissement",
                url="https://www.citypopulation.de/en/france/admin/ain/011__belley/",
            ),
            # Scraped from a department cities page: it already points to the arrondissement,
            # but must move down when the arrondissement is repaired.
            ScrapedAdminArea(
                code="01034",
                name="Belley",
                level=3,
                country_code="france",
                parent_code="011",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/ain/belley/01034__belley/",
            ),
        ]


class FranceScopedCodeOrphanDistrictScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="france", name="France", level=0, country_code="france"),
            ScrapedAdminArea(code="R75", name="Nouvelle-Aquitaine", level=1, country_code="france", parent_code="france", entity_type="Region"),
            ScrapedAdminArea(code="17", name="Charente-Maritime", level=2, country_code="france", parent_code="R75", entity_type="Department"),
            ScrapedAdminArea(
                code="17_174",
                name="Saintes",
                level=1,
                country_code="france",
                parent_code=None,
                entity_type="Arrondissement",
                data_wd="Q702457",
                url="https://www.citypopulation.de/en/france/charentemaritime/174__saintes/",
            ),
            ScrapedAdminArea(
                code="17415",
                name="Saintes",
                level=2,
                country_code="france",
                parent_code="17_174",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/charentemaritime/saintes/17415__saintes/",
            ),
        ]


class FranceOverseasDepartmentDistrictScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="france", name="France", level=0, country_code="france"),
            ScrapedAdminArea(
                code="france_guyane",
                name="French Guiana",
                level=1,
                country_code="france",
                parent_code="france",
                data_wd="Q3769",
                annotations="Raíz de página repetida",
            ),
            ScrapedAdminArea(
                code="france_guyane__repeat2",
                name="French Guiana",
                level=2,
                country_code="france",
                parent_code="france_guyane",
                data_wd="Q3769",
                annotations="Raíz de página repetida; Duplicación explícita por página",
            ),
            ScrapedAdminArea(
                code="1181",
                name="Cayenne",
                level=2,
                country_code="france",
                parent_code="france",
                entity_type="Arr",
                url="https://www.citypopulation.de/en/france/cities/guyane/#i1181",
            ),
            ScrapedAdminArea(
                code="9124",
                name="Cayenne",
                level=3,
                country_code="france",
                parent_code="1181",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/cities/guyane/#i9124",
            ),
        ]


class FranceEssonneReunionCodeCollisionScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="france", name="France", level=0, country_code="france"),
            ScrapedAdminArea(
                code="206",
                name="Réunion",
                level=1,
                country_code="france",
                parent_code="france",
                data_wd="Q17070",
                url="https://www.citypopulation.de/en/france/cities/reunion/",
            ),
            ScrapedAdminArea(
                code="1177",
                name="Saint-Denis",
                level=3,
                country_code="france",
                parent_code="206",
                url="https://www.citypopulation.de/en/france/cities/reunion/#i1177",
            ),
            ScrapedAdminArea(
                code="9152",
                name="Sainte-Marie",
                level=4,
                country_code="france",
                parent_code="1177",
                data_wd="Q662942",
                url="https://www.citypopulation.de/en/france/cities/reunion/#i9152",
                annotations="CityPopulation section: cities; Suma al padre",
            ),
            ScrapedAdminArea(
                code="91",
                name="Essonne",
                level=2,
                country_code="france",
                parent_code="169",
                data_wd="Q3368",
                url="https://www.citypopulation.de/en/france/reg/admin/île_de_france/91__essonne/",
            ),
            ScrapedAdminArea(
                code="912",
                name="Évry",
                level=3,
                country_code="france",
                parent_code="91",
                data_wd="Q702873",
                url="https://www.citypopulation.de/en/france/admin/essonne/912__évry/",
            ),
            ScrapedAdminArea(
                code="91521",
                name="Ris-Orangis",
                level=4,
                country_code="france",
                parent_code="912",
                data_wd="Q274237",
                url="https://www.citypopulation.de/en/france/essonne/évry/91521__ris_orangis/",
                annotations="CityPopulation section: cities",
            ),
        ]


class FranceOverseasSameNameBlockAnchorScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        mayotte_communes = [
            ScrapedAdminArea(
                code=f"YT-GT-{index:02}",
                name=f"Mayotte Commune {index:02}",
                level=4,
                country_code="france",
                parent_code="YT-GT",
                entity_type="Commune",
                url=f"https://www.citypopulation.de/en/france/cities/mayotte/#iYTGT{index:02}",
                annotations="CityPopulation section: cities",
            )
            for index in range(1, 16)
        ]
        return [
            ScrapedAdminArea(code="france", name="France", level=0, country_code="france"),
            ScrapedAdminArea(
                code="france_martinique",
                name="Martinique",
                level=1,
                country_code="france",
                parent_code="france",
                url="https://www.citypopulation.de/en/france/cities/martinique/",
            ),
            ScrapedAdminArea(
                code="france_martinique__repeat2",
                name="Martinique",
                level=2,
                country_code="france",
                parent_code="france_martinique",
                url="https://www.citypopulation.de/en/france/cities/martinique/",
            ),
            ScrapedAdminArea(
                code="MQ-SP",
                name="Saint-Pierre",
                level=3,
                country_code="france",
                parent_code="france_martinique__repeat2",
                entity_type="Arrondissement",
                url="https://www.citypopulation.de/en/france/cities/martinique/#iMQSP",
                annotations="CityPopulation section: major_subdivision",
            ),
            ScrapedAdminArea(
                code="MQ-97203",
                name="Bellefontaine",
                level=4,
                country_code="france",
                parent_code="MQ-SP",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/cities/martinique/#i97203",
                annotations="CityPopulation section: cities",
            ),
            ScrapedAdminArea(
                code="france_reunion",
                name="Reunion",
                level=1,
                country_code="france",
                parent_code="france",
                url="https://www.citypopulation.de/en/france/cities/reunion/",
            ),
            ScrapedAdminArea(
                code="france_reunion__repeat2",
                name="Reunion",
                level=2,
                country_code="france",
                parent_code="france_reunion",
                url="https://www.citypopulation.de/en/france/cities/reunion/",
            ),
            ScrapedAdminArea(
                code="RE-SP",
                name="Saint-Pierre",
                level=3,
                country_code="france",
                parent_code="france_reunion__repeat2",
                entity_type="Arrondissement",
                url="https://www.citypopulation.de/en/france/cities/reunion/#iRESP",
                annotations="CityPopulation section: major_subdivision",
            ),
            ScrapedAdminArea(
                code="RE-97424",
                name="Cilaos",
                level=4,
                country_code="france",
                parent_code="RE-SP",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/cities/reunion/#i97424",
                annotations="CityPopulation section: cities",
            ),
            ScrapedAdminArea(
                code="RE-97422",
                name="Le Tampon",
                level=4,
                country_code="france",
                parent_code="RE-SP",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/cities/reunion/#i97422",
                annotations="CityPopulation section: cities",
            ),
            ScrapedAdminArea(
                code="france_guadeloupe",
                name="Guadeloupe",
                level=1,
                country_code="france",
                parent_code="france",
                url="https://www.citypopulation.de/en/france/cities/guadeloupe/",
            ),
            ScrapedAdminArea(
                code="france_guadeloupe__repeat2",
                name="Guadeloupe",
                level=2,
                country_code="france",
                parent_code="france_guadeloupe",
                url="https://www.citypopulation.de/en/france/cities/guadeloupe/",
            ),
            ScrapedAdminArea(
                code="GP-GT",
                name="Grande-Terre",
                level=3,
                country_code="france",
                parent_code="france_guadeloupe__repeat2",
                entity_type="Island",
                url="https://www.citypopulation.de/en/france/cities/guadeloupe/#iGPGT",
                annotations="CityPopulation section: major_subdivision",
            ),
            ScrapedAdminArea(
                code="GP-97101",
                name="Les Abymes",
                level=4,
                country_code="france",
                parent_code="GP-GT",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/cities/guadeloupe/#i97101",
                annotations="CityPopulation section: cities",
            ),
            ScrapedAdminArea(
                code="france_mayotte",
                name="Mayotte",
                level=1,
                country_code="france",
                parent_code="france",
                url="https://www.citypopulation.de/en/france/cities/mayotte/",
            ),
            ScrapedAdminArea(
                code="france_mayotte__repeat2",
                name="Mayotte",
                level=2,
                country_code="france",
                parent_code="france_mayotte",
                url="https://www.citypopulation.de/en/france/cities/mayotte/",
            ),
            ScrapedAdminArea(
                code="YT-GT",
                name="Grande-Terre",
                level=3,
                country_code="france",
                parent_code="france_mayotte__repeat2",
                entity_type="Island",
                url="https://www.citypopulation.de/en/france/cities/mayotte/#iYTGT",
                annotations="CityPopulation section: major_subdivision",
            ),
            *mayotte_communes,
        ]


class FranceUnscopedOverseasPrefixCollisionScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="france", name="France", level=0, country_code="france"),
            ScrapedAdminArea(
                code="652",
                name="Tarbes",
                level=3,
                country_code="france",
                parent_code="65",
                entity_type="Arrondissement",
                url="https://www.citypopulation.de/en/france/hautespyrenees/tarbes/",
                annotations="CityPopulation section: minor_subdivision",
            ),
            ScrapedAdminArea(
                code="6527",
                name="Saint-Pierre",
                level=3,
                country_code="france",
                parent_code="france_martinique__repeat2",
                entity_type="Arrondissement",
                url="https://www.citypopulation.de#i6527",
                annotations="CityPopulation section: major_subdivision",
            ),
            ScrapedAdminArea(
                code="65270",
                name="Lespouey",
                level=4,
                country_code="france",
                parent_code="652",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/hautespyrenees/tarbes/65270__lespouey/",
                annotations="CityPopulation section: cities",
            ),
            ScrapedAdminArea(
                code="812",
                name="Castres",
                level=3,
                country_code="france",
                parent_code="81",
                entity_type="Arrondissement",
                url="https://www.citypopulation.de/en/france/tarn/castres/",
                annotations="CityPopulation section: minor_subdivision",
            ),
            ScrapedAdminArea(
                code="8125",
                name="Acoua",
                level=4,
                country_code="france",
                parent_code="137__france_cities_mayotte__repeat2_294",
                entity_type="Commune",
                url="https://www.citypopulation.de#i8125",
                annotations="CityPopulation section: minor_subdivision",
            ),
            ScrapedAdminArea(
                code="81250",
                name="Saint-Genest-de-Contest",
                level=4,
                country_code="france",
                parent_code="812",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/france/tarn/castres/81250__saint_genest_de_contest/",
                annotations="CityPopulation section: cities",
            ),
        ]



class GenericProvinceDistrictScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="testland", name="Testland", level=0, country_code="testland"),
            ScrapedAdminArea(code="R1", name="North Region", level=1, country_code="testland", parent_code="testland", entity_type="Region"),
            ScrapedAdminArea(code="10", name="Alpha Province", level=2, country_code="testland", parent_code="R1", entity_type="Province"),
            ScrapedAdminArea(
                code="101",
                name="Alpha District",
                level=2,
                country_code="testland",
                parent_code=None,
                entity_type="District",
                url="https://www.citypopulation.de/en/testland/admin/alpha/101__alpha_district/",
            ),
            ScrapedAdminArea(
                code="10101",
                name="Alpha Commune",
                level=3,
                country_code="testland",
                parent_code="101",
                entity_type="Commune",
                url="https://www.citypopulation.de/en/testland/alpha/alpha_district/10101__alpha_commune/",
            ),
        ]



class DuplicateCityAfterNormalizationScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="testland", name="Testland", level=0, country_code="testland"),
            ScrapedAdminArea(code="R11", name="Capital Region", level=1, country_code="testland", parent_code="testland", entity_type="Region"),
            ScrapedAdminArea(
                code="75",
                name="Paris",
                level=2,
                country_code="testland",
                parent_code="R11",
                entity_type="Department",
                data_wd="Q90",
                url="https://www.citypopulation.de/en/testland/reg/admin/capital/75__paris/",
            ),
            # Same real city from another page.  Without parent_level, the
            # commune duplicate below wins because it has the more precise code.
            ScrapedAdminArea(
                code="751",
                name="Paris",
                level=3,
                country_code="testland",
                parent_code="75",
                entity_type="City",
                data_wd="Q90",
                url="https://www.citypopulation.de/en/testland/admin/paris/751__paris/",
            ),
            ScrapedAdminArea(
                code="75056",
                name="Paris",
                level=3,
                country_code="testland",
                parent_code="75",
                entity_type="Commune",
                data_wd="Q90",
                url="https://www.citypopulation.de/en/testland/paris/paris/75056__paris/",
                annotations="CityPopulation section: cities",
            ),
        ]


class DuplicateBrusselsAfterNormalizationScraper(FakeScraper):
    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="belgium", name="Belgium", level=0, country_code="belgium"),
            ScrapedAdminArea(code="548", name="Région de Bruxelles", level=1, country_code="belgium", parent_code="belgium", entity_type="Reg"),
            ScrapedAdminArea(code="04000", name="Région de Bruxelles-Capitale", level=2, country_code="belgium", parent_code="548", entity_type="Region"),
            ScrapedAdminArea(
                code="21000",
                name="Bruxelles-Capitale",
                level=3,
                country_code="belgium",
                parent_code="04000",
                entity_type="Arrondissement",
                url="https://www.citypopulation.de/en/belgium/admin/région_de_bruxelles_capi/21000__bruxelles_capitale/",
            ),
            ScrapedAdminArea(
                code="7021",
                name="Bruxelles",
                level=4,
                country_code="belgium",
                parent_code="21000",
                entity_type="Mun",
                data_wd="Q239",
                url="https://www.citypopulation.de/en/belgium/bruxelles/bruxelles_capitale/21004__bruxelles/",
            ),
            # Duplicate representation of the same city from another table/page.
            ScrapedAdminArea(
                code="21004",
                name="Bruxelles",
                level=3,
                country_code="belgium",
                parent_code=None,
                entity_type="City",
                data_wd="Q239",
                url="https://www.citypopulation.de/en/belgium/bruxelles/bruxelles_capitale/21004__bruxelles/",
            ),
        ]

class FakeRepository:
    def __init__(self):
        self.reset_countries = []
        self.saved_country = None
        self.saved_entities = []
        self.deleted_ids = set()
        self.most_populated_assignments = []

    def reset_country(self, country_code):
        self.reset_countries.append(country_code)

    def save_many(self, country_code, entities):
        self.saved_country = country_code
        self.saved_entities = list(entities)
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        self.deleted_ids = set(ids)
        return 0

    def list_summaries(self, country_code):
        return [
            AdminAreaSummary(id=f"{country_code}_{country_code}", level=0, parent_id=None, pop_latest=100),
            AdminAreaSummary(id=f"{country_code}_child", level=1, parent_id=f"{country_code}_{country_code}", pop_latest=50),
        ]

    def save_most_populated_assignments(self, assignments):
        self.most_populated_assignments = list(assignments)
        return len(assignments)

    def save_representatives(self, country_code, config):
        return 0


class FlakySaveRepository(FakeRepository):
    def __init__(self, failures_before_success=2):
        super().__init__()
        self.failures_before_success = failures_before_success
        self.save_attempts = 0

    def save_many(self, country_code, entities):
        self.save_attempts += 1
        if self.save_attempts <= self.failures_before_success:
            raise RuntimeError("transient save failure")
        return super().save_many(country_code, entities)



class ScrapingSchemaV2ConfigTests(unittest.TestCase):
    def test_include_repeat_and_schema_v2_default_levels_are_parsed(self):
        pages = parse_pages(
            [
                {
                    "source": "admin",
                    "path": ["admin"],
                    "include": {"admin1": True, "admin2": True, "infosection": True},
                },
                {
                    "source": "admin",
                    "path": ["ceuta"],
                    "include": {"admin1": False, "admin2": True, "infosection": True},
                    "repeat": {"infosection": 2},
                },
                {
                    "source": "cities",
                    "path": ["localities/ceuta"],
                    "include": {"cities": True, "infosection": False, "major_subdivision": True},
                },
            ],
            slug="spain",
            schema_version=2,
        )

        self.assertEqual(pages[0].lowest_level, 0)
        self.assertEqual(pages[0].include_tables, ("admin1", "admin2"))
        self.assertTrue(pages[0].include_root)

        self.assertEqual(pages[1].lowest_level, 1)
        self.assertEqual(pages[1].include_tables, ("admin2",))
        self.assertEqual(pages[1].repeat, {"infosection": 2})

        self.assertEqual(pages[2].lowest_level, 2)
        self.assertEqual(pages[2].include_tables, ("tl", "ts"))
        self.assertFalse(pages[2].include_root)

    def test_schema_v2_country_cities_page_starts_at_country_root_level(self):
        pages = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["cities"],
                    "include": {"cities": True, "infosection": True, "major_subdivision": True},
                }
            ],
            slug="belgium",
            schema_version=2,
        )

        self.assertEqual(pages[0].path, "belgium/cities")
        self.assertEqual(pages[0].lowest_level, 0)
        self.assertEqual(pages[0].include_tables, ("tl", "ts"))
        self.assertTrue(pages[0].include_root)

    def test_schema_v2_root_cities_page_without_suffix_starts_at_country_root_level(self):
        pages = parse_pages(
            [
                {
                    "source": "cities",
                    "path": [""],
                    "include": {"cities": True, "infosection": True, "major_subdivision": True},
                }
            ],
            slug="andorra",
            schema_version=2,
        )

        self.assertEqual(pages[0].path, "andorra")
        self.assertEqual(pages[0].lowest_level, 0)

    def test_schema_v2_city_shaped_admin_page_with_infosection_starts_at_country_root_level(self):
        pages = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["admin"],
                    "include": {"cities": False, "infosection": True, "major_subdivision": True},
                }
            ],
            slug="gibraltar",
            schema_version=2,
        )

        self.assertEqual(pages[0].path, "gibraltar/admin")
        self.assertEqual(pages[0].lowest_level, 0)

    def test_schema_v2_nested_admin_path_does_not_start_at_country_root_level(self):
        pages = parse_pages(
            [
                {
                    "source": "admin",
                    "path": ["benimellalkhenifra/admin"],
                    "include": {"infosection": False, "major_subdivision": True, "minor_subdivision": True},
                }
            ],
            slug="morocco",
            schema_version=2,
        )

        self.assertEqual(pages[0].path, "morocco/benimellalkhenifra/admin")
        self.assertEqual(pages[0].lowest_level, 1)

    def test_schema_v2_include_can_keep_only_infosection_without_tables(self):
        pages = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["cities"],
                    "include": {"cities": False, "infosection": True, "major_subdivision": False},
                }
            ],
            slug="france",
            schema_version=2,
        )

        self.assertEqual(pages[0].path, "france/cities")
        self.assertEqual(pages[0].lowest_level, 0)
        self.assertEqual(pages[0].include_tables, ("__none__",))
        self.assertTrue(pages[0].include_root)

    def test_schema_v2_explicit_include_root_false_overrides_infosection_include(self):
        pages = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["drenthe"],
                    "include": {"cities": True, "infosection": True, "major_subdivision": True},
                    "include_root": False,
                }
            ],
            slug="netherlands",
            schema_version=2,
        )

        self.assertFalse(pages[0].include_root)
        self.assertNotIn("infosection", pages[0].include_sections)


class CityPopulationLinkingTests(unittest.TestCase):
    def test_self_parent_link_is_repaired_to_scoped_prefix_parent(self):
        result = normalize_citypopulation_entities(
            "spain",
            [
                ScrapedAdminArea(code="spain", name="Spain", level=0, country_code="spain"),
                ScrapedAdminArea(code="PV", name="Pais Vasco", level=1, country_code="spain", parent_code="spain"),
                ScrapedAdminArea(code="01", name="Alava", level=2, country_code="spain", parent_code="PV"),
                ScrapedAdminArea(
                    code="01047",
                    name="Erriberabeitia",
                    level=4,
                    country_code="spain",
                    parent_code="01047",
                    entity_type="Municipality",
                    url="https://www.citypopulation.de/en/spain/localities/alava/01047__erriberabeitia/",
                    annotations="CityPopulation section: cities",
                ),
            ],
        )

        fixed = next(entity for entity in result if entity.code == "01047")
        self.assertEqual(fixed.parent_code, "01")
        self.assertEqual(fixed.level, 3)

    def test_forced_parent_level_can_reparent_city_across_level_gap(self):
        result = normalize_citypopulation_entities(
            "testland",
            [
                ScrapedAdminArea(code="testland", name="Testland", level=0, country_code="testland"),
                ScrapedAdminArea(code="10", name="North", level=1, country_code="testland", parent_code="testland"),
                ScrapedAdminArea(code="101", name="Example Parent", level=3, country_code="testland", parent_code="10"),
                ScrapedAdminArea(
                    code="city-1",
                    name="Example City",
                    level=1,
                    country_code="testland",
                    entity_type="Urban Center",
                    url="https://www.citypopulation.de/en/testland/north/example_parent/city_1/",
                    annotations="CityPopulation section: cities; Forced parent level: 3",
                ),
            ],
        )

        city = next(entity for entity in result if entity.code == "city-1")
        self.assertEqual(city.parent_code, "101")
        self.assertEqual(city.level, 4)


class ScrapeAdminAreasTests(unittest.TestCase):
    def test_run_rewrites_same_page_root_parent_alias_before_block_validation(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[RootAliasCitiesScraper()])
        config = ScrapingJobConfig(
            slug="malta",
            country_code="malta",
            base_url="https://example.test/en/",
            legal_subdivision_level=1,
            pages=[
                ScrapingPageConfig(
                    path="malta/cities",
                    html_format="cities",
                    lowest_level=0,
                    include_sections=("infosection", "cities"),
                    include_tables=("ts",),
                )
            ],
        )

        use_case.run(config)

        child = next(entity for entity in repository.saved_entities if entity.code == "child")
        self.assertEqual(child.parent_code, "malta")

    def test_run_retries_transient_save_failures_before_failing_job(self):
        repository = FlakySaveRepository(failures_before_success=2)
        events = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[FakeScraper()],
            on_persistence_progress=lambda config, progress: events.append(progress),
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[ScrapingPageConfig(path="fake/admin", html_format="table", lowest_level=0)],
        )

        result = use_case.run(config)

        self.assertEqual(result.created, 2)
        self.assertEqual(repository.save_attempts, 3)
        self.assertEqual([event.count for event in events if event.phase == "save_retry"], [1, 2])

    def test_run_fails_block_before_persistence_when_configured_section_is_missing(self):
        repository = FakeRepository()
        logs = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[MissingSectionCitiesScraper()],
            on_block_validation_error=lambda config, error: logs.append(error.to_log_text()),
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(
                    path="fake/cities",
                    html_format="cities",
                    lowest_level=0,
                    include_sections=("infosection", "major_subdivision", "cities"),
                )
            ],
        )

        with self.assertRaises(ScrapeBlockValidationError) as raised:
            use_case.run(config)

        self.assertEqual(repository.saved_entities, [])
        self.assertIn("SCR-BLOCK-001", str(raised.exception))
        self.assertEqual(len(logs), 1)
        self.assertIn("SCR-BLOCK-SECTION", logs[0])
        self.assertIn("https://example.test/en/fake/cities/", logs[0])
        self.assertIn("<html><main>missing cities table</main></html>", logs[0])

    def test_run_fails_block_before_persistence_when_non_root_level_has_no_parent(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[MissingParentAdminScraper()])
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(
                    path="fake/admin",
                    html_format="admin",
                    lowest_level=0,
                    include_sections=("infosection", "major_subdivision", "minor_subdivision"),
                )
            ],
        )

        with self.assertRaises(ScrapeBlockValidationError) as raised:
            use_case.run(config)

        self.assertEqual(repository.saved_entities, [])
        self.assertIn("SCR-BLOCK-PARENT", raised.exception.to_log_text())
        self.assertIn("Province One", raised.exception.to_log_text())

    def test_run_fails_link_validation_before_persistence_when_v2_rows_remain_unlinked(self):
        repository = FakeRepository()
        logs = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[UnlinkedV2AdminScraper()],
            on_link_validation_error=lambda config, error: logs.append(error.to_log_text()),
        )
        config = ScrapingJobConfig(
            slug="algeria",
            country_code="algeria",
            base_url="https://www.citypopulation.de/en/",
            pages=[
                ScrapingPageConfig(
                    path="algeria/admin",
                    html_format="admin",
                    lowest_level=1,
                    include_sections=("major_subdivision",),
                )
            ],
        )

        with self.assertRaises(ScrapeLinkValidationError) as raised:
            use_case.run(config)

        self.assertEqual(repository.saved_entities, [])
        self.assertIn("SCR-LINK-001", str(raised.exception))
        self.assertEqual(len(logs), 1)
        self.assertIn("SCR-LINK-PARENT", logs[0])
        self.assertIn("Saida", logs[0])
        self.assertIn("<html><main>saida province</main></html>", logs[0])

    def test_run_repairs_missing_parent_with_wikidata_before_link_validation(self):
        repository = FakeRepository()
        events = []

        def parent_resolver(config, entities):
            return link_entities_with_wikidata_parent_candidates(
                config.country_code,
                entities,
                [
                    WikidataParentCandidate(
                        child_qid="Q228666",
                        parent_qid="Q1000",
                        parent_label="Tynaarlo",
                    )
                ],
            )

        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[NetherlandsAdminHierarchyScraper(), NetherlandsParentlessUrbanCenterScraper()],
            wikimedia_parent_resolver=parent_resolver,
            on_persistence_progress=lambda config, progress: events.append((progress.phase, progress.status)),
        )
        config = ScrapingJobConfig(
            slug="netherlands",
            country_code="netherlands",
            base_url="https://www.citypopulation.de/en/",
            pages=[
                ScrapingPageConfig(
                    path="netherlands/admin",
                    html_format="admin",
                    lowest_level=0,
                    include_sections=("infosection", "major_subdivision", "minor_subdivision"),
                    block_index=0,
                ),
                ScrapingPageConfig(
                    path="netherlands/drenthe",
                    html_format="cities",
                    lowest_level=1,
                    parent_level=2,
                    include_sections=("cities",),
                    block_index=1,
                ),
            ],
        )

        use_case.run(config)

        by_code = {str(entity.code): entity for entity in repository.saved_entities}
        self.assertEqual(by_code["BK00261"].parent_code, "GM1730")
        self.assertEqual(by_code["BK00261"].level, 3)
        self.assertLess(
            events.index(("wikimedia_parent_links", "done")),
            events.index(("link_validation", "start")),
        )

    def test_wikidata_parent_linking_can_use_unique_parent_label_when_parent_qid_is_missing(self):
        result = link_entities_with_wikidata_parent_candidates(
            "netherlands",
            [
                ScrapedAdminArea(code="netherlands", name="Netherlands", level=0, country_code="netherlands"),
                ScrapedAdminArea(code="NL01", name="Drenthe", level=1, country_code="netherlands", parent_code="netherlands"),
                ScrapedAdminArea(code="GM1730", name="Tynaarlo", level=2, country_code="netherlands", parent_code="NL01"),
                ScrapedAdminArea(
                    code="BK00261",
                    name="Zuidlaren",
                    level=1,
                    country_code="netherlands",
                    data_wd="Q228666",
                    annotations="Forced parent level: 2",
                ),
            ],
            [WikidataParentCandidate(child_qid="Q228666", parent_qid="Q999999", parent_label="Tynaarlo")],
        )

        by_code = {str(entity.code): entity for entity in result}
        self.assertEqual(by_code["BK00261"].parent_code, "GM1730")
        self.assertEqual(by_code["BK00261"].level, 3)

    def test_wikidata_parent_linking_falls_back_to_unique_url_scope_parent(self):
        result = link_entities_with_wikidata_parent_candidates(
            "netherlands",
            [
                ScrapedAdminArea(code="netherlands", name="Netherlands", level=0, country_code="netherlands"),
                ScrapedAdminArea(code="OV", name="Overijssel", level=1, country_code="netherlands", parent_code="netherlands"),
                ScrapedAdminArea(
                    code="BK00809",
                    name="Steenenkamer / De Hoven",
                    level=1,
                    country_code="netherlands",
                    data_wd="Q1221157",
                    annotations="Forced parent level: 2",
                    url="https://www.citypopulation.de/en/netherlands/overijssel/_/BK00809__steenenkamer_de_hoven/",
                ),
            ],
            [],
        )

        by_code = {str(entity.code): entity for entity in result}
        self.assertEqual(by_code["BK00809"].parent_code, "OV")
        self.assertEqual(by_code["BK00809"].level, 2)

    def test_run_links_parentless_level_one_rows_to_existing_country_root(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[AlgeriaRootCitiesScraper(), AlgeriaParentlessProvinceAdminScraper()],
        )
        config = ScrapingJobConfig(
            slug="algeria",
            country_code="algeria",
            base_url="https://www.citypopulation.de/en/",
            pages=[
                ScrapingPageConfig(
                    path="algeria/cities",
                    html_format="cities",
                    lowest_level=0,
                    include_sections=("infosection",),
                    block_index=0,
                ),
                ScrapingPageConfig(
                    path="algeria/admin",
                    html_format="admin",
                    lowest_level=1,
                    include_sections=("major_subdivision",),
                    block_index=1,
                ),
            ],
        )

        use_case.run(config)

        by_code = {str(entity.code): entity for entity in repository.saved_entities}
        self.assertEqual(by_code["20"].parent_code, "algeria")
        self.assertEqual(by_code["20"].level, 1)

    def test_orphan_rows_stay_unlinked_without_sum_to_root_and_are_reported(self):
        repository = FakeRepository()
        reported = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[OrphanFranceDepartmentScraper()],
            on_unlinked_entities=lambda config, entities: reported.extend(entities),
        )
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/cities/guyane", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertIsNone(by_code["france_guyane"].parent_code)
        self.assertEqual(by_code["france_guyane__repeat2"].parent_code, "france_guyane")
        self.assertEqual([entity.code for entity in reported], ["france_guyane"])

    def test_orphan_rows_from_sum_to_root_pages_attach_to_country_root(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[OrphanFranceDepartmentScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[
                ScrapingPageConfig(
                    path="france/cities/guyane",
                    html_format="table",
                    lowest_level=1,
                    sum_to_root=True,
                )
            ],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["france_guyane"].parent_code, "france")
        self.assertEqual(by_code["france_guyane__repeat2"].parent_code, "france_guyane")


    def test_run_links_french_arrondissements_to_departments_and_shifts_communes(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[FranceDepartmentDistrictScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/admin", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["011"].parent_code, "01")
        self.assertEqual(by_code["011"].level, 3)
        self.assertEqual(by_code["01034"].parent_code, "011")
        self.assertEqual(by_code["01034"].level, 4)

    def test_run_links_parentless_rows_by_scoped_conflict_code(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[FranceScopedCodeOrphanDistrictScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/charentemaritime", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["17_174"].parent_code, "17")
        self.assertEqual(by_code["17_174"].level, 3)
        self.assertEqual(by_code["17415"].parent_code, "17_174")
        self.assertEqual(by_code["17415"].level, 4)

    def test_run_generically_repairs_province_district_commune_hierarchy(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[GenericProvinceDistrictScraper()])
        config = ScrapingJobConfig(
            slug="testland",
            country_code="testland",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="testland/admin", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["101"].parent_code, "10")
        self.assertEqual(by_code["101"].level, 3)
        self.assertEqual(by_code["10101"].parent_code, "101")
        self.assertEqual(by_code["10101"].level, 4)

    def test_run_marks_french_overseas_department_and_links_its_districts(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[FranceOverseasDepartmentDistrictScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/cities/guyane", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["france_guyane"].entity_type, "Region [overseas]")
        self.assertEqual(by_code["france_guyane__repeat2"].entity_type, "Department [overseas]")
        self.assertEqual(by_code["1181"].parent_code, "france_guyane__repeat2")
        self.assertEqual(by_code["1181"].level, 3)
        self.assertEqual(by_code["9124"].parent_code, "1181")
        self.assertEqual(by_code["9124"].level, 4)

    def test_run_does_not_rewire_mainland_france_communes_to_reunion_code_prefix(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[FranceEssonneReunionCodeCollisionScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/cities/reunion", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["91521"].parent_code, "912")
        self.assertEqual(by_code["91521"].level, 4)

    def test_run_keeps_same_named_overseas_block_anchors_separate_by_page_scope(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[FranceOverseasSameNameBlockAnchorScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/cities/martinique", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        martinique_children = {entity.name for entity in repository.saved_entities if entity.parent_code == "MQ-SP"}
        reunion_children = {entity.name for entity in repository.saved_entities if entity.parent_code == "RE-SP"}
        mayotte_children = [entity for entity in repository.saved_entities if entity.parent_code == "YT-GT"]
        guadeloupe_children = [entity for entity in repository.saved_entities if entity.parent_code == "GP-GT"]

        self.assertIn("MQ-SP", by_code)
        self.assertIn("RE-SP", by_code)
        self.assertIn("GP-GT", by_code)
        self.assertIn("YT-GT", by_code)
        self.assertEqual(martinique_children, {"Bellefontaine"})
        self.assertEqual(reunion_children, {"Cilaos", "Le Tampon"})
        self.assertEqual(len(mayotte_children), 15)
        self.assertEqual([entity.name for entity in guadeloupe_children], ["Les Abymes"])

    def test_run_does_not_rewire_scoped_mainland_codes_to_unscoped_overseas_prefixes(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[FranceUnscopedOverseasPrefixCollisionScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/admin", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["65270"].parent_code, "652")
        self.assertEqual(by_code["65270"].level, 4)
        self.assertEqual(by_code["81250"].parent_code, "812")
        self.assertEqual(by_code["81250"].level, 4)


    def test_run_collapses_same_real_city_after_role_normalization(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[DuplicateCityAfterNormalizationScraper()])
        config = ScrapingJobConfig(
            slug="testland",
            country_code="testland",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="testland/admin", html_format="table", lowest_level=0)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertIn("75056", by_code)
        self.assertNotIn("751", by_code)
        self.assertEqual(by_code["75056"].parent_code, "75")
        self.assertEqual(by_code["75056"].level, 3)
        paris_children = [
            entity
            for entity in repository.saved_entities
            if entity.parent_code == "75" and entity.data_wd == "Q90"
        ]
        self.assertEqual([entity.code for entity in paris_children], ["75056"])

    def test_run_forced_parent_level_links_same_qid_city_child(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[DuplicateCityAfterNormalizationScraper()])
        config = ScrapingJobConfig(
            slug="testland",
            country_code="testland",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="testland/admin", html_format="table", lowest_level=0, parent_level=3)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertIn("751", by_code)
        self.assertIn("75056", by_code)
        self.assertEqual(by_code["751"].parent_code, "75")
        self.assertEqual(by_code["751"].level, 3)
        self.assertEqual(by_code["75056"].parent_code, "751")
        self.assertEqual(by_code["75056"].level, 4)

    def test_run_collapses_same_real_brussels_city_after_role_normalization(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[DuplicateBrusselsAfterNormalizationScraper()])
        config = ScrapingJobConfig(
            slug="belgium",
            country_code="belgium",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="belgium/cities", html_format="table", lowest_level=0)],
        )

        use_case.run(config)

        brussels_children = [
            entity
            for entity in repository.saved_entities
            if entity.parent_code == "21000" and entity.data_wd == "Q239"
        ]
        self.assertEqual(len(brussels_children), 1)
        self.assertEqual(brussels_children[0].level, 4)
        self.assertIn(brussels_children[0].code, {"7021", "21004"})

    def test_run_deduplicates_applies_area_overrides_and_saves_ids(self):
        repository = FakeRepository()
        starts = []
        completes = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[FakeScraper()],
            on_page_start=starts.append,
            on_page_complete=completes.append,
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            legal_subdivision_level=1,
            reset_before_import=True,
            pages=[
                ScrapingPageConfig(
                    path="fake/admin",
                    html_format="table",
                    lowest_level=0,
                    area_km2=Decimal("10"),
                    area_overrides={"child": Decimal("2")},
                )
            ],
        )

        result = use_case.run(config)

        self.assertEqual(result.found, 2)
        self.assertEqual(result.created, 2)
        self.assertEqual(repository.reset_countries, ["fake"])
        self.assertEqual(repository.saved_country, "fake")
        self.assertEqual(repository.deleted_ids, {"fake_fake", "fake_child"})
        self.assertEqual([entity.name for entity in repository.saved_entities], ["Testland", "Child"])

        root, child = repository.saved_entities
        self.assertEqual(root.area_km2, Decimal("10"))
        self.assertEqual(root.density, Decimal("10"))
        self.assertEqual(child.area_km2, Decimal("2"))
        self.assertEqual(child.density, Decimal("25"))

        self.assertEqual(starts[0].url, "https://example.test/en/fake/admin/")
        self.assertEqual(completes[0].found, 3)
        self.assertEqual(repository.most_populated_assignments, [])
        self.assertEqual(root.most_populated_city_code, "child")
        self.assertIsNone(child.most_populated_city_code)

    def test_run_exposes_downloaded_html_on_page_complete_when_scraper_supports_it(self):
        repository = FakeRepository()
        completes = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[FakeHtmlScraper()],
            on_page_complete=completes.append,
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            legal_subdivision_level=1,
            pages=[ScrapingPageConfig(path="fake/admin", html_format="table", lowest_level=0)],
        )

        use_case.run(config)

        self.assertEqual(completes[0].html, "<html>page</html>")
        self.assertEqual([entity.name for entity in completes[0].entities], ["Testland", "Child", "Duplicate Child"])

    def test_run_links_localities_by_citypopulation_code_prefix_after_data_wd(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[SpainLocalityCodePrefixScraper()])
        config = ScrapingJobConfig(
            slug="spain",
            country_code="spain",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="spain/localities/alicante", html_format="table", lowest_level=2)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["03082000202"].parent_code, "03082")

    def test_run_links_single_province_community_municipalities_to_numeric_province_prefix(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[SpainSingleProvinceCommunityScraper()])
        config = ScrapingJobConfig(
            slug="spain",
            country_code="spain",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="spain/madrid", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["28"].parent_code, "MAD")
        self.assertEqual(by_code["28079"].parent_code, "28")
        self.assertEqual(by_code["28079"].level, 3)

    def test_run_uses_parent_hint_to_link_locality_to_intermediate_parent(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[PortugalParentHintScraper()])
        config = ScrapingJobConfig(
            slug="portugal",
            country_code="portugal",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="portugal/aveiro", html_format="table", lowest_level=2)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["L1"].parent_code, "010121")
        self.assertEqual(by_code["L1"].level, 4)

    def test_run_keeps_multiword_url_scope_parent_over_numeric_prefix_parent(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[PortugalMultiwordUrlScopeScraper()])
        config = ScrapingJobConfig(
            slug="portugal",
            country_code="portugal",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="portugal/admin/castelo_branco", html_format="table", lowest_level=1)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["1690502"].parent_code, "05")
        self.assertEqual(by_code["1690502"].level, 2)

    def test_unknown_scraper_fails_before_persistence(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[])
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[ScrapingPageConfig(path="fake/admin", html_format="missing")],
        )

        with self.assertRaisesRegex(ValueError, "Unknown html_format"):
            use_case.run(config)

        self.assertEqual(repository.saved_entities, [])

    def test_run_reuses_cached_page_without_scraping_it_again(self):
        repository = FakeRepository()
        starts = []
        cached_events = []
        cached_root = ScrapedAdminArea(
            code="fake",
            name="Cached Testland",
            level=0,
            country_code="fake",
            pop_latest=100,
        )
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[],
            on_page_start=starts.append,
            cached_page_loader=lambda page: CachedScrapePage(
                found=1,
                html="<html>cached</html>",
                entities=(cached_root,),
            ),
            on_cached_page=cached_events.append,
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[ScrapingPageConfig(path="fake/admin", html_format="missing", lowest_level=0)],
        )

        result = use_case.run(config)

        self.assertEqual(result.found, 1)
        self.assertEqual(starts, [])
        self.assertEqual(cached_events[0].html, "<html>cached</html>")
        self.assertEqual([entity.name for entity in repository.saved_entities], ["Cached Testland"])
    def test_run_collapses_belgium_duplicate_provinces_and_rewires_districts(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[BelgiumDuplicateAdministrativeRowsScraper()])
        config = ScrapingJobConfig(
            slug="belgium",
            country_code="belgium",
            base_url="https://www.citypopulation.de/en/",
            legal_subdivision_level=4,
            pages=[ScrapingPageConfig(path="belgium/cities", html_format="table", lowest_level=0)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertIn("549", by_code)
        self.assertNotIn("10000", by_code)
        self.assertEqual(by_code["549"].level, 2)
        self.assertEqual(by_code["549"].parent_code, "554")
        self.assertEqual(by_code["11000"].level, 3)
        self.assertEqual(by_code["11000"].parent_code, "549")
        self.assertEqual(by_code["11002"].level, 4)
        self.assertEqual(by_code["11002"].parent_code, "11000")

    def test_run_drops_belgium_brussels_shortcut_child_when_official_district_exists(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[BelgiumBrusselsShortcutScraper()])
        config = ScrapingJobConfig(
            slug="belgium",
            country_code="belgium",
            base_url="https://www.citypopulation.de/en/",
            legal_subdivision_level=4,
            pages=[ScrapingPageConfig(path="belgium/cities", html_format="table", lowest_level=0)],
        )

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertIn("548", by_code)
        self.assertIn("04000", by_code)
        self.assertIn("21000", by_code)
        self.assertNotIn("8461", by_code)
        self.assertEqual(by_code["04000"].parent_code, "548")
        self.assertEqual(by_code["21000"].parent_code, "04000")
        self.assertEqual(by_code["7015"].parent_code, "21000")

    def test_run_drops_context_roots_and_chains_same_data_wd_rows(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[SpainAutonomousCityScraper()])
        config = ScrapingJobConfig(
            slug="spain",
            country_code="spain",
            base_url="https://www.citypopulation.de/en/",
            legal_subdivision_level=3,
            pages=[ScrapingPageConfig(path="spain/admin", html_format="table", lowest_level=0)],
        )

        result = use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(result.found, 6)
        self.assertNotIn("spain", by_code)
        self.assertEqual(by_code["AND"].parent_code, "espa_a")
        self.assertEqual(by_code["51"].parent_code, "CEU")
        self.assertEqual(by_code["51001"].parent_code, "51")
        self.assertEqual(by_code["51001000201"].parent_code, "51001")



class BelgiumDuplicateAdministrativeRowsScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="belgium", name="Belgium", level=0, country_code=country_code),
            ScrapedAdminArea(code="554", name="Vlaams Gewest", level=1, country_code=country_code, parent_code="belgium", data_wd="Q9337"),
            # Province from the grouped /cities/ table: correct regional parent, but CityPopulation symbol id.
            ScrapedAdminArea(
                code="549",
                name="Antwerpen",
                level=2,
                country_code=country_code,
                parent_code="554",
                entity_type="Prov",
                data_wd="Q1116",
                url="https://www.citypopulation.de/en/belgium/admin/10000__antwerpen/",
            ),
            # Same province from /admin/: official administrative code, but without region context.
            ScrapedAdminArea(
                code="10000",
                name="Antwerpen",
                level=2,
                country_code=country_code,
                parent_code="belgium",
                entity_type="Province",
                data_wd="Q1116",
                url="https://www.citypopulation.de/en/belgium/admin/10000__antwerpen/",
            ),
            ScrapedAdminArea(
                code="11000",
                name="Antwerpen",
                level=3,
                country_code=country_code,
                parent_code="10000",
                entity_type="Arrondissement",
                data_wd="Q90895",
                url="https://www.citypopulation.de/en/belgium/admin/antwerpen/11000__antwerpen/",
            ),
            ScrapedAdminArea(
                code="7016",
                name="Antwerpen",
                level=4,
                country_code=country_code,
                parent_code="549",
                entity_type="Mun",
                data_wd="Q12892",
                url="https://www.citypopulation.de/en/belgium/antwerpen/antwerpen/11002__antwerpen/",
            ),
            ScrapedAdminArea(
                code="11002",
                name="Antwerpen",
                level=4,
                country_code=country_code,
                parent_code="11000",
                entity_type="Municipality",
                data_wd="Q12892",
                url="https://www.citypopulation.de/en/belgium/antwerpen/antwerpen/11002__antwerpen/",
            ),
        ]


class BelgiumBrusselsShortcutScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="belgium", name="Belgium", level=0, country_code=country_code),
            ScrapedAdminArea(
                code="548",
                name="Région de Bruxelles",
                level=1,
                country_code=country_code,
                parent_code="belgium",
                entity_type="Reg",
                data_wd="Q240",
                url="https://www.citypopulation.de/en/belgium/brussels/",
            ),
            # Shortcut from the country /cities/ grouped table: same QID/role as the official
            # arrondissement, but attached directly under the region and with the same URL.
            ScrapedAdminArea(
                code="8461",
                name="Bruxelles-Capitale",
                level=2,
                country_code=country_code,
                parent_code="548",
                entity_type="Arr",
                data_wd="Q90870",
                url="https://www.citypopulation.de/en/belgium/brussels/",
            ),
            # Province-equivalent Brussels row from /admin/.
            ScrapedAdminArea(
                code="04000",
                name="Région de Bruxelles-Capitale",
                level=2,
                country_code=country_code,
                parent_code="548",
                entity_type="Region",
                data_wd="Q240",
                url="https://www.citypopulation.de/en/belgium/admin/04000__région_de_bruxelles_capi/",
            ),
            # Official arrondissement from /admin/.
            ScrapedAdminArea(
                code="21000",
                name="Bruxelles-Capitale",
                level=3,
                country_code=country_code,
                parent_code="04000",
                entity_type="Arrondissement",
                data_wd="Q90870",
                url="https://www.citypopulation.de/en/belgium/admin/région_de_bruxelles_capi/21000__bruxelles_capitale/",
            ),
            ScrapedAdminArea(
                code="7015",
                name="Anderlecht",
                level=4,
                country_code=country_code,
                parent_code="21000",
                entity_type="Mun",
                data_wd="Q12886",
                url="https://www.citypopulation.de/en/belgium/bruxelles/bruxelles_capitale/21001__anderlecht/",
            ),
        ]


class SpainAutonomousCityScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(
                code="espa_a",
                name="España",
                level=0,
                country_code=country_code,
            ),
            ScrapedAdminArea(
                code="CEU",
                name="Ceuta",
                level=1,
                country_code=country_code,
                parent_code="espa_a",
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/admin/",
            ),
            ScrapedAdminArea(
                code="51",
                name="Ceuta",
                level=2,
                country_code=country_code,
                parent_code="CEU",
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/ceuta/",
            ),
            ScrapedAdminArea(
                code=country_code,
                name="Ceuta",
                level=3,
                country_code=country_code,
                parent_code=country_code,
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/localities/ceuta/",
            ),
            ScrapedAdminArea(
                code="51001",
                name="Ceuta",
                level=3,
                country_code=country_code,
                parent_code=country_code,
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/ceuta/51001__ceuta/",
            ),
            ScrapedAdminArea(
                code="51001000201",
                name="Ceuta",
                level=4,
                country_code=country_code,
                parent_code=country_code,
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/localities/ceuta/ceuta/51001000201__ceuta/",
            ),
            ScrapedAdminArea(
                code="AND",
                name="Andalucía",
                level=1,
                country_code=country_code,
                parent_code=country_code,
                data_wd="Q5783",
            ),
        ]


class FakeHtmlFetcher:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return f"html:{url}"


class PrefetchHtmlScraper:
    html_format = "table"

    def scrape_html(self, html, url, country_code, level):
        code = url.rstrip("/").split("/")[-1]
        return [
            ScrapedAdminArea(
                code=code,
                name=html,
                level=level,
                country_code=country_code,
                pop_latest=1,
            )
        ]


class CountingPrefetchHtmlScraper(PrefetchHtmlScraper):
    def __init__(self):
        self.calls = []

    def scrape_html(self, html, url, country_code, level):
        self.calls.append((url, level))
        return super().scrape_html(html, url, country_code, level)


class PrefetchPipelineTests(unittest.TestCase):
    def test_page_prefetch_downloads_once_per_url_and_completes_in_config_order(self):
        repository = FakeRepository()
        starts = []
        completes = []
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
                ScrapingPageConfig(path="fake/b", html_format="table", lowest_level=2),
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=3),
            ],
        )

        fetcher = FakeHtmlFetcher()
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[PrefetchHtmlScraper()],
            on_page_start=starts.append,
            on_page_complete=completes.append,
            page_workers=3,
            html_fetcher=fetcher,
        )

        use_case.run(config)

        fetched_urls = fetcher.urls
        self.assertEqual(
            fetched_urls,
            [
                "https://example.test/en/fake/a/",
                "https://example.test/en/fake/b/",
            ],
        )
        self.assertEqual([event.url for event in starts], [
            "https://example.test/en/fake/a/",
            "https://example.test/en/fake/b/",
            "https://example.test/en/fake/a/",
        ])
        self.assertEqual([event.url for event in completes], [
            "https://example.test/en/fake/a/",
            "https://example.test/en/fake/b/",
            "https://example.test/en/fake/a/",
        ])
        self.assertEqual([entity.level for entity in repository.saved_entities], [1, 2])
        self.assertEqual(repository.saved_entities[0].name, "html:https://example.test/en/fake/a/")


    def test_page_prefetch_reuses_parsed_duplicate_pages(self):
        repository = FakeRepository()
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
            ],
        )

        fetcher = FakeHtmlFetcher()
        scraper = CountingPrefetchHtmlScraper()
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[scraper],
            page_workers=2,
            html_fetcher=fetcher,
        )

        use_case.run(config)

        self.assertEqual(fetcher.urls, ["https://example.test/en/fake/a/"])
        self.assertEqual(scraper.calls, [("https://example.test/en/fake/a/", 1)])

    def test_page_prefetch_requires_injected_html_fetcher(self):
        repository = FakeRepository()
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
                ScrapingPageConfig(path="fake/b", html_format="table", lowest_level=2),
            ],
        )
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[FakeHtmlScraper()],
            page_workers=3,
        )

        use_case.run(config)

        self.assertEqual([entity.name for entity in repository.saved_entities], ["Testland", "Child"])


class SpanishSyntheticRootScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="spain", name="Spain", level=0, country_code="spain", pop_latest=1),
            ScrapedAdminArea(code="51", name="Ceuta", level=1, country_code="spain", parent_code="spain", pop_latest=1),
            ScrapedAdminArea(
                code="spain",
                name="Ceuta (Autonomous City)",
                level=1,
                country_code="spain",
                parent_code=None,
                pop_latest=1,
                url="https://www.citypopulation.de/en/spain/ceuta/",
            ),
            ScrapedAdminArea(
                code="51001",
                name="Ceuta",
                level=2,
                country_code="spain",
                parent_code="spain",
                pop_latest=1,
                url="https://www.citypopulation.de/en/spain/ceuta/ceuta/51001__ceuta/",
            ),
        ]


class SpanishSyntheticRootTests(unittest.TestCase):
    def test_autonomous_city_synthetic_root_children_attach_to_real_root(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[SpanishSyntheticRootScraper()])
        config = ScrapingJobConfig(
            slug="spain",
            country_code="spain",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="spain/ceuta", html_format="table", lowest_level=1)],
        )

        result = use_case.run(config)

        self.assertEqual(result.found, 3)
        municipality = next(entity for entity in repository.saved_entities if entity.code == "51001")
        self.assertEqual(municipality.parent_code, "51")
        self.assertEqual(municipality.level, 2)
        self.assertEqual(
            [entity.name for entity in repository.saved_entities if entity.code == "spain"],
            ["Spain"],
        )

class RuntimeConfigExtensionScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(
                code="france",
                name="France",
                level=0,
                country_code="france",
                area_km2=Decimal("543940"),
                pop_latest=68_000_000,
            ),
            ScrapedAdminArea(
                code="IDF",
                name="Île-de-France",
                level=2,
                country_code="france",
                parent_code="france",
                pop_latest=12_000_000,
            ),
            ScrapedAdminArea(
                code="GUF",
                name="French Guiana",
                level=3,
                country_code="france",
                parent_code=None,
                area_km2=Decimal("83534"),
                pop_latest=298_554,
            ),
        ]


class RuntimeConfigExtensionTests(unittest.TestCase):
    def test_synthetic_containers_parent_overrides_and_root_metric_additions(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[RuntimeConfigExtensionScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/admin", html_format="table", lowest_level=0)],
        )
        object.__setattr__(
            config,
            "runtime_synthetic_entities",
            (
                {
                    "code": "METRO",
                    "name": "Metropolitan France",
                    "level": 1,
                    "parent_code": "france",
                    "copy_metrics_from": "france",
                },
                {
                    "code": "OVERSEAS",
                    "name": "Overseas France",
                    "level": 1,
                    "parent_code": "france",
                    "metric_source_codes": ("GUF",),
                },
            ),
        )
        object.__setattr__(
            config,
            "runtime_parent_overrides",
            (
                {"match_levels": (2,), "parent_code": "METRO", "exclude_codes": ("METRO", "OVERSEAS")},
                {"codes": ("GUF",), "parent_code": "OVERSEAS", "level": 3},
            ),
        )
        object.__setattr__(config, "runtime_root_metric_sources", ({"code": "GUF"},))

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["METRO"].parent_code, "france")
        self.assertEqual(by_code["METRO"].pop_latest, 68_000_000)
        self.assertEqual(by_code["OVERSEAS"].pop_latest, 298_554)
        self.assertEqual(by_code["IDF"].parent_code, "METRO")
        self.assertEqual(by_code["GUF"].parent_code, "OVERSEAS")
        self.assertEqual(by_code["france"].pop_latest, 68_298_554)
        self.assertEqual(by_code["france"].area_km2, Decimal("627474"))


class SpanishRepeatedInfoSectionScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="spain", name="España", level=0, country_code="spain"),
            ScrapedAdminArea(
                code="CEU",
                name="Ceuta",
                level=1,
                country_code="spain",
                parent_code="spain",
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/admin/CEU__ceuta/",
            ),
            ScrapedAdminArea(
                code="51",
                name="Ceuta",
                level=2,
                country_code="spain",
                parent_code="CEU",
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/admin/ceuta/51__ceuta/",
            ),
            ScrapedAdminArea(
                code="spain_ceuta",
                name="Ceuta",
                level=1,
                country_code="spain",
                parent_code="spain",
                url="https://www.citypopulation.de/en/spain/ceuta/",
                annotations="Raíz de página repetida",
            ),
            ScrapedAdminArea(
                code="spain_ceuta__repeat2",
                name="Ceuta",
                level=2,
                country_code="spain",
                parent_code="spain_ceuta",
                url="https://www.citypopulation.de/en/spain/ceuta/",
                annotations="Raíz de página repetida; Duplicación explícita por página",
            ),
            ScrapedAdminArea(
                code="51001",
                name="Ceuta",
                level=3,
                country_code="spain",
                parent_code="spain_ceuta__repeat2",
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/ceuta/ceuta/51001__ceuta/",
            ),
            ScrapedAdminArea(
                code="51001000201",
                name="Ceuta",
                level=4,
                country_code="spain",
                parent_code="51001",
                data_wd="Q5823",
                url="https://www.citypopulation.de/en/spain/localities/ceuta/ceuta/51001000201__ceuta/",
            ),
        ]


class SpainRepeatedInfoSectionTests(unittest.TestCase):
    def test_repeated_infosection_roots_reuse_real_same_level_entities(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[SpanishRepeatedInfoSectionScraper()])
        config = ScrapingJobConfig(
            slug="spain",
            country_code="spain",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="spain/ceuta", html_format="table", lowest_level=1)],
        )

        result = use_case.run(config)
        by_code = {entity.code: entity for entity in repository.saved_entities}

        self.assertEqual(result.found, 5)
        self.assertNotIn("spain_ceuta", by_code)
        self.assertNotIn("spain_ceuta__repeat2", by_code)
        self.assertEqual(by_code["CEU"].parent_code, "spain")
        self.assertEqual(by_code["51"].parent_code, "CEU")
        self.assertEqual(by_code["51001"].level, 3)
        self.assertEqual(by_code["51001"].parent_code, "51")
        self.assertEqual(by_code["51001000201"].level, 4)
        self.assertEqual(by_code["51001000201"].parent_code, "51001")
