"""
Proves the tenant-isolation system check (tenants/checks.py) actually
catches a model that opts itself out of TenantManager, not just that
it's registered and silent on today's compliant models. A check that
only ever returns [] against real models isn't proven to work, it's
just proven not to have false positives.

Uses isolate_apps so the throwaway test models don't permanently join
the real app registry (a plain module-level model class would).
"""
from django.db import models
from django.test import SimpleTestCase
from django.test.utils import isolate_apps

from core.models import TenantScopedModel
from tenants.checks import find_unscoped_tenant_models


class TenantIsolationCheckTest(SimpleTestCase):

    @isolate_apps("tenants")
    def test_compliant_model_produces_no_error(self):
        class CompliantModel(TenantScopedModel):
            class Meta:
                app_label = "tenants"

        errors = find_unscoped_tenant_models([CompliantModel])
        self.assertEqual(errors, [])

    @isolate_apps("tenants")
    def test_noncompliant_model_is_caught(self):
        class NonCompliantModel(TenantScopedModel):
            # The exact mistake this check exists to catch: silently
            # overriding the inherited TenantManager with a plain,
            # unscoped Manager.
            objects = models.Manager()

            class Meta:
                app_label = "tenants"

        errors = find_unscoped_tenant_models([NonCompliantModel])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "tenants.E001")
        self.assertIn("NonCompliantModel", errors[0].msg)

    def test_abstract_models_are_skipped(self):
        # TenantScopedModel itself is abstract and has no concrete
        # manager instance to check -- must not false-positive on it.
        errors = find_unscoped_tenant_models([TenantScopedModel])
        self.assertEqual(errors, [])

    @isolate_apps("tenants")
    def test_non_tenant_scoped_models_are_ignored(self):
        class PlainModel(models.Model):
            class Meta:
                app_label = "tenants"

        errors = find_unscoped_tenant_models([PlainModel])
        self.assertEqual(errors, [])

    def test_real_installed_models_are_all_compliant_today(self):
        # The actual, live check -- every real TenantScopedModel subclass
        # currently in the app registry must pass. This is the same
        # thing `manage.py check` runs at startup; asserting it here
        # keeps it covered by the normal test suite too.
        from django.apps import apps
        errors = find_unscoped_tenant_models(apps.get_models())
        self.assertEqual(
            errors, [],
            f"Found {len(errors)} model(s) that inherit TenantScopedModel "
            f"but don't use TenantManager: {[e.obj for e in errors]}"
        )
