from django.contrib import admin
from .models import ScheduledReportSubscription


@admin.register(ScheduledReportSubscription)
class ScheduledReportSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "outlet", "is_active", "created_at")
    list_filter = ("is_active",)
