from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.error import HTTPError
from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from unittest.mock import patch

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.admin import CityPopulationAdminScraper
from ciudades_del_mundo.services.visual_assets import (
    _asset_download_filename,
    _asset_download_url,
    _download_asset,
    _commons_filename_from_url,
    _first_local_asset_path,
    _kind_from_text,
    _local_asset_file_is_usable,
    _media_url,
    _safe_filename,
    _wikidata_candidates,
    commons_file_url,
    get_visual_assets_for_entity,
    seed_visual_assets_from_scraped_page,
)
from ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets import (
    Command as ScrapeWithAssetsCommand,
    _assign_wikidata_assets_from_scraped_data_wd,
    _asset_subdivision_levels_for_scrape,
    _citypopulation_country_qid_candidate_urls,
    _country_wikidata_id_from_citypopulation_html,
)


def _normalize_for_test(value: str) -> str:
    import unicodedata

    return "".join(
        char
        for char in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(char)
    ).strip()


class VisualAssetModelDeclarationTests(TestCase):
    def test_visual_asset_models_are_declared_to_match_existing_migrations(self):
        self.assertEqual(
            apps.get_model("ciudades_del_mundo", "VisualAsset")._meta.db_table,
            "ciudades_del_mundo_visual_asset",
        )
        translation_model = apps.get_model("ciudades_del_mundo", "VisualAssetTranslation")
        self.assertEqual(
            translation_model._meta.get_field("asset").remote_field.model._meta.object_name,
            "VisualAsset",
        )


class CityPopulationDataWdScrapingTests(TestCase):
    def test_admin_table_rows_store_citypopulation_data_wd(self):
        html = """
        <table id="tl">
          <thead><tr><th class="rpop" data-coldate="2024-01-01">Population</th></tr></thead>
          <tbody class="admin1">
            <tr>
              <td id="i01" class="rname" data-wd="Q115119">
                <span itemprop="name">Pinar del Río</span>
              </td>
              <td class="rpop" style="display:table-cell">500000</td>
            </tr>
          </tbody>
        </table>
        """

        entities = CityPopulationAdminScraper().scrape_html(
            html,
            "https://www.citypopulation.de/en/cuba/admin/",
            "cuba",
            0,
        )

        province = next(entity for entity in entities if entity.code == "01")
        self.assertEqual(province.data_wd, "Q115119")

    def test_infosection_root_data_wd_prefers_country_candidate_over_subregion(self):
        html = """
        <div class="infosection mainsection">
          <p class="infoname"><span data-wd="Q233" data-wiki="Malta">Malta Republic</span></p>
          <p class="infotext">Country</p>
          <a data-wd="Q2054290" data-wiki="Gozo &amp; Comino District">Gozo &amp; Comino District</a>
        </div>
        """

        entities = CityPopulationAdminScraper().scrape_html(
            html,
            "https://www.citypopulation.de/en/malta/admin/",
            "malta",
            0,
        )

        self.assertEqual(entities[0].data_wd, "Q233")



class VisualAssetSeedingTests(TestCase):
    def _asset_rows(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT entity_type, entity_key, country_code, kind, remote_url, source_url, source, status
                       , wikidata_id
                  FROM ciudades_del_mundo_visual_asset
                 ORDER BY entity_type, entity_key, kind
                """
            )
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def test_country_qid_parser_uses_matching_country_text_not_structural_subregion(self):
        html = """
        <nav><a data-wd="Q233">Malta</a></nav>
        <table>
          <tfoot><tr data-wd="Q2054290"><td>Gozo &amp; Comino District</td></tr></tfoot>
        </table>
        """

        qid = _country_wikidata_id_from_citypopulation_html(html, {_normalize_for_test("malta")})

        self.assertEqual(qid, "Q233")

    def test_country_qid_parser_ignores_structural_candidate_without_country_name(self):
        html = """
        <table>
          <tfoot><tr data-wd="Q2054290"><td>Gozo &amp; Comino District</td></tr></tfoot>
        </table>
        """

        qid = _country_wikidata_id_from_citypopulation_html(html, {_normalize_for_test("malta")})

        self.assertEqual(qid, "")

    def test_citypopulation_country_qid_urls_normalize_raw_paths_with_slug(self):
        config = SimpleNamespace(
            base_url="https://www.citypopulation.de/en/",
            pages=[SimpleNamespace(path="malta/admin", html_format="admin", lowest_level=0)],
        )
        config_data = {"pages": [{"source": "admin", "path": ["admin"], "lowest_level": 0}]}

        urls = _citypopulation_country_qid_candidate_urls(config, config_data, slug="malta")

        self.assertEqual(urls[0], "https://www.citypopulation.de/en/malta/")
        self.assertIn("https://www.citypopulation.de/en/malta/admin/", urls)
        self.assertNotIn("https://www.citypopulation.de/en/admin/", urls)

    def test_scrape_with_assets_separates_data_scrape_from_asset_resolution(self):
        command = ScrapeWithAssetsCommand()
        config = SimpleNamespace(
            country_code="cuba",
            base_url="https://www.citypopulation.de/en/",
            pages=[],
        )
        options = {
            "slug": "cuba",
            "page_workers": 4,
            "clear_first": False,
            "skip_assets": False,
            "no_download_assets": True,
            "download_assets": False,
            "skip_subdivision_assets": False,
            "subdivision_asset_levels": "",
            "max_individual_wikidata_lookups": 0,
            "skip_country_bulk_assets": False,
            "allow_missing_assets": True,
            "resume": False,
            "ai_enrich": False,
            "ai_languages": "es,en",
            "ai_translate_area_names": False,
            "ai_limit": 100,
        }

        with (
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets.PythonScrapingConfigRepository"
            ) as repository_cls,
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._config_data_for_slug",
                return_value={"visual_assets": {"required_levels": [1]}},
            ),
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets.call_command"
            ) as call_command_mock,
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets.visual_asset_tables_exist",
                return_value=True,
            ),
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._citypopulation_country_wikidata_id",
                return_value="Q241",
            ),
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._assign_wikidata_assets_from_scraped_data_wd",
                return_value=0,
            ) as assign_by_data_wd,
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._assign_bulk_country_wikidata_assets",
                return_value=0,
            ) as legacy_bulk,
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._mirror_country_assets_to_root_admin_areas",
                return_value=0,
            ),
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._apply_configured_wikidata_asset_overrides",
                return_value=0,
            ),
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets.write_config_progress"
            ),
        ):
            repository_cls.return_value.get.return_value = config

            command.handle(**options)

        scrape_call = next(call for call in call_command_mock.call_args_list if call.args[0] == "scrape_subdivisions")
        self.assertFalse(scrape_call.kwargs["seed_assets_from_pages"])
        self.assertTrue(scrape_call.kwargs["skip_subdivision_assets"])
        self.assertEqual(scrape_call.kwargs["asset_subdivision_levels"], "")
        self.assertEqual(scrape_call.kwargs["max_individual_wikidata_lookups"], 0)
        assign_by_data_wd.assert_called_once()
        self.assertEqual(assign_by_data_wd.call_args.kwargs["country_wikidata_id"], "Q241")
        self.assertEqual(assign_by_data_wd.call_args.kwargs["levels"], [1])
        legacy_bulk.assert_not_called()

    def test_assign_assets_from_scraped_data_wd_uses_values_sparql_instead_of_many_entitydata_calls(self):
        from ciudades_del_mundo.models import AdminArea

        AdminArea.objects.create(
            id="aa",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            data_wd="Q10",
        )
        AdminArea.objects.create(
            id="aa_pin",
            country_code="aa",
            code="pin",
            name="Pinar del Río",
            level=1,
            data_wd="Q115119",
        )

        with (
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._wikidata_visual_asset_filenames",
                return_value={"flag": "Flag of AA.svg", "coat": "Coat of AA.svg"},
            ),
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._wikidata_sparql_json",
                return_value={
                    "results": {
                        "bindings": [
                            {
                                "item": {"value": "http://www.wikidata.org/entity/Q115119"},
                                "kind": {"value": "coat"},
                                "asset": {"value": "Coat of Pinar del Rio.svg"},
                            }
                        ]
                    }
                },
            ) as sparql_json,
            patch(
                "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets._wikidata_entitydata_batch_json"
            ) as batch_json,
        ):
            applied = _assign_wikidata_assets_from_scraped_data_wd(
                "aa",
                "aa",
                config_data={},
                country_wikidata_id="Q10",
                required_kinds=["flag", "coat"],
                levels=[1],
            )

        sparql_json.assert_called_once()
        self.assertEqual(sparql_json.call_args.kwargs["method"], "POST")
        self.assertIn("VALUES ?item", sparql_json.call_args.args[0])
        self.assertIn("wd:Q115119", sparql_json.call_args.args[0])
        batch_json.assert_not_called()
        rows = self._asset_rows()
        self.assertGreaterEqual(applied, 3)
        country_flag = next(row for row in rows if row["entity_type"] == "country" and row["kind"] == "flag")
        pinar_coat = next(row for row in rows if row["entity_key"] == "aa_pin" and row["kind"] == "coat")
        self.assertEqual(country_flag["wikidata_id"], "Q10")
        self.assertEqual(pinar_coat["wikidata_id"], "Q115119")
        self.assertEqual(pinar_coat["source"], "wikidata_data_wd")

    def test_asset_subdivision_levels_empty_means_all_levels(self):
        self.assertEqual(_asset_subdivision_levels_for_scrape("", []), "")
        self.assertEqual(_asset_subdivision_levels_for_scrape("", [1, 2]), "1,2")
        self.assertEqual(_asset_subdivision_levels_for_scrape("3", [1, 2]), "3")

    def test_country_assets_fall_back_to_toml_commons_urls(self):
        with TemporaryDirectory() as tmpdir:
            seed_dir = Path(tmpdir) / "ciudades_del_mundo" / "subdivisions"
            seed_dir.mkdir(parents=True)
            (seed_dir / "spain.toml").write_text(
                '\nname = "España"\ncountry_code = "spain"\nwikidata_id = "Q29"\n[[pages]]\nsource = "infosection"\npath = [""]\nlowest_level = 0\n[visual_assets.flag]\ncommons_filename = "Flag of Spain.svg"\n[visual_assets.coat]\ncommons_filename = "Coat of Arms of Spain.svg"\n',
                encoding="utf-8",
            )
            with override_settings(BASE_DIR=Path(tmpdir)):
                assets = get_visual_assets_for_entity("country", "spain")

        self.assertIn("Flag_of_Spain.svg", assets["flag"]["image_url"])
        self.assertIn("Coat_of_Arms_of_Spain.svg", assets["coat"]["image_url"])
        self.assertEqual(assets["flag"]["source"], "config")

    def test_seed_visual_assets_from_scraped_page_uses_infosection_entity_id(self):
        entities = [
            ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa"),
            ScrapedAdminArea(code="10", name="Region", level=1, country_code="aa"),
        ]
        html = """
        <div id="ir10" class="infosection">
          <p class="infoname">
            <img class="coatofarms" src="/images/flags/region.svg" alt="flag">Region
          </p>
        </div>
        """

        result = seed_visual_assets_from_scraped_page(
            country_code="aa",
            page_url="https://www.citypopulation.de/en/aa/admin/",
            html=html,
            entities=entities,
            download_missing=False,
            subdivision_levels=(1,),
        )

        self.assertEqual(result.found, 1)
        self.assertEqual(
            self._asset_rows(),
            [
                {
                    "entity_type": "admin_area",
                    "entity_key": "aa_10",
                    "country_code": "aa",
                    "kind": "flag",
                    "remote_url": "https://www.citypopulation.de/images/flags/region.svg",
                    "source_url": "https://www.citypopulation.de/en/aa/admin/",
                    "source": "citypopulation",
                    "status": "found",
                    "wikidata_id": "",
                }
            ],
        )

    def test_seed_visual_assets_from_scraped_page_maps_single_infosection_to_root_country(self):
        entities = [ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa")]
        html = """
        <div id="ir123" class="infosection">
          <p class="infoname">
            <img class="coatofarms" src="/images/flags/aa.svg" alt="flag">AA Country
          </p>
        </div>
        """

        result = seed_visual_assets_from_scraped_page(
            country_code="aa",
            page_url="https://www.citypopulation.de/en/aa/",
            html=html,
            entities=entities,
            download_missing=False,
            subdivision_levels=(),
        )

        self.assertEqual(result.found, 1)
        self.assertEqual(self._asset_rows()[0]["entity_type"], "country")
        self.assertEqual(self._asset_rows()[0]["entity_key"], "aa")

    def test_seed_visual_assets_ignores_unlabelled_citypopulation_language_flags(self):
        entities = [
            ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa"),
            ScrapedAdminArea(code="10", name="Region", level=1, country_code="aa"),
        ]
        html = """
        <div id="ir10" class="infosection">
          <p class="infoname">
            <img src="/images/flags/canada_2_3.svg">Region
          </p>
        </div>
        """

        result = seed_visual_assets_from_scraped_page(
            country_code="aa",
            page_url="https://www.citypopulation.de/en/aa/admin/",
            html=html,
            entities=entities,
            download_missing=False,
            subdivision_levels=(1,),
        )

        self.assertEqual(result.found, 0)
        self.assertEqual(self._asset_rows(), [])

    def test_seed_visual_assets_prefers_country_search_qid_for_root(self):
        entities = [ScrapedAdminArea(code="aa", name="Spain", level=0, country_code="aa")]
        html = '<table><tr><td id="iaa" data-wd="Q999999"><span itemprop="name">Spain</span></td></tr></table>'

        with (
            patch("ciudades_del_mundo.services.visual_assets._find_wikidata_id", return_value="Q29"),
            patch("ciudades_del_mundo.services.visual_assets._wikidata_country_visual_asset_records", return_value=[]),
            patch("ciudades_del_mundo.services.visual_assets._fetch_json", side_effect=_fake_wikidata_fetch_json),
            patch("ciudades_del_mundo.services.visual_assets._commons_file_metadata", return_value={}),
        ):
            result = seed_visual_assets_from_scraped_page(
                country_code="aa",
                page_url="https://www.citypopulation.de/en/aa/",
                html=html,
                entities=entities,
                download_missing=False,
                subdivision_levels=(),
                fill_missing_with_wikidata=True,
            )

        rows = self._asset_rows()
        flag = next(row for row in rows if row["kind"] == "flag")
        coat = next(row for row in rows if row["kind"] == "coat")
        self.assertEqual(result.found, 2)
        self.assertEqual(flag["entity_type"], "country")
        self.assertEqual(flag["wikidata_id"], "Q29")
        self.assertEqual(coat["wikidata_id"], "Q29")
        self.assertNotIn("Q999999", {row["wikidata_id"] for row in rows})

    def test_seed_visual_assets_uses_country_sparql_index_for_subdivision_assets(self):
        entities = [
            ScrapedAdminArea(code="spain", name="Spain", level=0, country_code="spain"),
            ScrapedAdminArea(
                code="md",
                name="Madrid",
                level=1,
                country_code="spain",
                parent_code="spain",
            ),
        ]
        html = """
        <table id="tl"><tbody>
          <tr><td id="imd" class="rname" data-wd="Q2807"><span itemprop="name">Madrid</span></td></tr>
        </tbody></table>
        """

        with (
            patch(
                "ciudades_del_mundo.services.visual_assets._fetch_json",
                side_effect=_fake_wikidata_fetch_json,
            ),
            patch("ciudades_del_mundo.services.visual_assets._commons_file_metadata", return_value={}),
        ):
            result = seed_visual_assets_from_scraped_page(
                country_code="spain",
                page_url="https://www.citypopulation.de/en/spain/admin/",
                html=html,
                entities=entities,
                download_missing=False,
                subdivision_levels=(1,),
                fill_missing_with_wikidata=True,
            )

        rows = self._asset_rows()
        madrid_flag = next(row for row in rows if row["entity_key"] == "spain_md" and row["kind"] == "flag")
        madrid_coat = next(row for row in rows if row["entity_key"] == "spain_md" and row["kind"] == "coat")
        self.assertEqual(result.found, 4)
        self.assertEqual(madrid_flag["wikidata_id"], "Q2807")
        self.assertEqual(madrid_flag["source"], "wikidata")
        self.assertIn("Flag_of_Madrid.svg", madrid_flag["remote_url"])
        self.assertEqual(madrid_coat["wikidata_id"], "Q2807")
        self.assertIn("Coat_of_arms_of_Madrid.svg", madrid_coat["remote_url"])

    def test_seed_visual_assets_uses_page_wikidata_batch_for_all_levels_by_default(self):
        entities = [
            ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa"),
            ScrapedAdminArea(code="loc", name="Locality", level=3, country_code="aa"),
        ]
        html = """
        <table id="tl"><tbody>
          <tr><td id="iloc" class="rname" data-wd="Q12345"><span itemprop="name">Locality</span></td></tr>
        </tbody></table>
        """

        def fake_fetch_json(url: str, *, timeout: int = 10):
            if "wbgetentities" in url and "Q12345" in url:
                return {
                    "entities": {
                        "Q12345": {
                            "claims": {
                                "P41": [{"mainsnak": {"datavalue": {"value": "Flag of Locality.svg"}}}],
                                "P94": [{"mainsnak": {"datavalue": {"value": "Coat of Locality.svg"}}}],
                            },
                            "labels": {"en": {"value": "Locality"}},
                            "descriptions": {"en": {"value": "locality"}},
                        }
                    }
                }
            return {}

        with (
            patch("ciudades_del_mundo.services.visual_assets._find_wikidata_id", return_value="") as find_qid,
            patch("ciudades_del_mundo.services.visual_assets._fetch_json", side_effect=fake_fetch_json),
            patch("ciudades_del_mundo.services.visual_assets._commons_file_metadata", return_value={}),
        ):
            result = seed_visual_assets_from_scraped_page(
                country_code="aa",
                page_url="https://www.citypopulation.de/en/aa/localities/example/",
                html=html,
                entities=entities,
                download_missing=False,
                subdivision_levels=None,
                fill_missing_with_wikidata=True,
            )

        rows = self._asset_rows()
        locality_rows = [row for row in rows if row["entity_key"] == "aa_loc"]
        self.assertEqual(result.found, 2)
        self.assertEqual({row["kind"] for row in locality_rows}, {"flag", "coat"})
        self.assertEqual({row["wikidata_id"] for row in locality_rows}, {"Q12345"})
        self.assertEqual(find_qid.call_count, 1)


    def test_seed_visual_assets_uses_scraped_data_wd_when_page_html_no_longer_has_qid(self):
        entities = [
            ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa"),
            ScrapedAdminArea(code="pin", name="Pinar del Río", level=1, country_code="aa", data_wd="Q115119"),
        ]
        html = "<table id='tl'><tbody><tr><td id='ipin' class='rname'>Pinar del Río</td></tr></tbody></table>"

        def fake_fetch_json(url: str, *, timeout: int = 10):
            if "wbgetentities" in url and "Q115119" in url:
                return {
                    "entities": {
                        "Q115119": {
                            "claims": {
                                "P94": [{"mainsnak": {"datavalue": {"value": "Coat of Pinar del Rio.svg"}}}],
                            },
                            "labels": {"en": {"value": "Pinar del Río"}},
                        }
                    }
                }
            return {}

        with (
            patch("ciudades_del_mundo.services.visual_assets._find_wikidata_id", return_value=""),
            patch("ciudades_del_mundo.services.visual_assets._fetch_json", side_effect=fake_fetch_json),
            patch("ciudades_del_mundo.services.visual_assets._commons_file_metadata", return_value={}),
        ):
            result = seed_visual_assets_from_scraped_page(
                country_code="aa",
                page_url="https://www.citypopulation.de/en/aa/admin/",
                html=html,
                entities=entities,
                download_missing=False,
                subdivision_levels=(1,),
                fill_missing_with_wikidata=True,
            )

        rows = self._asset_rows()
        coat = next(row for row in rows if row["entity_key"] == "aa_pin" and row["kind"] == "coat")
        self.assertEqual(result.found, 1)
        self.assertEqual(coat["wikidata_id"], "Q115119")
        self.assertIn("Coat_of_Pinar_del_Rio.svg", coat["remote_url"])

    def test_seed_visual_assets_skips_individual_entity_search_by_default(self):
        entities = [
            ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa"),
            ScrapedAdminArea(code="tiny", name="Tiny Place", level=3, country_code="aa"),
        ]
        html = "<table><tr><td id='itiny'><span itemprop='name'>Tiny Place</span></td></tr></table>"
        logs = []

        with (
            patch("ciudades_del_mundo.services.visual_assets._find_wikidata_id", return_value="QROOT") as find_qid,
            patch("ciudades_del_mundo.services.visual_assets._wikidata_candidates", return_value=({}, {})),
            patch("ciudades_del_mundo.services.visual_assets._wikidata_country_visual_asset_records", return_value=[]),
        ):
            result = seed_visual_assets_from_scraped_page(
                country_code="aa",
                page_url="https://www.citypopulation.de/en/aa/localities/example/",
                html=html,
                entities=entities,
                download_missing=False,
                subdivision_levels=None,
                fill_missing_with_wikidata=True,
                logger=logs.append,
            )

        self.assertEqual(result.found, 0)
        self.assertEqual(find_qid.call_count, 1)
        self.assertTrue(any("búsqueda individual omitida" in message for message in logs))

    def test_visual_asset_filenames_preserve_unicode_characters(self):
        self.assertEqual(
            _safe_filename("Escudo de España (variant).svg"),
            "Escudo_de_España_(variant).svg",
        )
        self.assertEqual(_safe_filename("Flag of Andalucía.svg"), "Flag_of_Andalucía.svg")
        self.assertEqual(_safe_filename("Герб України.svg"), "Герб_України.svg")

    def test_commons_filenames_decode_and_encode_unicode_characters(self):
        filename = _commons_filename_from_url(
            "https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_Andaluc%C3%ADa.svg"
        )

        self.assertEqual(filename, "Flag of Andalucía.svg")
        self.assertIn("Flag_of_Andaluc%C3%ADa.svg", commons_file_url(filename, width=400))
        self.assertEqual(
            _media_url("visual_assets/coat/spain/Escudo_de_España_(variant).svg"),
            "/media/visual_assets/coat/spain/Escudo_de_Espa%C3%B1a_(variant).svg",
        )

    def test_kind_detection_is_accent_insensitive(self):
        self.assertEqual(_kind_from_text("Escudó oficial"), "coat")

    def test_download_filename_uses_country_entity_and_kind(self):
        self.assertEqual(
            _asset_download_filename("flag", ("spain",), ".svg", acquired_date="20260604"),
            "spain_spain_flag.svg",
        )
        self.assertEqual(
            _asset_download_filename("coat", ("spain",), ".png", acquired_date="20260604"),
            "spain_spain_coat.png",
        )
        self.assertEqual(
            _asset_download_filename("seal", ("spain", "spain_cat"), ".svg"),
            "spain_spain_cat_seal.svg",
        )

    def test_svg_download_uses_original_commons_file_url(self):
        url = _asset_download_url(
            "https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_Spain.svg?width=1600",
            "Flag of Spain.svg",
            ".svg",
        )

        self.assertEqual(url, commons_file_url("Flag of Spain.svg", width=None))
        self.assertNotIn("width=", url)

    def test_svg_download_falls_back_to_thumbnail_png_after_429(self):
        calls = []

        def fake_read(url: str, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise HTTPError(url, 429, "Too Many Requests", hdrs=None, fp=None)
            return b"\x89PNG\r\n\x1a\n" + (b"0" * 20)

        with TemporaryDirectory() as tmpdir, self.settings(MEDIA_ROOT=tmpdir):
            with (
                patch("ciudades_del_mundo.services.visual_assets._read_url_bytes", side_effect=fake_read),
                patch("ciudades_del_mundo.services.visual_assets._set_asset_local_path") as set_local_path,
            ):
                downloaded = _download_asset(
                    123,
                    "flag",
                    ("spain",),
                    commons_file_url("Flag of Spain.svg", width=500),
                    "Flag of Spain.svg",
                )

            self.assertTrue(downloaded)
            self.assertEqual(calls[0], commons_file_url("Flag of Spain.svg", width=None))
            self.assertIn("width=500", calls[1])
            set_local_path.assert_called_once_with(123, "visual_assets/flag/spain/spain_spain_flag.png", True)
            self.assertTrue((Path(tmpdir) / "visual_assets" / "flag" / "spain" / "spain_spain_flag.png").is_file())

    def test_local_svg_asset_rejects_png_bytes(self):
        with TemporaryDirectory() as tmpdir, self.settings(MEDIA_ROOT=tmpdir):
            relative_path = "visual_assets/flag/spain/spain_spain_flag.svg"
            path = Path(tmpdir) / relative_path
            path.parent.mkdir(parents=True)
            path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

            self.assertFalse(_local_asset_file_is_usable(relative_path))

            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
            self.assertTrue(_local_asset_file_is_usable(relative_path))

    def test_existing_generated_country_svg_is_preferred_and_tolerates_preamble(self):
        with TemporaryDirectory() as tmpdir, self.settings(MEDIA_ROOT=tmpdir):
            folder = Path(tmpdir) / "visual_assets" / "flag" / "spain"
            folder.mkdir(parents=True)
            (folder / "aaa_old_flag.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
            (folder / "spain_spain_flag.svg").write_text(
                """<!-- generated by commons -->
<!DOCTYPE svg>
<svg xmlns="http://www.w3.org/2000/svg"></svg>""",
                encoding="utf-8",
            )

            self.assertTrue(_local_asset_file_is_usable("visual_assets/flag/spain/spain_spain_flag.svg"))
            self.assertEqual(
                _first_local_asset_path("flag", "country", "spain", country_code="spain"),
                "visual_assets/flag/spain/spain_spain_flag.svg",
            )

    def test_wikidata_candidates_skip_empty_claims(self):
        payload = {
            "entities": {
                "Q4040": {
                    "claims": {
                        "P41": [
                            {"mainsnak": {"snaktype": "novalue"}},
                            {
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {
                                        "type": "string",
                                        "value": "Flag of Aragon.svg",
                                    },
                                }
                            },
                        ],
                        "P94": [
                            {
                                "rank": "preferred",
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {
                                        "type": "string",
                                        "value": "https://commons.wikimedia.org/wiki/Special:FilePath/Escudo_de_Aragon.svg",
                                    },
                                },
                            }
                        ],
                    },
                    "labels": {"en": {"value": "Aragon"}},
                    "descriptions": {"en": {"value": "autonomous community of Spain"}},
                }
            }
        }

        with (
            patch("ciudades_del_mundo.services.visual_assets._fetch_json", return_value=payload),
            patch("ciudades_del_mundo.services.visual_assets._commons_file_metadata", return_value={}),
        ):
            candidates, descriptions = _wikidata_candidates(
                "Q4040",
                kinds=["flag", "coat", "seal"],
                languages=("en", "es"),
            )

        self.assertEqual(candidates["flag"].commons_filename, "Flag of Aragon.svg")
        self.assertEqual(candidates["coat"].commons_filename, "Escudo de Aragon.svg")
        self.assertNotIn("seal", candidates)
        self.assertEqual(descriptions["en"]["title"], "Aragon")


def _fake_wikidata_fetch_json(url: str, *, timeout: int = 10):
    if "wbsearchentities" in url:
        return {"search": [{"id": "Q29"}]}
    if "query.wikidata.org/sparql" in url:
        return {
            "results": {
                "bindings": [
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q2807"},
                        "itemLabel": {"value": "Madrid"},
                        "type": {"value": "http://www.wikidata.org/entity/Q2074737"},
                        "typeLabel": {"value": "municipality of Spain"},
                        "parent": {"value": "http://www.wikidata.org/entity/Q5756"},
                        "parentLabel": {"value": "Community of Madrid"},
                        "flag": {
                            "value": "http://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_Madrid.svg"
                        },
                        "coat": {
                            "value": "http://commons.wikimedia.org/wiki/Special:FilePath/Coat_of_arms_of_Madrid.svg"
                        },
                    }
                ]
            }
        }
    if "wbgetentities" in url:
        return {
            "entities": {
                "Q29": {
                    "claims": {
                        "P41": [
                            {
                                "mainsnak": {
                                    "datavalue": {
                                        "value": "Flag of Spain.svg",
                                    }
                                }
                            }
                        ],
                        "P94": [
                            {
                                "mainsnak": {
                                    "datavalue": {
                                        "value": "Coat of arms of Spain.svg",
                                    }
                                }
                            }
                        ],
                    },
                    "labels": {"en": {"value": "Spain"}},
                    "descriptions": {"en": {"value": "country in southwestern Europe"}},
                }
            }
        }
    return {}
