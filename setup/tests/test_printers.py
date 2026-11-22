"""
Setup > Printers: every printer of an outlet on one page (22 Nov 2026).

What it fixes, each proved here and failing on the old code:
  * saving Outlet Settings wiped the printer's MAC, reset the PC agent to
    localhost, a 58 mm printer to 80 mm and turned QZ Tray off, every time
    (the form had none of those fields, the save wrote them anyway);
  * a printer address typo ("192.168.1.") reached the database, whose inet
    column refused it with a 500; a port of 0 was saved and never printed;
  * the Kitchen Stations Test Print needed a PC agent on the same device,
    so on a box or a phone it could not work: each printer's test page now
    goes through the print queue, like a real bill;
  * KOT big letters, totals on the KOT and the bill's margins were only in
    the Django admin;
  * nothing said which printer last failed, or why.

Run: python manage.py test setup.tests.test_printers
"""
import json
from datetime import timedelta

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from core.tests.test_tenant_walk import Restaurant
from printing.models import PrintJob
from setup.models import KitchenStation
from setup.services import printers as svc
from tenants.models import Outlet, PrintProfile


class PrintersWorld(TestCase):
    def setUp(self):
        self.r = Restaurant("Alpha", "pr-alpha")
        self.counter = self.r.station                           # the default station: the counter printer
        KitchenStation.objects.filter(pk=self.counter.pk).update(printer_ip="192.168.1.50")
        self.counter.refresh_from_db()
        self.bar = KitchenStation.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Bar",
                                                 printer_ip="192.168.1.61")
        self.owner = self.r.owner

    def client_as(self, user):
        c = Client()
        c.force_login(user)
        return c

    def save(self, station, user=None, **fields):
        body = {"printer_ip": station.printer_ip or "", "printer_port": "9100", "paper_width_mm": "80",
                "cut_type": "full", "printer_encoding": "cp437", **fields}
        return self.client_as(user or self.owner).post(f"/setup/printers/{station.id}/save/", data=json.dumps(body),
                                                       content_type="application/json")

    def job(self, station, status, minutes_ago, error="", port=True):
        payload = {"data_b64": "", "network_host": station.printer_ip}
        if port:
            payload["network_port"] = station.printer_port
        job = PrintJob.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, payload=payload)
        PrintJob.objects.filter(pk=job.pk).update(status=status, error_msg=error,
                                                  done_at=timezone.now() - timedelta(minutes=minutes_ago))
        return job


class OutletSettingsKeepsThePrinterTest(PrintersWorld):
    def test_saving_outlet_settings_leaves_every_printer_setting_alone(self):
        Outlet.objects.filter(pk=self.r.outlet.pk).update(
            printer_mac="00:57:27:10:ED:46", agent_host="192.168.1.20", use_qz_tray=True, paper_width_mm=58)
        self.client_as(self.owner).post("/setup/outlet/", {"outlet_name": "Alpha Outlet", "gst_inclusive": "false"})
        outlet = Outlet.objects.get(pk=self.r.outlet.pk)
        self.assertEqual((outlet.printer_mac, outlet.agent_host, outlet.use_qz_tray, outlet.paper_width_mm),
                         ("00:57:27:10:ED:46", "192.168.1.20", True, 58))

    def test_outlet_settings_points_at_printers(self):
        self.assertContains(self.client_as(self.owner).get("/setup/outlet/"), 'href="/setup/printers/"')


class PrintersPageTest(PrintersWorld):
    def test_every_printer_is_on_the_page_counter_first(self):
        page = self.client_as(self.owner).get("/setup/printers/")
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertLess(body.index(f'id="printer-{self.counter.id}"'), body.index(f'id="printer-{self.bar.id}"'))
        self.assertIn("192.168.1.61", body)

    def test_a_manager_may_a_cashier_may_not(self):
        manager = User.objects.create_user(username="pr-mgr", password="pw", role="manager",
                                           tenant=self.r.tenant, outlet=self.r.outlet)
        cashier = User.objects.create_user(username="pr-cash", password="pw", role="cashier",
                                           tenant=self.r.tenant, outlet=self.r.outlet)
        self.assertEqual(self.client_as(manager).get("/setup/printers/").status_code, 200)
        self.assertNotEqual(self.client_as(cashier).get("/setup/printers/").status_code, 200)
        self.assertNotEqual(self.save(self.bar, user=cashier, printer_ip="192.168.1.99").status_code, 200)
        self.bar.refresh_from_db()
        self.assertEqual(self.bar.printer_ip, "192.168.1.61")

    def test_the_old_printer_page_moved_here_and_its_test_still_works(self):
        r = self.client_as(self.owner).get("/setup/printer/")
        self.assertEqual((r.status_code, r["Location"]), (301, "/setup/printers/"))
        answer = self.client_as(self.owner).post("/setup/printer/test/", content_type="application/json").json()
        job = PrintJob.objects.get(pk=answer["job_id"])
        self.assertEqual(job.payload["network_host"], "192.168.1.50")

    def test_kitchen_stations_shows_each_printer_as_a_line_and_links_here(self):
        page = self.client_as(self.owner).get("/setup/kitchen-stations/").content.decode()
        self.assertIn(f'href="/setup/printers/#printer-{self.bar.id}"', page)
        self.assertNotIn(f'id="ip-{self.bar.id}"', page, "the printer's address is edited on Printers only")

    def test_setup_has_a_printers_card_saying_how_they_are(self):
        self.job(self.bar, PrintJob.FAILED, 5, error="Connection refused")
        page = self.client_as(self.owner).get("/setup/").content.decode()
        self.assertIn('id="printersCard"', page)
        self.assertIn("Bar didn&#x27;t print at", page)


class SavePrinterTest(PrintersWorld):
    def test_a_printer_is_saved(self):
        r = self.save(self.bar, printer_ip="192.168.1.62", printer_port="9101", paper_width_mm="58",
                      cut_type="partial", printer_encoding="cp850")
        self.assertEqual(r.status_code, 200, r.content)
        self.bar.refresh_from_db()
        self.assertEqual((self.bar.printer_ip, self.bar.printer_port, self.bar.paper_width_mm, self.bar.cut_type,
                          self.bar.printer_encoding), ("192.168.1.62", 9101, 58, "partial", "cp850"))

    def test_a_typo_in_the_address_is_a_sentence_not_a_500(self):
        for bad in ("192.168.1.", "192.168.1.300", "printer", "192.168.1.50; drop"):
            r = self.save(self.bar, printer_ip=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("Feed button", r.json()["error"])
        self.bar.refresh_from_db()
        self.assertEqual(self.bar.printer_ip, "192.168.1.61")

    def test_a_port_that_can_never_print_is_refused(self):
        for bad in ("0", "65536", "abc", "-1"):
            self.assertEqual(self.save(self.bar, printer_port=bad).status_code, 400, bad)
        for bad in ({"paper_width_mm": "70"}, {"cut_type": "half"}, {"printer_encoding": "latin1"}):
            self.assertEqual(self.save(self.bar, **bad).status_code, 400, bad)

    def test_a_blank_address_takes_the_printer_away(self):
        self.assertEqual(self.save(self.bar, printer_ip="").status_code, 200)
        self.bar.refresh_from_db()
        self.assertIsNone(self.bar.printer_ip)

    def test_the_counter_printers_paper_and_mac_are_the_outlets(self):
        self.save(self.counter, paper_width_mm="58", printer_mac="00 57 27 10 ed 46")
        outlet = Outlet.objects.get(pk=self.r.outlet.pk)
        self.assertEqual((outlet.paper_width_mm, outlet.printer_mac), (58, "00:57:27:10:ED:46"))
        self.assertEqual(self.save(self.counter, printer_mac="00:57:27").status_code, 400)

    def test_a_kitchen_printer_never_touches_the_outlets_mac_or_paper(self):
        Outlet.objects.filter(pk=self.r.outlet.pk).update(printer_mac="00:57:27:10:ED:46", paper_width_mm=80)
        self.save(self.bar, paper_width_mm="58", printer_mac="AA:BB:CC:DD:EE:FF")
        outlet = Outlet.objects.get(pk=self.r.outlet.pk)
        self.assertEqual((outlet.paper_width_mm, outlet.printer_mac), (80, "00:57:27:10:ED:46"))

    def test_a_field_the_form_does_not_have_is_left_as_it_was(self):
        KitchenStation.objects.filter(pk=self.bar.pk).update(printer_name="BillTouch ZY306")
        self.save(self.bar)                                     # a box's row has no USB printer name
        self.bar.refresh_from_db()
        self.assertEqual(self.bar.printer_name, "BillTouch ZY306")

    def test_another_restaurants_printer_is_a_404(self):
        other = Restaurant("Beta", "pr-beta")
        r = self.save(other.station, printer_ip="10.0.0.9")
        self.assertEqual(r.status_code, 404)
        other.station.refresh_from_db()
        self.assertIsNone(other.station.printer_ip)


class TestPrintTest(PrintersWorld):
    def test_each_printer_tests_itself_through_the_queue(self):
        answer = self.client_as(self.owner).post(f"/setup/printers/{self.bar.id}/test/").json()
        job = PrintJob.objects.get(pk=answer["job_id"])
        self.assertEqual((job.payload["network_host"], job.payload["network_port"], job.status),
                         ("192.168.1.61", 9100, PrintJob.PENDING))
        self.assertEqual(job.outlet_id, self.r.outlet.id)

    def test_no_address_no_test(self):
        KitchenStation.objects.filter(pk=self.bar.pk).update(printer_ip=None)
        before = PrintJob.objects.count()
        r = self.client_as(self.owner).post(f"/setup/printers/{self.bar.id}/test/")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(PrintJob.objects.count(), before)

    def test_the_test_page_names_its_printer_and_cuts_as_set(self):
        self.bar.cut_type = "partial"
        page = svc.test_page(self.bar)
        self.assertIn(b"Bar\n", page)
        self.assertTrue(page.endswith(b"\x1dV\x01"))
        self.bar.cut_type = "none"
        self.assertFalse(svc.test_page(self.bar).endswith(b"\x1dV\x00"))
        self.bar.name = "Bar \u2014 upstairs"                    # a dash the code page can't print
        self.assertNotIn("\u2014".encode("utf-8"), svc.test_page(self.bar))


class PrinterStateTest(PrintersWorld):
    def states(self):
        return svc.printer_states(self.r.outlet, svc.outlet_stations(self.r.outlet))

    def test_ok_bad_idle_and_none(self):
        third = KitchenStation.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Grill")
        self.job(self.counter, PrintJob.DONE, 3)
        self.job(self.bar, PrintJob.DONE, 30)
        self.job(self.bar, PrintJob.FAILED, 2, error="Connection refused")
        states = self.states()
        self.assertEqual(states[self.counter.id]["level"], "ok")
        self.assertEqual(states[self.counter.id]["words"], "Printed 3 min ago")
        self.assertEqual(states[self.bar.id]["level"], "bad")
        self.assertIn("Connection refused", states[self.bar.id]["words"])
        self.assertEqual(states[third.id]["level"], "none")
        KitchenStation.objects.filter(pk=third.pk).update(printer_ip="192.168.1.70")
        self.assertEqual(self.states()[third.id]["level"], "idle")

    def test_a_print_after_the_failure_means_it_works_again(self):
        self.job(self.bar, PrintJob.FAILED, 10, error="Out of paper")
        self.job(self.bar, PrintJob.DONE, 1)
        self.assertEqual(self.states()[self.bar.id]["level"], "ok")

    def test_an_old_job_without_a_port_is_the_9100_printer(self):
        self.job(self.bar, PrintJob.FAILED, 1, error="Timed out", port=False)
        self.assertEqual(self.states()[self.bar.id]["level"], "bad")

    def test_another_outlets_jobs_say_nothing_about_these_printers(self):
        other = Outlet.objects.create(tenant=self.r.tenant, name="Second")
        job = PrintJob.objects.create(tenant=self.r.tenant, outlet=other, payload={"network_host": "192.168.1.61"})
        PrintJob.objects.filter(pk=job.pk).update(status=PrintJob.FAILED, done_at=timezone.now(), error_msg="x")
        self.assertEqual(self.states()[self.bar.id]["level"], "idle")

    def test_the_same_queries_for_one_printer_or_ten(self):
        def count():
            stations = svc.outlet_stations(self.r.outlet)
            with CaptureQueriesContext(connection) as seen:
                svc.printer_states(self.r.outlet, stations)
            return len(seen)
        few = count()
        for n in range(8):
            s = KitchenStation.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name=f"S{n}",
                                              printer_ip=f"192.168.1.{100 + n}")
            self.job(s, PrintJob.DONE, n)
        self.assertEqual(count(), few)


class EveryPrinterTest(PrintersWorld):
    def post(self, **form):
        return self.client_as(self.owner).post("/setup/printers/", {"bill_inner_margin": "4", **form})

    def test_kot_letters_totals_and_margin_are_saved_for_this_outlet(self):
        self.post(kot_show_total="on", bill_inner_margin="2")             # big letters off
        outlet = Outlet.objects.get(pk=self.r.outlet.pk)
        profile = outlet.print_profile
        self.assertEqual((profile.kot_large_font, profile.kot_show_total, profile.bill_inner_margin), (False, True, 2))

    def test_a_profile_shared_with_another_outlet_is_copied_not_changed(self):
        shared = PrintProfile.objects.create(tenant=self.r.tenant, name="House", kot_large_font=True)
        other = Outlet.objects.create(tenant=self.r.tenant, name="Second", print_profile=shared)
        Outlet.objects.filter(pk=self.r.outlet.pk).update(print_profile=shared)
        self.post()                                                       # big letters off here
        shared.refresh_from_db()
        self.assertTrue(shared.kot_large_font, "the other outlet's KOTs must not change")
        mine = Outlet.objects.get(pk=self.r.outlet.pk).print_profile
        self.assertNotEqual(mine.pk, shared.pk)
        self.assertFalse(mine.kot_large_font)
        self.assertEqual(Outlet.objects.get(pk=other.pk).print_profile_id, shared.pk)

    def test_a_margin_not_on_the_list_is_refused(self):
        self.post(bill_inner_margin="40")
        self.assertIsNone(Outlet.objects.get(pk=self.r.outlet.pk).print_profile)

    def test_the_pc_agent_and_qz_tray_are_set_here_in_the_cloud(self):
        self.post(agent_host="192.168.1.20", use_qz_tray="on")
        outlet = Outlet.objects.get(pk=self.r.outlet.pk)
        self.assertEqual((outlet.agent_host, outlet.use_qz_tray), ("192.168.1.20", True))
        self.post(agent_host="two words")
        self.assertEqual(Outlet.objects.get(pk=self.r.outlet.pk).agent_host, "192.168.1.20")


class BoxPrintersPageTest(PrintersWorld):
    def test_a_box_hides_what_only_the_cloud_has(self):
        with override_settings(BOX=True, ALLOWED_HOSTS=["*"]):
            from box.models import BoxState
            BoxState.objects.update_or_create(pk=1, defaults={"setup_done_at": timezone.now()})
            page = self.client_as(self.owner).get("/setup/printers/").content.decode()
        self.assertIn("This box sends every bill and KOT to the printers itself", page)
        for cloud_only in ('name="printer_name"', 'name="agent_host"', 'name="use_qz_tray"', "rasova.apk",
                           'data-action="printer.find"'):
            self.assertNotIn(cloud_only, page, cloud_only)
        self.assertIn('href="/box/connect/"', page)

    def test_a_box_never_touches_the_pc_agent_settings(self):
        Outlet.objects.filter(pk=self.r.outlet.pk).update(agent_host="192.168.1.20", use_qz_tray=True)
        with override_settings(BOX=True, ALLOWED_HOSTS=["*"]):
            from box.models import BoxState
            BoxState.objects.update_or_create(pk=1, defaults={"setup_done_at": timezone.now()})
            self.client_as(self.owner).post("/setup/printers/", {"bill_inner_margin": "4", "agent_host": "x"})
        outlet = Outlet.objects.get(pk=self.r.outlet.pk)
        self.assertEqual((outlet.agent_host, outlet.use_qz_tray), ("192.168.1.20", True))
