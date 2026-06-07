"""
Regression + stress tests for the June 2026 critical-fix batch.

Covers:
  1. Token generation in create_order now uses the row-locked DailyTokenCounter
     (NOT MAX()+1) — the franchise/cafe QR + counter path that previously raced.
  2. Stress: assign_counter_token under heavy concurrency yields unique,
     gap-free token numbers.
  3. create_order never leaks internal exception text to the client.
  4. Tenant isolation: one tenant cannot read another tenant's order via the
     billing views (404, not data).

These tests fail against the pre-fix code and pass after it.
"""

import json
from decimal import Decimal
from threading import Thread
from unittest.mock import patch

from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from menu.models import MenuCategory, MenuItem
from orders.models import DailyTokenCounter, Order, TokenOrder
from orders.views.token_views import assign_counter_token
from setup.models import PaymentConfig
from tenants.models import Outlet, Tenant


def _franchise(name="Stress Franchise"):
    tenant = Tenant.objects.create(name=name, tenant_type="franchise")
    outlet = Outlet.objects.create(tenant=tenant, name="Outlet 1")
    PaymentConfig.objects.create(
        tenant=tenant, outlet=outlet,
        cash_enabled=True, upi_enabled=True, card_enabled=True,
    )
    return tenant, outlet


# ======================================================================
#  1. TOKEN GENERATION REGRESSION — create_order must use the counter row
# ======================================================================

class CreateOrderTokenFixTests(TransactionTestCase):
    """The buggy code used MAX()+1 and never touched DailyTokenCounter.
    These assertions only pass once create_order delegates to
    assign_counter_token (which row-locks DailyTokenCounter)."""

    def setUp(self):
        self.tenant, self.outlet = _franchise("Counter Fix Franchise")
        self.cashier = User.objects.create_user(
            username="fix_cashier", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier",
        )
        self.cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Burgers", is_active=True
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat,
            name="Zinger", price=Decimal("100"), is_available=True,
        )
        self.client = Client()
        self.client.force_login(self.cashier)

    def _create_order(self):
        return self.client.post(
            reverse("create-order"),
            data=json.dumps({"cart": [{"id": self.item.id, "quantity": 1}]}),
            content_type="application/json",
        )

    def test_create_order_creates_daily_token_counter_row(self):
        """Pre-fix code never created a DailyTokenCounter row. Post-fix it must."""
        resp = self._create_order()
        self.assertEqual(resp.status_code, 200, resp.content)

        business_date = get_business_date(timezone.now(), self.outlet)
        counter = DailyTokenCounter.objects.filter(
            tenant=self.tenant, outlet=self.outlet, date=business_date
        ).first()
        self.assertIsNotNone(
            counter,
            "create_order did not use DailyTokenCounter — still on MAX()+1?",
        )
        self.assertEqual(counter.value, 1)

    def test_tokens_are_sequential_and_counter_tracks_them(self):
        for expected in range(1, 6):
            resp = self._create_order()
            self.assertEqual(resp.status_code, 200, resp.content)
            order_id = resp.json()["order_id"]
            token = TokenOrder.objects.get(order_id=order_id)
            self.assertEqual(token.token_number, expected)

        business_date = get_business_date(timezone.now(), self.outlet)
        counter = DailyTokenCounter.objects.get(
            tenant=self.tenant, outlet=self.outlet, date=business_date
        )
        self.assertEqual(counter.value, 5)
        # No duplicate token numbers issued
        numbers = list(
            TokenOrder.objects.filter(outlet=self.outlet, is_online=False)
            .values_list("token_number", flat=True)
        )
        self.assertEqual(sorted(numbers), [1, 2, 3, 4, 5])


# ======================================================================
#  2. STRESS — assign_counter_token under heavy concurrency
# ======================================================================

class TokenStressTests(TransactionTestCase):
    """Hammer the token helper from many threads. If the row-lock were
    removed (or MAX()+1 reintroduced), token numbers collide and the
    unique_together(order, ...) / count assertions fail."""

    def setUp(self):
        self.tenant, self.outlet = _franchise("Hammer Franchise")
        self.business_date = get_business_date(timezone.now(), self.outlet)

    def _one(self, results, idx):
        from django.db import connection, transaction
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    tenant=self.tenant, outlet=self.outlet, table=None,
                    status="open", source="counter",
                )
                tok = assign_counter_token(
                    order, self.outlet, self.tenant, self.business_date
                )
                results[idx] = tok.token_number
        except Exception as e:          # noqa: BLE001 — record for assertion
            results[idx] = f"ERROR: {e}"
        finally:
            connection.close()

    def test_stress_25_concurrent_tokens_unique(self):
        n = 25
        results = [None] * n
        threads = [Thread(target=self._one, args=(results, i)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        errors = [r for r in results if isinstance(r, str)]
        self.assertEqual(errors, [], f"Errors under load: {errors}")

        # Every token number unique and the set is exactly 1..n (gap-free)
        self.assertEqual(len(set(results)), n, f"Duplicate tokens: {results}")
        self.assertEqual(sorted(results), list(range(1, n + 1)))

        counter = DailyTokenCounter.objects.get(
            tenant=self.tenant, outlet=self.outlet, date=self.business_date
        )
        self.assertEqual(counter.value, n)

    def test_sequential_50_gap_free(self):
        from django.db import transaction
        for i in range(1, 51):
            with transaction.atomic():
                order = Order.objects.create(
                    tenant=self.tenant, outlet=self.outlet, table=None,
                    status="open", source="counter",
                )
                tok = assign_counter_token(
                    order, self.outlet, self.tenant, self.business_date
                )
                self.assertEqual(tok.token_number, i)


# ======================================================================
#  3. SECURITY — create_order must not leak internal exception text
# ======================================================================

class CreateOrderSecurityTests(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _franchise("Leak Test Franchise")
        self.cashier = User.objects.create_user(
            username="leak_cashier", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier",
        )
        self.cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Cat", is_active=True
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat,
            name="Thing", price=Decimal("50"), is_available=True,
        )
        self.client = Client()
        self.client.force_login(self.cashier)

    @patch("orders.views.billing_views.add_items_to_order")
    def test_internal_error_returns_generic_message(self, mock_add):
        secret = "duplicate key value violates unique constraint orders_secret_idx"
        mock_add.side_effect = Exception(secret)

        resp = self.client.post(
            reverse("create-order"),
            data=json.dumps({"cart": [{"id": self.item.id, "quantity": 1}]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertNotIn("secret", body.get("error", "").lower())
        self.assertNotIn("constraint", body.get("error", "").lower())
        self.assertIn("try again", body.get("error", "").lower())

    def test_unauthenticated_without_token_rejected(self):
        c = Client()  # no login, no QR token
        resp = c.post(
            reverse("create-order"),
            data=json.dumps({"cart": [{"id": self.item.id, "quantity": 1}]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)


# ======================================================================
#  5. PHONE VALIDATION — bad numbers rejected cleanly, not via DB error
# ======================================================================

class CustomerPhoneValidationTests(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _franchise("Phone Test Franchise")
        self.cashier = User.objects.create_user(
            username="phone_cashier", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier",
        )
        self.cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Cat", is_active=True
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat,
            name="Thing", price=Decimal("50"), is_available=True,
        )
        self.client = Client()
        self.client.force_login(self.cashier)

    def _post(self, phone):
        return self.client.post(
            reverse("create-order"),
            data=json.dumps({
                "cart": [{"id": self.item.id, "quantity": 1}],
                "customer_phone": phone,
            }),
            content_type="application/json",
        )

    def test_valid_phone_is_stored_normalized(self):
        resp = self._post("+91 98765 43210")
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.customer_phone, "9876543210")

    def test_oversized_phone_rejected_with_generic_400(self):
        resp = self._post("9" * 1000)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mobile", resp.json()["error"].lower())
        # Nothing should have been written
        self.assertFalse(Order.objects.filter(outlet=self.outlet).exists())

    def test_empty_phone_is_allowed(self):
        resp = self._post("")
        self.assertEqual(resp.status_code, 200, resp.content)


# ======================================================================
#  6. PHONE NORMALISER — unit tests for the validator itself
# ======================================================================

class NormalizePhoneUnitTests(TestCase):

    def test_accepts_and_strips_prefixes(self):
        from core.validators import normalize_phone
        self.assertEqual(normalize_phone("9876543210"), "9876543210")
        self.assertEqual(normalize_phone("+919876543210"), "9876543210")
        self.assertEqual(normalize_phone("919876543210"), "9876543210")
        self.assertEqual(normalize_phone("09876543210"), "9876543210")
        self.assertEqual(normalize_phone("98765-43210"), "9876543210")

    def test_empty_returns_none(self):
        from core.validators import normalize_phone
        self.assertIsNone(normalize_phone(None))
        self.assertIsNone(normalize_phone(""))
        self.assertIsNone(normalize_phone("   "))

    def test_invalid_raises(self):
        from core.validators import normalize_phone
        from django.core.exceptions import ValidationError
        for bad in ["123", "5876543210", "abcdefghij", "9" * 50]:
            with self.assertRaises(ValidationError):
                normalize_phone(bad)


# ======================================================================
#  4. TENANT ISOLATION — one tenant cannot read another's orders
# ======================================================================

class TenantIsolationTests(TestCase):

    def setUp(self):
        self.t_a, self.o_a = _franchise("Tenant A")
        self.t_b, self.o_b = _franchise("Tenant B")

        self.user_a = User.objects.create_user(
            username="user_a", password="pass",
            tenant=self.t_a, outlet=self.o_a, role="owner",
        )
        # An order that belongs to tenant B
        self.order_b = Order.objects.create(
            tenant=self.t_b, outlet=self.o_b, table=None,
            status="open", source="counter",
        )
        TokenOrder.objects.create(
            tenant=self.t_b, outlet=self.o_b, order=self.order_b,
            token_number=1, date=get_business_date(timezone.now(), self.o_b),
            is_online=False,
        )

    def test_cannot_open_other_tenants_token_bill(self):
        c = Client()
        c.force_login(self.user_a)
        resp = c.get(reverse("token-bill", args=[self.order_b.id]))
        self.assertEqual(
            resp.status_code, 404,
            "Tenant A was able to load Tenant B's token bill — ISOLATION BREACH",
        )

    def test_cannot_open_other_tenants_bill_view(self):
        c = Client()
        c.force_login(self.user_a)
        resp = c.get(reverse("bill-view", args=[self.order_b.id]))
        self.assertEqual(resp.status_code, 404)