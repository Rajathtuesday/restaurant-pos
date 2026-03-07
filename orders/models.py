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
from decimal import Decimal

from django.db import models
from django.db.models import Q


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

    def __str__(self):
        return self.name


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
        Table,
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
        null=True,
        blank=True
    )

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gst_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    
    
    def save(self, *args, **kwargs):

        if not self.order_number:

            last = Order.objects.order_by("-id").first()

            if last:
                next_number = last.id + 1
            else:
                next_number = 1

            self.order_number = f"ORD-{next_number:04d}"

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order {self.id}"

    def recalculate_totals(self):

        subtotal = Decimal("0")
        gst_total = Decimal("0")

        for item in self.items.all():

            base = item.price * item.quantity

            modifier_total = sum(
                m.price for m in item.modifiers.all()
            ) * item.quantity

            item_total = base + modifier_total

            gst = (item_total * item.gst_percentage) / Decimal("100")

            subtotal += item_total
            gst_total += gst

        self.subtotal = subtotal
        self.gst_total = gst_total
        self.grand_total = subtotal + gst_total

        self.save(update_fields=["subtotal", "gst_total", "grand_total"])

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

    station = models.CharField(
        max_length=50,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="confirmed"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"KOT {self.kot_number}"

    class Meta:
        unique_together = ("order", "kot_number")


class OrderItem(models.Model):

    STATUS = (
        ("pending", "Pending"),
        ("sent", "Sent to Kitchen"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("served", "Served"),
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

    quantity = models.IntegerField(default=1)

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

    kot = models.ForeignKey(
        KOTBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="items"
    )

    def __str__(self):
        return f"{self.menu_item.name} x {self.quantity}"


class OrderItemModifier(models.Model):

    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="modifiers"
    )

    modifier = models.ForeignKey(
        "menu.Modifier",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=200)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    def __str__(self):
        return self.name


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

    def __str__(self):
        return f"{self.method} - {self.amount}"
    
    

class WaiterCall(models.Model):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE
    )

    is_resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Waiter Call - {self.table.name}"
    

class OrderEvent(models.Model):

    EVENT_TYPES = [

        ("order_created", "Order Created"),
        ("item_added", "Item Added"),
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

    def __str__(self):
        return f"{self.event_type} - Order {self.order.id}"
    

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

    def __str__(self):
        return f"Order {self.order.id} locked by {self.locked_by}"
    
class DailyKOTCounter(models.Model):

    date = models.DateField(unique=True)

    value = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.date} -> {self.value}"