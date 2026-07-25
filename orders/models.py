# ============================v2==============================
# orders/models.py
import uuid


from decimal import Decimal, ROUND_HALF_UP
from django.db import models, transaction
from core.models import TenantScopedModel
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

    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    round_off = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))

    # Cached GST breakdown populated by recalculate_totals() — avoids per-call DB queries and
    # logic duplication between the property and _recalculate_* methods.
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
        return (self.gst_total / Decimal("2.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
    @property
    def sgst_total(self):
        return self.gst_total - self.cgst_total

    @property
    def gst_breakdown(self):
        """GST grouped by rate, read from the pre-computed cache.

        Populated by recalculate_totals() — zero DB queries at bill render time.
        Returns Decimal values for consistent arithmetic downstream.
        """
        return [
            {k: Decimal(v) for k, v in row.items()}
            for row in (self.gst_breakdown_cache or [])
        ]

    def _build_gst_breakdown_data(self, items, order_discount_factor, gst_inclusive):
        """Compute GST breakdown given already-calculated order_discount_factor.

        Called from _recalculate_exclusive / _recalculate_inclusive so the
        discount factor is never recomputed a second time.
        Returns a JSON-serialisable list[dict[str]] ready for gst_breakdown_cache.
        """
        from collections import defaultdict
        breakdown = defaultdict(Decimal)

        for item in items:
            rate = item.gst_percentage
            item_base = item.total_price
            if getattr(item, "item_discount_pct", Decimal("0.00")) > 0:
                item_base = item_base * (1 - item.item_discount_pct / Decimal("100"))
            item_taxable = item_base * order_discount_factor
            if gst_inclusive:
                item_gst = (item_taxable * rate / (Decimal("100") + rate)).quantize(Decimal("0.01")) if rate > 0 else Decimal("0.00")
            else:
                item_gst = (item_taxable * rate / Decimal("100")).quantize(Decimal("0.01"))
            breakdown[rate] += item_gst

        result = []
        for rate, amount in sorted(breakdown.items()):
            if amount <= 0:
                continue
            half_rate = (rate / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            cgst_amt  = (amount / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            sgst_amt  = amount - cgst_amt   # ensures CGST + SGST == amount exactly
            result.append({
                "rate":        str(rate),
                "cgst_rate":   str(half_rate),
                "sgst_rate":   str(half_rate),
                "cgst_amount": str(cgst_amt),
                "sgst_amount": str(sgst_amt),
            })
        return result

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
        Recalculate order totals supporting two GST modes:

        EXCLUSIVE (default, gst_inclusive=False):
            item.price = base price.  GST added on top.
            grand_total = subtotal - discounts + gst

        INCLUSIVE (gst_inclusive=True):
            item.price = final customer price (GST already inside).
            GST is back-calculated: gst = price × rate / (100 + rate)
            grand_total = inclusive_price - discounts (GST already inside)
            subtotal field stores the back-calculated base (excl. GST).
        """
        items = list(self.items.exclude(status="voided").filter(is_complimentary=False))

        # Detect mode — safe fallback if outlet not loaded
        try:
            gst_inclusive    = bool(self.outlet.gst_inclusive)
            is_composition   = bool(self.outlet.is_composition_scheme)
        except Exception:
            gst_inclusive  = False
            is_composition = False

        if gst_inclusive:
            self._recalculate_inclusive(items, is_composition=is_composition)
        else:
            self._recalculate_exclusive(items, is_composition=is_composition)

    # ------------------------------------------------------------------
    # EXCLUSIVE MODE  (GST added on top — existing behaviour)
    # ------------------------------------------------------------------

    def _recalculate_exclusive(self, items, is_composition=False):
        raw_subtotal = sum((item.total_price for item in items), Decimal("0.0"))
        subtotal = self._quantize(raw_subtotal)

        item_discount_total = Decimal("0.00")
        subtotal_after_item_discounts = Decimal("0.00")
        for item in items:
            item_base = item.total_price
            if getattr(item, "item_discount_pct", Decimal("0.00")) > 0:
                item_discount = item_base * (item.item_discount_pct / Decimal("100"))
                item_discount_total += item_discount
                item_base -= item_discount
            subtotal_after_item_discounts += item_base

        order_discount_total = Decimal("0.00")
        if self.discount_type == "percentage" and (self.discount_value or 0) > 0:
            order_discount_total = subtotal_after_item_discounts * (Decimal(self.discount_value) / Decimal("100"))
        elif self.discount_type == "amount" and (self.discount_value or 0) > 0:
            order_discount_total = Decimal(str(self.discount_value))

        discount_total = self._quantize(item_discount_total + order_discount_total)
        if discount_total > subtotal:
            discount_total = subtotal

        taxable_amount = subtotal - discount_total

        if subtotal_after_item_discounts > 0:
            order_discount_factor = max(
                Decimal("0.0"),
                (subtotal_after_item_discounts - order_discount_total) / subtotal_after_item_discounts,
            )
        else:
            order_discount_factor = Decimal("1.0")

        gst_total = Decimal("0.00")
        if not is_composition:
            for item in items:
                item_base = item.total_price
                if getattr(item, "item_discount_pct", Decimal("0.00")) > 0:
                    item_base = item_base * (1 - item.item_discount_pct / Decimal("100"))
                item_taxable = item_base * order_discount_factor
                gst_total += (item_taxable * item.gst_percentage) / Decimal("100.0")

        gst_total = self._quantize(gst_total)
        final_total = self._quantize(taxable_amount + gst_total)
        rounded_total = final_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        parcel = self._quantize(self.parcel_surcharge or Decimal("0"))
        rounded_total = (final_total + parcel).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        self.subtotal           = subtotal
        self.gst_total          = gst_total
        self.discount_total     = discount_total
        self.grand_total        = rounded_total
        self.round_off          = rounded_total - final_total - parcel
        self.gst_breakdown_cache = self._build_gst_breakdown_data(items, order_discount_factor, False)
        self.save(update_fields=["subtotal", "gst_total", "discount_total",
                                 "grand_total", "round_off", "discount_type",
                                 "discount_value", "parcel_surcharge",
                                 "gst_breakdown_cache"])

    # ------------------------------------------------------------------
    # INCLUSIVE MODE  (GST back-calculated from the price)
    # ------------------------------------------------------------------

    def _recalculate_inclusive(self, items, is_composition=False):
        """
        item.total_price is the CUSTOMER-FACING price (GST inside).
        We back-calculate: gst = amount × rate / (100 + rate)
        Discounts are applied to the inclusive price first, then GST
        is back-calculated from the discounted inclusive amounts.

        Result:
          subtotal   = back-calculated base (excl. GST)
          gst_total  = back-calculated GST
          grand_total = inclusive_total - discounts  (what customer pays)
          subtotal + gst_total ≡ grand_total  ← always true
        """
        raw_inclusive = sum((item.total_price for item in items), Decimal("0.0"))

        # Item-level discounts on inclusive price
        item_discount_total = Decimal("0.00")
        inclusive_after_item_disc = Decimal("0.00")
        for item in items:
            inc = item.total_price
            if getattr(item, "item_discount_pct", Decimal("0.00")) > 0:
                item_disc = inc * (item.item_discount_pct / Decimal("100"))
                item_discount_total += item_disc
                inc -= item_disc
            inclusive_after_item_disc += inc

        # Order-level discount on inclusive price
        order_discount_total = Decimal("0.00")
        if self.discount_type == "percentage" and (self.discount_value or 0) > 0:
            order_discount_total = inclusive_after_item_disc * (Decimal(self.discount_value) / Decimal("100"))
        elif self.discount_type == "amount" and (self.discount_value or 0) > 0:
            order_discount_total = Decimal(str(self.discount_value))

        discount_total = self._quantize(item_discount_total + order_discount_total)
        if discount_total > raw_inclusive:
            discount_total = raw_inclusive

        grand_after_discount = self._quantize(raw_inclusive - discount_total)

        # Proportional discount factor for back-calculation
        if inclusive_after_item_disc > 0:
            order_discount_factor = max(
                Decimal("0.0"),
                (inclusive_after_item_disc - order_discount_total) / inclusive_after_item_disc,
            )
        else:
            order_discount_factor = Decimal("1.0")

        # Back-calculate GST from each item's discounted inclusive amount
        gst_total = Decimal("0.00")
        if not is_composition:
            for item in items:
                inc = item.total_price
                if getattr(item, "item_discount_pct", Decimal("0.00")) > 0:
                    inc = inc * (1 - item.item_discount_pct / Decimal("100"))
                inc_discounted = inc * order_discount_factor
                rate = item.gst_percentage
                if rate > 0:
                    gst_total += inc_discounted * rate / (Decimal("100") + rate)

        gst_total  = self._quantize(gst_total)
        subtotal   = self._quantize(grand_after_discount - gst_total)   # base excl. GST
        rounded_total = grand_after_discount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        parcel = self._quantize(self.parcel_surcharge or Decimal("0"))
        rounded_total = (grand_after_discount + parcel).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        self.subtotal            = subtotal
        self.gst_total           = gst_total
        self.discount_total      = discount_total
        self.grand_total         = rounded_total
        self.round_off           = rounded_total - grand_after_discount - parcel
        self.gst_breakdown_cache = self._build_gst_breakdown_data(items, order_discount_factor, True)
        self.save(update_fields=["subtotal", "gst_total", "discount_total",
                                 "grand_total", "round_off", "discount_type",
                                 "discount_value", "parcel_surcharge",
                                 "gst_breakdown_cache"])


# =====================================================
# KOT
# =====================================================

class KOTBatch(TenantScopedModel):

    STATUS = (
        ("confirmed", "Confirmed"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="kots"
    )

    kot_number = models.IntegerField()

    station = models.ForeignKey(
        "setup.KitchenStation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kots"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="confirmed"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("order", "kot_number")
        indexes = [
            models.Index(fields=["tenant", "outlet"]),
            models.Index(fields=["tenant", "outlet", "status"], name="kotbatch_tenant_outlet_status"),
        ]

    def __str__(self):
        return f"KOT {self.kot_number}"


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
        KOTBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="items"
    )

    class Meta:
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


# =====================================================
# RAZORPAY QR CODE — tracking row per generated QR
# =====================================================

class RazorpayQRCode(TenantScopedModel):
    """
    Records "QR X was generated for order Y, expecting amount Z, expires at T."

    Without this, the webhook can't validate a payment's amount against what
    was actually quoted (the order may have changed since the QR was shown),
    and the bill UI has no way to show "still pending" across a page reload.
    """
    STATUS_CHOICES = (
        ("active", "Active"),
        ("paid", "Paid"),
        ("expired", "Expired"),
        ("closed", "Closed"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="razorpay_qr_codes")

    qr_code_id = models.CharField(max_length=100, unique=True)
    image_url = models.URLField()
    quoted_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Razorpay QR {self.qr_code_id} - order #{self.order_id} - {self.status}"


# =====================================================
# REFUND
# =====================================================

class Refund(models.Model):

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    )

    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="refunds"
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="refunds"
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)

    customer_complaint = models.CharField(
        max_length=500, blank=True, default="",
        help_text="What the customer said — visible to owner"
    )

    reason = models.CharField(
        max_length=255,
        help_text="Manager's internal note for this refund"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )

    refunded_by = models.ForeignKey(
        "accounts.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="refunds_issued"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["payment"]),
        ]

    def __str__(self):
        return f"Refund ₹{self.amount} for Order {self.order_id}"



# =====================================================
# WAITER CALL
# =====================================================

class WaiterCall(TenantScopedModel):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    table = models.ForeignKey(Table, on_delete=models.CASCADE)

    is_resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["table"],
                condition=Q(is_resolved=False),
                name="one_active_waiter_call_per_table"
            )
        ]

    def __str__(self):
        return f"Waiter Call - {self.table.name}"


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


# =====================================================
# DAILY KOT COUNTER
# =====================================================

class DailyKOTCounter(TenantScopedModel):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    date = models.DateField()

    value = models.IntegerField(default=0)

    class Meta:
        unique_together = ("tenant", "outlet", "date")

    def __str__(self):
        return f"{self.date} -> {self.value}"


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
    

# =====================================================
# TABLE MERGE (for future feature)
# =====================================================


class TableMerge(TenantScopedModel):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    primary_table = models.ForeignKey(
        "Table",
        on_delete=models.CASCADE,
        related_name="merged_primary"
    )

    tables = models.ManyToManyField("Table")

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet", "is_active"]),
        ]

    def __str__(self):
        return f"Merge on {self.primary_table.name} ({self.tables.count()} tables)"


# =====================================================
# KITCHEN MESSAGE
# =====================================================

class KitchenMessage(TenantScopedModel):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="kitchen_messages"
    )

    message = models.CharField(max_length=255)

    is_resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet", "is_resolved"]),
        ]

    def __str__(self):
        return f"Message for {self.order.table.name if self.order.table else 'Walk-in'}: {self.message}"


# ---------------------------------------------------------------------------
# PROMO — outlet-level promotional discounts pickable from the bill screen
# ---------------------------------------------------------------------------

class Promo(TenantScopedModel):
    """
    Tenant/outlet-scoped promotional discount applied to any order.
    - outlet=NULL  → promo runs across ALL outlets of this tenant
    - outlet set   → outlet-specific only
    Supports usage caps, min-order validation, and date-range gating.
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
        ordering = ["name"]
        indexes  = [
            models.Index(fields=["tenant", "outlet", "is_active"]),
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


# =====================================================
# DAILY TOKEN COUNTER
# =====================================================

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
    """
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    date   = models.DateField()
    value  = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("outlet", "date")
        # unique_together already creates a (outlet, date) composite index in Postgres.

    def __str__(self):
        return f"Token counter | {self.outlet} | {self.date} = {self.value}"


# =====================================================
# TOKEN ORDER
# =====================================================

class TokenOrder(TenantScopedModel):
    """
    Attaches a daily sequential token number to an Order for
    Franchise / Cafe (QSR) tenants.

    One token per order — enforced by OneToOneField.
    Token numbers reset to 1 every business day per outlet.

    is_online=True  → Order came from Zomato/Swiggy/web aggregator.
                      Displayed as "O-{token_number}" on screen and receipts.
    is_online=False → Walk-in counter order.  Displayed as "#{token_number}".
    """
    tenant       = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet       = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    order        = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="token"
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
        # Counter tokens: unique per outlet + token_number + date + is_online=False
        # Online tokens:  unique per outlet + token_number + date + is_online=True
        # The DB constraint covers both because (outlet, token_number, date, is_online) is unique.
        unique_together = ("outlet", "token_number", "date", "is_online")
        indexes = [
            models.Index(fields=["outlet", "date"]),
            models.Index(fields=["outlet", "date", "is_online"]),
        ]

    @property
    def display_number(self):
        """Human-readable token label: 'O-3' for online, '#3' for counter."""
        return f"O-{self.token_number}" if self.is_online else f"#{self.token_number}"

    def __str__(self):
        return f"Token {self.display_number} | {self.date} | Outlet {self.outlet_id}"


# =====================================================
# DAILY ONLINE TOKEN COUNTER
# =====================================================

class DailyOnlineTokenCounter(TenantScopedModel):
    """
    Independent per-outlet, per-day counter for ONLINE orders (Zomato / Swiggy / web).

    Mirrors DailyTokenCounter but for the O-series.  Keeping them separate means:
      - Counter tokens (#1, #2 …) and online tokens (O-1, O-2 …) never interfere.
      - A burst of online orders doesn't push walk-in token numbers into the hundreds.
      - Kitchen and cashier can instantly tell walk-in from delivery by the prefix.

    Same row-lock pattern as DailyTokenCounter — never use MAX()+1.
    """
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    date   = models.DateField()
    value  = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("outlet", "date")
        # unique_together already creates a (outlet, date) composite index in Postgres.

    def __str__(self):
        return f"Online token counter | {self.outlet} | {self.date} = {self.value}"


# =====================================================
# PRINT JOB QUEUE
# =====================================================

class PrintJob(TenantScopedModel):
    """
    Queued receipt/KOT print job consumed by the Rasova Agent in polling mode.

    The browser pushes a job here (HTTPS POST to EC2).
    The agent running on the local device polls /orders/agent/<key>/jobs/ every 2 s,
    prints to the local printer over TCP 9100, then marks the job done.

    This architecture means the agent never needs an open port or inbound connection —
    it only makes outbound HTTP calls, so Android battery optimisers and NAT routers
    cannot break the connection.
    """

    PENDING    = "pending"
    PROCESSING = "processing"
    DONE       = "done"
    FAILED     = "failed"
    STATUS_CHOICES = [
        (PENDING,    "Pending"),
        (PROCESSING, "Processing"),
        (DONE,       "Done"),
        (FAILED,     "Failed"),
    ]

    tenant     = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet     = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    # Full payload the agent needs: lines (ESC/POS), network_host, network_port, encoding
    payload    = models.JSONField()
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at    = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    error_msg  = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        ordering = ["created_at"]
        indexes  = [models.Index(fields=["tenant", "outlet", "status", "created_at"])]

    def __str__(self):
        return f"PrintJob #{self.pk} [{self.status}] outlet={self.outlet_id}"

    # ── Redis poll-gating keys ────────────────────────────────────────────────
    # The agent poll endpoint checks a lightweight per-outlet Redis flag before
    # running the expensive claim transaction, so an idle poll costs one Redis
    # read instead of 2-3 Postgres queries.
    @staticmethod
    def pending_flag_key(outlet_id):
        return f"printq:pending:{outlet_id}"

    @staticmethod
    def sweep_key(outlet_id):
        return f"printq:swept:{outlet_id}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        # Arm the per-outlet "has pending work" hint whenever a brand-new job is
        # queued as PENDING. Setting it AFTER super().save() means the row is
        # committed/visible before the flag is set — which is what makes the
        # poll's delete-before-read safe (no job is ever left with a cleared flag).
        # Over-arming is harmless (a false positive just costs one wasted poll).
        # A cache failure must never block creating a print job; the poll's
        # periodic safety sweep still delivers the job if the flag is lost.
        if is_new and self.status == self.PENDING:
            try:
                from django.core.cache import cache
                cache.set(self.pending_flag_key(self.outlet_id), 1, timeout=3600)
            except Exception:
                pass


