# inventory/admin.py
from django.contrib import admin
from .models import InventoryItem, Recipe

admin.site.register(InventoryItem)
admin.site.register(Recipe)