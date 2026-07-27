# Phase 4 of the orders app split: WaiterCall moves to the new waiter app.
# This migration only removes it from orders' migration STATE -- no
# database_operations, so the real table (orders_waitercall) is untouched.
# Must apply after waiter/migrations/0001_initial.py has already created
# WaiterCall in waiter's state.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0057_delete_kitchen_models_state_only'),
        ('waiter', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='WaiterCall'),
            ],
            database_operations=[],
        ),
    ]
