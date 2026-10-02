from django.core.management.base import BaseCommand
from devtools.scripts.seed_restaurant import run

class Command(BaseCommand):
    help = "Seed restaurant data for testing"
    def handle(self, *args, **options):
        run()