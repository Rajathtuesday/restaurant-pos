"""
Tests for every schema-review fix applied to the orders app.

Coverage:
  - Order composite indexes replace four single-column indexes
  - OrderItem has (order), (order, status), (kot) indexes
  - KOTBatch has (tenant, outlet, status) index
  - Payment has (paid_at) and (order, method) indexes
  - DailyTokenCounter / DailyOnlineTokenCounter have no redundant index
  - OrderEvent has (order, event_type) composite index
  - gst_breakdown_cache populated by recalculate_totals, property reads from it
  - gst_breakdown property returns Decimal values (not strings)
  - Order.save() still generates order_number correctly without @transaction.atomic

(Promo.validate_and_use() coverage moved to promos/tests.py along with the
model itself, Phase 0 of the orders app split.)
"""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.core.exceptions import ValidationError

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import (
    DailyOnlineTokenCounter,
    DailyTokenCounter,
    KOTBatch,
    Order,
    OrderEvent,
    OrderItem,
    Payment,
)
from tenants.models import Tenant, Outlet


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _tenant(name="TestRest"):
    t = Tenant.objects.create(name=name)
    o = Outlet.objects.create(tenant=t, name=f"{name} HQ")
    return t, o


def _user(tenant, outlet, role="cashier"):
    return User.objects.create_user(
        username=f"u_{tenant.id}_{role}",
        password="pass",
        tenant=tenant, outlet=outlet, role=role,
    )


def _menu_item(tenant, outlet, price=100, gst=5, name="Dish"):
    cat, _ = MenuCategory.objects.get_or_create(
        tenant=tenant, outlet=outlet, name="Mains"
    )
    return MenuItem.objects.create(
        tenant=tenant, outlet=outlet, category=cat,
        name=name, price=Decimal(str(price)),
        gst_percentage=Decimal(str(gst)), is_available=True,
    )


def _order(tenant, outlet):
    return Order.objects.create(tenant=tenant, outlet=outlet)


def _order_with_item(tenant, outlet, price=100, gst=5):
    mi = _menu_item(tenant, outlet, price=price, gst=gst)
    order = _order(tenant, outlet)
    OrderItem.objects.create(
        order=order, menu_item=mi,
        quantity=1, price=Decimal(str(price)),
        gst_percentage=Decimal(str(gst)),
        total_price=Decimal(str(price)),
        status="pending",
    )
    order.recalculate_totals()
    order.refresh_from_db()
    return order


def _index_names(model):
    return [idx.name for idx in model._meta.indexes]


def _field_names_in_indexes(model):
    return [tuple(idx.fields) for idx in model._meta.indexes]


# ---------------------------------------------------------------------------
# Index structure tests
# ---------------------------------------------------------------------------

class TestOrderIndexes(TestCase):

    def test_composite_tenant_outlet_status_present(self):
        names = _index_names(Order)
        self.assertIn("order_tenant_outlet_status", names)

    def test_composite_tenant_outlet_date_present(self):
        names = _index_names(Order)
        self.assertIn("order_tenant_outlet_date", names)

    def test_table_index_present(self):
        names = _index_names(Order)
        self.assertIn("order_table", names)

    def test_no_single_column_tenant_index(self):
        field_sets = _field_names_in_indexes(Order)
        self.assertNotIn(("tenant",), field_sets)

    def test_no_single_column_outlet_index(self):
        field_sets = _field_names_in_indexes(Order)
        self.assertNotIn(("outlet",), field_sets)

    def test_no_single_column_status_index(self):
        field_sets = _field_names_in_indexes(Order)
        self.assertNotIn(("status",), field_sets)

    def test_exactly_three_indexes(self):
        self.assertEqual(len(Order._meta.indexes), 3)


class TestOrderItemIndexes(TestCase):

    def test_order_index_present(self):
        self.assertIn("orderitem_order", _index_names(OrderItem))

    def test_order_status_composite_present(self):
        self.assertIn("orderitem_order_status", _index_names(OrderItem))

    def test_kot_index_present(self):
        self.assertIn("orderitem_kot", _index_names(OrderItem))

    def test_no_standalone_status_index(self):
        field_sets = _field_names_in_indexes(OrderItem)
        self.assertNotIn(("status",), field_sets)


class TestKOTBatchIndexes(TestCase):

    def test_tenant_outlet_status_composite_present(self):
        self.assertIn("kotbatch_tenant_outlet_status", _index_names(KOTBatch))

    def test_tenant_outlet_base_index_present(self):
        field_sets = _field_names_in_indexes(KOTBatch)
        self.assertIn(("tenant", "outlet"), field_sets)


class TestPaymentIndexes(TestCase):

    def test_paid_at_index_present(self):
        self.assertIn("payment_paid_at", _index_names(Payment))

    def test_order_method_composite_present(self):
        self.assertIn("payment_order_method", _index_names(Payment))

    def test_order_index_still_present(self):
        field_sets = _field_names_in_indexes(Payment)
        self.assertIn(("order",), field_sets)


class TestOrderEventIndexes(TestCase):

    def test_order_event_type_composite_present(self):
        self.assertIn("orderevent_order_type", _index_names(OrderEvent))

    def test_tenant_type_composite_present(self):
        self.assertIn("orderevent_tenant_type_idx", _index_names(OrderEvent))


class TestDailyCounterNoRedundantIndex(TestCase):

    def test_daily_token_counter_no_duplicate_index(self):
        field_sets = _field_names_in_indexes(DailyTokenCounter)
        self.assertNotIn(("outlet", "date"), field_sets,
                         "unique_together already creates this index — explicit one is redundant")

    def test_daily_online_token_counter_no_duplicate_index(self):
        field_sets = _field_names_in_indexes(DailyOnlineTokenCounter)
        self.assertNotIn(("outlet", "date"), field_sets,
                         "unique_together already creates this index — explicit one is redundant")


# ---------------------------------------------------------------------------
# gst_breakdown cache tests
# ---------------------------------------------------------------------------

class TestGSTBreakdownCache(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _tenant("GSTCafe")

    def test_cache_populated_after_recalculate(self):
        order = _order_with_item(self.tenant, self.outlet, price=100, gst=5)
        self.assertTrue(len(order.gst_breakdown_cache) > 0)

    def test_cache_is_list_of_dicts(self):
        order = _order_with_item(self.tenant, self.outlet, price=100, gst=5)
        self.assertIsInstance(order.gst_breakdown_cache, list)
        self.assertIsInstance(order.gst_breakdown_cache[0], dict)

    def test_cache_stores_strings_not_decimals(self):
        order = _order_with_item(self.tenant, self.outlet, price=100, gst=5)
        row = order.gst_breakdown_cache[0]
        for key in ("rate", "cgst_rate", "sgst_rate", "cgst_amount", "sgst_amount"):
            self.assertIsInstance(row[key], str, f"Key '{key}' should be str in JSON cache")

    def test_property_returns_decimals(self):
        order = _order_with_item(self.tenant, self.outlet, price=100, gst=5)
        breakdown = order.gst_breakdown
        self.assertTrue(len(breakdown) > 0)
        row = breakdown[0]
        for key in ("rate", "cgst_rate", "sgst_rate", "cgst_amount", "sgst_amount"):
            self.assertIsInstance(row[key], Decimal, f"Key '{key}' should be Decimal in property output")

    def test_gst_amounts_correct_for_5pct(self):
        # 100 at 5% exclusive → GST = 5.00, CGST = 2.50, SGST = 2.50
        order = _order_with_item(self.tenant, self.outlet, price=100, gst=5)
        breakdown = order.gst_breakdown
        self.assertEqual(len(breakdown), 1)
        row = breakdown[0]
        self.assertEqual(row["rate"], Decimal("5"))
        self.assertEqual(row["cgst_amount"], Decimal("2.50"))
        self.assertEqual(row["sgst_amount"], Decimal("2.50"))

    def test_cache_updated_on_second_recalculate(self):
        mi = _menu_item(self.tenant, self.outlet, price=100, gst=5)
        order = _order(self.tenant, self.outlet)
        oi = OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=Decimal("100"), gst_percentage=Decimal("5"),
            total_price=Decimal("100"), status="pending",
        )
        order.recalculate_totals()
        order.refresh_from_db()
        first_cache = list(order.gst_breakdown_cache)

        # Add another item and recalculate again
        mi2 = _menu_item(self.tenant, self.outlet, price=200, gst=18, name="Beer")
        OrderItem.objects.create(
            order=order, menu_item=mi2, quantity=1,
            price=Decimal("200"), gst_percentage=Decimal("18"),
            total_price=Decimal("200"), status="pending",
        )
        order.recalculate_totals()
        order.refresh_from_db()

        self.assertNotEqual(order.gst_breakdown_cache, first_cache,
                            "Cache should be updated after second recalculate")
        self.assertEqual(len(order.gst_breakdown), 2)

    def test_zero_gst_produces_empty_breakdown(self):
        order = _order_with_item(self.tenant, self.outlet, price=100, gst=0)
        self.assertEqual(order.gst_breakdown, [])

    def test_property_accessible_multiple_times_without_extra_queries(self):
        order = _order_with_item(self.tenant, self.outlet, price=100, gst=5)
        # Both accesses should read from the cached JSON, not the DB.
        with self.assertNumQueries(0):
            _ = order.gst_breakdown
            _ = order.gst_breakdown


# ---------------------------------------------------------------------------
# Order.save() — no longer @transaction.atomic at class level
# ---------------------------------------------------------------------------

class TestOrderSaveNoTopLevelAtomic(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _tenant("SaveTest")

    def test_order_number_generated_on_create(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        order.refresh_from_db()
        self.assertIsNotNone(order.order_number)
        self.assertTrue(order.order_number.startswith("INV-"))

    def test_order_number_format_includes_outlet_id(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        order.refresh_from_db()
        self.assertIn(str(self.outlet.id), order.order_number)

    def test_update_save_does_not_change_order_number(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        order.refresh_from_db()
        original = order.order_number
        order.status = "billing"
        order.save(update_fields=["status"])
        order.refresh_from_db()
        self.assertEqual(order.order_number, original)

    def test_sequential_order_numbers_same_outlet(self):
        o1 = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        o2 = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        o1.refresh_from_db()
        o2.refresh_from_db()
        # Last 4 digits differ by 1
        num1 = int(o1.order_number.split("-")[-1])
        num2 = int(o2.order_number.split("-")[-1])
        self.assertEqual(num2, num1 + 1)

    def test_recalculate_totals_does_not_create_duplicate_order_number(self):
        order = _order_with_item(self.tenant, self.outlet)
        order.refresh_from_db()
        original = order.order_number
        # recalculate_totals calls save() internally — should not regenerate number
        order.recalculate_totals()
        order.refresh_from_db()
        self.assertEqual(order.order_number, original)

# Promo.validate_and_use() tests moved to promos/tests.py (Phase 0 of the
# orders app split).
