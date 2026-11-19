"""
Every script writes money through core/money.js (V12, 19 Nov 2026).

The billing screens moved to rupees() on 13 Nov, but eight scripts still
built the amount by hand: "Rs.21" for Rs 20.50 in order history (paise
rounded away), "₹50.00 Flat" beside "₹50" in the promo picker, a bare
"₹1234.5" in the sales chart. This scan keeps a hand-built amount from
coming back: a rupee sign (or "Rs.") glued to a number in code is refused;
call rupees() instead. A "₹" in a label or placeholder ("Price (₹)") is fine.
Run: python manage.py test core.tests.test_money_one_way
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

JS = Path(settings.BASE_DIR) / "static" / "js"
SKIP = {JS / "core" / "money.js"}
SKIP_DIRS = {JS / "vendor", JS / "tests"}

HAND_BUILT = [
    re.compile(r"₹\$\{"),                         # `₹${amount}`
    re.compile(r"&#8377;\s*\$\{"),                # `&#8377;${amount}`
    re.compile(r"[\"'`]₹[\"'`]\s*\+"),            # "₹" + amount
    re.compile(r"[\"'`]Rs\.?\s*[\"'`]?\s*\+"),    # "Rs." + amount
    re.compile(r"[\"'`]Rs\.?\s*\d"),              # "Rs.0"
]


def hand_built(text):
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0] if not line.lstrip().startswith("*") else ""
        if any(p.search(code) for p in HAND_BUILT):
            found.append((n, line.strip()))
    return found


class MoneyOneWayTest(SimpleTestCase):

    def test_no_script_builds_an_amount_by_hand(self):
        bad = []
        for path in sorted(JS.rglob("*.js")):
            if path in SKIP or any(d in path.parents for d in SKIP_DIRS):
                continue
            for n, line in hand_built(path.read_text(encoding="utf-8")):
                bad.append(f"{path.relative_to(JS)}:{n}: {line}")
        self.assertEqual(bad, [], "write money with rupees() from core/money.js")

    def test_the_scan_sees_each_way_of_doing_it(self):
        for sample in ['x = `₹${n}`', 'x = "&#8377;${n}"', "x = '₹' + n", 'x = "Rs." + n', 'x = "Rs.0"']:
            self.assertTrue(hand_built(sample), sample)
        for fine in ['placeholder: "Amount (₹)"', "x = rupees(n)", '// shows "₹430"']:
            self.assertFalse(hand_built(fine), fine)
