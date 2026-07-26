# Phase 1 of the orders app split: state-only move of PrintJob from orders to
# printing. No database_operations -- the real table (orders_printjob) is
# untouched, only Django's migration state changes. Paired with
# orders/migrations/0054_delete_printjob_state_only.py, which removes
# PrintJob from orders' state in the same way. See db_table =
# "orders_printjob" on printing/models.py::PrintJob.Meta for why the table
# name still says "orders". Field list and index name reflect PrintJob's
# final state after orders/migrations/0044-0047 (claimed_at added, status
# choices include "processing", index renamed to include tenant).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenants', '0023_add_printjob'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='PrintJob',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('payload', models.JSONField()),
                        ('status', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('done', 'Done'), ('failed', 'Failed')], db_index=True, default='pending', max_length=10)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('done_at', models.DateTimeField(blank=True, null=True)),
                        ('claimed_at', models.DateTimeField(blank=True, null=True)),
                        ('error_msg', models.CharField(blank=True, default='', max_length=512)),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_printjob',
                        'ordering': ['created_at'],
                    },
                ),
                migrations.AddIndex(
                    model_name='printjob',
                    index=models.Index(fields=['tenant', 'outlet', 'status', 'created_at'], name='orders_prin_tenant__e90431_idx'),
                ),
            ],
            database_operations=[],
        ),
    ]
