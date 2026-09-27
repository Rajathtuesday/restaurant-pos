"""
Golden receipts: the exact printout of every thermal slip that shows money,
for five bills in each outlet mode.

  bill           what the phone agent prints (the real bytes from
                 printing.views._build_receipt_b64), on 58 mm and 80 mm paper
  split bill     counter mode: the summary slip plus one slip per category
  token receipt  the QSR counter receipt

Any change to a printout shows up in `git diff` of
orders/tests/golden/receipts_v1.txt as exactly the lines that moved. The
liquor VAT work changes bills on purpose (CGST and SGST lines, a VAT line);
those changes must arrive with a regenerated file whose diff was read.

Each printed line is stored as  <font><bold><align> |<text>|
    font   A normal, B small          bold   * when bold
    align  < left, ^ centre, > right  a size like 2x2 follows when enlarged
The bars show the exact width, trailing spaces included.

Regenerate only for a deliberate change, then review the diff:
    GOLDEN_UPDATE=1 python manage.py test orders.tests.test_golden_receipts
"""
import base64
import datetime as dt
import difflib
import os
import pathlib
from decimal import Decimal

from django.test import TestCase

from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment, Table
from orders.services.printing_service import PrintingService
from printing.views import _build_receipt_b64
from tenants.models import Outlet, Tenant
from tokens.models import TokenOrder

GOLDEN = pathlib.Path(__file__).parent / "golden" / "receipts_v1.txt"

# (label, gst_inclusive, is_composition_scheme, area)
MODES = [
    ("GST extra", False, False, "Indiranagar"),
    ("GST included", True, False, "Koramangala"),
    ("composition", False, True, "Jayanagar"),
    ("composition, GST included", True, True, "HSR Layout"),
]

# key: (name, category, price, gst %, veg)
DISHES = {
    "dosa": ("Masala Dosa", "Mains", "90.00", "5", True),
    "chicken": ("Chicken 65", "Starters", "240.00", "5", False),
    "coffee": ("Filter Coffee", "Beverages", "25.00", "5", True),
    "combo": ("Paneer Tikka Masala with Butter Naan Combo Special", "Mains", "349.50", "5", True),
    "curd": ("Plain Curd", "Mains", "30.00", "0", True),
    "beer": ("Kingfisher Premium 650ml", "Bar", "320.00", "18", True),
    "sandwich": ("Veg Club Sandwich", "Starters", "99.99", "18", True),
}

BILLS = [
    dict(label="dine-in, cash", table="T4", lines=[("dosa", 2), ("chicken", 1)], pay="cash"),
    dict(label="parcel with a token, UPI", token=17, parcel="20.00",
         lines=[("coffee", 3), ("combo", 1)], pay="upi"),
    dict(label="mixed rates, 10% off, one voided and one free dish", table="T12",
         lines=[("chicken", 2), ("curd", 1), ("beer", 2),
                ("dosa", 1, {"status": "voided"}), ("coffee", 1, {"is_complimentary": True})],
         discount=("percentage", "10"), pay="card"),
    dict(label="paise, an item discount and a flat discount",
         lines=[("sandwich", 3), ("combo", 2, {"item_discount_pct": Decimal("12.50")})],
         discount=("amount", "49.99"), pay="cash"),
    dict(label="big order, not paid yet", table="T1", lines=[("dosa", 13), ("chicken", 7)]),
]

# 20 Sep 2026, 14:30 UTC, which is 8 PM in Bengaluru
BILLED_AT = dt.datetime(2026, 9, 20, 14, 30, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# Turning printouts into readable lines
# ---------------------------------------------------------------------------

def _tag(style):
    return f'{style["font"]}{style["bold"]}{style["align"]}{style["size"]}'


def render_escpos(raw, encoding="cp437"):
    """Readable lines from receipt bytes: each style change becomes the tag of
    the lines printed after it; unknown control codes are shown, not hidden."""
    style = {"font": "A", "bold": " ", "align": "<", "size": ""}
    lines, text, i = [], bytearray(), 0
    while i < len(raw):
        code = raw[i:i + 2]
        arg = raw[i + 2] if i + 2 < len(raw) else 0
        if code == b"\x1bM":
            style["font"] = "B" if arg == 1 else "A"
        elif code == b"\x1ba":
            style["align"] = "<^>"[arg] if arg < 3 else "?"
        elif code == b"\x1bE":
            style["bold"] = "*" if arg else " "
        elif code == b"\x1d!":
            style["size"] = "" if arg == 0 else f" {(arg >> 4) + 1}x{(arg & 15) + 1}"
        elif code == b"\x1dV":
            lines.append(f"~~~ cut {'FULL' if arg == 0 else 'PART'} ~~~")
        elif raw[i] in (0x1b, 0x1d):
            lines.append(f"~~~ unknown control {raw[i:i + 3].hex(' ')} ~~~")
        elif raw[i] == 0x0a:
            lines.append(f"{_tag(style)} |{text.decode(encoding)}|")
            text.clear()
            i += 1
            continue
        else:
            text.append(raw[i])
            i += 1
            continue
        i += 3
    if text:
        lines.append(f"{_tag(style)} |{text.decode(encoding)}| (no line feed)")
    return lines


class RecordingPrinter:
    """Takes the place of the printer on the split-bill and token paths and
    writes down what it was told, in the same form as render_escpos()."""

    def __init__(self):
        self.lines, self._text = [], ""
        self._style = {"font": "A", "bold": " ", "align": "<", "size": ""}

    def set(self, align="left", bold=False, font="a", double_width=False, double_height=False,
            width=1, height=1, **_):
        w = 2 if double_width else width
        h = 2 if double_height else height
        self._style = {
            "font": "B" if str(font).lower() == "b" else "A",
            "bold": "*" if bold else " ",
            "align": {"left": "<", "center": "^", "right": ">"}.get(align, "?"),
            "size": "" if (w, h) == (1, 1) else f" {w}x{h}",
        }

    def text(self, content):
        self._text += content
        *done, self._text = self._text.split("\n")
        self.lines += [f"{_tag(self._style)} |{line}|" for line in done]

    def cut(self, mode="FULL"):
        self.lines.append(f"~~~ cut {'FULL' if mode == 'FULL' else 'PART'} ~~~")


def _record(width, method, order):
    svc = PrintingService(chars_per_line=width)
    printer = RecordingPrinter()
    svc.get_printer = lambda: printer
    if not getattr(svc, method)(order):
        return printer.lines + ["~~~ printing failed ~~~"]
    return printer.lines


# ---------------------------------------------------------------------------
# The bills
# ---------------------------------------------------------------------------

def build_outlets():
    tenant = Tenant.objects.create(name="Golden Bistro")
    outlets = []
    for label, inclusive, composition, area in MODES:
        outlet = Outlet.objects.create(
            tenant=tenant, name=f"Golden Bistro {area}",
            address=f"No. 12, 100 Feet Road, {area}, Bengaluru 560038",
            phone="080 4123 4567", gst_no="29ABCDE1234F1Z5", fssai_no="11223344556677",
            gst_inclusive=inclusive, is_composition_scheme=composition,
        )
        categories, dishes = {}, {}
        for key, (name, category, price, gst, veg) in DISHES.items():
            if category not in categories:
                categories[category] = MenuCategory.objects.create(
                    tenant=tenant, outlet=outlet, name=category)
            dishes[key] = MenuItem.objects.create(
                tenant=tenant, outlet=outlet, category=categories[category], name=name,
                price=Decimal(price), gst_percentage=Decimal(gst), is_veg=veg,
            )
        outlets.append((outlet, dishes))
    return tenant, outlets


def make_order(tenant, outlet, dishes, mode_index, bill_index, bill):
    table = None
    if bill.get("table"):
        table = Table.objects.create(tenant=tenant, outlet=outlet, name=bill["table"])
    dtype, dval = bill.get("discount", (None, "0"))
    order = Order.objects.create(
        tenant=tenant, outlet=outlet, table=table,
        source="dine_in" if table else "counter",
        status="paid" if bill.get("pay") else "open",
        order_number=f"GOLD-{mode_index}{bill_index}",
        discount_type=dtype, discount_value=Decimal(dval),
        parcel_surcharge=Decimal(bill.get("parcel", "0")),
        # as toggle_parcel does: the outlet's parcel GST rate, copied onto the bill
        parcel_gst_rate=outlet.parcel_gst_rate if bill.get("parcel") else None,
    )
    for key, qty, *extra in bill["lines"]:
        dish = dishes[key]
        OrderItem.objects.create(
            order=order, menu_item=dish, quantity=qty, price=dish.price,
            gst_percentage=dish.gst_percentage, total_price=dish.price * qty,
            **(extra[0] if extra else {}),
        )
    if bill.get("token"):
        TokenOrder.objects.create(tenant=tenant, outlet=outlet, order=order,
                                  token_number=bill["token"], date=BILLED_AT.date())
    order.recalculate_totals()
    if bill.get("pay"):
        Payment.objects.create(order=order, method=bill["pay"], amount=order.grand_total)
    Order.objects.filter(pk=order.pk).update(created_at=BILLED_AT + dt.timedelta(minutes=bill_index))
    return Order.objects.get(pk=order.pk)


def all_printouts():
    tenant, outlets = build_outlets()
    out = []
    for mode_index, (outlet, dishes) in enumerate(outlets):
        for bill_index, bill in enumerate(BILLS):
            order = make_order(tenant, outlet, dishes, mode_index, bill_index, bill)
            about = f"{MODES[mode_index][0]} | {bill['label']}"
            for width in (32, 48):
                raw = base64.b64decode(_build_receipt_b64(order, width, "full", "cp437"))
                out.append(f"==== bill, {width} chars | {about} ====")
                out += render_escpos(raw)
            out.append(f"==== split bill, 48 chars | {about} ====")
            out += _record(48, "print_split_by_category", order)
            out.append(f"==== token receipt, 32 chars | {about} ====")
            out += _record(32, "print_token_receipt", order)
    return "\n".join(out) + "\n"


class GoldenReceiptsTest(TestCase):

    def test_every_printout_matches_the_golden_file(self):
        actual = all_printouts()
        if os.environ.get("GOLDEN_UPDATE") == "1":
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(actual, encoding="utf-8", newline="\n")
        expected = GOLDEN.read_text(encoding="utf-8")
        if actual != expected:
            diff = difflib.unified_diff(expected.splitlines(), actual.splitlines(),
                                        "golden", "now", lineterm="", n=2)
            self.fail("printouts changed:\n" + "\n".join(list(diff)[:150]))
