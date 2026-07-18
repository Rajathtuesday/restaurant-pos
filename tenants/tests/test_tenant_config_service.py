# tenants/tests/test_tenant_config_service.py
"""
Tests for tenants/services/tenant_config_service.py — the shared logic
extracted from portal/views.py::tenant_config and
accounts/views/superuser_views.py::tenant_config, which had silently
drifted apart: the superuser panel was missing parcel_charge_amount,
is_composition_scheme, and split_bill_by_category entirely from its
update_outlet action, its feature summary couldn't show
counter_billing/composition_scheme/parcel_charge at all, its PRESETS
dict had a different set of presets from portal's, and its apply_preset
never applied a preset's outlet-field overrides.

Coverage:
  - The service functions work correctly in isolation
  - Both admin panels (portal and superuser) now produce IDENTICAL
    results for the same tenant — the actual regression this exists to
    prevent
"""
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from tenants.models import Tenant, Outlet, TenantFeatureOverride
from setup.models import KitchenStation, PaymentConfig
from tenants.services import tenant_config_service as tcs


def _tenant_outlet(name="Config Test Tenant"):
    tenant = Tenant.objects.create(name=name, tenant_type="fine_dining")
    outlet = Outlet.objects.create(tenant=tenant, name=f"{name} Main")
    return tenant, outlet


def _superuser(suffix="su"):
    return User.objects.create_superuser(
        username=f"admin_{suffix}", password="pw", email=f"{suffix}@rasova.net",
    )


class TenantConfigServiceUnitTest(TestCase):
    """Direct tests of the extracted service functions."""

    def setUp(self):
        self.tenant, self.outlet = _tenant_outlet()

    def test_get_feature_summary_includes_previously_missing_features(self):
        # The superuser panel's own key_features list used to omit these
        # three entirely — they were invisible from that panel no matter
        # what state they were actually in.
        summary_keys = {row["key"] for row in tcs.get_feature_summary(self.tenant)}
        self.assertIn("counter_billing", summary_keys)
        self.assertIn("composition_scheme", summary_keys)
        self.assertIn("parcel_charge", summary_keys)

    def test_update_outlet_from_post_sets_previously_missing_fields(self):
        from django.http import QueryDict

        post = QueryDict(mutable=True)
        post.update({
            "phone": "9999999999", "gst_no": "29abcde1234f1z5", "address": "MG Road",
            "parcel_charge_amount": "25.50",
        })
        post.setlist("is_composition_scheme", ["on"])
        post.setlist("split_bill_by_category", ["on"])

        tcs.update_outlet_from_post(self.outlet, post)
        self.outlet.refresh_from_db()

        self.assertEqual(self.outlet.parcel_charge_amount, Decimal("25.50"))
        self.assertTrue(self.outlet.is_composition_scheme)
        self.assertTrue(self.outlet.split_bill_by_category)
        self.assertEqual(self.outlet.gst_no, "29ABCDE1234F1Z5")

    def test_unparseable_parcel_charge_leaves_value_unchanged_and_logs(self):
        from django.http import QueryDict

        original = self.outlet.parcel_charge_amount
        post = QueryDict(mutable=True)
        post.update({"parcel_charge_amount": "not-a-number"})

        with self.assertLogs("pos.tenants", level="WARNING") as cm:
            tcs.update_outlet_from_post(self.outlet, post)

        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.parcel_charge_amount, original)
        self.assertTrue(any("parcel_charge_amount" in msg for msg in cm.output))

    def test_apply_preset_to_tenant_applies_outlet_overrides(self):
        # The superuser copy of apply_preset never applied a preset's
        # "outlet" field overrides at all — this proves the shared
        # version actually does.
        user = _superuser()
        preset = tcs.apply_preset_to_tenant(self.tenant, "counter_billing", user)
        self.assertIsNotNone(preset)
        self.outlet.refresh_from_db()
        self.assertTrue(self.outlet.split_bill_by_category)

    def test_apply_preset_unknown_key_returns_none(self):
        user = _superuser()
        result = tcs.apply_preset_to_tenant(self.tenant, "not_a_real_preset", user)
        self.assertIsNone(result)

    def test_qsr_kds_preset_exists_and_matches_sibling_shape(self):
        # qsr_kds only ever existed in the superuser copy before — confirm
        # it survived the merge and got the same field shape as the others
        # (portal's presets never had it at all).
        self.assertIn("qsr_kds", tcs.PRESETS)
        preset = tcs.PRESETS["qsr_kds"]
        self.assertIn("outlet", preset)
        self.assertIn("icon", preset)


class TenantConfigPanelParityTest(TestCase):
    """
    Integration tests proving portal and superuser now agree with each
    other for the same tenant — the actual bug this whole fix exists for.
    """

    def setUp(self):
        self.tenant, self.outlet = _tenant_outlet("Parity Tenant")
        self.su = _superuser("parity")
        self.client = Client()
        self.client.force_login(self.su)

    def test_both_panels_show_identical_feature_summary(self):
        portal_resp = self.client.get(reverse("portal:tenant", args=[self.tenant.id]))
        su_resp = self.client.get(reverse("superuser_tenant", args=[self.tenant.id]))

        portal_summary = portal_resp.context["feature_summary"]
        su_summary = su_resp.context["feature_summary"]
        self.assertEqual(portal_summary, su_summary)

    def test_both_panels_offer_identical_presets(self):
        portal_resp = self.client.get(reverse("portal:tenant", args=[self.tenant.id]))
        su_resp = self.client.get(reverse("superuser_tenant", args=[self.tenant.id]))
        self.assertEqual(set(portal_resp.context["presets"].keys()), set(su_resp.context["presets"].keys()))

    def test_applying_same_preset_from_either_panel_gives_same_outlet_state(self):
        # Apply via superuser panel to one tenant...
        tenant_a, outlet_a = _tenant_outlet("Parity A")
        self.client.post(
            reverse("superuser_preset", args=[tenant_a.id]), {"preset": "counter_billing"}
        )
        outlet_a.refresh_from_db()

        # ...and via portal to a separate, identical tenant.
        tenant_b, outlet_b = _tenant_outlet("Parity B")
        self.client.post(
            reverse("portal:preset", args=[tenant_b.id]), {"preset": "counter_billing"}
        )
        outlet_b.refresh_from_db()

        # Before this fix, only the portal path would have set this.
        self.assertEqual(outlet_a.split_bill_by_category, outlet_b.split_bill_by_category)
        self.assertTrue(outlet_a.split_bill_by_category)

    def test_superuser_panel_can_now_set_previously_missing_outlet_fields(self):
        resp = self.client.post(
            reverse("superuser_tenant", args=[self.tenant.id]),
            {
                "action": "update_outlet",
                "phone": "9876543210",
                "parcel_charge_amount": "15.00",
                "is_composition_scheme": "on",
                "split_bill_by_category": "on",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.parcel_charge_amount, Decimal("15.00"))
        self.assertTrue(self.outlet.is_composition_scheme)
        self.assertTrue(self.outlet.split_bill_by_category)
