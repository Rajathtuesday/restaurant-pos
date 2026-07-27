# orders/admin.py
from django.contrib import admin
from .models import (
    Table,
    Order,
    OrderItem,
    Payment,
)


admin.site.register(Table)
admin.site.register(Payment)
# WaiterCall registration moved to waiter/admin.py (Phase 4 of the orders app split).


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "order_number",
        "table",
        "status",
        "grand_total",
        "created_at"
    )

    inlines = [OrderItemInline]