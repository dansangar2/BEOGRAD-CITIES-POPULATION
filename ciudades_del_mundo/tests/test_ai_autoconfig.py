from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from django.test import TestCase

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.models import ScrapingConfig
from ciudades_del_mundo.services.ai_autoconfig_context import (
    AUTOCONFIG_SPEC_DIR,
    build_ai_autoconfig_context,
    discover_recent_autoconfig_slugs,
)
from ciudades_del_mundo.services.scraped_page_logs import ScrapedPageLogWriter


class AiAutoconfigTests(TestCase):
    def test_scraped_page_log_writer_persists_html_entities_and_index(self):
        with TemporaryDirectory() as tmp:
            writer = ScrapedPageLogWriter(slug="testland", run_id="task1", root=Path(tmp))
            page = SimpleNamespace(
                index=2,
                block_index=1,
                path_index=0,
                path="testland/admin",
                url="https://www.citypopulation.de/en/testland/admin/",
                html_format="admin",
                lowest_level=2,
                found=1,
                html="<html><body>ok</body></html>",
                entities=(
                    ScrapedAdminArea(
                        code="TL-1",
                        name="North",
                        level=1,
                        country_code="TL",
                        parent_code="TL",
                    ),
                ),
            )

            html_path = writer.write_page(page)

            self.assertTrue(html_path.exists())
            self.assertIn("ok", html_path.read_text(encoding="utf-8"))
            index_text = writer.index_path.read_text(encoding="utf-8")
            self.assertIn('"path": "testland/admin"', index_text)
            self.assertIn('"found": 1', index_text)
            entity_files = list((writer.run_path / "entities").glob("*.json"))
            self.assertEqual(len(entity_files), 1)
            self.assertIn('"name": "North"', entity_files[0].read_text(encoding="utf-8"))

    def test_build_ai_autoconfig_context_collects_current_sources(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_dir = root / AUTOCONFIG_SPEC_DIR
            spec_dir.mkdir(parents=True)
            (spec_dir / "testland.txt").write_text("Region > Province > City\n", encoding="utf-8")

            ScrapingConfig.objects.create(
                slug="testland",
                country_code="TL",
                name="Testland",
                content='scrape_schema_version = 2\ncountry_code = "TL"\n[[pages]]\nsource = "admin"\npath = ["admin"]\n',
                content_hash="hash",
            )

            error_dir = root / ".web_scrape_block_errors" / "testland"
            error_dir.mkdir(parents=True)
            (error_dir / "testland_link_20260101_000000.txt").write_text("SCR-LINK-001\n", encoding="utf-8")
            task_dir = root / ".web_task_logs" / "testland"
            task_dir.mkdir(parents=True)
            (task_dir / "abc.log").write_text("SCRAPE admin L2: url\n", encoding="utf-8")
            page_dir = root / ".web_scrape_pages" / "testland" / "task1"
            page_dir.mkdir(parents=True)
            (page_dir / "index.jsonl").write_text('{"url": "url", "found": 1}\n', encoding="utf-8")

            result = build_ai_autoconfig_context("testland", base_dir=root)

            self.assertTrue(result.path.exists())
            text = result.path.read_text(encoding="utf-8")
            self.assertIn("Region > Province > City", text)
            self.assertIn('source = "admin"', text)
            self.assertIn("SCR-LINK-001", text)
            self.assertIn("SCRAPE admin", text)
            self.assertIn('"found": 1', text)
            self.assertEqual(result.warnings, ())

    def test_discover_recent_autoconfig_slugs_uses_diagnostic_dirs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dir = root / ".web_task_logs" / "oldland"
            old_dir.mkdir(parents=True)
            (old_dir / "old.log").write_text("old", encoding="utf-8")
            new_dir = root / ".web_scrape_block_errors" / "newland"
            new_dir.mkdir(parents=True)
            (new_dir / "new.txt").write_text("new", encoding="utf-8")

            slugs = discover_recent_autoconfig_slugs(base_dir=root, limit=2)

            self.assertEqual(set(slugs), {"oldland", "newland"})
