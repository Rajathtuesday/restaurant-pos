"""
Setup > Printers and the box's Connect a device, in a real browser, at a
phone's width (390 px) and a tablet's (768 px): nothing scrolls sideways,
every button is a 44 px target, Save and Test print work from the page, and
the QR codes are drawn by Rasova itself (a box has no internet).

RASOVA_SHOTS=<folder> also saves screenshots, light and dark, for a person
to look at. Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test setup.tests.test_printers_browser
"""
import os
import threading
import time

from django.db import connection
from django.test import override_settings
from django.utils import timezone

from box.models import BoxState
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from printing.models import PrintJob
from setup.models import KitchenStation

WIDTHS = ({"width": 390, "height": 844, "mobile": True}, {"width": 768, "height": 1024, "mobile": True})

FITS = """
const wide = document.documentElement.scrollWidth > window.innerWidth + 1;
const small = [...document.querySelectorAll(".pr-btn, .btn-box, .pr-more summary")]
  .filter((b) => b.offsetParent !== null && b.getBoundingClientRect().height < 44)
  .map((b) => b.textContent.trim());
return { wide, small };
"""


class PrintersBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "prb-alpha")
        KitchenStation.objects.filter(pk=self.r.station.pk).update(printer_ip="192.168.1.50")
        self.bar = KitchenStation.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Bar",
                                                 printer_ip="192.168.1.61")

    def shoot(self, url, name, user, viewport, dark=False):
        folder = os.environ.get("RASOVA_SHOTS")
        if not folder:
            return
        size = {k: v for k, v in viewport.items() if k != "mobile"}
        context =self.browser.new_context(viewport=size, is_mobile=True, has_touch=True)
        try:
            from django.conf import settings
            from django.test import Client
            client = Client()
            client.force_login(user)
            context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                  "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                                  "url": self.live_server_url}])
            context.add_init_script(f'try {{ localStorage.setItem("dark", "{str(dark).lower()}"); }} catch (e) {{}}')
            page = context.new_page()
            page.goto(self.live_server_url + url, wait_until="load")
            page.wait_for_timeout(400)
            page.screenshot(path=os.path.join(folder, f"{name}_{size['width']}_{'dark' if dark else 'light'}.png"),
                            full_page=True)
        finally:
            context.close()
            self.wait_for_the_server()

    def test_the_printers_page_fits_a_phone_and_a_tablet(self):
        for viewport in WIDTHS:
            out = self.run_page("/setup/printers/", [("fits", FITS)], user=self.r.owner, viewport=viewport)
            self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
            self.assertEqual(out["results"]["fits"], {"wide": False, "small": []}, viewport)
            for dark in (False, True):
                self.shoot("/setup/printers/", "printers", self.r.owner, viewport, dark)

    def test_save_from_the_page_and_the_state_line_follows(self):
        row = f"#printer-{self.bar.id}"
        out = self.run_page("/setup/printers/", [
            ("bad", f'''
                document.querySelector("{row} [name=printer_ip]").value = "192.168.1.";
                t.click("{row} button[type=submit]");
                return await t.until(() => document.querySelector("{row} .pr-result.is-bad")
                                           && t.text("{row} .pr-result"));'''),
            ("good", f'''
                document.querySelector("{row} [name=printer_ip]").value = "192.168.1.62";
                t.click("{row} button[type=submit]");
                return await t.until(() => document.querySelector("{row} .pr-result.is-ok")
                                           && t.text("{row} .pr-result"));'''),
        ], user=self.r.owner)
        # The refused save reports itself as a console error (the 400); that is the point of the step.
        self.assertIn("Feed button", out["results"]["bad"])
        self.assertIn("Saved Bar", out["results"]["good"])
        self.bar.refresh_from_db()
        self.assertEqual(self.bar.printer_ip, "192.168.1.62")

    def test_each_row_tests_its_own_printer(self):
        def agent():
            try:
                for _ in range(200):
                    job = PrintJob.objects.filter(outlet=self.r.outlet, status=PrintJob.PENDING,
                                                  payload__network_host="192.168.1.61").first()
                    if job:
                        time.sleep(0.3)
                        PrintJob.objects.filter(pk=job.pk).update(status=PrintJob.DONE, done_at=timezone.now())
                        return
                    time.sleep(0.05)
            finally:
                connection.close()
        thread = threading.Thread(target=agent)
        thread.start()
        row = f"#printer-{self.bar.id}"
        out = self.run_page("/setup/printers/", [
            ("result", f'''
                t.click("{row} [data-action='printer.test']");
                return await t.until(() => /is-(ok|bad)/.test(document.querySelector("{row} .pr-result").className)
                                           && t.text("{row} .pr-result"));'''),
        ], user=self.r.owner)
        thread.join()
        self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
        self.assertEqual(out["results"]["result"], "Printed. The printer took the test page.")


@override_settings(BOX=True, ALLOWED_HOSTS=["*"])
class ConnectBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "cnb-alpha")
        BoxState.objects.update_or_create(pk=1, defaults={"setup_done_at": timezone.now(),
                                                          "lan_address": "192.168.1.50"})

    def test_connect_a_device_fits_and_draws_its_code_without_the_internet(self):
        drawn = """
        await t.until(() => document.querySelector(".cn-qr canvas, .cn-qr.qr-failed"));
        return { canvas: !!document.querySelector(".cn-qr canvas"),
                 outside: performance.getEntriesByType("resource").map((e) => new URL(e.name).host)
                   .filter((h) => h !== location.host) };
        """
        for viewport in WIDTHS:
            out = self.run_page("/box/connect/", [("fits", FITS), ("qr", drawn)], user=self.r.owner, viewport=viewport)
            self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
            self.assertEqual(out["results"]["fits"], {"wide": False, "small": []}, viewport)
            self.assertEqual(out["results"]["qr"], {"canvas": True, "outside": []})
            for dark in (False, True):
                self.shoot_connect(viewport, dark)

    def shoot_connect(self, viewport, dark):
        PrintersBrowserTest.shoot(self, "/box/connect/", "connect", self.r.owner, viewport, dark)
