from django.conf import settings
from django.db import models

from core.tenant_context import get_current_tenant_id


class TenantQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)

    def for_outlet(self, tenant, outlet):
        return self.filter(tenant=tenant, outlet=outlet)


class TenantManager(models.Manager):
    """
    Auto-scopes every query to core.tenant_context's current tenant
    (set by ContextLoggingMiddleware from request.user.tenant for the
    duration of one authenticated request). With no tenant in context
    -- an anonymous request, a Celery task, a management command, or
    TENANT_AUTO_SCOPE_ENABLED off -- returns unfiltered, exactly like
    before this existed. That "no context -> unfiltered" behavior is
    deliberate, not a gap to close later: it's what keeps admin/Celery/
    management commands working, and it's also why token-authenticated
    anonymous routes (QR ordering, public bill links, the token display
    board) are unaffected by this change -- they were never driven by
    request.user.tenant to begin with, they resolve identity from a
    signed/opaque token instead.
    """
    def get_queryset(self):
        qs = TenantQuerySet(self.model, using=self._db)
        if not getattr(settings, "TENANT_AUTO_SCOPE_ENABLED", False):
            return qs
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            return qs
        return qs.filter(tenant_id=tenant_id)

    def for_tenant(self, tenant):
        # Deliberately NOT self.get_queryset() -- that would already carry
        # the ambient context's auto-filter, so an explicit for_tenant(B)
        # while context is A would AND the two together and always return
        # empty instead of B's rows. This is an explicit override, same as
        # unscoped(), just pre-filtered to one tenant instead of none.
        return TenantQuerySet(self.model, using=self._db).for_tenant(tenant)

    def for_outlet(self, tenant, outlet):
        return TenantQuerySet(self.model, using=self._db).for_outlet(tenant, outlet)

    def unscoped(self, reason):
        """
        The only sanctioned way to deliberately see every tenant's rows
        while TENANT_AUTO_SCOPE_ENABLED is on -- for portal/superuser
        tooling that manages tenants rather than belonging to one.
        `reason` is required so every call site is self-documenting in
        review, not a bare escape hatch; core/tests.py asserts an exact
        allowlist of where this is called from, so a new, unreviewed
        call site fails CI until a human deliberately updates it.

        Must be called directly on the manager (Model.objects.unscoped(...)),
        not chained after .filter()/.all() -- by the time you have a
        queryset, get_queryset() has already applied (or skipped) the
        auto-filter, and there's no reliable way to undo a filter that's
        already baked into the query.
        """
        if not reason or not reason.strip():
            raise ValueError("unscoped() requires a non-empty reason")
        return TenantQuerySet(self.model, using=self._db)


class TenantScopedModel(models.Model):
    """Abstract base for every model that belongs to a single tenant.

    objects is a TenantManager: every query auto-scopes to the current
    request's tenant when TENANT_AUTO_SCOPE_ENABLED is on. Use
    .objects.unscoped(reason="...") for deliberate cross-tenant access,
    and .objects.for_tenant(tenant)/.for_outlet(tenant, outlet) to scope
    explicitly regardless of the current context.
    """
    objects = TenantManager()

    class Meta:
        abstract = True
