"""
A guest orders from the QR menu WITH extras (modifiers): the server side of a path
the browser could never reach while the QR menu's modifier data was double-encoded
(see menu.tests.QRMenuModifierDataTest). Nothing else covered a guest order that
carries modifier ids, so this pins down what the server does with them.

Run: python manage.py test orders.tests.test_qr_modifiers
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from menu.models import (
    MenuCategory, MenuItem, ModifierGroup, Modifier, MenuItemModifierGroup,
)
from orders.models import Order, OrderItemModifier, Table
from tenants.models import Tenant, Outlet


class GuestOrderWithModifiersTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Extras Tenant", slug="extras-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Starters"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=category,
            name="Paneer Tikka", price=Decimal("220.00"),
        )
        spice = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Spice level", is_required=True,
        )
        self.medium = Modifier.objects.create(group=spice, name="Medium", price=Decimal("0"))
        addons = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Add-ons",
        )
        self.cheese = Modifier.objects.create(group=addons, name="Extra cheese", price=Decimal("30"))
        MenuItemModifierGroup.objects.create(menu_item=self.item, modifier_group=spice)
        MenuItemModifierGroup.objects.create(menu_item=self.item, modifier_group=addons)
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="T1", is_active=True
        )

    def _place(self, cart):
        payload = {"table_token": str(self.table.qr_token), "cart": cart, "source": "web"}
        return self.client.post(
            reverse("create-order"), data=json.dumps(payload), content_type="application/json",
        )

    def test_chosen_extras_are_stored_with_their_name_and_price(self):
        resp = self._place([{"id": self.item.id, "quantity": 2,
                             "modifiers": [self.medium.id, self.cheese.id]}])
        self.assertEqual(resp.status_code, 200)

        order = Order.objects.get(id=resp.json()["order_id"])
        line = order.items.get()
        stored = {m.name: m.price for m in OrderItemModifier.objects.filter(order_item=line)}
        self.assertEqual(stored, {"Medium": Decimal("0.00"), "Extra cheese": Decimal("30.00")})

        # (220 + 30) x 2 = 500 before GST, and the order total follows the line.
        self.assertEqual(line.total_price, Decimal("500.00"))
        self.assertEqual(order.subtotal, Decimal("500.00"))

    def test_the_price_comes_from_the_database_not_the_request(self):
        # The page sends only ids. Extra fields a hostile client adds are ignored.
        resp = self._place([{"id": self.item.id, "quantity": 1,
                             "modifiers": [self.cheese.id], "price": 0, "modifier_price": 0}])
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.subtotal, Decimal("250.00"))

    def test_an_order_with_no_extras_still_works(self):
        resp = self._place([{"id": self.item.id, "quantity": 1}])
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.subtotal, Decimal("220.00"))
        self.assertEqual(OrderItemModifier.objects.count(), 0)

    def test_an_extra_from_another_restaurant_is_refused(self):
        other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other Main")
        other_group = ModifierGroup.objects.create(
            tenant=other_tenant, outlet=other_outlet, name="Other extras",
        )
        stranger = Modifier.objects.create(group=other_group, name="Stranger", price=Decimal("5"))

        resp = self._place([{"id": self.item.id, "quantity": 1, "modifiers": [stranger.id]}])
        self.assertGreaterEqual(resp.status_code, 400)
        self.assertEqual(OrderItemModifier.objects.count(), 0)
