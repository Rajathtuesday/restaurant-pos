# orders/services/printing_service.py
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
                # 10-second timeout. Default is 60s — a stuck printer would
                # hold a Celery worker thread for a full minute, stacking up
                # the entire print queue behind one jammed connection.
                return Network(self.host, port=self.port, timeout=10)
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
        """₹ doesn't exist in CP437 — always use Rs. for safety."""
        return f"Rs.{float(amount):.0f}"

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

    # ------------------------------------------------------------------
    # KOT BODY  (shared by print_kot and print_bill_with_kots)
    # ------------------------------------------------------------------

    def _print_kot_body(self, p, order, kot_batch):
        W = self.W
        p.set(align="center", bold=True, double_width=True, double_height=True)
        p.text(f"KOT #{kot_batch.kot_number}\n")

        p.set(align="center", bold=True, double_width=False, double_height=False)
        if hasattr(order, "token") and order.token:
            p.text(f"Token: {order.token.token_number}\n")
        else:
            table_name = order.table.name if order.table else "Walk-in"
            p.text(f"Table: {table_name}\n")

        station_name = kot_batch.station.name if kot_batch.station else ""
        if station_name:
            p.text(f"[{station_name}]\n")

        p.set(align="left", bold=False)
        p.text(self._sep() + "\n")

        for item in kot_batch.items.select_related("menu_item").all():
            veg_flag = "[V]" if item.menu_item.is_veg else "[N]"
            qty_label = f"{item.quantity}x  {veg_flag} "
            item_name = str(item.menu_item.name)
            max_name = W - len(qty_label)
            p.set(bold=True)
            p.text(f"{qty_label}{item_name[:max_name]}\n")
            p.set(bold=False)
            if item.notes:
                p.text(f"   * {str(item.notes)[:W-5]}\n")
            for mod in item.modifiers.all():
                p.text(f"   + {str(mod.name)[:W-5]}\n")

        p.text(self._sep() + "\n")
        p.set(align="right")
        p.text(f"{order.created_at.strftime('%d/%m %H:%M')}\n")
        p.text("\n")

    # ------------------------------------------------------------------
    # BILL BODY  (shared by print_bill and print_bill_with_kots)
    # ------------------------------------------------------------------

    def _print_bill_body(self, p, order):
        W = self.W
        p.set(align="center", bold=True, double_width=True, double_height=True)
        tenant_name = str(order.tenant.name)[:W // 2]
        p.text(f"{tenant_name}\n")

        p.set(align="center", bold=False, double_width=False, double_height=False)
        p.text(f"{order.outlet.name}\n")
        if order.outlet.address:
            p.text(f"{str(order.outlet.address)[:W]}\n")
        if order.outlet.phone:
            p.text(f"Ph: {order.outlet.phone}\n")
        if order.outlet.gst_no:
            p.text(f"GSTIN: {order.outlet.gst_no}\n")
        if order.outlet.fssai_no:
            p.text(f"FSSAI: {order.outlet.fssai_no}\n")
        sac = getattr(order.outlet, "sac_code", None) or "996331"
        p.text(f"SAC: {sac}\n")
        p.text(self._sep() + "\n")

        p.set(align="left")
        p.text(f"Bill : {order.order_number or order.id}\n")
        if hasattr(order, "token") and order.token:
            p.text(f"Token: {order.token.token_number}\n")
        elif order.table:
            p.text(f"Table: {order.table.name}\n")
        p.text(f"Date : {order.created_at.strftime('%d/%m/%Y %H:%M')}\n")
        p.text(self._sep() + "\n")

        name_w = W - 10
        p.set(bold=True)
        p.text(f"{'Item':<{name_w}} {'Qty':>3} {'Amt':>5}\n")
        p.set(bold=False)
        p.text(self._sep() + "\n")

        for item in order.items.exclude(status="voided").select_related("menu_item"):
            name = str(item.menu_item.name)[:name_w]
            qty = str(item.quantity)
            amt = f"{float(item.total_price):.0f}"
            p.text(f"{name:<{name_w}} {qty:>3} {amt:>5}\n")

        gst_inclusive = getattr(order.outlet, "gst_inclusive", False)

        p.text(self._sep() + "\n")
        p.set(align="left")
        if gst_inclusive:
            p.text(self._two_col("Subtotal (excl.GST)", self._currency(order.subtotal)) + "\n")
        else:
            p.text(self._two_col("Subtotal", self._currency(order.subtotal)) + "\n")
        p.text(self._two_col("GST" + (" (incl.)" if gst_inclusive else ""), self._currency(order.gst_total)) + "\n")
        if order.discount_total > 0:
            p.text(self._two_col("Discount", f"-{self._currency(order.discount_total)}") + "\n")
        if getattr(order, "parcel_surcharge", 0) and order.parcel_surcharge > 0:
            p.text(self._two_col("Parcel Charge", self._currency(order.parcel_surcharge)) + "\n")

        p.text(self._sep() + "\n")
        p.set(bold=True, double_height=True)
        p.text(self._two_col("TOTAL", self._currency(order.grand_total)) + "\n")
        p.set(bold=False, double_height=False)

        payment = order.payments.order_by("-paid_at").first()
        if payment:
            p.text(self._two_col("Paid via", payment.method.upper()) + "\n")

        if gst_inclusive:
            p.text(self._two_col("", "(prices incl. GST)") + "\n")

        p.text(self._sep() + "\n")
        p.set(align="center")
        p.text("Thank you for your visit!\n")
        p.text("Powered by Rasova POS\n")
        p.text("\n\n")

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
            ).order_by("menu_item__category__name"):
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
        is_comp = getattr(order.outlet, "is_composition_scheme", False)

        p.set(align="center", bold=True, double_width=True, double_height=True)
        p.text(f"{str(order.tenant.name)[:W//2]}\n")
        p.set(bold=False, double_width=False, double_height=False)
        p.text(f"{order.outlet.name}\n")
        if order.outlet.gst_no:
            p.text(f"GSTIN: {order.outlet.gst_no}\n")
        if is_comp:
            p.set(bold=True)
            p.text("BILL OF SUPPLY\n")
            p.set(bold=False)

        p.text(self._sep() + "\n")
        p.set(align="left")

        # Token or order number
        if hasattr(order, "token") and order.token:
            p.set(bold=True, double_width=True, double_height=True)
            p.text(f"Token {order.token.display_number}\n")
            p.set(bold=False, double_width=False, double_height=False)
        else:
            p.text(f"Bill : {order.order_number or order.id}\n")
        p.text(f"Date : {order.created_at.strftime('%d/%m/%Y %H:%M')}\n")
        p.text(self._sep() + "\n")

        # Section totals — use cat_name (safe even if category is None)
        p.set(bold=True)
        p.text(f"{'Section':<{W-8}} {'Total':>6}\n")
        p.set(bold=False)
        p.text(self._sep() + "\n")
        for group in group_list:
            name = str(group["cat_name"])[:W-8]
            p.text(f"{name:<{W-8}} {self._currency(group['total']):>6}\n")

        p.text(self._sep() + "\n")
        p.set(bold=True, double_height=True)
        p.text(self._two_col("TOTAL", self._currency(order.grand_total)) + "\n")
        p.set(bold=False, double_height=False)

        payment = order.payments.order_by("-paid_at").first()
        if payment:
            p.text(self._two_col("Paid via", payment.method.upper()) + "\n")
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

        # Restaurant name + compliance header
        p.set(align="center", bold=True, double_width=False, double_height=False)
        p.text(f"{str(order.tenant.name)[:W]}\n")
        p.set(bold=False)
        p.text(f"{order.outlet.name}\n")
        if order.outlet.gst_no:
            p.text(f"GSTIN: {order.outlet.gst_no}\n")
        sac = getattr(order.outlet, "sac_code", None) or "996331"
        p.text(f"SAC: {sac}\n")
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

        # Items
        name_w = W - 8
        for item in order.items.exclude(status="voided").select_related("menu_item"):
            name = str(item.menu_item.name)[:name_w]
            amt  = f"{float(item.total_price):.0f}"
            p.text(f"{item.quantity}x {name:<{name_w - 2}} {amt:>5}\n")

        p.text(self._sep() + "\n")

        # Totals
        p.set(bold=True)
        p.text(self._two_col("TOTAL", self._currency(order.grand_total)) + "\n")
        p.set(bold=False)

        payment = order.payments.order_by("-paid_at").first()
        if payment:
            p.text(self._two_col("Paid", payment.method.upper()) + "\n")

        p.set(align="right")
        p.text(f"{order.created_at.strftime('%d/%m %H:%M')}\n")
        p.text("\n")

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
            p.text(f"   * Extra spicy\n")
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
