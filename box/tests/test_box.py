"""
The Rasova box (offline edition): box mode, first-run setup, the licence,
the clock and backups. Box mode is switched on per test with
override_settings(BOX=True); the cloud is untouched (last class).

Run: python manage.py test box
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest import mock, skipUnless

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import User
from box import backup, clock, license
from box.models import BoxState
from box.signals import LicenceEnded
from core.features import has_feature
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from setup.models import KitchenStation, PaymentConfig
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant, TenantFeatureOverride
from core.testing import not_in_compiled_build

FAST = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return key, pub


def signed(key, **payload):
    payload = {"box_id": "abc123", "customer": "Copper Tap", "expires": "2027-01-31", "edition": "box", **payload}
    return {"payload": payload, "signature": base64.b64encode(key.sign(license.canonical(payload))).decode()}


class BoxBase(TestCase):
    def setUp(self):
        cache.clear()
        license._cache["state"] = None
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "machine-id").write_text("test-machine")
        self.settings_override = override_settings(
            BOX=True, BOX_LICENSE_FILE=str(self.tmp / "license.json"), BOX_BACKUP_DIR=str(self.tmp / "backups"),
            BOX_MACHINE_ID_FILE=str(self.tmp / "machine-id"), PASSWORD_HASHERS=FAST,
            ALLOWED_HOSTS=["*"])                     # as on a box: rasova.local or its IP
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(cache.clear)

    def restaurant(self, tenant_type="pub"):
        from tenants.services.restaurant_setup import create_restaurant
        tenant, outlet, owner = create_restaurant(name="Copper Tap", tenant_type=tenant_type, owner_username="tap_owner",
                                                  owner_password="pw-long-enough", gst_no=SAMPLE_GSTIN)
        BoxState.get()
        BoxState.objects.filter(pk=1).update(setup_done_at=timezone.now())
        license._cache["state"] = None
        return tenant, outlet, owner

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c


class BoxModeTest(BoxBase):
    def test_internet_and_guest_features_are_off_even_if_switched_on(self):
        tenant, _, _ = self.restaurant()
        TenantFeatureOverride.objects.create(tenant=tenant, feature="qr_menu", enabled=True)
        tenant.refresh_from_db()
        for feature in ("qr_menu", "waiter_call", "whatsapp_receipts", "razorpay_gateway", "ai_menu_import", "platform_sync"):
            self.assertFalse(has_feature(tenant, feature), feature)
        self.assertTrue(has_feature(tenant, "liquor_vat"))

    def test_guest_and_cloud_pages_are_not_served(self):
        _, _, owner = self.restaurant()
        c = self.client_for(owner)
        for path in ("/menu/2b0c39ac-6b9e-4d6b-9a55-5e9a0c0e2c11/", "/menu/call-waiter/2b0c39ac-6b9e-4d6b-9a55-5e9a0c0e2c11/",
                     "/bill/public/abc/", "/live-demo/", "/superuser/", "/api/razorpay/webhook/", "/bar-pos/", "/sitemap.xml"):
            self.assertEqual(c.get(path).status_code, 404, path)
        self.assertEqual(c.get("/box/health/").status_code, 200)

    def test_the_front_page_goes_to_the_app(self):
        _, _, owner = self.restaurant()
        self.assertRedirects(Client().get("/"), "/dashboard/", fetch_redirect_response=False)
        self.assertRedirects(Client().get("/dashboard/"), "/login/?next=/dashboard/", fetch_redirect_response=False)


class BoxLoginTest(BoxBase):
    def test_signing_in_stays_on_the_address_staff_typed(self):
        # In the cloud, login moves you to your restaurant's subdomain
        # (copper-tap.rasova.net). A box has one restaurant at rasova.local or
        # its IP: a subdomain of that doesn't exist, so the login must stay put.
        self.restaurant()
        for host in ("rasova.local", "192.168.1.20"):
            r = Client(HTTP_HOST=host).post("/login/", {"username": "tap_owner", "password": "pw-long-enough"})
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r["Location"].startswith("/"), r["Location"])

    def test_the_cloud_still_moves_you_to_your_subdomain(self):
        self.restaurant()
        with override_settings(BOX=False, BASE_URL="https://rasova.net"):
            r = Client().post("/login/", {"username": "tap_owner", "password": "pw-long-enough"})
        self.assertTrue(r["Location"].startswith("https://copper-tap.rasova.net/"), r["Location"])


class FirstRunTest(BoxBase):
    form = {"name": "Copper Tap", "tenant_type": "pub", "outlet_name": "Indiranagar", "gst_no": SAMPLE_GSTIN,
            "username": "tap_owner", "password": "pubnight2026", "password_again": "pubnight2026"}

    def test_every_page_goes_to_setup_until_the_restaurant_exists(self):
        for path in ("/dashboard/", "/login/", "/billing/"):
            self.assertRedirects(Client().get(path), "/box/setup/", fetch_redirect_response=False)

    def test_setup_makes_the_restaurant_and_logs_the_owner_in(self):
        c = Client()
        resp = c.post("/box/setup/", self.form)
        self.assertRedirects(resp, "/setup/onboard/", fetch_redirect_response=False)
        tenant = Tenant.objects.get()
        outlet = Outlet.objects.get()
        owner = User.objects.get(username="tap_owner")
        self.assertEqual((tenant.tenant_type, outlet.name, outlet.gst_no, owner.role), ("pub", "Indiranagar", SAMPLE_GSTIN, "owner"))
        self.assertTrue(KitchenStation.objects.filter(outlet=outlet, is_default=True).exists())
        self.assertTrue(PaymentConfig.objects.filter(outlet=outlet).exists())
        self.assertIsNotNone(BoxState.get().setup_done_at)
        self.assertEqual(c.get("/dashboard/").status_code, 302)          # onboarding: no menu yet
        self.assertRedirects(Client().get("/box/setup/"), "/login/", fetch_redirect_response=False)

    def test_setup_explains_and_keeps_what_was_typed(self):
        for change, message in (({"password_again": "different1"}, "The two passwords"),
                                ({"password": "short", "password_again": "short"}, "at least 8"),
                                ({"gst_no": "NOTAGSTIN"}, "GSTIN")):
            resp = Client().post("/box/setup/", {**self.form, **change})
            self.assertContains(resp, message)
            self.assertContains(resp, 'value="Copper Tap"')
        self.assertFalse(Tenant.objects.exists())

    def test_setup_is_only_on_a_box(self):
        with override_settings(BOX=False):
            self.assertEqual(Client().get("/box/setup/").status_code, 404)


class LicenceTest(BoxBase):
    def setUp(self):
        super().setUp()
        self.key, pub = keypair()
        patcher = mock.patch.object(license, "LICENSE_PUBLIC_KEY_B64", pub)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.box_id = license.machine_id()

    def state(self, today, **payload):
        return license.evaluate(signed(self.key, box_id=self.box_id, **payload), today=today)

    def test_the_states_by_date(self):
        self.assertEqual(self.state(date(2027, 1, 31)).status, "valid")
        grace = self.state(date(2027, 2, 10))
        self.assertEqual((grace.status, grace.days_left, grace.blocks_new_orders), ("grace", 4, False))
        expired = self.state(date(2027, 2, 15))
        self.assertEqual((expired.status, expired.blocks_new_orders), ("expired", True))

    def test_an_edited_licence_is_refused(self):
        doc = signed(self.key, box_id=self.box_id)
        doc["payload"]["expires"] = "2099-12-31"
        self.assertEqual(license.evaluate(doc).status, "invalid")
        self.assertEqual(license.evaluate({"nonsense": 1}).status, "invalid")
        other_key, _ = keypair()
        self.assertEqual(license.evaluate(signed(other_key, box_id=self.box_id)).status, "invalid")

    def test_a_licence_for_another_box_does_not_fit(self):
        self.assertEqual(license.evaluate(signed(self.key, box_id="someotherbox")).status, "wrong_box")

    def test_the_trial_before_a_licence(self):
        started = timezone.now() - timedelta(days=3)
        self.assertEqual(license.evaluate(None, setup_done=started).status, "trial")
        over = license.evaluate(None, setup_done=timezone.now() - timedelta(days=20))
        self.assertEqual((over.status, over.blocks_new_orders), ("trial_over", True))

    def test_installing_a_file_checks_it_first(self):
        self.restaurant()
        self.assertEqual(license.install("not json").status, "invalid")
        self.assertFalse(Path(license.license_path()).exists())
        good = json.dumps(signed(self.key, box_id=self.box_id, expires="2099-01-01"))
        self.assertEqual(license.install(good).status, "valid")
        self.assertTrue(Path(license.license_path()).exists())

    def test_a_replacement_box_works_for_two_weeks_on_the_dead_boxs_licence(self):
        tenant, outlet, owner = self.restaurant()
        dead_box = signed(self.key, box_id="0123456789abcdef", expires="2099-01-01")
        Path(license.license_path()).write_text(json.dumps(dead_box))
        state = license.current(refresh=True)
        self.assertEqual(state.status, "moved")
        self.assertFalse(state.blocks_new_orders)
        self.assertEqual(state.days_left, license.GRACE_DAYS)
        self.assertIn("another Rasova box", state.message)
        Order.objects.create(tenant=tenant, outlet=outlet, created_by=owner, status="open", source="takeaway")
        # the grace counts from the first time this box saw it, not from each look
        BoxState.objects.filter(pk=1).update(licence_moved_at=timezone.now() - timedelta(days=license.GRACE_DAYS + 1))
        state = license.current(refresh=True)
        self.assertEqual(state.status, "wrong_box")
        self.assertTrue(state.blocks_new_orders)
        # its own licence ends it
        self.assertEqual(license.install(json.dumps(signed(self.key, box_id=self.box_id, expires="2099-01-01"))).status,
                         "valid")
        self.assertIsNone(BoxState.objects.get(pk=1).licence_moved_at)

    def test_an_ended_licence_gets_no_moved_grace(self):
        self.restaurant()
        old = signed(self.key, box_id="0123456789abcdef", expires="2020-01-01")
        Path(license.license_path()).write_text(json.dumps(old))
        self.assertEqual(license.current(refresh=True).status, "wrong_box")

    def test_backups_carry_the_licence_and_a_restore_takes_it_back(self):
        self.restaurant()
        doc = signed(self.key, box_id="0123456789abcdef", expires="2099-01-01")
        folder = self.tmp / "backups"
        folder.mkdir()
        Path(license.license_path()).write_text(json.dumps(doc))
        backup._carry_licence(folder)
        self.assertEqual(json.loads((folder / backup.LICENCE_COPY).read_text()), doc)
        # the replacement box: no licence of its own yet
        Path(license.license_path()).unlink()
        self.assertTrue(license.adopt(folder / backup.LICENCE_COPY))
        self.assertEqual(license.current(refresh=True).status, "moved")
        # never over a licence the box already has, never an edited one
        self.assertFalse(license.adopt(folder / backup.LICENCE_COPY))
        Path(license.license_path()).unlink()
        doc["payload"]["expires"] = "2199-01-01"
        (folder / backup.LICENCE_COPY).write_text(json.dumps(doc))
        self.assertFalse(license.adopt(folder / backup.LICENCE_COPY))
        self.assertFalse(Path(license.license_path()).exists())

    def test_the_signing_command_makes_a_licence_the_box_accepts(self):
        from cryptography.hazmat.primitives import serialization
        keyfile = self.tmp / "key.pem"
        keyfile.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
        out = self.tmp / "lic.json"
        call_command("sign_box_license", box_id=self.box_id, customer="Copper Tap", expires="2099-01-01",
                     out=str(out), key=str(keyfile))
        self.assertEqual(license.evaluate(json.loads(out.read_text())).status, "valid")
        # "unknown" (a box with no machine id) or a typo would make a licence that fits the wrong boxes
        for bad in ("unknown", self.box_id[:-1], self.box_id.upper()):
            with self.assertRaises(CommandError):
                call_command("sign_box_license", box_id=bad, customer="X", expires="2099-01-01",
                             out=str(out), key=str(keyfile))

    def test_an_ended_licence_stops_new_orders_but_not_open_ones(self):
        tenant, outlet, owner = self.restaurant()
        cat = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
        dish = MenuItem.objects.create(tenant=tenant, outlet=outlet, category=cat, name="Fries", price=D("150"),
                                       gst_percentage=D("5"))
        open_order = Order.objects.create(tenant=tenant, outlet=outlet, created_by=owner, status="open", source="takeaway")
        Path(license.license_path()).write_text(json.dumps(signed(self.key, box_id=self.box_id, expires="2020-01-01")))
        license._cache["state"] = None
        with self.assertRaises(LicenceEnded):
            Order.objects.create(tenant=tenant, outlet=outlet, created_by=owner, status="open", source="takeaway")
        resp = self.client_for(owner).post("/create-order/", data=json.dumps(
            {"source": "takeaway", "cart": [{"id": dish.id, "quantity": 1}]}), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("licence ended", resp.json()["error"])
        resp = self.client_for(owner).post("/create-order/", data=json.dumps(
            {"order_id": open_order.id, "source": "takeaway", "cart": [{"id": dish.id, "quantity": 1}]}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)


class ClockTest(BoxBase):
    def test_going_back_is_flagged_and_cleared(self):
        now = timezone.now()
        clock.observe(now, force=True)
        clock.observe(now - timedelta(hours=3), force=True)
        state = BoxState.get()
        self.assertIn("went back", state.clock_warning)
        self.assertEqual(state.last_seen_at, now)          # never moves back
        clock.clear_warning(now - timedelta(hours=3))
        state.refresh_from_db()
        self.assertEqual(state.clock_warning, "")

    def test_a_small_wobble_or_moving_forward_is_fine(self):
        now = timezone.now()
        clock.observe(now, force=True)
        clock.observe(now - timedelta(minutes=2), force=True)
        clock.observe(now + timedelta(hours=9), force=True)
        self.assertEqual(BoxState.get().clock_warning, "")

    def test_a_jump_of_months_is_flagged(self):
        now = timezone.now()
        clock.observe(now, force=True)
        clock.observe(now + timedelta(days=200), force=True)
        self.assertIn("jumped ahead", BoxState.get().clock_warning)

    def test_the_warning_shows_on_every_page(self):
        _, _, owner = self.restaurant()
        clock.observe(timezone.now(), force=True)
        clock.observe(timezone.now() - timedelta(days=2), force=True)
        cache.delete("box_warnings")
        self.assertContains(self.client_for(owner).get("/box/health/"), "clock went back")


class BackupLogicTest(BoxBase):
    """The parts around pg_dump, with pg_dump itself faked."""

    def fake_run(self, fail_dump=False, unreadable=False):
        def run(cmd, **kw):
            if cmd[0] == "pg_dump":
                if fail_dump:
                    return subprocess.CompletedProcess(cmd, 1, "", "connection refused")
                Path(cmd[cmd.index("-f") + 1]).write_bytes(b"x" * 1000)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "" if unreadable else "123; 0 0 TABLE DATA public orders_order", "")
        return mock.patch("box.backup.subprocess.run", side_effect=run)

    def test_a_good_backup_is_kept_and_recorded(self):
        with self.fake_run():
            path = backup.run_backup()
        self.assertTrue(path.exists())
        self.assertEqual(list(path.parent.glob(".rasova-*")), [])        # no partial file left
        state = BoxState.get()
        self.assertEqual((state.last_backup_ok, state.last_backup_file, state.last_backup_bytes), (True, path.name, 1000))

    def test_only_the_last_14_are_kept(self):
        start = timezone.now() - timedelta(days=20)
        with self.fake_run():
            for day in range(17):
                backup.run_backup(now=start + timedelta(days=day))
        names = [b["name"] for b in backup.list_backups()]
        self.assertEqual(len(names), 14)
        self.assertEqual(names[0], f"rasova-{timezone.localtime(start + timedelta(days=16)):%Y%m%d-%H%M%S}.dump")

    def test_a_failed_or_unreadable_backup_leaves_nothing_and_says_so(self):
        for kw, words in (({"fail_dump": True}, "pg_dump failed"), ({"unreadable": True}, "could not be read back")):
            with self.fake_run(**kw), self.assertRaisesMessage(backup.BackupError, words):
                backup.run_backup()
            self.assertEqual(backup.list_backups(), [])
            state = BoxState.get()
            self.assertEqual(state.last_backup_ok, False)
            self.assertIn(words, state.last_backup_error)

    def test_a_failed_backup_warns_the_owner(self):
        _, _, owner = self.restaurant()
        with self.fake_run(fail_dump=True), self.assertRaises(backup.BackupError):
            backup.run_backup()
        cache.delete("box_warnings")
        self.assertContains(self.client_for(owner).get("/box/health/"), "backup failed")

    def test_no_backup_for_a_day_and_a_half_warns(self):
        _, _, owner = self.restaurant()
        BoxState.objects.filter(pk=1).update(setup_done_at=timezone.now() - timedelta(days=3))
        cache.delete("box_warnings")
        self.assertContains(self.client_for(owner).get("/box/health/"), "No backup in the last day and a half")


@skipUnless(shutil.which("pg_dump") and shutil.which("pg_restore"), "needs pg_dump and pg_restore on PATH")
class RealBackupTest(BoxBase):
    def test_a_real_backup_lists_the_tables(self):
        self.restaurant()
        try:
            path = backup.run_backup()
        except backup.BackupError as e:
            if "server version mismatch" in str(e) or "aborting because of server version" in str(e):
                self.skipTest(str(e))
            raise
        listing = subprocess.run(["pg_restore", "--list", str(path)], capture_output=True, text=True).stdout
        self.assertIn("tenants_tenant", listing)


@skipUnless(shutil.which("pg_dump") and shutil.which("pg_restore") and shutil.which("psql"),
            "needs pg_dump, pg_restore and psql on PATH")
class RealRestoreTest(BoxBase):
    """Restores into a scratch database (the test database itself stays put)."""

    def setUp(self):
        super().setUp()
        env, args, name = backup._pg_env()
        self.env, self.args = env, args
        self.scratch = f"{name}_scratch"
        self.drop_scratch()
        self.addCleanup(self.drop_scratch)
        self.psql("postgres", f'CREATE DATABASE "{self.scratch}"')
        self.psql(self.scratch, "CREATE TABLE leftover (id int)")     # what a rolled-back update leaves
        try:
            self.path = backup.run_backup()
        except backup.BackupError as e:
            self.skipTest(str(e))

    def psql(self, database, statement):
        done = subprocess.run(["psql", "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", *self.args, "-d", database,
                               "-c", statement], env=self.env, capture_output=True, text=True)
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        return done.stdout.strip()

    def drop_scratch(self):
        env, args, _ = backup._pg_env()
        for db in ("", "_restoring", "_before_restore"):
            subprocess.run(["psql", "-X", "-q", *args, "-d", "postgres", "-c",
                            f'DROP DATABASE IF EXISTS "{self.scratch}{db}"'], env=env, capture_output=True)

    def tables(self, database):
        return set(self.psql(database, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").split())

    def test_a_restore_brings_back_the_backup_and_nothing_else(self):
        backup.restore(self.path, database=self.scratch)
        tables = self.tables(self.scratch)
        self.assertIn("tenants_tenant", tables)
        self.assertNotIn("leftover", tables)
        # the database it replaced is kept, as the undo
        self.assertIn("leftover", self.tables(f"{self.scratch}_before_restore"))
        self.assertEqual(self.psql("postgres", f"SELECT count(*) FROM pg_database WHERE datname = '{self.scratch}_restoring'"), "0")

    def test_a_damaged_backup_changes_nothing(self):
        damaged = self.path.with_name("rasova-20990101-000000.dump")
        damaged.write_bytes(self.path.read_bytes()[: len(self.path.read_bytes()) // 2])
        self.addCleanup(damaged.unlink, missing_ok=True)
        with self.assertRaises(backup.BackupError):
            backup.restore(damaged, database=self.scratch)
        self.assertEqual(self.tables(self.scratch), {"leftover"})
        self.assertEqual(self.psql("postgres", f"SELECT count(*) FROM pg_database WHERE datname LIKE '{self.scratch}_%'"), "0")


class UpdateBundleTest(TestCase):
    """rasova-box update refuses any bundle this check refuses, before loading anything."""

    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.key = Ed25519PrivateKey.generate()
        self.pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption())
        self.public = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    def bundle(self, files=None, pem=None):
        from box.updates import sha256_of, sign_manifest
        folder = self.tmp / "bundle"
        folder.mkdir(exist_ok=True)
        (folder / "images.tar.gz").write_bytes(b"image bytes")
        (folder / "compose.yml").write_text("services: {}")
        files = files or {n: sha256_of(folder / n) for n in ("images.tar.gz", "compose.yml")}
        manifest = json.dumps({"version": "1.4.0", "files": files}).encode()
        (folder / "manifest.json").write_bytes(manifest)
        (folder / "manifest.sig").write_text(sign_manifest(manifest, pem or self.pem))
        return folder

    def check(self, folder):
        from box.updates import verify_bundle
        return verify_bundle(folder, public_key_b64=self.public)

    def test_a_good_bundle_passes(self):
        self.assertEqual(self.check(self.bundle())["version"], "1.4.0")

    def test_a_changed_or_damaged_file_is_refused(self):
        from box.updates import BundleError
        folder = self.bundle()
        (folder / "images.tar.gz").write_bytes(b"image bytes, changed")
        with self.assertRaisesMessage(BundleError, "damaged or changed"):
            self.check(folder)

    def test_an_edited_manifest_or_someone_elses_key_is_refused(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from box.updates import BundleError
        folder = self.bundle()
        (folder / "manifest.json").write_bytes((folder / "manifest.json").read_bytes().replace(b"1.4.0", b"9.9.9"))
        with self.assertRaisesMessage(BundleError, "not signed by Rasova"):
            self.check(folder)
        other = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        with self.assertRaisesMessage(BundleError, "not signed by Rasova"):
            self.check(self.bundle(pem=other))

    def test_missing_pieces_and_odd_names_are_refused(self):
        from box.updates import BundleError
        folder = self.bundle()
        (folder / "manifest.sig").unlink()
        with self.assertRaisesMessage(BundleError, "missing its manifest or signature"):
            self.check(folder)
        with self.assertRaisesMessage(BundleError, "missing extra.bin"):
            self.check(self.bundle(files={"extra.bin": "0" * 64}))
        with self.assertRaisesMessage(BundleError, "odd file"):
            self.check(self.bundle(files={"../../etc/passwd": "0" * 64}))

    @not_in_compiled_build("box/docker, which only Rasova's PC has")
    def test_the_bundle_maker_ships_every_image_and_signs_what_the_box_accepts(self):
        from box.updates import verify_bundle
        keyfile = self.tmp / "update.pem"
        keyfile.write_bytes(self.pem)
        saved = []

        def fake_docker(args, **kwargs):
            if args[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["docker", "save"]:
                saved.extend(args[4:])
                Path(args[3]).write_bytes(b"pretend image layers")
                return subprocess.CompletedProcess(args, 0, "", "")
            raise AssertionError(args)

        out = self.tmp / "made"
        with mock.patch("box.updates.UPDATE_PUBLIC_KEY_B64", self.public), \
                mock.patch("devtools.management.commands.make_box_bundle.subprocess.run", side_effect=fake_docker):
            call_command("make_box_bundle", release="1.4.0", out=str(out), key=str(keyfile))
            manifest = verify_bundle(out)
        self.assertEqual(manifest["version"], "1.4.0")
        # the box can't download: the bundle carries Postgres and Redis too, as compose.yml names them
        self.assertEqual(sorted(saved), ["postgres:15-bookworm", "rasova-box:1.4.0", "redis:7-alpine"])
        self.assertFalse((out / "images.tar").exists())
        with self.assertRaises(CommandError):
            call_command("make_box_bundle", release="1.4.0; rm -rf /", out=str(out), key=str(keyfile))

    def test_the_box_trusts_rasovas_update_key(self):
        from box.license import LICENSE_PUBLIC_KEY_B64
        from box.updates import UPDATE_PUBLIC_KEY_B64
        self.assertEqual(len(base64.b64decode(UPDATE_PUBLIC_KEY_B64)), 32)
        self.assertNotEqual(UPDATE_PUBLIC_KEY_B64, LICENSE_PUBLIC_KEY_B64)   # one key leaking doesn't give the other


class HealthPageTest(BoxBase):
    def test_the_owner_sees_the_box_and_a_cashier_cannot_change_it(self):
        tenant, outlet, owner = self.restaurant()
        page = self.client_for(owner).get("/box/health/")
        self.assertContains(page, license.machine_id())
        cashier = User.objects.create_user(username="tap_cashier", password="pw", role="cashier", tenant=tenant, outlet=outlet)
        self.assertNotEqual(self.client_for(cashier).get("/box/health/").status_code, 200)
        manager = User.objects.create_user(username="tap_mgr", password="pw", role="manager", tenant=tenant, outlet=outlet)
        with mock.patch("box.backup.run_backup") as run:
            self.client_for(manager).post("/box/health/", {"action": "backup"})
        run.assert_not_called()


    def test_backups_on_the_same_drive_are_pointed_out(self):
        tenant, outlet, owner = self.restaurant()
        (self.tmp / "backups").mkdir()
        page = self.client_for(owner).get("/box/health/")       # licence and backups both in self.tmp
        self.assertContains(page, 'id="sameDrive"')
        real_stat = os.stat

        def other_drive(path, *a, **k):
            result = real_stat(path, *a, **k)
            if str(path).endswith("backups"):
                return os.stat_result((*result[:2], result.st_dev + 1, *result[3:]))
            return result
        with mock.patch("box.views.os.stat", side_effect=other_drive):
            self.assertNotContains(self.client_for(owner).get("/box/health/"), 'id="sameDrive"')


class BoxMediaTest(BoxBase):
    def test_the_box_serves_its_own_photos_and_the_cloud_does_not(self):
        tenant, outlet, owner = self.restaurant()
        media = self.tmp / "media"
        (media / "menu").mkdir(parents=True)
        (media / "menu" / "fries.png").write_bytes(b"PNG fake")
        with override_settings(MEDIA_ROOT=str(media)):
            page = self.client_for(owner).get("/media/menu/fries.png")
            self.assertEqual(page.status_code, 200)
            self.assertEqual(b"".join(page.streaming_content), b"PNG fake")
            self.assertIn(self.client_for(owner).get("/media/../license.json").status_code, (400, 404))   # refused
            with override_settings(BOX=False):
                self.assertEqual(self.client_for(owner).get("/media/menu/fries.png").status_code, 404)


class BoxStatusCommandTest(BoxBase):
    def test_status_reports_what_the_updater_checks(self):
        from io import StringIO
        tenant, outlet, owner = self.restaurant()
        Order.objects.create(tenant=tenant, outlet=outlet, created_by=owner, status="open", source="takeaway")
        out = StringIO()
        call_command("box_status", stdout=out)
        status = json.loads(out.getvalue())
        self.assertEqual(status["edition"], "box")
        self.assertEqual(status["open_orders"], 1)
        self.assertEqual(status["box_id"], license.machine_id())
        self.assertEqual(status["licence"]["status"], "trial")


class OneSetupAtATimeTest(TransactionTestCase):
    """Two phones pressing Start at the same moment make one restaurant."""

    def test_one_restaurant(self):
        cache.clear()
        with override_settings(BOX=True, PASSWORD_HASHERS=FAST):
            results = []
            barrier = threading.Barrier(2)

            def press(n):
                try:
                    barrier.wait()
                    r = Client().post("/box/setup/", {**FirstRunTest.form, "username": f"owner{n}"})
                    results.append(r.status_code)
                finally:
                    connection.close()

            threads = [threading.Thread(target=press, args=(n,)) for n in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(Tenant.objects.count(), 1)
            self.assertEqual(User.objects.filter(role="owner").count(), 1)
        cache.clear()


class CloudUntouchedTest(TestCase):
    def test_nothing_changes_in_the_cloud(self):
        tenant = Tenant.objects.create(name="Cloud Pub", tenant_type="pub")
        TenantFeatureOverride.objects.create(tenant=tenant, feature="qr_menu", enabled=True)
        self.assertTrue(has_feature(tenant, "qr_menu"))
        self.assertEqual(Client().get("/box/setup/").status_code, 404)
        Order.objects.create(tenant=tenant, outlet=Outlet.objects.create(tenant=tenant, name="M"), status="open",
                             source="takeaway")

    def test_box_settings(self):
        """The box's settings, read in a fresh process the way a box starts."""
        env = {**os.environ, "RASOVA_EDITION": "box", "DEBUG": "False", "ALLOWED_HOSTS": "", "EMAIL_BACKEND": "",
               "AWS_STORAGE_BUCKET_NAME": "", "DJANGO_SETTINGS_MODULE": "core.settings"}
        env.pop("EMAIL_BACKEND")
        code = ("import django,json;django.setup();from django.conf import settings as s;"
                "print(json.dumps([s.BOX,s.ALLOWED_HOSTS,s.SESSION_COOKIE_SECURE,s.CSRF_COOKIE_SECURE,"
                "s.SESSION_COOKIE_DOMAIN,s.SECURE_HSTS_SECONDS,list(s.CELERY_BEAT_SCHEDULE)]))")
        out = subprocess.run([os.sys.executable, "-c", code], env=env, capture_output=True, text=True,
                             cwd=str(Path(__file__).resolve().parents[2]))
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]),
                         [True, ["*"], False, False, None, 0, ["box-nightly-backup"]])
