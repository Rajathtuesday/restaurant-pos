# Phase 4 of the orders app split: state-only move of WaiterCall from
# orders to waiter. No database_operations -- the real table
# (orders_waitercall) is untouched, only Django's migration state changes.
#
# Unlike Kitchen (Phase 3), nothing has a reverse FK into WaiterCall, so
# this is the standard 2-step pattern: this migration creates the model in
# waiter's state, and orders/migrations/0058_delete_waitercall_model_state_only.py
# removes it from orders' state.
#
# Field list and constraint reflect the model's state since creation
# (orders/migrations/0001_initial.py, constraint added in 0014_...).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('orders', '0057_delete_kitchen_models_state_only'),
        ('tenants', '0030_outlet_display_token'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='WaiterCall',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('is_resolved', models.BooleanField(default=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('table', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='orders.table')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_waitercall',
                    },
                ),
                migrations.AddConstraint(
                    model_name='waitercall',
                    constraint=models.UniqueConstraint(condition=models.Q(('is_resolved', False)), fields=('table',), name='one_active_waiter_call_per_table'),
                ),
            ],
            database_operations=[],
        ),
    ]
