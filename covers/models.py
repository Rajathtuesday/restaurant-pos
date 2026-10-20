"""
Cover charges: entry paid at the door that the guest then spends on food
and drinks (covers/services.py).

A cover works like a voucher, not a discount: the bill is the bill, with all
its tax, and the cover pays part of it (a Payment with method "cover"). So
GST is charged on what was actually consumed, when it was consumed, and the
bill and GSTR-1 never depend on how a guest paid.
"""
from decimal import Decimal

from django.db import models
from django.db.models import Q, Sum

from core.models import TenantScopedModel

PAID_BY = (("cash", "Cash"), ("upi", "UPI"), ("card", "Card"))


class Cover(TenantScopedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    code = models.CharField(max_length=8, help_text="Printed on the slip; typed on the bill to use it.")
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="What the guest can spend.")
    guests = models.PositiveSmallIntegerField(default=1)
    guest_name = models.CharField(max_length=100, blank=True, default="")
    guest_phone = models.CharField(max_length=15, blank=True, default="")
    paid_by = models.CharField(max_length=10, choices=PAID_BY)
    business_date = models.DateField(help_text="The night it was sold; unused balance lapses after it.")
    sold_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    sold_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sold_at"]
        constraints = [
            models.UniqueConstraint(fields=["outlet", "code"], name="unique_cover_code_per_outlet"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="cover_amount_positive"),
        ]
        indexes = [models.Index(fields=["tenant", "outlet", "business_date"], name="cover_outlet_day")]

    def __str__(self):
        return f"Cover {self.code} ({self.amount})"

    def used(self):
        """What has been spent from it, not counting bills later cancelled."""
        total = (self.redemptions.exclude(order__status="cancelled")
                 .aggregate(total=Sum("amount"))["total"])
        return total or Decimal("0.00")

    def balance(self):
        return self.amount - self.used()


class CoverRedemption(models.Model):
    """Part (or all) of a cover spent on one bill."""
    cover = models.ForeignKey(Cover, on_delete=models.PROTECT, related_name="redemptions")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="cover_redemptions")
    payment = models.OneToOneField("orders.Payment", on_delete=models.PROTECT, related_name="cover_redemption")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
