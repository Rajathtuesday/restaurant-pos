# shifts/models.py
from django.db import models
from django.utils import timezone


class Shift(models.Model):
    """
    Records a staff member's clock-in and clock-out for a given day.
    Tips can be recorded at clock-out by a manager.
    """

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    staff = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="shifts"
    )

    clocked_in_at = models.DateTimeField(default=timezone.now)
    clocked_out_at = models.DateTimeField(null=True, blank=True)

    tips = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet", "staff"]),
            models.Index(fields=["clocked_in_at"]),
        ]
        ordering = ["-clocked_in_at"]

    @property
    def is_active(self):
        return self.clocked_out_at is None

    @property
    def duration_hours(self):
        if not self.clocked_out_at:
            return None
        delta = self.clocked_out_at - self.clocked_in_at
        return round(delta.total_seconds() / 3600, 2)

    def __str__(self):
        return f"{self.staff.username} – {self.clocked_in_at.date()}"
