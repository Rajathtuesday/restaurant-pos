"""
Which outlets name UTGST instead of SGST on their bills (Outlet.uses_utgst).

UTGST is charged only in the union territories without a legislature (CGST
Act, section 2(114)): Chandigarh, Ladakh, Lakshadweep, Andaman and Nicobar,
and Dadra and Nagar Haveli and Daman and Diu. Delhi, Puducherry and Jammu
and Kashmir have legislatures and charge SGST.

Before: the list of GSTIN state codes had Delhi (07), Puducherry (34) and
Andhra Pradesh (37, a state) and lacked 26 and 97, so an Andhra Pradesh or
Delhi restaurant's bill said UTGST.

Run: python manage.py test tenants.tests.test_utgst
"""
from decimal import Decimal as D

from django.test import TestCase

from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from orders.services.bill_layout import bill_layout
from tenants.models import Outlet, Tenant

UTGST = {"04": "Chandigarh", "25": "Daman and Diu (before 2020)", "26": "Dadra and Nagar Haveli and Daman and Diu",
         "31": "Lakshadweep", "35": "Andaman and Nicobar", "38": "Ladakh", "97": "Other Territory"}
SGST = {"01": "Jammu and Kashmir", "07": "Delhi", "34": "Puducherry", "37": "Andhra Pradesh",
        "29": "Karnataka", "27": "Maharashtra", "36": "Telangana"}


def gstin(code):
    return f"{code}ABCDE1234F1Z5"


class UsesUtgstTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="UT Test", slug="ut-test")

    def outlet(self, **fields):
        return Outlet(tenant=self.tenant, name="Main", **fields)

    def test_union_territories_without_a_legislature_charge_utgst(self):
        for code, place in UTGST.items():
            with self.subTest(place):
                self.assertTrue(self.outlet(gst_no=gstin(code)).uses_utgst)

    def test_states_and_territories_with_a_legislature_charge_sgst(self):
        for code, place in SGST.items():
            with self.subTest(place):
                self.assertFalse(self.outlet(gst_no=gstin(code)).uses_utgst)

    def test_without_a_gstin_the_manual_switch_decides(self):
        self.assertTrue(self.outlet(is_union_territory=True).uses_utgst)
        self.assertFalse(self.outlet().uses_utgst)

    def test_a_gstin_overrides_the_manual_switch(self):
        self.assertFalse(self.outlet(gst_no=gstin("07"), is_union_territory=True).uses_utgst)

    def test_the_bill_names_the_tax(self):
        for code, label in (("04", "UTGST 2.5%"), ("07", "SGST 2.5%")):
            outlet = Outlet.objects.create(tenant=self.tenant, name=f"Outlet {code}", gst_no=gstin(code))
            category = MenuCategory.objects.create(tenant=self.tenant, outlet=outlet, name="Food")
            dish = MenuItem.objects.create(tenant=self.tenant, outlet=outlet, category=category,
                                           name="Thali", price=D("200"), gst_percentage=D("5"))
            order = Order.objects.create(tenant=self.tenant, outlet=outlet, status="open")
            OrderItem.objects.create(order=order, menu_item=dish, quantity=1, price=dish.price,
                                     gst_percentage=D("5"), total_price=dish.price)
            order.recalculate_totals()
            with self.subTest(code):
                self.assertIn(label, [row.label for row in bill_layout(order).rows])
