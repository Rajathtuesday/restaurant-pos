# tenants/models.py
# tenants/models.py

from django.db import models
from django.utils.text import slugify
from django.core.exceptions import ValidationError


# --------------------------------------------------
# TENANT (Restaurant / Company)
# --------------------------------------------------

class Tenant(models.Model):

    name = models.CharField(
        max_length=255,
        unique=True
    )

    slug = models.SlugField(
    help_text="Unique identifier for the tenant",
    blank=True,
    null=True
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

    class Meta:

        ordering = ["name"]

        indexes = [
            models.Index(fields=["slug"]),
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

            # ensure slug uniqueness
            while Tenant.objects.filter(slug=slug).exclude(id=self.id).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            self.slug = slug

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

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
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