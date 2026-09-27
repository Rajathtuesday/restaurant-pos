"""
The tax engine's rules (orders/services/tax_engine.py), one hand-worked bill
at a time. No database: the engine is pure.

The golden bills and the property tests cover thousands of bills; these pin
each rule by name, with the arithmetic written out, so a failure says which
rule broke.

Run: python manage.py test orders.tests.test_tax_engine
"""
from decimal import Decimal as D

from django.test import SimpleTestCase

from orders.services.tax_engine import (
    GST, Line, RateRow, allocate, compute, rows_from_summary, split_gst,
)


def dish(amount, rate="5", discount="0"):
    return Line(amount=D(amount), rate=D(rate), item_discount_pct=D(discount))


def parcel(amount, rate="5"):
    return Line(amount=D(amount), rate=D(rate), charge="parcel")


class TaxOnTopTest(SimpleTestCase):
    """Prices exclude GST: it is added on top."""

    def test_each_dish_at_its_own_rate(self):
        # 200 at 5% = 10.00; 50 at 0% = 0; 100 at 18% = 18.00
        bill = compute([dish("200"), dish("50", "0"), dish("100", "18")])
        self.assertEqual((bill.subtotal, bill.gst, bill.grand_total), (D("350.00"), D("28.00"), D("378")))
        self.assertEqual([(r.rate, r.taxable, r.tax) for r in bill.rows],
                         [(D("0.00"), D("50.00"), D("0.00")), (D("5.00"), D("200.00"), D("10.00")),
                          (D("18.00"), D("100.00"), D("18.00"))])

    def test_gst_is_rounded_once_and_the_rows_are_allocated_to_it(self):
        # exact: 42.50 x 5% = 2.125 and 30.03 x 18% = 5.4054, together 7.5304 -> 7.53
        # rows: 2.12 + 5.40 = 7.52 rounded down; the paisa owed goes to the
        # larger remainder (0.0054 beats 0.005): 5.41
        bill = compute([dish("42.50"), dish("30.03", "18")])
        self.assertEqual(bill.gst, D("7.53"))
        self.assertEqual([r.tax for r in bill.rows], [D("2.12"), D("5.41")])
        self.assertEqual(sum(r.tax for r in bill.rows), bill.gst)

    def test_cgst_is_half_rounded_up_and_the_rows_add_up_to_it(self):
        # gst 7.53: CGST 3.77, SGST 3.76. Row halves 1.06 and 2.705: the odd
        # paisa goes to the 18% row, so the rows' CGST also makes 3.77.
        bill = compute([dish("42.50"), dish("30.03", "18")])
        self.assertEqual((bill.cgst, bill.sgst), (D("3.77"), D("3.76")))
        self.assertEqual([(r.cgst, r.sgst) for r in bill.rows], [(D("1.06"), D("1.06")), (D("2.71"), D("2.70"))])

    def test_grand_total_rounds_to_the_rupee(self):
        # 72.53 + 7.53 = 80.06 -> 80, round-off -0.06
        bill = compute([dish("42.50"), dish("30.03", "18")])
        self.assertEqual((bill.grand_total, bill.round_off), (D("80"), D("-0.06")))


class TaxInsideTest(SimpleTestCase):
    """Prices include GST: it is worked out from inside the price."""

    def test_the_guest_pays_the_menu_price(self):
        # 105 at 5% holds 5.00 of GST; the subtotal is the value before GST
        bill = compute([dish("105")], prices_include_tax=True)
        self.assertEqual((bill.subtotal, bill.gst, bill.grand_total), (D("100.00"), D("5.00"), D("105")))
        self.assertEqual(bill.rows[0].taxable, D("100.00"))

    def test_taxable_value_is_what_is_left_after_the_gst(self):
        # 99.99 x 3 = 299.97 at 18%: GST inside = 299.97 x 18 / 118 = 45.758... -> 45.76
        bill = compute([dish("299.97", "18")], prices_include_tax=True)
        self.assertEqual((bill.gst, bill.rows[0].taxable), (D("45.76"), D("254.21")))
        self.assertEqual(bill.rows[0].taxable + bill.rows[0].tax, D("299.97"))


class CompositionTest(SimpleTestCase):

    def test_no_gst_and_no_rows_on_a_bill_of_supply(self):
        bill = compute([dish("100"), parcel("20")], composition=True)
        self.assertEqual((bill.gst, bill.cgst, bill.sgst, bill.charge_tax), (0, 0, 0, 0))
        self.assertEqual(bill.rows, ())
        self.assertEqual(bill.grand_total, D("120"))


class DiscountTest(SimpleTestCase):

    def test_order_discount_is_spread_over_the_dishes_by_value(self):
        # 480 at 5%, 30 at 0%, 640 at 18%, 10% off: taxable 432 / 27 / 576
        bill = compute([dish("480"), dish("30", "0"), dish("640", "18")],
                       discount_type="percentage", discount_value=D("10"))
        self.assertEqual(bill.discount, D("115.00"))
        self.assertEqual([r.taxable for r in bill.rows], [D("27.00"), D("432.00"), D("576.00")])
        self.assertEqual(bill.gst, D("125.28"))                    # 21.60 + 103.68
        self.assertEqual(bill.grand_total, D("1160"))             # 1150 - 115 + 125.28 = 1160.28

    def test_a_dish_discount_comes_off_before_the_order_discount(self):
        # 200 with 25% off = 150; then 10% off the order = 135; GST 5% = 6.75
        bill = compute([dish("200", discount="25")], discount_type="percentage", discount_value=D("10"))
        self.assertEqual((bill.discount, bill.rows[0].taxable, bill.gst), (D("65.00"), D("135.00"), D("6.75")))

    def test_a_discount_never_goes_past_the_dishes(self):
        bill = compute([dish("100")], discount_type="amount", discount_value=D("100000"))
        self.assertEqual((bill.discount, bill.gst, bill.grand_total), (D("100.00"), D("0.00"), D("0")))

    def test_charges_are_never_discounted(self):
        # 50% off takes the food from 100 to 50 (GST 2.50); the parcel keeps
        # its full 20 and its GST 1.00: 50 + 2.50 + 20 + 1.00 = 73.50 -> 74
        bill = compute([dish("100"), parcel("20")], discount_type="percentage", discount_value=D("50"))
        self.assertEqual((bill.discount, bill.charge_tax, bill.gst), (D("50.00"), D("1.00"), D("3.50")))
        self.assertEqual((bill.grand_total, bill.round_off), (D("74"), D("0.50")))


class ChargeTest(SimpleTestCase):
    """The parcel charge: taxed like the food, rounded on its own."""

    def test_parcel_gst_on_top(self):
        # 120 food + 6.00 GST; parcel 15 + 0.75 GST = 141.75 -> 142
        bill = compute([dish("120"), parcel("15")])
        self.assertEqual((bill.gst, bill.charge_tax, bill.grand_total), (D("6.75"), D("0.75"), D("142")))
        self.assertEqual(bill.rows[0].taxable, D("135.00"))      # food 120 + parcel 15, both at 5%

    def test_parcel_gst_inside(self):
        # parcel 21 holds 1.00 of GST; the guest pays 105 + 21
        bill = compute([dish("105"), parcel("21")], prices_include_tax=True)
        self.assertEqual((bill.gst, bill.charge_tax, bill.grand_total), (D("6.00"), D("1.00"), D("126")))
        self.assertEqual(bill.subtotal, D("100.00"))               # the food's value; the parcel is not in it

    def test_a_charge_never_moves_the_food_figures(self):
        food = [dish("42.50"), dish("30.03", "18")]
        without = compute(food)
        with_parcel = compute(food + [parcel("37.50", "18")])
        self.assertEqual(with_parcel.gst - with_parcel.charge_tax, without.gst)
        self.assertEqual(with_parcel.charge_tax, D("6.75"))           # 37.50 x 18% exactly

    def test_an_untaxed_charge_carries_no_tax_and_no_row(self):
        # a parcel charge from before parcel GST: kind None
        bill = compute([dish("100"), Line(amount=D("15"), kind=None, charge="parcel")])
        self.assertEqual((bill.gst, bill.charge_tax, bill.grand_total), (D("5.00"), D("0.00"), D("120")))
        self.assertEqual([r.taxable for r in bill.rows], [D("100.00")])


class OtherTaxKindsTest(SimpleTestCase):
    """Liquor VAT will be a second kind of tax. The engine keeps kinds apart
    already, so adding it is data, not a rewrite."""

    def test_a_second_kind_is_totalled_on_its_own_and_never_mixed_into_gst(self):
        beer = Line(amount=D("200"), rate=D("5.5"), kind="vat")
        bill = compute([dish("100"), beer])
        self.assertEqual(bill.gst, D("5.00"))
        vat_row = [r for r in bill.rows if r.kind == "vat"][0]
        self.assertEqual((vat_row.rate, vat_row.taxable, vat_row.tax), (D("5.50"), D("200.00"), D("11.00")))
        self.assertEqual((vat_row.cgst, vat_row.sgst), (0, 0))
        self.assertEqual(bill.grand_total, D("316"))      # 300 + 5 GST + 11 VAT

    def test_the_composition_scheme_only_switches_off_gst(self):
        beer = Line(amount=D("200"), rate=D("5.5"), kind="vat")
        bill = compute([dish("100"), beer], composition=True)
        self.assertEqual(bill.gst, 0)
        self.assertEqual([(r.kind, r.tax) for r in bill.rows], [("vat", D("11.00"))])


class RecordTest(SimpleTestCase):

    def test_the_stored_record_reads_back_as_the_same_rows(self):
        bill = compute([dish("42.50"), dish("30.03", "18"), parcel("20")],
                       discount_type="amount", discount_value=D("10"))
        self.assertEqual(rows_from_summary(bill.summary()), list(bill.rows))
        self.assertEqual(bill.summary()["charges"][0]["name"], "parcel")

    def test_every_row_adds_its_cgst_and_sgst_to_its_tax(self):
        bill = compute([dish("0.10"), dish("99.99", "18"), dish("333.33", "12")])
        for row in bill.rows:
            self.assertEqual(row.cgst + row.sgst, row.tax)


class AllocateTest(SimpleTestCase):

    def test_figures_add_up_to_the_total(self):
        self.assertEqual(allocate([D("2.125"), D("5.4054")], D("7.53")), [D("2.12"), D("5.41")])

    def test_ties_go_to_the_larger_figure_then_the_earlier_one(self):
        # equal remainders (0.005): the larger figure gets the paisa first
        self.assertEqual(allocate([D("1.005"), D("2.005")], D("3.01")), [D("1.00"), D("2.01")])
        # equal figures: the earlier one
        self.assertEqual(allocate([D("1.005"), D("1.005")], D("2.01")), [D("1.01"), D("1.00")])
        # enough paise for both
        self.assertEqual(allocate([D("1.005"), D("1.005")], D("2.02")), [D("1.01"), D("1.01")])

    def test_an_impossible_total_is_refused(self):
        with self.assertRaises(ValueError):
            allocate([D("1.00")], D("1.02"))

    def test_split_gst(self):
        self.assertEqual(split_gst(D("0.05")), (D("0.03"), D("0.02")))
        self.assertEqual(split_gst(D("10.00")), (D("5.00"), D("5.00")))
