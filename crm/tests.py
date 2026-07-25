"""
Smoke + access-control tests for the CRM app.

CRM touches loyalty points (money-adjacent), so at minimum we prove:
  - the dashboard and reservations load for allowed roles
  - disallowed roles get 403
  - anonymous users are redirected to login
  - one tenant cannot see another tenant's guests
"""

import json

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from crm.models import Guest, Reservation
from orders.models import Table
from tenants.models import Outlet, Tenant


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
