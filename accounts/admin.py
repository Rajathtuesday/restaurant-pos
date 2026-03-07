# accounts/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("POS Details", {
            "fields": ("tenant", "outlet", "role")
        }),
    )

    list_display = ("username", "email", "role", "tenant", "outlet", "is_staff")


admin.site.register(User, CustomUserAdmin)