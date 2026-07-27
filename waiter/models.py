# waiter/models.py
from django.db import models
from django.db.models import Q

from core.models import TenantScopedModel


class WaiterCall(TenantScopedModel):
    """
    Moved here from orders/models.py (Phase 4 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_waitercall, so this move touched zero rows and required zero
    downtime.
    """

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    table = models.ForeignKey("orders.Table", on_delete=models.CASCADE)

    is_resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_waitercall"
        constraints = [
            models.UniqueConstraint(
                fields=["table"],
                condition=Q(is_resolved=False),
                name="one_active_waiter_call_per_table"
            )
        ]

    def __str__(self):
        return f"Waiter Call - {self.table.name}"
