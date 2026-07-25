from django.contrib import admin
from .models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("category", "amount", "outlet", "expense_date", "is_recurring", "created_by")
    list_filter = ("category", "outlet", "is_recurring", "expense_date")
    search_fields = ("notes",)
