"""
Tests for the QR-guest reorder/merge fix in create_order (orders/views/billing_views.py).

Background: a guest ordering from the digital menu always sent source="web"
and never an order_id, so create_order always tried to INSERT a new
status="open" Order for the table — colliding with the DB's
unique_open_order_per_table constraint the moment a second round was placed,
and failing with a generic error. The fix: the frontend now remembers the
order_id from the first placement and sends it back on later rounds, and the
backend merges new items into that same order — but only after verifying the
order actually belongs to the SAME table the guest scanned (a guest is never
authenticated, so this is the one thing standing between "reorder" and an
IDOR letting a guest add items to a stranger's table's bill).

Run: python manage.py test orders.tests.test_qr_reorder
"""
import json
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="QR Tenant", slug="qr-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Butter Naan", price=Decimal("60.00"),
        )
        self.item2 = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Dal Makhani", price=Decimal("180.00"),
        )
        self.table_a = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="A1", is_active=True
        )
        self.table_b = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="B1", is_active=True
        )

    def _place(self, table, item, qty=1, order_id=None):
        payload = {
            "table_token": str(table.qr_token),
            "cart": [{"id": item.id, "quantity": qty}],
            "source": "web",
        }
        if order_id:
            payload["order_id"] = order_id
        return self.client.post(
            reverse("create-order"),
            data=json.dumps(payload),
            content_type="application/json",
        )


class ReorderMergeTest(_Base):
    def test_second_round_merges_into_same_order_with_correct_total(self):
        first = self._place(self.table_a, self.item, qty=1)
        self.assertEqual(first.status_code, 200)
        order_id = first.json()["order_id"]

        second = self._place(self.table_a, self.item2, qty=2, order_id=order_id)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["order_id"], order_id)  # same order, not a new one

        order = Order.objects.get(id=order_id)
        # Both rounds' items are present...
        self.assertEqual(order.items.count(), 2)
        # ...and the subtotal reflects BOTH rounds, not just the latest one.
        # (subtotal is the pre-GST raw sum; grand_total also includes GST, so
        # comparing subtotal here avoids hardcoding the tax rate in the test.)
        expected_subtotal = self.item.price + (self.item2.price * 2)
        self.assertEqual(order.subtotal, expected_subtotal)
        self.assertGreater(order.grand_total, order.subtotal)  # GST was applied

        # Still exactly one open order for the table — no duplicate created.
        self.assertEqual(
            Order.objects.filter(tenant=self.tenant, outlet=self.outlet,
                                  table=self.table_a, status="open").count(),
            1,
        )

    def test_without_stored_order_id_second_call_would_have_collided(self):
        """Sanity check on the OLD bug: two back-to-back guest orders with NO
        order_id for the same table must not silently succeed as two separate
        open orders (the unique constraint should still be doing its job;
        the view's own check-then-create is what the fix routes around)."""
        first = self._place(self.table_a, self.item)
        self.assertEqual(first.status_code, 200)

        second = self._place(self.table_a, self.item2)  # no order_id on purpose
        # Must not silently create a second OPEN order for the same table.
        open_orders = Order.objects.filter(
            tenant=self.tenant, outlet=self.outlet, table=self.table_a, status="open"
        )
        self.assertEqual(open_orders.count(), 1)


class ReorderCrossTableIDORTest(_Base):
    def test_guest_cannot_merge_items_into_a_different_tables_order(self):
        # Table B already has its own open order.
        table_b_order = self._place(self.table_b, self.item)
        self.assertEqual(table_b_order.status_code, 200)
        table_b_order_id = table_b_order.json()["order_id"]

        # A guest scanning TABLE A tries to "reorder" using Table B's order_id
        # (e.g. a guessed/copy-pasted id) — must be refused.
        resp = self._place(self.table_a, self.item2, order_id=table_b_order_id)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("does not belong to this table", resp.json()["error"])

        # Table B's order must be untouched — still just the original item.
        order = Order.objects.get(id=table_b_order_id)
        self.assertEqual(order.items.count(), 1)


class ReorderAfterBilledTest(_Base):
    def test_reorder_into_already_paid_order_is_rejected_cleanly(self):
        first = self._place(self.table_a, self.item)
        order_id = first.json()["order_id"]
        Order.objects.filter(id=order_id).update(status="paid")

        resp = self._place(self.table_a, self.item2, order_id=order_id)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("already been billed", resp.json()["error"])

        order = Order.objects.get(id=order_id)
        self.assertEqual(order.items.count(), 1)  # the second item was NOT added


class OrderStatusEndpointTest(_Base):
    def test_order_status_reflects_review_stage_for_guest_order(self):
        first = self._place(self.table_a, self.item)
        order_id = first.json()["order_id"]

        resp = self.client.get(reverse("order_status", args=[order_id]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["stage"], "waiting_confirmation")
        self.assertTrue(data["can_add_more"])
        self.assertEqual(len(data["items"]), 1)
        # Must never leak price/payment info to an unauthenticated poll.
        self.assertNotIn("price", json.dumps(data))
        self.assertNotIn("total", json.dumps(data).lower())

    def test_order_status_stops_offering_more_once_paid(self):
        first = self._place(self.table_a, self.item)
        order_id = first.json()["order_id"]
        Order.objects.filter(id=order_id).update(status="paid")

        resp = self.client.get(reverse("order_status", args=[order_id]))
        self.assertFalse(resp.json()["can_add_more"])


# ---------------------------------------------------------------------------
# QSR "Order Ready" feature: a QR-guest order for a franchise/cafe tenant
# must be assigned a token (previously skipped entirely -- see
# orders/views/billing_views.py::create_order, the token-assignment block
# used to require table is None, but a QR scan always resolves a table).
# ---------------------------------------------------------------------------

class _FranchiseBase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="QSR Tenant", slug="qsr-tenant", tenant_type="franchise",
        )
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Burger", price=Decimal("100.00"),
        )
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="T1", is_active=True
        )

    def _place(self, item, qty=1, order_id=None):
        payload = {
            "table_token": str(self.table.qr_token),
            "cart": [{"id": item.id, "quantity": qty}],
            "source": "web",
        }
        if order_id:
            payload["order_id"] = order_id
        return self.client.post(
            reverse("create-order"),
            data=json.dumps(payload),
            content_type="application/json",
        )


class QRGuestOrderTokenAssignmentTest(_FranchiseBase):
    def test_qr_order_for_franchise_tenant_gets_a_token(self):
        from tokens.models import TokenOrder

        resp = self._place(self.item)
        self.assertEqual(resp.status_code, 200)
        order_id = resp.json()["order_id"]

        token = TokenOrder.objects.filter(order_id=order_id).first()
        self.assertIsNotNone(token, "QR order for a franchise tenant must get a token")
        self.assertFalse(token.is_online)
        self.assertEqual(token.token_number, 1)

    def test_second_round_does_not_assign_a_second_token(self):
        from tokens.models import TokenOrder

        first = self._place(self.item)
        order_id = first.json()["order_id"]

        second = self._place(self.item, order_id=order_id)
        self.assertEqual(second.status_code, 200)

        self.assertEqual(
            TokenOrder.objects.filter(order_id=order_id).count(), 1,
            "Merging more items into the same order must not create a second token",
        )

    def test_fine_dining_qr_order_still_gets_no_token(self):
        # Regression guard: the fix must stay scoped to franchise/cafe only.
        from tokens.models import TokenOrder

        self.tenant.tenant_type = "fine_dining"
        self.tenant.save(update_fields=["tenant_type"])

        resp = self._place(self.item)
        self.assertEqual(resp.status_code, 200)
        order_id = resp.json()["order_id"]

        self.assertFalse(TokenOrder.objects.filter(order_id=order_id).exists())


class OrderStatusTokenFieldsTest(_FranchiseBase):
    def test_token_fields_absent_when_no_token(self):
        # Fine-dining-style order with no table -- e.g. a takeaway placed by
        # staff, never gets a token, order_status must degrade gracefully.
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        resp = self.client.get(reverse("order_status", args=[order.id]))
        data = resp.json()
        self.assertIsNone(data["token_display"])
        self.assertFalse(data["token_ready"])
        self.assertFalse(data["token_collected"])

    def test_token_display_present_not_ready_before_staff_marks_it(self):
        first = self._place(self.item)
        order_id = first.json()["order_id"]

        resp = self.client.get(reverse("order_status", args=[order_id]))
        data = resp.json()
        self.assertEqual(data["token_display"], "#1")
        self.assertFalse(data["token_ready"])
        self.assertFalse(data["token_collected"])

    def test_token_ready_true_once_marked_ready(self):
        from tokens.models import TokenOrder
        from django.utils import timezone

        first = self._place(self.item)
        order_id = first.json()["order_id"]
        TokenOrder.objects.filter(order_id=order_id).update(ready_at=timezone.now())

        resp = self.client.get(reverse("order_status", args=[order_id]))
        data = resp.json()
        self.assertTrue(data["token_ready"])
        self.assertFalse(data["token_collected"])

    def test_token_ready_false_once_collected(self):
        from tokens.models import TokenOrder
        from django.utils import timezone

        first = self._place(self.item)
        order_id = first.json()["order_id"]
        now = timezone.now()
        TokenOrder.objects.filter(order_id=order_id).update(ready_at=now, collected_at=now)

        resp = self.client.get(reverse("order_status", args=[order_id]))
        data = resp.json()
        self.assertFalse(data["token_ready"])
        self.assertTrue(data["token_collected"])
