from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import MenuCategory, MenuItem
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


class OutletSettingsParcelChargeTest(TestCase):
    """
    outlet_settings silently left parcel_charge_amount unchanged on bad
    input with zero trace of why - this confirms that behavior is
    preserved (no 500, no data corruption) and is now logged.
    """

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("Parcel Charge Cafe")
        self.owner = _make_user(self.tenant, self.outlet, role="owner", username="parcel_owner")
        self.client.force_login(self.owner)

    def test_unparseable_parcel_charge_amount_logs_a_warning(self):
        original = self.outlet.parcel_charge_amount
        with self.assertLogs("pos.setup", level="WARNING") as cm:
            response = self.client.post(reverse("outlet_settings"), {
                "outlet_name": self.outlet.name,
                "parcel_charge_amount": "not-a-number",
            })
        self.assertEqual(response.status_code, 302)
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.parcel_charge_amount, original)
        self.assertTrue(any("parcel_charge_amount" in msg for msg in cm.output))


class OnboardingWizardSilentFailureTest(TestCase):
    """
    Steps 2 and 3 of the onboarding wizard always redirect to the next
    step regardless of whether the item/staff account inside them was
    actually created - a bad price or a username collision used to fail
    completely silently, leaving an owner who just finished "setup"
    thinking they'd added something that was never actually saved.
    """

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("Onboarding Cafe")
        self.owner = _make_user(self.tenant, self.outlet, role="owner", username="onboard_owner")
        self.client.force_login(self.owner)

    def test_step2_unparseable_item_price_logs_a_warning_and_creates_nothing(self):
        with self.assertLogs("pos.setup", level="WARNING") as cm:
            response = self.client.post(
                reverse("onboarding_wizard") + "?step=2",
                {"category": "Mains", "item_1_name": "Burger", "item_1_price": "free"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MenuItem.objects.filter(tenant=self.tenant, name="Burger").exists())
        self.assertTrue(any("menu item" in msg for msg in cm.output))


class OnboardingWizardUsernameCollisionTest(TransactionTestCase):
    """
    User.username is globally unique (Django's default AbstractUser field),
    but onboarding step 3 only checks for a collision WITHIN the current
    tenant before calling create_user() - a username already taken by a
    DIFFERENT tenant's staff account raises IntegrityError, previously
    swallowed with zero trace.

    Uses TransactionTestCase (not TestCase) because a caught IntegrityError
    on Postgres poisons the rest of an open transaction - the plain
    TestCase's outer atomic-wrapped-test would make every ORM call AFTER
    the swallowed exception raise TransactionManagementError, which is a
    test-isolation artifact, not something that happens in production
    (views here aren't wrapped in transaction.atomic()/ATOMIC_REQUESTS).
    """

    def setUp(self):
        self.tenant, self.outlet = _make_tenant("Onboarding Collision Cafe")
        self.owner = _make_user(self.tenant, self.outlet, role="owner", username="collision_owner")
        self.client.force_login(self.owner)

    def test_step3_username_collision_across_tenants_logs_a_warning(self):
        other_tenant, other_outlet = _make_tenant("Other Tenant")
        User.objects.create_user(
            username="taken_name", password="pw", tenant=other_tenant, outlet=other_outlet,
            role="cashier",
        )

        with self.assertLogs("pos.setup", level="WARNING") as cm:
            response = self.client.post(
                reverse("onboarding_wizard") + "?step=3",
                {"username": "taken_name", "password": "pw", "role": "cashier"},
            )
        self.assertEqual(response.status_code, 302)
        # Must not have silently attached a second, same-username account
        # under THIS tenant.
        self.assertFalse(
            User.objects.filter(username="taken_name", tenant=self.tenant).exists()
        )
        self.assertTrue(any("staff user" in msg for msg in cm.output))


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
