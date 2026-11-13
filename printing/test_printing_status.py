"""
The printing state behind the printer chip, and the job status setup's
Test Print waits on (B12, 13 Nov 2026).

Why: when printing failed, the cashier found out when no paper came out.
The server did not even know when a print agent last asked for jobs. Now
the agent's poll records it (at most once a minute, so an idle poll stays
cheap), the 8-second notifications answer carries the outlet's printing
state, and Test Print asks for its job until a printer took it.

Run: python manage.py test printing.test_printing_status
"""
import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import User
from printing.models import PrintJob
from printing.services.status import printing_state, record_agent_seen
from tenants.models import Outlet, Tenant


class PrintingWorld(TestCase):

    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.tenant = Tenant.objects.create(name="Print Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.other_outlet = Outlet.objects.create(tenant=self.tenant, name="Second")
        other_tenant = Tenant.objects.create(name="Elsewhere")
        self.stranger_outlet = Outlet.objects.create(tenant=other_tenant, name="Theirs")
        self.manager = User.objects.create_user(username="pr-manager", password="pw", role="manager",
                                                tenant=self.tenant, outlet=self.outlet)
        self.waiter = User.objects.create_user(username="pr-waiter", password="pw", role="waiter",
                                               tenant=self.tenant, outlet=self.outlet)

    def job(self, outlet, minutes_ago=0, status=PrintJob.PENDING, error="", done_minutes_ago=None):
        job = PrintJob.objects.create(tenant=outlet.tenant, outlet=outlet, payload={"text": "x"}, status=status,
                                      error_msg=error)
        fields = {"created_at": self.now - timedelta(minutes=minutes_ago)}
        if done_minutes_ago is not None:
            fields["done_at"] = self.now - timedelta(minutes=done_minutes_ago)
        PrintJob.objects.filter(pk=job.pk).update(**fields)
        return job

    def client_as(self, user):
        client = Client()
        client.force_login(user)
        return client


class AgentSeenTest(PrintingWorld):

    def test_the_first_poll_in_a_minute_is_written_and_the_rest_are_not(self):
        self.assertTrue(record_agent_seen(self.outlet, now=self.now))
        self.assertFalse(record_agent_seen(self.outlet, now=self.now + timedelta(seconds=2)))
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.print_agent_seen_at, self.now)
        cache.clear()                                       # the minute is over
        self.assertTrue(record_agent_seen(self.outlet, now=self.now + timedelta(seconds=61)))

    def test_an_idle_poll_with_the_agent_key_counts_as_seen(self):
        response = Client().get("/orders/agent/jobs/", HTTP_X_AGENT_KEY=str(self.outlet.print_agent_key))
        self.assertEqual(response.status_code, 200)
        self.outlet.refresh_from_db()
        self.assertIsNotNone(self.outlet.print_agent_seen_at)

    def test_a_wrong_key_is_not_seen_anywhere(self):
        Client().get("/orders/agent/jobs/", HTTP_X_AGENT_KEY="00000000-0000-0000-0000-000000000000")
        self.assertFalse(Outlet.objects.exclude(print_agent_seen_at=None).exists())

    def test_one_poll_writes_one_outlet(self):
        Client().get("/orders/agent/jobs/", HTTP_X_AGENT_KEY=str(self.outlet.print_agent_key))
        self.assertEqual(list(Outlet.objects.exclude(print_agent_seen_at=None)), [self.outlet])


class PrintingStateTest(PrintingWorld):

    def test_nothing_set_up(self):
        self.assertEqual(printing_state(self.outlet, now=self.now), {
            "agent_seen_seconds": None, "waiting": 0, "oldest_seconds": None, "failed_recent": 0, "last_error": "",
        })

    def test_the_facts_for_one_outlet_and_no_other(self):
        Outlet.objects.filter(pk=self.outlet.pk).update(print_agent_seen_at=self.now - timedelta(seconds=30))
        self.job(self.outlet, minutes_ago=5)
        self.job(self.outlet, minutes_ago=1, status=PrintJob.PROCESSING)
        self.job(self.outlet, minutes_ago=50, status=PrintJob.DONE, done_minutes_ago=49)
        self.job(self.outlet, minutes_ago=20, status=PrintJob.FAILED, error="printer 192.168.1.50 refused", done_minutes_ago=20)
        self.job(self.outlet, minutes_ago=200, status=PrintJob.FAILED, error="old", done_minutes_ago=180)
        self.job(self.other_outlet, minutes_ago=90)
        self.job(self.stranger_outlet, minutes_ago=90, status=PrintJob.FAILED, error="theirs", done_minutes_ago=1)
        self.assertEqual(printing_state(self.outlet, now=self.now), {
            "agent_seen_seconds": 30, "waiting": 2, "oldest_seconds": 300, "failed_recent": 1,
            "last_error": "printer 192.168.1.50 refused",
        })


class NotificationAnswerTest(PrintingWorld):

    def test_the_eight_second_answer_carries_the_users_own_outlet(self):
        self.job(self.outlet, minutes_ago=3)
        self.job(self.other_outlet, minutes_ago=9)
        answer = self.client_as(self.waiter).get("/api/notifications/").json()
        self.assertEqual(answer["printing"]["waiting"], 1)
        self.assertEqual(answer["printing"]["oldest_seconds"] // 60, 3)


class JobStatusTest(PrintingWorld):

    def url(self, job):
        return f"/printing/job/{job.pk}/"

    def test_a_manager_sees_where_the_test_page_is(self):
        client = self.client_as(self.manager)
        waiting = self.job(self.outlet)
        self.assertEqual(client.get(self.url(waiting)).json()["status"], "pending")
        failed = self.job(self.outlet, status=PrintJob.FAILED, error="no paper", done_minutes_ago=0)
        self.assertEqual(client.get(self.url(failed)).json(), {"status": "failed", "error": "no paper", "age_seconds": 0})

    def test_another_outlets_or_restaurants_job_is_not_found(self):
        client = self.client_as(self.manager)
        self.assertEqual(client.get(self.url(self.job(self.other_outlet))).status_code, 404)
        self.assertEqual(client.get(self.url(self.job(self.stranger_outlet))).status_code, 404)

    def test_a_waiter_is_refused(self):
        self.assertEqual(self.client_as(self.waiter).get(self.url(self.job(self.outlet))).status_code, 403)

    def test_test_print_answers_with_the_job_to_wait_on(self):
        from setup.services.station_service import get_default_station
        station = get_default_station(self.manager)
        station.printer_ip = "192.168.1.50"
        station.save(update_fields=["printer_ip"])
        answer = self.client_as(self.manager).post("/setup/printer/test/", data=json.dumps({}),
                                                   content_type="application/json").json()
        self.assertTrue(PrintJob.objects.filter(pk=answer["job_id"], outlet=self.outlet).exists())
