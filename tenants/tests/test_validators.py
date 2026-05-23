"""
Tests for the schema-review fixes applied to the tenants app.

Coverage:
  - Outlet.gst_no  GSTIN regex validator (valid / invalid patterns)
  - Outlet.fssai_no 14-digit validator (valid / invalid)
  - Tenant.slug no redundant explicit index (unique=True already indexes it)
"""
from django.core.exceptions import ValidationError
from django.test import TestCase

from tenants.models import Outlet, Tenant


def _tenant(name="TestTenant"):
    return Tenant.objects.create(name=name)


class TestGSTINValidator(TestCase):

    def setUp(self):
        self.tenant = _tenant("GSTIN Cafe")

    def _outlet(self, gst_no=None, fssai_no=None):
        o = Outlet(tenant=self.tenant, name="Branch", gst_no=gst_no, fssai_no=fssai_no)
        return o

    # ── Valid GSTINs ──────────────────────────────────────────────────────

    def test_valid_gstin_passes(self):
        outlet = self._outlet(gst_no="29ABCDE1234F1Z5")
        # Should not raise
        outlet.full_clean()

    def test_valid_gstin_another_state(self):
        outlet = self._outlet(gst_no="07AABCA1234A1ZA")
        outlet.full_clean()

    def test_blank_gstin_allowed(self):
        outlet = self._outlet(gst_no="")
        outlet.full_clean()

    def test_null_gstin_allowed(self):
        outlet = self._outlet(gst_no=None)
        outlet.full_clean()

    # ── Invalid GSTINs ────────────────────────────────────────────────────

    def test_short_gstin_rejected(self):
        outlet = self._outlet(gst_no="29ABCDE1234")
        with self.assertRaises(ValidationError):
            outlet.full_clean()

    def test_all_digits_gstin_rejected(self):
        outlet = self._outlet(gst_no="123456789012345")
        with self.assertRaises(ValidationError):
            outlet.full_clean()

    def test_lowercase_gstin_rejected(self):
        outlet = self._outlet(gst_no="29abcde1234f1z5")
        with self.assertRaises(ValidationError):
            outlet.full_clean()

    def test_wrong_length_gstin_rejected(self):
        outlet = self._outlet(gst_no="29ABCDE1234F1Z")
        with self.assertRaises(ValidationError):
            outlet.full_clean()

    def test_missing_z_rejected(self):
        outlet = self._outlet(gst_no="29ABCDE1234F1A5")
        with self.assertRaises(ValidationError):
            outlet.full_clean()

    def test_gstin_saved_correctly(self):
        outlet = Outlet.objects.create(
            tenant=self.tenant, name="Valid Branch", gst_no="29ABCDE1234F1Z5"
        )
        outlet.refresh_from_db()
        self.assertEqual(outlet.gst_no, "29ABCDE1234F1Z5")


class TestFSSAIValidator(TestCase):

    def setUp(self):
        self.tenant = _tenant("FSSAI Cafe")

    def _outlet(self, fssai_no=None):
        o = Outlet(tenant=self.tenant, name="Branch", fssai_no=fssai_no)
        return o

    def test_valid_14_digit_fssai_passes(self):
        outlet = self._outlet(fssai_no="12345678901234")
        outlet.full_clean()

    def test_blank_fssai_allowed(self):
        outlet = self._outlet(fssai_no="")
        outlet.full_clean()

    def test_null_fssai_allowed(self):
        outlet = self._outlet(fssai_no=None)
        outlet.full_clean()

    def test_13_digit_fssai_rejected(self):
        outlet = self._outlet(fssai_no="1234567890123")
        with self.assertRaises(ValidationError):
            outlet.full_clean()

    def test_15_digit_fssai_rejected(self):
        outlet = self._outlet(fssai_no="123456789012345")
        with self.assertRaises(ValidationError):
            outlet.full_clean()

    def test_alphanumeric_fssai_rejected(self):
        outlet = self._outlet(fssai_no="1234567890AB34")
        with self.assertRaises(ValidationError):
            outlet.full_clean()


class TestTenantSlugNoRedundantIndex(TestCase):

    def test_slug_index_not_explicitly_listed(self):
        """unique=True on slug already creates an index — no manual Index should duplicate it."""
        field_sets = [tuple(idx.fields) for idx in Tenant._meta.indexes]
        self.assertNotIn(("slug",), field_sets,
                         "Explicit Index(['slug']) is redundant — unique=True creates the index.")

    def test_tenant_type_index_present(self):
        field_sets = [tuple(idx.fields) for idx in Tenant._meta.indexes]
        self.assertIn(("tenant_type",), field_sets)
