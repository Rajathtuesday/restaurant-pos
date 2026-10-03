"""
The shifts and reservations pages open on the business day.

Found 3 Oct 2026: both listed the calendar date, so at 1 AM the shifts page
was empty while tonight's staff were still clocked in, and the reservations
page showed tomorrow. The rest of the app counts a business day from 6 AM to
6 AM (core.utils.get_business_date); these two pages now do the same.

Run: python manage.py test core.tests.test_business_day_pages
"""
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from crm.models import Guest, Reservation
from shifts.models import Shift
from tenants.models import Outlet, Tenant, TenantFeatureOverride

IST = ZoneInfo("Asia/Kolkata")


def ist(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


class BusinessDayPagesTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Late Night Pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="reservations", enabled=True)
        self.manager = User.objects.create_user(username="bd_manager", password="pw", role="manager",
                                                tenant=self.tenant, outlet=self.outlet)
        self.client = Client()
        self.client.force_login(self.manager)

    def get_at(self, when, url):
        with mock.patch("django.utils.timezone.now", return_value=when):
            return self.client.get(url)

    def test_at_1am_tonights_shifts_are_still_listed(self):
        evening = Shift.objects.create(tenant=self.tenant, outlet=self.outlet, staff=self.manager,
                                       clocked_in_at=ist(3, 18))
        after_midnight = Shift.objects.create(tenant=self.tenant, outlet=self.outlet, staff=self.manager,
                                              clocked_in_at=ist(4, 0, 30), clocked_out_at=ist(4, 0, 45))
        next_day = Shift.objects.create(tenant=self.tenant, outlet=self.outlet, staff=self.manager,
                                        clocked_in_at=ist(4, 9), clocked_out_at=ist(4, 10))
        resp = self.get_at(ist(4, 1), reverse("shift-dashboard"))
        self.assertEqual(str(resp.context["view_date"]), "2026-10-03")
        listed = {s.id for s in resp.context["shifts"]}
        self.assertEqual(listed, {evening.id, after_midnight.id})
        self.assertNotIn(next_day.id, listed)

    def test_at_1am_tonights_reservations_are_listed(self):
        guest = Guest.objects.create(tenant=self.tenant, phone="9000000000", name="Asha")
        dinner = Reservation.objects.create(tenant=self.tenant, outlet=self.outlet, guest=guest,
                                            reservation_time=ist(3, 21))
        late = Reservation.objects.create(tenant=self.tenant, outlet=self.outlet, guest=guest,
                                          reservation_time=ist(4, 0, 30), status="seated")
        lunch = Reservation.objects.create(tenant=self.tenant, outlet=self.outlet, guest=guest,
                                           reservation_time=ist(4, 13), status="seated")
        resp = self.get_at(ist(4, 1), reverse("reservation-list"))
        self.assertEqual(str(resp.context["view_date"]), "2026-10-03")
        self.assertEqual([r.id for r in resp.context["reservations"]], [dinner.id, late.id])
        # Asking for a date shows that business day.
        resp = self.get_at(ist(4, 1), reverse("reservation-list") + "?date=2026-10-04")
        self.assertEqual([r.id for r in resp.context["reservations"]], [lunch.id])
