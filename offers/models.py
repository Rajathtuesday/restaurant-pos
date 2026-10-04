# offers/models.py
"""
Offers a pub runs by rule: buy N get M free, percent off, amount off, for
some dishes or categories, at some times. The engine (offers/engine.py)
applies them; offers/services.py runs it every time a bill is totalled.

Different from a promo (promos/models.py): a promo is a discount on the
whole bill that staff choose to apply; an offer applies itself to the
dishes it covers, so nobody types anything.
"""
from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TenantScopedModel
from offers.engine import AMOUNT_OFF, BUY_GET_FREE, PERCENT_OFF

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class Offer(TenantScopedModel):
    KIND_CHOICES = [
        (BUY_GET_FREE, "Buy N, get M free"),
        (PERCENT_OFF,  "% off"),
        (AMOUNT_OFF,   "Rs off each"),
    ]

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey(
        "tenants.Outlet", on_delete=models.CASCADE, null=True, blank=True,
        help_text="Leave blank for every outlet of this restaurant.",
    )
    name = models.CharField(max_length=80, help_text="Shown on the bill, e.g. 'Buy 2 pitchers, get 1 free'.")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)

    buy_qty = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Buy N: the units paid for.")
    free_qty = models.PositiveSmallIntegerField(default=1, help_text="Get M free: the cheapest M of each group.")
    percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                  help_text="% off: off each covered dish.")
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                 help_text="Rs off each covered dish (never more than its price).")

    priority = models.SmallIntegerField(
        default=0, help_text="When two offers could take the same dish, the higher priority is tried first.")
    valid_from = models.DateField(null=True, blank=True, help_text="First business day it runs.")
    valid_until = models.DateField(null=True, blank=True, help_text="Last business day it runs.")

    is_active = models.BooleanField(default=True)
    # When it was switched off. Dishes added before then keep the offer (the
    # price was locked when they were ordered); dishes added after don't.
    paused_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)

    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
                                   editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "id"]
        indexes = [models.Index(fields=["tenant", "is_active"], name="offer_tenant_active")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(percent__isnull=True) | (models.Q(percent__gt=0) & models.Q(percent__lte=100)),
                name="offer_percent_range",
            ),
            models.CheckConstraint(condition=models.Q(amount__isnull=True) | models.Q(amount__gt=0),
                                   name="offer_amount_positive"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.kind == BUY_GET_FREE:
            if not self.buy_qty or self.buy_qty < 1:
                errors["buy_qty"] = "How many must be bought (1 or more)."
            if not self.free_qty or self.free_qty < 1:
                errors["free_qty"] = "How many are free (1 or more)."
            elif self.buy_qty and self.buy_qty + self.free_qty > 20:
                errors["free_qty"] = "A group can be at most 20 units."
        elif self.kind == PERCENT_OFF:
            if self.percent is None or not (Decimal("0") < self.percent <= Decimal("100")):
                errors["percent"] = "A percent above 0 and at most 100."
        elif self.kind == AMOUNT_OFF:
            if self.amount is None or self.amount <= 0:
                errors["amount"] = "An amount above 0."
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            errors["valid_until"] = "The last day can't be before the first day."
        if self.outlet_id and self.tenant_id and self.outlet.tenant_id != self.tenant_id:
            errors["outlet"] = "That outlet belongs to another restaurant."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Switching off records when; switching on again clears it.
        if not self.is_active and self.paused_at is None:
            self.paused_at = timezone.now()
        elif self.is_active:
            self.paused_at = None
        super().save(*args, **kwargs)

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save()

    @property
    def summary(self):
        """One line for a list: what it does."""
        if self.kind == BUY_GET_FREE:
            return f"Buy {self.buy_qty}, get {self.free_qty} free"
        if self.kind == PERCENT_OFF:
            return f"{self.percent:.2f}".rstrip("0").rstrip(".") + "% off"
        return f"Rs {self.amount} off each"


class OfferTarget(models.Model):
    """A dish or a whole category an offer covers. An offer with no targets
    covers the whole menu."""
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="targets")
    menu_item = models.ForeignKey("menu.MenuItem", on_delete=models.CASCADE, null=True, blank=True)
    category = models.ForeignKey("menu.MenuCategory", on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(menu_item__isnull=False, category__isnull=True)
                           | models.Q(menu_item__isnull=True, category__isnull=False)),
                name="offer_target_one_of",
            ),
        ]

    def clean(self):
        if bool(self.menu_item_id) == bool(self.category_id):
            raise ValidationError("Pick a dish or a category, not both.")
        owner = (self.menu_item or self.category)
        if owner is not None and self.offer_id and owner.tenant_id != self.offer.tenant_id:
            raise ValidationError("That dish or category belongs to another restaurant.")

    def __str__(self):
        return str(self.menu_item or self.category)


class OfferWindow(models.Model):
    """When an offer runs. Days and times follow the business day (6 AM to
    6 AM by default), so a Friday window of 20:00 to 02:00 covers 1 AM on
    Saturday morning. An offer with no windows runs all the time."""
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="windows")
    days = models.CharField(
        max_length=7, blank=True, default="",
        help_text="Business-day weekdays as digits, Monday 0 to Sunday 6, e.g. 01234 for Mon-Fri. Blank: every day.",
    )
    start_time = models.TimeField(null=True, blank=True, help_text="Blank: from the start of the business day.")
    end_time = models.TimeField(null=True, blank=True,
                                help_text="Blank: to the end of the business day. Before the start: past midnight.")

    def clean(self):
        if self.days and (any(c not in "0123456" for c in self.days) or len(set(self.days)) != len(self.days)):
            raise ValidationError({"days": "Digits 0 (Monday) to 6 (Sunday), each once."})
        if self.start_time and self.end_time and self.start_time == self.end_time:
            raise ValidationError({"end_time": "The end can't be the same as the start."})

    @property
    def day_set(self):
        return frozenset(int(c) for c in self.days)

    def __str__(self):
        days = ", ".join(DAY_NAMES[d] for d in sorted(self.day_set)) or "Every day"
        start = (self.start_time or time(0)).strftime("%H:%M") if self.start_time else "open"
        end = self.end_time.strftime("%H:%M") if self.end_time else "close"
        return f"{days} {start}-{end}"


class OfferChange(models.Model):
    """One edit of an offer: who, when, and each field from what to what, so
    an owner can see that "Happy hour 25%" became 50% at 9 PM and by whom."""
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="changes")
    changed_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    # [{"field": "Percent off", "before": "50", "after": "5"}, ...]
    changes = models.JSONField(default=list)

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return f"{self.offer} changed by {self.changed_by} at {self.changed_at:%d %b %H:%M}"
