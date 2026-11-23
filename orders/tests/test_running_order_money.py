"""
The running order page's money adds up (the walkthrough recording, 23 Nov
2026): it showed Subtotal and GST only, so with happy hour on a table it
read "Subtotal Rs 3,750, GST Rs 40.50, Total Rs 3,056". It now shows the
bill's own rows (orders/services/bill_layout.py): Subtotal, Offers, the
taxes, Round off, which add up to the total.

Run: python manage.py test orders.tests.test_running_order_money
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from offers.models import Offer, OfferTarget
from orders.models import Order
from orders.services.order_service import add_items_to_order
from tenants.models import Outlet, Tenant


class RunningOrderMoneyTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Money Adds Up", tenant_type="pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.user = User.objects.create_user(username="ro_money", password="pw", tenant=self.tenant,
                                             outlet=self.outlet, role="manager")
        beer = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Draught")
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.pint = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=beer,
                                            name="Lager Pint", price=Decimal("320"), gst_percentage=5)
        self.nachos = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                              name="Loaded Nachos", price=Decimal("390"), gst_percentage=5)
        offer = Offer.objects.create(tenant=self.tenant, outlet=self.outlet, name="Happy hour",
                                     kind="percent_off", percent=Decimal("25"))
        OfferTarget.objects.create(offer=offer, menu_item=self.pint)
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.user,
                                          status="open", source="dine_in")
        add_items_to_order(self.user, self.order, [{"id": self.pint.id, "quantity": 2},
                                                   {"id": self.nachos.id, "quantity": 1}])
        self.order.refresh_from_db()
        self.client.force_login(self.user)

    def test_the_rows_include_the_offer_and_add_up_to_the_total(self):
        data = self.client.get(reverse("running-order-data", args=[self.order.id])).json()
        labels = [r["label"] for r in data["rows"]]
        self.assertTrue(any("Offer" in label for label in labels), labels)
        self.assertAlmostEqual(sum(r["amount"] for r in data["rows"]), data["total"], places=2)
        self.assertLess(data["total"], data["subtotal"] + data["gst"], "the offer came off")
