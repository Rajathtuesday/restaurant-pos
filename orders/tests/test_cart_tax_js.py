"""
The carts on the POS, the QSR counter and the QR menu show a total before the
order exists, worked out in the browser by static/js/cart_tax.js. This runs
that script in Node on random carts, in every outlet mode, with and without a
parcel charge, and checks it against what Order.recalculate_totals() puts on
the real bill: the GST, the parcel's GST, the round-off and the total must be
the same. Pub carts (liquor under the liquor_vat feature) are checked the
same way, VAT included.

Needs Node.js, which GitHub's runners have. Without it the test is skipped
locally; in CI (where CI=true) a missing node fails it instead.

Run: python manage.py test orders.tests.test_cart_tax_js
"""
import json
import os
import random
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from orders.tests.money_scenarios import (
    LIQUOR_CONFIGS, LIQUOR_RATES, OUTLET_CONFIGS, build_liquor_world, build_world, create_orders,
    liquor_item, make_item, make_spec,
)

HELPER = Path(settings.BASE_DIR) / "static" / "js" / "cart_tax.js"
NODE = shutil.which("node")

RUNNER = """
const { totals } = require(process.argv[1]);
const carts = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(carts.map(c => totals(c.lines, c.outlet))));
"""


def run_js(carts, runner=RUNNER):
    done = subprocess.run(
        [NODE, "-e", runner, str(HELPER)], input=json.dumps(carts),
        capture_output=True, text=True, timeout=120, check=True,
    )
    return json.loads(done.stdout)


LINE_RUNNER = """
const { line } = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map(c => line(c.amount, c.table, c.id, c.fallback))));
"""


def _needs_node(test):
    if not NODE:
        if os.environ.get("CI"):
            test.fail("node is missing on the CI runner; this test must not be skipped there")
        test.skipTest("node is not installed")


def random_carts(count, seed=20260927):
    """Carts as the screens build them: dishes at any rate, no discounts."""
    rng = random.Random(seed)
    specs = []
    for n in range(count):
        items = [
            make_item(
                rng.choice(["25", "90", "99.99", "140", "240", "333.33", "0.10", "1234.56", "0"]),
                qty=rng.randint(1, 7), gst=rng.choice(["0", "5", "5", "18", "12", "28"]),
            )
            for _ in range(rng.randint(1, 6))
        ]
        parcel = rng.choice(["0", "0", "5", "10", "20", "37.50", "120"])
        specs.append(make_spec(n % len(OUTLET_CONFIGS), items, parcel=parcel,
                               parcel_rate=rng.choice(["5", "5", "18"])))
    return specs


def cart_for(spec):
    inclusive, composition = OUTLET_CONFIGS[spec["cfg"]]
    return {
        "lines": [{"amount": it["total"], "gstRate": it["gst"]} for it in spec["items"]],
        "outlet": {"inclusive": inclusive, "composition": composition,
                   "parcel": spec["parcel"], "parcelGstRate": spec["parcel_rate"]},
    }


class CartTaxMatchesTheBillTest(TestCase):

    def setUp(self):
        _needs_node(self)

    def test_cart_totals_match_the_real_bill(self):
        specs = random_carts(400)
        orders = create_orders(build_world(), specs)
        shown = run_js([cart_for(spec) for spec in specs])
        for n, (order, spec, cart) in enumerate(zip(orders, specs, shown)):
            order.recalculate_totals()
            with self.subTest(cart=n, mode=spec["cfg"], parcel=spec["parcel"]):
                self.assertEqual(Decimal(str(cart["gst"])).quantize(Decimal("0.01")), order.gst_total)
                self.assertEqual(Decimal(str(cart["parcelGst"])).quantize(Decimal("0.01")), order.parcel_tax)
                self.assertEqual(Decimal(cart["roundedTotal"]), order.grand_total)
                self.assertEqual(Decimal(str(cart["roundOff"])).quantize(Decimal("0.01")), order.round_off)


class CartTaxRulesTest(SimpleTestCase):
    """The rules, one hand-worked cart each."""

    def setUp(self):
        _needs_node(self)

    def test_hand_worked_carts(self):
        cases = [
            # GST extra: 2 x 100 at 5% = 10; 50 at 0% stays 0
            ({"lines": [{"amount": 200, "gstRate": 5}, {"amount": 50, "gstRate": 0}], "outlet": {}},
             {"gst": 10, "total": 260, "roundedTotal": 260}),
            # GST included: 105 holds 5 of GST, the guest pays 105
            ({"lines": [{"amount": 105, "gstRate": 5}], "outlet": {"inclusive": True}},
             {"gst": 5, "total": 105, "roundedTotal": 105}),
            # composition: no GST at all, not even on the parcel
            ({"lines": [{"amount": 100, "gstRate": 5}], "outlet": {"composition": True, "parcel": 20, "parcelGstRate": 5}},
             {"gst": 0, "parcelGst": 0, "total": 120, "roundedTotal": 120}),
            # parcel on top: 20 at 5% adds 1
            ({"lines": [{"amount": 100, "gstRate": 5}], "outlet": {"parcel": 20, "parcelGstRate": 5}},
             {"gst": 6, "parcelGst": 1, "total": 126, "roundedTotal": 126}),
            # parcel inside: 21 holds 1 of GST, the guest pays 126
            ({"lines": [{"amount": 105, "gstRate": 5}], "outlet": {"inclusive": True, "parcel": 21, "parcelGstRate": 5}},
             {"gst": 6, "parcelGst": 1, "total": 126, "roundedTotal": 126}),
            # rounding: 99.99 x 3 at 18% = 53.99 GST, 353.96 rounds to 354
            ({"lines": [{"amount": 299.97, "gstRate": 18}], "outlet": {}},
             {"gst": 53.99, "total": 353.96, "roundedTotal": 354, "roundOff": 0.04}),
            # a missing rate is 0%, never a guessed 5%
            ({"lines": [{"amount": 100}], "outlet": {}},
             {"gst": 0, "total": 100}),
        ]
        results = run_js([cart for cart, _ in cases])
        for n, ((cart, expected), got) in enumerate(zip(cases, results)):
            for field, value in expected.items():
                with self.subTest(case=n, field=field):
                    self.assertAlmostEqual(got[field], value, places=9)


def random_pub_carts(count, seed=20260928):
    """Pub carts: food and liquor at any rate, in every liquor outlet mode."""
    rng = random.Random(seed)
    specs = []
    for n in range(count):
        items = []
        for _ in range(rng.randint(1, 6)):
            price = rng.choice(["25", "90", "99.99", "140", "250", "333.33", "0.10", "1234.56", "0"])
            qty = rng.randint(1, 7)
            if rng.random() < 0.5:
                items.append(liquor_item(price, qty=qty, vat=rng.choice(LIQUOR_RATES)))
            else:
                items.append(make_item(price, qty=qty, gst=rng.choice(["0", "5", "5", "18"])))
        parcel = rng.choice(["0", "0", "0", "10", "37.50"])
        specs.append(make_spec(n % len(LIQUOR_CONFIGS), items, parcel=parcel,
                               parcel_rate=rng.choice(["5", "18"])))
    return specs


def pub_cart_for(spec):
    gst_inclusive, vat_inclusive = LIQUOR_CONFIGS[spec["cfg"]]
    return {
        "lines": [
            {"amount": it["total"], "kind": "vat", "rate": it["vat"]} if it.get("kind") == "vat"
            else {"amount": it["total"], "kind": "gst", "rate": it["gst"]}
            for it in spec["items"]
        ],
        "outlet": {"inclusive": gst_inclusive, "vatInclusive": vat_inclusive,
                   "parcel": spec["parcel"], "parcelGstRate": spec["parcel_rate"]},
    }


class PubCartTaxMatchesTheBillTest(TestCase):

    def setUp(self):
        _needs_node(self)

    def test_pub_cart_totals_match_the_real_bill(self):
        specs = random_pub_carts(400)
        orders = create_orders(build_liquor_world(), specs)
        shown = run_js([pub_cart_for(spec) for spec in specs])
        for n, (order, spec, cart) in enumerate(zip(orders, specs, shown)):
            order.recalculate_totals()
            with self.subTest(cart=n, mode=spec["cfg"], parcel=spec["parcel"]):
                self.assertEqual(Decimal(str(cart["gst"])).quantize(Decimal("0.01")), order.gst_total)
                self.assertEqual(Decimal(str(cart["vat"])).quantize(Decimal("0.01")), order.vat_total)
                self.assertEqual(Decimal(str(cart["parcelGst"])).quantize(Decimal("0.01")), order.parcel_tax)
                self.assertEqual(Decimal(cart["roundedTotal"]), order.grand_total)
                self.assertEqual(Decimal(str(cart["roundOff"])).quantize(Decimal("0.01")), order.round_off)


class PubCartRulesTest(SimpleTestCase):
    """Pub carts, one hand-worked cart each (the plan's sample bill)."""

    def setUp(self):
        _needs_node(self)

    def test_hand_worked_pub_carts(self):
        food = {"amount": 660, "kind": "gst", "rate": 5}
        cases = [
            # Karnataka: no VAT on the drinks; 660 + 33 + 1,190
            ({"lines": [food, {"amount": 1190, "kind": "vat", "rate": 0}], "outlet": {}},
             {"gst": 33, "vat": 0, "total": 1883, "roundedTotal": 1883}),
            # a state at 5.5%: 1,190 x 5.5% = 65.45, 1,948.45 rounds to 1,948
            ({"lines": [food, {"amount": 1190, "kind": "vat", "rate": 5.5}], "outlet": {}},
             {"gst": 33, "vat": 65.45, "total": 1948.45, "roundedTotal": 1948, "roundOff": -0.45}),
            # drinks priced with VAT inside, food with GST on top
            ({"lines": [food, {"amount": 1190, "kind": "vat", "rate": 5.5}], "outlet": {"vatInclusive": True}},
             {"gst": 33, "vat": 62.04, "total": 1883, "roundedTotal": 1883}),
            # the composition scheme only switches off GST
            ({"lines": [{"amount": 100, "kind": "gst", "rate": 5}, {"amount": 200, "kind": "vat", "rate": 5.5}],
              "outlet": {"composition": True}},
             {"gst": 0, "vat": 11, "total": 311, "roundedTotal": 311}),
        ]
        results = run_js([cart for cart, _ in cases])
        for n, ((cart, expected), got) in enumerate(zip(cases, results)):
            for field, value in expected.items():
                with self.subTest(case=n, field=field):
                    self.assertAlmostEqual(got[field], value, places=9)

    def test_a_dish_takes_its_tax_from_the_pages_table(self):
        table = {"7": {"kind": "vat", "rate": "5.50"}, "8": {"kind": "gst", "rate": "18.00"}}
        cases = [
            {"amount": 440, "table": table, "id": 7, "fallback": 5},       # a drink: VAT, from the table
            {"amount": 100, "table": table, "id": "8", "fallback": 5},     # the table wins over the page's rate
            {"amount": 100, "table": table, "id": 9, "fallback": 5},       # not in the table: GST at the page's rate
            {"amount": 100, "table": None, "id": 7, "fallback": None},     # no table, no rate: 0%, never a guess
        ]
        self.assertEqual(run_js(cases, LINE_RUNNER), [
            {"amount": 440, "kind": "vat", "rate": 5.5},
            {"amount": 100, "kind": "gst", "rate": 18},
            {"amount": 100, "kind": "gst", "rate": 5},
            {"amount": 100, "kind": "gst", "rate": 0},
        ])
