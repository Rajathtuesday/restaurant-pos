from django.db import models


class TenantQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)

    def for_outlet(self, tenant, outlet):
        return self.filter(tenant=tenant, outlet=outlet)


class TenantManager(models.Manager):
    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db)

    def for_tenant(self, tenant):
        return self.get_queryset().for_tenant(tenant)

    def for_outlet(self, tenant, outlet):
        return self.get_queryset().for_outlet(tenant, outlet)


class TenantScopedModel(models.Model):
    """Abstract base for every model that belongs to a single tenant.

    Adds .objects.for_tenant(tenant) and .objects.for_outlet(tenant, outlet)
    without changing get_queryset() — admin, Celery, and management commands
    continue to see all rows unfiltered.
    """
    objects = TenantManager()

    class Meta:
        abstract = True
