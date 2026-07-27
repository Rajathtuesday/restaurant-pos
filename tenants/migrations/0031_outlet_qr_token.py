import uuid

from django.db import migrations, models


def backfill_qr_tokens(apps, schema_editor):
    Outlet = apps.get_model("tenants", "Outlet")
    for outlet in Outlet.objects.all():
        outlet.qr_token = uuid.uuid4()
        outlet.save(update_fields=["qr_token"])


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0030_outlet_display_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='outlet',
            name='qr_token',
            field=models.UUIDField(null=True, help_text=(
                "Permanent secret for the outlet-wide 'Counter / Walk-in' menu QR "
                "-- for QSR/cafe outlets with no seating, so there's no Table to "
                "hang a per-table QR on. Orders placed via this token get "
                "table=None, same as a staff-created walk-in order."
            )),
        ),
        migrations.RunPython(backfill_qr_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='outlet',
            name='qr_token',
            field=models.UUIDField(default=uuid.uuid4, unique=True, help_text=(
                "Permanent secret for the outlet-wide 'Counter / Walk-in' menu QR "
                "-- for QSR/cafe outlets with no seating, so there's no Table to "
                "hang a per-table QR on. Orders placed via this token get "
                "table=None, same as a staff-created walk-in order."
            )),
        ),
    ]
