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
        ("captain", "Captain"),
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
    ASSIGNABLE_STAFF_ROLES = frozenset({"manager", "cashier", "captain", "waiter", "chef", "kitchen"})

    # A manager's or owner's approval PIN, typed on a cashier's screen to
    # approve taking money off a printed bill (orders/services/approvals.py).
    # Stored hashed like a password; blank means no PIN set. Wrong tries are
    # counted and lock the PIN for a while, so it can't be guessed.
    approval_pin = models.CharField(max_length=128, blank=True, default="")
    approval_pin_failures = models.PositiveSmallIntegerField(default=0)
    approval_pin_locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["outlet"], name="user_outlet"),
        ]

    def __str__(self):
        return self.username