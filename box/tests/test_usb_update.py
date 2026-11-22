"""
Box health's "Update from a pen drive" (22 Nov 2026).

The web app writes a request; on the box, `rasova-box usb-request` (run by a
systemd path unit) reads the pen drive, checks each bundle's signature and
installs with `rasova-box update`. These tests run that script for real
with bash (on Linux: CI and the box; skipped where bash, openssl or
sha256sum are missing, and on Windows), against folders standing in for the
pen drive (RASOVA_USB_ROOTS, developer mode only) and a test signing key
(RASOVA_UPDATE_KEY_PEM, developer mode only).

Run: python manage.py test box.tests.test_usb_update
"""
import json
import os
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import skipIf

from django.conf import settings
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import User
from box import usb_update
from box.tests.test_box import BoxBase
from core.testing import not_in_compiled_build

SCRIPT = Path(settings.BASE_DIR) / "box" / "host" / "rasova-box"


def _tools_missing():
    if os.name == "nt":
        return "the box script runs on Linux (CI runs it)"
    for tool in ("bash", "openssl", "sha256sum"):
        if shutil.which(tool) is None:
            return f"{tool} isn't installed"
    probe = subprocess.run(["openssl", "pkeyutl", "-help"], capture_output=True, text=True)
    if "-rawin" not in probe.stdout + probe.stderr:
        return "this openssl can't check Ed25519 signatures"
    return None


def make_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


def make_bundle(folder, version, private_pem, damage=False):
    from box.updates import sha256_of, sign_manifest
    folder.mkdir(parents=True)
    (folder / "images.tar.gz").write_bytes(b"image bytes " + version.encode())
    (folder / "compose.yml").write_text("services: {}\n")
    files = {n: sha256_of(folder / n) for n in ("images.tar.gz", "compose.yml")}
    manifest = json.dumps({"version": version, "files": files}).encode()
    (folder / "manifest.json").write_bytes(manifest)
    (folder / "manifest.sig").write_text(sign_manifest(manifest, private_pem))
    if damage:
        (folder / "images.tar.gz").write_bytes(b"changed on the way")
    return folder


class UsbStatusTest(BoxBase):
    """The web app's half: requests out, status in, strictly."""

    def test_a_request_is_a_few_plain_lines_moved_in_whole(self):
        request_id = usb_update.ask("install", "1.4.2")
        files = list(usb_update.requests_dir().iterdir())
        self.assertEqual([f.name for f in files], [f"{request_id}.req"])
        self.assertEqual(files[0].read_text(), f"id={request_id}\naction=install\nversion=1.4.2\n")
        self.assertFalse(list(usb_update.data_dir().glob(".update-request-*")), "no half-written request left over")

    def test_only_scan_or_install_of_a_version_shaped_version(self):
        for action, version in (("rm", ""), ("install", "1.4; rm -rf /"), ("install", ""), ("install", "../x")):
            with self.assertRaises(ValueError):
                usb_update.ask(action, version)

    def test_the_status_lets_through_only_what_it_knows(self):
        usb_update.status_file().write_text(
            "id=abc\nstate=found\nrunning=1.4.1\nfound=1.4.2\nfound=1.4.1\nfound=<script>\nfound=1.4.2\n"
            "message=<b>hi</b>\nsecret=1\n")
        status = usb_update.read_status()
        self.assertEqual(status["found"], ["1.4.2", "1.4.1"])
        self.assertEqual(status["newer"], ["1.4.2"])
        self.assertNotIn("secret", status)
        usb_update.status_file().write_text("id=abc\nstate=rebooting\n")
        self.assertEqual(usb_update.read_status(), {}, "an unknown state shows nothing")

    def test_a_request_nobody_answers_says_so_after_a_minute(self):
        usb_update.ask("scan")
        self.assertEqual(usb_update.read_status()["state"], "asked")
        request_id = (usb_update.data_dir() / "update-asked").read_text().split("\n")[0]
        then = (timezone.now() - timedelta(seconds=90)).isoformat()
        (usb_update.data_dir() / "update-asked").write_text(f"{request_id}\n{then}\n")
        status = usb_update.read_status()
        self.assertEqual(status["state"], "failed")
        self.assertIn("enable-usb-updates", status["message"])


class UpdatePanelTest(BoxBase):
    def client_on(self, user):
        c = Client(HTTP_HOST="192.168.1.50")
        c.force_login(user)
        return c

    def test_the_owner_asks_the_box_to_look_at_the_pen_drive(self):
        _, _, owner = self.restaurant()
        c = self.client_on(owner)
        self.assertContains(c.get("/box/health/"), 'id="lookForUpdate"')
        c.post("/box/health/", {"action": "usb_scan"})
        self.assertEqual(len(list(usb_update.requests_dir().iterdir())), 1)
        page = c.get("/box/health/")
        self.assertContains(page, "Asking the box")
        self.assertContains(page, 'data-working="1"')
        self.assertNotContains(page, 'id="lookForUpdate"', msg_prefix="no second press while it works")

    def test_a_manager_sees_it_but_cannot_start_it(self):
        tenant, outlet, _ = self.restaurant()
        manager = User.objects.create_user(username="usb_mgr", password="pw", role="manager", tenant=tenant, outlet=outlet)
        c = self.client_on(manager)
        self.assertNotContains(c.get("/box/health/"), 'id="lookForUpdate"')
        c.post("/box/health/", {"action": "usb_scan"})
        self.assertFalse(usb_update.requests_dir().exists() and list(usb_update.requests_dir().iterdir()))

    def test_only_a_version_the_pen_drive_was_found_to_hold_can_be_installed(self):
        _, _, owner = self.restaurant()
        usb_update.status_file().write_text("id=x1\nstate=found\nrunning=1.4.1\nfound=1.4.2\nmessage=\n")
        (usb_update.data_dir() / "update-asked").write_text(f"x1\n{timezone.now().isoformat()}\n")
        c = self.client_on(owner)
        page = c.get("/box/health/")
        self.assertContains(page, 'id="install-1.4.2"')
        c.post("/box/health/", {"action": "usb_install", "version": "9.9.9"})
        self.assertFalse(usb_update.requests_dir().exists() and list(usb_update.requests_dir().iterdir()))
        c.post("/box/health/", {"action": "usb_install", "version": "1.4.2"})
        request = next(usb_update.requests_dir().iterdir()).read_text()
        self.assertIn("action=install\nversion=1.4.2\n", request)

    def test_what_the_helper_says_is_shown_as_text(self):
        _, _, owner = self.restaurant()
        usb_update.status_file().write_text("id=x2\nstate=failed\nmessage=<img src=x onerror=alert(1)>\n")
        (usb_update.data_dir() / "update-asked").write_text(f"x2\n{timezone.now().isoformat()}\n")
        page = self.client_on(owner).get("/box/health/").content.decode()
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)

    def test_the_status_address_for_the_page_to_follow(self):
        _, _, owner = self.restaurant()
        usb_update.status_file().write_text("id=x3\nstate=installing\nrunning=1.4.1\nmessage=Installing\n")
        (usb_update.data_dir() / "update-asked").write_text(f"x3\n{timezone.now().isoformat()}\n")
        answer = self.client_on(owner).get("/box/update-status/").json()
        self.assertEqual((answer["state"], answer["working"]), ("installing", True))
        with override_settings(BOX=False):
            c = Client()
            c.force_login(owner)
            self.assertEqual(c.get("/box/update-status/").status_code, 404)


@skipIf(_tools_missing(), _tools_missing() or "")
@not_in_compiled_build("box/host, which only Rasova's PC has")
class UsbRequestScriptTest(BoxBase):
    """box/host/rasova-box usb-request, run for real."""

    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "data").mkdir()
        self.usb = self.home / "usb"
        self.usb.mkdir()
        self.private, self.public = make_key()
        self.box_settings = override_settings(BOX_LICENSE_FILE=str(self.home / "data" / "license.json"))
        self.box_settings.enable()
        self.addCleanup(self.box_settings.disable)

    def run_script(self, roots=None):
        env = {**os.environ, "RASOVA_DEV": "1", "RASOVA_HOME": str(self.home),
               "RASOVA_UPDATE_KEY_PEM": self.public}
        env.pop("RASOVA_USB_ROOTS", None)
        if roots is not None:
            env["RASOVA_USB_ROOTS"] = roots
        done = subprocess.run(["bash", str(SCRIPT), "usb-request"], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        return usb_update.read_status()

    def test_the_signed_bundle_on_the_pen_drive_is_found(self):
        (self.home / ".env").write_text("RASOVA_VERSION=1.4.1\n")
        make_bundle(self.usb / "rasova-box-1.4.2", "1.4.2", self.private)
        usb_update.ask("scan")
        status = self.run_script(str(self.usb))
        self.assertEqual((status["state"], status["found"], status["running"], status["newer"]),
                         ("found", ["1.4.2"], "1.4.1", ["1.4.2"]))
        self.assertEqual(list(usb_update.requests_dir().iterdir()), [], "the request is answered and gone")

    def test_unsigned_and_damaged_bundles_are_never_offered(self):
        other_private, _ = make_key()
        make_bundle(self.usb / "good", "1.4.2", self.private)
        make_bundle(self.usb / "not-rasova", "6.6.6", other_private)
        make_bundle(self.usb / "damaged", "1.4.3", self.private, damage=True)
        usb_update.ask("scan")
        self.assertEqual(self.run_script(str(self.usb))["found"], ["1.4.2"])

    def test_no_update_and_no_pen_drive_are_told_apart(self):
        usb_update.ask("scan")
        self.assertIn("no update from Rasova", self.run_script(str(self.usb))["message"])
        usb_update.ask("scan")
        self.assertIn("No pen drive found", self.run_script()["message"])

    def test_install_hands_the_bundle_to_rasova_box_update(self):
        # Not installed here, so update refuses at once: proof the found
        # bundle went to the same cmd_update, and its reason is shown.
        make_bundle(self.usb / "rasova-box-1.4.2", "1.4.2", self.private)
        usb_update.ask("install", "1.4.2")
        status = self.run_script(str(self.usb))
        self.assertEqual(status["state"], "failed")
        self.assertIn("isn't installed yet", status["message"])

    def test_a_version_not_on_the_pen_drive_is_not_installed(self):
        make_bundle(self.usb / "rasova-box-1.4.2", "1.4.2", self.private)
        usb_update.ask("install", "1.4.9")
        status = self.run_script(str(self.usb))
        self.assertEqual(status["state"], "failed")
        self.assertIn("isn't on the pen drive", status["message"])

    def test_an_odd_request_is_ignored(self):
        folder = usb_update.requests_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for body in ("id=abcdefgh1\naction=rm -rf /\n", "id=x;reboot\naction=scan\n",
                     "id=abcdefgh2\naction=install\nversion=$(reboot)\n", "garbage"):
            (folder / "odd.req").write_text(body)
            (usb_update.data_dir() / "update-asked").unlink(missing_ok=True)
            self.run_script(str(self.usb))
            text = usb_update.status_file().read_text()
            self.assertIn("state=failed", text, body)
            self.assertIn("odd request", text, body)
