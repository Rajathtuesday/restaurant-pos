# promos/models.py
from decimal import Decimal

from django.db import models
from django.db.models import Q

from core.models import TenantScopedModel


class Promo(TenantScopedModel):
    """
    Tenant/outlet-scoped promotional discount applied to any order.
    - outlet=NULL  → promo runs across ALL outlets of this tenant
    - outlet set   → outlet-specific only
    Supports usage caps, min-order validation, and date-range gating.

    Moved here from orders/models.py (Phase 0 of the orders app split) via a
    state-only migration -- the underlying table is still named orders_promo
    (see Meta.db_table below), so this move touched zero rows and required
    zero downtime.
    """

    DISCOUNT_TYPE_CHOICES = [
        ("percentage", "% Off"),
        ("amount",     "₹ Flat Off"),
    ]

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)

    # NULL → applies to every outlet of this tenant
    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE,
        null=True, blank=True,
        help_text="Leave blank to broadcast across all outlets of this tenant.",
    )

    name        = models.CharField(max_length=120, help_text="e.g. 'Happy Hours 20%'")
    code        = models.CharField(
        max_length=30, blank=True,
        help_text="Short code printed on bill (e.g. HH20). Unique per tenant.",
    )
    description = models.TextField(blank=True, help_text="T&C / internal notes visible to staff")

    discount_type  = models.CharField(max_length=12, choices=DISCOUNT_TYPE_CHOICES, default="percentage")
    discount_value = models.DecimalField(max_digits=8, decimal_places=2)

    min_order_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Minimum order subtotal to apply this promo (0 = no minimum)",
    )

    max_uses    = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Leave blank for unlimited uses",
    )
    usage_count = models.PositiveIntegerField(default=0)

    valid_from  = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)

    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_promo"
        ordering = ["name"]
        indexes  = [
            models.Index(fields=["tenant", "outlet", "is_active"], name="orders_prom_tenant__495286_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=Q(code__gt=""),
                name="unique_promo_code_per_tenant",
            )
        ]

    def __str__(self):
        scope  = self.outlet.name if self.outlet_id else "All Outlets"
        symbol = "%" if self.discount_type == "percentage" else "₹"
        return f"{self.name} [{scope}] ({symbol}{self.discount_value})"

    # ── Validity helpers ──────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        from django.utils.timezone import localdate
        return bool(self.valid_until and localdate() > self.valid_until)

    @property
    def is_not_started(self) -> bool:
        from django.utils.timezone import localdate
        return bool(self.valid_from and localdate() < self.valid_from)

    @property
    def is_exhausted(self) -> bool:
        return bool(self.max_uses and self.usage_count >= self.max_uses)

    @property
    def is_currently_valid(self) -> bool:
        return self.is_active and not self.is_expired and not self.is_not_started and not self.is_exhausted

    def applies_to_outlet(self, outlet) -> bool:
        """True if this promo covers the given outlet (or is tenant-wide)."""
        return self.outlet_id is None or self.outlet_id == outlet.id

    def validate(self, outlet, order_subtotal: Decimal) -> tuple:
        """(ok: bool, error: str) — call before applying the discount."""
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
            return False, f"Minimum order value ₹{self.min_order_value} required."
        return True, ""

    def record_use(self):
        """Increments usage_count. Must be called within a select_for_update() transaction block."""
        self.usage_count += 1
        self.save(update_fields=["usage_count"])

    def validate_and_use(self, outlet, order_subtotal: Decimal) -> tuple:
        """Atomic validate + increment — prevents the race where two cashiers
        both pass validate() before either calls record_use().

        Must be called inside a transaction.atomic() block in the view.
        Returns (ok: bool, error: str).
        """
        locked = Promo.objects.select_for_update().get(pk=self.pk)
        ok, error = locked.validate(outlet, order_subtotal)
        if ok:
            locked.record_use()
            # Keep in-memory object consistent so the caller sees updated count.
            self.usage_count = locked.usage_count
        return ok, error
