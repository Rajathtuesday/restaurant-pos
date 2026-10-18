from django.contrib import admin

from .models import Promo


@admin.register(Promo)
class PromoAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "tenant", "outlet", "discount_type", "discount_value", "is_active")
    list_filter = ("is_active", "discount_type", "tenant")
    search_fields = ("name", "code")
