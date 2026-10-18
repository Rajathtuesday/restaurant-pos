# A bill holding a promo carries its discount (18 Oct 2026). Its own
# migration: Postgres won't alter the table in the transaction that just
# updated its rows in 0068 (pending foreign-key trigger events).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0068_promo_rules'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='order',
            constraint=models.CheckConstraint(condition=models.Q(('promo__isnull', True), ('discount_value__gt', 0), _connector='OR'), name='order_promo_has_discount'),
        ),
    ]
