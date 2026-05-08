# orders/migrations/0033_dailyordercounter_orderevent_composite_idx.py
#
# Changes:
#   1. Adds DailyOrderCounter model — per-tenant/outlet/day sequential counter
#      used by Order.save() to generate INV-YYYYMMDD-NNNN order numbers.
#
#   2. Adds composite index on OrderEvent(tenant, outlet, event_type, created_at)
#      to efficiently serve the bypass daily-limit counter query, which filters
#      on all four columns. Previously only individual-column indexes existed.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0032_order_round_off'),
        ('tenants', '__first__'),
    ]

    operations = [
        # ---------------------------------------------------------------
        # 1. DailyOrderCounter
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name='DailyOrderCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('value', models.IntegerField(default=0)),
                ('outlet', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.outlet',
                )),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.tenant',
                )),
            ],
            options={
                'unique_together': {('tenant', 'outlet', 'date')},
            },
        ),

        # ---------------------------------------------------------------
        # 2. Composite index on OrderEvent for bypass counter query
        # ---------------------------------------------------------------
        migrations.AddIndex(
            model_name='orderevent',
            index=models.Index(
                fields=['tenant', 'outlet', 'event_type', 'created_at'],
                name='orderevent_tenant_type_idx',
            ),
        ),
    ]
