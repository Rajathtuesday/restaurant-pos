import uuid
from django.db import migrations, models


def _assign_keys(apps, schema_editor):
    Outlet = apps.get_model("tenants", "Outlet")
    for outlet in Outlet.objects.filter(print_agent_key__isnull=True):
        outlet.print_agent_key = uuid.uuid4()
        outlet.save(update_fields=["print_agent_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0021_outlet_union_territory"),
    ]

    operations = [
        # Step 1: add without unique so existing rows get a NULL first
        migrations.AddField(
            model_name="outlet",
            name="print_agent_key",
            field=models.UUIDField(null=True, blank=True),
        ),
        # Step 2: fill every existing row with a unique UUID
        migrations.RunPython(_assign_keys, migrations.RunPython.noop),
        # Step 3: make it NOT NULL + unique
        migrations.AlterField(
            model_name="outlet",
            name="print_agent_key",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                help_text="Secret key the Rasova Agent uses to poll for print jobs.",
            ),
        ),
    ]
