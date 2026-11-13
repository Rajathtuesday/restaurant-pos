"""
Every new migration leaves the previous version able to run (B1, 13 Nov 2026).

deploy.sh can put the previous version back by itself (core/tests/
test_deploy_script.py), but it never rolls the database back: the old code
runs against the new schema. That is only safe if a migration adds and
never takes away. So, from 13 Nov 2026 on, a migration may not:

  remove or rename a field, a model or a table (the old code still reads it)
  add a column that is NOT NULL with no database default (the old code
  inserts rows without it, and the insert fails)
  make an existing column NOT NULL with no default (the same)

Removing something is done in two releases: release 1 stops using it,
release 2 removes it, listed in ALLOWED below with its reason (central
kitchen's tables will be the first).

Migrations older than the cut-off are not checked; 13 Nov's own two are,
so this test runs on real files from the start.

Run: python manage.py test core.tests.test_migrations_additive
"""
import sys
from pathlib import Path

from django.conf import settings
from django.db import migrations
from django.db.migrations.loader import MigrationLoader
from django.db.models import NOT_PROVIDED
from django.test import SimpleTestCase

# The newest migration per app that is NOT checked (everything before 13 Nov).
CHECKED_AFTER = {
    "accounts": 11, "billing": 1, "box": 2, "covers": 1, "crm": 4, "finance": 1, "inventory": 19,
    "kitchen": 1, "menu": 17, "notifications": 4, "offers": 2, "orders": 71, "payments": 1,
    "printing": 1, "promos": 3, "setup": 13, "shifts": 6, "tablemerge": 1, "tenants": 41,
    "tokens": 2, "waiter": 1,
}

# (app, migration name): why this one may take something away. The second
# release of a two-release removal goes here.
ALLOWED = {}

REMOVING = (migrations.RemoveField, migrations.DeleteModel, migrations.RenameField, migrations.RenameModel,
            migrations.AlterModelTable)


def _not_null_without_default(field):
    return (not field.null and field.default is NOT_PROVIDED
            and getattr(field, "db_default", NOT_PROVIDED) is NOT_PROVIDED and not field.primary_key)


def problems_in(migration):
    found = []
    for op in migration.operations:
        name = type(op).__name__
        if isinstance(op, REMOVING):
            found.append(f"{name} {getattr(op, 'name', '')}".strip())
        elif isinstance(op, migrations.AddField) and _not_null_without_default(op.field) and op.preserve_default:
            found.append(f"AddField {op.model_name}.{op.name} is NOT NULL with no default")
        elif isinstance(op, migrations.AddField) and not op.field.null and not op.preserve_default \
                and getattr(op.field, "db_default", NOT_PROVIDED) is NOT_PROVIDED:
            found.append(f"AddField {op.model_name}.{op.name} is NOT NULL with a one-off default only")
        elif isinstance(op, migrations.AlterField) and _not_null_without_default(op.field):
            found.append(f"AlterField {op.model_name}.{op.name} leaves it NOT NULL with no default")
    return found


class MigrationsOnlyAddTest(SimpleTestCase):

    def checked(self):
        """Rasova's own migrations past the cut-off (a new app: all of them)."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        root = Path(settings.BASE_DIR).resolve()
        for (app, name), migration in sorted(loader.disk_migrations.items()):
            path = Path(getattr(sys.modules.get(migration.__module__), "__file__", "") or "/").resolve()
            # (Path compares case as the system does: F:\pos and f:\pos are one folder on Windows.)
            if not path.is_relative_to(root) or ".venv" in path.parts or "site-packages" in path.parts:
                continue                  # Django's own and third-party apps
            number = int(name[:4]) if name[:4].isdigit() else 0
            if app not in CHECKED_AFTER or number > CHECKED_AFTER[app]:
                yield app, name, migration

    def test_new_migrations_only_add(self):
        bad = {}
        seen = []
        for app, name, migration in self.checked():
            seen.append(f"{app}.{name}")
            found = problems_in(migration)
            if found and (app, name) not in ALLOWED:
                bad[f"{app}.{name}"] = found
        self.assertIn("orders.0072_order_request", seen, "the cut-off moved: today's migrations must be checked")
        self.assertIn("tenants.0042_outlet_print_agent_seen_at", seen)
        self.assertEqual(bad, {}, "\nThese migrations take something away the previous version still uses. "
                                  "Do it in two releases, and list the second in ALLOWED with its reason.")

    def test_it_catches_what_it_should(self):
        from django.db import models

        class Fake:
            operations = [
                migrations.RemoveField("order", "notes"),
                migrations.AddField("order", "code", models.CharField(max_length=5), preserve_default=False),
                migrations.AddField("order", "flag", models.BooleanField(default=False)),
                migrations.AddField("order", "seen", models.DateTimeField(null=True)),
                migrations.AlterField("order", "name", models.CharField(max_length=9)),
                migrations.AlterField("order", "label", models.CharField(max_length=9, default="")),
            ]
        found = problems_in(Fake)
        self.assertEqual(found, [
            "RemoveField notes",
            "AddField order.code is NOT NULL with a one-off default only",
            "AlterField order.name leaves it NOT NULL with no default",
        ])
