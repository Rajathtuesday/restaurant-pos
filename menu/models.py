from django.db import models
from django.db.models import UniqueConstraint, Index
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from setup.models import KitchenStation
import logging
from io import BytesIO
from PIL import Image

logger = logging.getLogger("pos.menu")




class MenuCategory(models.Model):

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



class MenuItem(models.Model):

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
    from core.validators import validate_image_size
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


    created_at = models.DateTimeField(auto_now_add=True)
    


    class Meta:

        ordering = ["display_order"]

        indexes = [
            Index(fields=["tenant", "outlet"]),
            Index(fields=["category"])
        ]

    

    def clean(self):

        if self.price < 0:
            raise ValidationError("Price cannot be negative")

    def save(self, *args, **kwargs):
        # Image compression logic
        if self.image:
            # Check if this is a new image or a re-save of an existing compressed image
            # A simple way to check is looking at the extension. If it's already webp, skip it.
            if not self.image.name.lower().endswith('.webp'):
                try:
                    img = Image.open(self.image)
                    
                    # Convert to RGB to prevent transparency issues with JPEG/WebP
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                        
                    # Resize if it's too large (cap at 800x800 for menu items)
                    max_size = (800, 800)
                    img.thumbnail(max_size, Image.Resampling.LANCZOS)
                    
                    # Save into memory
                    output = BytesIO()
                    img.save(output, format='WebP', quality=85)
                    output.seek(0)
                    
                    # Rename to .webp
                    filename = self.image.name.rsplit('.', 1)[0] + '.webp'
                    
                    # Save to model field without triggering infinite loop
                    self.image.save(filename, ContentFile(output.read()), save=False)
                    
                    # 🚀 S3/R2 Placeholder 🚀
                    # Right now, this saves to `media/menu_items/` locally.
                    # Once you configure `django-storages` and AWS settings in settings.py,
                    # this exact code will automatically upload the compressed WebP to S3 or Cloudflare R2!
                    # You won't need to change anything here.
                except Exception as e:
                    logger.error(f"Image compression failed for {self.name}: {e}")
                    
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name



class ModifierGroup(models.Model):

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

    is_active = models.BooleanField(default=True)


    class Meta:

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

    is_active = models.BooleanField(default=True)


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
    
    
    