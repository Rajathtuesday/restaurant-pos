# tenants/models.py
# tenants/models.py

import uuid

from django.db import models
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from core.validators import validate_image_size

gstin_validator = RegexValidator(
    regex=r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$',
    message="Enter a valid 15-character GSTIN (e.g. 29ABCDE1234F1Z5)."
)

fssai_validator = RegexValidator(
    regex=r'^\d{14}$',
    message="FSSAI licence number must be exactly 14 digits."
)


# --------------------------------------------------
# TENANT (Restaurant / Company)
# --------------------------------------------------

RESERVED_SLUGS = frozenset({
    # System paths that would conflict with the main app's URL routing
    'www', 'api', 'app', 'admin', 'superadmin', 'static', 'media',
    'support', 'login', 'logout', 'signup', 'register',
    'help', 'mail', 'smtp', 'rasova', 'health', 'favicon',
    # Reserved to prevent confusion with Rasova branding
    'billing', 'dashboard', 'setup',
})


class Tenant(models.Model):

    name = models.CharField(
        max_length=255,
        unique=True
    )

    slug = models.SlugField(
        help_text="Unique identifier for the tenant",
        unique=True
    )

    timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="Tenant timezone (example: Asia/Kolkata)"
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    logo = models.ImageField(
        upload_to="tenant_logos/",
        null=True,
        blank=True,
        help_text="Restaurant Logo for bills",
        validators=[validate_image_size]
    )

    sales_agent = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenants_sold",
        help_text="Superuser/Agent who brought this client"
    )

    class TenantType(models.TextChoices):
        FINE_DINING = 'fine_dining', 'Fine Dining'
        FRANCHISE = 'franchise', 'Franchise / QSR'
        CAFE = 'cafe', 'Cafe / Coffee Shop'

    tenant_type = models.CharField(
        max_length=20,
        choices=TenantType.choices,
        default=TenantType.FINE_DINING,
        help_text="Controls which features are visible to the tenant"
    )

    # --------------------------------------------------
    # INTERNAL BILLING & SUBSCRIPTION (Only visible to Admin)
    # --------------------------------------------------
    subscription_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Monthly subscription fee charged to this tenant"
    )
    
    subscription_status = models.CharField(
        max_length=20,
        choices=[
            ('trial', 'Trial'),
            ('active', 'Active'),
            ('suspended', 'Suspended')
        ],
        default='trial'
    )
    
    subscription_start_date = models.DateField(null=True, blank=True)
    subscription_end_date = models.DateField(null=True, blank=True)

    font_scale = models.FloatField(
        default=1.0,
        help_text=(
            "UI font scale multiplier. 1.0 = default size. "
            "1.1 = 10% larger (good for large-screen kiosks). "
            "0.9 = 10% smaller (laptops with limited space)."
        )
    )

    class Meta:

        ordering = ["name"]

        indexes = [
            # slug has unique=True — Postgres already creates a unique index on it.
            models.Index(fields=["tenant_type"]),
        ]

    def __str__(self):
        return self.name

    # --------------------------------------------
    # AUTO SLUG GENERATION
    # --------------------------------------------
    def save(self, *args, **kwargs):

        # Slugs are subdomain names — always force lowercase
        if self.slug:
            self.slug = self.slug.lower().strip()

        if not self.slug:
            base_slug = slugify(self.name)   # slugify already lowercases
            slug = base_slug
            counter = 1

            while (
                Tenant.objects.filter(slug=slug).exclude(id=self.id).exists()
                or slug in RESERVED_SLUGS
            ):
                slug = f"{base_slug}-{counter}"
                counter += 1

            self.slug = slug
        else:
            if self.slug in RESERVED_SLUGS:
                raise ValidationError(f"'{self.slug}' is a reserved subdomain and cannot be used.")

        from django.db import IntegrityError as _IntegrityError
        try:
            super().save(*args, **kwargs)
        except _IntegrityError:
            # Two tenants created simultaneously with the same name can race
            # through the uniqueness loop and both attempt the same slug.
            # Retry with a numeric suffix to resolve the collision.
            base_slug = slugify(self.name)
            counter = 1
            while True:
                candidate = f"{base_slug}-{counter}"
                if not Tenant.objects.filter(slug=candidate).exists():
                    self.slug = candidate
                    break
                counter += 1
            super().save(*args, **kwargs)


# --------------------------------------------------
# OUTLET (Restaurant branch)
# --------------------------------------------------

class PrintProfile(models.Model):
    """Named receipt/KOT format assigned per outlet. Scoped to a tenant."""

    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.CASCADE,
        related_name="print_profiles",
    )
    name = models.CharField(max_length=100)

    # ── KOT settings ──────────────────────────────────────────────────────
    kot_large_font = models.BooleanField(
        default=True,
        help_text="Items print double-height — easier for chefs to read at a glance.",
    )
    kot_show_total = models.BooleanField(
        default=True,
        help_text="Print the sum of KOT item prices at the bottom of the KOT.",
    )

    # ── Bill settings ─────────────────────────────────────────────────────
    bill_inner_margin = models.PositiveSmallIntegerField(
        default=4,
        help_text=(
            "Characters removed from paper width to create side margins on the bill. "
            "4 = ~2-char margin each side on 80mm paper. 0 = full width."
        ),
    )

    class Meta:
        ordering = ["tenant", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="unique_print_profile_per_tenant",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"


class Outlet(models.Model):

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="outlets"
    )

    name = models.CharField(
        max_length=255
    )

    address = models.TextField(
        blank=True
    )

    gst_no = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        validators=[gstin_validator],
        help_text="Restaurant GSTIN (15 characters, e.g. 29ABCDE1234F1Z5)"
    )

    phone = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        help_text="Outlet phone number for bills"
    )

    email = models.EmailField(
        blank=True,
        null=True,
        help_text="Outlet email"
    )

    fssai_no = models.CharField(
        max_length=14,
        blank=True,
        null=True,
        validators=[fssai_validator],
        help_text="FSSAI License Number (14 digits)"
    )

    sac_code = models.CharField(
        max_length=8,
        default="996331",
        blank=True,
        help_text=(
            "SAC (Services Accounting Code) under GST. "
            "996331 = Restaurant / café / QSR / food court (correct for most restaurants). "
            "996332 = Delivery / food truck. 996334 = Catering. "
            "Change only if your CA specifies a different code."
        )
    )

    gst_inclusive = models.BooleanField(
        default=False,
        help_text=(
            "True  → menu prices already include GST. Bill back-calculates and shows GST inside the price. "
            "         Example: Coffee ₹25 — GST (5%) ₹1.19 included. Customer pays ₹25 exactly. "
            "         Use this for QSRs, cafés, and Zomato/Swiggy restaurants. "
            "False → GST is added on top of menu prices at billing (default). "
            "         Example: Coffee ₹23.81 + GST ₹1.19 = ₹25. "
            "         Use this for fine dining and hotels."
        )
    )

    parcel_charge_amount = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text=(
            "Packaging charge per item for parcel/takeaway orders. "
            "Set to 0 to disable. "
            "Example: ₹5 per item — 3 items = ₹15 parcel charge total."
        )
    )

    use_qz_tray = models.BooleanField(
        default=False,
        help_text=(
            "Use QZ Tray for USB printing with real partial/full cuts. "
            "Requires QZ Tray installed on the billing laptop. "
            "Falls back to browser print if QZ Tray is not running."
        )
    )

    parcel_charge_per_item = models.BooleanField(
        default=True,
        help_text=(
            "True (recommended): charge × quantity (3 idlis = ₹15). "
            "False: flat charge per order regardless of items."
        )
    )

    is_composition_scheme = models.BooleanField(
        default=False,
        help_text=(
            "True → Composition Scheme dealer. "
            "Bill header shows 'Bill of Supply'. "
            "No CGST/SGST lines on printed bill. "
            "Legal note added to CSV exports only."
        )
    )

    is_union_territory = models.BooleanField(
        default=False,
        help_text=(
            "True → Outlet is in a Union Territory (Delhi, J&K, Chandigarh, "
            "Puducherry, Daman & Diu, etc.). "
            "Invoices will show UTGST instead of SGST as required by GST law."
        )
    )

    split_bill_by_category = models.BooleanField(
        default=False,
        help_text=(
            "True → Counter Billing Mode. "
            "After payment, prints one slip per menu category "
            "(e.g. Breakfast, Juice, Tea) with partial cuts between them. "
            "Customer carries each slip to the relevant counter. "
            "Used in food courts and multi-section hotels."
        )
    )

    whatsapp_no = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        help_text="WhatsApp number for sending digital bills"
    )

    opening_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Outlet opening time (e.g. 11:00)"
    )

    closing_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Outlet closing time (e.g. 23:30). Set after midnight for late-night outlets."
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    business_day_start_hour = models.IntegerField(
        default=6,
        help_text="Hour (0-23) at which a new business day starts. If it's before this hour, the order belongs to the previous calendar day."
    )

    # ── Printer settings ──────────────────────────────────────────────────────
    printer_mac = models.CharField(
        max_length=17,
        blank=True,
        help_text=(
            "Thermal printer MAC address (e.g. 00:1B:44:11:3A:B7). "
            "Printed on the sticker on the bottom of the printer. "
            "Used to locate the printer automatically even if its IP changes."
        )
    )

    agent_host = models.CharField(
        max_length=253,
        default="localhost",
        help_text=(
            "'localhost' when the Rasova Agent runs on this device (Windows PC or Termux on tablet). "
            "Set to a local IP (e.g. 192.168.1.200) when using a Raspberry Pi or separate print server."
        )
    )

    print_agent_key = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text="Secret key the Rasova Agent uses to poll for print jobs. Never share publicly."
    )

    paper_width_mm = models.IntegerField(
        default=80,
        choices=[(58, "58mm — narrow roll"), (80, "80mm — standard roll")],
        help_text="Thermal paper width. Most restaurant printers use 80mm."
    )

    print_profile = models.ForeignKey(
        "PrintProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outlets",
        help_text="Receipt and KOT print format for this outlet. Leave blank to use defaults.",
    )

    outlet_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Sequential outlet number within the tenant (1, 2, 3…). Auto-assigned on creation.",
    )

    class Meta:

        ordering = ["tenant", "name"]

        constraints = [

            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="unique_outlet_per_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "outlet_number"],
                name="unique_outlet_number_per_tenant",
            ),

        ]

        indexes = [
            models.Index(fields=["tenant"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk is None and self.outlet_number is None:
            from django.db import transaction as _tx
            with _tx.atomic():
                last = (
                    Outlet.objects
                    .select_for_update()
                    .filter(tenant=self.tenant)
                    .order_by("-outlet_number")
                    .values_list("outlet_number", flat=True)
                    .first()
                )
                self.outlet_number = (last or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"

    # State codes for Union Territories under Indian GST law.
    # First 2 digits of GSTIN always encode the state/UT.
    _UT_STATE_CODES = {"04", "07", "25", "31", "34", "35", "37", "38"}

    @property
    def uses_utgst(self) -> bool:
        """
        True if invoices should show UTGST instead of SGST.

        Auto-detected from the GSTIN prefix (first 2 digits = state code).
        Falls back to the manual is_union_territory toggle for outlets
        without a GSTIN (e.g. composition dealers using Bill of Supply).
        """
        if self.gst_no and len(self.gst_no) >= 2:
            return self.gst_no[:2] in self._UT_STATE_CODES
        return self.is_union_territory


class TenantFeatureOverride(models.Model):
    """
    Overrides the default features provided by the tenant_type.
    """
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="feature_overrides")
    feature = models.CharField(max_length=50, help_text="Feature name (e.g. barcode_transfer, split_bill)")
    enabled = models.BooleanField(default=True, help_text="True to enable, False to explicitly disable")
    notes = models.TextField(blank=True, help_text="Reason for override (e.g. 'Central kitchen needed')")

    class Meta:
        unique_together = ('tenant', 'feature')

    def __str__(self):
        return f"{self.tenant.name} - {self.feature} - {'Enabled' if self.enabled else 'Disabled'}"


class TenantFeatureAuditLog(models.Model):
    """
    Append-only history of feature flag changes per tenant.

    TenantFeatureOverride is a single mutable row per (tenant, feature) — toggling
    it overwrites the previous state. This log exists so "who turned on Razorpay for
    this tenant, and when" survives being toggled again later.
    """
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="feature_audit_log")
    feature = models.CharField(max_length=50)
    enabled = models.BooleanField()
    source = models.CharField(max_length=20, help_text="'override' or 'default' (reset to type default)")
    changed_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "feature"])]
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.tenant.name} - {self.feature} - {'Enabled' if self.enabled else 'Disabled'} @ {self.changed_at:%Y-%m-%d %H:%M}"
