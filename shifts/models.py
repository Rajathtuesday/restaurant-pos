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

    @property
    def overtime_hours(self):
        """
        Calculates overtime hours based on the assigned schedule.
        If no schedule exists, uses a default 9-hour limit.
        """
        if not self.clocked_out_at:
            return 0
            
        # Try to find the schedule for this staff on this day
        schedule = StaffSchedule.objects.filter(
            staff=self.staff,
            date=self.clocked_in_at.date(),
            is_active=True
        ).first()
        
        actual_hours = self.duration_hours
        
        if schedule:
            scheduled_hours = schedule.duration_hours
            if actual_hours > scheduled_hours:
                return round(float(actual_hours) - float(scheduled_hours), 2)
        elif actual_hours > 9:
            return round(float(actual_hours) - 9, 2)
            
        return 0

    def __str__(self):
        return f"{self.staff.username} – {self.clocked_in_at.date()}"


class ShiftTemplate(models.Model):
    """
    Predefined shift patterns (e.g. 'Morning Shift', 'Kitchen Opening').
    """
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    # Optional: Standard pay rate for this shift
    base_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    class Meta:
        unique_together = ("tenant", "outlet", "name")

    def __str__(self):
        return f"{self.name} ({self.start_time}-{self.end_time})"


class StaffSchedule(models.Model):
    """
    Assigns a staff member to a shift on a specific date.
    Used for labor cost forecasting and overtime detection.
    """
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    
    staff = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="schedules")
    date = models.DateField()
    
    template = models.ForeignKey(ShiftTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Overrides if template is not used
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    @property
    def duration_hours(self):
        start = self.template.start_time if self.template else self.start_time
        end = self.template.end_time if self.template else self.end_time
        
        if not start or not end:
            return 0
            
        from datetime import datetime, date
        # Handle overnight shifts
        d1 = datetime.combine(date.today(), start)
        d2 = datetime.combine(date.today(), end)
        if d2 < d1:
            from datetime import timedelta
            d2 += timedelta(days=1)
            
        return round((d2 - d1).total_seconds() / 3600, 2)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet", "date"]),
            models.Index(fields=["staff", "date"]),
        ]

    def __str__(self):
        return f"{self.staff.username} - {self.date}"


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
