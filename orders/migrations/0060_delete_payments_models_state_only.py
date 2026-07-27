# Phase 6 of the orders app split: RazorpayQRCode and Refund move to the
# new payments app. This migration only removes them from orders'
# migration STATE -- no database_operations, so the real tables
# (orders_razorpayqrcode, orders_refund) are untouched.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0059_delete_tablemerge_model_state_only'),
        ('payments', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='RazorpayQRCode'),
                migrations.DeleteModel(name='Refund'),
            ],
            database_operations=[],
        ),
    ]
