import json

from django.core.management.base import BaseCommand, CommandError

from box.updates import BundleError, verify_bundle


class Command(BaseCommand):
    help = "Check an update bundle is Rasova's and complete (rasova-box update runs this first)."

    def add_arguments(self, parser):
        parser.add_argument("folder")

    def handle(self, *args, folder, **options):
        try:
            manifest = verify_bundle(folder)
        except BundleError as e:
            raise CommandError(str(e))
        self.stdout.write(json.dumps(manifest))
