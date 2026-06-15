from django.test import TestCase
from django.utils import timezone

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.models import AdminArea, VisualAsset, VisualAssetTranslation


class DjangoAdminAreaRepositoryTests(TestCase):
    def test_save_many_batches_existing_lookup_and_updates_only_changed_rows(self):
        country_code = "batchland"
        now = timezone.now()
        existing = [
            AdminArea(
                id=f"{country_code}_{index:04d}",
                country_code=country_code,
                code=f"{index:04d}",
                name="Same",
                level=1,
                created_at=now,
                updated_at=now,
            )
            for index in range(1100)
        ]
        AdminArea.objects.bulk_create(existing, batch_size=100)
        unchanged_before = AdminArea.objects.get(id=f"{country_code}_0001").updated_at
        changed_before = AdminArea.objects.get(id=f"{country_code}_0002").updated_at

        entities = [
            ScrapedAdminArea(
                code=f"{index:04d}",
                name="Changed" if index == 2 else "Same",
                level=1,
                country_code=country_code,
            )
            for index in range(1100)
        ]
        entities.append(
            ScrapedAdminArea(
                code="1100",
                name="New",
                level=1,
                country_code=country_code,
            )
        )

        created, updated = DjangoAdminAreaRepository().save_many(country_code, entities)

        self.assertEqual(created, 1)
        self.assertEqual(updated, 1)
        self.assertEqual(AdminArea.objects.filter(country_code=country_code).count(), 1101)
        self.assertEqual(AdminArea.objects.get(id=f"{country_code}_0001").updated_at, unchanged_before)
        changed_after = AdminArea.objects.get(id=f"{country_code}_0002")
        self.assertEqual(changed_after.name, "Changed")
        self.assertNotEqual(changed_after.updated_at, changed_before)

    def test_delete_missing_removes_visual_assets_for_deleted_admin_area_ids(self):
        country_code = "assetland"
        now = timezone.now()
        keep = AdminArea.objects.create(
            id=f"{country_code}_keep",
            country_code=country_code,
            code="keep",
            name="Keep",
            level=1,
        )
        stale = AdminArea.objects.create(
            id=f"{country_code}_stale",
            country_code=country_code,
            code="stale",
            name="Stale",
            level=1,
        )
        stale_asset = VisualAsset.objects.create(
            entity_type="admin_area",
            entity_key=stale.id,
            entity_name=stale.name,
            country_code=country_code,
            kind="coat",
            status="found",
            created_at=now,
            updated_at=now,
        )
        keep_asset = VisualAsset.objects.create(
            entity_type="admin_area",
            entity_key=keep.id,
            entity_name=keep.name,
            country_code=country_code,
            kind="coat",
            status="found",
            created_at=now,
            updated_at=now,
        )
        VisualAssetTranslation.objects.create(
            asset=stale_asset,
            language="es",
            title="Stale coat",
            created_at=now,
            updated_at=now,
        )

        deleted = DjangoAdminAreaRepository().delete_missing(country_code, {keep.id})

        self.assertEqual(deleted, 1)
        self.assertFalse(AdminArea.objects.filter(id=stale.id).exists())
        self.assertFalse(VisualAsset.objects.filter(id=stale_asset.id).exists())
        self.assertFalse(VisualAssetTranslation.objects.filter(asset_id=stale_asset.id).exists())
        self.assertTrue(VisualAsset.objects.filter(id=keep_asset.id).exists())
