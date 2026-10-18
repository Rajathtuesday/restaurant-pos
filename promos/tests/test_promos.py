# promos/tests.py
"""
Tests for Promo.refusal_for() (the cap, the minimum, switched off). They
began as the validate_and_use() tests moved from
orders/tests/test_schema_review.py (Phase 0 of the orders app split); the
usage counter they tested was replaced by counting bills on 18 Oct 2026.

Also covers the HTTP layer (list/create/toggle/delete + cross-app use from
orders' apply_discount), which had NO test coverage before this move --
added here specifically to prove the full stack (URL routing -> view ->
promos.models.Promo) still works end to end after relocating the app,
since that's the part most likely to silently break in a file-move refactor
(a stale import, a URL name typo) that model-only tests can't catch.

Run: python manage.py test promos
"""
import json
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from promos.models import Promo
from tenants.models import Tenant, Outlet


def _tenant(name="TestRest"):
    t = Tenant.objects.create(name=name)
    o = Outlet.objects.create(tenant=t, name=f"{name} HQ")
    return t, o


class TestPromoRefusal(TestCase):
    """Promo.refusal_for(): whether a bill may take the promo now. Its uses
    are the bills holding it (Promo.uses), so the cap counts bills; it used
    to be a counter (validate_and_use), replaced on 18 Oct 2026."""

    def setUp(self):
        from orders.models import Order
        self.Order = Order
        self.tenant, self.outlet = _tenant("PromoCafe")
        self.promo = Promo.objects.create(
            tenant=self.tenant,
            name="10% Off",
            discount_type="percentage",
            discount_value=Decimal("10"),
            min_order_value=Decimal("0"),
            max_uses=5,
            is_active=True,
        )
        self.order = self.bill()

    def bill(self, holding=False):
        order = self.Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        if holding:
            self.Order.objects.filter(pk=order.pk).update(
                promo=self.promo, promo_name=self.promo.name,
                discount_type="percentage", discount_value=Decimal("10"))
        return order

    def test_a_valid_promo_is_not_refused(self):
        self.assertEqual(self.promo.refusal_for(self.order, Decimal("500")), "")

    def test_refused_when_the_bills_have_used_it_up(self):
        for _ in range(5):
            self.bill(holding=True)
        self.assertEqual(self.promo.uses, 5)
        self.assertIn("limit", self.promo.refusal_for(self.order, Decimal("500")).lower())

    def test_the_bills_own_hold_does_not_count(self):
        for _ in range(4):
            self.bill(holding=True)
        mine = self.bill(holding=True)
        self.assertEqual(self.promo.refusal_for(mine, Decimal("500")), "")

    def test_cancelled_bills_do_not_count(self):
        for _ in range(5):
            self.bill(holding=True)
        self.Order.objects.filter(promo=self.promo).update(status="cancelled")
        self.assertEqual(self.promo.uses, 0)
        self.assertEqual(self.promo.refusal_for(self.order, Decimal("500")), "")

    def test_refused_below_the_minimum(self):
        self.promo.min_order_value = Decimal("1000")
        self.promo.save(update_fields=["min_order_value"])
        self.assertIn("Minimum", self.promo.refusal_for(self.order, Decimal("100")))

    def test_refused_when_switched_off(self):
        self.promo.is_active = False
        self.promo.save(update_fields=["is_active"])
        self.assertIn("not active", self.promo.refusal_for(self.order, Decimal("500")))

    def test_promo_without_max_uses_is_unlimited(self):
        self.promo.max_uses = None
        self.promo.save(update_fields=["max_uses"])
        for _ in range(10):
            self.bill(holding=True)
        self.assertEqual(self.promo.refusal_for(self.order, Decimal("500")), "")


class PromoEndpointsTest(TestCase):
    """
    The Setup screen's promo endpoints (setup/views/promo_views.py) end to
    end, and the bill screen's list (promos/views.py). A second, older set of
    create/toggle/delete endpoints in promos/views.py, which ignored the
    minimum, the cap and the scope, was removed on 3 Oct 2026; deleting is
    now archiving, so bills that used a promo keep pointing at it.
    """

    def setUp(self):
        self.tenant, self.outlet = _tenant("PromoEndpointCafe")
        self.owner = User.objects.create_user(
            username="promo_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.client = Client()
        self.client.force_login(self.owner)

    def test_create_then_list_then_toggle_then_archive(self):
        create_resp = self.client.post(
            reverse("promo_create"),
            data=json.dumps({
                "name": "Happy Hour", "code": "HH10",
                "discount_type": "percentage", "discount_value": "10",
            }),
            content_type="application/json",
        )
        self.assertEqual(create_resp.status_code, 200)
        promo_id = create_resp.json()["id"]
        promo = Promo.objects.get(id=promo_id)
        self.assertEqual(promo.tenant_id, self.tenant.id)

        list_resp = self.client.get(reverse("list-promos"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertIn(promo_id, [p["id"] for p in list_resp.json()["promos"]])

        toggle_resp = self.client.post(reverse("promo_toggle", args=[promo_id]), content_type="application/json")
        self.assertEqual(toggle_resp.status_code, 200)
        promo.refresh_from_db()
        self.assertFalse(promo.is_active)

        delete_resp = self.client.post(reverse("promo_delete", args=[promo_id]), content_type="application/json")
        self.assertEqual(delete_resp.status_code, 200)
        promo.refresh_from_db()
        self.assertIsNotNone(promo.archived_at)          # archived, not deleted
        self.assertNotIn(promo_id, [p["id"] for p in self.client.get(reverse("list-promos")).json()["promos"]])


class PromoAppliedFromOrdersDiscountViewTest(TestCase):
    """
    Cross-app proof: orders/views/discount_views.py::apply_discount imports
    promos.models.Promo directly (a stale `from orders.models import Promo`
    left behind by the move would break this at request time, not at
    manage.py check time, since the import happens inside the function body).
    """

    def setUp(self):
        from menu.models import MenuCategory, MenuItem
        from orders.models import Order, OrderItem

        self.tenant, self.outlet = _tenant("PromoDiscountCafe")
        self.owner = User.objects.create_user(
            username="promo_discount_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=category,
            name="Thali", price=Decimal("500.00"), gst_percentage=Decimal("0"),
        )
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner)
        OrderItem.objects.create(
            order=self.order, menu_item=menu_item, quantity=1,
            price=menu_item.price, gst_percentage=menu_item.gst_percentage,
            total_price=menu_item.price, status="pending",
        )
        self.order.recalculate_totals()

        self.promo = Promo.objects.create(
            tenant=self.tenant, name="Flat 50", discount_type="amount",
            discount_value=Decimal("50"), is_active=True,
        )

        self.client = Client()
        self.client.force_login(self.owner)

    def test_apply_discount_with_promo_id_finds_and_applies_promo(self):
        resp = self.client.post(
            reverse("apply-discount", args=[self.order.id]),
            data=json.dumps({"promo_id": self.promo.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.discount_value, Decimal("50"))
        self.assertEqual(self.promo.uses, 1)
