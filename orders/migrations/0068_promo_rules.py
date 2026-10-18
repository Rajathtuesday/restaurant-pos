# Promo rules, round 2 (18 Oct 2026): a bill keeps its promo's code and
# minimum order (tax_engine rule 1), records what its bill discount took,
# and (0069) the database refuses a promo with no discount behind it.

from decimal import Decimal

from django.db import migrations, models


def copy_promo_details(apps, schema_editor):
    """Bills holding a promo get its code and minimum, so an open bill
    follows the minimum from now on. A bill left with a promo and nothing
    off (P2) loses the promo, so the new rule can be added."""
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(promo__isnull=False, discount_value__lte=0).update(promo=None, promo_name="")
    for order in Order.objects.filter(promo__isnull=False).select_related("promo").only(
            "id", "promo__code", "promo__min_order_value"):
        Order.objects.filter(pk=order.pk).update(promo_code=order.promo.code,
                                                 promo_min_order=order.promo.min_order_value)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0067_offers_on_lines'),
        ('promos', '0003_promo_rules'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='order_discount_total',
            field=models.DecimalField(db_default=Decimal('0.00'), decimal_places=2, default=Decimal('0.00'), max_digits=12),
        ),
        migrations.AddField(
            model_name='order',
            name='promo_code',
            field=models.CharField(blank=True, db_default='', default='', max_length=30),
        ),
        migrations.AddField(
            model_name='order',
            name='promo_min_order',
            field=models.DecimalField(db_default=Decimal('0.00'), decimal_places=2, default=Decimal('0.00'), max_digits=10),
        ),
        migrations.RunPython(copy_promo_details, migrations.RunPython.noop),
    ]
