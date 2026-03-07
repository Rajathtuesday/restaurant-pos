from django.core.management.base import BaseCommand
from orders.models import Order, OrderItem, KOTBatch, Payment, WaiterCall, OrderEvent, OrderLock


class Command(BaseCommand):

    help = "Reset POS data"

    def handle(self, *args, **kwargs):

        print("Deleting POS data...")

        OrderItem.objects.all().delete()
        KOTBatch.objects.all().delete()
        Payment.objects.all().delete()
        WaiterCall.objects.all().delete()
        OrderEvent.objects.all().delete()
        OrderLock.objects.all().delete()
        Order.objects.all().delete()

        print("POS data cleared.")