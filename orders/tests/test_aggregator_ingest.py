# orders/tests/test_aggregator_ingest.py
"""
Tests for api_ingest_order (orders/api.py) — the aggregator webhook endpoint.

Covers two real bugs found and fixed in this file:
1. A `return` from inside `transaction.atomic()` does not roll back (only an
   exception does). A bad menu_item_id partway through an order used to
   commit a broken, half-built, status="paid" order while telling the
   caller the request had failed.
2. The duplicate-order check (`.exists()` before `.create()`) is a
   check-then-act pattern with a real race window — two near-simultaneous
   webhook deliveries for the same aggregator order could both pass the
   check before either committed. The database-level unique constraint on
   (outlet, aggregator_order_id) is the real backstop; the view needs to
   catch the resulting IntegrityError and respond cleanly instead of 500ing.

Run: python manage.py test orders.tests.test_aggregator_ingest
"""
import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from menu.models import MenuCategory, MenuItem
from orders.models import Order, Payment
from setup.models import AggregatorConfig
from tenants.models import Tenant, Outlet

WEBHOOK_SECRET = "test_zomato_secret"


def _sign(body_str, secret=WEBHOOK_SECRET):
    return hmac.new(secret.encode(), body_str.encode(), hashlib.sha256).hexdigest()


class AggregatorIngestBase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Ingest Test Tenant", slug="ingest-test")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")
        self.config = AggregatorConfig.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            zomato_enabled=True,
            zomato_webhook_secret=WEBHOOK_SECRET,
            auto_accept_orders=False,  # keep the test focused, skip auto-KOT side effects
        )
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Butter Naan", price=60,
        )

        # is_ip_allowed only returns True automatically when settings.DEBUG is
        # True — patch it directly so this test doesn't depend on that.
        patcher = patch("orders.api.is_ip_allowed", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, payload_dict):
        body = json.dumps(payload_dict)
        sig = _sign(body)
        return self.client.post(
            reverse("api-ingest-order"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=sig,
        )


class ValidOrderIngestTest(AggregatorIngestBase):
    def test_valid_order_ingested_successfully(self):
        resp = self._post({
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-VALID-1",
            "items": [{"menu_item_id": self.menu_item.id, "quantity": 2}],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])

        order = Order.objects.get(id=data["order_id"])
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)


class PartialOrderRegressionTest(AggregatorIngestBase):
    """The critical regression test — a `return` inside atomic() doesn't
    roll back, so this used to leave a broken order committed."""

    def test_bad_menu_item_does_not_create_partial_order(self):
        resp = self._post({
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-BAD-1",
            "items": [
                {"menu_item_id": self.menu_item.id, "quantity": 2},  # valid
                {"menu_item_id": 999999, "quantity": 1},              # does not exist
            ],
        })
        self.assertEqual(resp.status_code, 422)

        # The critical assertion: nothing was left behind. Before the fix,
        # the Order (and the first, valid OrderItem) would have been
        # committed here despite the 422 response.
        self.assertEqual(Order.objects.filter(aggregator_order_id="AGG-BAD-1").count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_bad_first_item_also_leaves_nothing(self):
        """Same check with the bad item first in the list, not second —
        confirms the fix validates everything up front, not just 'the first
        N items that happened to succeed before hitting a bad one'."""
        resp = self._post({
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-BAD-2",
            "items": [
                {"menu_item_id": 999999, "quantity": 1},              # does not exist
                {"menu_item_id": self.menu_item.id, "quantity": 2},  # valid
            ],
        })
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(Order.objects.count(), 0)


class AutoAcceptKotTest(AggregatorIngestBase):
    """C5 regression: with auto_accept_orders=True, api_ingest_order used to call
    a nonexistent send_order_to_kitchen() and 500 (rolling back the whole order).
    It must now create the order AND a KOT successfully."""

    def setUp(self):
        super().setUp()
        self.config.auto_accept_orders = True
        self.config.save(update_fields=["auto_accept_orders"])

    def test_auto_accept_order_creates_kot_and_succeeds(self):
        from kitchen.models import KOTBatch

        resp = self._post({
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-AUTOKOT-1",
            "items": [{"menu_item_id": self.menu_item.id, "quantity": 2}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["success"])

        order = Order.objects.get(aggregator_order_id="AGG-AUTOKOT-1")
        # A KOT was generated and the items were transitioned out of "pending".
        self.assertTrue(KOTBatch.objects.filter(order=order).exists())
        self.assertFalse(order.items.filter(status="pending").exists())


class DuplicateOrderTest(AggregatorIngestBase):
    def test_duplicate_order_returns_400_not_500(self):
        payload = {
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-DUPE-1",
            "items": [{"menu_item_id": self.menu_item.id, "quantity": 1}],
        }
        first = self._post(payload)
        self.assertEqual(first.status_code, 200)

        second = self._post(payload)
        self.assertEqual(second.status_code, 400)
        self.assertIn("already exists", second.json()["error"])
        self.assertEqual(Order.objects.filter(aggregator_order_id="AGG-DUPE-1").count(), 1)

    @patch("django.db.models.query.QuerySet.exists", return_value=False)
    def test_concurrent_duplicate_race_returns_400_not_500(self, mock_exists):
        """Simulates the actual race the .exists() pre-check can't close on
        its own: another request already committed the duplicate order, but
        this request's .exists() check (patched to always miss, exactly as
        a genuine race would) says "no duplicate found" anyway. The real
        backstop — the database unique constraint — must be what catches
        this, and the view must turn that IntegrityError into a clean 400,
        not an unhandled 500."""
        Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, source="zomato",
            aggregator_order_id="AGG-RACE-1", status="paid",
        )

        resp = self._post({
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-RACE-1",
            "items": [{"menu_item_id": self.menu_item.id, "quantity": 1}],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already exists", resp.json()["error"])
        # Still exactly one order — no crash, no duplicate row.
        self.assertEqual(Order.objects.filter(aggregator_order_id="AGG-RACE-1").count(), 1)
