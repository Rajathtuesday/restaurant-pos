# setup/models.py
from django.db import models
from django.db.models import UniqueConstraint, Q


class KitchenStation(models.Model):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    name = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["tenant", "outlet"],
                condition=Q(is_default=True),
                name="one_default_station_per_outlet"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.outlet})"


# -------------------------------------------------
# PAYMENT CONFIG (replaces session-based storage)
# -------------------------------------------------

class PaymentConfig(models.Model):
    """
    Stores which payment methods are enabled for an outlet.
    One record per outlet.
    """
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.OneToOneField(
        "tenants.Outlet",
        on_delete=models.CASCADE,
        related_name="payment_config"
    )

    cash_enabled = models.BooleanField(default=True)
    upi_enabled = models.BooleanField(default=True)
    card_enabled = models.BooleanField(default=False)

    # Label overrides (e.g. "GPay" instead of "UPI")
    cash_label = models.CharField(max_length=50, default="Cash")
    upi_label = models.CharField(max_length=50, default="UPI / GPay")
    card_label = models.CharField(max_length=50, default="Card")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payment Configuration"

    def enabled_methods(self):
        """Returns list of dicts for enabled payment methods."""
        methods = []
        if self.cash_enabled:
            methods.append({"key": "cash", "label": self.cash_label})
        if self.upi_enabled:
            methods.append({"key": "upi", "label": self.upi_label})
        if self.card_enabled:
            methods.append({"key": "card", "label": self.card_label})
        return methods

    def __str__(self):
        return f"Payment Config for {self.outlet}"
