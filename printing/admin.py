from django.contrib import admin

from .models import PrintJob


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "outlet", "status", "created_at", "done_at")
    list_filter = ("status", "tenant")
