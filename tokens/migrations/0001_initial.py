# Phase 2 of the orders app split: state-only move of DailyTokenCounter,
# TokenOrder, and DailyOnlineTokenCounter from orders to tokens. No
# database_operations -- the real tables (orders_dailytokencounter,
# orders_tokenorder, orders_dailyonlinetokencounter) are untouched, only
# Django's migration state changes. Paired with
# orders/migrations/0055_delete_token_models_state_only.py, which removes
# these three models from orders' state in the same way. Field lists,
# db_table values, and index names reflect their final state after
# orders/migrations/0037-0043 (DailyTokenCounter/DailyOnlineTokenCounter had
# their explicit indexes REMOVED in 0043 as redundant with unique_together --
# confirmed neither carries an indexes= list today; only TokenOrder does).
#
# TokenOrder.order depends on orders.Order, so this migration depends on the
# latest orders migration to ensure Order exists in migration state first.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('orders', '0054_delete_printjob_state_only'),
        ('tenants', '0010_tenant_subscription_end_date_tenant_subscription_fee_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='DailyTokenCounter',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('date', models.DateField()),
                        ('value', models.PositiveIntegerField(default=0)),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_dailytokencounter',
                        'unique_together': {('outlet', 'date')},
                    },
                ),
                migrations.CreateModel(
                    name='TokenOrder',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('token_number', models.PositiveIntegerField()),
                        ('date', models.DateField()),
                        ('is_online', models.BooleanField(default=False, help_text='True for aggregator (Zomato/Swiggy/web) orders; False for walk-in counter orders.')),
                        ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='token', to='orders.order')),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_tokenorder',
                        'unique_together': {('outlet', 'token_number', 'date', 'is_online')},
                    },
                ),
                migrations.AddIndex(
                    model_name='tokenorder',
                    index=models.Index(fields=['outlet', 'date'], name='orders_toke_outlet__635998_idx'),
                ),
                migrations.AddIndex(
                    model_name='tokenorder',
                    index=models.Index(fields=['outlet', 'date', 'is_online'], name='orders_toke_outlet__a9e5a6_idx'),
                ),
                migrations.CreateModel(
                    name='DailyOnlineTokenCounter',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('date', models.DateField()),
                        ('value', models.PositiveIntegerField(default=0)),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_dailyonlinetokencounter',
                        'unique_together': {('outlet', 'date')},
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
