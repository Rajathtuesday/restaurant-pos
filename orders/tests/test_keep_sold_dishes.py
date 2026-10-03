"""
A dish that was ever sold can't be deleted, so old bills never lose it.

Found 3 Oct 2026 by running it: OrderItem.menu_item was on_delete=CASCADE, so
deleting a dish from the menu deleted every bill line that sold it. A paid
bill went from one line to none while its total (Rs 1,000) and its bill
number stayed: the bill, the item and category reports, and the GSTR-1 HSN
table all lost the sale. Deleting the dish's category did the same.

Now the line is RESTRICT, and the menu screens say what to do instead
(switch the dish off) before anything is deleted.

Run: python manage.py test orders.tests.test_keep_sold_dishes
"""
from decimal import Decimal as D

from django.db.models import RestrictedError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from tenants.models import Outlet, Tenant


class KeepSoldDishesTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Keep Dishes Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(username="kd_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.sold = self.dish("Pitcher")
        self.unsold = self.dish("Mojito")
        self.bill = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                         status="open", source="takeaway")
        OrderItem.objects.create(order=self.bill, menu_item=self.sold, quantity=1, price=D("1000"),
                                 gst_percentage=D("0"), total_price=D("1000"), status="served")
        self.bill.recalculate_totals()
        self.bill.status = "paid"
        self.bill.save()

    def dish(self, name, category=None):
        return MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category or self.bar,
                                       name=name, price=D("1000"), gst_percentage=D("0"))

    def post(self, url):
        client = Client()
        client.force_login(self.owner)
        return client.post(url)

    def test_a_sold_dish_is_kept_with_a_clear_message(self):
        resp = self.post(reverse("delete_menu_item", args=[self.sold.id]))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"],
                         "'Pitcher' is on past bills, so it can't be deleted: the bills would lose it. "
                         "Switch it off instead (it disappears from the menu and stays on the old bills).")
        self.assertTrue(MenuItem.objects.filter(pk=self.sold.pk).exists())
        self.assertEqual(self.bill.items.count(), 1)

    def test_a_dish_never_sold_can_still_be_deleted(self):
        self.assertEqual(self.post(reverse("delete_menu_item", args=[self.unsold.id])).status_code, 200)
        self.assertFalse(MenuItem.objects.filter(pk=self.unsold.pk).exists())

    def test_a_category_with_a_sold_dish_is_kept_and_says_which(self):
        resp = self.post(reverse("delete_category", args=[self.bar.id]))
        self.assertEqual(resp.status_code, 409)
        self.assertIn("(Pitcher)", resp.json()["error"])
        self.assertTrue(MenuCategory.objects.filter(pk=self.bar.pk).exists())
        self.assertEqual(MenuItem.objects.filter(category=self.bar).count(), 2)    # nothing half-deleted
        self.assertEqual(self.bill.items.count(), 1)

    def test_a_category_never_sold_from_can_still_be_deleted(self):
        mocktails = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mocktails")
        self.dish("Virgin Mary", mocktails)
        self.assertEqual(self.post(reverse("delete_category", args=[mocktails.id])).status_code, 200)
        self.assertFalse(MenuCategory.objects.filter(pk=mocktails.pk).exists())

    def test_the_database_refuses_it_too(self):
        # The backstop for any other code path: the line holds on to its dish.
        with self.assertRaises(RestrictedError):
            self.sold.delete()

    def test_deleting_a_whole_restaurant_still_works(self):
        # RESTRICT (not PROTECT) lets the dish go when its bills go in the
        # same delete, as when a tenant is removed with all its data.
        other = Tenant.objects.create(name="Closing Down")
        outlet = Outlet.objects.create(tenant=other, name="Main")
        category = MenuCategory.objects.create(tenant=other, outlet=outlet, name="Food")
        dish = MenuItem.objects.create(tenant=other, outlet=outlet, category=category, name="Dosa",
                                       price=D("90"), gst_percentage=D("0"))
        order = Order.objects.create(tenant=other, outlet=outlet, status="open", source="takeaway")
        OrderItem.objects.create(order=order, menu_item=dish, quantity=1, price=D("90"),
                                 gst_percentage=D("0"), total_price=D("90"))
        other.delete()
        self.assertFalse(MenuItem.objects.filter(pk=dish.pk).exists())
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
