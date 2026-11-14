"""
Setup, Kitchen Stations in a real browser (static/js/setup/stations.js,
14 Nov 2026).

Why: the old inline script asked with the browser's confirm() before
switching to One Printer mode and before removing a station. confirm()
never shows in the Android app (Playwright, like the app, answers it with
"no"), so neither could be done there. Both ask in Rasova's own sheet now.
The first two tests fail on the old page; selectors are ones both share.
Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test setup.tests.test_kitchen_stations_browser
"""
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from setup.models import KitchenStation

URL = "/setup/kitchen-stations/"
SHEET = 'return !!(await t.until(() => document.querySelector(".swal2-confirm")));'


class KitchenStationsBrowserTest(BrowserTestCase):

    def setUp(self):
        self.restaurant = Restaurant("Alpha", "ks-alpha")
        r = self.restaurant
        KitchenStation.objects.filter(pk=r.station.pk).update(is_default=True, printer_ip="192.168.1.50")
        self.bar = KitchenStation.objects.create(tenant=r.tenant, outlet=r.outlet, name="Bar",
                                                 printer_ip="192.168.1.60")

    def _run(self, steps):
        out = self.run_page(URL, steps, user=self.restaurant.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]

    def test_one_printer_mode_asks_in_rasovas_sheet(self):
        self._run([
            ("sheet", 't.click("#modeCashier");' + SHEET),
            ("switched", 't.click(".swal2-confirm");'
                         'return await t.until(() => t.text("#printModeHint").includes("active"));'),
        ])
        self.bar.refresh_from_db()
        self.assertIsNone(self.bar.printer_ip)

    def test_remove_asks_in_rasovas_sheet(self):
        self._run([
            ("open", f't.click("#card-{self.bar.id} .station-header"); return true;'),
            ("sheet", f't.click("#card-{self.bar.id} .btn-delete-station");' + SHEET),
            ("removed", 't.click(".swal2-confirm"); return true;', {"selector": f"body:not(:has(#card-{self.bar.id}))"}),
        ])
        self.assertFalse(KitchenStation.objects.filter(pk=self.bar.pk).exists())

    def test_a_station_opens_and_saves_its_printer(self):
        self._run([
            ("open", f't.click("#card-{self.bar.id} .station-header");'
                     f'return document.getElementById("panel-{self.bar.id}").classList.contains("open");'),
            ("saved", f'document.getElementById("ip-{self.bar.id}").value = "192.168.1.61";'
                      f't.click("#card-{self.bar.id} .btn-save");'
                      f'return await t.until(() => t.text("#status-{self.bar.id}").includes("Saved")'
                      f' || t.text("#status-{self.bar.id}").includes("saved"));'),
        ])
        self.bar.refresh_from_db()
        self.assertEqual(self.bar.printer_ip, "192.168.1.61")

    def test_set_default_does_not_open_the_station(self):
        results = self._run([
            ("default", f't.click("#card-{self.bar.id} .btn-ghost[type=submit]"); return true;',
             {"selector": f"#card-{self.bar.id}.is-default"}),
        ])
        self.assertIn("default", results)
        self.bar.refresh_from_db()
        self.assertTrue(self.bar.is_default)
