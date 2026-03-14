# menu/admin.py
from django.contrib import admin
from .models import KitchenStation, MenuCategory, MenuItem

admin.site.register(MenuCategory)
admin.site.register(MenuItem)
admin.site.register(KitchenStation)