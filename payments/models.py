# payments/models.py
from django.db import models

from core.models import TenantScopedModel


class RazorpayQRCode(TenantScopedModel):
    """
    Records "QR X was generated for order Y, expecting amount Z, expires at T."

    Without this, the webhook can't validate a payment's amount against what
    was actually quoted (the order may have changed since the QR was shown),
    and the bill UI has no way to show "still pending" across a page reload.

    Moved here from orders/models.py (Phase 6 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_razorpayqrcode, so this move touched zero rows and required zero
    downtime.
    """
    STATUS_CHOICES = (
        ("active", "Active"),
        ("paid", "Paid"),
        ("expired", "Expired"),
        ("closed", "Closed"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="razorpay_qr_codes")

    qr_code_id = models.CharField(max_length=100, unique=True)
    image_url = models.URLField()
    quoted_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "orders_razorpayqrcode"
        indexes = [
            models.Index(fields=["order"], name="orders_razo_order_i_03566e_idx"),
            models.Index(fields=["status"], name="orders_razo_status_5c6673_idx"),
        ]

    def __str__(self):
        return f"Razorpay QR {self.qr_code_id} - order #{self.order_id} - {self.status}"


class Refund(models.Model):
    """
    The one model in the whole split that is NOT TenantScopedModel -- no
    tenant/outlet fields at all. Tenant scoping happens indirectly through
    order__tenant/order__outlet filters at every call site (see
    payments/refund_service.py's approve_refund/reject_refund, which filter
    on those specifically to close a cross-tenant IDOR).

    Moved here from orders/models.py (Phase 6 of the orders app split) via a
    state-only migration -- the underlying table is still named
    orders_refund, so this move touched zero rows and required zero
    downtime.
    """

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    )

    payment = models.ForeignKey(
        "orders.Payment",
        on_delete=models.PROTECT,
        related_name="refunds"
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="refunds"
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)

    customer_complaint = models.CharField(
        max_length=500, blank=True, default="",
        help_text="What the customer said — visible to owner"
    )

    reason = models.CharField(
        max_length=255,
        help_text="Manager's internal note for this refund"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )

    refunded_by = models.ForeignKey(
        "accounts.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="refunds_issued"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orders_refund"
        indexes = [
            models.Index(fields=["order"], name="orders_refu_order_i_341cd7_idx"),
            models.Index(fields=["payment"], name="orders_refu_payment_32acb6_idx"),
        ]

    def __str__(self):
        return f"Refund ₹{self.amount} for Order {self.order_id}"
