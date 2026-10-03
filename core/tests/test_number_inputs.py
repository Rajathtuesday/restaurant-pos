"""
Every number staff type in is checked before it is saved.

Found 3 Oct 2026 by running it: Python's Decimal reads "NaN", "Infinity" and
"1e20" as numbers, and the screens parsed with a bare Decimal(). So:
  * a negative outlet parcel charge (-50), dish price, opening cash (-500),
    low-stock level or cost price was saved: quietly wrong money;
  * NaN in a payment, refund, expense, restock or cost price, and Infinity or
    1e20 as a dish price, became a 500;
  * negative stock on a new inventory item was a 500 (the database check);
  * a refund over the amount left showed "try again", not the real reason.
Now they all go through core.validators.read_number and say what to fix.

Run: python manage.py test core.tests.test_number_inputs
"""
import json
from decimal import Decimal as D

from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import User
from core.validators import NumberInputError, read_number
from finance.models import Expense
from inventory.models import InventoryItem
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment
from setup.models import PaymentConfig
from shifts.models import CashSession, ShiftTemplate
from tenants.models import Outlet, Tenant, TenantFeatureOverride

BAD = ("NaN", "nan", "Infinity", "-Infinity", "abc", "1e400")


class ReadNumberTest(SimpleTestCase):
    def test_odd_values_are_refused_with_a_reason(self):
        for raw in ("NaN", "Infinity", "-inf", "abc", "1,000", True, [1]):
            with self.assertRaisesMessage(NumberInputError, "The price must be a number."):
                read_number(raw, "The price")
        with self.assertRaisesMessage(NumberInputError, "The price can't be negative."):
            read_number("-1", "The price")
        with self.assertRaisesMessage(NumberInputError, "The price can have at most 2 decimal places."):
            read_number("1.005", "The price")
        with self.assertRaisesMessage(NumberInputError, "The price is required."):
            read_number("  ", "The price")

    def test_good_values(self):
        self.assertEqual(read_number(" 12.5 ", "x"), D("12.50"))
        self.assertEqual(read_number(7, "x"), D("7.00"))
        self.assertEqual(read_number("", "x", blank=D("0")), D("0"))
        self.assertEqual(read_number("-3", "x", minimum=None), D("-3.00"))

    def test_the_column_sets_the_largest_value_and_the_decimals(self):
        price = MenuItem._meta.get_field("price")                  # 10 digits, 2 decimals
        self.assertEqual(read_number("99999999.99", "The price", field=price), D("99999999.99"))
        for raw in ("100000000", "1e20"):
            with self.assertRaisesMessage(NumberInputError, "The price is too large."):
                read_number(raw, "The price", field=price)
        stock = InventoryItem._meta.get_field("stock")              # 3 decimals
        self.assertEqual(read_number("1.125", "The stock", field=stock), D("1.125"))


class NumberInputsTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Numbers Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        for feature in ("inventory", "advanced_reports"):
            TenantFeatureOverride.objects.create(tenant=self.tenant, feature=feature, enabled=True)
        self.owner = User.objects.create_user(username="num_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.client = Client()
        self.client.force_login(self.owner)
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=self.category,
                                            name="Dosa", price=D("90"), gst_percentage=D("0"))
        self.stock = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name="Rice",
                                                  unit="kg", stock=D("10"))

    def post(self, name, body, *args):
        return self.client.post(reverse(name, args=args), data=json.dumps(body),
                                content_type="application/json")

    def assertRefused(self, resp, message=None):
        self.assertEqual(resp.status_code, 400, resp.content)
        if message:
            self.assertEqual(resp.json()["error"], message)

    # --------------------------------------------------------------- setup
    def test_a_negative_outlet_parcel_charge_is_not_saved(self):
        self.outlet.parcel_charge_amount = D("10")
        self.outlet.save()
        for raw in ("-50", "NaN", "1e20"):
            resp = self.client.post(reverse("outlet_settings"),
                                    {"outlet_name": "Main", "parcel_charge_amount": raw}, follow=True)
            self.outlet.refresh_from_db()
            self.assertEqual(self.outlet.parcel_charge_amount, D("10"), raw)
            self.assertTrue(any("The parcel charge" in str(m) for m in resp.context["messages"]), raw)
        self.client.post(reverse("outlet_settings"), {"outlet_name": "Main", "parcel_charge_amount": "15"})
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.parcel_charge_amount, D("15"))

    # -------------------------------------------------------------- shifts
    def test_the_opening_cash_cannot_be_negative_or_nan(self):
        self.assertRefused(self.post("open-cash-session", {"opening_balance": "-500"}),
                           "The opening cash can't be negative.")
        self.assertRefused(self.post("open-cash-session", {"opening_balance": "NaN"}),
                           "The opening cash must be a number.")
        self.assertFalse(CashSession.objects.exists())
        self.assertEqual(self.post("open-cash-session", {"opening_balance": "500"}).status_code, 200)

    def test_the_cash_counted_at_close_cannot_be_negative(self):
        CashSession.objects.create(tenant=self.tenant, outlet=self.outlet, opened_by=self.owner,
                                   opening_balance=D("0"), status="open")
        self.assertRefused(self.post("close-cash-session", {"actual_cash": "-1"}),
                           "The cash counted can't be negative.")
        self.assertRefused(self.post("close-cash-session", {"actual_cash": "Infinity"}))
        self.assertEqual(CashSession.objects.get().status, "open")

    def test_a_shift_template_base_pay(self):
        body = {"name": "Night", "start_time": "18:00", "end_time": "23:00"}
        self.assertRefused(self.post("shift-template-create", {**body, "base_pay": "-100"}),
                           "The base pay can't be negative.")
        self.assertRefused(self.post("shift-template-create", {**body, "base_pay": "NaN"}))
        self.assertFalse(ShiftTemplate.objects.exists())

    # ------------------------------------------------------------- finance
    def test_an_expense_amount(self):
        body = {"category": dict(Expense.CATEGORY_CHOICES).popitem()[0], "expense_date": "2026-10-03"}
        for raw in BAD:
            self.assertRefused(self.post("expense_create", {**body, "amount": raw}))
        self.assertRefused(self.post("expense_create", {**body, "amount": "1e20"}), "The amount is too large.")
        self.assertFalse(Expense.objects.exists())

    # ----------------------------------------------------------- inventory
    def test_a_new_inventory_item(self):
        body = {"name": "Oil", "unit": "l"}
        self.assertRefused(self.post("create_inventory_item", {**body, "stock": "-5"}),
                           "The stock can't be negative.")
        self.assertRefused(self.post("create_inventory_item", {**body, "threshold": "-1"}),
                           "The low-stock level can't be negative.")
        self.assertRefused(self.post("create_inventory_item", {**body, "cost_price": "-2"}),
                           "The cost price can't be negative.")
        self.assertRefused(self.post("create_inventory_item", {**body, "cost_price": "NaN"}))
        self.assertFalse(InventoryItem.objects.filter(name="Oil").exists())
        self.assertEqual(self.post("create_inventory_item", {**body, "stock": "2.5"}).status_code, 200)

    def test_editing_an_inventory_item(self):
        self.assertRefused(self.post("update_inventory_item", {"cost_price": "-2"}, self.stock.id))
        self.assertRefused(self.post("update_inventory_item", {"threshold": "NaN"}, self.stock.id))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.cost_price, D("0"))

    def test_restock_wastage_and_counts(self):
        for raw in BAD:
            self.assertRefused(self.post("restock_item", {"quantity": raw}, self.stock.id))
            self.assertRefused(self.post("inventory_log_wastage", {"quantity": raw}, self.stock.id))
        self.assertRefused(self.post("inventory_adjust_stock", {"new_count": "-1", "reason": "count"},
                                     self.stock.id), "The count can't be negative.")
        self.assertRefused(self.post("inventory_adjust_stock", {"delta": "NaN", "reason": "count"},
                                     self.stock.id))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.stock, D("10"))
        # A change can still go down.
        self.assertEqual(self.post("inventory_adjust_stock", {"delta": "-2", "reason": "count"},
                                   self.stock.id).status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.stock, D("8"))

    def test_the_setup_wizard_dish_price(self):
        resp = self.client.post(reverse("onboarding_wizard") + "?step=2", {
            "category": "Starters", "item_1_name": "Soup", "item_1_price": "-50",
            "item_2_name": "Salad", "item_2_price": "120"}, follow=True)
        self.assertFalse(MenuItem.objects.filter(name="Soup").exists())
        self.assertEqual(MenuItem.objects.get(name="Salad").price, D("120"))
        self.assertIn("The price of Soup can't be negative. Soup was not added; add it from the menu screen.",
                      [str(m) for m in resp.context["messages"]])

    # ---------------------------------------------------------------- menu
    def test_a_dish_price(self):
        for raw in ("-50", "NaN", "Infinity", "1e20"):
            resp = self.client.post(reverse("create_menu_item"),
                                    {"name": "Idli " + raw, "price": raw, "category": self.category.id})
            self.assertRefused(resp)
            self.assertRefused(self.client.post(reverse("update_menu_item", args=[self.dish.id]),
                                                {"name": "Dosa", "price": raw, "category": self.category.id}))
            self.assertRefused(self.post("update_price", {"price": raw}, self.dish.id))
        self.assertFalse(MenuItem.objects.filter(name__startswith="Idli").exists())
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.price, D("90"))
        self.assertRefused(self.client.post(reverse("create_menu_item"), {
            "name": "Vada", "price": "40", "category": self.category.id, "parcel_charge": "-5"}),
            "The parcel charge can't be negative.")
        self.assertEqual(self.client.post(reverse("create_menu_item"), {
            "name": "Vada", "price": "40", "category": self.category.id}).status_code, 200)

    # ------------------------------------------------------------ payments
    def _bill(self):
        PaymentConfig.objects.get_or_create(tenant=self.tenant, outlet=self.outlet,
                                            defaults={"cash_enabled": True})
        CashSession.objects.get_or_create(tenant=self.tenant, outlet=self.outlet, status="open",
                                          defaults={"opened_by": self.owner, "opening_balance": D("0")})
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                     status="open", source="takeaway")
        OrderItem.objects.create(order=order, menu_item=self.dish, quantity=1, price=D("90"),
                                 gst_percentage=D("0"), total_price=D("90"), status="served")
        order.recalculate_totals()
        return order

    def test_a_payment_amount(self):
        order = self._bill()
        for raw in BAD + ("-10", "1e20"):
            self.assertRefused(self.post("pay-order", {"method": "cash", "amount": raw}, order.id))
        self.assertFalse(Payment.objects.filter(order=order).exists())

    def test_a_refund_amount_and_its_real_reason(self):
        order = self._bill()
        payment = Payment.objects.create(order=order, method="cash", amount=D("90"))
        for raw in BAD:
            self.assertRefused(self.post("refund-payment", {"amount": raw, "reason": "cold food"}, payment.id))
        resp = self.post("refund-payment", {"amount": "100", "reason": "cold food"}, payment.id)
        self.assertRefused(resp, "Refund request exceeds available amount. Max: ₹90.00")
        self.assertEqual(self.post("refund-payment", {"amount": "10", "reason": "x"}, 999999).status_code, 404)

    # ----------------------------------------------------------- discounts
    def test_a_discount_figure(self):
        order = self._bill()
        item = order.items.get()
        for raw in ("NaN", "Infinity", "abc"):
            self.assertRefused(self.post("apply-discount",
                                         {"type": "amount", "value": raw, "reason": "regular guest"}, order.id),
                               "The discount must be a number.")
            self.assertRefused(self.post("item-discount",
                                         {"percent": raw, "reason": "regular guest"}, item.id),
                               "The discount must be a number.")
        self.assertRefused(self.post("apply-discount", {"type": "amount", "value": "-5", "reason": "x y z"},
                                     order.id), "The discount can't be negative.")
        order.refresh_from_db()
        self.assertEqual(order.discount_value, D("0"))

    def test_the_order_api_refuses_a_discount_that_is_not_a_number(self):
        resp = self.post("create-order", {
            "source": "takeaway", "discount_type": "amount", "discount_value": "Infinity",
            "discount_reason": "regular guest",
            "cart": [{"id": self.dish.id, "quantity": 1}]})
        self.assertRefused(resp, "The discount must be a number.")
        resp = self.post("create-order", {
            "source": "takeaway",
            "cart": [{"id": self.dish.id, "quantity": 1, "discount_pct": "NaN", "discount_reason": "x y z"}]})
        self.assertRefused(resp, "A dish discount must be a number.")

    # --------------------------------------------------- tips and pay rates
    def test_tips_and_pay_rates(self):
        from shifts.models import Shift, StaffPayRate
        Shift.objects.create(tenant=self.tenant, outlet=self.outlet, staff=self.owner)
        for raw in ("-100", "abc", "NaN"):
            self.assertRefused(self.post("clock-out", {"tips": raw}))
        shift = Shift.objects.get()
        self.assertIsNone(shift.clocked_out_at)              # still on shift, nothing half-saved
        self.assertRefused(self.post("shift-tips", {"tips": "-5"}, shift.id), "The tips can't be negative.")
        self.assertEqual(self.post("clock-out", {"tips": "120"}).status_code, 200)
        shift.refresh_from_db()
        self.assertEqual(shift.tips, D("120"))

        waiter = User.objects.create_user(username="num_waiter", password="pw", role="waiter",
                                          tenant=self.tenant, outlet=self.outlet)
        for raw in ("NaN", "Infinity", "1e20"):
            self.assertRefused(self.post("edit_pay_rate", {"pay_type": "hourly", "amount": raw}, waiter.id))
        self.assertFalse(StaffPayRate.objects.exists())
