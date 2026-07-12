"""
Continuation of 0027: two more orphaned NOT NULL columns on tenants_tenant with no
matching model field and no code references — `font_family` and `primary_color`.
Like `plan`, they were left NOT NULL with no default when their model fields were
removed, so ORM Tenant inserts still failed (just on the next column).

Relax them to nullable so tenant creation works. Postgres-guarded.
"""
from django.db import migrations

_ORPHAN_COLUMNS = ("font_family", "primary_color")


def drop_not_null(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for col in _ORPHAN_COLUMNS:
        schema_editor.execute(
            f'ALTER TABLE tenants_tenant ALTER COLUMN "{col}" DROP NOT NULL;'
        )


def restore_not_null(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for col in _ORPHAN_COLUMNS:
        schema_editor.execute(
            f'UPDATE tenants_tenant SET "{col}" = \'\' WHERE "{col}" IS NULL;'
        )
        schema_editor.execute(
            f'ALTER TABLE tenants_tenant ALTER COLUMN "{col}" SET NOT NULL;'
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0027_fix_orphan_plan_not_null"),
    ]

    operations = [
        migrations.RunPython(drop_not_null, restore_not_null),
    ]
