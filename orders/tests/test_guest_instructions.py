"""
A QR guest's "special instructions" reach the kitchen.

The guest menu sends the instructions box as "notes" with the cart, and
create_order never read it: "less spicy" was dropped on the way. It now
becomes the kitchen note of each dish in that cart, which the KOT and the
kitchen screen show; a dish's own note wins.

Run: python manage.py test orders.tests.test_guest_instructions
"""
import json
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from tenants.models import Outlet, Tenant


class GuestInstructionsTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Instructions Cafe", slug="instructions-cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.curry = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                             name="Paneer Curry", price=D("220"))
        self.naan = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Butter Naan", price=D("60"))
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T3", is_active=True)

    def order(self, cart, **extra):
        return self.client.post(reverse("create-order"), content_type="application/json", data=json.dumps({
            "table_token": str(self.table.qr_token), "cart": cart, "source": "web", **extra,
        }))

    def notes(self):
        return {item.menu_item.name: item.notes for item in OrderItem.objects.select_related("menu_item")}

    def test_the_instructions_go_on_every_dish_in_the_cart(self):
        resp = self.order([{"id": self.curry.id, "quantity": 1}, {"id": self.naan.id, "quantity": 2}],
                          notes="  less spicy, no onion  ")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.notes(), {"Paneer Curry": "less spicy, no onion", "Butter Naan": "less spicy, no onion"})

    def test_a_dishs_own_note_wins(self):
        self.order([{"id": self.curry.id, "quantity": 1, "note": "extra gravy"}, {"id": self.naan.id, "quantity": 1}],
                   notes="less spicy")
        self.assertEqual(self.notes(), {"Paneer Curry": "extra gravy", "Butter Naan": "less spicy"})

    def test_no_instructions_leaves_no_note(self):
        self.order([{"id": self.curry.id, "quantity": 1}], notes="   ")
        self.assertEqual(self.notes(), {"Paneer Curry": ""})

    def test_instructions_longer_than_a_note_are_refused(self):
        resp = self.order([{"id": self.curry.id, "quantity": 1}], notes="x" * 201)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "A note can be at most 200 characters.")
        self.assertFalse(Order.objects.exists())

    def test_the_kitchen_ticket_prints_them(self):
        from kitchen.services.kot_service import create_kot
        from orders.tests.test_golden_receipts import RecordingPrinter
        from orders.services.printing_service import PrintingService
        self.order([{"id": self.curry.id, "quantity": 1}], notes="less spicy")
        order = Order.objects.get()
        order.items.update(status="pending")     # as staff approving the guest's items
        [kot] = create_kot(None, order, print_on_create=False)
        printer = RecordingPrinter()
        PrintingService(chars_per_line=32)._print_kot_body(printer, order, kot)
        self.assertTrue(any("less spicy" in line for line in printer.lines), printer.lines)
