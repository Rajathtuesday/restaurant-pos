# orders/management/commands/reset_demo_tenant.py
from django.core.management.base import BaseCommand

from orders.scripts.demo_seed import create_or_reset_demo_tenant


class Command(BaseCommand):
    help = (
        "Creates the public Demo Bistro tenant if it doesn't exist, and always "
        "wipes + reseeds its orders so a visitor never inherits a mess left by "
        "an earlier one. Safe to run by hand or on a schedule."
    )

    def handle(self, *args, **options):
        tenant = create_or_reset_demo_tenant()
        self.stdout.write(self.style.SUCCESS(
            f"Demo tenant ready: {tenant.name} (slug={tenant.slug})"
        ))
