"""
A KOT prints each modifier once, and its big header doesn't leak into the
lines below it (found on the walkthrough recording, 23 Nov 2026).

  * The till pasted the chosen modifier names into the dish's note as well,
    so a KOT printed "*Peri Peri" and "+Peri Peri". The order service now
    keeps only what was typed (orders/services/item_notes.py), and older
    rows read through OrderItem.typed_note.
  * The kitchen screen showed the note but never the modifiers, so a guest's
    QR sauce choice (which has no note) never reached the cook.
  * python-escpos 3 sends nothing for set(double_width=False,
    double_height=False), so after the KOT's double-size header the divider
    printed double wide and wrapped into two lines. printing_service wraps
    the printer in SizeSafe.

These tests print through the real python-escpos (its Dummy printer), not a
stand-in that always sends a size, which is why the old tests never saw it.

Run: python manage.py test orders.tests.test_kot_prints_once
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from escpos.printer import Dummy

from accounts.models import User
from kitchen.models import KOTBatch
from kitchen.services.kitchen_service import get_kitchen_data
from menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup
from orders.models import Order, OrderItem, OrderItemModifier
from orders.services.item_notes import note_without_modifiers
from orders.services.order_service import add_items_to_order
from printing.services.printing_service import PrintingService
from setup.models import KitchenStation
from tenants.models import Outlet, Tenant

PRINTED = []


class CapturedNetwork(Dummy):
    """escpos.printer.Network, but the bytes stay here."""

    def __init__(self, host, port=9100, timeout=3, *args, **kwargs):
        super().__init__()
        PRINTED.append(self)


class NoteWithoutModifiersTests(TestCase):

    def test_a_pasted_modifier_list_comes_off(self):
        self.assertEqual(note_without_modifiers("Peri Peri", ["Peri Peri"]), "")
        self.assertEqual(note_without_modifiers("Peri Peri, Extra Cheese", ["Peri Peri", "Extra Cheese"]), "")
        self.assertEqual(note_without_modifiers("Peri Peri | no onion", ["Peri Peri"]), "no onion")

    def test_a_typed_note_stays(self):
        self.assertEqual(note_without_modifiers("no onion", ["Peri Peri"]), "no onion")
        self.assertEqual(note_without_modifiers("Peri Peri please", ["Peri Peri"]), "Peri Peri please")
        self.assertEqual(note_without_modifiers("no onion", []), "no onion")
        self.assertEqual(note_without_modifiers("", ["Peri Peri"]), "")


class KotTestBase(TestCase):

    def setUp(self):
        PRINTED.clear()
        self.tenant = Tenant.objects.create(name="KOT Once", tenant_type="pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.user = User.objects.create_user(username="kot_once", password="x", tenant=self.tenant,
                                             outlet=self.outlet, role="manager")
        self.station = KitchenStation.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Kitchen", is_default=True,
            printer_ip="192.168.1.51", printer_port=9100)
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar Bites")
        self.wings = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                             name="Chicken Wings", price=Decimal("420"), gst_percentage=5)
        sauce = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Wing sauce",
                                             is_required=True, max_select=1)
        self.peri = Modifier.objects.create(group=sauce, name="Peri Peri", price=Decimal("0"))
        MenuItemModifierGroup.objects.create(menu_item=self.wings, modifier_group=sauce)
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.user,
                                          status="open", source="dine_in")

    def old_style_line(self, note):
        """A line as an older till left it: the modifier name in the note too."""
        item = OrderItem.objects.create(order=self.order, menu_item=self.wings, quantity=1, price=self.wings.price,
                                        gst_percentage=5, total_price=self.wings.price, notes=note, status="sent")
        OrderItemModifier.objects.create(order_item=item, modifier=self.peri, name="Peri Peri", price=0)
        kot = KOTBatch.objects.create(tenant=self.tenant, outlet=self.outlet, order=self.order, kot_number=7,
                                      station=self.station, status="confirmed")
        OrderItem.objects.filter(pk=item.pk).update(kot=kot)
        return item, kot

    def print_kot(self, kot):
        service = PrintingService(printer_type="network", host="192.168.1.51")
        with mock.patch("escpos.printer.Network", CapturedNetwork):
            self.assertTrue(service.print_kot(self.order, kot))
        return PRINTED[-1].output


class TheTillsNoteTests(KotTestBase):

    def test_the_order_service_keeps_only_the_typed_note(self):
        add_items_to_order(self.user, self.order, [
            {"id": self.wings.id, "quantity": 1, "modifiers": [self.peri.id], "note": "Peri Peri | extra crispy"},
            {"id": self.wings.id, "quantity": 1, "modifiers": [self.peri.id], "note": "Peri Peri"},
        ])
        self.assertEqual(sorted(self.order.items.values_list("notes", flat=True)), ["", "extra crispy"])

    def test_a_note_that_is_not_the_modifier_list_is_kept(self):
        add_items_to_order(self.user, self.order, [
            {"id": self.wings.id, "quantity": 1, "modifiers": [self.peri.id], "note": "no onion"},
        ])
        self.assertEqual(self.order.items.get().notes, "no onion")


class KotPrintTests(KotTestBase):

    def test_each_modifier_prints_once(self):
        _, kot = self.old_style_line("Peri Peri")
        out = self.print_kot(kot)
        self.assertEqual(out.count(b"Peri Peri"), 1, out)
        self.assertIn(b"+Peri Peri", out)

    def test_the_typed_part_of_an_old_note_still_prints(self):
        _, kot = self.old_style_line("Peri Peri | extra crispy")
        out = self.print_kot(kot)
        self.assertIn(b"*extra crispy", out)
        self.assertEqual(out.count(b"Peri Peri"), 1, out)

    def test_the_divider_after_the_big_header_is_normal_size(self):
        _, kot = self.old_style_line("")
        out = self.print_kot(kot)
        header = out.index(b"KOT #7")
        divider = out.index(b"-" * 10, header)
        self.assertIn(b"\x1b!\x00", out[header:divider],
                      "normal size must be sent between the double-size header and the divider")

    def test_a_tall_dish_name_does_not_make_its_modifier_tall(self):
        _, kot = self.old_style_line("")
        out = self.print_kot(kot)
        name = out.index(b"Chicken Wings")
        modifier = out.index(b"+Peri Peri")
        self.assertIn(b"\x1b!\x00", out[name:modifier])


class KitchenScreenTests(KotTestBase):

    def test_the_kitchen_screen_shows_the_modifiers_and_the_typed_note(self):
        self.old_style_line("Peri Peri | extra crispy")
        rows = [i for k in get_kitchen_data(self.user) for i in k["items"]]
        self.assertEqual(rows[0]["modifiers"], ["Peri Peri"])
        self.assertEqual(rows[0]["notes"], "extra crispy")
