"""
orders/services/bill_layout.py: the money lines every bill shows a guest.

The rule every bill must obey: read top to bottom, the rows add up to the
total, and the dish lines add up to the subtotal. The old printed and web
bills broke it two ways: a bill with prices including GST printed the
discount again after a subtotal that was already the value after discount
(927 + 108 - 115 is not 1035), and a free dish printed at full price.

Checked on random bills in every outlet mode and on pub bills with liquor,
through the real recalculate_totals(), plus the worked examples.

Run: python manage.py test orders.tests.test_bill_layout
"""
from decimal import Decimal as D

from hypothesis import given
from hypothesis.extra.django import TestCase as HypothesisTestCase

from orders.models import Order
from orders.services.bill_layout import (
    BILL_OF_SUPPLY, COMPOSITION_STATEMENT, PLAIN_BILL, TAX_INVOICE, bill_layout, rate_text,
)
from orders.tests.money_scenarios import (
    LIQUOR_CONFIGS, OUTLET_CONFIGS, build_liquor_world, build_world, create_orders, make_item, make_spec,
)
from orders.tests.test_totals_properties import REAL, bills, pub_bills
from tenants.models import Outlet


def _check_adds_up(test, order, layout):
    test.assertEqual(sum((row.amount for row in layout.rows), D("0")), order.grand_total)
    test.assertEqual(layout.total, order.grand_total)
    subtotal = next(row.amount for row in layout.rows if row.key == "subtotal")
    test.assertEqual(sum((line.amount for line in layout.lines), D("0")), subtotal)
    for line in layout.lines:
        if line.free:
            test.assertEqual(line.amount, D("0"))


class EveryBillAddsUpTest(HypothesisTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    @REAL
    @given(bills())
    def test_rows_add_up_to_the_total(self, spec):
        [order] = create_orders(self.world, [spec])
        order.recalculate_totals()
        layout = bill_layout(order)
        _check_adds_up(self, order, layout)

        inclusive, composition = OUTLET_CONFIGS[spec["cfg"]]
        added_gst = sum((row.amount for row in layout.rows if row.key in ("cgst", "sgst")), D("0"))
        if composition:
            self.assertEqual((layout.title, layout.statement), (BILL_OF_SUPPLY, COMPOSITION_STATEMENT))
            self.assertEqual((added_gst, layout.included), (D("0"), ()))
            self.assertFalse(layout.prices_include_gst)
        elif inclusive:
            # The tax inside the prices is shown, never added again.
            self.assertEqual(added_gst, D("0"))
            self.assertEqual(layout.included_total, order.gst_total)
        else:
            self.assertEqual(added_gst, order.gst_total)
            self.assertEqual(layout.included, ())
        if not composition:
            self.assertEqual(layout.title, TAX_INVOICE)

    @REAL
    @given(bills())
    def test_a_bill_from_before_the_tax_record_adds_up_too(self, spec):
        [order] = create_orders(self.world, [spec])
        order.recalculate_totals()
        Order.objects.filter(pk=order.pk).update(tax_summary=None)
        order.refresh_from_db()
        _check_adds_up(self, order, bill_layout(order))


class EveryPubBillAddsUpTest(HypothesisTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_liquor_world()

    @REAL
    @given(pub_bills())
    def test_rows_add_up_to_the_total(self, spec):
        [order] = create_orders(self.world, [spec])
        order.recalculate_totals()
        layout = bill_layout(order)
        _check_adds_up(self, order, layout)

        gst_inclusive, vat_inclusive = LIQUOR_CONFIGS[spec["cfg"]]
        added_vat = sum((row.amount for row in layout.rows if row.key == "vat"), D("0"))
        self.assertEqual(added_vat, D("0") if vat_inclusive else order.vat_total)
        included_vat = sum((tax.tax for tax in layout.included if tax.kind == "vat"), D("0"))
        self.assertEqual(included_vat, order.vat_total if vat_inclusive else D("0"))


class WorkedExamplesTest(HypothesisTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    def order(self, cfg, items, **spec):
        [order] = create_orders(self.world, [make_spec(cfg, items, **spec)])
        order.recalculate_totals()
        return order

    def test_prices_including_gst_with_10_percent_off(self):
        # The golden bill whose old printout read 927 + 108 - 115 = 1035.
        order = self.order(1, [
            make_item("240.00", qty=2, gst="5"), make_item("30.00", gst="0"),
            make_item("320.00", qty=2, gst="18"), make_item("25.00", comp=True),
        ], dtype="percentage", dval="10")
        layout = bill_layout(order)
        self.assertEqual([(row.label, row.amount) for row in layout.rows],
                         [("Subtotal", D("1150.00")), ("Discount", D("-115.00"))])
        self.assertEqual(layout.total, D("1035"))
        self.assertEqual(
            [(tax.label, tax.taxable, tax.cgst, tax.sgst) for tax in layout.included],
            [("GST 5%", D("411.43"), D("10.28"), D("10.29")),
             ("GST 18%", D("488.13"), D("43.94"), D("43.93"))],   # 576 less 87.87
        )
        self.assertTrue(layout.prices_include_gst)
        self.assertEqual(layout.included_total, D("108.44"))
        self.assertEqual([(line.amount, line.free) for line in layout.lines][-1], (D("0"), True))

    def test_prices_excluding_gst_show_cgst_and_sgst_by_rate(self):
        order = self.order(0, [make_item("100.00", gst="5"), make_item("200.00", gst="18")])
        layout = bill_layout(order)
        self.assertEqual([(row.label, row.amount) for row in layout.rows], [
            ("Subtotal", D("300.00")),
            ("CGST 2.5%", D("2.50")), ("SGST 2.5%", D("2.50")),
            ("CGST 9%", D("18.00")), ("SGST 9%", D("18.00")),
        ])
        self.assertEqual(layout.total, D("341"))

    def test_parcel_charge_and_round_off_are_rows(self):
        order = self.order(0, [make_item("99.99", gst="5")], parcel="20.00", parcel_rate="5")
        layout = bill_layout(order)
        labels = [row.label for row in layout.rows]
        self.assertEqual(labels, ["Subtotal", "CGST 2.5%", "SGST 2.5%", "Parcel charge", "Round off"])
        self.assertEqual(sum(row.amount for row in layout.rows), order.grand_total)

    def test_composition_bill_of_supply(self):
        order = self.order(2, [make_item("100.00", gst="5")])
        layout = bill_layout(order)
        self.assertEqual(layout.title, BILL_OF_SUPPLY)
        self.assertEqual(layout.statement, COMPOSITION_STATEMENT)
        self.assertEqual([row.label for row in layout.rows], ["Subtotal"])

    def test_an_outlet_without_a_gstin_issues_a_plain_bill(self):
        outlet = self.world["outlets"][0]
        Outlet.objects.filter(pk=outlet.pk).update(gst_no=None)
        outlet.refresh_from_db()
        [order] = create_orders(self.world, [make_spec(0, [make_item("100.00", gst="5")])])
        order.outlet = outlet
        order.recalculate_totals()
        layout = bill_layout(order)
        self.assertEqual((layout.title, layout.statement), (PLAIN_BILL, ""))
        self.assertEqual([row.label for row in layout.rows], ["Subtotal"])

    def test_rate_text(self):
        self.assertEqual([rate_text(D(r)) for r in ("2.50", "9.00", "0", "2.75", "20")],
                         ["2.5%", "9%", "0%", "2.75%", "20%"])


# ---------------------------------------------------------------------------
# The bills as a guest gets them add up: read back from the printout and the
# WhatsApp page, not from the layout. On the old renderers both failed.
# ---------------------------------------------------------------------------

import base64
import re

from django.core.signing import TimestampSigner
from django.test import TestCase
from django.urls import reverse

from orders.tests.test_golden_receipts import BILLS, MODES, build_outlets, make_order, render_escpos
from orders.views.public_views import PUBLIC_BILL_SALT
from printing.views import _build_receipt_b64


def _amount(text):
    """A printed amount as a Decimal ("Rs.1035.00", "-115.00", "FREE" -> 0), else None."""
    text = text.strip()
    for mark in ("Rs.", "&#8377;", "₹", "&minus;", "−", "+"):
        text = text.replace(mark, "-" if mark in ("&minus;", "−") else "")
    if text in ("FREE", "Free"):
        return D("0")
    return D(text) if re.fullmatch(r"-?\d+(\.\d+)?", text) else None


def _printed_sections(lines):
    """(dish amounts, money rows, total) read off a printed bill."""
    body = [re.match(r"[^|]*\|(.*)\|", line).group(1) for line in lines if "|" in line]
    total_at = next(i for i, text in enumerate(body) if text.startswith("TOTAL"))

    def block_above(i):
        rows = []
        while body[i].strip("-"):
            rows.insert(0, body[i])
            i -= 1
        return rows, i

    rows, top = block_above(total_at - 2)          # total_at - 1 is the line of dashes
    dishes, _ = block_above(top - 1)
    dish_amounts = [_amount(text.split()[-1]) for text in dishes if not text.startswith(" ")]
    return dish_amounts, [(text.rsplit(None, 1)[0], _amount(text.split()[-1])) for text in rows], \
        _amount(body[total_at].split()[-1])


class BillsAsPrintedAddUpTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant, cls.outlets = build_outlets()

    def each_bill(self):
        for mode_index, (outlet, dishes) in enumerate(self.outlets):
            for bill_index, bill in enumerate(BILLS):
                yield f"{MODES[mode_index][0]} | {bill['label']}", make_order(
                    self.tenant, outlet, dishes, mode_index, bill_index, bill)

    def test_the_printed_bill_adds_up(self):
        for about, order in self.each_bill():
            for width in (32, 48):
                with self.subTest(about, width=width):
                    raw = base64.b64decode(_build_receipt_b64(order, width, "full", "cp437"))
                    dishes, rows, total = _printed_sections(render_escpos(raw))
                    self.assertEqual(sum(amount for _, amount in rows), total)
                    self.assertEqual(sum(dishes), rows[0][1])        # the subtotal
                    self.assertEqual(total, order.grand_total)

    def test_the_whatsapp_bill_adds_up(self):
        for about, order in self.each_bill():
            with self.subTest(about):
                token = TimestampSigner(salt=PUBLIC_BILL_SALT).sign(str(order.id))
                html = self.client.get(reverse("public-bill", args=[token])).content.decode()
                money = html.split('class="grand-total"')[0]
                rows = [_amount(a) for a in re.findall(
                    r'<div class="total-row"><span>[^<]*</span><span>([^<]*)</span></div>', money)]
                dishes = [_amount(a) for a in re.findall(r'<div class="item-val">([^<]*)</div>', money)]
                total = _amount(re.search(
                    r'class="grand-total"><span>Total</span><span>([^<]*)</span>', html).group(1))
                self.assertEqual(sum(rows), total)
                self.assertEqual(sum(dishes), rows[0])
                self.assertEqual(total, order.grand_total)
