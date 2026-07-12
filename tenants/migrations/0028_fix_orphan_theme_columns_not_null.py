"""
Continuation of 0027 for two more drifted NOT NULL columns with no matching
model field and no code references: `font_family` and `primary_color`. Same
story as `plan` (their add-migration source was removed from the graph), so
they exist only on already-migrated databases and still broke Tenant inserts on
the next column after `plan` was fixed.

Idempotent + portable: only relaxes a column if it actually exists. Postgres-guarded.
"""
from django.db import migrations

_ORPHAN_COLUMNS = ("font_family", "primary_color")


def _column_exists(schema_editor, table, column):
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        return cur.fetchone() is not None


def drop_not_null(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for col in _ORPHAN_COLUMNS:
        if _column_exists(schema_editor, "tenants_tenant", col):
            schema_editor.execute(
                f'ALTER TABLE tenants_tenant ALTER COLUMN "{col}" DROP NOT NULL;'
            )


def restore_not_null(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for col in _ORPHAN_COLUMNS:
        if _column_exists(schema_editor, "tenants_tenant", col):
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
