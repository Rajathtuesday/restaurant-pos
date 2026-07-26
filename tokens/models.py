# tokens/models.py
from django.db import models

from core.models import TenantScopedModel


class DailyTokenCounter(TenantScopedModel):
    """
    Per-outlet, per-day sequential counter for token numbers.

    WHY this exists instead of MAX(token_number)+1:
      select_for_update() cannot lock aggregate results in Django.
      Two concurrent requests both read MAX=5, both try to create
      token 6, the second crashes on unique_together — order is lost.
      A counter ROW can be locked with select_for_update(), so only
      one request increments at a time.

    Usage (inside transaction.atomic + select_for_update):
        counter, _ = DailyTokenCounter.objects.select_for_update().get_or_create(
            outlet=outlet, tenant=tenant, date=today, defaults={"value": 0}
        )
        counter.value += 1
        counter.save(update_fields=["value"])
        next_token = counter.value

    Moved here from orders/models.py (Phase 2 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_dailytokencounter (see Meta.db_table below), so this move touched
    zero rows and required zero downtime.
    """
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    date   = models.DateField()
    value  = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "orders_dailytokencounter"
        unique_together = ("outlet", "date")
        # unique_together already creates a (outlet, date) composite index in Postgres.

    def __str__(self):
        return f"Token counter | {self.outlet} | {self.date} = {self.value}"


class TokenOrder(TenantScopedModel):
    """
    Attaches a daily sequential token number to an Order for
    Franchise / Cafe (QSR) tenants.

    One token per order — enforced by OneToOneField.
    Token numbers reset to 1 every business day per outlet.

    is_online=True  → Order came from Zomato/Swiggy/web aggregator.
                      Displayed as "O-{token_number}" on screen and receipts.
    is_online=False → Walk-in counter order.  Displayed as "#{token_number}".

    Moved here from orders/models.py (Phase 2 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_tokenorder (see Meta.db_table below), so this move touched zero
    rows and required zero downtime.
    """
    tenant       = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet       = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    order        = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="token"
    )
    token_number = models.PositiveIntegerField()
    date         = models.DateField()

    # ── Online / Counter split ─────────────────────────────────────────
    # is_online separates the two token series so counter and online
    # numbers never collide and receipts can be visually distinguished.
    is_online = models.BooleanField(
        default=False,
        help_text="True for aggregator (Zomato/Swiggy/web) orders; False for walk-in counter orders.",
    )

    class Meta:
        db_table = "orders_tokenorder"
        # Counter tokens: unique per outlet + token_number + date + is_online=False
        # Online tokens:  unique per outlet + token_number + date + is_online=True
        # The DB constraint covers both because (outlet, token_number, date, is_online) is unique.
        unique_together = ("outlet", "token_number", "date", "is_online")
        indexes = [
            models.Index(fields=["outlet", "date"], name="orders_toke_outlet__635998_idx"),
            models.Index(fields=["outlet", "date", "is_online"], name="orders_toke_outlet__a9e5a6_idx"),
        ]

    @property
    def display_number(self):
        """Human-readable token label: 'O-3' for online, '#3' for counter."""
        return f"O-{self.token_number}" if self.is_online else f"#{self.token_number}"

    def __str__(self):
        return f"Token {self.display_number} | {self.date} | Outlet {self.outlet_id}"


class DailyOnlineTokenCounter(TenantScopedModel):
    """
    Independent per-outlet, per-day counter for ONLINE orders (Zomato / Swiggy / web).

    Mirrors DailyTokenCounter but for the O-series.  Keeping them separate means:
      - Counter tokens (#1, #2 …) and online tokens (O-1, O-2 …) never interfere.
      - A burst of online orders doesn't push walk-in token numbers into the hundreds.
      - Kitchen and cashier can instantly tell walk-in from delivery by the prefix.

    Same row-lock pattern as DailyTokenCounter — never use MAX()+1.

    Moved here from orders/models.py (Phase 2 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_dailyonlinetokencounter (see Meta.db_table below), so this move
    touched zero rows and required zero downtime.
    """
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    date   = models.DateField()
    value  = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "orders_dailyonlinetokencounter"
        unique_together = ("outlet", "date")
        # unique_together already creates a (outlet, date) composite index in Postgres.

    def __str__(self):
        return f"Online token counter | {self.outlet} | {self.date} = {self.value}"
