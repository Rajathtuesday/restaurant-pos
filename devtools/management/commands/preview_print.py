"""
Management command to preview thermal print output without a physical printer.

Usage:
    python manage.py preview_print <order_id>
    python manage.py preview_print <order_id> --width 32   # for 58mm paper
    python manage.py preview_print --list                   # show recent orders
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Preview bill + KOT print output in the terminal (no printer needed)"

    def add_arguments(self, parser):
        parser.add_argument("order_id", nargs="?", type=int, help="Order ID to preview")
        parser.add_argument("--width", type=int, default=48,
                            help="Chars per line (32 for 58mm paper, 48 for 80mm). Default: 48")
        parser.add_argument("--list", action="store_true",
                            help="List the 10 most recent orders instead of printing")
        parser.add_argument("--strip", action="store_true",
                            help="Preview QSR strip mode (token + KOTs all connected, one full cut at end)")

    def handle(self, *args, **options):
        from orders.models import Order
        from kitchen.models import KOTBatch
        from orders.services.printing_service import PrintingService

        if options["list"]:
            orders = (
                Order.objects
                .select_related("table", "outlet")
                .order_by("-created_at")[:10]
            )
            self.stdout.write("\nRecent orders:\n")
            for o in orders:
                loc = o.table.name if o.table else "Walk-in"
                self.stdout.write(
                    f"  #{o.id:>5}  {str(o.created_at)[:16]}  "
                    f"{loc:<12}  {o.status:<10}  Rs.{o.grand_total:.0f}"
                )
            self.stdout.write("")
            return

        order_id = options.get("order_id")
        if not order_id:
            raise CommandError("Provide an order_id, or use --list to see recent orders.")

        try:
            order = (
                Order.objects
                .prefetch_related("items__menu_item", "payments")
                .select_related("table", "tenant", "outlet")
                .get(id=order_id)
            )
        except Order.DoesNotExist:
            raise CommandError(f"Order #{order_id} not found.")

        kots = list(
            KOTBatch.objects
            .filter(order=order)
            .prefetch_related("items__menu_item", "items__modifiers")
            .select_related("station")
            .order_by("kot_number")
        )

        width      = options["width"]
        strip_mode = options["strip"]

        # Auto-detect strip mode if not forced by --strip flag
        if not strip_mode:
            from core.features import has_feature
            strip_mode = not has_feature(order.tenant, "kitchen_display")

        mode_label = "QSR STRIP (token+KOTs connected, one full cut)" if strip_mode else "FINE DINING (bill full cut, KOTs partial)"
        sep = "=" * (width + 4)
        self.stdout.write("\n" + sep)
        self.stdout.write(f"  PRINT PREVIEW  Order #{order.id}  ({order.outlet.name})  {len(kots)} KOT(s)")
        self.stdout.write(f"  Mode: {mode_label}")
        self.stdout.write(sep + "\n")

        svc = PrintingService(printer_type="console", chars_per_line=width)
        svc.print_bill_with_kots(order, kots, strip_mode=strip_mode)

        self.stdout.write("\n" + sep)
        self.stdout.write("  END OF PREVIEW")
        self.stdout.write(sep + "\n")
