"""
Fix schema drift on the `plan` column.

History context: an earlier migration (0008_tenant_plan_tenant_trial_ends_at)
added Tenant.plan, then that migration's source was later deleted from the
graph. The result is that the CURRENT .py migration graph never creates the
`plan` column, but databases migrated back when it existed still carry it —
as a NOT NULL column with no default and no matching model field. On those
databases every ORM Tenant insert (the superuser create-restaurant flow)
failed with: null value in column "plan".

This migration relaxes `plan` to nullable so tenant creation works again. It is
IDEMPOTENT and portable: on a fresh database the column doesn't exist and this
is a no-op; on a drifted database it drops the NOT NULL constraint. Guarded to
PostgreSQL (the real deployments + the Postgres test DB).
"""
from django.db import migrations


def _column_exists(schema_editor, table, column):
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        return cur.fetchone() is not None


def drop_plan_not_null(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    if _column_exists(schema_editor, "tenants_tenant", "plan"):
        schema_editor.execute('ALTER TABLE tenants_tenant ALTER COLUMN "plan" DROP NOT NULL;')


def restore_plan_not_null(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    if _column_exists(schema_editor, "tenants_tenant", "plan"):
        schema_editor.execute('UPDATE tenants_tenant SET "plan" = \'\' WHERE "plan" IS NULL;')
        schema_editor.execute('ALTER TABLE tenants_tenant ALTER COLUMN "plan" SET NOT NULL;')


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0026_add_feature_audit_log"),
    ]

    operations = [
        migrations.RunPython(drop_plan_not_null, restore_plan_not_null),
    ]
