"""
System check guarding the tenant-isolation guarantee: every concrete
TenantScopedModel subclass must keep TenantManager as its default
manager. Without this, a future `objects = models.Manager()` override
on some model would silently opt it back out of tenant auto-scoping,
with no error, no test failure, just a query that quietly stops being
filtered. This turns that into a startup-time error instead.
"""
from django.core.checks import Error, register


def find_unscoped_tenant_models(models_iterable):
    """
    The actual check logic, factored out from the @register() entry point
    so it can be unit tested against an explicit list of models instead
    of always walking the live app registry -- see
    tenants/tests/test_isolation_check.py.
    """
    from core.models import TenantManager, TenantScopedModel

    errors = []
    for model in models_iterable:
        if not issubclass(model, TenantScopedModel) or model._meta.abstract:
            continue
        if not isinstance(model._default_manager, TenantManager):
            errors.append(
                Error(
                    f"{model._meta.label} inherits TenantScopedModel but its "
                    f"default manager ({model._default_manager.__class__.__name__}) "
                    f"is not a TenantManager -- it won't be auto-scoped to the "
                    f"current tenant, and any query against it can silently "
                    f"return every tenant's rows.",
                    hint="Give it 'objects = TenantManager()' (inherited from "
                         "TenantScopedModel by default -- only an explicit "
                         "override on the subclass would cause this).",
                    obj=model,
                    id="tenants.E001",
                )
            )
    return errors


@register()
def tenant_scoped_models_use_tenant_manager(app_configs, **kwargs):
    from django.apps import apps
    return find_unscoped_tenant_models(apps.get_models())
