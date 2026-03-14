# tenants/admin.py
from django.contrib import admin
from .models import Tenant, Outlet


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "slug",
        "timezone",
        "is_active",
        "created_at"
    )

    search_fields = ("name", "slug")

    list_filter = ("is_active",)


@admin.register(Outlet)
class OutletAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "tenant",
        "is_active",
        "created_at"
    )

    list_filter = ("tenant", "is_active")

    search_fields = ("name",)