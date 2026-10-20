from django.core.management.base import BaseCommand, CommandError

from box.backup import BackupError, run_backup


class Command(BaseCommand):
    help = "Make a backup of this box's database now (rasova-box backup)."

    def handle(self, *args, **options):
        try:
            path = run_backup()
        except BackupError as e:
            raise CommandError(str(e))
        self.stdout.write(str(path))
