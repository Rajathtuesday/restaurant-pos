"""
Open bills with more paid on them than their total: left that way before
orders/services/paid_guard.py (21 Nov 2026), when a dish came off or a promo
went on after a payment.

    python manage.py bills_below_paid            list them (changes nothing)
    python manage.py bills_below_paid --apply    bring cover payments down to the bill

A cover is a voucher: bringing it down moves no money, and what comes off is
spendable on the cover again (on its night). Money paid in cash, UPI or card
is never moved here: each such bill is listed with the amount to give back,
which a manager does from the bill screen (Give Back, with their PIN), so it
is counted in the drawer.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce

from orders.models import Order
from orders.services.paid_guard import _pending_refunds, _shrink_covers


def overpaid_bills():
    zero = Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))
    return (Order.objects.filter(status__in=("open", "billing"))
            .annotate(paid=Coalesce(Sum("payments__amount"), zero))
            .filter(paid__gt=F("grand_total"))
            .select_related("tenant", "outlet").order_by("tenant_id", "id"))


class Command(BaseCommand):
    help = "List open bills with more paid than their total; --apply brings cover payments down to the bill."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Bring cover payments down to the bill.")

    def handle(self, *args, **options):
        rows = list(overpaid_bills())
        if not rows:
            self.stdout.write("No open bill has more paid than its total.")
            return
        for listed in rows:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=listed.pk)
                over = order.amount_paid() - _pending_refunds(order) - order.grand_total
                if over <= 0:
                    continue
                where = f"{order.tenant.name} / {order.outlet.name}, bill #{order.display_number} (order {order.id})"
                on_covers = (order.payments.filter(method="cover").aggregate(total=Sum("amount"))["total"]
                             or over * 0)
                from_covers = min(over, on_covers)
                if options["apply"] and from_covers > 0:
                    _shrink_covers(order, from_covers)
                    self.stdout.write(f"{where}: cover brought down by {from_covers}.")
                elif from_covers > 0:
                    self.stdout.write(f"{where}: {from_covers} over on a cover (--apply brings it down).")
                money = over - from_covers
                if money > 0:
                    self.stdout.write(f"{where}: {money} paid in money over the bill. "
                                      "A manager gives it back on the bill screen (Give Back).")
        if not options["apply"]:
            self.stdout.write("Nothing was changed. Run with --apply to bring cover payments down to the bill.")
