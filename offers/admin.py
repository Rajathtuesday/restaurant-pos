from django.contrib import admin

from .models import Offer, OfferTarget, OfferWindow


class OfferTargetInline(admin.TabularInline):
    model = OfferTarget
    extra = 1
    autocomplete_fields = ()
    raw_id_fields = ("menu_item", "category")


class OfferWindowInline(admin.TabularInline):
    model = OfferWindow
    extra = 1


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    """Until the Setup screen exists, a superuser sets offers up here."""
    list_display = ("name", "summary", "tenant", "outlet", "priority", "is_active", "valid_from", "valid_until")
    list_filter = ("is_active", "kind", "tenant")
    search_fields = ("name",)
    readonly_fields = ("paused_at", "archived_at", "created_at", "updated_at")
    raw_id_fields = ("tenant", "outlet")
    inlines = [OfferTargetInline, OfferWindowInline]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
