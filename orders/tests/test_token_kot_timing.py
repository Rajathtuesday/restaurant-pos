"""
Tests for WHEN a token (QSR/cafe counter) order's kitchen ticket fires.

Background: pay_order's auto-KOT logic used to be "if the tenant has no
kitchen display and no dedicated kitchen printer, fire the KOT at payment
time" -- which accidentally meant a token order at an outlet that DOES run
a kitchen display (franchise and cafe both have kitchen_display ON by
default) fell back to fine-dining's model instead: kitchen prep starts
the moment a QR item is approved, before the customer has even paid.

That's backwards for a counter order. QSR is pay-first, cook-after,
regardless of whether there happens to be a kitchen display screen -- a
KDS is just where the ticket gets shown, not a signal that prep should
start before payment. Fixed: _auto_kot now fires for every token order
unconditionally; approve_item (Token Billing's per-item accept action)
no longer creates a KOT itself at all.

Run: python manage.py test orders.tests.test_token_kot_timing
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from kitchen.models import KOTBatch
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from setup.models import PaymentConfig
from shifts.models import CashSession
from tenants.models import Tenant, Outlet
from tokens.models import TokenOrder


class _Base(TestCase):
    def setUp(self):
        # cafe gets kitchen_display ON by default (core/features.py) --
        # exactly the case that used to defer KOT-firing to a manual
        # "send to kitchen" step instead of payment.
        self.tenant = Tenant.objects.create(name="KDS Token Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.cashier = User.objects.create_user(
            username="cashier1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="cashier",
        )
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Thali", price=Decimal("150.00"), gst_percentage=Decimal("5"),
        )
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet, opened_by=self.cashier,
            opening_balance=Decimal("0"), status="open",
        )
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            cash_enabled=True, upi_enabled=True, card_enabled=True,
        )
        self.client = Client()
        self.client.login(username="cashier1", password="pw")

    def _make_token_order(self, item_status="pending"):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=None,
            created_by=self.cashier, status="open", source="counter",
        )
        OrderItem.objects.create(
            order=order, menu_item=self.item, quantity=1, price=self.item.price,
            gst_percentage=self.item.gst_percentage, total_price=self.item.price,
            status=item_status,
        )
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            token_number=1, date=date.today(), is_online=False,
        )
        order.recalculate_totals()
        return order

    def _pay(self, order, method="cash"):
        return self.client.post(
            reverse("pay-order", args=[order.id]),
            data='{"method": "%s", "amount": %s}' % (method, order.grand_total),
            content_type="application/json",
        )


class ApproveItemDoesNotFireKotTest(_Base):
    def test_approve_item_leaves_item_pending_no_kot(self):
        order = self._make_token_order()
        item = OrderItem.objects.filter(order=order).first()
        item.status = "review"
        item.save(update_fields=["status"])

        resp = self.client.post(reverse("approve-item", args=[item.id]))
        self.assertEqual(resp.status_code, 200)

        item.refresh_from_db()
        self.assertEqual(item.status, "pending")
        self.assertFalse(KOTBatch.objects.filter(order=order).exists())


class PayOrderFiresKotForTokenOrdersTest(_Base):
    """
    The actual fix: a token order at a kitchen_display=ON outlet (cafe's
    default) now gets its KOT at payment, same as one with no KDS at all.
    """

    def test_payment_creates_a_kot_even_with_kitchen_display_on(self):
        from core.features import has_feature
        self.assertTrue(has_feature(self.tenant, "kitchen_display"))  # the case that used to be missed

        order = self._make_token_order(item_status="pending")
        self.assertFalse(KOTBatch.objects.filter(order=order).exists())

        resp = self._pay(order)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(KOTBatch.objects.filter(order=order).exists())

        item = OrderItem.objects.filter(order=order).first()
        self.assertEqual(item.status, "sent")

    def test_payment_does_not_fire_a_second_kot_for_an_already_approved_and_sent_item(self):
        # Regression guard: if an item was already sent to kitchen some
        # other way before payment, pay_order's auto-KOT must not try to
        # send it again (it only picks up items still "pending").
        order = self._make_token_order(item_status="sent")
        resp = self._pay(order)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(KOTBatch.objects.filter(order=order).count(), 0)


class GenerateBillBlocksOnUnreviewedItemsTest(_Base):
    def test_checkout_blocked_while_a_review_item_remains(self):
        order = self._make_token_order(item_status="review")
        resp = self.client.post(reverse("generate-bill", args=[order.id]))
        self.assertEqual(resp.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, "open")

    def test_checkout_succeeds_once_all_items_reviewed(self):
        order = self._make_token_order(item_status="pending")
        resp = self.client.post(reverse("generate-bill", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "billing")


class RunningOrderItemsIncludesPriceTest(_Base):
    def test_item_price_and_total_present_in_response(self):
        order = self._make_token_order(item_status="pending")
        resp = self.client.get(reverse("running-order-items"), {"order": order.id})
        data = resp.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(Decimal(str(data["items"][0]["price"])), self.item.price)
        self.assertEqual(Decimal(str(data["items"][0]["total"])), self.item.price)
