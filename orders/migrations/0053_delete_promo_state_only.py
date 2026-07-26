# Phase 0 of the orders app split: Promo moves to the new promos app. This
# migration only removes Promo from orders' migration STATE -- no
# database_operations, so the real table (orders_promo) is untouched.
# Must apply after promos.0001_initial creates Promo in the new app's state.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0052_alter_orderevent_event_type'),
        ('promos', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='Promo'),
            ],
            database_operations=[],
        ),
    ]
