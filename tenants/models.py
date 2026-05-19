# tenants/models.py
# tenants/models.py

from django.db import models
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from core.validators import validate_image_size


# --------------------------------------------------
# TENANT (Restaurant / Company)
# --------------------------------------------------

RESERVED_SLUGS = frozenset({
    'www', 'api', 'app', 'admin', 'superadmin', 'static', 'media',
    'support', 'billing', 'login', 'logout', 'signup', 'register',
    'help', 'mail', 'smtp', 'rasova', 'dashboard', 'setup', 'reports',
    'demo', 'staging', 'test', 'dev', 'health', 'favicon',
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

    class Meta:

        ordering = ["name"]

        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["tenant_type"]),
        ]

    def __str__(self):
        return self.name

    # --------------------------------------------
    # AUTO SLUG GENERATION
    # --------------------------------------------
    def save(self, *args, **kwargs):

        if not self.slug:
            base_slug = slugify(self.name)
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
        help_text="Restaurant GSTIN (15 characters)"
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
        help_text="FSSAI License Number"
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

    class Meta:

        ordering = ["tenant", "name"]

        constraints = [

            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="unique_outlet_per_tenant"
            )

        ]

        indexes = [
            models.Index(fields=["tenant"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"
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
