# orders/tests/test_aggregator_ingest.py
"""
Tests for api_ingest_order (orders/api.py) — the aggregator webhook endpoint.

Bugs found and fixed in this file, oldest first:
1. A `return` from inside `transaction.atomic()` does not roll back (only an
   exception does). A bad menu_item_id partway through an order used to
   commit a broken, half-built, status="paid" order while telling the
   caller the request had failed.
2. The duplicate-order check is a check-then-act pattern with a real race
   window; the unique constraint on (outlet, aggregator_order_id) is the
   real backstop and the view must answer cleanly when it fires.
3. (Code review, 1 Oct 2026.) A repeated order ID answered 400, so the
   platform, which retries until it gets a 2xx, kept retrying; an order with
   no ID was never deduplicated, so a retry made a second paid order;
   quantity wasn't checked; the tenant/outlet lookup ran before the
   signature check and a wrong ID was a logged 500; a signed request could
   be replayed at any time.

Run: python manage.py test orders.tests.test_aggregator_ingest
"""
import hashlib
import hmac
import json
from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from menu.models import MenuCategory, MenuItem
from orders.api import is_ip_allowed
from orders.models import Order, Payment
from orders.services.aggregator_webhook import sign, signed_headers
from setup.models import AggregatorConfig
from tenants.models import Tenant, Outlet

WEBHOOK_SECRET = "test_zomato_secret"


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

    def payload(self, **changes):
        payload = {
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-1",
            "items": [{"menu_item_id": self.menu_item.id, "quantity": 2}],
        }
        payload.update(changes)
        return payload

    def _post(self, payload, headers=None):
        body = payload if isinstance(payload, str) else json.dumps(payload)
        if headers is None:
            headers = signed_headers(WEBHOOK_SECRET, body.encode())
        return self.client.post(
            reverse("api-ingest-order"), data=body, content_type="application/json", headers=headers,
        )


class ValidOrderIngestTest(AggregatorIngestBase):
    def test_valid_order_ingested_successfully(self):
        resp = self._post(self.payload(aggregator_order_id="AGG-VALID-1"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["duplicate"])

        order = Order.objects.get(id=data["order_id"])
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)

    def test_a_numeric_order_id_is_taken_as_text(self):
        resp = self._post(self.payload(aggregator_order_id=8812345))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(Order.objects.filter(aggregator_order_id="8812345").exists())


class PartialOrderRegressionTest(AggregatorIngestBase):
    """The critical regression test — a `return` inside atomic() doesn't
    roll back, so this used to leave a broken order committed."""

    def test_bad_menu_item_does_not_create_partial_order(self):
        resp = self._post(self.payload(aggregator_order_id="AGG-BAD-1", items=[
            {"menu_item_id": self.menu_item.id, "quantity": 2},  # valid
            {"menu_item_id": 999999, "quantity": 1},              # does not exist
        ]))
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_bad_first_item_also_leaves_nothing(self):
        resp = self._post(self.payload(aggregator_order_id="AGG-BAD-2", items=[
            {"menu_item_id": 999999, "quantity": 1},              # does not exist
            {"menu_item_id": self.menu_item.id, "quantity": 2},  # valid
        ]))
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

        resp = self._post(self.payload(aggregator_order_id="AGG-AUTOKOT-1"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["success"])

        order = Order.objects.get(aggregator_order_id="AGG-AUTOKOT-1")
        self.assertTrue(KOTBatch.objects.filter(order=order).exists())
        self.assertFalse(order.items.filter(status="pending").exists())


class RetriedDeliveryTest(AggregatorIngestBase):
    """A platform retries until it gets a 2xx: the same order ID is the same order."""

    def test_repeat_delivery_answers_200_with_the_first_order(self):
        first = self._post(self.payload(aggregator_order_id="AGG-DUPE-1"))
        self.assertEqual(first.status_code, 200)

        second = self._post(self.payload(aggregator_order_id="AGG-DUPE-1"))
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(second.json()["order_id"], first.json()["order_id"])
        self.assertEqual(Order.objects.filter(aggregator_order_id="AGG-DUPE-1").count(), 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_repeat_delivery_after_the_dish_was_removed_still_answers_200(self):
        first = self._post(self.payload(aggregator_order_id="AGG-DUPE-2"))
        self.menu_item.delete()
        second = self._post(self.payload(aggregator_order_id="AGG-DUPE-2"))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["order_id"], first.json()["order_id"])

    def test_simultaneous_deliveries_make_one_order(self):
        """Another delivery of the same order committed after this request's
        own check: the unique constraint stops the second insert, and the
        answer is the order already made, not an error."""
        other = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, source="zomato",
            aggregator_order_id="AGG-RACE-1", status="paid",
        )
        real_first = QuerySet.first
        missed = []

        def first_misses_once(qs):
            if qs.model is Order and not missed:
                missed.append(True)
                return None          # this request's check ran before the other committed
            return real_first(qs)

        with patch.object(QuerySet, "first", first_misses_once):
            resp = self._post(self.payload(aggregator_order_id="AGG-RACE-1"))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["order_id"], other.id)
        self.assertEqual(Order.objects.filter(aggregator_order_id="AGG-RACE-1").count(), 1)

    def test_an_order_without_its_id_is_refused(self):
        for missing in ({}, {"aggregator_order_id": ""}, {"aggregator_order_id": "   "},
                        {"aggregator_order_id": None}, {"aggregator_order_id": ["x"]}):
            payload = self.payload()
            payload.pop("aggregator_order_id")
            payload.update(missing)
            resp = self._post(payload)
            self.assertEqual(resp.status_code, 400, missing)
        self.assertEqual(Order.objects.count(), 0)

    def test_an_over_long_id_is_refused(self):
        resp = self._post(self.payload(aggregator_order_id="Z" * 101))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)


class QuantityTest(AggregatorIngestBase):
    def test_bad_quantities_are_refused_and_nothing_is_written(self):
        for quantity in (0, -2, "abc", 2.5, True, None, [1], 1000):
            resp = self._post(self.payload(items=[{"menu_item_id": self.menu_item.id, "quantity": quantity}]))
            self.assertEqual(resp.status_code, 400, quantity)
        self.assertEqual(Order.objects.count(), 0)

    def test_whole_number_quantities_are_taken(self):
        resp = self._post(self.payload(items=[{"menu_item_id": self.menu_item.id, "quantity": "3"}]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Order.objects.get().items.get().quantity, 3)

    def test_a_missing_quantity_is_one(self):
        resp = self._post(self.payload(items=[{"menu_item_id": self.menu_item.id}]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Order.objects.get().items.get().quantity, 1)


class MalformedBodyTest(AggregatorIngestBase):
    def test_shapes_that_used_to_crash_are_a_400(self):
        for payload in ("[1, 2]", "not json", json.dumps(self.payload(items="naan")),
                        json.dumps(self.payload(items=[])), json.dumps(self.payload(items=["naan"]))):
            with self.assertNoLogs("pos.api", level="ERROR"):
                resp = self._post(payload)
            self.assertEqual(resp.status_code, 400, payload)
        self.assertEqual(Order.objects.count(), 0)

    def test_too_many_lines_is_refused(self):
        items = [{"menu_item_id": self.menu_item.id, "quantity": 1}] * 101
        self.assertEqual(self._post(self.payload(items=items)).status_code, 400)


class SignatureFirstTest(AggregatorIngestBase):
    """Nothing about the tenant, outlet or order is learnt before the signature checks out."""

    def test_unknown_ids_are_a_quiet_401_not_a_logged_500(self):
        for ids in ({"tenant_id": 999999}, {"outlet_id": 999999}, {"tenant_id": "abc"},
                    {"tenant_id": None}, {"outlet_id": [1]}):
            with self.assertNoLogs("pos.api", level="ERROR"):
                resp = self._post(self.payload(**ids))
            self.assertEqual(resp.status_code, 401, ids)

    def test_another_tenants_outlet_is_a_401(self):
        other = Tenant.objects.create(name="Other", slug="other-ingest")
        resp = self._post(self.payload(tenant_id=other.id))
        self.assertEqual(resp.status_code, 401)

    def test_wrong_secret_is_a_401(self):
        body = json.dumps(self.payload())
        resp = self._post(body, headers=signed_headers("wrong-secret", body.encode()))
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(Order.objects.count(), 0)

    def test_unknown_source_is_a_401(self):
        self.assertEqual(self._post(self.payload(source="uber_eats")).status_code, 401)
        self.assertEqual(self._post(self.payload(source=["zomato"])).status_code, 401)


class ReplayTest(AggregatorIngestBase):
    """
    The webhook's clock is frozen here. With a live clock, "301 seconds in
    the future" had under a second of margin (the timestamp is whole
    seconds), so a slow run let the request through as 300.x seconds ahead.
    """
    NOW = 1_800_000_000.9    # a fraction near 1 is the case that used to slip

    def setUp(self):
        super().setUp()
        clock = patch("orders.services.aggregator_webhook.time")
        clock.start().time.return_value = self.NOW
        self.addCleanup(clock.stop)

    def _post_at(self, sent_at, signature=None):
        body = json.dumps(self.payload())
        signature = signature or sign(WEBHOOK_SECRET, int(sent_at), body.encode())
        return self._post(body, headers={"X-Timestamp": str(int(sent_at)), "X-Signature": signature})

    def test_a_request_older_than_five_minutes_is_refused(self):
        self.assertEqual(self._post_at(int(self.NOW) - 301).status_code, 401)
        self.assertEqual(Order.objects.count(), 0)

    def test_a_request_from_the_future_is_refused(self):
        self.assertEqual(self._post_at(int(self.NOW) + 301).status_code, 401)
        self.assertEqual(Order.objects.count(), 0)

    def test_a_request_within_the_window_is_taken(self):
        self.assertEqual(self._post_at(int(self.NOW) - 60).status_code, 200)

    def test_the_window_edge_is_exactly_five_minutes(self):
        # sent_at is whole seconds, NOW is not: 299.9 s old is in, 300.9 s is out,
        # and 300.1 s ahead is out.
        self.assertEqual(self._post_at(int(self.NOW) - 299).status_code, 200)
        self.assertEqual(self._post_at(int(self.NOW) - 300).status_code, 401)
        self.assertEqual(self._post_at(int(self.NOW) + 301).status_code, 401)

    def test_moving_the_timestamp_breaks_the_signature(self):
        old = int(self.NOW) - 3600
        body = json.dumps(self.payload())
        signature = sign(WEBHOOK_SECRET, old, body.encode())
        resp = self._post(body, headers={"X-Timestamp": str(int(self.NOW)), "X-Signature": signature})
        self.assertEqual(resp.status_code, 401)

    def test_a_body_only_signature_is_refused(self):
        # The format before the timestamp was signed.
        body = json.dumps(self.payload())
        old_style = hmac.new(WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(self._post(body, headers={"X-Signature": old_style}).status_code, 401)

    def test_a_missing_or_garbage_timestamp_is_refused(self):
        body = json.dumps(self.payload())
        signature = sign(WEBHOOK_SECRET, int(self.NOW), body.encode())
        for timestamp in (None, "", "yesterday"):
            headers = {"X-Signature": signature}
            if timestamp is not None:
                headers["X-Timestamp"] = timestamp
            self.assertEqual(self._post(body, headers=headers).status_code, 401, timestamp)


@override_settings(DEBUG=False)
class AllowlistRangesTest(TestCase):
    def _from(self, ip):
        return RequestFactory().post("/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP=ip)

    @override_settings(AGGREGATOR_IP_ALLOWLIST=["203.0.113.0/24", "198.51.100.7"])
    def test_ranges_and_single_addresses(self):
        self.assertTrue(is_ip_allowed(self._from("203.0.113.9")))
        self.assertTrue(is_ip_allowed(self._from("198.51.100.7")))
        self.assertFalse(is_ip_allowed(self._from("198.51.100.8")))
        self.assertFalse(is_ip_allowed(self._from("192.0.2.1")))
