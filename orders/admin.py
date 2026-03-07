# orders/admin.py
from django.contrib import admin
from .models import (
    Table,
    Order,
    OrderItem,
    KOTBatch,
    Payment,
    WaiterCall
)


admin.site.register(Table)
admin.site.register(KOTBatch)
admin.site.register(Payment)
admin.site.register(WaiterCall)


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