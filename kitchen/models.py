# kitchen/models.py
from django.db import models

from core.models import TenantScopedModel


class KOTBatch(TenantScopedModel):
    """
    Moved here from orders/models.py (Phase 3 of the orders app split) via a
    state-only migration -- the underlying table is still named orders_kotbatch
    (see Meta.db_table below), so this move touched zero rows and required
    zero downtime.

    Note: orders.OrderItem.kot is a real ForeignKey into this model -- the
    only reverse dependency (core model -> satellite model) anywhere in the
    orders app split. See kitchen/migrations/0001_initial.py and
    orders/migrations/0056_alter_orderitem_kot.py for how that's handled.
    """

    STATUS = (
        ("confirmed", "Confirmed"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="kots"
    )

    kot_number = models.IntegerField()

    station = models.ForeignKey(
        "setup.KitchenStation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kots"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="confirmed"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_kotbatch"
        unique_together = ("order", "kot_number")
        indexes = [
            models.Index(fields=["tenant", "outlet"], name="orders_kotb_tenant__8938f0_idx"),
            models.Index(fields=["tenant", "outlet", "status"], name="kotbatch_tenant_outlet_status"),
        ]

    def __str__(self):
        return f"KOT {self.kot_number}"


class DailyKOTCounter(TenantScopedModel):
    """
    Moved here from orders/models.py (Phase 3 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_dailykotcounter, so this move touched zero rows and required
    zero downtime.
    """

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    date = models.DateField()

    value = models.IntegerField(default=0)

    class Meta:
        db_table = "orders_dailykotcounter"
        unique_together = ("tenant", "outlet", "date")

    def __str__(self):
        return f"{self.date} -> {self.value}"


class KitchenMessage(TenantScopedModel):
    """
    Moved here from orders/models.py (Phase 3 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_kitchenmessage, so this move touched zero rows and required
    zero downtime.
    """

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="kitchen_messages"
    )

    message = models.CharField(max_length=255)

    is_resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_kitchenmessage"
        indexes = [
            models.Index(fields=["tenant", "outlet", "is_resolved"], name="orders_kitc_tenant__89aa92_idx"),
        ]

    def __str__(self):
        return f"Message for {self.order.table.name if self.order.table else 'Walk-in'}: {self.message}"
