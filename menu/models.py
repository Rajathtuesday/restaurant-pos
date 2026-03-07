# menu/models.py
from django.db import models


class MenuCategory(models.Model):
    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)
    outlet = models.ForeignKey('tenants.Outlet', on_delete=models.CASCADE)

    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE)
    outlet = models.ForeignKey('tenants.Outlet', on_delete=models.CASCADE)

    category = models.ForeignKey(MenuCategory, on_delete=models.CASCADE, related_name="items")

    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    gst_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=5.00)

    is_available = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

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

    max_select = models.IntegerField(default=1)

    is_active = models.BooleanField(default=True)

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
        default=0.00
        )
    
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

    def __str__(self):
        return f"{self.menu_item.name} → {self.modifier_group.name}"