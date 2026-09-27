"""
Golden HTML bills: the money lines of the three bills Rasova renders as web
pages, for the same twenty bills as test_golden_receipts.py.

  bill page        /bill/<id>/ (bill.html; also what the PDF download renders)
  thermal HTML     /thermal-receipt/<id>/ (thermal_receipt.html, browser printing)
  public bill      /bill/public/<token>/ (public_bill.html, the WhatsApp link)

Only lines that carry money or tax wording are kept (amounts, GST, discount,
parcel, totals, GSTIN and so on), so a change to buttons or layout elsewhere
on the page doesn't disturb this file, while any change to what a bill says
about money does. Stored in orders/tests/golden/html_bills_v1.txt.

Regenerate only for a deliberate change, then review the diff:
    GOLDEN_UPDATE=1 python manage.py test orders.tests.test_golden_html_bills
"""
import difflib
import os
import pathlib
import re
from html.parser import HTMLParser

from django.core.signing import TimestampSigner
from django.test import TestCase

from accounts.models import User
from orders.tests.test_golden_receipts import BILLS, MODES, build_outlets, make_order
from orders.views.public_views import PUBLIC_BILL_SALT

GOLDEN = pathlib.Path(__file__).parent / "golden" / "html_bills_v1.txt"

_SKIP = {"script", "style", "template", "noscript", "svg", "head"}
_BLOCKS = {"p", "div", "tr", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "table",
           "section", "header", "footer", "hr", "form", "button", "label", "option", "span"}
_MONEY = re.compile(
    r"₹|\bRs\.?|\d\.\d\d\b|\b(c?s?gst\w*|igst|vat|tax\w*|total|sub-?total|discount|parcel|packing|"
    r"round|bill of supply|composition|incl\w*|invoice|sac|hsn|paid|balance|change|due|free|"
    r"complimentary)\b",
    re.IGNORECASE,
)


class _VisibleText(HTMLParser):
    """The text a person would see, one line per block, table cells joined by ' | '."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skipping = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skipping += 1
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)


def money_lines(html):
    parser = _VisibleText()
    parser.feed(html)
    lines = []
    for raw in "".join(parser.parts).split("\n"):
        line = " ".join(raw.split()).strip(" |")
        if line and _MONEY.search(line):
            lines.append(line)
    return lines


def all_pages(client):
    tenant, outlets = build_outlets()
    out = []
    for mode_index, (outlet, dishes) in enumerate(outlets):
        user = User.objects.create_user(
            username=f"golden_html_{mode_index}", password="x",
            tenant=tenant, outlet=outlet, role="owner",
        )
        client.force_login(user)
        for bill_index, bill in enumerate(BILLS):
            order = make_order(tenant, outlet, dishes, mode_index, bill_index, bill)
            about = f"{MODES[mode_index][0]} | {bill['label']}"
            token = TimestampSigner(salt=PUBLIC_BILL_SALT).sign(order.pk)
            for page, url in (
                ("bill page", f"/bill/{order.pk}/"),
                ("thermal HTML", f"/thermal-receipt/{order.pk}/"),
                ("public bill", f"/bill/public/{token}/"),
            ):
                response = client.get(url)
                out.append(f"==== {page} | {about} ====")
                if response.status_code != 200:
                    out.append(f"(HTTP {response.status_code})")
                    continue
                out += money_lines(response.content.decode("utf-8"))
    return "\n".join(out) + "\n"


class GoldenHtmlBillsTest(TestCase):

    def test_every_html_bill_matches_the_golden_file(self):
        actual = all_pages(self.client)
        if os.environ.get("GOLDEN_UPDATE") == "1":
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(actual, encoding="utf-8", newline="\n")
        expected = GOLDEN.read_text(encoding="utf-8")
        if actual != expected:
            diff = difflib.unified_diff(expected.splitlines(), actual.splitlines(),
                                        "golden", "now", lineterm="", n=2)
            self.fail("HTML bills changed:\n" + "\n".join(list(diff)[:150]))
