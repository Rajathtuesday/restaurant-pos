# Phase 1 of the orders app split: PrintJob moves to the new printing app.
# This migration only removes PrintJob from orders' migration STATE -- no
# database_operations, so the real table (orders_printjob) is untouched.
# Must apply after printing.0001_initial creates PrintJob in the new app's
# state.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0053_delete_promo_state_only'),
        ('printing', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='PrintJob'),
            ],
            database_operations=[],
        ),
    ]
