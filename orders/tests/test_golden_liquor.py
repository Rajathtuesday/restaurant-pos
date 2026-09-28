"""
The money safety net for bills with liquor: 1,000 fixed pub bills must total
exactly as the golden file says, to the paisa.

The golden file (orders/tests/golden/liquor_totals_v1.jsonl) was produced
when liquor arrived (Phase 1, 28 September 2026) and has been checked against
an independent copy of the rules (legacy_totals.liquor_totals). One line per
bill, so a deliberate change shows up in `git diff` as exactly the bills it
touched. The food-only bills keep their own golden file (test_golden_totals).

Regenerate only when a change to the maths is deliberate, and review the diff:
    GOLDEN_UPDATE=1 python manage.py test orders.tests.test_golden_liquor

Run: python manage.py test orders.tests.test_golden_liquor
"""
import json
import os
import pathlib

from django.test import SimpleTestCase, TestCase

from orders.tests.legacy_totals import liquor_totals
from orders.tests.money_scenarios import (
    LIQUOR_CONFIGS, build_liquor_world, create_orders, generate_liquor_specs, liquor_snapshot, row_head,
)
from orders.tests.test_golden_totals import _differences

GOLDEN = pathlib.Path(__file__).parent / "golden" / "liquor_totals_v1.jsonl"


def _read_golden():
    with GOLDEN.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class GoldenLiquorTotalsTest(TestCase):
    """recalculate_totals() on real rows with liquor lines must match the
    golden file."""

    def test_every_golden_pub_bill_totals_the_same(self):
        specs = generate_liquor_specs()
        orders = create_orders(build_liquor_world(), specs)
        actual = []
        for index, (order, spec) in enumerate(zip(orders, specs)):
            order.recalculate_totals()
            actual.append(liquor_snapshot(order, index, spec))

        if os.environ.get("GOLDEN_UPDATE") == "1":
            GOLDEN.parent.mkdir(exist_ok=True)
            with GOLDEN.open("w", encoding="utf-8", newline="\n") as fh:
                for row in actual:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")

        expected = _read_golden()
        self.assertEqual(len(actual), len(expected), "the golden file has a different number of bills")
        diffs = _differences(actual, expected)
        self.assertFalse(diffs, "pub bills no longer total the same:\n" + "\n".join(diffs))

    def test_the_golden_bills_are_the_pub_bills_we_mean(self):
        # the file must really hold liquor at each rate, in every mode
        rows = _read_golden()
        self.assertEqual(len(rows), 1000)
        vat_rates = {row[1] for bill in rows for row in bill["bd"] if row[0] == "vat"}
        self.assertEqual(vat_rates, {"0.00", "5.50", "10.00", "20.00"})
        self.assertEqual({bill["cfg"] for bill in rows}, set(range(len(LIQUOR_CONFIGS))))
        # the plan's two worked bills (edge specs 3 and 4 of mode 0)
        self.assertEqual((rows[2]["grand"], rows[2]["ro"], rows[2]["vat"]), ("1948.00", "-0.45", "65.45"))
        self.assertEqual((rows[3]["grand"], rows[3]["ro"], rows[3]["vat"]), ("1754.00", "0.39", "58.91"))
        # and today's Karnataka tab: no tax on the drinks
        self.assertEqual((rows[1]["grand"], rows[1]["vat"]), ("1883.00", "0.00"))


class IndependentCopyMatchesLiquorGoldenTest(SimpleTestCase):
    """The independent copy of the rules agrees with every golden pub bill,
    so the random comparisons in test_totals_properties.py can trust it."""

    def test_the_independent_copy_matches_every_golden_pub_bill(self):
        specs = generate_liquor_specs()
        actual = [{**row_head(index, spec), **liquor_totals(spec, LIQUOR_CONFIGS)}
                  for index, spec in enumerate(specs)]
        diffs = _differences(actual, _read_golden())
        self.assertFalse(diffs, "liquor_totals() disagrees with the golden file:\n" + "\n".join(diffs))
