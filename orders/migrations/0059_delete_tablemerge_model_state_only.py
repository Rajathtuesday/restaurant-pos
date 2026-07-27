# Phase 5 of the orders app split: TableMerge moves to the new tablemerge
# app. This migration only removes it from orders' migration STATE -- no
# database_operations, so the real tables (orders_tablemerge,
# orders_tablemerge_tables) are untouched.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0058_delete_waitercall_model_state_only'),
        ('tablemerge', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='TableMerge'),
            ],
            database_operations=[],
        ),
    ]
