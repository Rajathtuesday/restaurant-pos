# billing/models.py
"""
Rasova's own subscription billing -- charging restaurant tenants for using
Rasova itself. Deliberately NOT a TenantScopedModel: this is Rasova-internal
billing data ABOUT tenants (same relationship as Tenant itself, which is
also a plain models.Model), not tenant-facing operational data that should
ever be auto-scoped to "the current tenant's own view."

Version 1: monthly generated Razorpay Payment Links, not true auto-charge.
See md_files/eli5_subscription_billing_plan.html for the full design and
why full Razorpay Subscriptions (auto-charge via UPI Autopay mandate) is a
deliberate version-2, not part of this pass.
"""
from django.db import models


class SubscriptionInvoice(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("overdue", "Overdue"),
        ("waived", "Waived"),
    )

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,  # financial record -- never silently disappears
        related_name="subscription_invoices",
    )

    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    razorpay_payment_link_id = models.CharField(max_length=64, blank=True, default="")
    razorpay_payment_link_url = models.URLField(blank=True, default="")

    # Idempotency key for the webhook -- unique so a duplicate webhook
    # delivery (Razorpay explicitly warns these happen) can never mark two
    # invoices paid from one real payment, or double-process one delivery
    # received twice. Same lesson as tonight's webhook-idempotency doc,
    # applied here from the start rather than relearned the hard way.
    razorpay_payment_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True, default=None,
    )

    paid_at = models.DateTimeField(null=True, blank=True)
    overdue_warning_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["status", "period_end"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "period_start", "period_end"],
                name="unique_invoice_per_tenant_period",
            ),
        ]
        ordering = ["-period_start"]

    def __str__(self):
        return f"{self.tenant} | {self.period_start} to {self.period_end} | {self.status}"
