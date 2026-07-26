import uuid

from django.db import migrations, models


def backfill_display_tokens(apps, schema_editor):
    Outlet = apps.get_model("tenants", "Outlet")
    for outlet in Outlet.objects.all():
        outlet.display_token = uuid.uuid4()
        outlet.save(update_fields=["display_token"])


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0029_alter_tenantfeatureauditlog_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='outlet',
            name='display_token',
            field=models.UUIDField(null=True, help_text=(
                "Permanent secret used in the public 'Now Serving' display board "
                "URL (a TV/monitor at the pickup counter) -- same pattern as "
                "Table.qr_token, never regenerated."
            )),
        ),
        migrations.RunPython(backfill_display_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='outlet',
            name='display_token',
            field=models.UUIDField(default=uuid.uuid4, unique=True, help_text=(
                "Permanent secret used in the public 'Now Serving' display board "
                "URL (a TV/monitor at the pickup counter) -- same pattern as "
                "Table.qr_token, never regenerated."
            )),
        ),
    ]
