"""
Fix schema drift: the `plan` column (added in 0008_tenant_plan_tenant_trial_ends_at)
was later removed from the Tenant model, but no migration dropped or relaxed the
column. It remained NOT NULL with no default and no matching model field, so every
ORM Tenant insert (e.g. the superuser "create restaurant" onboarding flow) failed
with an IntegrityError: null value in column "plan".

Since `plan` is no longer a model field and is referenced nowhere in the codebase,
we drop its NOT NULL constraint so tenant creation works again. (The column is left
in place, now nullable, rather than dropped outright to keep this reversible and
non-destructive; a dedicated cleanup can drop the dead column later.)

Guarded to PostgreSQL — SQLite doesn't support ALTER COLUMN ... DROP NOT NULL, and
the real deployments (prod + the Postgres test database) are where this bug bites.
"""
from django.db import migrations


def drop_plan_not_null(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute('ALTER TABLE tenants_tenant ALTER COLUMN "plan" DROP NOT NULL;')


def restore_plan_not_null(apps, schema_editor):
    # Reverse is best-effort: only re-add the constraint if no NULLs exist.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            'UPDATE tenants_tenant SET "plan" = \'\' WHERE "plan" IS NULL;'
        )
        schema_editor.execute('ALTER TABLE tenants_tenant ALTER COLUMN "plan" SET NOT NULL;')


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0026_add_feature_audit_log"),
    ]

    operations = [
        migrations.RunPython(drop_plan_not_null, restore_plan_not_null),
    ]
