from django.contrib import admin

from .models import DailyKOTCounter, KitchenMessage, KOTBatch

admin.site.register(KOTBatch)


@admin.register(DailyKOTCounter)
class DailyKOTCounterAdmin(admin.ModelAdmin):
    list_display = ("tenant", "outlet", "date", "value")


@admin.register(KitchenMessage)
class KitchenMessageAdmin(admin.ModelAdmin):
    list_display = ("order", "message", "is_resolved", "created_at")
    list_filter = ("is_resolved",)
