# inventory/models.py
from django.db import models, transaction
from django.db.models import F, Q, CheckConstraint
from decimal import Decimal
from django.core.exceptions import ValidationError
from notifications.services.notification_service import create_notification


UNIT_CHOICES = [
    ("pcs", "Pieces"),
    ("g", "Grams"),
    ("kg", "Kilograms"),
    ("ml", "Milliliters"),
    ("l", "Liters"),
]


TRANSACTION_TYPES = [
    ("restock", "Restock"),
    ("consume", "Consumption"),
    ("wastage", "Wastage"),
    ("adjustment", "Manual Adjustment"),
]


# -------------------------------------------------------
# INVENTORY ITEM
# -------------------------------------------------------

class InventoryItem(models.Model):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=255)

    unit = models.CharField(
        max_length=10,
        choices=UNIT_CHOICES
    )

    stock = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0
    )

    low_stock_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0
    )

    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Cost price per unit"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)


    class Meta:

        constraints = [

            CheckConstraint(
                condition=Q(stock__gte=0),
                name="inventory_stock_non_negative"
            )

        ]

        indexes = [
            models.Index(fields=["tenant", "outlet"]),
        ]


    # -------------------------------------------------------
    # REDUCE STOCK (USED BY KITCHEN)
    # -------------------------------------------------------

    def reduce_stock(self, quantity, reference=None):

        quantity = Decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be positive")

        with transaction.atomic():

            item = InventoryItem.objects.select_for_update().get(id=self.id)

            if item.stock < quantity:
                raise ValidationError(
                    f"Insufficient stock for {item.name}"
                )

            item.stock = F("stock") - quantity
            item.save(update_fields=["stock"])

            InventoryTransaction.objects.create(
                item=item,
                tenant=item.tenant,
                outlet=item.outlet,
                quantity=-quantity,
                transaction_type="consume",
                reference=reference
            )

            item.refresh_from_db()

            if item.stock <= item.low_stock_threshold:

                create_notification(
                    item.tenant,
                    item.outlet,
                    "low_stock",
                    f"{item.name} low stock ({item.stock} {item.unit})"
                )


    # -------------------------------------------------------
    # ADD STOCK
    # -------------------------------------------------------

    def add_stock(self, quantity, reference=None):

        quantity = Decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be positive")

        with transaction.atomic():

            item = InventoryItem.objects.select_for_update().get(id=self.id)

            item.stock = F("stock") + quantity
            item.save(update_fields=["stock"])

            InventoryTransaction.objects.create(
                item=item,
                tenant=item.tenant,
                outlet=item.outlet,
                quantity=quantity,
                transaction_type="restock",
                reference=reference
            )


    # -------------------------------------------------------
    # WASTAGE
    # -------------------------------------------------------

    def record_wastage(self, quantity, reference=None):

        quantity = Decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be positive")

        with transaction.atomic():

            item = InventoryItem.objects.select_for_update().get(id=self.id)

            if item.stock < quantity:
                raise ValidationError("Not enough stock")

            item.stock = F("stock") - quantity
            item.save(update_fields=["stock"])

            InventoryTransaction.objects.create(
                item=item,
                tenant=item.tenant,
                outlet=item.outlet,
                quantity=-quantity,
                transaction_type="wastage",
                reference=reference
            )


    # -------------------------------------------------------
    # LOW STOCK CHECK
    # -------------------------------------------------------

    @property
    def is_low_stock(self):

        return self.stock <= self.low_stock_threshold


    def __str__(self):

        return f"{self.name} ({self.stock} {self.unit})"



# -------------------------------------------------------
# INVENTORY TRANSACTION LEDGER
# -------------------------------------------------------

class InventoryTransaction(models.Model):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="transactions"
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES
    )

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3
    )

    reference = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:

        indexes = [
            models.Index(fields=["tenant", "outlet"]),
            models.Index(fields=["item"]),
        ]


    def __str__(self):

        return f"{self.transaction_type} {self.quantity} {self.item.name}"



# -------------------------------------------------------
# RECIPE
# -------------------------------------------------------

class Recipe(models.Model):

    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.CASCADE,
        related_name="recipes"
    )

    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE
    )

    quantity_required = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    unit = models.CharField(
        max_length=10,
        choices=UNIT_CHOICES,
        default="g"
    )


    class Meta:

        unique_together = ("menu_item", "inventory_item")


    def __str__(self):

        return f"{self.menu_item.name} → {self.quantity_required} {self.unit}"