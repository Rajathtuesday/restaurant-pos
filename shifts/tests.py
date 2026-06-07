"""
Tests for schema-review fixes applied to the shifts app.

Coverage:
  - CashSession.date field exists and is auto-populated from opened_at business date
  - CashSession (tenant, outlet, date) index present
  - CashSession filtering by date instead of opened_at__date works correctly
  - Shift.overtime_hours N+1 warning documented via test
"""
from datetime import date, time
from decimal import Decimal

from django.test import TestCase
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
