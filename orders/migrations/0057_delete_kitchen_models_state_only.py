# Phase 3 of the orders app split, step 3 of 3: KOTBatch, DailyKOTCounter,
# and KitchenMessage move to the new kitchen app. This migration only
# removes them from orders' migration STATE -- no database_operations, so
# the real tables are untouched. Must apply after 0056_alter_orderitem_kot.py
# has already repointed OrderItem.kot away from orders.KOTBatch, otherwise
# Django's state would have a dangling FK reference.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0056_alter_orderitem_kot'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='KOTBatch'),
                migrations.DeleteModel(name='DailyKOTCounter'),
                migrations.DeleteModel(name='KitchenMessage'),
            ],
            database_operations=[],
        ),
    ]
