from decimal import Decimal

from django.db import models
from django.db.models import UniqueConstraint, Index
from core.models import TenantScopedModel
from django.core.exceptions import ValidationError
from setup.models import KitchenStation
from core.validators import validate_image_size, process_uploaded_image
import logging

logger = logging.getLogger("pos.menu")

# D7: the composition scheme is closed to anyone who sells something
# outside GST (CGST Act section 10(2)(b)), and liquor is exactly that.
COMPOSITION_SELLS_NO_LIQUOR = (
    "An outlet on the composition scheme can't sell liquor: the law bars composition "
    "for anyone selling something outside GST. Turn composition off first."
)




class MenuCategory(TenantScopedModel):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=255)

    display_order = models.IntegerField(default=0)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:

        ordering = ["display_order"]

        constraints = [

            UniqueConstraint(
                fields=["tenant", "outlet", "name"],
                name="unique_category_per_outlet"
            )

        ]

        indexes = [
            Index(fields=["tenant", "outlet"])
        ]


    def __str__(self):
        return self.name



class VatClass(TenantScopedModel):
    """A state VAT rate for one kind of liquor at one outlet, such as
    "Beer 5.5%". Alcohol is outside GST; the state taxes it with VAT. A drink
    points at its class, so a rate change is one edit instead of eighty, and
    a bill line copies the rate when it is ordered (OrderItem.vat_rate), so
    no bill already made ever changes. Per outlet, because the rate depends
    on the state, and sometimes the area, the outlet is in. A 0% class is
    valid: that liquor is still a non-GST supply, never nil-rated GST."""

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    name = models.CharField(max_length=40)
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            UniqueConstraint(fields=["outlet", "name"], name="unique_vat_class_per_outlet"),
            models.CheckConstraint(
                condition=models.Q(rate__gte=Decimal("0")) & models.Q(rate__lte=Decimal("100")),
                name="vat_class_rate_0_to_100",
            ),
        ]
        indexes = [Index(fields=["tenant", "outlet"])]

    def __str__(self):
        return f"{self.name} {self.rate.normalize():f}%"

    def clean(self):
        if self.outlet_id and self.outlet.is_composition_scheme:
            raise ValidationError(COMPOSITION_SELLS_NO_LIQUOR)


class MenuItem(TenantScopedModel):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    category = models.ForeignKey(
        MenuCategory,
        on_delete=models.CASCADE,
        related_name="items"
    )
    
    station = models.ForeignKey(
        KitchenStation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )


    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="menu_items/", null=True, blank=True, validators=[validate_image_size])

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5.00
    )

    # Liquor: taxed by the state's VAT, not GST. Set only with the liquor_vat
    # feature; a drink with a class carries 0% GST (enforced below and in the
    # database). RESTRICT: a class in use can't be deleted from under a drink,
    # but deleting a whole restaurant still takes its drinks and classes
    # together (PROTECT refused that, so no pub with liquor could be deleted).
    vat_class = models.ForeignKey(
        VatClass,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="items",
    )

    estimated_prep_time = models.IntegerField(
        default=15,
        help_text="Estimated preparation time in minutes"
    )

    display_order = models.IntegerField(default=0)

    is_available = models.BooleanField(default=True)
    
    # Platform specific toggles
    available_takeaway = models.BooleanField(default=True)
    available_zomato = models.BooleanField(default=True)
    available_swiggy = models.BooleanField(default=True)

    is_veg = models.BooleanField(default=True)

    parcel_charge = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text=(
            "Packing/parcel charge for this specific item. "
            "When any item in the order has this set, per-item charges override the outlet-level flat charge. "
            "Set to 0 to use the outlet default."
        )
    )

    created_at = models.DateTimeField(auto_now_add=True)
    


    class Meta:
        ordering = ["display_order"]
        indexes = [
            Index(fields=["tenant", "outlet"]),
            Index(fields=["category"]),
            Index(fields=["station"], name="menuitem_station"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="menu_item_price_non_negative"
            ),
            # Which GST rates may be chosen is enforced where they are chosen
            # (GST_RATES in orders/services/tax_service.py); older dishes may
            # still carry a retired rate, so the database doesn't forbid them.
            # A drink taxed by VAT carries no GST at all.
            models.CheckConstraint(
                condition=models.Q(vat_class__isnull=True) | models.Q(gst_percentage=Decimal("0")),
                name="vat_item_has_no_gst",
            ),
        ]

    def clean(self):

        if self.price < 0:
            raise ValidationError("Price cannot be negative")
        if self.vat_class_id:
            if self.vat_class.outlet_id != self.outlet_id:
                raise ValidationError("That VAT class belongs to another outlet.")
            if self.gst_percentage:
                raise ValidationError("A drink taxed by VAT carries no GST: set its GST to 0%.")
            if self.outlet.is_composition_scheme:
                raise ValidationError(COMPOSITION_SELLS_NO_LIQUOR)

    def save(self, *args, **kwargs):
        # Only compress when a genuinely new image file is being uploaded.
        # Checking hasattr(self.image, 'file') is True only when Django has a
        # fresh in-memory upload; it is False when the field holds an existing
        # storage path (re-saves for price/availability changes, etc.).
        # This prevents redundant S3/disk reads on every non-image save.
        if self.image and hasattr(self.image, 'file') and not self.image.name.lower().endswith('.webp'):
            # Fail CLOSED, not open: a file that Pillow can't decode as a
            # real image (an SVG carrying a <script>, an HTML file renamed
            # to .jpg) must not be kept and stored as-is under menu_items/
            # just because re-encoding failed -- that was the actual
            # security hole here, not just a missed compression pass.
            try:
                filename, content = process_uploaded_image(self.image)
                self.image.save(filename, content, save=False)
            except ValidationError as e:
                logger.error("Rejected invalid image upload for menu item '%s': %s", self.name, e)
                self.image = None

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name



class ModifierGroup(TenantScopedModel):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=100)

    is_required = models.BooleanField(default=False)

    max_select = models.PositiveIntegerField(default=1)

    display_order = models.IntegerField(default=0)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "name"]
        indexes = [
            Index(fields=["tenant", "outlet"])
        ]


    def clean(self):

        if self.max_select < 1:
            raise ValidationError("max_select must be >= 1")


    def __str__(self):
        return self.name



class Modifier(models.Model):

    group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name="modifiers"
    )

    name = models.CharField(max_length=100)

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    display_order = models.IntegerField(default=0)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="modifier_price_non_negative"
            )
        ]

    def clean(self):

        if self.price < 0:
            raise ValidationError("Modifier price cannot be negative")


    def __str__(self):
        return f"{self.name} ({self.price})"



class MenuItemModifierGroup(models.Model):

    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.CASCADE,
        related_name="modifier_groups"
    )

    modifier_group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE
    )


    class Meta:

        constraints = [

            UniqueConstraint(
                fields=["menu_item", "modifier_group"],
                name="unique_modifier_group_per_menu_item"
            )

        ]


    def __str__(self):
        return f"{self.menu_item.name} → {self.modifier_group.name}"
    
    
    