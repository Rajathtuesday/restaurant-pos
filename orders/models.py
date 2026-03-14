# # orders/models.py
# import uuid
# from decimal import Decimal

# from django.db import models
# from django.db.models import Q


# class Table(models.Model):

#     tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)
#     outlet = models.ForeignKey('tenants.Outlet', on_delete=models.CASCADE)

#     name = models.CharField(max_length=100)
#     qr_token = models.UUIDField(default=uuid.uuid4, unique=True)

#     is_active = models.BooleanField(default=True)

#     def __str__(self):
#         return self.name


# class Order(models.Model):

#     STATUS_CHOICES = (
#         ('open', 'Open'),
#         ('confirmed', 'Confirmed'),
#         ('preparing', 'Preparing'),
#         ('ready', 'Ready'),
#         ('paid', 'Paid'),
#         ('cancelled', 'Cancelled'),
#     )

#     tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)
#     outlet = models.ForeignKey('tenants.Outlet', on_delete=models.CASCADE)

#     table = models.ForeignKey(
#         Table,
#         on_delete=models.SET_NULL,
#         null=True,
#         blank=True
#     )

#     created_by = models.ForeignKey(
#         'accounts.User',
#         on_delete=models.SET_NULL,
#         null=True
#     )

#     status = models.CharField(
#         max_length=20,
#         choices=STATUS_CHOICES,
#         default='open'
#     )

#     subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
#     gst_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
#     grand_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return f"Order {self.id}"

#     def recalculate_totals(self):

#         items = self.items.all()

#         subtotal = sum(i.price * i.quantity for i in items)

#         gst = sum(
#             (i.price * i.quantity * i.gst_percentage) / Decimal("100")
#             for i in items
#         )

#         self.subtotal = subtotal
#         self.gst_total = gst
#         self.grand_total = subtotal + gst

#         self.save(update_fields=["subtotal", "gst_total", "grand_total"])

#     class Meta:

#         indexes = [

#             models.Index(fields=["tenant"]),
#             models.Index(fields=["outlet"]),
#             models.Index(fields=["table"]),
#             models.Index(fields=["status"]),
#             models.Index(fields=["created_at"]),

#             # PERFORMANCE INDEX
#             models.Index(fields=["tenant", "outlet", "status"]),

#         ]

#         constraints = [

#             models.UniqueConstraint(
#                 fields=["tenant", "outlet", "table"],
#                 condition=Q(status="open"),
#                 name="unique_open_order_per_table"
#             )

#         ]

# class KOTBatch(models.Model):

#     tenant = models.ForeignKey(
#         "tenants.Tenant",
#         on_delete=models.CASCADE
#     )

#     outlet = models.ForeignKey(
#         "tenants.Outlet",
#         on_delete=models.CASCADE
#     )

#     order = models.ForeignKey(
#         Order,
#         on_delete=models.CASCADE,
#         related_name="kots"
#     )

#     kot_number = models.IntegerField()

#     # optional future feature
#     station = models.CharField(
#         max_length=50,
#         null=True,
#         blank=True
#     )

#     status = models.CharField(
#         max_length=20,
#         choices=[
#             ("confirmed", "Confirmed"),
#             ("preparing", "Preparing"),
#             ("ready", "Ready")
#         ],
#         default="confirmed"
#     )

#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return f"KOT {self.kot_number}"

#     class Meta:

#         indexes = [

#             models.Index(fields=["tenant", "outlet"]),
#             models.Index(fields=["status", "created_at"]),
#             models.Index(fields=["kot_number"]),

#             # kitchen screen performance
#             models.Index(fields=["tenant", "outlet", "status"]),
#         ]

# class OrderItem(models.Model):

#     order = models.ForeignKey(
#         Order,
#         on_delete=models.CASCADE,
#         related_name="items"
#     )

#     menu_item = models.ForeignKey(
#         'menu.MenuItem',
#         on_delete=models.CASCADE
#     )

#     quantity = models.IntegerField(default=1)

#     price = models.DecimalField(max_digits=10, decimal_places=2)

#     gst_percentage = models.DecimalField(
#         max_digits=5,
#         decimal_places=2
#     )

#     total_price = models.DecimalField(
#         max_digits=10,
#         decimal_places=2
#     )

#     kot = models.ForeignKey(
#         KOTBatch,
#         null=True,
#         blank=True,
#         on_delete=models.SET_NULL,
#         related_name="items"
#     )

#     def __str__(self):
#         return f"{self.menu_item.name} x {self.quantity}"

# class OrderItemModifier(models.Model):

#     order_item = models.ForeignKey(
#         OrderItem,
#         on_delete=models.CASCADE,
#         related_name="modifiers"
#     )

#     modifier = models.ForeignKey(
#         "menu.Modifier",
#         on_delete=models.CASCADE
#     )

#     # snapshot (important if modifier price changes later)
#     name = models.CharField(max_length=200)

#     price = models.DecimalField(
#         max_digits=10,
#         decimal_places=2,
#         default=0
#     )

#     def __str__(self):
#         return f"{self.name} ({self.price})"

# class Payment(models.Model):

#     METHOD_CHOICES = (
#         ('cash', 'Cash'),
#         ('upi', 'UPI'),
#         ('card', 'Card'),
#     )

#     order = models.OneToOneField(
#         Order,
#         on_delete=models.CASCADE
#     )

#     method = models.CharField(
#         max_length=20,
#         choices=METHOD_CHOICES
#     )

#     amount = models.DecimalField(
#         max_digits=10,
#         decimal_places=2
#     )

#     paid_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return f"{self.method} - {self.amount}"


# class WaiterCall(models.Model):

#     tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)
#     outlet = models.ForeignKey('tenants.Outlet', on_delete=models.CASCADE)

#     table = models.ForeignKey(Table, on_delete=models.CASCADE)

#     is_resolved = models.BooleanField(default=False)

#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return f"Waiter Call - {self.table.name}"


# class OrderEvent(models.Model):

#     EVENT_TYPES = [

#         ("order_created","Order Created"),
#         ("item_added","Item Added"),
#         ("kot_sent","KOT Sent"),
#         ("kitchen_preparing","Kitchen Preparing"),
#         ("kitchen_ready","Kitchen Ready"),
#         ("payment_completed","Payment Completed"),
#         ("order_cancelled","Order Cancelled"),

#     ]

#     tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
#     outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

#     order = models.ForeignKey(
#         Order,
#         on_delete=models.CASCADE,
#         related_name="events"
#     )

#     event_type = models.CharField(
#         max_length=50,
#         choices=EVENT_TYPES
#     )

#     metadata = models.JSONField(blank=True, null=True)

#     created_by = models.ForeignKey(
#         "accounts.User",
#         null=True,
#         on_delete=models.SET_NULL
#     )

#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return f"{self.event_type} - Order {self.order.id}"


# class OrderLock(models.Model):

#     order = models.OneToOneField(
#         Order,
#         on_delete=models.CASCADE,
#         related_name="lock"
#     )

#     locked_by = models.ForeignKey(
#         "accounts.User",
#         on_delete=models.CASCADE
#     )

#     locked_at = models.DateTimeField(auto_now_add=True)

#     expires_at = models.DateTimeField()

#     def __str__(self):
#         return f"Order {self.order.id} locked by {self.locked_by}"
    
    

# class DailyKOTCounter(models.Model):

#     date = models.DateField(unique=True)

#     value = models.IntegerField(default=0)

#     def __str__(self):
#         return f"{self.date} -> {self.value}"








# ============================v2==============================
# orders/models.py
import uuid


from decimal import Decimal, ROUND_HALF_UP
from django.db import models
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

    created_at = models.DateTimeField(auto_now_add=True)
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
            )
        ]

    def __str__(self):
        return f"Order {self.order_number or self.id}"

    # -------------------------------------------------
    # SAFE ORDER NUMBER GENERATION
    # -------------------------------------------------
    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)

        if creating and not self.order_number:
            self.order_number = f"ORD-{self.id:05d}"
            super().save(update_fields=["order_number"])

    # -------------------------------------------------
    # UTIL: normalize Decimal to 2 dp
    # -------------------------------------------------
    @staticmethod
    def _quantize(amount):
        if amount is None:
            return Decimal("0.00")
        return (Decimal(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
        # totals will be recomputed by recalculate_totals
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
        Recalculate subtotal, gst_total, discount_total and grand_total.
        - Complimentary items (OrderItem.is_complimentary == True) are ignored for subtotal & GST.
        - Discount is applied against subtotal.
        - discount_total is subtracted after GST (if you want discount to reduce taxable amount, change logic).
        """

        subtotal = Decimal("0.00")
        gst_total = Decimal("0.00")

        # exclude voided items
        items_qs = self.items.exclude(status="voided")

        for item in items_qs:
            # if an item is complimentary, skip pricing (but it still exists for records)
            if getattr(item, "is_complimentary", False):
                continue

            # item.price and item.quantity assumed to be Decimal/int
            item_price = Decimal(item.price)
            quantity = Decimal(item.quantity)

            base = (item_price * quantity)
            # modifiers may be a queryset or list of objects with .price
            modifier_total = sum(Decimal(m.price) for m in getattr(item, "modifiers", []).all()) if hasattr(item, "modifiers") else Decimal("0.00")
            modifier_total = modifier_total * quantity

            item_total = base + modifier_total

            # GST per item (gst_percentage stored as Decimal percentage)
            gst_pct = Decimal(item.gst_percentage or Decimal("0.00"))
            gst = (item_total * gst_pct) / Decimal("100")

            subtotal += item_total
            gst_total += gst

        # quantize intermediate totals
        subtotal = self._quantize(subtotal)
        gst_total = self._quantize(gst_total)

        # compute discount_total based on subtotal
        discount_total = Decimal("0.00")
        if self.discount_type == "percentage" and (self.discount_value or Decimal("0.00")) > 0:
            # discount_value is e.g. 10 for 10%
            discount_total = (subtotal * (Decimal(self.discount_value) / Decimal("100.00")))
        elif self.discount_type == "amount" and (self.discount_value or Decimal("0.00")) > 0:
            discount_total = Decimal(self.discount_value)

        # make sure discount doesn't exceed subtotal
        if discount_total > subtotal:
            discount_total = subtotal

        discount_total = self._quantize(discount_total)

        # final grand total: subtotal + gst - discount_total
        grand_total = subtotal + gst_total - discount_total
        grand_total = self._quantize(max(grand_total, Decimal("0.00")))

        # persist
        self.subtotal = subtotal
        self.gst_total = gst_total
        self.discount_total = discount_total
        self.grand_total = grand_total

        self.save(update_fields=["subtotal", "gst_total", "discount_total", "grand_total"])

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

    station = models.CharField(max_length=50, null=True, blank=True)

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

    is_complimentary = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

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
        return f"{self.menu_item.name} x {self.quantity}"


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
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
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
# ORDER EVENTS
# =====================================================

class OrderEvent(models.Model):

    EVENT_TYPES = [

        ("order_created", "Order Created"),
        ("item_added", "Item Added"),
        ("item_voided", "Item Voided"),
        ("kot_sent", "KOT Sent"),
        ("kitchen_preparing", "Kitchen Preparing"),
        ("kitchen_ready", "Kitchen Ready"),
        ("payment_completed", "Payment Completed"),
        ("order_cancelled", "Order Cancelled"),
    ]

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="events"
    )

    event_type = models.CharField(
        max_length=50,
        choices=EVENT_TYPES
    )

    metadata = models.JSONField(blank=True, null=True)

    created_by = models.ForeignKey(
        "accounts.User",
        null=True,
        on_delete=models.SET_NULL
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["order"]),
        ]

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

    date = models.DateField(unique=True)

    value = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.date} -> {self.value}"
    
    
# =====================================================
# Discounts (future feature)
# =====================================================
discount_type = models.CharField(
    max_length=20,
    choices=[("percentage","Percentage"),("amount","Amount")],
    null=True,
    blank=True
)

discount_value = models.DecimalField(
    max_digits=10,
    decimal_places=2,
    default=0
)

discount_total = models.DecimalField(
    max_digits=10,
    decimal_places=2,
    default=0
)
