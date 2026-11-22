"""
Phones, tablets and the counter PC on a Rasova box (22 Nov 2026).

  * Every page told the Android app the box was at https://<its address>
    and handed it the outlet's print agent key. A box has no https, and it
    prints its own queue, so a phone there must not start a print service.
  * Connect a device: the box's address, a QR code that pairs a phone, the
    iPhone steps and a shortcut that opens Rasova in its own window on the
    counter PC. The box learns its number address from the owner's own
    visit, because many Android phones can't find rasova.local.
  * An iPhone's Add to Home Screen needs a PNG icon (it ignores SVG) and the
    full-screen tags, on every page and the login.

Run: python manage.py test box.tests.test_box_devices
"""
import json
import re
import struct
from pathlib import Path

from django.conf import settings
from django.test import Client, override_settings

from accounts.models import User
from box.models import BoxState
from box.tests.test_box import BoxBase
from core.testing import not_in_compiled_build


def page_data(response):
    match = re.search(r'<script id="rasova-data" type="application/json">(.*?)</script>', response.content.decode(), re.S)
    return json.loads(match.group(1))


class PageDataOnABoxTest(BoxBase):
    def test_a_box_says_http_and_hands_no_agent_key(self):
        _, _, owner = self.restaurant()
        c = Client(HTTP_HOST="192.168.1.50")
        c.force_login(owner)
        data = page_data(c.get("/dashboard/", follow=True))
        self.assertEqual(data["server_url"], "http://192.168.1.50")
        self.assertNotIn("agent_key", data, "the box prints by itself: a phone must not start polling it")

    def test_the_cloud_still_says_https_and_hands_the_key(self):
        _, outlet, owner = self.restaurant()
        with override_settings(BOX=False):
            c = Client(HTTP_HOST="copper-tap.rasova.net")
            c.force_login(owner)
            data = page_data(c.get("/dashboard/", follow=True))
        self.assertEqual(data["server_url"], "https://copper-tap.rasova.net")
        self.assertEqual(data["agent_key"], str(outlet.print_agent_key))


class ConnectPageTest(BoxBase):
    def open(self, user, host="192.168.1.50"):
        c = Client(HTTP_HOST=host)
        c.force_login(user)
        return c.get("/box/connect/")

    def test_the_address_and_the_pairing_code(self):
        _, _, owner = self.restaurant()
        page = self.open(owner)
        self.assertContains(page, 'id="boxAddress">http://192.168.1.50<')
        self.assertContains(page, 'data-qr="http://192.168.1.50/login/"')
        self.assertContains(page, 'id="iphoneSteps"')
        self.assertContains(page, 'href="/box/connect/Rasova.cmd"')
        self.assertEqual(BoxState.get().lan_address, "192.168.1.50")

    def test_opened_by_name_it_pairs_by_the_number_it_learned(self):
        _, _, owner = self.restaurant()
        self.open(owner, host="192.168.1.50:8089")
        page = self.open(owner, host="rasova.local")
        self.assertContains(page, 'data-qr="http://192.168.1.50:8089/login/"')
        self.assertContains(page, 'id="byName"')
        self.assertEqual(BoxState.get().lan_address, "192.168.1.50:8089", "a name never replaces the number")

    def test_opened_on_the_box_itself_it_says_so(self):
        _, _, owner = self.restaurant()
        page = self.open(owner, host="localhost")
        self.assertContains(page, 'id="onTheBox"')
        self.assertEqual(BoxState.get().lan_address, "", "localhost is not an address a phone can use")

    def test_a_public_address_is_never_learned(self):
        _, _, owner = self.restaurant()
        self.open(owner, host="8.8.8.8")
        self.assertEqual(BoxState.get().lan_address, "")

    def test_owner_and_manager_only(self):
        tenant, outlet, owner = self.restaurant()
        cashier = User.objects.create_user(username="tap_cash", password="pw", role="cashier", tenant=tenant, outlet=outlet)
        self.assertNotEqual(self.open(cashier).status_code, 200)
        manager = User.objects.create_user(username="tap_mgr2", password="pw", role="manager", tenant=tenant, outlet=outlet)
        self.assertEqual(self.open(manager).status_code, 200)

    def test_not_in_the_cloud(self):
        _, _, owner = self.restaurant()
        with override_settings(BOX=False):
            self.assertEqual(self.open(owner, host="copper-tap.rasova.net").status_code, 404)

    def test_box_health_leads_here_and_to_printers(self):
        _, _, owner = self.restaurant()
        c = Client(HTTP_HOST="192.168.1.50")
        c.force_login(owner)
        page = c.get("/box/health/")
        self.assertContains(page, 'href="/box/connect/"')
        self.assertContains(page, 'href="/setup/printers/"')
        self.assertContains(page, 'id="printersPanel"')


class ShortcutTest(BoxBase):
    def test_a_windows_shortcut_that_opens_rasova_in_its_own_window(self):
        _, _, owner = self.restaurant()
        c = Client(HTTP_HOST="192.168.1.50")
        c.force_login(owner)
        r = c.get("/box/connect/Rasova.cmd")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Disposition"], 'attachment; filename="Rasova.cmd"')
        body = r.content.decode("ascii")
        self.assertIn('set "RASOVA=http://192.168.1.50/login/"', body)
        self.assertIn('start "" msedge --app="%RASOVA%"', body)
        self.assertNotIn("\n", body.replace("\r\n", ""), "a .cmd file needs Windows line ends")

    def test_not_in_the_cloud(self):
        _, _, owner = self.restaurant()
        with override_settings(BOX=False):
            c = Client()
            c.force_login(owner)
            self.assertEqual(c.get("/box/connect/Rasova.cmd").status_code, 404)


class HomeScreenIconTest(BoxBase):
    def test_every_page_and_the_login_give_an_iphone_a_png_icon_and_full_screen(self):
        _, _, owner = self.restaurant()
        c = Client(HTTP_HOST="192.168.1.50")
        login = c.get("/login/")
        c.force_login(owner)
        for page in (login, c.get("/dashboard/", follow=True)):
            self.assertContains(page, 'rel="apple-touch-icon" sizes="180x180" href="/static/icon-180.png"')
            self.assertContains(page, '<meta name="apple-mobile-web-app-capable" content="yes">')

    def test_the_icons_are_real_pngs_of_their_size(self):
        root = Path(settings.BASE_DIR) / "static"
        for size in (180, 192, 512):
            data = (root / f"icon-{size}.png").read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))
        manifest = json.loads((Path(settings.BASE_DIR) / "templates" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn({"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                      manifest["icons"])


class AppFromTheBoxTest(BoxBase):
    def test_a_phone_installs_the_app_from_the_box_itself(self):
        # No internet on a box: the app comes from the box, over the Wi-Fi.
        self.restaurant()
        r = Client(HTTP_HOST="192.168.1.50").get("/android/rasova.apk")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content)[:2], b"PK", "an APK is a zip")

    @not_in_compiled_build("box/docker, which only Rasova's PC has")
    def test_the_box_image_carries_the_app(self):
        base = Path(settings.BASE_DIR) / "box" / "docker"
        self.assertIn("!public/android/rasova.apk", (base / "Dockerfile.dockerignore").read_text().splitlines())
        self.assertIn("COPY public/android/rasova.apk /app/public/android/rasova.apk",
                      (base / "Dockerfile").read_text())


class ConnectEdgeCaseTest(BoxBase):
    def open(self, user, host):
        c = Client(HTTP_HOST=host)
        c.force_login(user)
        return c.get("/box/connect/")

    def test_addresses_no_other_device_can_use_are_never_learned(self):
        _, _, owner = self.restaurant()
        for host in ("169.254.10.20", "0.0.0.0", "127.0.0.1:8000", "[::1]", "[fe80::1]"):
            self.open(owner, host)
            self.assertEqual(BoxState.get().lan_address, "", host)
        self.open(owner, "[fd00::50]")                          # a private IPv6 the router gave out
        self.assertEqual(BoxState.get().lan_address, "[fd00::50]")

    def test_by_name_before_any_number_says_to_open_it_by_number_once(self):
        _, _, owner = self.restaurant()
        page = self.open(owner, "rasova.local")
        self.assertContains(page, 'id="numberUnknown"')
        self.assertNotContains(page, 'id="byName"')

    def test_a_new_number_replaces_the_old_one(self):
        _, _, owner = self.restaurant()
        self.open(owner, "192.168.1.50")
        self.open(owner, "192.168.0.20")                        # the router was replaced
        self.assertEqual(BoxState.get().lan_address, "192.168.0.20")
        self.assertContains(self.open(owner, "rasova.local"), 'data-qr="http://192.168.0.20/login/"')

    def test_a_platform_superuser_on_box_health_is_not_a_500(self):
        self.restaurant()
        boss = User.objects.create_superuser(username="box-boss", password="pw", email="")
        c = Client(HTTP_HOST="192.168.1.50")
        c.force_login(boss)
        self.assertNotEqual(c.get("/box/health/").status_code, 500)
