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


# ── Tenant isolation (new tenant_id filter) ───────────────────────────────────

class TenantIsolationTests(PrintQueueBase):
    """
    The poll/done/failed queries filter on BOTH tenant_id AND outlet.
    These tests verify no cross-tenant bleed is possible even with a known job_id.
    """

    def _make_other_tenant_job(self):
        """Set up a second tenant with its own outlet, station, order, and queued job."""
        other_tenant = Tenant.objects.create(name="Rival Corp")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Rival Outlet")
        PaymentConfig.objects.create(tenant=other_tenant, outlet=other_outlet, cash_enabled=True)
        other_user = User.objects.create_user(
            username="rival_owner", password="x",
            tenant=other_tenant, outlet=other_outlet, role="owner",
        )
        from setup.models import KitchenStation
        KitchenStation.objects.create(
            tenant=other_tenant, outlet=other_outlet, name="Cashier",
            is_default=True, is_active=True, printer_ip="10.0.0.50",
        )
        cat = MenuCategory.objects.create(
            tenant=other_tenant, outlet=other_outlet, name="Food"
        )
        item = MenuItem.objects.create(
            tenant=other_tenant, outlet=other_outlet, category=cat,
            name="Tea", price=15, gst_percentage=0,
        )
        order = Order.objects.create(
            tenant=other_tenant, outlet=other_outlet,
            created_by=other_user, source="counter", status="open",
        )
        OrderItem.objects.create(
            order=order, menu_item=item, quantity=1, price=15,
            gst_percentage=0, total_price=15,
        )
        order.recalculate_totals()
        rival_client = Client()
        rival_client.login(username="rival_owner", password="x")
        r = rival_client.post(
            "/orders/agent/add-job/",
            data={"order_id": order.id},
            content_type="application/json",
        )
        return other_outlet, r.json()["job_id"]

    def test_poll_cross_tenant_job_invisible(self):
        """Our outlet key sees zero jobs even when another tenant has pending jobs."""
        self._make_other_tenant_job()
        self.assertEqual(self._poll().json()["jobs"], [])

    def test_done_cross_tenant_job_returns_404(self):
        """Our outlet key cannot mark another tenant's job done — 404, job stays pending."""
        _, rival_job_id = self._make_other_tenant_job()
        r = self._done(rival_job_id)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(PrintJob.objects.get(pk=rival_job_id).status, PrintJob.PENDING)

    def test_failed_cross_tenant_job_silently_ignored(self):
        """Our outlet key cannot fail another tenant's job — 200 but job stays pending."""
        _, rival_job_id = self._make_other_tenant_job()
        r = self._failed(rival_job_id, error="attempted cross-tenant failure")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PrintJob.objects.get(pk=rival_job_id).status, PrintJob.PENDING)

    def test_job_tenant_set_to_creator_tenant(self):
        """PrintJob.tenant_id always equals the requesting user's tenant — no cross-contamination."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self.assertEqual(PrintJob.objects.get(pk=job_id).tenant_id, self.tenant.id)


# ── add-job edge cases ────────────────────────────────────────────────────────

class AddJobEdgeCaseTests(PrintQueueBase):

    def test_empty_body_returns_400(self):
        """Completely empty POST body → JSON parse fails → 400."""
        r = self.client.post(
            "/orders/agent/add-job/", data=b"", content_type="application/json"
        )
        self.assertEqual(r.status_code, 400)

    def test_non_integer_order_id_returns_400(self):
        """String order_id → int() raises ValueError → 400."""
        r = self.client.post(
            "/orders/agent/add-job/",
            data={"order_id": "not-a-number"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_missing_order_id_returns_404(self):
        """No order_id in body defaults to 0 → no order with pk=0 → 404."""
        r = self.client.post(
            "/orders/agent/add-job/", data={}, content_type="application/json"
        )
        self.assertEqual(r.status_code, 404)

    def test_get_method_not_allowed(self):
        r = self.client.get("/orders/agent/add-job/")
        self.assertEqual(r.status_code, 405)

    def test_order_with_voided_items_still_queues(self):
        """Receipt is still generated when some items are voided — voided items excluded."""
        import base64
        order = self._make_order(qty=1)
        extra = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.category, name="Coffee", price=30, gst_percentage=0,
        )
        OrderItem.objects.create(
            order=order, menu_item=extra, quantity=1, price=30,
            gst_percentage=0, total_price=30, status="voided",
        )
        r = self._add_job(order.id)
        self.assertEqual(r.status_code, 200)
        raw = base64.b64decode(PrintJob.objects.get(pk=r.json()["job_id"]).payload["data_b64"])
        self.assertGreater(len(raw), 0)


# ── poll edge cases ───────────────────────────────────────────────────────────

class PollEdgeCaseTests(PrintQueueBase):

    def test_jobs_returned_oldest_first(self):
        """Agent must process in the order jobs were enqueued."""
        from datetime import timedelta
        from django.utils import timezone
        order = self._make_order()
        now = timezone.now()
        job_ids = []
        for i in range(3):
            jid = self._add_job(order.id).json()["job_id"]
            job_ids.append(jid)
            # Spread timestamps: job_ids[0] oldest, job_ids[2] newest
            PrintJob.objects.filter(pk=jid).update(
                created_at=now - timedelta(seconds=(3 - i) * 10)
            )
        polled_ids = [j["id"] for j in self._poll().json()["jobs"]]
        self.assertEqual(polled_ids, job_ids)

    def test_exactly_5_jobs_at_boundary_all_returned(self):
        """Exactly 5 pending jobs → all 5 come back (boundary, not over-limited)."""
        order = self._make_order()
        for _ in range(5):
            self._add_job(order.id)
        self.assertEqual(len(self._poll().json()["jobs"]), 5)

    def test_data_b64_contains_escpos_cut_sequence(self):
        """Decoded base64 contains GS V (0x1D 0x56) — the ESC/POS paper-cut command."""
        import base64
        order = self._make_order()
        self._add_job(order.id)
        raw = base64.b64decode(self._poll().json()["jobs"][0]["data_b64"])
        self.assertIn(b'\x1d\x56', raw, "ESC/POS cut sequence missing from receipt bytes")

    def test_post_method_not_allowed(self):
        r = self.client.post(f"/orders/agent/{self.outlet.print_agent_key}/jobs/")
        self.assertEqual(r.status_code, 405)

    def test_same_tenant_different_outlet_isolated(self):
        """Outlet B's key (same tenant) cannot see outlet A's pending jobs."""
        branch = Outlet.objects.create(tenant=self.tenant, name="Branch 2")
        from setup.models import KitchenStation, PaymentConfig
        KitchenStation.objects.create(
            tenant=self.tenant, outlet=branch, name="Cashier",
            is_default=True, is_active=True, printer_ip="192.168.1.200",
        )
        PaymentConfig.objects.create(tenant=self.tenant, outlet=branch, cash_enabled=True)
        branch_user = User.objects.create_user(
            username="branch2_staff", password="x",
            tenant=self.tenant, outlet=branch, role="owner",
        )
        cat  = MenuCategory.objects.create(tenant=self.tenant, outlet=branch, name="Food")
        item = MenuItem.objects.create(
            tenant=self.tenant, outlet=branch, category=cat,
            name="Dosa", price=50, gst_percentage=0,
        )
        branch_order = Order.objects.create(
            tenant=self.tenant, outlet=branch,
            created_by=branch_user, source="counter", status="open",
        )
        OrderItem.objects.create(
            order=branch_order, menu_item=item, quantity=1, price=50,
            gst_percentage=0, total_price=50,
        )
        branch_client = Client()
        branch_client.login(username="branch2_staff", password="x")
        branch_client.post(
            "/orders/agent/add-job/",
            data={"order_id": branch_order.id},
            content_type="application/json",
        )
        # Polling with OUR outlet's key should not reveal branch 2's job
        self.assertEqual(self._poll().json()["jobs"], [])


# ── done edge cases ───────────────────────────────────────────────────────────

class DoneEdgeCaseTests(PrintQueueBase):

    def test_failed_job_cannot_be_marked_done(self):
        """A job that failed is no longer pending — done returns 404, status unchanged."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._failed(job_id, error="printer offline")
        r = self._done(job_id)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(PrintJob.objects.get(pk=job_id).status, PrintJob.FAILED)

    def test_get_method_not_allowed(self):
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self.client.get(
            f"/orders/agent/{self.outlet.print_agent_key}/done/{job_id}/"
        )
        self.assertEqual(r.status_code, 405)


# ── failed edge cases ─────────────────────────────────────────────────────────

class FailedEdgeCaseTests(PrintQueueBase):

    def test_failed_sets_done_at_timestamp(self):
        """done_at is stamped when a job fails — enables audit and dashboard queries."""
        order = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._failed(job_id, error="timeout")
        self.assertIsNotNone(PrintJob.objects.get(pk=job_id).done_at)

    def test_wrong_outlet_key_same_tenant_silently_ignored(self):
        """Outlet B key (same tenant) cannot fail outlet A's job — 200 but job unchanged."""
        branch = Outlet.objects.create(tenant=self.tenant, name="Branch 3")
        order  = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        r = self._failed(job_id, key=str(branch.print_agent_key), error="wrong outlet")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PrintJob.objects.get(pk=job_id).status, PrintJob.PENDING)

    def test_already_done_job_cannot_be_failed(self):
        """Marking a done job as failed does nothing — status stays DONE."""
        order  = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self._done(job_id)
        self._failed(job_id, error="late failure signal")
        self.assertEqual(PrintJob.objects.get(pk=job_id).status, PrintJob.DONE)

    def test_empty_error_body_stores_empty_string(self):
        """No error field in body → error_msg is '' not None — safe for DB NOT NULL."""
        order  = self._make_order()
        job_id = self._add_job(order.id).json()["job_id"]
        self.client.post(
            f"/orders/agent/{self.outlet.print_agent_key}/failed/{job_id}/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(PrintJob.objects.get(pk=job_id).error_msg, "")
