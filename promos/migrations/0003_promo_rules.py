# A promo's uses are now the bills holding it (Promo.uses), not a counter.
#
# The column stays in the database for one release, with a database default
# so new rows still insert, and leaves only Django's model now: workers
# still running the old code during a deploy, or a rollback, keep reading
# it without errors. Drop the column in a later release.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('promos', '0002_archive_promos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='promo',
            name='usage_count',
            field=models.PositiveIntegerField(default=0, db_default=0),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name='promo', name='usage_count'),
            ],
        ),
    ]
