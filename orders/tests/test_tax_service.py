# orders/tests/test_tax_service.py
"""
split_cgst_sgst was extracted from three independent, drifting copies of
the same CGST/SGST 50/50-split-and-round logic (orders/models.py's
cgst_total/sgst_total properties, orders/models.py's
_build_gst_breakdown_data, and reports/services/export_services.py's
GSTR-1 export). This is a pure-function test on the shared helper itself;
the three call sites' own existing tests cover that the refactor didn't
change their behavior.
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
