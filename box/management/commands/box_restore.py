from django.core.management.base import BaseCommand, CommandError

from box.backup import BackupError, restore


class Command(BaseCommand):
    help = ("Replace this box's database with a backup. Run only with the app stopped: "
            "rasova-box restore <file> does that.")

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--yes", action="store_true", help="Really replace everything.")

    def handle(self, *args, path, yes, **options):
        if not yes:
            raise CommandError("This replaces every bill, menu and setting with the backup's. Add --yes to do it.")
        try:
            restore(path)
        except BackupError as e:
            raise CommandError(str(e))
        # On a replacement box (no licence yet): the licence the backups carry.
        from box import license
        from box.backup import LICENCE_COPY, backup_dir
        if license.adopt(backup_dir() / LICENCE_COPY):
            self.stdout.write("licence taken from the backup drive")
        self.stdout.write("restored")
