"""
Printing Service — Unit Tests
==============================

Tests for:
  - BytesPrinter.set()   ESC/POS byte sequences
  - _print_bill_body     margin from PrintProfile, fallback defaults
  - _print_kot_body      large_font / show_total from PrintProfile, fallback defaults

Run: python manage.py test orders.tests.test_printing_service --keepdb
"""

import base64

from django.test import TestCase

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import KOTBatch, Order, OrderItem
from printing.views import _build_receipt_b64
from orders.services.printing_service import PrintingService
from setup.models import KitchenStation, PaymentConfig
from tenants.models import Outlet, PrintProfile, Tenant


# ── Minimal ESC/POS printer that captures raw bytes ───────────────────────────

class BP:
    """Mirror of the BytesPrinter used in print_queue.py — used for direct tests."""
    def __init__(self, encoding="cp437"):
        self.buf = b""
        self._enc = encoding

    def text(self, t):
        if isinstance(t, str):
            self.buf += t.encode(self._enc, errors="replace")
        else:
            self.buf += bytes(t)

    def set(self, **kw):
        f = kw.get("font", "a")
        self.buf += b"\x1b\x4d" + bytes([0 if str(f).lower() == "a" else 1])
        a = {"left": 0, "center": 1, "right": 2}.get(kw.get("align", "left"), 0)
        self.buf += b"\x1b\x61" + bytes([a])
        self.buf += b"\x1b\x45" + bytes([1 if kw.get("bold") else 0])
        w = 2 if kw.get("double_width") else kw.get("width", 1)
        h = 2 if kw.get("double_height") else kw.get("height", 1)
        self.buf += b"\x1d\x21" + bytes([((w - 1) << 4) | (h - 1)])

    def cut(self, mode="FULL"):
        self.buf += b"\x1d\x56\x00" if mode == "FULL" else b"\x1d\x56\x01"


# ── BytesPrinter.set() ESC/POS byte tests ─────────────────────────────────────

class BytesPrinterSetTests(TestCase):

    def setUp(self):
        self.p = BP()

    def test_center_align_emits_esc_a_1(self):
        self.p.set(align="center")
        self.assertIn(b"\x1b\x61\x01", self.p.buf)

    def test_left_align_emits_esc_a_0(self):
        self.p.set(align="left")
        self.assertIn(b"\x1b\x61\x00", self.p.buf)

    def test_right_align_emits_esc_a_2(self):
        self.p.set(align="right")
        self.assertIn(b"\x1b\x61\x02", self.p.buf)

    def test_bold_on_emits_esc_e_1(self):
        self.p.set(bold=True)
        self.assertIn(b"\x1b\x45\x01", self.p.buf)

    def test_bold_off_emits_esc_e_0(self):
        self.p.set(bold=False)
        self.assertIn(b"\x1b\x45\x00", self.p.buf)

    def test_double_height_emits_gs_bang_01(self):
        self.p.set(double_height=True)
        self.assertIn(b"\x1d\x21\x01", self.p.buf)

    def test_double_width_emits_gs_bang_10(self):
        self.p.set(double_width=True)
        self.assertIn(b"\x1d\x21\x10", self.p.buf)

    def test_double_width_and_height_emits_gs_bang_11(self):
        self.p.set(double_width=True, double_height=True)
        self.assertIn(b"\x1d\x21\x11", self.p.buf)

    def test_normal_size_emits_gs_bang_00(self):
        self.p.set()
        self.assertIn(b"\x1d\x21\x00", self.p.buf)

    def test_font_b_emits_esc_m_1(self):
        self.p.set(font="b")
        self.assertIn(b"\x1b\x4d\x01", self.p.buf)

    def test_font_a_emits_esc_m_0(self):
        self.p.set(font="a")
        self.assertIn(b"\x1b\x4d\x00", self.p.buf)

    def test_no_align_defaults_to_left(self):
        self.p.set(bold=True)
        self.assertIn(b"\x1b\x61\x00", self.p.buf)

    def test_full_cut_emits_gs_v_00(self):
        self.p.cut(mode="FULL")
        self.assertIn(b"\x1d\x56\x00", self.p.buf)

    def test_partial_cut_emits_gs_v_01(self):
        self.p.cut(mode="PART")
        self.assertIn(b"\x1d\x56\x01", self.p.buf)


# ── Shared fixture ─────────────────────────────────────────────────────────────

class PrintingServiceBase(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Print Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Branch")
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet, cash_enabled=True,
        )
        self.owner = User.objects.create_user(
            username="ps_owner", password="testpass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Food"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.category, name="Masala Dosa",
            price=80, gst_percentage=5,
        )
        self.station = KitchenStation.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Kitchen", is_default=True, is_active=True,
            printer_ip="192.168.1.50", printer_port=9100,
            printer_encoding="cp437", paper_width_mm=80,
        )

    def _make_order(self, qty=2):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.owner, source="counter", status="closed",
        )
        OrderItem.objects.create(
            order=order, menu_item=self.item,
            quantity=qty, price=self.item.price,
            gst_percentage=self.item.gst_percentage,
            total_price=self.item.price * qty,
        )
        order.recalculate_totals()
        return order

    def _make_kot(self, order):
        kot = KOTBatch.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, kot_number=1,
            station=self.station, status="confirmed",
        )
        # Link order items to this KOT
        order.items.update(kot=kot)
        return kot

    def _bill_bytes(self, order, chars=48):
        raw = base64.b64decode(_build_receipt_b64(order, chars, "full", "cp437"))
        return raw

    def _kot_bytes(self, order, kot, chars=48):
        svc = PrintingService(chars_per_line=chars)
        buf = BP()
        svc._print_kot_body(buf, order, kot)
        return buf.buf


# ── _print_bill_body: margin and alignment ─────────────────────────────────────

class BillBodyTests(PrintingServiceBase):

    def test_bill_contains_center_alignment(self):
        order = self._make_order()
        raw = self._bill_bytes(order)
        self.assertIn(b"\x1b\x61\x01", raw)  # ESC a 1 — center

    def test_bill_contains_bold_total(self):
        order = self._make_order()
        raw = self._bill_bytes(order)
        total_bytes = "TOTAL".encode("cp437")
        bold_on = b"\x1b\x45\x01"
        # bold_on must appear before TOTAL somewhere in the buffer
        bold_pos  = raw.rfind(bold_on, 0, raw.find(total_bytes))
        self.assertGreater(bold_pos, -1, "ESC E 1 not found before TOTAL")

    def test_bill_contains_full_cut(self):
        order = self._make_order()
        raw = self._bill_bytes(order)
        self.assertIn(b"\x1d\x56\x00", raw)

    def test_bill_default_margin_content_width_44(self):
        """Without a profile, inner width C = 48 - 4 = 44 → inner separators are 44 dashes."""
        order = self._make_order()
        raw = self._bill_bytes(order, chars=48)
        text = raw.decode("cp437", errors="replace")
        # Inner separators (items/totals sections) are 44 dashes
        self.assertIn("-" * 44, text)
        # Inner separators must NOT be 48 dashes (only the full-width header sep is 48)
        # Count occurrences: exactly 1 line of 48 dashes (the header sep), rest are 44
        self.assertEqual(text.count("-" * 48), 1)

    def test_bill_profile_margin_0_content_width_48(self):
        """Profile with bill_inner_margin=0 → full-width content, no indented separator."""
        profile = PrintProfile.objects.create(
            tenant=self.tenant, name="No Margin",
            bill_inner_margin=0, kot_large_font=True, kot_show_total=True,
        )
        self.outlet.print_profile = profile
        self.outlet.save()
        order = self._make_order()
        raw = self._bill_bytes(order, chars=48)
        text = raw.decode("cp437", errors="replace")
        self.assertIn("-" * 48, text)

    def test_bill_profile_margin_8_content_width_40(self):
        """Profile with bill_inner_margin=8 → inner separators are 40 dashes."""
        profile = PrintProfile.objects.create(
            tenant=self.tenant, name="Wide Margin",
            bill_inner_margin=8, kot_large_font=True, kot_show_total=True,
        )
        self.outlet.print_profile = profile
        self.outlet.save()
        order = self._make_order()
        raw = self._bill_bytes(order, chars=48)
        text = raw.decode("cp437", errors="replace")
        self.assertIn("-" * 40, text)
        # Only the full-width header sep should be 48 dashes (exactly once)
        self.assertEqual(text.count("-" * 48), 1)

    def test_bill_outlet_without_profile_uses_defaults(self):
        """Outlet with no profile assigned still produces a valid bill."""
        self.outlet.print_profile = None
        self.outlet.save()
        order = self._make_order()
        raw = self._bill_bytes(order)
        self.assertIn(b"\x1d\x56\x00", raw)   # has a cut
        self.assertGreater(len(raw), 50)        # non-trivial output


# ── _print_kot_body: large_font and show_total ─────────────────────────────────

class KotBodyTests(PrintingServiceBase):

    def test_kot_large_font_true_emits_double_height(self):
        """Default / profile with kot_large_font=True → GS ! 0x01 in item rows."""
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        self.assertIn(b"\x1d\x21\x01", raw)   # GS ! — double height

    def test_kot_large_font_false_no_double_height_for_items(self):
        """Profile with kot_large_font=False → item rows do NOT use double-height."""
        profile = PrintProfile.objects.create(
            tenant=self.tenant, name="Small KOT",
            bill_inner_margin=4, kot_large_font=False, kot_show_total=False,
        )
        self.outlet.print_profile = profile
        self.outlet.save()
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        # Double-height (0x01) must NOT appear after the header double-size block
        # Header uses double_width+double_height → GS ! 0x11
        # After header the only GS ! values should be 0x00 (normal)
        after_header = raw[raw.find(b"\x1d\x21\x11") + 3:]
        self.assertNotIn(b"\x1d\x21\x01", after_header)

    def test_kot_show_total_true_prints_total_line(self):
        """Default / profile with kot_show_total=True → 'Total' in output."""
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        self.assertIn(b"Total", raw)

    def test_kot_show_total_false_omits_total_line(self):
        """Profile with kot_show_total=False → no 'Total' line."""
        profile = PrintProfile.objects.create(
            tenant=self.tenant, name="No Total KOT",
            bill_inner_margin=4, kot_large_font=True, kot_show_total=False,
        )
        self.outlet.print_profile = profile
        self.outlet.save()
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        self.assertNotIn(b"Total", raw)

    def test_kot_no_profile_defaults_show_total(self):
        """Outlet without profile uses fallback default: show_total=True."""
        self.outlet.print_profile = None
        self.outlet.save()
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        self.assertIn(b"Total", raw)

    def test_kot_no_profile_defaults_large_font(self):
        """Outlet without profile uses fallback default: large_font=True."""
        self.outlet.print_profile = None
        self.outlet.save()
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        self.assertIn(b"\x1d\x21\x01", raw)   # double height present

    def test_kot_item_name_appears_in_output(self):
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        self.assertIn(b"Masala Dosa", raw)

    def test_kot_header_uses_double_width_and_height(self):
        order = self._make_order()
        kot   = self._make_kot(order)
        raw   = self._kot_bytes(order, kot)
        self.assertIn(b"\x1d\x21\x11", raw)   # GS ! 0x11 — double width + height
