# notifications/models.py
from django.db import models
from django.db.models import Index
from core.models import TenantScopedModel


class Notification(TenantScopedModel):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    TYPE_CHOICES = [
        ("low_stock", "Low Stock"),
        ("waiter_call", "Waiter Call"),
        ("order_ready", "Order Ready"),
        ("system", "System"),
    ]

    type = models.CharField(
        max_length=50,
        choices=TYPE_CHOICES
    )

    message = models.TextField()

    item = models.ForeignKey(
        "inventory.InventoryItem",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    is_read = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:

        ordering = ["-created_at"]

        indexes = [
            Index(fields=["tenant", "outlet"]),
            Index(fields=["is_read"]),
        ]


    def __str__(self):
        return f"{self.type} - {self.message[:40]}"