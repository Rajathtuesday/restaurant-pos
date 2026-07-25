# finance/models.py
from django.db import models
from core.models import TenantScopedModel


class Expense(TenantScopedModel):
    """
    An operating expense (rent, salaries, utilities, ...) — the piece of data
    reports/services/pl_reports.py's gross_margin_report() has always been
    missing to get from "gross margin" to a real net profit number.
    """

    CATEGORY_CHOICES = (
        ("rent", "Rent"),
        ("salaries", "Salaries"),
        ("utilities", "Utilities"),
        ("marketing", "Marketing"),
        ("maintenance", "Maintenance"),
        ("supplies", "Supplies"),
        ("other", "Other"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    # Null = tenant-wide expense (e.g. head-office marketing spend) not
    # attributable to a single outlet — counted against every outlet's
    # report rather than none of them. See net_profit_report().
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE, null=True, blank=True)

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_recurring = models.BooleanField(default=False)

    # Not auto_now_add — expenses are often entered after the fact
    # ("last month's electricity bill"), so the date has to be settable.
    expense_date = models.DateField()

    notes = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet", "expense_date"]),
        ]
        ordering = ["-expense_date"]

    def __str__(self):
        return f"{self.get_category_display()} — ₹{self.amount} ({self.expense_date})"
