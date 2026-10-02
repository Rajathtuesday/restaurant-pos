from django.core.management.base import BaseCommand
from devtools.scripts.concurrency_test import run


class Command(BaseCommand):

    help = "Test concurrent waiters ordering"

    def handle(self, *args, **kwargs):

        run()