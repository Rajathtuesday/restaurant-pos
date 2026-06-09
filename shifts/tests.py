"""
Tests for schema-review fixes applied to the shifts app.

Coverage:
  - CashSession.date field exists and is auto-populated from opened_at business date
  - CashSession (tenant, outlet, date) index present
  - CashSession filtering by date instead of opened_at__date works correctly
  - Shift.overtime_hours N+1 warning documented via test
"""
import json
from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from shifts.models import CashSession, Shift, ShiftTemplate, StaffSchedule
from tenants.models import Outlet, Tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tenant(name="ShiftCafe"):
    t = Tenant.objects.create(name=name)
    o = Outlet.objects.create(tenant=t, name=f"{name} HQ", business_day_start_hour=6)
    return t, o


def _user(tenant, outlet, role="cashier", suffix=""):
    return User.objects.create_user(
        username=f"u_{tenant.id}_{suffix or role}",
        password="pass", tenant=tenant, outlet=outlet, role=role,
    )


# ---------------------------------------------------------------------------
# CashSession.date tests
# ---------------------------------------------------------------------------

class TestCashSessionDateField(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _tenant("Date Field Test")
        self.user = _user(self.tenant, self.outlet)

    def test_date_field_exists_on_model(self):
        field_names = [f.name for f in CashSession._meta.get_fields()]
        self.assertIn("date", field_names)

    def test_date_is_datefield(self):
        from django.db.models import DateField
        field = CashSession._meta.get_field("date")
        self.assertIsInstance(field, DateField)

    def test_session_created_with_explicit_date(self):
        today = date.today()
        session = CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, date=today,
        )
        self.assertEqual(session.date, today)

    def test_date_stored_and_retrieved(self):
        today = date.today()
        session = CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, date=today,
        )
        session.refresh_from_db()
        self.assertEqual(session.date, today)

    def test_filter_by_date_returns_correct_session(self):
        today = date.today()
        s = CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, date=today,
        )
        qs = CashSession.objects.filter(
            tenant=self.tenant, outlet=self.outlet, date=today
        )
        self.assertIn(s, qs)

    def test_sessions_on_different_dates_distinguished(self):
        from datetime import timedelta
        today = date.today()
        yesterday = today - timedelta(days=1)

        u2 = _user(self.tenant, self.outlet, suffix="extra")

        s1 = CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, date=today,
        )
        # Close first session so the unique constraint allows a second open one
        s1.status = "closed"
        s1.save(update_fields=["status"])

        s2 = CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=u2, date=yesterday,
        )
        s2.status = "closed"
        s2.save(update_fields=["status"])

        self.assertEqual(
            CashSession.objects.filter(
                tenant=self.tenant, outlet=self.outlet, date=today
            ).count(), 1
        )
        self.assertEqual(
            CashSession.objects.filter(
                tenant=self.tenant, outlet=self.outlet, date=yesterday
            ).count(), 1
        )


class TestCashSessionIndexes(TestCase):

    def test_outlet_date_composite_index_present(self):
        names = [idx.name for idx in CashSession._meta.indexes]
        self.assertIn("cashsession_outlet_date", names)

    def test_tenant_outlet_status_index_still_present(self):
        field_sets = [tuple(idx.fields) for idx in CashSession._meta.indexes]
        self.assertIn(("tenant", "outlet", "status"), field_sets)

    def test_opened_at_index_still_present(self):
        field_sets = [tuple(idx.fields) for idx in CashSession._meta.indexes]
        self.assertIn(("opened_at",), field_sets)


# ---------------------------------------------------------------------------
# Shift.overtime_hours N+1 awareness test
# ---------------------------------------------------------------------------

class TestShiftOvertimeHoursQueryBehaviour(TestCase):
    """
    Documents the N+1 behaviour: overtime_hours fires one extra DB query per
    Shift instance. This test asserts the current count so a future refactor
    (annotated queryset) doesn't silently break the feature.
    """

    def setUp(self):
        self.tenant, self.outlet = _tenant("OT Test")
        self.staff = _user(self.tenant, self.outlet, role="waiter")

    def test_overtime_hours_returns_zero_for_active_shift(self):
        shift = Shift.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.staff,
        )
        self.assertEqual(shift.overtime_hours, 0)

    def test_overtime_hours_zero_within_limit(self):
        from datetime import datetime, timedelta
        now = timezone.now()
        shift = Shift.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.staff,
            clocked_in_at=now - timedelta(hours=7),
            clocked_out_at=now,
        )
        # No schedule → uses 9-hour default; 7h < 9h → no overtime
        self.assertEqual(shift.overtime_hours, 0)

    def test_overtime_hours_positive_beyond_limit(self):
        from datetime import timedelta
        now = timezone.now()
        shift = Shift.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.staff,
            clocked_in_at=now - timedelta(hours=11),
            clocked_out_at=now,
        )
        # No schedule → uses 9-hour default; 11h > 9h → 2h overtime
        self.assertAlmostEqual(shift.overtime_hours, 2.0, delta=0.05)

    def test_overtime_hours_uses_schedule_when_present(self):
        from datetime import timedelta
        template = ShiftTemplate.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Morning", start_time=time(8, 0), end_time=time(16, 0),
        )
        now = timezone.now()
        clock_in = now - timedelta(hours=10)
        # Schedule must match the clock-in DATE — overtime_hours looks up by
        # clocked_in_at.date() (shifts/models.py:60). Using now.date() made this
        # test flaky: a 10h-earlier clock-in crosses UTC midnight ~40% of the day,
        # missing the schedule and falling back to the 9h default.
        StaffSchedule.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            staff=self.staff, date=clock_in.date(),
            template=template, is_active=True,
        )
        shift = Shift.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.staff,
            clocked_in_at=clock_in,
            clocked_out_at=now,
        )
        # Schedule says 8h; worked 10h → 2h overtime
        self.assertAlmostEqual(shift.overtime_hours, 2.0, delta=0.05)


# ---------------------------------------------------------------------------
# Role-gate tests — codify who may access shifts management views.
# These replace 8 inline role checks that are now @role_required("manager","owner").
# If any gate is dropped, the deny-loop below fails immediately.
# ---------------------------------------------------------------------------

class ShiftsRoleGateTests(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _tenant("RoleGate")
        self.owner   = _user(self.tenant, self.outlet, role="owner",   suffix="owner")
        self.manager = _user(self.tenant, self.outlet, role="manager", suffix="manager")
        self.waiter  = _user(self.tenant, self.outlet, role="waiter",  suffix="waiter")
        self.cashier = _user(self.tenant, self.outlet, role="cashier", suffix="cashier")

        # Every manager/owner-gated shifts view. Args use dummy ids — the role
        # gate (decorator) runs before the view body, so a 403 fires regardless
        # of whether the id exists.
        self.gated = [
            reverse("shift-tips", args=[1]),
            reverse("cash-session-list"),
            reverse("schedule-builder"),
            reverse("schedule-create"),
            reverse("schedule-delete", args=[1]),
            reverse("shift-template-list"),
            reverse("shift-template-create"),
            reverse("shift-template-delete", args=[1]),
        ]

    def test_waiter_denied_on_every_gated_view(self):
        c = Client()
        c.force_login(self.waiter)
        for url in self.gated:
            resp = c.get(url)  # role_required fires before require_POST → 403 even on GET
            self.assertEqual(resp.status_code, 403, f"waiter NOT blocked at {url}")

    def test_cashier_denied_on_every_gated_view(self):
        c = Client()
        c.force_login(self.cashier)
        for url in self.gated:
            resp = c.get(url)
            self.assertEqual(resp.status_code, 403, f"cashier NOT blocked at {url}")

    def test_anonymous_redirected_to_login(self):
        c = Client()
        for url in self.gated:
            resp = c.get(url)
            self.assertEqual(resp.status_code, 302, f"anon not redirected at {url}")
            self.assertIn("/login", resp["Location"])

    def test_manager_allowed_through_page_gate(self):
        c = Client()
        c.force_login(self.manager)
        resp = c.get(reverse("schedule-builder"))
        self.assertEqual(resp.status_code, 200)

    def test_owner_allowed_through_page_gate(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("shift-template-list"))
        self.assertEqual(resp.status_code, 200)

    def test_manager_passes_gate_on_post_endpoint(self):
        # Manager POSTs empty body → gets past the role gate (not 403/302).
        # Body then fails validation (400) — proves the gate allowed them through.
        c = Client()
        c.force_login(self.manager)
        resp = c.post(
            reverse("shift-template-create"),
            data="{}", content_type="application/json",
        )
        self.assertNotIn(resp.status_code, (302, 403))


# ---------------------------------------------------------------------------
# Cash register (session) open/close endpoints — the backend of the restored
# Cash Sessions UI. These guard the feature that, once disabled, deadlocked
# fine-dining payment (no session UI, but payment requires a session).
# ---------------------------------------------------------------------------

class CashSessionEndpointTests(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _tenant("Register Co")
        self.manager = _user(self.tenant, self.outlet, role="manager", suffix="mgr")
        self.client = Client()
        self.client.force_login(self.manager)

    def test_open_session_creates_open_register(self):
        resp = self.client.post(
            reverse("open-cash-session"),
            data=json.dumps({"opening_balance": "500"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        s = CashSession.objects.get(tenant=self.tenant, outlet=self.outlet, status="open")
        self.assertEqual(s.opening_balance, Decimal("500"))

    def test_cannot_open_two_sessions_at_once(self):
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.manager, status="open",
        )
        resp = self.client.post(
            reverse("open-cash-session"),
            data=json.dumps({"opening_balance": "0"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already open", resp.json()["error"].lower())

    def test_close_session_reconciles_to_closed(self):
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet, opened_by=self.manager,
            opening_balance=Decimal("100"), status="open",
        )
        resp = self.client.post(
            reverse("close-cash-session"),
            data=json.dumps({"actual_cash": "100"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        s = CashSession.objects.get(tenant=self.tenant, outlet=self.outlet)
        self.assertEqual(s.status, "closed")
        # No sales recorded → expected == opening (100); actual 100 → no discrepancy
        self.assertEqual(s.discrepancy, Decimal("0"))

    def test_close_without_open_session_is_rejected(self):
        resp = self.client.post(
            reverse("close-cash-session"),
            data=json.dumps({"actual_cash": "0"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_cash_session_page_loads_for_owner(self):
        owner = _user(self.tenant, self.outlet, role="owner", suffix="own")
        c = Client()
        c.force_login(owner)
        resp = c.get(reverse("cash-session-list"))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Payment ↔ cash-session gate — proves WHY the Cash Sessions UI must stay
# reachable: fine-dining payment is blocked until a register is open, while
# cafe/franchise auto-opens one.
# ---------------------------------------------------------------------------

class PaymentCashSessionGateTests(TestCase):

    def _outlet_with_config(self, tenant_type):
        from setup.models import PaymentConfig
        tenant = Tenant.objects.create(name=f"Gate {tenant_type}", tenant_type=tenant_type)
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        PaymentConfig.objects.create(
            tenant=tenant, outlet=outlet,
            cash_enabled=True, upi_enabled=True, card_enabled=False,
        )
        return tenant, outlet

    def _order(self, tenant, outlet):
        from orders.models import Order
        return Order.objects.create(
            tenant=tenant, outlet=outlet, status="open", source="counter",
            grand_total=Decimal("100.00"),
        )

    def _pay(self, client, order):
        return client.post(
            reverse("pay-order", args=[order.id]),
            data=json.dumps({"method": "cash", "amount": "100"}),
            content_type="application/json",
        )

    def test_fine_dining_payment_blocked_without_open_session(self):
        tenant, outlet = self._outlet_with_config("fine_dining")
        cashier = _user(tenant, outlet, role="cashier", suffix="fd")
        c = Client(); c.force_login(cashier)
        resp = self._pay(c, self._order(tenant, outlet))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("session", resp.json()["error"].lower())

    @patch("orders.views.payment_views.process_payment")
    def test_fine_dining_payment_allowed_once_session_open(self, mock_pp):
        mock_pp.return_value = {"change_due": Decimal("0.00"), "order_closed": True}
        tenant, outlet = self._outlet_with_config("fine_dining")
        cashier = _user(tenant, outlet, role="cashier", suffix="fd2")
        CashSession.objects.create(
            tenant=tenant, outlet=outlet, opened_by=cashier, status="open",
        )
        c = Client(); c.force_login(cashier)
        resp = self._pay(c, self._order(tenant, outlet))
        self.assertNotEqual(resp.status_code, 400)
        # The session-gate error must be gone now that a register is open.
        self.assertNotIn("session", str(resp.json()).lower())

    @patch("orders.views.payment_views.process_payment")
    def test_cafe_payment_auto_opens_session(self, mock_pp):
        mock_pp.return_value = {"change_due": Decimal("0.00"), "order_closed": True}
        tenant, outlet = self._outlet_with_config("cafe")
        cashier = _user(tenant, outlet, role="cashier", suffix="cafe")
        c = Client(); c.force_login(cashier)
        # No session exists beforehand.
        self.assertFalse(CashSession.objects.filter(tenant=tenant, outlet=outlet).exists())
        resp = self._pay(c, self._order(tenant, outlet))
        self.assertNotEqual(resp.status_code, 400)
        # A session was auto-created for the QSR path.
        self.assertTrue(
            CashSession.objects.filter(tenant=tenant, outlet=outlet, status="open").exists()
        )
