# orders/management/commands/audit_pos.py
from django.core.management.base import BaseCommand
from django.db import models
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta

from orders.models import Order, OrderItem, Table, KOTBatch
from inventory.models import InventoryItem

class Command(BaseCommand):

    help = "Audit POS system after simulation"

    def handle(self, *args, **kwargs):

        print("\nPOS SYSTEM AUDIT")
        print("-----------------------------")

        total_orders = Order.objects.count()
        total_items = OrderItem.objects.count()
        total_tables = Table.objects.count()
        total_kots = KOTBatch.objects.count()

        print(f"Total Orders: {total_orders}")
        print(f"Total Order Items: {total_items}")
        print(f"Total Tables: {total_tables}")
        print(f"Total KOT Batches: {total_kots}")

        # -------------------------
        # Order Status Breakdown
        # -------------------------

        print("\nOrder Status Breakdown")

        statuses = Order.objects.values("status").annotate(
            count=Count("id")
        )

        for s in statuses:
            print(f"{s['status']} : {s['count']}")

        # -------------------------
        # Orphan Orders
        # -------------------------

        print("\nOrders Without Items")

        orphan_orders = Order.objects.annotate(
            item_count=Count("items")
        ).filter(item_count=0)

        print("Orphan Orders:", orphan_orders.count())

        # -------------------------
        # Items Missing KOT
        # -------------------------

        print("\nItems Without KOT")

        missing_kot = OrderItem.objects.filter(kot__isnull=True).count()

        print("Items missing KOT:", missing_kot)

        # -------------------------
        # Orders Missing KOT
        # -------------------------

        print("\nOrders Missing KOT")

        orders_without_kot = Order.objects.filter(
            kots__isnull=True
        ).count()

        print("Orders missing KOT:", orders_without_kot)

        # -------------------------
        # Stuck Orders
        # -------------------------

        print("\nStuck Kitchen Orders")

        stuck_time = timezone.now() - timedelta(minutes=30)

        stuck_orders = Order.objects.filter(
            status__in=["confirmed", "preparing"],
            created_at__lt=stuck_time
        )

        print("Orders stuck in kitchen:", stuck_orders.count())

        # -------------------------
        # Table Blocking Check
        # -------------------------

        print("\nTables Blocked By Unpaid Orders")

        blocked_tables = Order.objects.filter(
            status="ready"
        ).count()

        print("Tables waiting for payment:", blocked_tables)

        # -------------------------
        # Inventory Health
        # -------------------------

        print("\nInventory Check")

        negative_inventory = InventoryItem.objects.filter(stock__lt=0)

        if negative_inventory.exists():

            print("WARNING: Negative inventory detected")

            for item in negative_inventory:
                print(item.name, item.stock)

        else:
            print("Inventory OK")

        # -------------------------
        # Low Stock Warning
        # -------------------------

        print("\nLow Stock Items")

        low_stock = InventoryItem.objects.filter(
            stock__lte=models.F("low_stock_threshold")
        )

        if low_stock.exists():

            for item in low_stock:
                print(item.name, "→", item.stock)

        else:
            print("No low stock items")

        # -------------------------
        # Top Selling Items
        # -------------------------

        print("\nTop Selling Items")

        top_items = OrderItem.objects.values(
            "menu_item__name"
        ).annotate(
            qty=Sum("quantity")
        ).order_by("-qty")[:10]

        for item in top_items:
            print(item["menu_item__name"], "→", item["qty"])

        # -------------------------
        # Table Distribution
        # -------------------------

        print("\nTable Order Distribution")

        table_orders = Order.objects.values(
            "table__name"
        ).annotate(
            count=Count("id")
        ).order_by("-count")

        for t in table_orders[:10]:
            print(t["table__name"], ":", t["count"])

        print("\nAudit Complete")