from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.test import SimpleTestCase

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.services.scrape_resume import ScrapeResumeStore


class ScrapeResumeStoreAssetPhaseTests(SimpleTestCase):
    def test_tracks_populated_data_and_asset_page_checkpoints(self):
        entity = ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa")
        page = SimpleNamespace(
            index=2,
            path="admin/",
            html_format="admin",
            lowest_level=1,
            url="https://www.citypopulation.de/en/aa/admin/",
            found=1,
            html="<html></html>",
            entities=(entity,),
        )

        with TemporaryDirectory() as tmpdir:
            store = ScrapeResumeStore(
                slug="aa",
                country_code="aa",
                content_hash="hash",
                root=Path(tmpdir),
            )
            store.save_page(page)
            self.assertFalse(store.data_populated())

            store.mark_data_populated()
            cached_pages = store.iter_pages()

            self.assertTrue(store.data_populated())
            self.assertEqual(len(cached_pages), 1)
            self.assertEqual(cached_pages[0].url, page.url)
            self.assertEqual(cached_pages[0].html, page.html)
            self.assertEqual(cached_pages[0].entities[0], entity)
            self.assertFalse(store.asset_page_done(cached_pages[0].key))

            store.mark_asset_page_done(cached_pages[0].key)
            self.assertTrue(store.asset_page_done(cached_pages[0].key))
