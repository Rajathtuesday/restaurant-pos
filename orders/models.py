# ============================v2==============================
# orders/models.py
import uuid


from decimal import Decimal, ROUND_HALF_UP
from django.db import models, transaction
from core.models import TenantScopedModel
from orders.exceptions import IssuedBillError  # noqa: F401 -- also imported from here
from orders.services.tax_engine import GST, Line, compute as compute_tax, rows_from_summary
from django.db.models import Q
from django.utils import timezone



# =====================================================
# TABLE
# =====================================================

class Table(TenantScopedModel):

    STATES = (
        ("free", "Free"),
        ("ordering", "Ordering"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("billing", "Billing"),
        ("cleaning", "Cleaning"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    name = models.CharField(max_length=100)

    section = models.CharField(max_length=100, default="Main Hall", blank=True)

    qr_token = models.UUIDField(default=uuid.uuid4, unique=True)

    state = models.CharField(
        max_length=20,
        choices=STATES,
        default="free"
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet"]),
        ]

    def __str__(self):
        return self.name


# =====================================================
# ORDER
# =====================================================


def _is_issued_bill(status, grand_total, tax_summary):
    """A paid or closed bill that has been totalled: an issued tax invoice.
    (One created paid, like an aggregator order, isn't issued until its
    first totalling.)"""
    return status in ("paid", "closed") and (tax_summary is not None or bool(grand_total))


class Order(TenantScopedModel):
    STATUS = (
        ("open", "Open"),
        ("billing", "Billing"),
        ("paid", "Paid"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    )

    SOURCE_CHOICES = (
        ("dine_in",   "Dine In"),
        ("takeaway",  "Takeaway"),
        ("counter",   "Counter / QSR"),   # franchise / cafe token orders
        ("zomato",    "Zomato"),
        ("swiggy",    "Swiggy"),
        ("uber_eats", "Uber Eats"),
        ("web",       "Website"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    table = models.ForeignKey(
        "orders.Table",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="open"
    )

    order_number = models.CharField(
        max_length=30,
        unique=True,
        null=True,
        blank=True
    )

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default="dine_in"
    )

    aggregator_order_id = models.CharField(
        max_length=100,
        null=True,
        blank=True
    )

    customer_name = models.CharField(max_length=100, null=True, blank=True)
    customer_phone = models.CharField(max_length=20, null=True, blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    gst_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # Discount fields
    discount_type = models.CharField(
        max_length=20,
        choices=[("percentage", "Percentage"), ("amount", "Amount")],
        null=True,
        blank=True
    )
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    parcel_surcharge = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0.00"),
        help_text="Extra charge for parcel/takeaway orders. Added to grand_total."
    )
    # The outlet's parcel GST rate, copied onto the bill when the parcel
    # charge is turned on (toggle_parcel), so changing the setting later never
    # changes a bill already made. Empty on bills from before 27 Sep 2026:
    # their parcel charge carried no GST, and re-totalling keeps it that way.
    parcel_gst_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    round_off = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))

    # The bill's tax record, from the tax engine (orders/services/tax_engine.py):
    # taxable value, tax, CGST and SGST per rate, and the tax on each charge.
    # Bills, reports and returns read it; none of them re-does the maths.
    # Empty on bills totalled before 27 Sep 2026 (see tax_rows()).
    tax_summary = models.JSONField(null=True, blank=True)

    # The GST breakdown in its original shape (rate, cgst_rate, sgst_rate,
    # cgst_amount, sgst_amount). Still written next to tax_summary so that a
    # rollback to the release before it shows every bill's breakdown; bills
    # from before tax_summary have only this. tax_summary is the record.
    gst_breakdown_cache = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)



    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet", "status"], name="order_tenant_outlet_status"),
            models.Index(fields=["tenant", "outlet", "created_at"], name="order_tenant_outlet_date"),
            models.Index(fields=["table"], name="order_table"),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "outlet", "table"],
                condition=Q(status="open"),
                name="unique_open_order_per_table"
            ),
            models.UniqueConstraint(
                fields=["outlet", "aggregator_order_id"],
                condition=~Q(aggregator_order_id="") & Q(aggregator_order_id__isnull=False),
                name="unique_aggregator_order_per_outlet"
            )
        ]

    def __str__(self):
        return f"Order {self.order_number or self.id}"

    # -------------------------------------------------
    # SAFE ORDER NUMBER GENERATION
    # For new orders: the entire INSERT + counter increment is wrapped in one
    # atomic block so no concurrent reader ever sees order_number=NULL.
    # For update saves (recalculate_totals, apply_discount …): no transaction
    # wrapper here — the caller's atomic block (if any) is not polluted with
    # extra savepoints on every call.
    # -------------------------------------------------
    def save(self, *args, **kwargs):
        creating = self._state.adding

        if creating and not self.order_number:
            with transaction.atomic():
                super().save(*args, **kwargs)
                self._generate_order_number()
        else:
            super().save(*args, **kwargs)

    def _generate_order_number(self):
        from core.utils import get_business_date
        business_date = get_business_date(self.created_at, self.outlet)
        counter, _ = DailyOrderCounter.objects.select_for_update().get_or_create(
            tenant=self.tenant,
            outlet=self.outlet,
            date=business_date,
            defaults={"value": 0}
        )
        counter.value += 1
        counter.save(update_fields=["value"])
        order_number = f"INV-{self.outlet.id}-{business_date.strftime('%Y%m%d')}-{counter.value:04d}"
        Order.objects.filter(pk=self.pk).update(order_number=order_number)
        self.order_number = order_number

    # -------------------------------------------------
    # UTIL: normalize Decimal to 2 dp
    # -------------------------------------------------
    @staticmethod
    def _quantize(amount):
        if amount is None:
            return Decimal("0.00")
        return (Decimal(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def cgst_total(self):
        from orders.services.tax_service import split_cgst_sgst
        cgst, _ = split_cgst_sgst(self.gst_total)
        return cgst

    @property
    def sgst_total(self):
        from orders.services.tax_service import split_cgst_sgst
        _, sgst = split_cgst_sgst(self.gst_total)
        return sgst

    @property
    def gst_breakdown(self):
        """GST by rate for the bill: the rows with some tax, each as
        {rate, cgst_rate, sgst_rate, cgst_amount, sgst_amount, taxable} in
        Decimals. From the tax record; bills totalled before it existed have
        the breakdown they were saved with (without taxable values).
        """
        if self.tax_summary is not None:
            rows = []
            for row in rows_from_summary(self.tax_summary):
                if row.kind != GST or row.tax <= 0:
                    continue
                half = (row.rate / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                rows.append({
                    "rate": row.rate, "cgst_rate": half, "sgst_rate": half,
                    "cgst_amount": row.cgst, "sgst_amount": row.sgst, "taxable": row.taxable,
                })
            return rows
        return [
            {k: Decimal(v) for k, v in row.items()}
            for row in (self.gst_breakdown_cache or [])
        ]

    def tax_rows(self):
        """The bill's tax by kind and rate (tax_engine.RateRow), 0% rows
        included, as returns and tax reports need it. From the tax record.
        Bills totalled before the record existed are worked out by the same
        engine from their lines, which gives exactly the totals they were
        billed with. Reads self.items.all(), so prefetch items for many bills.
        """
        if self.tax_summary is not None:
            return rows_from_summary(self.tax_summary)
        items = [i for i in self.items.all() if i.status != "voided" and not i.is_complimentary]
        return list(self._run_tax_engine(items).rows)

    @property
    def parcel_tax(self):
        """GST on the parcel charge (inside it when prices include GST), from
        the tax record; part of gst_total already."""
        for charge in (self.tax_summary or {}).get("charges", []):
            if charge["name"] == "parcel":
                return Decimal(charge["tax"])
        return Decimal("0.00")

    # -------------------------------------------------
    # APPLY / CLEAR DISCOUNT (helpers for views / API)
    # -------------------------------------------------
    def apply_discount(self, discount_type: str, discount_value: Decimal):
        """
        discount_type: "percentage" or "amount"
        discount_value: Decimal (percentage like 10.0 for 10% or amount in currency)
        """
        if discount_type not in ("percentage", "amount"):
            raise ValueError("invalid discount type")

        self.discount_type = discount_type
        self.discount_value = self._quantize(discount_value)
        # We MUST save these before recalculate_totals so it picks them up or they are persisted in the final save
        self.save(update_fields=["discount_type", "discount_value"])
        self.recalculate_totals()

    def clear_discount(self):
        self.discount_type = None
        self.discount_value = Decimal("0.00")
        self.discount_total = Decimal("0.00")
        self.recalculate_totals()

    # -------------------------------------------------
    # TOTAL RECALCULATION
    # -------------------------------------------------

    def recalculate_totals(self):
        """
        Total the bill with the tax engine and save the result: the money
        fields, and the tax record (tax_summary) that every bill, report and
        return reads. orders/services/tax_engine.py sets out every rule:
        prices exclude GST (added on top) or include it (worked out from
        inside), the composition scheme collects none, the parcel charge is
        taxed like the food and never discounted, totals are rounded once,
        and the per-rate rows always add up to them.

        A paid or closed bill that has been totalled is an issued tax invoice
        and is never re-totalled: this raises IssuedBillError. (An order that
        arrives already paid, from an aggregator, is totalled once.)
        """
        if self.pk:
            self._refuse_if_issued()

        items = list(self.items.exclude(status="voided").filter(is_complimentary=False))
        bill = self._run_tax_engine(items)

        self.subtotal       = bill.subtotal
        self.discount_total = bill.discount
        self.gst_total      = bill.gst
        self.grand_total    = bill.grand_total
        self.round_off      = bill.round_off
        self.tax_summary    = bill.summary()
        self.gst_breakdown_cache = [
            {"rate": str(row["rate"]), "cgst_rate": str(row["cgst_rate"]), "sgst_rate": str(row["sgst_rate"]),
             "cgst_amount": str(row["cgst_amount"]), "sgst_amount": str(row["sgst_amount"])}
            for row in self.gst_breakdown
        ]
        self.save(update_fields=["subtotal", "gst_total", "discount_total",
                                 "grand_total", "round_off", "discount_type",
                                 "discount_value", "parcel_surcharge",
                                 "tax_summary", "gst_breakdown_cache"])

    def _refuse_if_issued(self):
        """Raise IssuedBillError if this bill is issued, by this copy or by
        the database: a screen can hold a copy read before another screen
        took the payment. Inside a transaction the row is locked while it is
        read, so a payment can't land between this check and the save; every
        screen that changes a bill already runs in one."""
        if _is_issued_bill(self.status, self.grand_total, self.tax_summary):
            raise IssuedBillError(self.pk, self.status)
        rows = Order.objects.filter(pk=self.pk)
        if transaction.get_connection().in_atomic_block:
            rows = rows.select_for_update()
        current = rows.values("status", "grand_total", "tax_summary").first()
        if current and _is_issued_bill(current["status"], current["grand_total"], current["tax_summary"]):
            raise IssuedBillError(self.pk, current["status"])

    def _run_tax_engine(self, items):
        """The engine's result for these (live) lines, this bill's discount
        and parcel charge, and the outlet's GST settings."""
        try:
            prices_include_tax = bool(self.outlet.gst_inclusive)
            composition        = bool(self.outlet.is_composition_scheme)
        except Exception:
            prices_include_tax = composition = False

        lines = [
            Line(amount=item.total_price, rate=item.gst_percentage,
                 item_discount_pct=item.item_discount_pct or Decimal("0"))
            for item in items
        ]
        parcel = self._quantize(self.parcel_surcharge or Decimal("0"))
        if parcel > 0:
            # A bill from before parcel GST has no rate: its charge stays untaxed.
            taxed = self.parcel_gst_rate is not None
            lines.append(Line(amount=parcel, rate=self.parcel_gst_rate if taxed else Decimal("0"),
                              kind=GST if taxed else None, charge="parcel"))

        return compute_tax(
            lines, prices_include_tax=prices_include_tax, composition=composition,
            discount_type=self.discount_type, discount_value=self.discount_value,
        )


# KOTBatch moved to kitchen/models.py (Phase 3 of the orders app split,
# state-only migration -- see orders/migrations/0057_delete_kitchen_models_state_only.py).
# The underlying table is still orders_kotbatch; nothing here changed in the DB.


# =====================================================
# ORDER ITEM
# =====================================================

class OrderItem(models.Model):

    STATUS = (
        ("review", "Needs Approval"),
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("served", "Served"),
        ("voided", "Voided"),
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items"
    )

    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.CASCADE
    )

    quantity = models.PositiveIntegerField(default=1)

    price = models.DecimalField(max_digits=10, decimal_places=2)

    item_discount_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Per-item discount percentage applied at billing"
    )

    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2
    )

    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="pending"
    )

    is_takeaway = models.BooleanField(default=False)


    is_complimentary = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

    @property
    def discounted_price(self):
        if self.is_complimentary:
            return Decimal("0.00")
        if self.item_discount_pct > 0:
            return (self.total_price * (1 - self.item_discount_pct / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self.total_price

    void_reason = models.CharField(max_length=255, null=True, blank=True)

    voided_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    voided_at = models.DateTimeField(null=True, blank=True)

    kot = models.ForeignKey(
        "kitchen.KOTBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="items"
    )

    class Meta:
        # A bill's lines always come back in the order they were added, so
        # every bill, receipt, KOT and screen lists them the same way. Without
        # it they came back in whatever order the database stored them.
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order"],           name="orderitem_order"),
            models.Index(fields=["order", "status"], name="orderitem_order_status"),
            models.Index(fields=["kot"],             name="orderitem_kot"),
        ]

    def __str__(self):
        item_name = self.menu_item.name if self.menu_item else "Unknown Item"
        return f"{item_name} x {self.quantity}"


# =====================================================
# MODIFIERS
# =====================================================

class OrderItemModifier(models.Model):

    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="modifiers"
    )
    
    modifier = models.ForeignKey(
        "menu.Modifier",
        on_delete=models.SET_NULL,
        null=True
    )

    name = models.CharField(max_length=200)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    def __str__(self):
        return self.name


# =====================================================
# PAYMENT
# =====================================================

class Payment(models.Model):

    METHOD_CHOICES = (
        ("cash",      "Cash"),
        ("upi",       "UPI"),
        ("card",      "Card"),
        # Aggregator methods — written by api_ingest_order for pre-paid webhook orders
        ("zomato",    "Zomato"),
        ("swiggy",    "Swiggy"),
        ("uber_eats", "Uber Eats"),
        ("web",       "Website"),
        ("refund",    "Refund"),   # negative-amount entry created on refund approval
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="payments"
    )

    method = models.CharField(
        max_length=20,
        choices=METHOD_CHOICES
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=100,
        null=True,
        blank=True
    )

    paid_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        "accounts.User",
        null=True,
        on_delete=models.SET_NULL
    )

    class Meta:
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["paid_at"],           name="payment_paid_at"),
            models.Index(fields=["order", "method"],   name="payment_order_method"),
        ]
        constraints = [
            # Enforces payment-gateway idempotency at the database level.
            # A gateway (Razorpay, etc.) may retry the same webhook; the view-level
            # `.exists()` check before creating a Payment closes that race in the
            # common case, but is a plain SELECT-then-INSERT with no atomicity
            # guarantee of its own — two near-simultaneous deliveries could both
            # pass the check before either commits. This constraint is the real
            # backstop: the second INSERT fails at the database instead of silently
            # recording a duplicate payment. Scoped to non-blank references only —
            # manual cash/card payments leave `reference` null, and blank ("")
            # values (e.g. an aggregator payment with no aggregator_id) are excluded
            # too, since those are legitimately non-unique.
            models.UniqueConstraint(
                fields=["reference"],
                condition=~models.Q(reference=None) & ~models.Q(reference=""),
                name="unique_nonblank_payment_reference",
            ),
        ]

    def __str__(self):
        return f"{self.method} - {self.amount}"


# RazorpayQRCode and Refund moved to payments/models.py (Phase 6 of the
# orders app split, state-only migration -- see
# orders/migrations/0060_delete_payments_models_state_only.py). The
# underlying tables are still orders_razorpayqrcode / orders_refund;
# nothing here changed in the DB.



# WaiterCall moved to waiter/models.py (Phase 4 of the orders app split,
# state-only migration -- see orders/migrations/0058_delete_waitercall_model_state_only.py).
# The underlying table is still orders_waitercall; nothing here changed in the DB.


# =====================================================
# ORDER EVENTS (PRODUCTION GRADE)
# =====================================================

class OrderEvent(TenantScopedModel):

    EVENT_TYPES = [

        # Order lifecycle
        ("order_created", "Order Created"),
        ("order_cancelled", "Order Cancelled"),

        # Items
        ("item_added", "Item Added"),
        ("item_updated", "Item Updated"),
        ("item_voided", "Item Voided"),
        ("item_discount_applied", "Item Discount Applied"),
        ("item_complimentary", "Item Marked Complimentary"),

        # Discounts
        ("discount_applied", "Discount Applied"),

        # Kitchen
        ("kot_sent", "KOT Sent"),
        ("kitchen_preparing", "Kitchen Preparing"),
        ("kitchen_ready", "Kitchen Ready"),

        # Payments
        ("payment_added", "Payment Added"),
        ("payment_completed", "Payment Completed"),
        ("payment_refund_requested", "Refund Requested"),
        ("payment_refunded", "Payment Refunded"),
        ("payment_refund_rejected", "Refund Rejected"),
        ("razorpay_overpaid_reconciliation", "Razorpay Payment Needs Reconciliation"),
        ("razorpay_amount_mismatch", "Razorpay Webhook Amount Mismatch"),

        # Table actions
        ("table_transferred", "Table Transferred"),
        ("tables_merged", "Tables Merged"),
        ("tables_unmerged", "Tables Unmerged"),

        # System
        ("status_changed", "Status Changed"),
    ]

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="events"
    )

    event_type = models.CharField(
        max_length=50,
        choices=EVENT_TYPES
    )

    # 🔥 WHAT CHANGED (STRUCTURED)
    metadata = models.JSONField(blank=True, null=True)

    # 🔥 FINANCIAL TRACKING (IMPORTANT)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )

    # 🔥 STATE SNAPSHOT (CRITICAL)
    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField(null=True, blank=True)

    created_by = models.ForeignKey(
        "accounts.User",
        null=True,
        on_delete=models.SET_NULL
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["order", "event_type"], name="orderevent_order_type"),
            models.Index(fields=["event_type"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["tenant", "outlet", "event_type", "created_at"],
                         name="orderevent_tenant_type_idx"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} - Order {self.order.id}"

# =====================================================
# ORDER LOCK
# =====================================================

class OrderLock(models.Model):

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="lock"
    )

    locked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE
    )

    locked_at = models.DateTimeField(auto_now_add=True)

    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"]),
        ]

    def is_expired(self):
        return self.expires_at < timezone.now()

    def __str__(self):
        return f"Order {self.order.id} locked by {self.locked_by}"


# DailyKOTCounter moved to kitchen/models.py (Phase 3 of the orders app
# split, state-only migration). The underlying table is still
# orders_dailykotcounter; nothing here changed in the DB.


# =====================================================
# DAILY ORDER COUNTER
# =====================================================

class DailyOrderCounter(TenantScopedModel):
    """
    Per-tenant, per-outlet, per-day sequential counter for invoice numbers.

    Mirrors DailyKOTCounter. Using a dedicated counter row (vs. relying on the
    global Order PK) means:
      - Order numbers are sequential within each outlet's day, matching
        what accountants expect (INV-20250507-0001 ... INV-20250507-0042).
      - No cross-tenant PK gaps leak onto customer bills.
      - Zero-padding never overflows (4 digits -> 9999 orders/outlet/day).
    """

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    date = models.DateField()
    value = models.IntegerField(default=0)

    class Meta:
        unique_together = ("tenant", "outlet", "date")

    def __str__(self):
        return f"{self.tenant} | {self.outlet} | {self.date} -> {self.value}"
    

# TableMerge moved to tablemerge/models.py (Phase 5 of the orders app split,
# state-only migration -- see orders/migrations/0059_delete_tablemerge_model_state_only.py).
# The underlying tables are still orders_tablemerge / orders_tablemerge_tables;
# nothing here changed in the DB.


# KitchenMessage moved to kitchen/models.py (Phase 3 of the orders app
# split, state-only migration). The underlying table is still
# orders_kitchenmessage; nothing here changed in the DB.


# Promo moved to promos/models.py (Phase 0 of the orders app split, state-only
# migration -- see orders/migrations/0053_delete_promo_state_only.py). The
# underlying table is still orders_promo; nothing here changed in the DB.

# DailyTokenCounter, TokenOrder, and DailyOnlineTokenCounter moved to
# tokens/models.py (Phase 2 of the orders app split, state-only migration --
# see orders/migrations/0055_delete_token_models_state_only.py). The
# underlying tables are unchanged; nothing here changed in the DB.

# PrintJob moved to printing/models.py (Phase 1 of the orders app split,
# state-only migration -- see orders/migrations/0054_delete_printjob_state_only.py).
# The underlying table is still orders_printjob; nothing here changed in the DB.


