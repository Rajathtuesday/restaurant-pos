"""
B11: the parcel charge a billing screen shows before the order exists comes
from the bill's own rule.

Why: in October the cart showed 252 and the bill said 264, because the screen
had its own idea of the parcel rule. The screen's copy is
static/js/billing/parcel.js; the bill's rule is orders/services/parcel.py,
which toggle_parcel uses. The tax and offer engines already have tests like
this one (orders/tests/test_cart_tax_js.py, offers/tests/test_offers_js.py).

Three checks:
  1. random carts: the screen's rule in Node against the server's rule;
  2. a real billing page: what it hands the browser (the outlet's rate, each
     dish's own charge in data-parcel), through the screen's cart and parcel
     modules, against toggle_parcel on an order of the same dishes;
  3. the parcel button's words use the one money format ("₹2.50/item", not
     "₹3/item" from floatformat:0).

Needs Node.js, like the other parity tests: skipped locally without it, a
failure in CI.

Run: python manage.py test orders.tests.test_parcel_parity
"""
import json
import random
import re
import subprocess
from decimal import Decimal as D
from html import unescape
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from orders.services.parcel import parcel_charge
from orders.tests.test_cart_tax_js import NODE, _needs_node
from orders.tests.test_parcel_dispatch import ParcelBase

BILLING_JS = Path(settings.BASE_DIR) / "static" / "js" / "billing"

# The screen's own modules: a cart filled the way a tap fills it, then the
# parcel wish made before the order exists, then what the cart shows.
RUNNER = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const dir = pathToFileURL(process.argv[1] + "/").href;
const { createCart } = await import(dir + "cart.js");
const { createParcel } = await import(dir + "parcel.js");
const out = [];
for (const c of JSON.parse(readFileSync(0, "utf8"))) {
  const cart = createCart();
  for (const dish of c.dishes) for (let n = 0; n < dish.quantity; n++) cart.add(dish.dish, { mergeBy: "dish" });
  const parcel = createParcel({ rate: c.rate, perItem: c.perItem, api: null, toggleUrl: "/toggle-parcel/0/" });
  await parcel.toggle(null);
  out.push(parcel.chargeFor(cart.items));
}
process.stdout.write(JSON.stringify(out));
"""


def screen_charges(carts):
    done = subprocess.run([NODE, "--input-type=module", "-e", RUNNER, str(BILLING_JS)],
                          input=json.dumps(carts), capture_output=True, text=True, timeout=180)
    if done.returncode:
        raise AssertionError(done.stderr[-3000:])
    return json.loads(done.stdout)


def paise(value):
    return D(str(value)).quantize(D("0.01"))


def random_carts(count, seed=20261114):
    rng = random.Random(seed)
    carts = []
    for _ in range(count):
        dishes = []
        for dish_id in range(1, rng.randint(1, 6) + 1):
            own = rng.choice(["0", "0", "0", "0", "5", "7.50", "2.25", "12", "0.10"])
            dishes.append({"dish": {"id": dish_id, "name": f"Dish {dish_id}", "price": 100,
                                    "parcel_charge": own},
                           "quantity": rng.randint(1, 7)})
        carts.append({"rate": rng.choice(["0", "5", "10", "2.50", "12.75", "0.10"]),
                      "perItem": rng.random() < 0.5, "dishes": dishes})
    return carts


def server_charge(cart):
    return parcel_charge(((d["dish"]["parcel_charge"], d["quantity"]) for d in cart["dishes"]),
                         cart["rate"], cart["perItem"])


class ParcelRuleParityTest(SimpleTestCase):

    def test_random_carts_show_what_the_bill_charges(self):
        _needs_node(self)
        carts = random_carts(300)
        shown = screen_charges(carts)
        own_rule = sum(1 for c in carts if any(D(d["dish"]["parcel_charge"]) > 0 for d in c["dishes"]))
        self.assertGreater(own_rule, 50, "the random carts must reach both halves of the rule")
        for n, (cart, on_screen) in enumerate(zip(carts, shown)):
            with self.subTest(cart=n, rate=cart["rate"], per_item=cart["perItem"]):
                self.assertEqual(paise(on_screen), paise(server_charge(cart)))

    def test_the_rule_itself(self):
        self.assertEqual(parcel_charge([(None, 2), (D("0"), 3)], D("5"), True), D("25"))
        self.assertEqual(parcel_charge([(None, 2), (D("0"), 3)], D("5"), False), D("5"))
        self.assertEqual(parcel_charge([(D("7.50"), 2), (D("0"), 3)], D("5"), True), D("15"),
                         "a dish with its own charge: only those, the outlet's rate is not added")
        self.assertEqual(parcel_charge([], D("5"), True), D("0"))
        self.assertEqual(parcel_charge([(None, 1)], None, False), D("0"))


class BillingPageParcelTest(ParcelBase):
    """What the billing page hands the browser, against toggle_parcel."""

    def page(self):
        html = self.client.get("/billing/").content.decode()
        data = json.loads(re.search(
            r'<script id="page-data" type="application/json">(.*?)</script>', html, re.S).group(1))
        charges = {int(i): unescape(p) for i, p in re.findall(
            r'data-action="menu.pick" data-id="(\d+)".*?data-parcel="([^"]*)"', html)}
        return html, data, charges

    def check(self, rate, per_item, own, quantities):
        self.outlet.parcel_charge_amount = D(rate)
        self.outlet.parcel_charge_per_item = per_item
        self.outlet.save()
        dishes = [self.idli, self.dosa, self.juice]
        for dish, charge in zip(dishes, own):
            dish.parcel_charge = D(charge)
            dish.save()
        _html, data, charges = self.page()
        cart = {"rate": data["outlet"]["parcelCharge"], "perItem": data["outlet"]["parcelPerItem"],
                "dishes": [{"dish": {"id": d.id, "name": d.name, "price": float(d.price),
                                     "parcel_charge": charges[d.id]}, "quantity": q}
                           for d, q in zip(dishes, quantities) if q]}
        [on_screen] = screen_charges([cart])
        order = self._make_order([(d, q) for d, q in zip(dishes, quantities) if q])
        on_bill = self._toggle(order.id).json()["parcel_amount"]
        self.assertEqual(paise(on_screen), paise(on_bill), (rate, per_item, own, quantities))
        return paise(on_bill)

    def test_the_screen_shows_what_toggle_parcel_charges(self):
        _needs_node(self)
        self.assertEqual(self.check("5", True, ["0", "0", "0"], [2, 1, 3]), D("30"))
        self.assertEqual(self.check("2.50", True, ["0", "0", "0"], [3, 0, 1]), D("10"))
        self.assertEqual(self.check("20", False, ["0", "0", "0"], [4, 2, 0]), D("20"))
        self.assertEqual(self.check("5", True, ["7.50", "0", "12"], [2, 5, 1]), D("27"))
        self.assertEqual(self.check("12.75", False, ["0.10", "0", "0"], [3, 1, 1]), D("0.30"))

    def test_the_parcel_button_says_the_rate_the_one_way(self):
        self.outlet.parcel_charge_amount = D("2.50")
        self.outlet.save()
        html, data, _charges = self.page()
        self.assertEqual(data["parcelOffer"], "Add Parcel ₹2.50/item")
        self.assertIn('<span class="parcel-btn-text">Add Parcel ₹2.50/item</span>', html)
        self.assertNotIn("₹3/item", html)
        self.outlet.parcel_charge_amount = D("20")
        self.outlet.parcel_charge_per_item = False
        self.outlet.save()
        self.assertEqual(self.page()[1]["parcelOffer"], "Add Parcel ₹20 flat")
