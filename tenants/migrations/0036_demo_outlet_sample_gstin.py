from django.db import migrations
from django.db.models import Q

# Frozen here: a migration never reads app code that may change later.
DEMO_TENANT_SLUG = "demo-bistro"
SAMPLE_GSTIN = "29ABCDE1234F1Z5"   # made up, the Outlet help text's example


def give_the_demo_its_gstin(apps, schema_editor):
    """The demo restaurant is there to show GST bills, and an outlet without
    a GSTIN charges no GST. The demo reset puts the sample GSTIN back every
    two hours; this puts it there at deploy, so the live demo never bills a
    reset's length without GST. Nothing happens where there is no demo."""
    Outlet = apps.get_model("tenants", "Outlet")
    Outlet.objects.filter(tenant__slug=DEMO_TENANT_SLUG).filter(
        Q(gst_no__isnull=True) | Q(gst_no="")
    ).update(gst_no=SAMPLE_GSTIN)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0035_liquor_vat"),
    ]

    operations = [
        migrations.RunPython(give_the_demo_its_gstin, migrations.RunPython.noop),
    ]
