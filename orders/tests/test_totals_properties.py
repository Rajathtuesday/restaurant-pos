"""
Property tests for the bill maths: rules every bill must obey, checked on
thousands of random bills instead of a few hand-picked ones.

Two kinds of test live here:

  * Rules on the frozen copy of today's maths (legacy_totals.py): the bill
    adds up, round-off stays within 50 paise, CGST and SGST split the GST,
    composition outlets charge no GST, voided and free dishes change nothing,
    the parcel charge carries its own rate and never changes the food's tax.
    No database, so they run fast on many bills.

  * A differential test: random bills go through the real
    Order.recalculate_totals() on real rows and must match the frozen copy to
    the paisa. When the liquor VAT work rewrites the maths, this is what
    catches a food-only bill that no longer totals the same, and Hypothesis
    shrinks the failure down to the smallest bill that shows it.

Bills with liquor (Phase 1) get the same two kinds of test, against their
own independent copy of the rules (legacy_totals.liquor_totals).

In CI the random bills are the same on every run (derandomize), so a red
build always means the code changed, never bad luck. For a deeper search:
    MONEY_THOROUGH=1 python manage.py test orders.tests.test_totals_properties
"""
import os
from decimal import ROUND_HALF_UP, Decimal as D

from django.test import SimpleTestCase
from hypothesis import HealthCheck, given, settings, strategies as st
from hypothesis.extra.django import TestCase as HypothesisTestCase

from orders.tests.legacy_totals import legacy_totals, liquor_totals
from tenants.models import Outlet
from orders.tests.money_scenarios import (
    CURRENT_GST_RATES, GST_RATES, LIQUOR_CONFIGS, LIQUOR_RATES, OUTLET_CONFIGS, build_liquor_world,
    build_world, create_orders, liquor_item, liquor_snapshot, make_item, make_spec, row_head, snapshot,
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
# None: a bill from before parcel GST, whose parcel charge carries none
parcel_rates = st.sampled_from([None, "5", "5", "18"])


@st.composite
def bills(draw, cfgs=tuple(range(len(OUTLET_CONFIGS)))):
    cfg = draw(st.sampled_from(list(cfgs)))
    dtype, dval = draw(order_discounts)
    return make_spec(cfg, draw(st.lists(dishes, max_size=10)), dtype, dval,
                     draw(parcels), draw(parcel_rates))


def live_items(spec):
    return [it for it in spec["items"] if it["status"] != "voided" and not it["comp"]]


def totals(spec):
    """legacy_totals() with the money fields as Decimals (pgst is 0 without a parcel)."""
    out = legacy_totals(spec, OUTLET_CONFIGS)
    return {"pgst": D("0"), **{key: (value if key == "bd" else D(value)) for key, value in out.items()}}


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
            # menu prices and the parcel charge already hold their GST, and
            # the discount came off the prices
            expected = t["sub"] + t["gst"] - t["pgst"] + parcel + t["ro"]
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
        self.assertEqual((t["gst"], t["cgst"], t["sgst"], t["pgst"]), (0, 0, 0, 0))
        self.assertEqual(t["bd"], [], "a bill of supply has no GST breakdown")

    @PURE
    @given(bills(cfgs=GST_CFGS))
    def test_parcel_gst_is_the_parcel_rate_on_the_parcel(self, spec):
        t = totals(spec)
        inclusive, _ = OUTLET_CONFIGS[spec["cfg"]]
        parcel = D(spec["parcel"])
        rate = D(spec["parcel_rate"]) if spec["parcel_rate"] else D("0")
        if parcel <= 0 or rate == 0:
            expected = D("0")
        elif inclusive:
            expected = parcel * rate / (100 + rate)      # inside the charge
        else:
            expected = parcel * rate / 100               # on top of the charge
        self.assertEqual(t["pgst"], expected.quantize(D("0.01"), rounding=ROUND_HALF_UP))

    @PURE
    @given(bills())
    def test_the_parcel_never_changes_the_food_tax(self, spec):
        with_parcel = totals(spec)
        without = totals(dict(spec, parcel="0"))
        self.assertEqual(with_parcel["gst"] - with_parcel["pgst"], without["gst"])
        self.assertEqual((with_parcel["sub"], with_parcel["disc"]), (without["sub"], without["disc"]))

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
        """Menu prices and the parcel charge both already include their GST."""
        items = [dict(it, disc="0") for it in spec["items"]]
        spec = dict(spec, items=items, dtype=None, dval="0")
        menu_total = sum((D(it["total"]) for it in live_items(spec)), D("0")) + D(spec["parcel"])
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
    def test_tax_rows_are_well_formed(self, spec):
        """One row per rate in use, in rate order, each with CGST and SGST
        splitting its tax to within a paisa."""
        t = totals(spec)
        rates = [D(row[1]) for row in t["bd"]]
        self.assertEqual(rates, sorted(set(rates)))
        allowed = {D(it["gst"]) for it in live_items(spec)}
        if D(spec["parcel"]) > 0 and spec["parcel_rate"] is not None:
            allowed.add(D(spec["parcel_rate"]))
        self.assertLessEqual(set(rates), allowed)
        for kind, rate, taxable, tax, cgst, sgst in t["bd"]:
            self.assertEqual(kind, "gst")
            self.assertTrue(D(taxable) != 0 or D(tax) != 0, "an empty row")
            self.assertEqual(D(cgst) + D(sgst), D(tax))
            self.assertIn(D(cgst) - D(sgst), (D("-0.01"), 0, D("0.01")))

    @PURE
    @given(bills(cfgs=GST_CFGS))
    def test_tax_rows_add_up_to_the_bill(self, spec):
        """The rows add up to exactly the bill's GST, CGST, SGST and taxable
        value (P11 closed: before the engine they could miss by a few paise)."""
        t = totals(spec)
        inclusive, _ = OUTLET_CONFIGS[spec["cfg"]]
        column = lambda n: sum((D(row[n]) for row in t["bd"]), D("0"))
        self.assertEqual(column(3), t["gst"])
        self.assertEqual(column(4), t["cgst"])
        self.assertEqual(column(5), t["sgst"])
        parcel = D(spec["parcel"]) if spec["parcel_rate"] is not None else D("0")
        if inclusive:
            expected_taxable = t["sub"] + parcel - t["pgst"]
        else:
            expected_taxable = t["sub"] - t["disc"] + parcel
        self.assertEqual(column(2), expected_taxable)


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
        expected = {**row_head(0, spec), **legacy_totals(spec, OUTLET_CONFIGS)}
        self.assertEqual(snapshot(order, 0, spec), expected)


class UnregisteredOutletTest(HypothesisTestCase):
    """An outlet with no GSTIN may not collect GST (CGST Act, section 32). Its
    bill costs exactly what the same bill costs on the composition scheme,
    where GST is off too, and its tax record says it was unregistered."""

    MONEY = ("sub", "gst", "disc", "grand", "ro", "cgst", "sgst", "pgst", "bd")

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    @REAL
    @given(bills(cfgs=GST_CFGS))
    def test_no_gstin_bills_like_the_composition_scheme(self, spec):
        inclusive, _ = OUTLET_CONFIGS[spec["cfg"]]
        same_bill_on_composition = {**spec, "cfg": OUTLET_CONFIGS.index((inclusive, True))}
        unregistered, composition = create_orders(self.world, [spec, same_bill_on_composition])
        Outlet.objects.filter(pk=unregistered.outlet_id).update(gst_no=None)
        unregistered.outlet = Outlet.objects.get(pk=unregistered.outlet_id)

        unregistered.recalculate_totals()
        composition.recalculate_totals()

        mine, theirs = snapshot(unregistered, 0, spec), snapshot(composition, 0, same_bill_on_composition)
        self.assertEqual({k: v for k, v in mine.items() if k in self.MONEY},
                         {k: v for k, v in theirs.items() if k in self.MONEY})
        self.assertEqual((unregistered.gst_scheme, composition.gst_scheme), ("unregistered", "composition"))


# ---------------------------------------------------------------------------
# Bills with liquor
# ---------------------------------------------------------------------------

liquor_lines = st.builds(
    liquor_item,
    price=prices,
    qty=st.integers(1, 25),
    vat=st.sampled_from(LIQUOR_RATES),
    disc=st.one_of(st.just("0"), _paise(0.01, 100)),
    status=st.sampled_from(["pending", "sent", "served", "served", "voided"]),
    comp=st.sampled_from([False, False, False, True]),
    modifier=st.sampled_from(["0", "0", "0", "10", "25"]),
)
food_lines = st.builds(
    make_item,
    price=prices,
    qty=st.integers(1, 25),
    gst=st.sampled_from(CURRENT_GST_RATES),
    disc=st.one_of(st.just("0"), _paise(0.01, 100)),
    status=st.sampled_from(["pending", "sent", "served", "served", "voided"]),
    comp=st.sampled_from([False, False, False, True]),
    modifier=st.sampled_from(["0", "0", "0", "10", "25"]),
)


@st.composite
def pub_bills(draw, cfgs=tuple(range(len(LIQUOR_CONFIGS))), discounts=order_discounts):
    cfg = draw(st.sampled_from(list(cfgs)))
    dtype, dval = draw(discounts)
    items = draw(st.lists(st.one_of(food_lines, liquor_lines), max_size=10))
    return make_spec(cfg, items, dtype, dval, draw(parcels), draw(st.sampled_from([None, "5", "5", "18"])))


def pub_totals(spec):
    out = liquor_totals(spec, LIQUOR_CONFIGS)
    return {"pgst": D("0"), **{key: (value if key in ("bd", "sec") else D(value)) for key, value in out.items()}}


def is_liquor(item):
    return item.get("kind") == "vat"


class PubBillRulesTest(SimpleTestCase):
    """Rules every bill with liquor obeys, on the independent copy."""

    @PURE
    @given(pub_bills())
    def test_the_bill_adds_up(self, spec):
        """Taxable value plus tax over both sections, the parcel charge (and
        its GST when added on top) and the round-off make the grand total."""
        t = pub_totals(spec)
        gst_inclusive, _ = LIQUOR_CONFIGS[spec["cfg"]]
        sections = sum((D(sec[3]) + D(sec[4]) for sec in t["sec"]), D("0"))
        parcel = D(spec["parcel"]) + (D("0") if gst_inclusive else t["pgst"])
        self.assertEqual(sections + parcel + t["ro"], t["grand"])

    @PURE
    @given(pub_bills())
    def test_grand_total_is_whole_rupees_and_round_off_is_at_most_fifty_paise(self, spec):
        t = pub_totals(spec)
        self.assertEqual(t["grand"], t["grand"].to_integral_value())
        self.assertLessEqual(abs(t["ro"]), D("0.50"))

    @PURE
    @given(pub_bills())
    def test_vat_never_enters_cgst_and_sgst(self, spec):
        t = pub_totals(spec)
        self.assertEqual(t["cgst"] + t["sgst"], t["gst"])
        for kind, rate, taxable, tax, cgst, sgst in t["bd"]:
            if kind == "vat":
                self.assertEqual((D(cgst), D(sgst)), (0, 0))

    @PURE
    @given(pub_bills())
    def test_rows_and_sections_add_up_to_the_bill(self, spec):
        t = pub_totals(spec)
        for kind in ("gst", "vat"):
            rows = sum((D(row[3]) for row in t["bd"] if row[0] == kind), D("0"))
            self.assertEqual(rows, t[kind], kind)
        self.assertEqual(sum((D(sec[4]) for sec in t["sec"]), D("0")), t["gst"] - t["pgst"] + t["vat"])
        self.assertEqual(sum((D(sec[2]) for sec in t["sec"]), D("0")), t["disc"])

    @PURE
    @given(pub_bills(discounts=st.just((None, "0"))))
    def test_liquor_never_changes_the_foods_tax(self, spec):
        """Without an order discount to share, the food's tax is what it
        would be with no drinks on the bill at all, row by row. (Taxable
        values can move a paisa between food and drinks: the bill's discount
        is rounded once, over both.)"""
        with_drinks = pub_totals(spec)
        food_only = pub_totals(dict(spec, items=[it for it in spec["items"] if not is_liquor(it)]))
        self.assertEqual((with_drinks["gst"], with_drinks["cgst"], with_drinks["sgst"]),
                         (food_only["gst"], food_only["cgst"], food_only["sgst"]))
        tax_only = lambda rows: [(r[1], r[3], r[4], r[5]) for r in rows if r[0] == "gst" and D(r[3])]
        self.assertEqual(tax_only(with_drinks["bd"]), tax_only(food_only["bd"]))

    @PURE
    @given(pub_bills(cfgs=(1, 3), discounts=st.just((None, "0"))))
    def test_drinks_priced_with_vat_cost_their_menu_price(self, spec):
        items = [dict(it, disc="0") for it in spec["items"] if is_liquor(it)]
        spec = dict(spec, items=items, parcel="0")
        menu_total = sum((D(it["total"]) for it in live_items(spec)), D("0"))
        self.assertEqual(pub_totals(spec)["grand"], menu_total.quantize(D("1"), rounding=ROUND_HALF_UP))

    @PURE
    @given(pub_bills(discounts=st.just((None, "0"))))
    def test_zero_percent_liquor_is_still_reported_as_liquor(self, spec):
        """A 0% class makes no VAT, but its drinks keep a row of their own at
        their full value: on the return they are a non-GST supply, never
        nil-rated food (which has its own GST 0% row)."""
        spec = dict(spec, items=[dict(it, disc="0") for it in spec["items"]])
        t = pub_totals(spec)
        value = sum((D(it["total"]) for it in live_items(spec) if is_liquor(it) and D(it["vat"]) == 0), D("0"))
        rows = [r for r in t["bd"] if r[:2] == ["vat", "0.00"]]
        expected = [["vat", "0.00", f"{value:.2f}", "0.00", "0.00", "0.00"]] if value else []
        self.assertEqual(rows, expected)


class RealPubMathsMatchesIndependentCopyTest(HypothesisTestCase):
    """Random bills with liquor through the real recalculate_totals()."""

    @classmethod
    def setUpTestData(cls):
        cls.world = build_liquor_world()

    @REAL
    @given(pub_bills())
    def test_real_pub_bill_matches_the_independent_copy(self, spec):
        [order] = create_orders(self.world, [spec])
        order.recalculate_totals()
        expected = {**row_head(0, spec), **liquor_totals(spec, LIQUOR_CONFIGS)}
        self.assertEqual(liquor_snapshot(order, 0, spec), expected)
