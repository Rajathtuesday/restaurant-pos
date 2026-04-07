# menu/admin.py
from django.contrib import admin
from .models import KitchenStation, MenuCategory, MenuItem, ModifierGroup, Modifier, MenuItemModifierGroup

admin.site.register(MenuCategory)
admin.site.register(MenuItem)
admin.site.register(KitchenStation)

class ModifierInline(admin.TabularInline):
    model = Modifier
    extra = 1

@admin.register(ModifierGroup)
class ModifierGroupAdmin(admin.ModelAdmin):
    inlines = [ModifierInline]
    list_display = ('name', 'is_required', 'max_select', 'is_active', 'outlet')

@admin.register(MenuItemModifierGroup)
class MenuItemModifierGroupAdmin(admin.ModelAdmin):
    list_display = ('menu_item', 'modifier_group')