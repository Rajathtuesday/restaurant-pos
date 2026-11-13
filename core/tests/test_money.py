"""
Money written one way (core/money.py). The same table of examples is read
by static/js/tests/money.test.mjs for the screens' copy of the rule, so the
two cannot drift apart.

Run: python manage.py test core.tests.test_money
"""
import json
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from core.money import rupees

EXAMPLES = json.loads((Path(__file__).parent / "money_examples.json").read_text(encoding="utf-8"))


class RupeesTest(SimpleTestCase):

    def test_every_example(self):
        for given, expected in EXAMPLES:
            self.assertEqual(rupees(given), expected, given)

    def test_numbers_as_well_as_text(self):
        self.assertEqual(rupees(Decimal("20.50")), "₹20.50")
        self.assertEqual(rupees(430), "₹430")
        self.assertEqual(rupees(None), "₹0")
