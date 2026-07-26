# promos/tests.py
"""
Tests for Promo.validate_and_use() -- moved from
orders/tests/test_schema_review.py::TestPromoValidateAndUse (Phase 0 of the
orders app split). Behavior is unchanged; only the location moved.

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


class TestPromoValidateAndUse(TestCase):

    def setUp(self):
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

    def test_validate_and_use_returns_true_on_valid_promo(self):
        ok, error = self.promo.validate_and_use(self.outlet, Decimal("500"))
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_validate_and_use_increments_usage_count(self):
        self.promo.validate_and_use(self.outlet, Decimal("500"))
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.usage_count, 1)

    def test_validate_and_use_updates_in_memory_count(self):
        self.promo.validate_and_use(self.outlet, Decimal("500"))
        # No refresh_from_db — in-memory should already reflect the increment
        self.assertEqual(self.promo.usage_count, 1)

    def test_validate_and_use_blocks_when_exhausted(self):
        Promo.objects.filter(pk=self.promo.pk).update(usage_count=5)
        self.promo.refresh_from_db()
        ok, error = self.promo.validate_and_use(self.outlet, Decimal("500"))
        self.assertFalse(ok)
        self.assertIn("limit", error.lower())

    def test_validate_and_use_does_not_increment_on_invalid(self):
        # Set min_order to something higher than the passed amount
        self.promo.min_order_value = Decimal("1000")
        self.promo.save(update_fields=["min_order_value"])
        ok, error = self.promo.validate_and_use(self.outlet, Decimal("100"))
        self.assertFalse(ok)
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.usage_count, 0)

    def test_validate_and_use_respects_inactive_promo(self):
        self.promo.is_active = False
        self.promo.save(update_fields=["is_active"])
        ok, error = self.promo.validate_and_use(self.outlet, Decimal("500"))
        self.assertFalse(ok)
        self.assertEqual(self.promo.usage_count, 0)

    def test_multiple_sequential_uses_count_correctly(self):
        for _ in range(3):
            self.promo.validate_and_use(self.outlet, Decimal("500"))
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.usage_count, 3)

    def test_exhausted_after_max_uses(self):
        for _ in range(5):
            self.promo.validate_and_use(self.outlet, Decimal("500"))
        ok, error = self.promo.validate_and_use(self.outlet, Decimal("500"))
        self.assertFalse(ok)

    def test_promo_without_max_uses_is_unlimited(self):
        self.promo.max_uses = None
        self.promo.save(update_fields=["max_uses"])
        for _ in range(10):
            ok, _ = self.promo.validate_and_use(self.outlet, Decimal("500"))
            self.assertTrue(ok)


class PromoEndpointsTest(TestCase):
    """
    HTTP-level proof that promos/urls.py -> promos/views.py -> promos.models
    wires up correctly end to end. No test exercised these URLs before the
    move either (a pre-existing gap), so this is new coverage, not a
    regression test against old behavior.
    """

    def setUp(self):
        self.tenant, self.outlet = _tenant("PromoEndpointCafe")
        self.owner = User.objects.create_user(
            username="promo_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.client = Client()
        self.client.force_login(self.owner)

    def test_create_then_list_then_toggle_then_delete(self):
        create_resp = self.client.post(
            reverse("create-promo"),
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

        toggle_resp = self.client.post(reverse("toggle-promo", args=[promo_id]), content_type="application/json")
        self.assertEqual(toggle_resp.status_code, 200)
        promo.refresh_from_db()
        self.assertFalse(promo.is_active)

        delete_resp = self.client.post(reverse("delete-promo", args=[promo_id]), content_type="application/json")
        self.assertEqual(delete_resp.status_code, 200)
        self.assertFalse(Promo.objects.filter(id=promo_id).exists())


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
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.usage_count, 1)
