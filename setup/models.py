# setup/models.py
from django.db import models
from django.db.models import UniqueConstraint, Q
from core.models import TenantScopedModel
from core.fields import EncryptedCharField


class KitchenStation(TenantScopedModel):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    name = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    
    printer_ip = models.GenericIPAddressField(null=True, blank=True, help_text="IP of the thermal printer for this station")

    # QZ Tray — USB/local printing via browser
    printer_name = models.CharField(
        max_length=200, blank=True, default="",
        help_text=(
            "Windows printer name for QZ Tray USB printing "
            "(e.g. 'BillTouch ZY306'). Find it in Windows → Printers & Scanners."
        )
    )
    printer_port = models.IntegerField(default=9100)

    PAPER_WIDTH_CHOICES = [(58, "58mm — small receipt (32 chars/line)"), (80, "80mm — standard receipt (48 chars/line)")]
    paper_width_mm = models.IntegerField(default=80, choices=PAPER_WIDTH_CHOICES, help_text="Physical paper roll width")

    CUT_CHOICES = [("full", "Full cut"), ("partial", "Partial cut"), ("none", "No cut (tear manually)")]
    cut_type = models.CharField(max_length=10, default="full", choices=CUT_CHOICES)

    ENCODING_CHOICES = [("cp437", "CP437 — standard (most printers)"), ("cp850", "CP850 — western Europe"), ("utf-8", "UTF-8 — modern printers only")]
    printer_encoding = models.CharField(max_length=10, default="cp437", choices=ENCODING_CHOICES, help_text="Use UTF-8 only if your printer explicitly supports it; otherwise CP437")

    @property
    def chars_per_line(self):
        return 32 if self.paper_width_mm == 58 else 48

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["tenant", "outlet"],
                condition=Q(is_default=True),
                name="one_default_station_per_outlet"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.outlet})"


# -------------------------------------------------
# PAYMENT CONFIG (replaces session-based storage)
# -------------------------------------------------

class PaymentConfig(TenantScopedModel):
    """
    Stores which payment methods are enabled for an outlet.
    One record per outlet.
    """
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.OneToOneField(
        "tenants.Outlet",
        on_delete=models.CASCADE,
        related_name="payment_config"
    )

    cash_enabled = models.BooleanField(default=True)
    upi_enabled = models.BooleanField(default=True)
    card_enabled = models.BooleanField(default=False)

    # Label overrides (e.g. "GPay" instead of "UPI")
    cash_label = models.CharField(max_length=50, default="Cash")
    upi_label = models.CharField(max_length=50, default="UPI / GPay")
    card_label = models.CharField(max_length=50, default="Card")

    # UPI VPA for QR generation on the bill (e.g. restaurant@okaxis)
    upi_id = models.CharField(max_length=100, blank=True, default="",
                              help_text="UPI ID (VPA) shown as QR on bill — e.g. myrestaurant@okaxis")

    # Razorpay — bring-your-own-keys. Each tenant connects their OWN Razorpay
    # account; money settles to their own bank account, Rasova never holds funds.
    # key_id is a public identifier (shown in Razorpay's own dashboard URLs,
    # not sensitive) and stays plaintext. key_secret/webhook_secret are real
    # secrets and are encrypted at rest via EncryptedCharField.
    razorpay_enabled = models.BooleanField(default=False)
    razorpay_key_id = models.CharField(max_length=100, blank=True, default="")
    razorpay_key_secret = EncryptedCharField(max_length=255, blank=True, default="")
    razorpay_webhook_secret = EncryptedCharField(max_length=255, blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payment Configuration"

    def enabled_methods(self):
        """Returns list of dicts for enabled payment methods."""
        methods = []
        if self.cash_enabled:
            methods.append({"key": "cash", "label": self.cash_label})
        if self.upi_enabled:
            methods.append({"key": "upi", "label": self.upi_label})
        if self.card_enabled:
            methods.append({"key": "card", "label": self.card_label})
        return methods

    @classmethod
    def for_outlet(cls, outlet, tenant=None):
        """Safe get-or-create that handles the OneToOneField race condition.
        Returns (obj, created) — drop-in replacement for get_or_create."""
        from django.db import IntegrityError
        try:
            return cls.objects.get_or_create(
                outlet=outlet,
                defaults={'tenant': tenant or outlet.tenant},
            )
        except IntegrityError:
            return cls.objects.get(outlet=outlet), False

    def __str__(self):
        return f"Payment Config for {self.outlet}"

# -------------------------------------------------
# AGGREGATOR CONFIG 
# -------------------------------------------------

class AggregatorConfig(TenantScopedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.OneToOneField(
        "tenants.Outlet",
        on_delete=models.CASCADE,
        related_name="aggregator_config"
    )

    zomato_enabled = models.BooleanField(default=False)
    swiggy_enabled = models.BooleanField(default=False)
    uber_eats_enabled = models.BooleanField(default=False)
    
    # Store aggregator sync API keys or webhook secrets.
    # These are genuine secrets — used to validate inbound Zomato/Swiggy
    # webhook signatures — so they are encrypted at rest, exactly like the
    # Razorpay secrets above. Previously plaintext CharFields, which meant a DB
    # dump leaked them and let an attacker forge order webhooks.
    zomato_webhook_secret = EncryptedCharField(max_length=255, null=True, blank=True)
    swiggy_webhook_secret = EncryptedCharField(max_length=255, null=True, blank=True)

    auto_accept_orders = models.BooleanField(
        default=True, 
        help_text="Automatically accept online orders and send KOT to kitchen"
    )

    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def for_outlet(cls, outlet, tenant=None):
        """Safe get-or-create that handles the OneToOneField race condition.
        Returns (obj, created) — drop-in replacement for get_or_create."""
        from django.db import IntegrityError
        try:
            return cls.objects.get_or_create(
                outlet=outlet,
                defaults={'tenant': tenant or outlet.tenant},
            )
        except IntegrityError:
            return cls.objects.get(outlet=outlet), False

    def __str__(self):
        return f"Aggregator Config for {self.outlet}"


class ScheduledReportSubscription(TenantScopedModel):
    """
    Who gets the daily report-digest email. Lives here (not in `reports`,
    which is architecturally model-less -- a pure aggregation layer) because
    this is tenant *configuration*, the same shape as PaymentConfig/
    AggregatorConfig above, not a report itself.
    """
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    # Null = tenant-wide digest (all outlets rolled into one email), matching
    # finance.Expense's outlet=None convention for "applies everywhere".
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE, null=True, blank=True)

    # Comma-separated, matching the plain-field style already used for
    # config values in this file (no JSONField precedent here to follow instead).
    recipient_emails = models.CharField(max_length=1000)
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def recipient_list(self):
        return [e.strip() for e in self.recipient_emails.split(",") if e.strip()]

    def __str__(self):
        return f"Report digest for {self.tenant.name} ({'active' if self.is_active else 'paused'})"

