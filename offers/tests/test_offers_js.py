"""
The browser copy of the offer engine (static/js/offers.js) against the real
one (offers/engine.py), and the carts' totals with offers
(static/js/cart_tax.js) against the tax engine, in Node on random carts.

Needs Node.js, like orders/tests/test_cart_tax_js.py: skipped locally
without it, a failure in CI.

Run: python manage.py test offers.tests.test_offers_js
"""
import json
import random
import subprocess
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from offers.engine import CartLine, Rule, Window, evaluate
from offers.services import rule_json
from orders.services.tax_engine import GST, VAT, Line, compute
from orders.tests.test_cart_tax_js import NODE, _needs_node

OFFERS_JS = Path(settings.BASE_DIR) / "static" / "js" / "offers.js"
CART_TAX_JS = Path(settings.BASE_DIR) / "static" / "js" / "cart_tax.js"

EVALUATE = """
const { evaluate } = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map(c => evaluate(c.lines, c.rules, {cutoffHour: c.cutoff}))));
"""
TOTALS = """
const { totals } = require(process.argv[1]);
const carts = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(carts.map(c => totals(c.lines, c.outlet))));
"""


def node(script, path, payload):
    done = subprocess.run([NODE, "-e", script, str(path)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=180, check=True)
    return json.loads(done.stdout)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S") if moment else None


def random_case(rng):
    """A cart of up to 7 lines added over a Friday night, and up to 3 offers."""
    base = datetime(2026, 10, 2, 16, 0)
    lines = []
    for position in range(1, rng.randint(1, 7) + 1):
        added = base + timedelta(minutes=rng.randint(0, 14 * 60))
        lines.append(CartLine(
            key=position, position=position, dish_id=rng.choice([1, 2, 3, 4]),
            category_id=rng.choice([10, 10, 20, None]),
            unit_price=D(rng.choice(["0", "99.99", "120", "250", "333.33", "450", "2300", "2500"])),
            quantity=rng.randint(0, 5), added_at=added if rng.random() > 0.05 else None,
            eligible=rng.random() > 0.15,
        ))
    rules = []
    for rule_id in range(1, rng.randint(1, 3) + 1):
        kind = rng.choice(["buy_get_free", "percent_off", "amount_off"])
        windows = ()
        if rng.random() < 0.6:
            start = rng.choice([None, time(17), time(20), time(22, 30)])
            end = rng.choice([None, time(19), time(2), time(23, 59)])
            if start is not None and start == end:
                end = None
            days = frozenset(rng.sample(range(7), rng.randint(0, 3)))
            windows = (Window(days=days, start=start, end=end),)
        rules.append(Rule(
            id=rule_id, name=f"Offer {rule_id}", kind=kind,
            buy=rng.randint(0, 3), free=rng.randint(0, 2),
            percent=D(rng.choice(["5", "12.5", "33.33", "100"])), amount=D(rng.choice(["10", "75.5", "500"])),
            priority=rng.choice([0, 0, 1, 5]),
            dishes=frozenset(rng.sample([1, 2, 3, 4], rng.randint(0, 2))),
            categories=frozenset(rng.sample([10, 20], rng.randint(0, 1))),
            windows=windows,
            valid_from=rng.choice([None, date(2026, 10, 2), date(2026, 10, 3)]),
            valid_until=rng.choice([None, date(2026, 10, 2), date(2026, 10, 1)]),
            live_until=rng.choice([None, None, base + timedelta(hours=rng.randint(1, 12))]),
        ))
    return lines, rules, rng.choice([6, 6, 4, 0])


def as_js(lines, rules, cutoff):
    return {
        "cutoff": cutoff,
        "lines": [{"key": l.key, "position": l.position, "dishId": l.dish_id, "categoryId": l.category_id,
                   "unitPrice": str(l.unit_price), "quantity": l.quantity, "addedAt": _iso(l.added_at),
                   "eligible": l.eligible} for l in lines],
        # The very form the pages embed (offers.services.rule_json).
        "rules": [rule_json(r) for r in rules],
    }


class OffersJsMatchesTheEngineTest(SimpleTestCase):
    def setUp(self):
        _needs_node(self)

    def test_random_carts_and_offers(self):
        rng = random.Random(20261004)
        cases = [random_case(rng) for _ in range(600)]
        shown = node(EVALUATE, OFFERS_JS, [as_js(*case) for case in cases])
        for n, ((lines, rules, cutoff), js) in enumerate(zip(cases, shown)):
            python = evaluate(lines, rules, cutoff_hour=cutoff)
            with self.subTest(case=n):
                self.assertEqual(
                    {str(k): (a.offer_id, a.offer_name, f"{a.discount:.2f}", a.free_units) for k, a in python.items()},
                    {k: (a["offerId"], a["offerName"], f"{D(str(a['discount'])):.2f}", a["freeUnits"])
                     for k, a in js.items()},
                )

    def test_hand_worked(self):
        lines = [CartLine(key=k, position=k, dish_id=1, category_id=10, unit_price=D(p), quantity=1,
                          added_at=datetime(2026, 10, 3, 1, 0)) for k, p in ((1, "2300"), (2, "2500"), (3, "2300"))]
        late = Rule(id=1, name="Friday late, buy 2 get 1", kind="buy_get_free", buy=2, free=1,
                    windows=(Window(days=frozenset({4}), start=time(20), end=time(2)),))
        js = node(EVALUATE, OFFERS_JS, [as_js(lines, [late], 6)])[0]
        self.assertEqual(js, {"3": {"offerId": 1, "offerName": "Friday late, buy 2 get 1",
                                    "discount": 2300, "freeUnits": 1}})


class CartTotalsWithOffersTest(SimpleTestCase):
    """cart_tax.js with an offer on its lines against the tax engine."""

    def setUp(self):
        _needs_node(self)

    def test_random_carts(self):
        rng = random.Random(4102026)
        carts, bills = [], []
        for _ in range(500):
            inclusive, vat_inclusive = rng.choice([False, True]), rng.choice([False, True])
            js_lines, engine_lines = [], []
            for _ in range(rng.randint(1, 6)):
                amount = D(rng.choice(["99.99", "240", "333.33", "2300", "4600", "1234.56"]))
                offer = rng.choice([D("0"), D("0"), (amount * D(rng.choice(["0.25", "0.1", "0.5", "1"]))).quantize(D("0.01"))])
                if rng.random() < 0.4:
                    rate, kind = D(rng.choice(["0", "5.5", "10"])), VAT
                else:
                    rate, kind = D(rng.choice(["0", "5", "18"])), GST
                js_lines.append({"amount": str(amount), "kind": kind, "rate": str(rate), "offer": str(offer)})
                engine_lines.append(Line(amount=amount, rate=rate, kind=kind, offer_discount=offer,
                                         inclusive=vat_inclusive if kind == VAT else inclusive))
            carts.append({"lines": js_lines, "outlet": {"inclusive": inclusive, "vatInclusive": vat_inclusive}})
            bills.append(compute(engine_lines, prices_include_tax=inclusive))
        shown = node(TOTALS, CART_TAX_JS, carts)
        for n, (bill, cart) in enumerate(zip(bills, shown)):
            with self.subTest(cart=n):
                self.assertEqual(D(str(cart["gst"])).quantize(D("0.01")), bill.gst)
                self.assertEqual(D(str(cart["vat"])).quantize(D("0.01")), bill.vat)
                self.assertEqual(D(str(cart["offers"])).quantize(D("0.01")), bill.discount)
                self.assertEqual(D(cart["roundedTotal"]), bill.grand_total)
