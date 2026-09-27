"""
The money safety net: 5,000 fixed bills must total exactly the same as the
golden file says, to the paisa.

The golden file (orders/tests/golden/totals_v1.jsonl) was produced from the
code of 27 September 2026, before the liquor VAT work. One line per bill, so a
deliberate change shows up in `git diff` as exactly the bills it touched.

Regenerate only when a change to the maths is deliberate, and review the diff:
    GOLDEN_UPDATE=1 python manage.py test orders.tests.test_golden_totals

Run: python manage.py test orders.tests.test_golden_totals
"""
import json
import os
import pathlib

from django.test import TestCase

from orders.tests.legacy_totals import legacy_totals
from orders.tests.money_scenarios import (
    OUTLET_CONFIGS, build_world, create_orders, generate_specs, snapshot,
)

GOLDEN = pathlib.Path(__file__).parent / "golden" / "totals_v1.jsonl"
FIELDS = ("sub", "gst", "disc", "grand", "ro", "cgst", "sgst", "bd")


def _read_golden():
    with GOLDEN.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _differences(actual, expected, limit=15):
    lines = []
    for a, e in zip(actual, expected):
        bad = [f for f in FIELDS if a[f] != e[f]]
        if bad:
            lines.append(f"bill {a['i']} (mode {a['cfg']}, parcel {a['parcel']}): "
                         + ", ".join(f"{f} {e[f]} -> {a[f]}" for f in bad))
            if len(lines) >= limit:
                break
    return lines


class GoldenTotalsTest(TestCase):
    """recalculate_totals() on real rows must match the golden file."""

    def test_every_golden_bill_totals_the_same(self):
        specs = generate_specs()
        world = build_world()
        orders = create_orders(world, specs)
        actual = []
        for index, (order, spec) in enumerate(zip(orders, specs)):
            order.recalculate_totals()
            actual.append(snapshot(order, index, spec))

        if os.environ.get("GOLDEN_UPDATE") == "1":
            GOLDEN.parent.mkdir(exist_ok=True)
            with GOLDEN.open("w", encoding="utf-8", newline="\n") as fh:
                for row in actual:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")

        expected = _read_golden()
        self.assertEqual(len(actual), len(expected), "the golden file has a different number of bills")
        diffs = _differences(actual, expected)
        self.assertFalse(diffs, "bills no longer total the same:\n" + "\n".join(diffs))

    def test_recalculating_twice_changes_nothing(self):
        specs = generate_specs(count=400)
        world = build_world()
        orders = create_orders(world, specs)
        for index, (order, spec) in enumerate(zip(orders, specs)):
            order.recalculate_totals()
            first = snapshot(order, index, spec)
            order.recalculate_totals()
            self.assertEqual(snapshot(order, index, spec), first, f"bill {index} changed on a second pass")


class LegacyCopyMatchesGoldenTest(TestCase):
    """The frozen copy of the maths must agree with every golden bill, so the
    random comparisons in test_totals_properties.py are trustworthy."""

    def test_legacy_copy_matches_every_golden_bill(self):
        specs = generate_specs()
        expected = _read_golden()
        actual = []
        for index, spec in enumerate(specs):
            row = {"i": index, "cfg": spec["cfg"], "parcel": spec["parcel"]}
            row.update(legacy_totals(spec, OUTLET_CONFIGS))
            actual.append(row)
        diffs = _differences(actual, expected)
        self.assertFalse(diffs, "legacy_totals() disagrees with the golden file:\n" + "\n".join(diffs))
