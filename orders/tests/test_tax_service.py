# orders/tests/test_tax_service.py
"""
split_cgst_sgst is the CGST/SGST 50/50 split: CGST half the GST rounded half
up, SGST the rest. It began as the shared copy of three drifting ones; since
27 Sep 2026 the tax engine owns the rule (orders/services/tax_engine.py,
split_gst) and this name stays for its callers (the cgst_total and
sgst_total properties). A pure-function test on the rule itself.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from orders.services.tax_service import split_cgst_sgst


class SplitCgstSgstTests(SimpleTestCase):

    def test_even_split(self):
        cgst, sgst = split_cgst_sgst(Decimal("100.00"))
        self.assertEqual(cgst, Decimal("50.00"))
        self.assertEqual(sgst, Decimal("50.00"))
        self.assertEqual(cgst + sgst, Decimal("100.00"))

    def test_odd_paisa_split_sums_exactly(self):
        # 100.01 doesn't split evenly to the paisa -- CGST rounds
        # independently, SGST absorbs the leftover so the sum always
        # matches the original amount exactly, never off by a paisa.
        cgst, sgst = split_cgst_sgst(Decimal("100.01"))
        self.assertEqual(cgst + sgst, Decimal("100.01"))
        self.assertEqual(cgst, Decimal("50.01"))  # rounds half up
        self.assertEqual(sgst, Decimal("50.00"))

    def test_zero_amount(self):
        cgst, sgst = split_cgst_sgst(Decimal("0.00"))
        self.assertEqual(cgst, Decimal("0.00"))
        self.assertEqual(sgst, Decimal("0.00"))

    def test_result_is_quantized_to_two_decimal_places(self):
        cgst, sgst = split_cgst_sgst(Decimal("33.33"))
        self.assertEqual(cgst + sgst, Decimal("33.33"))
        self.assertEqual(cgst.as_tuple().exponent, -2)
        self.assertEqual(sgst.as_tuple().exponent, -2)
