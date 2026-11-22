"""
The printer chip and setup's Test Print, in a real browser (B12).

The chip on the billing screen says whether printing works and what to
do; Test Print says "Printed" only once a print agent finished the job.
A thread stands in for the agent, marking the job done the way
printing.views.print_queue_done does.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test printing.test_printing_browser
"""
import threading
import time
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from accounts.models import User
from core.tests.browser import BrowserTestCase
from printing.models import PrintJob
from setup.services.station_service import get_default_station
from tenants.models import Outlet, Tenant

CHIP = """
const chip = () => document.getElementById("printer-chip");
await t.until(() => !chip().hidden);
return { text: t.text("#printer-chip"), level: [...chip().classList].find((c) => c.startsWith("is-")) };
"""
EXPLAIN = """
t.click("#printer-chip");
return await t.until(() => { const el = document.querySelector(".swal2-toast .swal2-title"); return el && el.textContent; });
"""


class PrinterChipTest(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Chip Cafe", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(username="chip_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)

    def chip(self, url="/billing/", explain=False):
        steps = [("chip", CHIP)] + ([("explain", EXPLAIN)] if explain else [])
        out = self.run_page(url, steps, user=self.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]

    def seen(self, ago):
        Outlet.objects.filter(pk=self.outlet.pk).update(print_agent_seen_at=timezone.now() - ago)

    def test_a_connected_print_app_is_ready(self):
        self.seen(timedelta(seconds=10))
        self.assertEqual(self.chip()["chip"], {"text": "Printer ready", "level": "is-ok"})

    def test_a_print_app_gone_quiet_is_red_and_says_what_to_open(self):
        self.seen(timedelta(minutes=10))
        seen = self.chip(explain=True)
        self.assertEqual(seen["chip"], {"text": "Print app not seen for 10 min", "level": "is-bad"})
        self.assertIn("counter phone", seen["explain"])

    def test_bills_stuck_in_the_queue_are_red_on_the_floor_plan_too(self):
        self.seen(timedelta(seconds=5))
        job = PrintJob.objects.create(tenant=self.tenant, outlet=self.outlet, payload={"text": "x"})
        PrintJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(self.chip("/tables/")["chip"], {"text": "1 waiting to print (6 min)", "level": "is-bad"})

    def test_no_print_app_ever_is_grey(self):
        self.assertEqual(self.chip()["chip"], {"text": "No print app connected", "level": "is-info"})


class TestPrintTest(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Print Cafe", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(username="tp_manager", password="pw", role="manager",
                                                tenant=self.tenant, outlet=self.outlet)
        station = get_default_station(self.manager)
        station.printer_ip = "192.168.1.50"
        station.save(update_fields=["printer_ip"])
        self.station = station

    def agent(self, finish):
        """Stand in for a print agent: wait for the test job, then finish it."""
        def run():
            try:
                for _ in range(200):
                    job = PrintJob.objects.filter(outlet=self.outlet).first()
                    if job:
                        time.sleep(0.5)
                        finish(job)
                        return
                    time.sleep(0.05)
            finally:
                connection.close()
        thread = threading.Thread(target=run)
        thread.start()
        return thread

    def press(self):
        # Setup > Printers: the counter printer's own Test print and result line.
        row, result = f"#printer-{self.station.id}", f"result-{self.station.id}"
        return self.run_page("/setup/printers/", [
            ("result", f"""
                t.click('{row} [data-action="printer.test"]');
                await t.until(() => /is-(ok|bad)/.test(document.getElementById("{result}").className));
                return {{ words: t.text("#{result}"), level: document.getElementById("{result}").className }};
            """),
        ], user=self.manager)

    def test_printed_only_once_the_agent_finished_it(self):
        thread = self.agent(lambda job: PrintJob.objects.filter(pk=job.pk).update(
            status=PrintJob.DONE, done_at=timezone.now()))
        out = self.press()
        thread.join()
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["results"]["result"]["words"], "Printed. The printer took the test page.")
        self.assertIn("is-ok", out["results"]["result"]["level"])

    def test_the_agents_reason_when_the_printer_refused(self):
        thread = self.agent(lambda job: PrintJob.objects.filter(pk=job.pk).update(
            status=PrintJob.FAILED, done_at=timezone.now(), error_msg="Connection refused"))
        out = self.press()
        thread.join()
        self.assertEqual(out["errors"], [])
        self.assertIn("The printer refused it: Connection refused", out["results"]["result"]["words"])
