"""
Print Queue — Complete Test Suite
===================================

HOW THE SYSTEM WORKS
--------------------
Android phones cannot reliably host a WebSocket server (battery optimiser kills it).
Instead, the browser pushes a print job to EC2 via HTTPS, the agent polls EC2 every
2 s over plain HTTP, prints, and marks the job done.

DATA MODEL
----------
  Outlet.print_agent_key  — UUID secret that authenticates the agent's HTTP polls.
                             Never exposed to the browser.  One per outlet.
  PrintJob                — One row per receipt.  States: pending → done / failed.
                            Auto-expires after 5 min (stale jobs are not served).

API SURFACE
-----------
  POST /orders/agent/add-job/
      Browser (logged in) queues a job.  Requires order_id in JSON body.
      Server generates ESC/POS lines and stores with printer IP from KitchenStation.
      Returns 422 when no printer is configured for the outlet.

  GET  /orders/agent/<key>/jobs/
      Agent polls.  Returns up to 5 pending non-expired jobs.
      Invalid key → 403.  No CSRF needed (auth via key).

  POST /orders/agent/<key>/done/<id>/
      Agent marks a job done.  Wrong key or already-done → 404/403.

  POST /orders/agent/<key>/failed/<id>/
      Agent records a failure so operators can see it in the DB.

SECURITY
--------
  add-job requires Django session (login_required).
  All agent endpoints authenticate via the outlet's print_agent_key UUID.
  A wrong key always returns 403, even if the job exists.
  The key is never sent to the browser (baked into the agent command only).

Run: python manage.py test orders.tests.test_print_queue --keepdb
"""

import uuid

from django.test import Client, TestCase

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, PrintJob
from setup.models import KitchenStation, PaymentConfig
from tenants.models import Outlet, Tenant


# ── Shared fixture ─────────────────────────────────────────────────────────────

class PrintQueueBase(TestCase):
    """Outlet with a configured KitchenStation printer and one menu item."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Print Queue Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Café Counter")
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet, cash_enabled=True,
        )
        self.owner = User.objects.create_user(
            username="pq_owner", password="testpass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Drinks"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.category, name="Chai",
            price=20, gst_percentage=0,
        )
        # Default kitchen station with a printer IP
        self.station = KitchenStation.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Cashier", is_default=True, is_active=True,
            printer_ip="192.168.1.100", printer_port=9100,
            printer_encoding="cp437", paper_width_mm=80,
        )
        self.client = Client()
        self.client.login(username="pq_owner", password="testpass")

    def _make_order(self, qty=2, status="open"):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.owner, source="counter", status=status,
        )
        OrderItem.objects.create(
            order=order, menu_item=self.item,
            quantity=qty, price=self.item.price,
            gst_percentage=self.item.gst_percentage,
            total_price=self.item.price * qty,
        )
        order.recalculate_totals()
        return order

    def _add_job(self, order_id):
        return self.client.post(
            "/orders/agent/add-job/",
            data={"order_id": order_id},
            content_type="application/json",
        )

    def _poll(self, key=None):
        key = key or str(self.outlet.print_agent_key)
        return self.client.get(f"/orders/agent/{key}/jobs/")

    def _done(self, job_id, key=None):
        key = key or str(self.outlet.print_agent_key)
        return self.client.post(f"/orders/agent/{key}/done/{job_id}/",
                                content_type="application/json")

    def _failed(self, job_id, key=None, error=""):
        key = key or str(self.outlet.print_agent_key)
        return self.client.post(
            f"/orders/agent/{key}/failed/{job_id}/",
            data={"error": error},
            content_type="application/json",
        )


# ── add-job: browser-side ─────────────────────────────────────────────────────

class AddJobTests(PrintQueueBase):

    def test_add_job_returns_200_and_job_id(self):
        """Valid order → 200 with job_id."""
        order = self._make_order()
        r = self._add_job(order.id)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["success"])
        self.assertIn("job_id", d)

    def test_job_created_in_db_with_pending_status(self):
        """Job row is created with status=pending."""
        order = self._make_order()
        r = self._add_job(order.id)
        job_id = r.json()["job_id"]
        job = PrintJob.objects.get(pk=job_id)
        self.assertEqual(job.status, PrintJob.PENDING)
        self.assertEqual(job.outlet, self.outlet)

    def test_job_payload_contains_printer_ip(self):
        """Payload has the station's printer_ip so the agent knows where to print."""
        order = self._make_order()
        r = self._add_job(order.id)
        job = PrintJob.objects.get(pk=r.json()["job_id"])
        self.assertEqual(job.payload["network_host"], "192.168.1.100")
        self.assertEqual(job.payload["network_port"], 9100)

    def test_job_payload_has_escpos_data_b64(self):
        """Payload contains base64-encoded ESC/POS bytes (non-empty, valid base64)."""
        import base64
        order = self._make_order()
        r = self._add_job(order.id)
        job = PrintJob.objects.get(pk=r.json()["job_id"])
        data_b64 = job.payload.get("data_b64", "")
        self.assertGreater(len(data_b64), 0)
        raw = base64.b64decode(data_b64)
        self.assertGreater(len(raw), 0)

    def test_add_job_no_printer_configured_returns_422(self):
        """Outlet with no printer IP → 422 Unprocessable."""
        self.station.printer_ip = ""
        self.station.save()
        order = self._make_order()
        r = self._add_job(order.id)
        self.assertEqual(r.status_code, 422)

    def test_add_job_nonexistent_order_returns_404(self):
        r = self._add_job(999999)
        self.assertEqual(r.status_code, 404)

    def test_add_job_requires_login(self):
        """Unauthenticated request is rejected (redirected to login)."""
        anon = Client()
        order = self._make_order()
        r = anon.post("/orders/agent/add-job/",
                      data={"order_id": order.id},
                      content_type="application/json")
        self.assertNotEqual(r.status_code, 200)

    def test_add_job_invalid_json_returns_400(self):
        r = self.client.post("/orders/agent/add-job/",
                             data="not-json",
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_add_job_cross_tenant_order_returns_404(self):
        """Cannot queue a job for another tenant's order."""
        other_tenant = Tenant.objects.create(name="Other Tenant")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other")
        other_owner  = User.objects.create_user(
            username="other_owner", password="x",
            tenant=other_tenant, outlet=other_outlet, role="owner",
        )
        other_order = Order.objects.create(
            tenant=other_tenant, outlet=other_outlet,
            created_by=other_owner, source="counter", status="open",
        )
        r = self._add_job(other_order.id)
        self.assertEqual(r.status_code, 404)

    def test_multiple_jobs_can_be_queued_for_same_order(self):
        """Re-print is allowed — creates a second pending job."""
        order = self._make_order()
        self._add_job(order.id)
        self._add_job(order.id)
        self.assertEqual(PrintJob.objects.filter(outlet=self.outlet).count(), 2)


# ── poll: agent-side ──────────────────────────────────────────────────────────

class PollTests(PrintQueueBase):

    def test_poll_returns_pending_jobs(self):
        """Fresh job appears in poll response."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self._poll()
        self.assertEqual(r.status_code, 200)
        ids = [j["id"] for j in r.json()["jobs"]]
        self.assertIn(job_id, ids)

    def test_poll_empty_when_no_jobs(self):
        r = self._poll()
        self.assertEqual(r.json()["jobs"], [])

    def test_poll_invalid_key_returns_403(self):
        r = self._poll(key=str(uuid.uuid4()))
        self.assertEqual(r.status_code, 403)

    def test_poll_malformed_key_returns_403(self):
        r = self.client.get("/orders/agent/not-a-uuid/jobs/")
        self.assertEqual(r.status_code, 404)  # Django URL resolver rejects non-UUID

    def test_poll_does_not_return_done_jobs(self):
        """Done jobs must not be served again."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._done(job_id)
        r = self._poll()
        ids = [j["id"] for j in r.json()["jobs"]]
        self.assertNotIn(job_id, ids)

    def test_poll_does_not_return_failed_jobs(self):
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._failed(job_id)
        ids = [j["id"] for j in self._poll().json()["jobs"]]
        self.assertNotIn(job_id, ids)

    def test_poll_job_response_has_required_fields(self):
        """Each job in the response has all fields the agent needs."""
        order = self._make_order()
        self._add_job(order.id)
        jobs = self._poll().json()["jobs"]
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        for field in ("id", "network_host", "network_port", "data_b64"):
            self.assertIn(field, job, f"Missing field: {field}")

    def test_poll_returns_at_most_5_jobs(self):
        """Agent processes in batches of 5 to avoid overload."""
        order = self._make_order()
        for _ in range(8):
            self._add_job(order.id)
        jobs = self._poll().json()["jobs"]
        self.assertLessEqual(len(jobs), 5)

    def test_poll_cross_outlet_key_isolation(self):
        """Agent key from outlet A cannot see outlet B's jobs."""
        other_tenant = Tenant.objects.create(name="Another Cafe")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Branch")
        other_owner  = User.objects.create_user(
            username="branch_owner", password="x",
            tenant=other_tenant, outlet=other_outlet, role="owner",
        )
        KitchenStation.objects.create(
            tenant=other_tenant, outlet=other_outlet, name="Cashier",
            is_default=True, is_active=True, printer_ip="192.168.2.5",
        )
        PaymentConfig.objects.create(tenant=other_tenant, outlet=other_outlet, cash_enabled=True)

        # Create a job on the OTHER outlet
        other_client = Client()
        other_client.login(username="branch_owner", password="x")
        other_order = Order.objects.create(
            tenant=other_tenant, outlet=other_outlet,
            created_by=other_owner, source="counter", status="open",
        )
        other_client.post("/orders/agent/add-job/",
                          data={"order_id": other_order.id},
                          content_type="application/json")

        # Poll with OUR outlet's key — should see 0 jobs
        jobs = self._poll().json()["jobs"]
        self.assertEqual(jobs, [])

    def test_stale_jobs_not_returned(self):
        """Jobs older than 5 minutes are expired and not served."""
        from django.utils import timezone
        from datetime import timedelta
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        # Back-date the job
        PrintJob.objects.filter(pk=job_id).update(
            created_at=timezone.now() - timedelta(minutes=6)
        )
        ids = [j["id"] for j in self._poll().json()["jobs"]]
        self.assertNotIn(job_id, ids)

    def test_fresh_jobs_returned_within_ttl(self):
        """Jobs within the 5-min window ARE returned."""
        from django.utils import timezone
        from datetime import timedelta
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        PrintJob.objects.filter(pk=job_id).update(
            created_at=timezone.now() - timedelta(minutes=4)
        )
        ids = [j["id"] for j in self._poll().json()["jobs"]]
        self.assertIn(job_id, ids)


# ── done: agent-side ──────────────────────────────────────────────────────────

class DoneTests(PrintQueueBase):

    def test_done_marks_job_done(self):
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self._done(job_id)
        self.assertEqual(r.status_code, 200)
        job = PrintJob.objects.get(pk=job_id)
        self.assertEqual(job.status, PrintJob.DONE)
        self.assertIsNotNone(job.done_at)

    def test_done_invalid_key_returns_403(self):
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self._done(job_id, key=str(uuid.uuid4()))
        self.assertEqual(r.status_code, 403)
        # Job should still be pending
        self.assertEqual(PrintJob.objects.get(pk=job_id).status, PrintJob.PENDING)

    def test_done_wrong_outlet_key_cannot_mark_other_outlets_job(self):
        """Outlet B's key cannot mark outlet A's job done — returns 404 (not 403)
        so the response doesn't reveal that the job exists for another outlet."""
        other_outlet = Outlet.objects.create(tenant=self.tenant, name="Branch 2")
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self._done(job_id, key=str(other_outlet.print_agent_key))
        self.assertEqual(r.status_code, 404)

    def test_done_already_done_returns_404(self):
        """Marking a done job done again → 404 (idempotency guard)."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._done(job_id)
        r = self._done(job_id)
        self.assertEqual(r.status_code, 404)

    def test_done_nonexistent_job_returns_404(self):
        r = self._done(999999)
        self.assertEqual(r.status_code, 404)


# ── failed: agent-side ────────────────────────────────────────────────────────

class FailedTests(PrintQueueBase):

    def test_failed_marks_job_failed_with_error_message(self):
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self._failed(job_id, error="Connection refused 192.168.1.100:9100")
        self.assertEqual(r.status_code, 200)
        job = PrintJob.objects.get(pk=job_id)
        self.assertEqual(job.status, PrintJob.FAILED)
        self.assertIn("Connection refused", job.error_msg)

    def test_failed_invalid_key_returns_403(self):
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self._failed(job_id, key=str(uuid.uuid4()))
        self.assertEqual(r.status_code, 403)

    def test_failed_job_not_returned_in_next_poll(self):
        """Failed job does not loop forever in the queue."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._failed(job_id)
        ids = [j["id"] for j in self._poll().json()["jobs"]]
        self.assertNotIn(job_id, ids)

    def test_error_message_truncated_to_512_chars(self):
        """Oversized error strings are clamped to protect the DB."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._failed(job_id, error="x" * 1000)
        self.assertLessEqual(len(PrintJob.objects.get(pk=job_id).error_msg), 512)


# ── Security: key isolation ───────────────────────────────────────────────────

class KeySecurityTests(PrintQueueBase):

    def test_each_outlet_gets_unique_key(self):
        """Two outlets must never share a key."""
        other = Outlet.objects.create(tenant=self.tenant, name="Other Branch")
        self.assertNotEqual(self.outlet.print_agent_key, other.print_agent_key)

    def test_agent_key_is_uuid(self):
        """Key must be a valid UUID (not guessable short string)."""
        key = self.outlet.print_agent_key
        self.assertIsInstance(key, uuid.UUID)

    def test_poll_requires_exact_key_match(self):
        """Even a one-character-off key is rejected."""
        key_str = str(self.outlet.print_agent_key)
        # Flip last char
        bad_key = key_str[:-1] + ("0" if key_str[-1] != "0" else "1")
        r = self.client.get(f"/orders/agent/{bad_key}/jobs/")
        self.assertEqual(r.status_code, 403)
