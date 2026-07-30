"""
Preflight check for the tenant-isolation hardening work (see the plan in
project notes: TenantManager will start auto-filtering by the current
request's tenant instead of returning every row unfiltered by default).

Before that flip, any row whose tenant_id is NULL becomes invisible to
everyone, silently, the moment it lands -- and any row whose tenant_id
points at a tenant that no longer exists is a sign of a deeper data
integrity gap worth knowing about regardless. This walks every
TenantScopedModel subclass currently installed (not a hardcoded list, so
it stays accurate as models are added) and reports both.

Run: python manage.py tenant_isolation_preflight
"""
from django.apps import apps
from django.core.management.base import BaseCommand

from core.models import TenantScopedModel
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Reports null/orphaned tenant_id rows across every TenantScopedModel subclass."

    def handle(self, *args, **options):
        valid_tenant_ids = set(Tenant.objects.values_list("id", flat=True))

        scoped_models = [
            m for m in apps.get_models()
            if issubclass(m, TenantScopedModel) and not m._meta.abstract
        ]

        self.stdout.write(f"Checking {len(scoped_models)} TenantScopedModel subclasses...\n")

        any_issue = False
        for model in sorted(scoped_models, key=lambda m: (m._meta.app_label, m.__name__)):
            label = f"{model._meta.app_label}.{model.__name__}"

            field_names = {f.name for f in model._meta.get_fields()}
            if "tenant" not in field_names:
                self.stdout.write(self.style.WARNING(f"  {label}: no 'tenant' field at all (skipped)"))
                continue

            tenant_field = model._meta.get_field("tenant")
            nullable = tenant_field.null

            null_count = model.objects.unscoped(reason="preflight-check").filter(tenant__isnull=True).count() \
                if hasattr(model.objects, "unscoped") \
                else model.objects.filter(tenant__isnull=True).count()

            all_tenant_ids = set(
                (model.objects.unscoped(reason="preflight-check") if hasattr(model.objects, "unscoped") else model.objects)
                .exclude(tenant__isnull=True)
                .values_list("tenant_id", flat=True)
                .distinct()
            )
            orphaned_ids = all_tenant_ids - valid_tenant_ids

            if null_count or orphaned_ids:
                any_issue = True
                flag = self.style.ERROR("ISSUE")
            else:
                flag = self.style.SUCCESS("ok")

            nullable_note = " (field allows null)" if nullable else " (field does NOT allow null -- unexpected if count > 0)"
            self.stdout.write(f"  [{flag}] {label}: null_tenant_id={null_count}{nullable_note if null_count else ''}, orphaned_tenant_ids={sorted(orphaned_ids) if orphaned_ids else 0}")

        self.stdout.write("")
        if any_issue:
            self.stdout.write(self.style.ERROR(
                "One or more models have null or orphaned tenant_id rows. "
                "Resolve or consciously accept these before enabling TENANT_AUTO_SCOPE_ENABLED."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("Clean. No null or orphaned tenant_id rows found."))
