# Phase 3 of the orders app split, step 2 of 3: OrderItem.kot is the only
# reverse dependency in the whole split (a permanent-core model FK-ing into
# a satellite model). This state-only AlterField repoints its target from
# 'orders.kotbatch' to 'kitchen.kotbatch', now that kitchen.0001_initial has
# created KOTBatch there. No database_operations -- the physical FK column
# and constraint, pointing at the same physical table (orders_kotbatch),
# are completely unchanged; only Django's bookkeeping of which app's model
# class governs this field changes. Must apply before
# 0057_delete_kitchen_models_state_only.py removes KOTBatch from orders'
# state entirely.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0055_delete_token_models_state_only'),
        ('kitchen', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='orderitem',
                    name='kot',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='items', to='kitchen.kotbatch'),
                ),
            ],
            database_operations=[],
        ),
    ]
