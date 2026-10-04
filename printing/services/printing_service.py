# printing/services/printing_service.py
"""
Thermal printer service using python-escpos.

Supports Epson, Star, Citizen, and any ESC/POS-compatible printer over:
  - Network (IP:port 9100)  — most restaurant printers
  - USB (vendor_id + product_id) — single-terminal setups

Paper widths:
  58mm → 32 chars/line
  80mm → 48 chars/line   ← default in KitchenStation

₹ symbol is NOT in CP437 (the ESC/POS standard code page). All money
amounts are formatted as "Rs.X" to be safe. If the printer is configured
with printer_encoding="utf-8" and actually supports UTF-8, you can swap
the currency symbol, but CP437 is the safe default.
"""

import logging

from django.utils import timezone

from orders.services.bill_layout import bill_layout, money, rate_text

logger = logging.getLogger("pos.orders")


class ConsolePrinter:
    """
    Drop-in replacement for escpos Network/Usb printers.
    Writes to stdout so you can preview output without a physical printer.
    Set printer_type="console" on PrintingService to use it.
    """
    def __init__(self, chars_per_line=48):
        self._bold = False
        self._W = chars_per_line

    def text(self, content):
        print(content, end="")

    def set(self, align=None, bold=False, double_width=False, double_height=False, **_):
        self._bold = bold

    def cut(self, mode="FULL"):
        w = self._W
        if mode == "FULL":
            print("\n" + "=" * w + " [FULL CUT - paper tears here] " + "=" * w + "\n")
        else:
            print("\n" + "-" * w + " [partial cut - stays connected] " + "-" * w + "\n")



def _get_printer_from_station(station):
    """
    Build a PrintingService from a KitchenStation instance.
    Returns None if the station has no printer IP.
    """
    if not station or not station.printer_ip:
        return None
    return PrintingService(
        printer_type="network",
        host=station.printer_ip,
        port=station.printer_port,
        chars_per_line=station.chars_per_line,
        cut_type=station.cut_type,
        encoding=station.printer_encoding,
    )


class PrintingService:
    def __init__(
        self,
        printer_type="network",
        host=None,
        port=9100,
        vendor_id=None,
        product_id=None,
        chars_per_line=48,
        cut_type="full",
        encoding="cp437",
    ):
        self.printer_type = printer_type
        self.host = host
        self.port = port
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.W = chars_per_line      # chars per line at normal font size
        self.cut_type = cut_type
        self.encoding = encoding

    # ------------------------------------------------------------------
    # CONNECTION
    # ------------------------------------------------------------------

    def get_printer(self):
        try:
            if self.printer_type == "console":
                return ConsolePrinter()
            elif self.printer_type == "network" and self.host:
                from escpos.printer import Network
                # 3-second timeout. Real LAN printers connect in <1s.
                # Short timeout means unreachable printers fail fast instead of
                # blocking send-to-kitchen for 10s per station.
                return Network(self.host, port=self.port, timeout=3)
            elif self.printer_type == "usb" and self.vendor_id:
                from escpos.printer import Usb
                return Usb(self.vendor_id, self.product_id)
        except Exception as e:
            logger.error("Printer connection failed (%s:%s): %s", self.host, self.port, e)
        return None

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _cut(self, p):
        if self.cut_type == "full":
            p.cut(mode="FULL")
        elif self.cut_type == "partial":
            p.cut(mode="PART")
        # "none" → skip cut; staff tears manually

    def _currency(self, amount) -> str:
        """₹ doesn't exist in CP437 — always use Rs. for safety. To the paisa:
        a bill rounded to the rupee line by line doesn't add up."""
        return f"Rs.{money(amount)}"

    def _dish_rows(self, line, width) -> list:
        """A bill's dish on as many lines as its name needs: quantity, veg
        mark, name and amount on the first, the rest of the name below it,
        never past the paper edge."""
        prefix = f"{line.quantity}x {'[V]' if line.is_veg else '[N]'} "
        amount = "FREE" if line.free else money(line.amount)
        name_width = max(width - len(prefix) - len(amount) - 1, 6)
        chunks = self._wrap_text(line.name, name_width)
        first = f"{prefix}{chunks[0]}"
        rows = [first[:width - len(amount) - 1].ljust(width - len(amount)) + amount]
        indent = " " * len(prefix)
        for chunk in self._wrap_text(" ".join(chunks[1:]), width - len(prefix)) if chunks[1:] else []:
            rows.append(f"{indent}{chunk}"[:width])
        if line.offer_amount:
            cut = f"-{money(line.offer_amount)}"
            label = f"{indent}{line.offer}"[:width - len(cut) - 1]
            rows.append(label.ljust(width - len(cut)) + cut)
        return rows

    def _print_money(self, p, layout, width, align="center", tall_total=False):
        """The bill's money rows and its total, from
        orders/services/bill_layout.py: what every printed bill says.
        Returns the two-column formatter for the lines that follow."""
        def two(left, right):
            right = str(right)
            return str(left)[:width - len(right) - 1].ljust(width - len(right)) + right

        for row in layout.rows:
            p.text(two(row.label, money(row.amount)) + "\n")
        p.text("-" * width + "\n")
        p.set(align=align, bold=True, font="a", double_height=tall_total)
        p.text(two("TOTAL", self._currency(layout.total)) + "\n")
        p.set(align=align, bold=False, font="a")
        return two

    def _print_included(self, p, layout, two):
        """Tax already inside the prices: shown with its taxable value, never
        added again (a tax invoice states the rate and amount of tax)."""
        if not layout.included:
            return
        p.text(("Prices include GST:" if layout.prices_include_gst else "Prices include tax:") + "\n")
        for tax in layout.included:
            p.text(f"{tax.label} on {money(tax.taxable)}\n")
            if tax.kind == "gst":
                half = rate_text(tax.rate / 2)
                p.text(two(f"  CGST {half}", money(tax.cgst)) + "\n")
                p.text(two(f"  {layout.state_tax} {half}", money(tax.sgst)) + "\n")
            else:
                p.text(two("  " + tax.label.split()[0], money(tax.tax)) + "\n")
        if len(layout.included) > 1:
            p.text(two("Total tax included", money(layout.included_total)) + "\n")

    def _print_title(self, p, layout, width):
        """Tax Invoice, Bill of Supply (with the composition statement) or Bill."""
        p.set(align="center", bold=True, font="a")
        p.text(f"{layout.title.upper()}\n")
        p.set(align="center", bold=False, font="a")
        for chunk in self._wrap_text(layout.statement, width) if layout.statement else []:
            p.text(f"{chunk}\n")

    def _sep(self) -> str:
        return "-" * self.W

    def _ljust(self, text, width) -> str:
        text = str(text)
        return text[:width].ljust(width)

    def _rjust(self, text, width) -> str:
        text = str(text)
        return text[:width].rjust(width)

    def _two_col(self, left, right) -> str:
        """Left-justify + right-justify to fill exactly W chars."""
        right = str(right)
        left_width = self.W - len(right)
        left = str(left)[:left_width].ljust(left_width)
        return left + right

    def _wrap_text(self, text: str, width: int) -> list:
        """Break text at word boundaries to fit within width."""
        import textwrap
        return textwrap.wrap(text, width) or [text[:width]]

    def _pack_lines(self, items: list, width: int) -> list:
        """Pack strings onto fewest lines, two-space-separated."""
        lines = []
        current = ""
        for item in items:
            candidate = (current + "  " + item) if current else item
            if len(candidate) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = item[:width]
        if current:
            lines.append(current)
        return lines

    # ------------------------------------------------------------------
    # KOT BODY  (shared by print_kot and print_bill_with_kots)
    # ------------------------------------------------------------------

    def _print_kot_body(self, p, order, kot_batch):
        W = self.W
        profile = getattr(order.outlet, "print_profile", None)
        large_font = profile.kot_large_font if profile else True
        show_total = profile.kot_show_total if profile else True

        p.set(align="center", bold=True, double_width=True, double_height=True)
        p.text(f"KOT #{kot_batch.kot_number}\n")

        p.set(align="left", bold=False, double_width=False, double_height=False)
        if hasattr(order, "token") and order.token:
            ref = f"TKN:{order.token.token_number}"
        else:
            table_name = order.table.name if order.table else "Walk-in"
            ref = f"TBL:{table_name}"
        station_name = kot_batch.station.name if kot_batch.station else ""
        info = f"{ref}  [{station_name}]" if station_name else ref
        p.text(f"{info[:W]}\n")

        p.text(self._sep() + "\n")

        # A reprint must not bring back dishes cancelled after the ticket went out.
        items = list(kot_batch.items.exclude(status="voided").select_related("menu_item"))
        kot_total = sum(item.total_price for item in items)

        for item in items:
            veg_flag = "[V]" if item.menu_item.is_veg else "[N]"
            qty_label = f"{item.quantity}x {veg_flag} "
            max_name = W - len(qty_label)
            name = str(item.menu_item.name)[:max_name]
            p.set(align="left", bold=True, double_height=large_font)
            p.text(f"{qty_label}{name}\n")
            p.set(align="left", bold=False, double_height=False)
            if item.notes:
                p.text(f"  *{str(item.notes)[:W-3]}\n")
            for mod in item.modifiers.all():
                p.text(f"  +{str(mod.name)[:W-3]}\n")

        p.text(self._sep() + "\n")

        if show_total:
            p.set(align="left", bold=True)
            p.text(self._two_col("Total", self._currency(kot_total)) + "\n")

        p.set(align="right", bold=False)
        p.text(f"{timezone.localtime(order.created_at).strftime('%d/%m %H:%M')}\n")

    # ------------------------------------------------------------------
    # BILL BODY  (shared by print_bill and print_bill_with_kots)
    # ------------------------------------------------------------------

    def _print_bill_body(self, p, order):
        W = self.W
        layout = bill_layout(order)
        profile = getattr(order.outlet, "print_profile", None)
        bill_margin = profile.bill_inner_margin if profile else 4

        # ── HEADER — centered, Font A ───────────────────────────────────
        p.set(align="center", bold=True,  font='a')
        p.text(f"{str(order.tenant.name)[:W]}\n")

        p.set(align="center", bold=False, font='a')
        p.text(f"{order.outlet.name}\n")

        if order.outlet.address:
            for chunk in self._wrap_text(str(order.outlet.address), W):
                p.text(f"{chunk}\n")
        if order.outlet.phone:
            p.text(f"Ph: {order.outlet.phone}\n")

        # Each compliance field on its own line
        if order.outlet.gst_no:
            p.text(f"GSTIN: {order.outlet.gst_no}\n")
        if order.outlet.fssai_no:
            p.text(f"FSSAI: {order.outlet.fssai_no}\n")
        sac = getattr(order.outlet, 'sac_code', None) or '996331'
        p.text(f"SAC: {sac}\n")

        p.text(self._sep() + "\n")

        C = W - bill_margin

        def tc(left, right):
            r = str(right)
            l = str(left)[:C - len(r)].ljust(C - len(r))
            return l + r

        sep_inner = "-" * C

        # ── TITLE — Tax Invoice / Bill of Supply / Bill ─────────────────
        self._print_title(p, layout, C)

        # ── INVOICE INFO — centered block, Font A ───────────────────────
        p.set(align="center", bold=False, font='a')
        p.text(tc("Bill No.", order.display_number) + "\n")
        # created_at is stored in UTC; the bill shows the outlet's local time
        p.text(tc("Date", timezone.localtime(order.created_at).strftime("%d/%m/%Y %H:%M")) + "\n")
        if order.table:
            p.text(tc("Table", order.table.name) + "\n")
        elif hasattr(order, 'token') and order.token:
            p.text(tc("Token", str(order.token.token_number)) + "\n")

        p.text(sep_inner + "\n")

        # ── ITEMS — centered block, Font A ──────────────────────────────
        p.set(align="center", bold=False, font='a')
        for line in layout.lines:
            for row in self._dish_rows(line, C):
                p.text(row + "\n")

        p.text(sep_inner + "\n")

        # ── MONEY — rows that add up to the total (bill_layout) ─────────
        p.set(align="center", bold=False, font='a')
        two = self._print_money(p, layout, C)

        payment = order.payments.order_by("-paid_at").first()
        if payment:
            p.text(two("Paid via", payment.method.upper()) + "\n")

        self._print_included(p, layout, two)

        p.text(sep_inner + "\n")

        # ── FOOTER — Font B, centered ────────────────────────────────────
        p.set(align="center", bold=False, font='b')
        p.text("Thank you for visiting!\n")
        p.text("Powered by Rasova POS\n")

    # ------------------------------------------------------------------
    # SPLIT BILL BY CATEGORY  (Counter Billing Mode)
    #
    # Summary slip → PARTIAL → Category slip → PARTIAL → ... → FULL CUT
    # Customer tears each section and takes it to the relevant counter.
    # ------------------------------------------------------------------

    def print_split_by_category(self, order) -> bool:
        p = self.get_printer()
        if not p:
            return False
        try:
            # Group non-voided items by category
            # Items with no category go into a fallback "General" group
            groups: dict = {}
            _UNCATEGORISED = "uncategorised"
            for item in order.items.exclude(status="voided").select_related(
                "menu_item__category"
            ).order_by("menu_item__category__name", "id"):
                cat = item.menu_item.category if item.menu_item else None
                key = cat.id if cat else _UNCATEGORISED
                if key not in groups:
                    groups[key] = {
                        "category": cat,  # may be None
                        "cat_name": cat.name if cat else "General",
                        "items": [],
                        "total": 0,
                    }
                groups[key]["items"].append(item)
                if not item.is_complimentary:   # a free dish costs nothing here either
                    groups[key]["total"] += item.total_price

            group_list = list(groups.values())
            if not group_list:
                return False

            # ── Summary slip ──────────────────────────────────────────
            self._print_summary_slip(p, order, group_list)

            # ── One slip per category ─────────────────────────────────
            for i, group in enumerate(group_list):
                p.cut(mode="PART")
                self._print_category_slip(p, order, group)

            p.cut(mode="FULL")
            return True

        except Exception as e:
            logger.error("Split bill print failed for order %s: %s", order.id, e)
            return False

    def _print_summary_slip(self, p, order, group_list):
        W = self.W
        layout = bill_layout(order)

        p.set(align="center", bold=True, double_width=True, double_height=True)
        p.text(f"{str(order.tenant.name)[:W//2]}\n")
        p.set(bold=False, double_width=False, double_height=False)
        p.text(f"{order.outlet.name}\n")
        if order.outlet.gst_no:
            p.text(f"GSTIN: {order.outlet.gst_no}\n")
        self._print_title(p, layout, W)

        p.text(self._sep() + "\n")
        p.set(align="left")

        # Token or order number
        if hasattr(order, "token") and order.token:
            p.set(bold=True, double_width=True, double_height=True)
            p.text(f"Token {order.token.display_number}\n")
            p.set(bold=False, double_width=False, double_height=False)
        p.text(f"Bill : {order.display_number}\n")
        p.text(f"Date : {timezone.localtime(order.created_at).strftime('%d/%m/%Y %H:%M')}\n")
        p.text(self._sep() + "\n")

        # FULL item list — same as a normal bill (customer needs this for records)
        for line in layout.lines:
            for row in self._dish_rows(line, W):
                p.text(row + "\n")
        p.text(self._sep() + "\n")

        two = self._print_money(p, layout, W, align="left", tall_total=True)

        payment = order.payments.order_by("-paid_at").first()
        if payment:
            p.text(two("Paid via", payment.method.upper()) + "\n")

        self._print_included(p, layout, two)

        p.text(self._sep() + "\n")
        p.set(align="center")
        p.text("Powered by Rasova\n")
        p.text("\n")

    def _print_category_slip(self, p, order, group):
        W = self.W
        cat_name = str(group["cat_name"]).upper()  # safe even if category is None

        # Big category name
        p.set(align="center", bold=True, double_width=True, double_height=True)
        p.text(f"{cat_name[:W//2]}\n")
        p.set(bold=False, double_width=False, double_height=False)

        # Token
        if hasattr(order, "token") and order.token:
            p.set(align="center", bold=True)
            p.text(f"Token {order.token.display_number}\n")
        p.set(align="left", bold=False)
        p.text(self._sep() + "\n")

        # Items
        for item in group["items"]:
            veg = "[V]" if item.menu_item.is_veg else "[N]"
            name = str(item.menu_item.name)[:W-10]
            p.set(bold=True)
            p.text(f"{item.quantity}x {veg} {name}\n")
            p.set(bold=False)
            if item.notes:
                p.text(f"   * {str(item.notes)[:W-5]}\n")

        p.text(self._sep() + "\n")
        p.set(bold=True)
        p.text(self._two_col(f"{cat_name[:W-10]} Total",
                             self._currency(group["total"])) + "\n")
        p.set(bold=False)
        p.set(align="center")
        p.text("Powered by Rasova\n")
        p.text("\n")

    # ------------------------------------------------------------------
    # KOT PRINT
    # ------------------------------------------------------------------

    def print_kot(self, order, kot_batch) -> bool:
        p = self.get_printer()
        if not p:
            return False
        try:
            self._print_kot_body(p, order, kot_batch)
            self._cut(p)
            return True
        except Exception as e:
            logger.error("KOT print failed for order %s: %s", order.id, e)
            return False

    # ------------------------------------------------------------------
    # BILL PRINT
    # ------------------------------------------------------------------

    def print_bill(self, order) -> bool:
        p = self.get_printer()
        if not p:
            return False
        try:
            self._print_bill_body(p, order)
            self._cut(p)
            return True
        except Exception as e:
            logger.error("Bill print failed for order %s: %s", order.id, e)
            return False

    # ------------------------------------------------------------------
    # QSR TOKEN RECEIPT BODY
    # Compact receipt for counter handoff — token number printed big.
    # ------------------------------------------------------------------

    def _print_qsr_token_body(self, p, order):
        W = self.W
        layout = bill_layout(order)

        # Restaurant name + compliance header
        p.set(align="center", bold=True, double_width=False, double_height=False)
        p.text(f"{str(order.tenant.name)[:W]}\n")
        p.set(bold=False)
        p.text(f"{order.outlet.name}\n")
        if order.outlet.gst_no:
            p.text(f"GSTIN: {order.outlet.gst_no}\n")
        sac = getattr(order.outlet, "sac_code", None) or "996331"
        p.text(f"SAC: {sac}\n")
        # At a QSR counter this receipt is the guest's bill: it says what
        # kind of bill it is and carries its number, like any other.
        self._print_title(p, layout, W)
        p.text(f"Bill No. {order.display_number}\n")
        p.text(self._sep() + "\n")

        # Token number — as large as the printer supports
        try:
            token_num = order.token.display_number if hasattr(order, "token") and order.token else None
        except Exception:
            token_num = None

        if token_num:
            p.set(align="center", bold=True, double_width=True, double_height=True)
            p.text(f"TOKEN {token_num}\n")
        else:
            p.set(align="center", bold=True, double_width=True, double_height=True)
            p.text(f"#{order.order_number or order.id}\n")

        p.set(align="left", bold=False, double_width=False, double_height=False)
        p.text(self._sep() + "\n")

        # Items: a long name wraps instead of running past the paper edge
        for line in layout.lines:
            for row in self._dish_rows(line, W):
                p.text(row + "\n")

        p.text(self._sep() + "\n")

        two = self._print_money(p, layout, W, align="left")

        payment = order.payments.order_by("-paid_at").first()
        if payment:
            p.text(two("Paid", payment.method.upper()) + "\n")

        self._print_included(p, layout, two)

        p.set(align="right")
        p.text(f"{timezone.localtime(order.created_at).strftime('%d/%m %H:%M')}\n")

    # ------------------------------------------------------------------
    # TOKEN RECEIPT ONLY  (QSR when KOTs already printed at stations)
    # ------------------------------------------------------------------

    def print_token_receipt(self, order) -> bool:
        p = self.get_printer()
        if not p:
            return False
        try:
            self._print_qsr_token_body(p, order)
            self._cut(p)
            return True
        except Exception as e:
            logger.error("Token receipt print failed for order %s: %s", order.id, e)
            return False

    # ------------------------------------------------------------------
    # COMBINED PRINT — three modes
    #
    # strip_mode=True (QSR, no station printers):
    #   token receipt → PARTIAL → KOT 1 → PARTIAL → KOT N → FULL CUT
    #   Customer carries the full strip to the food counter.
    #
    # cashier_strip=True (hotel / fine dining, one cashier printer):
    #   full bill → PARTIAL → KOT 1 [Station A] → PARTIAL → KOT N → FULL CUT
    #   Runner delivers the strip, tears off each section at each station.
    #
    # neither (fine dining with per-station printers):
    #   full bill → FULL CUT  (KOTs already printed at station printers)
    # ------------------------------------------------------------------

    def print_bill_with_kots(self, order, kots, strip_mode=False, cashier_strip=False) -> bool:
        p = self.get_printer()
        if not p:
            return False
        try:
            # ── Bill / receipt section ─────────────────────────────────
            if strip_mode:
                self._print_qsr_token_body(p, order)
            else:
                self._print_bill_body(p, order)

            # ── Cut after bill ─────────────────────────────────────────
            if kots and (strip_mode or cashier_strip):
                p.cut(mode="PART")   # stay connected to first KOT
            else:
                p.cut(mode="FULL")   # bill tears off; no KOTs on this printer
                return True          # nothing more to print

            # ── KOT sections ──────────────────────────────────────────
            for i, kot in enumerate(kots):
                self._print_kot_body(p, order, kot)
                if i == len(kots) - 1:
                    p.cut(mode="FULL")   # final tear — entire strip off the roll
                else:
                    p.cut(mode="PART")   # stay connected to next section

            return True
        except Exception as e:
            logger.error("Combined bill+KOT print failed for order %s: %s", order.id, e)
            return False

    # ------------------------------------------------------------------
    # TEST PRINT — run this to verify alignment before going live
    # ------------------------------------------------------------------

    def test_print(self, station_name="Test Station") -> bool:
        """
        Prints a full alignment test page. Run this after changing any
        printer setting to confirm the output looks right before a real order.
        """
        p = self.get_printer()
        if not p:
            return False
        try:
            W = self.W
            p.set(align="center", bold=True, double_width=True, double_height=True)
            p.text("RASOVA POS\n")
            p.set(bold=False, double_width=False, double_height=False)
            p.text("-- TEST PRINT --\n")
            p.text("-" * W + "\n")

            p.set(align="left")
            p.text(f"Station : {station_name[:W-10]}\n")
            p.text(f"Paper   : {self.W} chars/line\n")
            p.text(f"Cut     : {self.cut_type}\n")
            p.text(f"Encoding: {self.encoding}\n")
            p.text("-" * W + "\n")

            # Ruler — shows if chars per line matches physical paper
            ruler_top = "".join(str((i + 1) % 10) for i in range(W))
            ruler_bot = "".join(str(((i + 1) // 10) % 10) for i in range(W))
            p.text(ruler_top + "\n")
            p.text(ruler_bot + "\n")
            p.text("-" * W + "\n")

            # Sample KOT block
            p.set(bold=True, double_width=True, double_height=True)
            p.text("KOT #1\n")
            p.set(bold=True, double_width=False, double_height=False)
            p.text("Token: 42\n")
            p.set(bold=False)
            p.text("-" * W + "\n")
            p.set(bold=True)
            p.text(f"2x  {'Chicken Burger'[:W-4]}\n")
            p.set(bold=False)
            p.text("   * Extra spicy\n")
            p.set(bold=True)
            p.text(f"1x  {'Masala Chai'[:W-4]}\n")
            p.set(bold=False)
            p.text("-" * W + "\n")

            # Sample bill block
            name_w = W - 10
            p.set(bold=True)
            p.text(f"{'Item':<{name_w}} {'Qty':>3} {'Amt':>5}\n")
            p.set(bold=False)
            p.text("-" * W + "\n")
            p.text(f"{'Chicken Burger'[:name_w]:<{name_w}} {'2':>3} {'360':>5}\n")
            p.text(f"{'Masala Chai'[:name_w]:<{name_w}} {'1':>3} {'40':>5}\n")
            p.text("-" * W + "\n")
            p.text(self._two_col("Subtotal", "Rs.400") + "\n")
            p.text(self._two_col("GST 5%", "Rs.20") + "\n")
            p.text("-" * W + "\n")
            p.set(bold=True, double_height=True)
            p.text(self._two_col("TOTAL", "Rs.420") + "\n")
            p.set(bold=False, double_height=False)
            p.text("-" * W + "\n")
            p.set(align="center")
            p.text("If this looks correct, you are ready!\n")
            p.text("\n\n")

            self._cut(p)
            return True
        except Exception as e:
            logger.error("Test print failed: %s", e)
            return False
