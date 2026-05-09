# ============================v2==============================
# orders/models.py
import uuid


from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone



# =====================================================
# TABLE
# =====================================================

class Table(models.Model):

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


class Order(models.Model):
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
        max_length=20,
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

    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    round_off = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)



    class Meta:
        indexes = [
            models.Index(fields=["tenant"]),
            models.Index(fields=["outlet"]),
            models.Index(fields=["table"]),
            models.Index(fields=["status"]),
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
    # order_number is set immediately after the INSERT in the same
    # atomic block — no concurrent reader can see order_number=NULL.
    # -------------------------------------------------
    @transaction.atomic
    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)

        if creating and not self.order_number:
            # Generate a per-tenant, per-outlet, per-day sequential invoice number.
            # Format: INV-YYYYMMDD-NNNN (e.g. INV-20250507-0012)
            
            from core.utils import get_business_date
            
            # Use the business date based on when the order was opened
            business_date = get_business_date(self.created_at, self.outlet)

            # 2. Get or create the counter for this business date
            counter, _ = DailyOrderCounter.objects.select_for_update().get_or_create(
                tenant=self.tenant,
                outlet=self.outlet,
                date=business_date,
                defaults={"value": 0}
            )
            counter.value += 1
            counter.save(update_fields=["value"])

            # 3. Format and update the order number
            order_number = f"INV-{business_date.strftime('%Y%m%d')}-{counter.value:04d}"
            Order.objects.filter(pk=self.pk).update(order_number=order_number)
            self.order_number = order_number  # keep in-memory object consistent

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
        """Returns GST grouped by rate — required for GST-compliant bills."""
        from collections import defaultdict
        breakdown = defaultdict(Decimal)

        items = list(self.items.exclude(status="voided").filter(is_complimentary=False))
        
        # Calculate subtotal after item discounts to determine order discount factor
        subtotal_after_item_discounts = Decimal("0.00")
        for item in items:
            item_base = item.total_price
            if getattr(item, 'item_discount_pct', Decimal("0.00")) > 0:
                item_base = item_base * (1 - item.item_discount_pct / Decimal("100"))
            subtotal_after_item_discounts += item_base
            
        order_discount_total = Decimal("0.00")
        if self.discount_type == "percentage" and (self.discount_value or 0) > 0:
            order_discount_total = subtotal_after_item_discounts * (Decimal(self.discount_value) / Decimal("100"))
        elif self.discount_type == "amount" and (self.discount_value or 0) > 0:
            order_discount_total = Decimal(str(self.discount_value))
            
        if subtotal_after_item_discounts > 0:
            order_discount_factor = max(Decimal("0.0"), (subtotal_after_item_discounts - order_discount_total) / subtotal_after_item_discounts)
        else:
            order_discount_factor = Decimal("1.0")

        for item in items:
            rate = item.gst_percentage
            item_base = item.total_price
            if getattr(item, 'item_discount_pct', Decimal("0.00")) > 0:
                item_base = item_base * (1 - item.item_discount_pct / Decimal("100"))
            
            item_taxable = item_base * order_discount_factor
            item_gst = (item_taxable * rate / Decimal("100")).quantize(Decimal("0.01"))
            breakdown[rate] += item_gst

        return [
            {
                "rate": rate,
                "cgst_rate": (rate / 2).quantize(Decimal("0.01")),
                "sgst_rate": (rate / 2).quantize(Decimal("0.01")),
                "cgst_amount": (amount / 2).quantize(Decimal("0.01")),
                "sgst_amount": (amount / 2).quantize(Decimal("0.01")),
            }
            for rate, amount in sorted(breakdown.items())
            if amount > 0
        ]

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
        # Fetch items once to avoid multiple queries / N+1 issues
        items = list(self.items.exclude(status="voided").filter(is_complimentary=False))
        
        # 1. Calculate Gross Subtotal
        raw_subtotal = sum((item.total_price for item in items), Decimal("0.0"))
        subtotal = self._quantize(raw_subtotal)

        # 2. Item Discounts
        item_discount_total = Decimal("0.00")
        subtotal_after_item_discounts = Decimal("0.00")
        for item in items:
            item_base = item.total_price
            if getattr(item, 'item_discount_pct', Decimal("0.00")) > 0:
                item_discount = item_base * (item.item_discount_pct / Decimal("100"))
                item_discount_total += item_discount
                item_base = item_base - item_discount
            subtotal_after_item_discounts += item_base

        # 3. Order Discounts
        order_discount_total = Decimal("0.00")
        if self.discount_type == "percentage" and (self.discount_value or 0) > 0:
            order_discount_total = subtotal_after_item_discounts * (Decimal(self.discount_value) / Decimal("100"))
        elif self.discount_type == "amount" and (self.discount_value or 0) > 0:
            order_discount_total = Decimal(str(self.discount_value))
        
        discount_total = self._quantize(item_discount_total + order_discount_total)
        if discount_total > subtotal:
            discount_total = subtotal

        # 4. Taxable Amount (Post-Discount)
        taxable_amount = subtotal - discount_total
        
        # 5. GST
        gst_total = Decimal("0.00")
        if subtotal_after_item_discounts > 0:
            order_discount_factor = max(Decimal("0.0"), (subtotal_after_item_discounts - order_discount_total) / subtotal_after_item_discounts)
        else:
            order_discount_factor = Decimal("1.0")

        for item in items:
            item_base = item.total_price
            if getattr(item, 'item_discount_pct', Decimal("0.00")) > 0:
                item_base = item_base * (1 - item.item_discount_pct / Decimal("100"))
            
            item_taxable = item_base * order_discount_factor
            item_gst = (item_taxable * item.gst_percentage) / Decimal("100.0")
            gst_total += item_gst
        
        gst_total = self._quantize(gst_total)
        
        # Rounding to nearest integer for grand_total
        final_total = self._quantize(taxable_amount + gst_total)
        rounded_total = final_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        round_off = rounded_total - final_total

        self.subtotal = subtotal
        self.gst_total = gst_total
        self.discount_total = discount_total    
        self.grand_total = rounded_total
        self.round_off = round_off

        self.save(update_fields=["subtotal", "gst_total", "discount_total", "grand_total", "round_off", "discount_type", "discount_value"])
# =====================================================
# TOKEN ORDER
# =====================================================

class TokenOrder(models.Model):
    """
    Used for Franchise and Cafe tenants. Instead of assigning an order to a table,
    they get a daily sequential token number.
    """
    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)
    outlet = models.ForeignKey('tenants.Outlet', on_delete=models.CASCADE)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="token")
    token_number = models.PositiveIntegerField()
    date = models.DateField(auto_now_add=True)

    class Meta:
        unique_together = ['outlet', 'token_number', 'date']
        indexes = [
            models.Index(fields=["outlet", "date"]),
        ]

    def __str__(self):
        return f"Token {self.token_number} - {self.date}"

# =====================================================
# KOT
# =====================================================

class KOTBatch(models.Model):

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
            models.Index(fields=["status"]),
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
        ("cash", "Cash"),
        ("upi", "UPI"),
        ("card", "Card"),
        ("refund", "Refund"),  # negative-amount entry created on refund approval
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
        ]

    def __str__(self):
        return f"{self.method} - {self.amount}"


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

    reason = models.CharField(max_length=255)

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

class WaiterCall(models.Model):

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

class OrderEvent(models.Model):

    EVENT_TYPES = [

        # Order lifecycle
        ("order_created", "Order Created"),
        ("order_cancelled", "Order Cancelled"),

        # Items
        ("item_added", "Item Added"),
        ("item_updated", "Item Updated"),
        ("item_voided", "Item Voided"),

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
            models.Index(fields=["event_type"]),
            models.Index(fields=["created_at"]),
            # Composite index for the bypass daily-limit counter query which filters
            # on (tenant, outlet, created_by, event_type, created_at__gte).
            # Without this, the query does a full table scan at high event volumes.
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

class DailyKOTCounter(models.Model):

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

class DailyOrderCounter(models.Model):
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


class TableMerge(models.Model):

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

class KitchenMessage(models.Model):

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

class Promo(models.Model):
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
        """Atomically increments usage_count."""
        Promo.objects.filter(pk=self.pk).update(usage_count=models.F("usage_count") + 1)


# =====================================================
# DAILY TOKEN COUNTER
# =====================================================

class DailyTokenCounter(models.Model):
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
        indexes = [
            models.Index(fields=["outlet", "date"]),
        ]

    def __str__(self):
        return f"Token counter | {self.outlet} | {self.date} = {self.value}"


# =====================================================
# TOKEN ORDER
# =====================================================

class TokenOrder(models.Model):
    """
    Attaches a daily sequential token number to an Order for
    Franchise / Cafe (QSR) tenants.

    One token per order — enforced by OneToOneField.
    Token numbers reset to 1 every business day per outlet.
    """
    tenant       = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet       = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    order        = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="token"
    )
    token_number = models.PositiveIntegerField()
    date         = models.DateField()

    class Meta:
        unique_together = ("outlet", "token_number", "date")
        indexes = [
            models.Index(fields=["outlet", "date"]),
        ]

    def __str__(self):
        return f"Token #{self.token_number} | {self.date} | Outlet {self.outlet_id}"

