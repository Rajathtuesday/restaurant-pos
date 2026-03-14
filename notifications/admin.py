#notifications/admin.py
from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "tenant",
        "outlet",
        "type",
        "message",
        "is_read",
        "created_at"
    )

    list_filter = (
        "type",
        "is_read",
        "created_at"
    )

    search_fields = (
        "message",
    )

    ordering = ("-created_at",)