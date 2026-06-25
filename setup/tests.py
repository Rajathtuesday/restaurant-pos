from django.db import connection
from django.test import TestCase
from django.urls import reverse

from tenants.models import Tenant, Outlet
from accounts.models import User
from setup.models import PaymentConfig, KitchenStation


def _make_tenant(name):
    t = Tenant.objects.create(name=name)
    o = Outlet.objects.create(tenant=t, name=f"{name} Outlet")
    return t, o


def _make_user(tenant, outlet, role="owner", username=None):
    username = username or f"setup_{role}_{tenant.id}"
    return User.objects.create_user(
        username=username,
        password="testpass",
        role=role,
        tenant=tenant,
        outlet=outlet
    )


class SetupWizardAccessTest(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("Setup Restaurant")
        self.owner = _make_user(self.tenant, self.outlet, role="owner", username="setup_owner")
        self.cashier = _make_user(self.tenant, self.outlet, role="cashier", username="setup_cashier")

    def test_owner_can_access_setup_wizard(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("setup_wizard"))
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_redirected_from_setup(self):
        response = self.client.get(reverse("setup_wizard"))
        self.assertIn(response.status_code, [301, 302])


class ChecklistStatusTest(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("Checklist Cafe")
        self.owner = _make_user(self.tenant, self.outlet, role="owner", username="chk_owner")

    def test_checklist_returns_json_with_steps_key(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("setup_checklist"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("steps", data)

    def test_checklist_returns_all_done_key(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("setup_checklist"))
        data = response.json()
        self.assertIn("all_done", data)

    def test_checklist_returns_done_count_key(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("setup_checklist"))
        data = response.json()
        self.assertIn("done_count", data)

    def test_checklist_steps_have_expected_keys(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("setup_checklist"))
        steps = response.json()["steps"]
        self.assertTrue(len(steps) > 0)
        first = steps[0]
        self.assertIn("key", first)
        self.assertIn("done", first)
        self.assertIn("url", first)

    def test_unauthenticated_redirected_from_checklist(self):
        response = self.client.get(reverse("setup_checklist"))
        self.assertIn(response.status_code, [301, 302])


class PaymentConfigModelTest(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("PayConf Restaurant")

    def test_payment_config_created_with_defaults(self):
        config = PaymentConfig.objects.create(
            tenant=self.tenant,
            outlet=self.outlet
        )
        self.assertTrue(config.cash_enabled)
        self.assertTrue(config.upi_enabled)
        self.assertFalse(config.card_enabled)

    def test_payment_config_enabled_methods_cash_and_upi(self):
        config = PaymentConfig.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            cash_enabled=True,
            upi_enabled=True,
            card_enabled=False
        )
        methods = config.enabled_methods()
        keys = [m["key"] for m in methods]
        self.assertIn("cash", keys)
        self.assertIn("upi", keys)
        self.assertNotIn("card", keys)

    def test_payment_config_card_enabled(self):
        config = PaymentConfig.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            cash_enabled=True,
            upi_enabled=False,
            card_enabled=True
        )
        methods = config.enabled_methods()
        keys = [m["key"] for m in methods]
        self.assertIn("card", keys)
        self.assertNotIn("upi", keys)

    def test_one_config_per_outlet(self):
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet)
        with self.assertRaises(Exception):
            PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet)


class KitchenStationModelTest(TestCase):

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("Station Restaurant")

    def test_station_created_with_defaults(self):
        station = KitchenStation.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Hot Kitchen"
        )
        self.assertTrue(station.is_active)
        self.assertFalse(station.is_default)
        self.assertEqual(station.paper_width_mm, 80)

    def test_chars_per_line_80mm(self):
        station = KitchenStation.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Main Station",
            paper_width_mm=80
        )
        self.assertEqual(station.chars_per_line, 48)

    def test_chars_per_line_58mm(self):
        station = KitchenStation.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Small Station",
            paper_width_mm=58
        )
        self.assertEqual(station.chars_per_line, 32)


class RazorpaySecretEncryptionTest(TestCase):
    """razorpay_key_secret / razorpay_webhook_secret must never sit in the
    database as plaintext — only razorpay_key_id (a public identifier, not
    a secret) is allowed to stay readable in a raw row."""

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("Encryption Test Cafe")
        self.config = PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            razorpay_key_id="rzp_test_publicid123",
            razorpay_key_secret="sk_live_supersecret_value",
            razorpay_webhook_secret="whsec_supersecret_value",
        )

    def _raw_row(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT razorpay_key_id, razorpay_key_secret, razorpay_webhook_secret "
                "FROM setup_paymentconfig WHERE id = %s",
                [self.config.id],
            )
            return cur.fetchone()

    def test_secrets_are_not_plaintext_in_the_raw_database_row(self):
        raw_key_id, raw_key_secret, raw_webhook_secret = self._raw_row()
        self.assertEqual(raw_key_id, "rzp_test_publicid123")
        self.assertNotEqual(raw_key_secret, "sk_live_supersecret_value")
        self.assertNotEqual(raw_webhook_secret, "whsec_supersecret_value")

    def test_orm_read_transparently_decrypts_back_to_the_original_value(self):
        self.config.refresh_from_db()
        self.assertEqual(self.config.razorpay_key_secret, "sk_live_supersecret_value")
        self.assertEqual(self.config.razorpay_webhook_secret, "whsec_supersecret_value")

    def test_blank_secret_is_not_encrypted_or_corrupted(self):
        self.config.razorpay_key_secret = ""
        self.config.save()
        self.config.refresh_from_db()
        self.assertEqual(self.config.razorpay_key_secret, "")
