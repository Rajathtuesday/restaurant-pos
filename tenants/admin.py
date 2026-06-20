# tenants/admin.py
from django.contrib import admin
from .models import Tenant, Outlet, TenantFeatureOverride, TenantFeatureAuditLog, PrintProfile


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


@admin.register(PrintProfile)
class PrintProfileAdmin(admin.ModelAdmin):
    list_display  = ("name", "tenant", "kot_large_font", "kot_show_total", "bill_inner_margin")
    list_filter   = ("tenant",)
    search_fields = ("name", "tenant__name")
    fieldsets = (
        (None, {
            "fields": ("tenant", "name"),
        }),
        ("KOT settings", {
            "fields": ("kot_large_font", "kot_show_total"),
        }),
        ("Bill settings", {
            "fields": ("bill_inner_margin",),
        }),
    )


@admin.register(Outlet)
class OutletAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "tenant",
        "print_profile",
        "is_active",
        "created_at",
    )

    list_filter   = ("tenant", "is_active")
    search_fields = ("name",)
    raw_id_fields = ("print_profile",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "print_profile":
            # Only show profiles belonging to the outlet's tenant.
            # On the change form we know the object; on add we can't filter.
            if request.resolver_match.kwargs.get("object_id"):
                try:
                    outlet = Outlet.objects.get(pk=request.resolver_match.kwargs["object_id"])
                    kwargs["queryset"] = PrintProfile.objects.filter(tenant=outlet.tenant)
                except Outlet.DoesNotExist:
                    pass
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(TenantFeatureOverride)
class TenantFeatureOverrideAdmin(admin.ModelAdmin):
    list_display = ("tenant", "feature", "enabled")
    list_filter  = ("feature", "enabled")
    search_fields = ("tenant__name", "feature")


@admin.register(TenantFeatureAuditLog)
class TenantFeatureAuditLogAdmin(admin.ModelAdmin):
    list_display = ("tenant", "feature", "enabled", "source", "changed_by", "changed_at")
    list_filter = ("feature", "enabled", "source")
    search_fields = ("tenant__name", "feature")
    readonly_fields = ("tenant", "feature", "enabled", "source", "changed_by", "changed_at", "notes")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False