# Phase 2 of the orders app split: DailyTokenCounter, TokenOrder, and
# DailyOnlineTokenCounter move to the new tokens app. This migration only
# removes them from orders' migration STATE -- no database_operations, so
# the real tables are untouched. Must apply after tokens.0001_initial
# creates them in the new app's state.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0054_delete_printjob_state_only'),
        ('tokens', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='DailyTokenCounter'),
                migrations.DeleteModel(name='TokenOrder'),
                migrations.DeleteModel(name='DailyOnlineTokenCounter'),
            ],
            database_operations=[],
        ),
    ]
