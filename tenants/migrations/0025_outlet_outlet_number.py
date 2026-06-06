from django.db import migrations, models


def backfill_outlet_numbers(apps, schema_editor):
    Outlet = apps.get_model("tenants", "Outlet")
    tenants_seen = {}
    for outlet in Outlet.objects.order_by("tenant_id", "id"):
        tenants_seen[outlet.tenant_id] = tenants_seen.get(outlet.tenant_id, 0) + 1
        outlet.outlet_number = tenants_seen[outlet.tenant_id]
        outlet.save(update_fields=["outlet_number"])


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0024_printprofile_outlet_print_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="outlet",
            name="outlet_number",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="Sequential outlet number within the tenant (1, 2, 3…). Auto-assigned on creation.",
            ),
        ),
        migrations.RunPython(backfill_outlet_numbers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="outlet",
            constraint=models.UniqueConstraint(
                fields=["tenant", "outlet_number"],
                name="unique_outlet_number_per_tenant",
            ),
        ),
    ]
