"""
Regression tests for the June 2026 critical-fix batch.

Covers:
  1. create_order never leaks internal exception text to the client.
  2. Tenant isolation: one tenant cannot read another tenant's order via the
     billing views (404, not data).

The token-generation regression/stress tests that used to live here
(CreateOrderTokenFixTests, TokenStressTests) moved to tokens/tests.py
(Phase 2 of the orders app split), along with the token-bill half of
TenantIsolationTests below -- the bill-view half stayed, since it's
unrelated to tokens.

These tests fail against the pre-fix code and pass after it.
"""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order
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
    """The token-bill half of this class (test_cannot_open_other_tenants_token_bill)
    moved to tokens/tests.py, with its own copy of this fixture including the
    TokenOrder row that test needed -- dropped here since it's unnecessary
    for the bill-view test that stayed."""

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

    def test_cannot_open_other_tenants_bill_view(self):
        c = Client()
        c.force_login(self.user_a)
        resp = c.get(reverse("bill-view", args=[self.order_b.id]))
        self.assertEqual(resp.status_code, 404)