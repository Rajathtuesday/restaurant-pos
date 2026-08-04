from django.contrib import admin
from .models import SubscriptionInvoice


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ("tenant", "period_start", "period_end", "amount", "status", "paid_at")
    list_filter = ("status",)
    search_fields = ("tenant__name", "razorpay_payment_id")
    readonly_fields = ("razorpay_payment_id", "paid_at", "created_at")
