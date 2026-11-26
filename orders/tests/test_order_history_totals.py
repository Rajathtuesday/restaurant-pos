"""
Order History's summary: how many orders, what they came to, the average.

Why (26 Nov 2026, item 1 of md_files/rasova_fault_fix_plan_2026-11-26.md):
the summary was one aggregate() with Count("items") beside
Sum("grand_total"). The join to the items table repeats each order once per
dish, so each bill was added once per dish and the dishes were counted as
orders. The demo pub showed 49 orders and Rs 84,109; the truth was 21 orders
and Rs 34,379. The third chip showed the revenue again as its big number,
and money read "Rs.84109", unlike every other screen.

Run: python manage.py test orders.tests.test_order_history_totals
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.money import rupees
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment
from tenants.models import Outlet, Tenant


class OrderHistoryTotalsTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="History Pub", slug="history-pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="history_owner", password="pw", role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Starters")
        self.dishes = [
            MenuItem.objects.create(
                tenant=self.tenant, outlet=self.outlet, category=category, name=f"Dish {n}",
                price=Decimal("1000.00"), gst_percentage=Decimal("5"),
            )
            for n in range(3)
        ]
        # Three dishes on one bill, two on the other.
        self.three = self._settled([1, 1, 1])
        self.two = self._settled([2, 1])
        self.client.force_login(self.owner)

    def _settled(self, quantities):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, created_by=self.owner, status="closed", source="dine_in",
        )
        for dish, quantity in zip(self.dishes, quantities):
            OrderItem.objects.create(
                order=order, menu_item=dish, quantity=quantity, price=dish.price,
                gst_percentage=dish.gst_percentage, total_price=dish.price * quantity,
            )
        order.recalculate_totals()
        order.refresh_from_db()
        return order

    def _page(self, **params):
        resp = self.client.get(reverse("order-history"), params)
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_two_orders_are_two_orders(self):
        self.assertEqual(self._page().context["total_orders"], 2)

    def test_the_revenue_is_the_two_bills(self):
        resp = self._page()
        self.assertEqual(resp.context["total_revenue"], self.three.grand_total + self.two.grand_total)

    def test_the_chips_say_orders_revenue_and_the_average_bill(self):
        total = self.three.grand_total + self.two.grand_total
        resp = self._page()
        self.assertContains(resp, f"<strong>{rupees(total)}</strong>", html=False)
        self.assertContains(resp, f"<strong>{rupees((total / 2).quantize(Decimal('1')))}</strong>", html=False)
        self.assertContains(resp, "Average bill")

    def test_money_reads_in_rupees_with_indian_grouping(self):
        resp = self._page()
        self.assertNotContains(resp, "Rs.")
        self.assertContains(resp, rupees(self.three.grand_total))    # the row of the three-dish bill

    def test_a_filter_that_joins_payments_counts_each_order_once(self):
        # A split bill paid in cash twice: the payments filter joins the
        # payments table, which repeats the order once per payment.
        for amount in (Decimal("1500.00"), self.three.grand_total - Decimal("1500.00")):
            Payment.objects.create(order=self.three, method="cash", amount=amount, created_by=self.owner)
        resp = self._page(method="cash")
        self.assertEqual(resp.context["total_orders"], 1)
        self.assertEqual(resp.context["total_revenue"], self.three.grand_total)
