# accounts/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    outlet = models.ForeignKey(
        'tenants.Outlet',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    ROLE_CHOICES = (
        ("owner",   "Owner"),
        ("manager", "Manager"),
        ("agent",   "Agent"),
        ("cashier", "Cashier"),
        ("waiter",  "Waiter"),
        ("chef",    "Chef"),
        ("kitchen", "Kitchen Staff"),
    )

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')

    # Roles a tenant's owner/manager may assign through staff-creation forms.
    # Deliberately excludes "owner" (privilege escalation) and "agent" (a
    # Rasova-internal sales role, not a restaurant staff role). Any role value
    # posted to a staff form MUST be validated against this set — otherwise a
    # low-privilege account can mint itself an owner.
    ASSIGNABLE_STAFF_ROLES = frozenset({"manager", "cashier", "waiter", "chef", "kitchen"})

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["outlet"], name="user_outlet"),
        ]

    def __str__(self):
        return self.username