# Phase 3 of the orders app split: state-only move of KOTBatch,
# DailyKOTCounter, and KitchenMessage from orders to kitchen. No
# database_operations -- the real tables (orders_kotbatch,
# orders_dailykotcounter, orders_kitchenmessage) are untouched, only
# Django's migration state changes.
#
# Unlike every prior phase, this one has a genuine reverse dependency:
# orders.OrderItem.kot is a real ForeignKey into KOTBatch. That FK is
# repointed to "kitchen.KOTBatch" by a SEPARATE, later orders migration
# (0056_alter_orderitem_kot.py) which must apply AFTER this one creates
# KOTBatch in kitchen's state. orders/migrations/0057_delete_kitchen_models_state_only.py
# then removes these three models from orders' state, once nothing in
# orders' state references them anymore.
#
# Field lists, db_table values, and index names below reflect each model's
# final state, confirmed by reading the full migration history:
#   KOTBatch: orders/migrations/0002, 0006, 0007, 0008, 0010, 0012, 0014, 0026
#   DailyKOTCounter: 0009, 0026, 0029
#   KitchenMessage: 0021 (unchanged since)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('orders', '0055_delete_token_models_state_only'),
        ('tenants', '0010_tenant_subscription_end_date_tenant_subscription_fee_and_more'),
        ('setup', '0004_kitchenstation_printer_ip_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='KOTBatch',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('kot_number', models.IntegerField()),
                        ('status', models.CharField(choices=[('confirmed', 'Confirmed'), ('preparing', 'Preparing'), ('ready', 'Ready')], default='confirmed', max_length=20)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='kots', to='orders.order')),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('station', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='kots', to='setup.kitchenstation')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_kotbatch',
                        'unique_together': {('order', 'kot_number')},
                    },
                ),
                migrations.AddIndex(
                    model_name='kotbatch',
                    index=models.Index(fields=['tenant', 'outlet'], name='orders_kotb_tenant__8938f0_idx'),
                ),
                migrations.AddIndex(
                    model_name='kotbatch',
                    index=models.Index(fields=['tenant', 'outlet', 'status'], name='kotbatch_tenant_outlet_status'),
                ),
                migrations.CreateModel(
                    name='DailyKOTCounter',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('date', models.DateField()),
                        ('value', models.IntegerField(default=0)),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_dailykotcounter',
                        'unique_together': {('tenant', 'outlet', 'date')},
                    },
                ),
                migrations.CreateModel(
                    name='KitchenMessage',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('message', models.CharField(max_length=255)),
                        ('is_resolved', models.BooleanField(default=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='kitchen_messages', to='orders.order')),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_kitchenmessage',
                    },
                ),
                migrations.AddIndex(
                    model_name='kitchenmessage',
                    index=models.Index(fields=['tenant', 'outlet', 'is_resolved'], name='orders_kitc_tenant__89aa92_idx'),
                ),
            ],
            database_operations=[],
        ),
    ]
