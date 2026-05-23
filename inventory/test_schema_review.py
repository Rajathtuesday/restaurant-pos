"""
Tests for schema-review fixes applied to the inventory app.

Coverage:
  - Recipe.clean() raises ValidationError for cross-tenant item links
  - BatchItem unique_together allows same barcode across tenants but blocks within one
  - InventoryTransaction has (item, created_at) and (tenant, outlet, created_at) indexes
  - PurchaseOrder.po_number format includes outlet ID
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from inventory.models import (
    BatchItem,
    InventoryItem,
    InventoryTransaction,
    ProductionBatch,
    PurchaseOrder,
    Recipe,
    Supplier,
    TenantPOCounter,
)
from menu.models import MenuCategory, MenuItem
from tenants.models import Outlet, Tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tenant(name):
    t = Tenant.objects.create(name=name)
    o = Outlet.objects.create(tenant=t, name=f"{name} HQ")
    return t, o


def _inv_item(tenant, outlet, name="Flour"):
    return InventoryItem.objects.create(
        tenant=tenant, outlet=outlet, name=name, unit="kg",
        stock=Decimal("10"), low_stock_threshold=Decimal("1"),
    )


def _menu_item(tenant, outlet, name="Bread"):
    cat, _ = MenuCategory.objects.get_or_create(
        tenant=tenant, outlet=outlet, name="Food"
    )
    return MenuItem.objects.create(
        tenant=tenant, outlet=outlet, category=cat,
        name=name, price=Decimal("50"),
        gst_percentage=Decimal("5"), is_available=True,
    )


# ---------------------------------------------------------------------------
# Recipe.clean() — cross-tenant guard
# ---------------------------------------------------------------------------

class TestRecipeClean(TestCase):

    def setUp(self):
        self.t1, self.o1 = _tenant("Tenant Alpha")
        self.t2, self.o2 = _tenant("Tenant Beta")

    def test_valid_recipe_same_tenant_passes(self):
        mi = _menu_item(self.t1, self.o1)
        inv = _inv_item(self.t1, self.o1)
        recipe = Recipe(menu_item=mi, inventory_item=inv, quantity_required=Decimal("0.5"), unit="kg")
        recipe.full_clean()  # should not raise

    def test_cross_tenant_recipe_raises(self):
        mi = _menu_item(self.t1, self.o1, name="BreadA")
        inv = _inv_item(self.t2, self.o2, name="FlourB")
        recipe = Recipe(menu_item=mi, inventory_item=inv, quantity_required=Decimal("0.5"), unit="kg")
        with self.assertRaises(ValidationError) as ctx:
            recipe.full_clean()
        self.assertIn("tenant", str(ctx.exception).lower())

    def test_cross_tenant_recipe_save_blocked_by_clean(self):
        mi = _menu_item(self.t1, self.o1, name="BreadC")
        inv = _inv_item(self.t2, self.o2, name="FlourD")
        recipe = Recipe(menu_item=mi, inventory_item=inv, quantity_required=Decimal("0.5"), unit="kg")
        with self.assertRaises(ValidationError):
            recipe.full_clean()
            recipe.save()


# ---------------------------------------------------------------------------
# BatchItem — per-tenant barcode uniqueness
# ---------------------------------------------------------------------------

class TestBatchItemBarcode(TestCase):

    def setUp(self):
        self.t1, self.o1 = _tenant("Franchise Alpha")
        self.t2, self.o2 = _tenant("Franchise Beta")
        self.inv1 = _inv_item(self.t1, self.o1)
        self.inv2 = _inv_item(self.t2, self.o2)
        self.batch1 = ProductionBatch.objects.create(
            tenant=self.t1, batch_number="B001", source_outlet=self.o1
        )
        self.batch2 = ProductionBatch.objects.create(
            tenant=self.t2, batch_number="B001", source_outlet=self.o2
        )

    def test_same_barcode_different_tenants_allowed(self):
        """Two franchise outlets can both use barcode '0001' without collision."""
        BatchItem.objects.create(
            batch=self.batch1, tenant=self.t1, inventory_item=self.inv1,
            quantity=Decimal("5"), unit="kg", barcode="0001"
        )
        # Should not raise
        BatchItem.objects.create(
            batch=self.batch2, tenant=self.t2, inventory_item=self.inv2,
            quantity=Decimal("3"), unit="kg", barcode="0001"
        )

    def test_duplicate_barcode_same_tenant_blocked(self):
        """Same barcode within one tenant must be unique."""
        BatchItem.objects.create(
            batch=self.batch1, tenant=self.t1, inventory_item=self.inv1,
            quantity=Decimal("5"), unit="kg", barcode="DUPE"
        )
        with self.assertRaises(IntegrityError):
            BatchItem.objects.create(
                batch=self.batch1, tenant=self.t1, inventory_item=self.inv1,
                quantity=Decimal("2"), unit="kg", barcode="DUPE"
            )

    def test_batch_item_has_tenant_fk(self):
        self.assertIn("tenant_id", [f.attname for f in BatchItem._meta.get_fields()
                                    if hasattr(f, "attname")])

    def test_batch_item_unique_together_is_tenant_barcode(self):
        self.assertIn(
            ("tenant", "barcode"),
            [tuple(ut) for ut in BatchItem._meta.unique_together]
        )


# ---------------------------------------------------------------------------
# InventoryTransaction — date-range indexes
# ---------------------------------------------------------------------------

class TestInventoryTransactionIndexes(TestCase):

    def test_item_date_composite_index_present(self):
        names = [idx.name for idx in InventoryTransaction._meta.indexes]
        self.assertIn("invtxn_item_date", names)

    def test_outlet_date_composite_index_present(self):
        names = [idx.name for idx in InventoryTransaction._meta.indexes]
        self.assertIn("invtxn_outlet_date", names)

    def test_base_indexes_still_present(self):
        field_sets = [tuple(idx.fields) for idx in InventoryTransaction._meta.indexes]
        self.assertIn(("tenant", "outlet"), field_sets)
        self.assertIn(("item",), field_sets)


# ---------------------------------------------------------------------------
# PurchaseOrder po_number includes outlet ID
# ---------------------------------------------------------------------------

class TestPurchaseOrderFormat(TestCase):

    def setUp(self):
        self.t, self.o = _tenant("PO Format Test")
        self.supplier = Supplier.objects.create(
            tenant=self.t, outlet=self.o, name="Test Supplier"
        )
        self.inv = _inv_item(self.t, self.o)
        self.inv.preferred_supplier = self.supplier
        self.inv.reorder_quantity = Decimal("10")
        self.inv.save(update_fields=["preferred_supplier", "reorder_quantity"])

    def test_auto_reorder_po_number_includes_outlet_id(self):
        """trigger_reorder() must embed outlet ID so two outlets never share PO numbers."""
        self.inv.trigger_reorder()
        po = PurchaseOrder.objects.filter(
            tenant=self.t, outlet=self.o, supplier=self.supplier
        ).first()
        self.assertIsNotNone(po)
        self.assertIsNotNone(po.po_number)
        self.assertIn(str(self.o.id), po.po_number,
                      f"PO number '{po.po_number}' must contain outlet ID {self.o.id}")

    def test_po_number_format_components(self):
        self.inv.trigger_reorder()
        po = PurchaseOrder.objects.filter(tenant=self.t, outlet=self.o).first()
        parts = po.po_number.split("-")
        # Expected: PO-<outlet_id>-<year>-<seq>
        self.assertEqual(parts[0], "PO")
        self.assertEqual(parts[1], str(self.o.id))
        self.assertTrue(parts[2].isdigit())  # year
        self.assertTrue(parts[3].isdigit())  # sequence
