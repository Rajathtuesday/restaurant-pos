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
    # Uses are not stored: they are the bills holding the promo (see uses).

    valid_from  = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)

    is_active  = models.BooleanField(default=True)
    # Set instead of deleting, so bills that used the promo still point at it.
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_promo"
        ordering = ["name"]
        indexes  = [
            models.Index(fields=["tenant", "outlet", "is_active"], name="orders_prom_tenant__495286_idx"),
        ]
        constraints = [
            # One live promo per code; an archived promo frees its code.
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=Q(code__gt="") & Q(archived_at__isnull=True),
                name="unique_promo_code_per_tenant",
            )
        ]

    def __str__(self):
        scope  = self.outlet.name if self.outlet_id else "All Outlets"
        symbol = "%" if self.discount_type == "percentage" else "₹"
        return f"{self.name} [{scope}] ({symbol}{self.discount_value})"

    # ── Validity helpers ──────────────────────────────────────────
    # Dates are judged by the outlet's BUSINESS day (core.utils), not the
    # calendar: a pub's Saturday night runs until the day's cutoff (6 am by
    # default), so a promo "valid until Saturday" must still be valid at 1 am.
    # A tenant-wide promo with no outlet uses the default cutoff.

    def business_today(self, outlet=None):
        from django.utils import timezone
        from core.utils import get_business_date
        return get_business_date(timezone.now(), outlet if outlet is not None else self.outlet)

    def is_expired_for(self, outlet=None) -> bool:
        return bool(self.valid_until and self.business_today(outlet) > self.valid_until)

    def is_not_started_for(self, outlet=None) -> bool:
        return bool(self.valid_from and self.business_today(outlet) < self.valid_from)

    @property
    def is_expired(self) -> bool:
        return self.is_expired_for()

    @property
    def is_not_started(self) -> bool:
        return self.is_not_started_for()

    # ── Uses ───────────────────────────────────────────────────────
    # A promo's uses are the bills that hold it, cancelled ones not counted.
    # They used to be a counter that every path touching a promo had to move
    # up and down by hand; now no path can forget, and a bill that took the
    # promo off simply stops counting.

    def bills(self):
        return self.orders.exclude(status="cancelled")

    @property
    def uses(self) -> int:
        return self.bills().count()

    @property
    def is_exhausted(self) -> bool:
        return bool(self.max_uses and self.uses >= self.max_uses)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def is_live_for(self, outlet) -> bool:
        """Usable at this outlet right now: active, not archived, covers the
        outlet, inside its dates (business day), uses left."""
        return (self.is_active and not self.is_archived and self.applies_to_outlet(outlet)
                and not self.is_expired_for(outlet) and not self.is_not_started_for(outlet)
                and not self.is_exhausted)

    def status_for(self, outlet):
        """(key, text) for the Setup screen: what the promo is right now at
        this outlet. Keys: archived, paused, starts, ended, used_up, active."""
        if self.is_archived:
            return "archived", "Deleted"
        if not self.is_active:
            return "paused", "Paused"
        if self.is_not_started_for(outlet):
            return "starts", f"Starts {self.valid_from:%d %b}"
        if self.is_expired_for(outlet):
            return "ended", f"Ended {self.valid_until:%d %b}"
        if self.is_exhausted:
            return "used_up", "Used up"
        return "active", "Active"

    def applies_to_outlet(self, outlet) -> bool:
        """True if this promo covers the given outlet (or is tenant-wide)."""
        return self.outlet_id is None or self.outlet_id == outlet.id

    def refusal_for(self, order, menu_value: Decimal) -> str:
        """Why `order` can't take this promo now, or "" if it can. menu_value
        is the bill's menu value (Order.menu_value). The bill's own hold on
        the promo doesn't count against the cap, so applying it again is
        always free."""
        outlet = order.outlet
        if not self.is_active or self.is_archived:
            return "Promo is not active."
        if not self.applies_to_outlet(outlet):
            return "Promo is not valid for this outlet."
        if self.is_not_started_for(outlet):
            return f"Promo starts on {self.valid_from.strftime('%d %b %Y')}."
        if self.is_expired_for(outlet):
            return f"Promo expired on {self.valid_until.strftime('%d %b %Y')}."
        if self.max_uses and self.bills().exclude(pk=order.pk).count() >= self.max_uses:
            return "Promo usage limit has been reached."
        if menu_value < self.min_order_value:
            return f"Minimum order ₹{self.min_order_value} required."
        return ""

    def locked(self):
        """This promo read again with its row locked, so two tills can't both
        take its last use: the second waits, then counts the first one's bill.
        Must be called inside a transaction."""
        return Promo.objects.select_for_update().get(pk=self.pk)
