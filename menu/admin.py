# menu/admin.py
from django.contrib import admin
from .models import MenuCategory, MenuItem

admin.site.register(MenuCategory)
admin.site.register(MenuItem)