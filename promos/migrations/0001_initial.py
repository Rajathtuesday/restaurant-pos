# Phase 0 of the orders app split: state-only move of Promo from orders to
# promos. No database_operations -- the real table (orders_promo) is
# untouched, only Django's migration state changes. Paired with
# orders/migrations/0053_delete_promo_state_only.py, which removes Promo
# from orders' state in the same way. See db_table = "orders_promo" on
# promos/models.py::Promo.Meta for why the table name still says "orders".

import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenants', '0009_outlet_closing_time_outlet_opening_time'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='Promo',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('name', models.CharField(help_text="e.g. 'Happy Hours 20%'", max_length=120)),
                        ('code', models.CharField(blank=True, help_text='Short code printed on bill (e.g. HH20). Unique per tenant.', max_length=30)),
                        ('description', models.TextField(blank=True, help_text='T&C / internal notes visible to staff')),
                        ('discount_type', models.CharField(choices=[('percentage', '% Off'), ('amount', '₹ Flat Off')], default='percentage', max_length=12)),
                        ('discount_value', models.DecimalField(decimal_places=2, max_digits=8)),
                        ('min_order_value', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Minimum order subtotal to apply this promo (0 = no minimum)', max_digits=10)),
                        ('max_uses', models.PositiveIntegerField(blank=True, help_text='Leave blank for unlimited uses', null=True)),
                        ('usage_count', models.PositiveIntegerField(default=0)),
                        ('valid_from', models.DateField(blank=True, null=True)),
                        ('valid_until', models.DateField(blank=True, null=True)),
                        ('is_active', models.BooleanField(default=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('outlet', models.ForeignKey(blank=True, help_text='Leave blank to broadcast across all outlets of this tenant.', null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_promo',
                        'ordering': ['name'],
                    },
                ),
                migrations.AddIndex(
                    model_name='promo',
                    index=models.Index(fields=['tenant', 'outlet', 'is_active'], name='orders_prom_tenant__495286_idx'),
                ),
                migrations.AddConstraint(
                    model_name='promo',
                    constraint=models.UniqueConstraint(condition=models.Q(('code__gt', '')), fields=('tenant', 'code'), name='unique_promo_code_per_tenant'),
                ),
            ],
            database_operations=[],
        ),
    ]
