# Phase 6 of the orders app split: state-only move of RazorpayQRCode and
# Refund from orders to payments. No database_operations -- the real
# tables (orders_razorpayqrcode, orders_refund) are untouched, only
# Django's migration state changes.
#
# Both models' FKs point INTO orders.Order/orders.Payment (the safe
# direction), so this is the standard 2-step pattern -- no reverse-FK
# complication like Kitchen's. orders/migrations/0060_delete_payments_models_state_only.py
# removes both from orders' state.
#
# Field lists and index names below reflect each model's final state:
#   RazorpayQRCode: orders/migrations/0049_razorpay_gateway.py (unchanged since)
#   Refund: orders/migrations/0022_refund.py, 0026 (status default pending->
#           already pending at creation... see 0026 for the actual default
#           change), 0048_refund_customer_complaint.py

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('orders', '0059_delete_tablemerge_model_state_only'),
        ('tenants', '0030_outlet_display_token'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='RazorpayQRCode',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('qr_code_id', models.CharField(max_length=100, unique=True)),
                        ('image_url', models.URLField()),
                        ('quoted_amount', models.DecimalField(decimal_places=2, max_digits=10)),
                        ('status', models.CharField(choices=[('active', 'Active'), ('paid', 'Paid'), ('expired', 'Expired'), ('closed', 'Closed')], default='active', max_length=20)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('expires_at', models.DateTimeField()),
                        ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='razorpay_qr_codes', to='orders.order')),
                        ('outlet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.outlet')),
                        ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='tenants.tenant')),
                    ],
                    options={
                        'db_table': 'orders_razorpayqrcode',
                    },
                ),
                migrations.AddIndex(
                    model_name='razorpayqrcode',
                    index=models.Index(fields=['order'], name='orders_razo_order_i_03566e_idx'),
                ),
                migrations.AddIndex(
                    model_name='razorpayqrcode',
                    index=models.Index(fields=['status'], name='orders_razo_status_5c6673_idx'),
                ),
                migrations.CreateModel(
                    name='Refund',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                        ('customer_complaint', models.CharField(blank=True, default='', help_text='What the customer said — visible to owner', max_length=500)),
                        ('reason', models.CharField(help_text="Manager's internal note for this refund", max_length=255)),
                        ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='refunds', to='orders.order')),
                        ('payment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='refunds', to='orders.payment')),
                        ('refunded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='refunds_issued', to='accounts.user')),
                    ],
                    options={
                        'db_table': 'orders_refund',
                    },
                ),
                migrations.AddIndex(
                    model_name='refund',
                    index=models.Index(fields=['order'], name='orders_refu_order_i_341cd7_idx'),
                ),
                migrations.AddIndex(
                    model_name='refund',
                    index=models.Index(fields=['payment'], name='orders_refu_payment_32acb6_idx'),
                ),
            ],
            database_operations=[],
        ),
    ]
