# menu/models.py
from django.db import models
from django.db.models import UniqueConstraint, Index
from django.core.exceptions import ValidationError


class KitchenStation(models.Model):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    name = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name



class MenuCategory(models.Model):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=255)

    display_order = models.IntegerField(default=0)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:

        ordering = ["display_order"]

        constraints = [

            UniqueConstraint(
                fields=["tenant", "outlet", "name"],
                name="unique_category_per_outlet"
            )

        ]

        indexes = [
            Index(fields=["tenant", "outlet"])
        ]


    def __str__(self):
        return self.name



class MenuItem(models.Model):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    category = models.ForeignKey(
        MenuCategory,
        on_delete=models.CASCADE,
        related_name="items"
    )
    
    station = models.ForeignKey(
        KitchenStation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )


    name = models.CharField(max_length=255)

    description = models.TextField(blank=True)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5.00
    )

    display_order = models.IntegerField(default=0)

    is_available = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    


    class Meta:

        ordering = ["display_order"]

        indexes = [
            Index(fields=["tenant", "outlet"]),
            Index(fields=["category"])
        ]

    

    def clean(self):

        if self.price < 0:
            raise ValidationError("Price cannot be negative")


    def __str__(self):
        return self.name



class ModifierGroup(models.Model):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=100)

    is_required = models.BooleanField(default=False)

    max_select = models.PositiveIntegerField(default=1)

    is_active = models.BooleanField(default=True)


    class Meta:

        indexes = [
            Index(fields=["tenant", "outlet"])
        ]


    def clean(self):

        if self.max_select < 1:
            raise ValidationError("max_select must be >= 1")


    def __str__(self):
        return self.name



class Modifier(models.Model):

    group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name="modifiers"
    )

    name = models.CharField(max_length=100)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    is_active = models.BooleanField(default=True)


    def clean(self):

        if self.price < 0:
            raise ValidationError("Modifier price cannot be negative")


    def __str__(self):
        return f"{self.name} ({self.price})"



class MenuItemModifierGroup(models.Model):

    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.CASCADE,
        related_name="modifier_groups"
    )

    modifier_group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE
    )


    class Meta:

        constraints = [

            UniqueConstraint(
                fields=["menu_item", "modifier_group"],
                name="unique_modifier_group_per_menu_item"
            )

        ]


    def __str__(self):
        return f"{self.menu_item.name} → {self.modifier_group.name}"
    
    
    