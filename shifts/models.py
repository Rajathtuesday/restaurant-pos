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


class CashSession(models.Model):
    """
    Manages the cash drawer for an entire outlet shift/day.
    Reconciles physical cash with digital payment records.
    """
    STATUS_CHOICES = (
        ("open", "Open"),
        ("closed", "Closed"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    opened_by = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="opened_sessions")
    closed_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="closed_sessions")

    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Financials populated at closing
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discrepancy = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    total_digital_payments = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet", "status"]),
            models.Index(fields=["opened_at"]),
        ]
        ordering = ["-opened_at"]

    def __str__(self):
        return f"Session {self.id} ({self.status}) - {self.opened_at.date()}"
