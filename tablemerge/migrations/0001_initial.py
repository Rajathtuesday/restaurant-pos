# Phase 5 of the orders app split: state-only move of TableMerge from
# orders to tablemerge. No database_operations -- the real tables
# (orders_tablemerge, orders_tablemerge_tables) are untouched, only
# Django's migration state changes.
#
# The only ManyToManyField in the whole split: `tables` gets db_table=
# pinned explicitly on the field itself (there's no Meta for M2M through
# tables) so Django's auto-created through table keeps its original name
# instead of being renamed to tablemerge_tablemerge_tables to match this
# app's label. Column names inside that table (tablemerge_id, table_id)
# are derived from the model names, not the app label, so they're
# unaffected by the move -- only the table's own name needed pinning.
#
# Standard 2-step pattern -- no reverse-FK complication like Kitchen's,
# since both primary_table and tables point INTO orders.Table (the safe
# direction). orders/migrations/0059_delete_tablemerge_model_state_only.py
# removes it from orders' state.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('orders', '0058_delete_waitercall_model_state_only'),
        ('tenants', '0030_outlet_display_token'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='TableMerge',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('is_active', models.BooleanField(default=True)),
                        ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='accounts.user')),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('primary_table', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='merged_primary', to='orders.table')),
                        ('tables', models.ManyToManyField(db_table='orders_tablemerge_tables', to='orders.table')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_tablemerge',
                    },
                ),
                migrations.AddIndex(
                    model_name='tablemerge',
                    index=models.Index(fields=['tenant', 'outlet', 'is_active'], name='orders_tabl_tenant__4ffa85_idx'),
                ),
            ],
            database_operations=[],
        ),
    ]
