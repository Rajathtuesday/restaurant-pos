"""
Smoke + access-control tests for the CRM app.

CRM touches loyalty points (money-adjacent), so at minimum we prove:
  - the dashboard and reservations load for allowed roles
  - disallowed roles get 403
  - anonymous users are redirected to login
  - one tenant cannot see another tenant's guests
"""

import json
import threading
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from crm.models import Guest, Reservation
from orders.models import Table
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class CRMAccessControlTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="CRM Cafe", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="crm_owner", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.waiter = User.objects.create_user(
            username="crm_waiter", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="waiter",
        )

    def test_dashboard_loads_for_owner(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_forbidden_for_waiter(self):
        c = Client()
        c.force_login(self.waiter)
        resp = c.get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_dashboard_redirects_anonymous(self):
        resp = Client().get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_guest_profile_loads_for_owner(self):
        guest = Guest.objects.create(tenant=self.tenant, phone="9876543210", name="Test Guest")
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("guest-profile", args=[guest.id]))
        self.assertEqual(resp.status_code, 200)

    def test_guest_profile_forbidden_for_waiter(self):
        guest = Guest.objects.create(tenant=self.tenant, phone="9876543211", name="Test Guest 2")
        c = Client()
        c.force_login(self.waiter)
        resp = c.get(reverse("guest-profile", args=[guest.id]))
        self.assertEqual(resp.status_code, 403)

    def test_reservations_loads_for_owner(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("reservation-list"))
        self.assertEqual(resp.status_code, 200)


class CRMTenantIsolationTests(TestCase):

    def setUp(self):
        self.t_a = Tenant.objects.create(name="CRM Tenant A", tenant_type="fine_dining")
        self.o_a = Outlet.objects.create(tenant=self.t_a, name="A")
        self.t_b = Tenant.objects.create(name="CRM Tenant B", tenant_type="fine_dining")
        self.o_b = Outlet.objects.create(tenant=self.t_b, name="B")

        self.owner_a = User.objects.create_user(
            username="crm_owner_a", password="pass",
            tenant=self.t_a, outlet=self.o_a, role="owner",
        )
        # A guest that belongs to tenant B only
        Guest.objects.create(tenant=self.t_b, name="Bob B", phone="9000000001")

    def test_owner_cannot_see_other_tenants_guests(self):
        c = Client()
        c.force_login(self.owner_a)
        resp = c.get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Bob B")


class ReservationStatusTransitionTests(TestCase):
    """
    Regression tests for update_reservation_status -- until now the
    Reservation model promised a full pending -> confirmed -> seated /
    cancelled / no_show lifecycle via STATUS_CHOICES, but no endpoint existed
    to actually drive it (the "Seat Guest" button called a JS function that
    was never defined).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Res Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="res_owner", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.waiter = User.objects.create_user(
            username="res_waiter", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="waiter",
        )
        self.guest = Guest.objects.create(tenant=self.tenant, name="Alice", phone="9000000010")
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")

        self.other_tenant = Tenant.objects.create(name="Res Tenant B", tenant_type="fine_dining")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Main B")
        self.other_owner = User.objects.create_user(
            username="res_owner_b", password="pass",
            tenant=self.other_tenant, outlet=self.other_outlet, role="owner",
        )

    def _reservation(self, status="pending", table=None):
        from django.utils import timezone
        return Reservation.objects.create(
            tenant=self.tenant, outlet=self.outlet, guest=self.guest,
            table=table, reservation_time=timezone.now(), status=status,
        )

    def _post(self, client, reservation_id, status):
        return client.post(
            reverse("update-reservation-status", args=[reservation_id]),
            data=json.dumps({"status": status}),
            content_type="application/json",
        )

    def test_pending_to_confirmed(self):
        res = self._reservation(status="pending")
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "confirmed")
        self.assertEqual(resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.status, "confirmed")

    def test_confirmed_to_seated(self):
        res = self._reservation(status="confirmed")
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "seated")
        self.assertEqual(resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.status, "seated")

    def test_confirmed_to_cancelled(self):
        res = self._reservation(status="confirmed")
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "cancelled")
        self.assertEqual(resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.status, "cancelled")

    def test_confirmed_to_no_show(self):
        res = self._reservation(status="confirmed")
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "no_show")
        self.assertEqual(resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.status, "no_show")

    def test_pending_to_seated_rejected(self):
        # Can't jump straight from pending to seated -- must be confirmed first.
        res = self._reservation(status="pending")
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "seated")
        self.assertEqual(resp.status_code, 400)
        res.refresh_from_db()
        self.assertEqual(res.status, "pending")

    def test_seated_is_terminal(self):
        res = self._reservation(status="seated")
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "pending")
        self.assertEqual(resp.status_code, 400)
        res.refresh_from_db()
        self.assertEqual(res.status, "seated")

    def test_waiter_cannot_update_status(self):
        res = self._reservation(status="pending")
        c = Client()
        c.force_login(self.waiter)
        resp = self._post(c, res.id, "confirmed")
        self.assertEqual(resp.status_code, 403)
        res.refresh_from_db()
        self.assertEqual(res.status, "pending")

    def test_cannot_update_across_tenants(self):
        res = self._reservation(status="pending")
        c = Client()
        c.force_login(self.other_owner)
        resp = self._post(c, res.id, "confirmed")
        self.assertEqual(resp.status_code, 404)
        res.refresh_from_db()
        self.assertEqual(res.status, "pending")

    def test_seating_moves_free_table_to_ordering(self):
        res = self._reservation(status="confirmed", table=self.table)
        self.assertEqual(self.table.state, "free")
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "seated")
        self.assertEqual(resp.status_code, 200)
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "ordering")

    def test_seating_does_not_clobber_a_busy_table(self):
        # A walk-in already has this table mid-service (state="preparing") --
        # seating a reservation linked to the same table must not silently
        # reset that in-progress state.
        self.table.state = "preparing"
        self.table.save(update_fields=["state"])
        res = self._reservation(status="confirmed", table=self.table)
        c = Client()
        c.force_login(self.owner)
        resp = self._post(c, res.id, "seated")
        self.assertEqual(resp.status_code, 200)
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "preparing")


class CreateReservationTest(TestCase):
    """
    Regression tests for create_reservation's table-lock (TOCTOU fix) and
    error-handling (no more raw exception text to the client).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Create Res Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="createres_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.other_tenant = Tenant.objects.create(name="Create Res Other", tenant_type="fine_dining")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")
        self.other_table = Table.objects.create(tenant=self.other_tenant, outlet=self.other_outlet, name="OT1")
        # Built via localtime + make_aware (not a plain UTC .replace()) so it
        # round-trips correctly through the view's own parsing: create_reservation
        # takes a naive "%Y-%m-%dT%H:%M" string and calls timezone.make_aware()
        # on it, which attaches the local zone (Asia/Kolkata). A future_time
        # built directly off timezone.now() (UTC) and reformatted would silently
        # shift by the UTC offset when reparsed, breaking any test that compares
        # it against a POSTed time for proximity.
        naive_future = (timezone.localtime(timezone.now()) + timedelta(days=1)).replace(
            hour=19, minute=0, second=0, microsecond=0, tzinfo=None
        )
        self.future_time = timezone.make_aware(naive_future)

    def _post(self, client, **overrides):
        payload = {
            "phone": "9000000001",
            "reservation_time": self.future_time.strftime("%Y-%m-%dT%H:%M"),
            "table_id": self.table.id,
            "guests": 2,
        }
        payload.update(overrides)
        return client.post(
            reverse("create-reservation"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_valid_reservation_succeeds(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Reservation.objects.filter(table=self.table).exists())

    def test_conflict_within_one_hour_window_rejected(self):
        Reservation.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            guest=Guest.objects.create(tenant=self.tenant, phone="9000000099"),
            table=self.table, reservation_time=self.future_time, status="confirmed",
        )
        client = Client()
        client.force_login(self.owner)
        # 30 minutes later than the existing one -- inside the +-1hr window.
        resp = self._post(client, reservation_time=(self.future_time + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M"))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(Reservation.objects.filter(table=self.table).count(), 1)

    def test_cross_tenant_table_rejected(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, table_id=self.other_table.id)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Reservation.objects.filter(table=self.other_table).exists())

    def test_malformed_time_returns_400_not_500(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, reservation_time="not-a-real-date")
        self.assertEqual(resp.status_code, 400)
        # And, unlike the old code, the response must never contain raw
        # Python/strptime exception text.
        self.assertNotIn("unconverted", resp.json().get("error", ""))
        self.assertNotIn("does not match format", resp.json().get("error", ""))

    def test_unexpected_error_returns_generic_message_not_raw_exception(self):
        client = Client()
        client.force_login(self.owner)
        with patch("crm.models.Reservation.objects.create", side_effect=RuntimeError("db connection reset by peer on host 10.0.4.12")):
            resp = self._post(client)
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertNotIn("10.0.4.12", body.get("error", ""))
        self.assertNotIn("db connection reset", body.get("error", ""))
        self.assertEqual(body.get("error"), "Reservation could not be created. Please try again.")


class ReservationConcurrencyTest(TransactionTestCase):
    """
    Best-effort real-concurrency proof for the TOCTOU fix: two genuinely
    parallel requests booking the same table around the same time must not
    both succeed. TransactionTestCase (not TestCase) is required here --
    TestCase wraps each test in one outer transaction, which would make the
    two threads share a single connection's uncommitted state instead of
    racing for real, defeating the point of the test.
    """

    def test_concurrent_double_booking_is_prevented(self):
        tenant = Tenant.objects.create(name="Race Tenant", tenant_type="fine_dining")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        owner = User.objects.create_user(
            username="race_owner", password="pw", role="owner", tenant=tenant, outlet=outlet,
        )
        table = Table.objects.create(tenant=tenant, outlet=outlet, name="RaceTable")
        res_time = (timezone.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)

        results = []
        barrier = threading.Barrier(2)

        def make_request():
            client = Client()
            client.force_login(owner)
            barrier.wait()  # line both threads up to fire as close together as possible
            resp = client.post(
                reverse("create-reservation"),
                data=json.dumps({
                    "phone": "9000000001",
                    "reservation_time": res_time.strftime("%Y-%m-%dT%H:%M"),
                    "table_id": table.id,
                    "guests": 2,
                }),
                content_type="application/json",
            )
            results.append(resp.status_code)

        t1 = threading.Thread(target=make_request)
        t2 = threading.Thread(target=make_request)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one request must win (200); the other must be rejected
        # (409, having seen the first one's committed row) -- never both 200.
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(Reservation.objects.filter(table=table).count(), 1)


class CrmFeatureGateTest(TestCase):
    """
    Regression tests: crm/views.py previously had NO feature gating anywhere
    -- any tenant could reach reservations/guest endpoints by URL regardless
    of the crm/reservations feature flags, even though the dashboard link
    itself was correctly hidden. One representative view per gated feature.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Gate Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="gate_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.future_time = timezone.make_aware(
            (timezone.localtime(timezone.now()) + timedelta(days=1)).replace(
                hour=19, minute=0, second=0, microsecond=0, tzinfo=None
            )
        )

    def _create_reservation(self, client):
        return client.post(
            reverse("create-reservation"),
            data=json.dumps({
                "phone": "9000000001", "table_id": self.table.id,
                "reservation_time": self.future_time.strftime("%Y-%m-%dT%H:%M"),
                "guests": 2,
            }),
            content_type="application/json",
        )

    def test_create_reservation_blocked_when_reservations_feature_off(self):
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="reservations", enabled=False,
        )
        client = Client()
        client.force_login(self.owner)

        resp = self._create_reservation(client)

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Reservation.objects.filter(table=self.table).exists())

    def test_create_reservation_allowed_when_reservations_feature_on(self):
        # fine_dining already includes "reservations" by default -- no
        # override needed, this just proves the gate doesn't over-block.
        client = Client()
        client.force_login(self.owner)

        resp = self._create_reservation(client)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Reservation.objects.filter(table=self.table).exists())

    def test_link_guest_to_order_blocked_when_crm_feature_off(self):
        from orders.models import Order
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="crm", enabled=False,
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, grand_total="100.00")
        client = Client()
        client.force_login(self.owner)

        resp = client.post(
            reverse("link-guest", args=[order.id]),
            data=json.dumps({"phone": "9000000002"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Guest.objects.filter(phone="9000000002").exists())
