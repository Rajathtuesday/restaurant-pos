"""
Who may say where an order came from (create_order's source and aggregator_id).

Before: both fields were saved exactly as the caller sent them, guests
included. A QR guest could label an order "zomato", and, worse, claim a
real aggregator order ID: aggregator_order_id is unique per outlet, so the
platform's own webhook for that ID would then be turned away as a
duplicate. Django does not enforce `choices` on save, so a staff screen (or
anyone with a staff session) could also store a source no report knows.

Now a guest always places a "web" order with no aggregator ID, and staff
values must be real choices.

Run: python manage.py test orders.tests.test_order_source
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Source Tenant", slug="source-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=category,
            name="Dal Makhani", price=Decimal("180.00"),
        )
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="T1", is_active=True,
        )
        self.cashier = User.objects.create_user(
            username="src_cashier", password="pw", role="cashier",
            tenant=self.tenant, outlet=self.outlet,
        )

    def _post(self, payload):
        return self.client.post(
            reverse("create-order"), data=json.dumps(payload), content_type="application/json",
        )

    def _guest(self, token, **extra):
        return self._post({"table_token": str(token), "cart": [{"id": self.item.id, "quantity": 1}], **extra})

    def _staff(self, **extra):
        self.client.force_login(self.cashier)
        return self._post({"cart": [{"id": self.item.id, "quantity": 1}], **extra})


class GuestCannotNameTheSourceTest(_Base):
    def test_table_guest_claiming_zomato_places_a_web_order(self):
        resp = self._guest(self.table.qr_token, source="zomato", aggregator_id="ZOM-123")
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.source, "web")
        self.assertFalse(order.aggregator_order_id)

    def test_counter_guest_claiming_swiggy_places_a_web_order(self):
        # The tableless counter QR creates the order on a different line.
        resp = self._guest(self.outlet.qr_token, source="swiggy", aggregator_id="SWG-9")
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.source, "web")
        self.assertFalse(order.aggregator_order_id)

    def test_guest_cannot_squat_a_real_aggregator_order_id(self):
        self._guest(self.table.qr_token, source="zomato", aggregator_id="ZOM-REAL-1")
        self.assertFalse(Order.objects.filter(aggregator_order_id="ZOM-REAL-1").exists())

    def test_guest_sending_nonsense_source_still_orders(self):
        # A guest's source is ignored, not refused: the menu page must keep working.
        resp = self._guest(self.table.qr_token, source=["not", "a", "string"])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Order.objects.get(id=resp.json()["order_id"]).source, "web")


class StaffSourceMustBeRealTest(_Base):
    def test_unknown_source_is_refused_and_nothing_is_saved(self):
        resp = self._staff(source="doordash")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Unknown order source.")
        self.assertEqual(Order.objects.count(), 0)

    def test_non_text_source_is_a_400_not_a_crash(self):
        resp = self._staff(source={"x": 1})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_zomato_order_keeps_its_id(self):
        resp = self._staff(source="zomato", aggregator_id="  ZOM-77  ")
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual((order.source, order.aggregator_order_id), ("zomato", "ZOM-77"))

    def test_takeaway_order_drops_an_aggregator_id(self):
        # The billing screen hides the ID box for takeaway; the server agrees.
        resp = self._staff(source="takeaway", aggregator_id="ZOM-78")
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.source, "takeaway")
        self.assertFalse(order.aggregator_order_id)

    def test_over_long_aggregator_id_is_a_clear_400(self):
        resp = self._staff(source="swiggy", aggregator_id="S" * 101)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Aggregator order ID is too long.")
        self.assertEqual(Order.objects.count(), 0)

    def test_missing_source_is_dine_in(self):
        resp = self._staff()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Order.objects.get(id=resp.json()["order_id"]).source, "dine_in")


class HistorySourceFilterTest(_Base):
    def test_filter_offers_the_values_orders_are_saved_with(self):
        owner = User.objects.create_user(
            username="src_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.client.force_login(owner)
        html = self.client.get(reverse("order-history")).content.decode()
        for value, _label in Order.SOURCE_CHOICES:
            self.assertIn(f'<option value="{value}"', html)
        self.assertNotIn('value="qr_menu"', html)
