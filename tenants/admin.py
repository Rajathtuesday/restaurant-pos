# tenants/admin.py
from django.contrib import admin
from .models import Tenant, Outlet, TenantFeatureOverride


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "slug",
        "tenant_type",
        "subscription_status",
        "subscription_fee",
        "is_active",
        "created_at"
    )

    search_fields = ("name", "slug")

    list_filter = ("tenant_type", "subscription_status", "is_active")


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

@admin.register(TenantFeatureOverride)
class TenantFeatureOverrideAdmin(admin.ModelAdmin):
    list_display = ("tenant", "feature", "enabled")
    list_filter = ("feature", "enabled")
    search_fields = ("tenant__name", "feature")