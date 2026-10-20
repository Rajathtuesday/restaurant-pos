import json

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "This box's state as JSON (rasova-box status, and the updater's health check)."

    def handle(self, *args, **options):
        from django.db import connection

        from box import backup, license
        from box.models import BoxState
        from orders.models import Order
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        lic = license.current(refresh=True)
        state = BoxState.get()
        self.stdout.write(json.dumps({
            "version": getattr(settings, "RASOVA_VERSION", "dev"),
            "edition": "box" if getattr(settings, "BOX", False) else "cloud",
            "box_id": license.machine_id(),
            "licence": {"status": lic.status, "expires": str(lic.expires or ""), "days_left": lic.days_left},
            "setup_done": bool(state.setup_done_at),
            "last_backup": {"at": str(state.last_backup_at or ""), "ok": state.last_backup_ok},
            "backups": len(backup.list_backups()),
            # Updates wait for these to be settled (rasova-box update)
            "open_orders": Order.objects.filter(status__in=["open", "billing"]).count(),
            "database": "ok",
        }))
