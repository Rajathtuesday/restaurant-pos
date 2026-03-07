from django.core.management.base import BaseCommand
from orders.scripts.simulate_rush import run


class Command(BaseCommand):

    help = "Simulate restaurant rush"

    def handle(self, *args, **kwargs):

        run()