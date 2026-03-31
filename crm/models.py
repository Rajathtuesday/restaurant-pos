# crm/models.py
from django.db import models


class Guest(models.Model):
    """
    A uniquely-identified guest by phone number per tenant.
    Points are accumulated across all visits.
    """
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)

    phone = models.CharField(max_length=20)
    name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)

    total_points = models.IntegerField(default=0)
    total_spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    visit_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tenant", "phone")
        indexes = [
            models.Index(fields=["tenant", "phone"]),
        ]

    def __str__(self):
        return f"{self.name or self.phone}"


class LoyaltyTransaction(models.Model):
    """
    Records every points earn/redeem event for a guest.
    """
    TYPE_CHOICES = (
        ("earn", "Earn"),
        ("redeem", "Redeem"),
        ("adjust", "Adjust"),
    )

    guest = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="transactions")

    order = models.ForeignKey(
        "orders.Order",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="loyalty_transactions"
    )

    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)

    points = models.IntegerField()  # positive = earned, negative = redeemed

    description = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.guest} – {self.transaction_type} {self.points}pts"
