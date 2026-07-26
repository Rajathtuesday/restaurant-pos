# orders/tests/test_token_system.py
"""
Tests that stayed behind when the Token Order system moved to tokens/tests.py
(Phase 2 of the orders app split) -- neither class here is token-specific,
both just used a franchise fixture as a convenient test vehicle for
otherwise-generic functionality:

  - get_business_date utility: before/after cutoff, edge midnight, no outlet
  - payment_service.process_payment: exact payment, overpayment (change_due),
    zero-amount guard, already-paid guard, closed_at set, order_closed flag

The token-specific classes that used to live here (DailyTokenCounter,
TokenOrder, create_token_order/token_dashboard/token_billing views,
feature_required gate, concurrency) moved to tokens/tests.py.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from orders.services.payment_service import process_payment
from setup.models import PaymentConfig
from shifts.models import CashSession
from tenants.models import Outlet, Tenant


# ======================================================================
#  SHARED FIXTURE MIXIN
# ======================================================================

class TokenFixtureMixin:
    """Shared setup -- kept here only for TestProcessPayment below (the
    token-specific classes that used this mixin moved to tokens/tests.py,
    which has its own copy rather than a cross-app test import)."""

    def _build_franchise_fixtures(self):
        self.tenant = Tenant.objects.create(name="Preetam Franchise", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Outlet 1")

        self.user = User.objects.create_user(
            username="cashier1",
            password="pass",
            tenant=self.tenant,
            outlet=self.outlet,
            role="cashier",
        )

        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Burgers", is_active=True
        )
        self.item_available = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Zinger", price=Decimal("120"), is_available=True,
        )
        self.item_unavailable = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="86'd Item", price=Decimal("50"), is_available=False,
        )

        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            cash_enabled=True, upi_enabled=True, card_enabled=True,
        )
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, opening_balance=0, status="open",
        )


# ======================================================================
#  1. UTILITY: get_business_date
# ======================================================================

class TestGetBusinessDate(TestCase):

    def test_daytime_returns_same_date(self):
        from core.utils import get_business_date
        from datetime import date
        outlet = Outlet.objects.create(
            tenant=Tenant.objects.create(name="T"), name="O"
        )
        # 10 AM IST is always after the 6 AM cutoff
        dt = timezone.datetime(2025, 6, 15, 10, 0, tzinfo=timezone.get_current_timezone())
        self.assertEqual(get_business_date(dt, outlet), date(2025, 6, 15))

    def test_before_cutoff_returns_previous_day(self):
        from core.utils import get_business_date
        from datetime import date
        outlet = Outlet.objects.create(
            tenant=Tenant.objects.create(name="T2"), name="O2"
        )
        # 1 AM IST is before 6 AM cutoff → previous business day
        dt = timezone.datetime(2025, 6, 15, 1, 0, tzinfo=timezone.get_current_timezone())
        self.assertEqual(get_business_date(dt, outlet), date(2025, 6, 14))

    def test_no_outlet_defaults_to_6am_cutoff(self):
        from core.utils import get_business_date
        from datetime import date
        dt = timezone.datetime(2025, 6, 15, 5, 59, tzinfo=timezone.get_current_timezone())
        self.assertEqual(get_business_date(dt, None), date(2025, 6, 14))

    def test_at_cutoff_hour_returns_same_day(self):
        from core.utils import get_business_date
        from datetime import date
        dt = timezone.datetime(2025, 6, 15, 6, 0, tzinfo=timezone.get_current_timezone())
        self.assertEqual(get_business_date(dt, None), date(2025, 6, 15))


# ======================================================================
#  2. SERVICE: process_payment
# ======================================================================

class TestProcessPayment(TestCase, TokenFixtureMixin):

    def setUp(self):
        self._build_franchise_fixtures()
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.user, status="open", source="counter",
            grand_total=Decimal("500"),
        )

    def test_exact_payment_closes_order(self):
        result = process_payment(self.order, "cash", Decimal("500"), self.user)
        self.order.refresh_from_db()
        self.assertTrue(result["order_closed"])
        self.assertEqual(self.order.status, "closed")
        self.assertIsNotNone(self.order.closed_at)
        self.assertEqual(result["remaining"], Decimal("0"))
        self.assertEqual(result["change_due"], Decimal("0"))

    def test_overpayment_gives_correct_change(self):
        """Customer hands ₹500, bill is ₹480. Change = ₹20, recorded = ₹480."""
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.user, status="open", source="counter",
            grand_total=Decimal("480"),
        )
        result = process_payment(order, "cash", Decimal("500"), self.user)
        self.assertEqual(result["change_due"], Decimal("20"))
        self.assertEqual(result["remaining"], Decimal("0"))
        # Only ₹480 was recorded, not ₹500
        self.assertEqual(result["payment"].amount, Decimal("480"))
        order.refresh_from_db()
        self.assertEqual(order.status, "closed")

    def test_partial_payment_does_not_close(self):
        result = process_payment(self.order, "upi", Decimal("200"), self.user)
        self.order.refresh_from_db()
        self.assertFalse(result["order_closed"])
        self.assertNotEqual(self.order.status, "closed")
        self.assertEqual(result["remaining"], Decimal("300"))

    def test_split_payment_closes_on_second(self):
        process_payment(self.order, "cash", Decimal("300"), self.user)
        result = process_payment(self.order, "upi", Decimal("200"), self.user)
        self.order.refresh_from_db()
        self.assertTrue(result["order_closed"])
        self.assertEqual(self.order.status, "closed")

    def test_zero_amount_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            process_payment(self.order, "cash", Decimal("0"))

    def test_negative_amount_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            process_payment(self.order, "cash", Decimal("-50"))

    def test_already_paid_raises(self):
        from django.core.exceptions import ValidationError
        process_payment(self.order, "cash", Decimal("500"), self.user)
        with self.assertRaises(ValidationError):
            process_payment(self.order, "cash", Decimal("1"), self.user)

    def test_string_amount_is_coerced(self):
        """Amounts sent as strings from JSON should still work."""
        result = process_payment(self.order, "cash", "500", self.user)
        self.assertTrue(result["order_closed"])

    def test_payment_record_links_to_order(self):
        result = process_payment(self.order, "card", Decimal("500"), self.user)
        self.assertEqual(result["payment"].order_id, self.order.id)
        self.assertEqual(result["payment"].method, "card")

    def test_closed_at_is_set(self):
        process_payment(self.order, "cash", Decimal("500"), self.user)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.closed_at)

    def test_closed_at_not_set_on_partial(self):
        process_payment(self.order, "cash", Decimal("100"), self.user)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.closed_at)
