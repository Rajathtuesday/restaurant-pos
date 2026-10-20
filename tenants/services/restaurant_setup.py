"""
Making a new restaurant: the tenant, its first outlet, the owner's login, a
default kitchen station and the payment methods.

One place, used by the superuser panel (accounts/views/superuser_views.py)
and by a Rasova box's first switch-on (box/views.py), so the two can never
make a restaurant differently.
"""
from django.db import transaction

from core.errors import UserError


class RestaurantSetupError(UserError):
    """A restaurant Rasova can't make as asked; says why."""


def create_restaurant(*, name, tenant_type, owner_username, owner_password, outlet_name="", phone="", gst_no=""):
    from accounts.models import User
    from setup.models import KitchenStation, PaymentConfig
    from tenants.models import Outlet, Tenant, read_gstin

    name = (name or "").strip()
    owner_username = (owner_username or "").strip()
    owner_password = owner_password or ""
    if not name or not owner_username or not owner_password.strip():
        raise RestaurantSetupError("Restaurant name, owner username and password are required.")
    if tenant_type not in Tenant.TenantType.values:
        raise RestaurantSetupError(f"Unknown restaurant type '{tenant_type}'.")
    gstin, gstin_error = read_gstin(gst_no)
    if gstin_error:
        raise RestaurantSetupError(gstin_error)
    if User.objects.filter(username=owner_username).exists():
        raise RestaurantSetupError(f"Username '{owner_username}' is already taken.")

    with transaction.atomic():
        tenant = Tenant.objects.create(name=name, tenant_type=tenant_type)
        outlet = Outlet.objects.create(
            tenant=tenant, name=(outlet_name or "").strip() or "Main Branch",
            phone=(phone or "").strip() or None, gst_no=gstin or None,
        )
        owner = User.objects.create_user(
            username=owner_username, password=owner_password, tenant=tenant, outlet=outlet, role="owner",
        )
        KitchenStation.objects.create(tenant=tenant, outlet=outlet, name="Counter", is_default=True)
        PaymentConfig.for_outlet(outlet, tenant)
    return tenant, outlet, owner
