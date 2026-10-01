"""
How much one create_order request may put into an order (orders/services/cart_limits.py).

Before: quantity had a floor but no ceiling, and nothing bounded the
number of lines, a note's length or a line's modifier list. A QR guest's
items wait in review for staff, but one request could still write
thousands of rows or a bill in the crores for someone to clear. 2.5
portions was quietly taken as 2, a non-list modifier value or a cart that
wasn't a list crashed into the generic "Could not create the order", and
every cart message ("'Lassi' is currently unavailable.") was hidden
behind that same generic one.

Run: python manage.py test orders.tests.test_cart_limits
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem, Modifier, ModifierGroup
from orders.models import Order, OrderItem, Table
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Limits Tenant", slug="limits-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=category,
            name="Masala Dosa", price=Decimal("90.00"),
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1", is_active=True)
        self.cashier = User.objects.create_user(
            username="limits_cashier", password="pw", role="cashier",
            tenant=self.tenant, outlet=self.outlet,
        )

    def line(self, **changes):
        line = {"id": self.item.id, "quantity": 1}
        line.update(changes)
        return line

    def _post(self, payload):
        return self.client.post(reverse("create-order"), data=json.dumps(payload), content_type="application/json")

    def guest(self, cart):
        return self._post({"table_token": str(self.table.qr_token), "cart": cart})

    def staff(self, cart):
        self.client.force_login(self.cashier)
        return self._post({"cart": cart, "source": "takeaway"})

    def assertRefused(self, resp, message):
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()["error"], message)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)


class GuestLimitsTest(_Base):
    def test_fifty_portions_is_the_most(self):
        self.assertEqual(self.guest([self.line(quantity=50)]).status_code, 200)
        Order.objects.all().delete()
        self.assertRefused(self.guest([self.line(quantity=51)]), "Quantity can be at most 50.")

    def test_thirty_lines_is_the_most(self):
        self.assertRefused(self.guest([self.line()] * 31), "A cart can have at most 30 lines.")

    def test_a_long_note_is_refused(self):
        self.assertRefused(self.guest([self.line(note="x" * 201)]), "A note can be at most 200 characters.")

    def test_a_200_character_note_is_kept(self):
        self.assertEqual(self.guest([self.line(note="x" * 200)]).status_code, 200)
        self.assertEqual(len(OrderItem.objects.get().notes), 200)

    def test_too_many_modifiers_is_refused(self):
        self.assertRefused(self.guest([self.line(modifiers=list(range(1, 22)))]),
                           "A dish can have at most 20 modifiers.")

    def test_modifiers_that_are_not_a_list_are_refused(self):
        self.assertRefused(self.guest([self.line(modifiers="1,2")]), "Modifiers must be a list.")

    def test_a_modifier_that_is_not_an_id_is_refused(self):
        self.assertRefused(self.guest([self.line(modifiers=[{"id": 1}])]), "Modifier not found or access denied.")

    def test_fractional_and_boolean_quantities_are_refused(self):
        self.assertRefused(self.guest([self.line(quantity=2.5)]), "Quantity must be a whole number.")
        self.assertRefused(self.guest([self.line(quantity=True)]), "Quantity must be a whole number.")

    def test_a_cart_that_is_not_a_list_is_refused(self):
        self.assertRefused(self.guest({"0": self.line()}), "Cart must be a list of items.")
        self.assertRefused(self.guest(["dosa"]), "Each cart item must be an object.")


class StaffLimitsTest(_Base):
    def test_staff_may_order_up_to_999(self):
        self.assertEqual(self.staff([self.line(quantity=999)]).status_code, 200)
        Order.objects.all().delete()
        self.assertRefused(self.staff([self.line(quantity=1000)]), "Quantity can be at most 999.")

    def test_staff_get_more_lines_than_guests(self):
        self.assertEqual(self.staff([self.line()] * 31).status_code, 200)
        Order.objects.all().delete()
        self.assertRefused(self.staff([self.line()] * 101), "A cart can have at most 100 lines.")

    def test_a_modifier_still_works(self):
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        ghee = Modifier.objects.create(group=group, name="Ghee", price=Decimal("20.00"))
        resp = self.staff([self.line(quantity=2, modifiers=[ghee.id, str(ghee.id)])])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(OrderItem.objects.get().total_price, Decimal("260.00"))

    def test_takeaway_must_be_a_real_true(self):
        self.assertEqual(self.staff([self.line(is_takeaway="yes")]).status_code, 200)
        self.assertFalse(OrderItem.objects.get().is_takeaway)
        Order.objects.all().delete()
        self.assertEqual(self.staff([self.line(is_takeaway=True)]).status_code, 200)
        self.assertTrue(OrderItem.objects.get().is_takeaway)


class CartMessagesReachThePersonOrderingTest(_Base):
    def test_an_unavailable_dish_says_so(self):
        self.item.is_available = False
        self.item.save(update_fields=["is_available"])
        self.assertRefused(self.guest([self.line()]), "'Masala Dosa' is currently unavailable.")
