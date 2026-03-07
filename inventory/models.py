# inventory/models.py
from django.db import models
from django.core.exceptions import ValidationError


from django.db import models

from notifications.services.notification_service import create_notification


class InventoryItem(models.Model):

    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)
    outlet = models.ForeignKey('tenants.Outlet', on_delete=models.CASCADE)

    name = models.CharField(max_length=255)

    unit = models.CharField(
        max_length=20,
        choices=[
            ("pcs","Pieces"),
            ("g","Grams"),
            ("ml","Milliliters")
        ]
    )

    stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    low_stock_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    def reduce_stock(self, quantity):

        if quantity <= 0:
            return

        if self.stock >= quantity:

            self.stock -= quantity

        else:

            shortage = quantity - self.stock

            print(
                f"⚠ STOCK SHORTAGE: {self.name} "
                f"(missing {shortage})"
            )

            self.stock = 0

        self.save(update_fields=["stock"])

        # LOW STOCK ALERT
        if self.stock <= self.low_stock_threshold:

            create_notification(
                self.tenant,
                self.outlet,
                "low_stock",
                f"{self.name} stock low ({self.stock} remaining)"
            )

    @property
    def is_low_stock(self):
        return self.stock <= self.low_stock_threshold

    def __str__(self):
        return self.name


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

    def __str__(self):
        return f"{self.menu_item.name} -> {self.inventory_item.name}"