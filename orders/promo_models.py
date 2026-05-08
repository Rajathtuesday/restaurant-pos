# orders/promo_models.py
from decimal import Decimal
from django.db import models
from django.utils import timezone


class Promo(models.Model):
    """
    Tenant/outlet-scoped promotional discount applied to any order.
    Supports outlet-specific OR tenant-wide ("all outlets") promos,
    usage caps, min-order validation, and date-range gating.
    """

    DISCOUNT_TYPE_CHOICES = [
        ("percentage", "% Off"),
        ("amount",     "₹ Flat Off"),
    ]

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)

    # NULL outlet → promo runs on EVERY outlet of this tenant
    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE,
        null=True, blank=True,
        help_text="Leave blank to apply across all outlets of this tenant.",
    )

    name  = models.CharField(max_length=120, help_text="e.g. 'Happy Hours 20%'")
    code  = models.CharField(
        max_length=30, blank=True,
        help_text="Short code printed on bill (e.g. HH20). Must be unique per tenant if set.",
    )
    description = models.TextField(blank=True, help_text="Internal notes / T&C visible to staff")

    discount_type  = models.CharField(max_length=12, choices=DISCOUNT_TYPE_CHOICES, default="percentage")
    discount_value = models.DecimalField(max_digits=8, decimal_places=2)

    min_order_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Minimum order subtotal required to apply this promo",
    )

    max_uses      = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Leave blank for unlimited uses",
    )
    usage_count   = models.PositiveIntegerField(default=0)

    valid_from    = models.DateField(null=True, blank=True)
    valid_until   = models.DateField(null=True, blank=True)

    is_active     = models.BooleanField(default=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes  = [
            models.Index(fields=["tenant", "outlet", "is_active"]),
        ]
        constraints = [
            # Unique code per tenant (ignores empty codes)
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=models.Q(code__gt=""),
                name="unique_promo_code_per_tenant",
            )
        ]

    def __str__(self):
        scope = self.outlet.name if self.outlet_id else "All Outlets"
        return f"{self.name} [{scope}] ({self.discount_type} {self.discount_value})"

    # ── Validity helpers ──────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        if self.valid_until and timezone.localdate() > self.valid_until:
            return True
        return False

    @property
    def is_not_started(self) -> bool:
        if self.valid_from and timezone.localdate() < self.valid_from:
            return True
        return False

    @property
    def is_exhausted(self) -> bool:
        return bool(self.max_uses and self.usage_count >= self.max_uses)

    @property
    def is_currently_valid(self) -> bool:
        return self.is_active and not self.is_expired and not self.is_not_started and not self.is_exhausted

    def applies_to_outlet(self, outlet) -> bool:
        """Returns True if this promo covers the given outlet."""
        return self.outlet_id is None or self.outlet_id == outlet.id

    def validate(self, outlet, order_subtotal: Decimal) -> tuple[bool, str]:
        """
        Returns (ok, error_message).
        Call from billing view before applying the discount.
        """
        if not self.is_active:
            return False, "Promo is not active."
        if not self.applies_to_outlet(outlet):
            return False, "Promo is not valid for this outlet."
        if self.is_not_started:
            return False, f"Promo starts on {self.valid_from.strftime('%d %b %Y')}."
        if self.is_expired:
            return False, f"Promo expired on {self.valid_until.strftime('%d %b %Y')}."
        if self.is_exhausted:
            return False, "Promo usage limit has been reached."
        if order_subtotal < self.min_order_value:
            return False, f"Minimum order value of ₹{self.min_order_value} required."
        return True, ""

    def record_use(self):
        """Atomically increments usage_count. Call after discount is applied."""
        Promo.objects.filter(pk=self.pk).update(usage_count=models.F("usage_count") + 1)

