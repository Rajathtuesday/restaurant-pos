from django.db import migrations


def clear_orphaned_low_stock_alerts(apps, schema_editor):
    """
    Every low_stock Notification created before this app's `item` field
    existed (migration 0003) has item=NULL. The new dedup logic in
    notifications/services/notification_service.py matches on item_id, so
    a NULL-item row can never be deduped against or auto-cleared by a
    later, correctly-tagged alert for the same item -- it just sits there
    forever, which is why the header badge stayed inflated even after the
    dedup fix shipped. These rows predate the fix and there's no reliable
    way to tell which items they were about without parsing free text, so
    the honest reset is to mark them read: anything still genuinely low
    re-alerts cleanly, correctly deduped, the next time it's sold or
    checked.
    """
    Notification = apps.get_model("notifications", "Notification")
    Notification.objects.filter(
        type="low_stock", item__isnull=True, is_read=False,
    ).update(is_read=True)


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0003_notification_item"),
    ]

    operations = [
        migrations.RunPython(clear_orphaned_low_stock_alerts, migrations.RunPython.noop),
    ]
