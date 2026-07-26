from django.contrib import admin

from .models import DailyOnlineTokenCounter, DailyTokenCounter, TokenOrder


@admin.register(TokenOrder)
class TokenOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "outlet", "token_number", "date", "is_online", "order")
    list_filter = ("is_online", "outlet")


@admin.register(DailyTokenCounter)
class DailyTokenCounterAdmin(admin.ModelAdmin):
    list_display = ("outlet", "date", "value")


@admin.register(DailyOnlineTokenCounter)
class DailyOnlineTokenCounterAdmin(admin.ModelAdmin):
    list_display = ("outlet", "date", "value")
