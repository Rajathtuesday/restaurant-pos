"""
Property tests for the bill maths: rules every bill must obey, checked on
thousands of random bills instead of a few hand-picked ones.

Two kinds of test live here:

  * Rules on the frozen copy of today's maths (legacy_totals.py): the bill
    adds up, round-off stays within 50 paise, CGST and SGST split the GST,
    composition outlets charge no GST, voided and free dishes change nothing.
    No database, so they run fast on many bills.

  * A differential test: random bills go through the real
    Order.recalculate_totals() on real rows and must match the frozen copy to
    the paisa. When the liquor VAT work rewrites the maths, this is what
    catches a food-only bill that no longer totals the same, and Hypothesis
    shrinks the failure down to the smallest bill that shows it.

In CI the random bills are the same on every run (derandomize), so a red
build always means the code changed, never bad luck. For a deeper search:
    MONEY_THOROUGH=1 python manage.py test orders.tests.test_totals_properties
"""
import os
from decimal import ROUND_HALF_UP, Decimal as D

from django.test import SimpleTestCase
from hypothesis import HealthCheck, given, settings, strategies as st
from hypothesis.extra.django import TestCase as HypothesisTestCase

from orders.tests.legacy_totals import legacy_totals
from orders.tests.money_scenarios import (
    GST_RATES, OUTLET_CONFIGS, build_world, create_orders, make_item, make_spec, snapshot,
)

THOROUGH = os.environ.get("MONEY_THOROUGH") == "1"
_COMMON = dict(deadline=None, database=None, derandomize=not THOROUGH)
PURE = settings(max_examples=20_000 if THOROUGH else 500, **_COMMON)
REAL = settings(max_examples=3_000 if THOROUGH else 150,
                suppress_health_check=[HealthCheck.too_slow], **_COMMON)

INCLUSIVE_CFGS = [i for i, (inclusive, _) in enumerate(OUTLET_CONFIGS) if inclusive]
COMPOSITION_CFGS = [i for i, (_, composition) in enumerate(OUTLET_CONFIGS) if composition]
GST_CFGS = [i for i, (_, composition) in enumerate(OUTLET_CONFIGS) if not composition]


# ---------------------------------------------------------------------------
# Random bills, in the same shape as money_scenarios specs
# ---------------------------------------------------------------------------

def _paise(low, high):
    """A money string with exactly two decimals, between low and high rupees."""
    return st.integers(int(low * 100), int(high * 100)).map(lambda n: f"{D(n) / 100:.2f}")


prices = st.one_of(
    st.just("0.00"),
    st.integers(1, 60).map(lambda n: f"{n * 5}.00"),   # round menu prices
    _paise(0.01, 2500),                                # anything with paise
)
dishes = st.builds(
    make_item,
    price=prices,
    qty=st.integers(1, 25),
    gst=st.sampled_from(GST_RATES),
    disc=st.one_of(st.just("0"), _paise(0.01, 100)),
    status=st.sampled_from(["pending", "sent", "served", "served", "voided"]),
    comp=st.sampled_from([False, False, False, True]),
    modifier=st.sampled_from(["0", "0", "0", "10", "25", "49.50"]),
)
order_discounts = st.one_of(
    st.just((None, "0")),
    st.tuples(st.just("percentage"), _paise(0.01, 100)),
    st.tuples(st.just("amount"), _paise(0.01, 100000)),
)
parcels = st.one_of(st.just("0"), _paise(0.01, 500))


@st.composite
def bills(draw, cfgs=tuple(range(len(OUTLET_CONFIGS)))):
    cfg = draw(st.sampled_from(list(cfgs)))
    dtype, dval = draw(order_discounts)
    return make_spec(cfg, draw(st.lists(dishes, max_size=10)), dtype, dval, draw(parcels))


def live_items(spec):
    return [it for it in spec["items"] if it["status"] != "voided" and not it["comp"]]


def totals(spec):
    """legacy_totals() with the money fields as Decimals."""
    out = legacy_totals(spec, OUTLET_CONFIGS)
    return {key: (value if key == "bd" else D(value)) for key, value in out.items()}


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class BillRulesTest(SimpleTestCase):
    """Rules today's maths obeys on any bill. They must still hold after the
    liquor VAT work, for food bills and mixed bills alike."""

    @PURE
    @given(bills())
    def test_the_bill_adds_up(self, spec):
        t = totals(spec)
        inclusive, _ = OUTLET_CONFIGS[spec["cfg"]]
        parcel = D(spec["parcel"])
        if inclusive:
            # menu prices already hold the GST, and the discount came off them
            expected = t["sub"] + t["gst"] + parcel + t["ro"]
        else:
            expected = t["sub"] - t["disc"] + t["gst"] + parcel + t["ro"]
        self.assertEqual(t["grand"], expected)

    @PURE
    @given(bills())
    def test_grand_total_is_whole_rupees_and_round_off_is_at_most_fifty_paise(self, spec):
        t = totals(spec)
        self.assertEqual(t["grand"], t["grand"].to_integral_value())
        self.assertLessEqual(abs(t["ro"]), D("0.50"))

    @PURE
    @given(bills())
    def test_cgst_and_sgst_split_the_gst_evenly(self, spec):
        t = totals(spec)
        self.assertEqual(t["cgst"] + t["sgst"], t["gst"])
        self.assertIn(t["cgst"] - t["sgst"], (D("0.00"), D("0.01")))

    @PURE
    @given(bills(cfgs=COMPOSITION_CFGS))
    def test_composition_outlets_charge_no_gst(self, spec):
        t = totals(spec)
        self.assertEqual((t["gst"], t["cgst"], t["sgst"]), (0, 0, 0))

    @PURE
    @given(bills(), st.lists(dishes, min_size=1, max_size=4))
    def test_voided_and_free_dishes_change_nothing(self, spec, extra):
        before = legacy_totals(spec, OUTLET_CONFIGS)
        dead = [dict(it, status="voided") if n % 2 == 0 else dict(it, comp=True)
                for n, it in enumerate(extra)]
        after = legacy_totals(dict(spec, items=dead[:1] + spec["items"] + dead[1:]), OUTLET_CONFIGS)
        self.assertEqual(after, before)

    @PURE
    @given(bills(cfgs=INCLUSIVE_CFGS))
    def test_inclusive_menu_price_is_what_the_guest_pays(self, spec):
        items = [dict(it, disc="0") for it in spec["items"]]
        spec = dict(spec, items=items, dtype=None, dval="0", parcel="0")
        menu_total = sum((D(it["total"]) for it in live_items(spec)), D("0"))
        self.assertEqual(totals(spec)["grand"], menu_total.quantize(D("1"), rounding=ROUND_HALF_UP))

    @PURE
    @given(bills())
    def test_discount_never_goes_past_the_bill(self, spec):
        t = totals(spec)
        menu_total = sum((D(it["total"]) for it in live_items(spec)), D("0"))
        self.assertLessEqual(t["disc"], menu_total)
        for field in ("sub", "gst", "grand"):
            self.assertGreaterEqual(t[field], 0, field)

    @PURE
    @given(bills())
    def test_breakdown_rows_are_well_formed(self, spec):
        rows = totals(spec)["bd"]
        rates = [D(row[0]) for row in rows]
        self.assertEqual(rates, sorted(set(rates)))
        self.assertLessEqual(set(rates), {D(it["gst"]) for it in live_items(spec)})
        for rate, cgst_rate, sgst_rate, cgst, sgst in rows:
            self.assertEqual(D(cgst_rate), D(rate) / 2)
            self.assertEqual(D(sgst_rate), D(cgst_rate))
            self.assertGreater(D(cgst) + D(sgst), 0)
            self.assertIn(D(cgst) - D(sgst), (0, D("0.01")))

    @PURE
    @given(bills(cfgs=GST_CFGS))
    def test_breakdown_is_within_half_a_paisa_per_dish_of_the_gst_total(self, spec):
        """A known gap, recorded in the liquor VAT plan: the breakdown rounds
        each dish's tax on its own while the GST total is rounded once, so the
        two can differ by a few paise. Phase 1 closes it to zero; until then
        it must stay this small."""
        t = totals(spec)
        shown = sum((D(row[3]) + D(row[4]) for row in t["bd"]), D("0"))
        allowed = D("0.005") * (len(live_items(spec)) + 1)
        self.assertLessEqual(abs(shown - t["gst"]), allowed)


class RealMathsMatchesFrozenCopyTest(HypothesisTestCase):
    """Random bills through the real recalculate_totals() on real rows."""

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    @REAL
    @given(bills())
    def test_real_bill_matches_the_frozen_copy(self, spec):
        [order] = create_orders(self.world, [spec])
        order.recalculate_totals()
        expected = {"i": 0, "cfg": spec["cfg"], "parcel": spec["parcel"]}
        expected.update(legacy_totals(spec, OUTLET_CONFIGS))
        self.assertEqual(snapshot(order, 0, spec), expected)
