# tenants/models.py
# tenants/models.py

import logging
import re
import uuid
from decimal import Decimal

from django.db import models
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from core.validators import validate_image_size, process_uploaded_image

logger = logging.getLogger("pos.tenants")

GSTIN_REGEX = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'

# A made-up Karnataka GSTIN in the right format (the help text's example),
# for the demo restaurant and local test setups, never a real business.
SAMPLE_GSTIN = "29ABCDE1234F1Z5"

gstin_validator = RegexValidator(
    regex=GSTIN_REGEX,
    message="Enter a valid 15-character GSTIN (e.g. 29ABCDE1234F1Z5)."
)

# Only a business registered for GST may collect it (CGST Act, section 32),
# and a tax invoice must show its GSTIN (CGST Rules, rule 46), so an outlet
# without a valid GSTIN bills without GST.
NO_GSTIN_MESSAGE = (
    "No GSTIN saved, so this outlet's bills carry no GST: only a GST-registered "
    "business may collect it. If the outlet is registered, enter its GSTIN."
)
INVALID_GSTIN_MESSAGE = (
    "{value} is not a valid GSTIN, so it was not saved. "
    "A GSTIN has 15 characters, like 29ABCDE1234F1Z5."
)


BILL_CODE_REGEX = r'^[A-Z0-9]{1,3}$'
BILL_CODE_RULE = "A bill number code is up to 3 capital letters or digits, like SG."


def suggest_bill_code(name, taken=()):
    """A code for an outlet's bill numbers (orders/services/bill_numbers.py),
    from the restaurant's name: its initials ("Spice Garden" -> "SG"), or the
    first three letters of a one-word name ("Malenadu" -> "MAL"). A digit
    keeps it apart from the codes in `taken` ("SG2")."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    base = ("".join(word[0] for word in words) if len(words) > 1 else "".join(words))[:3].upper() or "R"
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = base[:3 - len(str(n))] + str(n)
        if candidate not in taken:
            return candidate
    raise ValueError(f"no free bill number code for {name!r}")


def read_bill_code(raw, outlet):
    """A bill number code typed into Outlet Settings. Returns (code, None) to
    save, (None, None) when left blank (the outlet keeps its code), and
    (None, message) when it can't be used."""
    value = re.sub(r"\s+", "", raw or "").upper()
    if not value:
        return None, None
    if not re.fullmatch(BILL_CODE_REGEX, value):
        return None, f"{value} can't start bill numbers. {BILL_CODE_RULE}"
    clash = Outlet.objects.filter(tenant_id=outlet.tenant_id, bill_code=value).exclude(pk=outlet.pk).first()
    if clash:
        return None, f"{value} already starts the bills of {clash.name}: each outlet needs its own code."
    return value, None


def read_gstin(raw, pattern=GSTIN_REGEX):
    """A GSTIN typed into a form. Returns (gstin, None) when it is valid,
    (None, None) when it is blank, and (None, message) when it isn't a GSTIN:
    then keep the outlet's old one and show the message."""
    value = re.sub(r"\s+", "", raw or "").upper()
    if not value:
        return None, None
    if re.fullmatch(pattern, value):
        return value, None
    return None, INVALID_GSTIN_MESSAGE.format(value=value)


# An e-commerce operator's GSTIN may be its tax-collection registration,
# which has C where a normal GSTIN has Z (the 14th character).
OPERATOR_GSTIN_REGEX = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}[ZC][0-9A-Z]{1}$'


def read_operator_gstin(raw):
    """An app's GSTIN (Zomato, Swiggy) typed into a form, as read_gstin."""
    return read_gstin(raw, OPERATOR_GSTIN_REGEX)

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
        PUB = 'pub', 'Pub / Bar'

    tenant_type = models.CharField(
        max_length=20,
        choices=TenantType.choices,
        default=TenantType.FINE_DINING,
        help_text="Controls which features are visible to the tenant"
    )

    @property
    def is_table_service(self):
        """Guests are seated and served at tables (fine dining, pubs), as
        opposed to the counter and token types (QSR, cafe). It decides where
        staff land after login."""
        return self.tenant_type in (self.TenantType.FINE_DINING, self.TenantType.PUB)

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

        # Validate + re-encode any freshly-uploaded logo via Pillow before it
        # ever touches storage. This is the actual content-type security
        # boundary (not the filename/extension) -- without it, a raw
        # request.FILES["logo"] assignment (setup/views/onboarding_views.py,
        # setup/views/core_views.py) never runs full_clean(), so neither
        # validate_image_size nor Django's own ImageField content check ever
        # fire, and an SVG-with-<script> or an HTML file renamed to .jpg
        # would be stored and served as-is from the public logo URL.
        if self.logo and hasattr(self.logo, "file") and not self.logo.name.lower().endswith(".webp"):
            try:
                validate_image_size(self.logo)
                filename, content = process_uploaded_image(self.logo)
                self.logo.save(filename, content, save=False)
            except ValidationError as exc:
                logger.error("Rejected invalid logo upload for tenant '%s': %s", self.name, exc)
                self.logo = None

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

    bill_code = models.CharField(
        max_length=3,
        blank=True,
        default="",
        db_default="",
        validators=[RegexValidator(BILL_CODE_REGEX, BILL_CODE_RULE)],
        help_text=(
            "Starts this outlet's bill numbers, as in SG/2627/000123: up to 3 letters or "
            "digits, different for each outlet of the restaurant. Set from the restaurant's "
            "name when the outlet is made; changing it starts a new series."
        ),
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

    display_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        help_text=(
            "Permanent secret used in the public 'Now Serving' display board "
            "URL (a TV/monitor at the pickup counter) -- same pattern as "
            "Table.qr_token, never regenerated."
        )
    )

    qr_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        help_text=(
            "Permanent secret for the outlet-wide 'Counter / Walk-in' menu QR "
            "-- for QSR/cafe outlets with no seating, so there's no Table to "
            "hang a per-table QR on. Orders placed via this token get "
            "table=None, same as a staff-created walk-in order."
        )
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

    staff_discount_limit_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "The largest discount a cashier or captain may give on their own, as a "
            "percent of the bill (a free dish counts as 100%). Above it, a manager "
            "or the owner has to apply it. Blank = no limit."
        ),
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

    parcel_gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("5.00"),
        db_default=Decimal("5.00"),   # kept in the database, so a deploy's old code can still save outlets
        help_text=(
            "GST on the parcel charge, in percent. Packing is part of the "
            "restaurant service, so it carries the food's rate: 5% for most "
            "restaurants, 18% in a hotel with rooms over ₹7,500 a night. "
            "Added on top when prices exclude GST, taken out of the charge "
            "when they include it, and never charged on the composition scheme."
        )
    )

    # ── Liquor VAT (the liquor_vat feature) ──────────────────────────────
    # Alcohol is outside GST: the state taxes it with VAT, at the rates in
    # menu.VatClass. orders/services/tax_engine.py carries both on one bill.
    vat_registration_no = models.CharField(
        max_length=20,
        blank=True,
        default="",
        db_default="",
        help_text="State VAT registration (TIN) number, printed on the liquor part of the bill.",
    )
    vat_inclusive = models.BooleanField(
        default=False,
        db_default=False,
        help_text=(
            "True: liquor menu prices already include VAT. Separate from GST "
            "inclusive, because many bars price food with tax added and drinks "
            "with tax included, or the other way round."
        ),
    )
    LIQUOR_BILLING_MODES = [
        ("one_bill", "One bill, food and liquor in two sections"),
        ("two_bills", "A separate bill for liquor"),
    ]
    liquor_billing_mode = models.CharField(
        max_length=10,
        choices=LIQUOR_BILLING_MODES,
        default="one_bill",
        db_default="one_bill",
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
            "True: the outlet is in a union territory without a legislature "
            "(Chandigarh, Ladakh, Lakshadweep, Andaman and Nicobar, Dadra and "
            "Nagar Haveli and Daman and Diu), so bills show UTGST instead of "
            "SGST. Delhi, Puducherry and Jammu and Kashmir charge SGST. "
            "Used only when the outlet has no GSTIN; a GSTIN decides it."
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

    is_central_kitchen = models.BooleanField(
        default=False,
        help_text=(
            "True → this outlet is the tenant's central kitchen, the source "
            "that fulfills stock requisitions raised by other outlets "
            "internally instead of routing them to a vendor. Only meaningful "
            "for franchise/hub-spoke tenants with the central_kitchen "
            "feature. Explicit flag — StockRequisition.auto_route() used to "
            "guess this from batch history alone before this field existed."
        )
    )

    po_vendor_email_enabled = models.BooleanField(
        default=False,
        help_text=(
            "True → automatically email a PDF copy of a purchase order to "
            "the vendor when a manager marks it as ordered. Off by default: "
            "sending email on a tenant's behalf on a vendor's real inbox "
            "needs explicit opt-in, not an assumed default."
        )
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
            # Two outlets of one restaurant never share a bill number.
            models.UniqueConstraint(
                fields=["tenant", "bill_code"],
                condition=~models.Q(bill_code=""),
                name="unique_bill_code_per_tenant",
            ),

        ]

        indexes = [
            models.Index(fields=["tenant"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk is None and (self.outlet_number is None or not self.bill_code):
            from django.db import transaction as _tx
            with _tx.atomic():
                # The restaurant's outlets stay locked until this one is saved,
                # so two new outlets never get the same number or bill code.
                siblings = Outlet.objects.select_for_update().filter(tenant=self.tenant)
                if self.outlet_number is None:
                    last = siblings.order_by("-outlet_number").values_list("outlet_number", flat=True).first()
                    self.outlet_number = (last or 0) + 1
                if not self.bill_code:
                    self.bill_code = suggest_bill_code(
                        self.tenant.name, set(siblings.values_list("bill_code", flat=True)))
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    def ensure_bill_code(self):
        """This outlet's bill number code, set from the restaurant's name if it
        has none yet (an outlet saved before codes existed)."""
        if not self.bill_code:
            taken = set(Outlet.objects.filter(tenant_id=self.tenant_id).exclude(pk=self.pk)
                        .values_list("bill_code", flat=True))
            self.bill_code = suggest_bill_code(self.tenant.name, taken)
            Outlet.objects.filter(pk=self.pk).update(bill_code=self.bill_code)
        return self.bill_code

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"

    @property
    def is_gst_registered(self) -> bool:
        """Whether this outlet may collect GST: it has a valid GSTIN (in any
        case: one saved in lowercase is still the outlet's GSTIN). Without
        one its bills carry no GST (see NO_GSTIN_MESSAGE)."""
        return re.fullmatch(GSTIN_REGEX, (self.gst_no or "").strip().upper()) is not None

    @property
    def collects_gst(self) -> bool:
        """Whether this outlet's new bills carry GST: it is registered and
        not on the composition scheme."""
        return self.is_gst_registered and not self.is_composition_scheme

    # GSTIN state codes (the first 2 digits) of the union territories that
    # charge UTGST: those without a legislature (CGST Act, section 2(114)).
    # 04 Chandigarh, 25 and 26 Dadra and Nagar Haveli and Daman and Diu (25
    # is Daman and Diu's code from before the 2020 merger), 31 Lakshadweep,
    # 35 Andaman and Nicobar, 38 Ladakh, 97 Other Territory. Delhi (07),
    # Puducherry (34) and Jammu and Kashmir (01) have legislatures and charge
    # SGST; 37 is Andhra Pradesh, a state. The list once had 07, 34 and 37 and
    # lacked 26 and 97.
    _UT_STATE_CODES = {"04", "25", "26", "31", "35", "38", "97"}

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
    # 50, not 20 — "preset:<key>" needs to fit any real preset key.
    # "preset:counter_billing" alone is 23 characters; the original 20-char
    # limit meant applying that specific preset would crash with a
    # DataError the moment anyone actually did it, this had just never
    # been exercised by a test before.
    source = models.CharField(max_length=50, help_text="'override' or 'default' (reset to type default), or 'preset:<key>'")
    changed_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "feature"])]
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.tenant.name} - {self.feature} - {'Enabled' if self.enabled else 'Disabled'} @ {self.changed_at:%Y-%m-%d %H:%M}"
