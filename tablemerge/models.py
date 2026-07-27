# tablemerge/models.py
from django.db import models

from core.models import TenantScopedModel


class TableMerge(TenantScopedModel):
    """
    Moved here from orders/models.py (Phase 5 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_tablemerge, so this move touched zero rows and required zero
    downtime.

    The `tables` M2M is the only ManyToManyField anywhere in the split.
    db_table is pinned explicitly (same principle as Meta.db_table on every
    other moved model) so Django's auto-created through table keeps its
    original name (orders_tablemerge_tables) instead of being renamed to
    match this app's label.
    """

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    primary_table = models.ForeignKey(
        "orders.Table",
        on_delete=models.CASCADE,
        related_name="merged_primary"
    )

    tables = models.ManyToManyField("orders.Table", db_table="orders_tablemerge_tables")

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "orders_tablemerge"
        indexes = [
            models.Index(fields=["tenant", "outlet", "is_active"], name="orders_tabl_tenant__4ffa85_idx"),
        ]

    def __str__(self):
        return f"Merge on {self.primary_table.name} ({self.tables.count()} tables)"
