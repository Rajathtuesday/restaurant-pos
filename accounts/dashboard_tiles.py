"""
What the owner's dashboard shows, worked out in one place (V6, 13 Nov 2026).

The dashboard used to decide each tile in the template, in some forty
nested {% if %} blocks, and it opened on a wall of identical tiles with
"Today's Performance" at the bottom. Now:

  today_blocks()     what an owner opens the page for, first: how much
                     today, what is open right now, and anything that
                     needs a look (voids, discounts, low stock)
  dashboard_tiles()  the tiles, grouped by job: Service, Back office, and
                     Rasova staff (your own tools: superuser only; a reseller
                     sees Partner)

Both are plain data, so who sees what is tested without a page
(accounts/tests/test_dashboard.py).
"""
from dataclasses import dataclass, field

from django.urls import reverse

from core.money import rupees


@dataclass
class Tile:
    label: str
    hint: str
    icon: str
    href: str = ""
    action: str = ""          # a data-action instead of a link (New Order)
    badge: int = 0
    accent: bool = False


@dataclass
class TileGroup:
    key: str
    title: str
    note: str
    tiles: list = field(default_factory=list)


def dashboard_tiles(user, features, *, direct_billing_mode=False, active_token_count=0):
    """The tile groups for this user. `features` is the restaurant's
    feature set (core.features), as the templates see it."""
    has = features.__contains__
    cashier = user.role == "cashier"
    manager = user.role == "manager"

    service = TileGroup("service", "Service", "Front of house")
    add = service.tiles.append
    if has("floor_plan"):
        add(Tile("Tables", "Live floor plan", "grid-3x3-gap", reverse("table-dashboard")))
    if has("token_system"):
        if direct_billing_mode:
            add(Tile("New Order", "Tap to start billing", "receipt-cutoff", action="dashboard.new-order",
                     badge=active_token_count, accent=True))
        else:
            add(Tile("Tokens", "Counter queue", "receipt-cutoff", reverse("token-dashboard"),
                     badge=active_token_count, accent=True))
        if not cashier:
            add(Tile("Token Board", "All active", "grid-3x3-gap", reverse("token-dashboard")))
    if has("running_order") or has("token_system"):
        add(Tile("Live Orders", "Every open order", "card-checklist", reverse("live-orders")))
    if has("cover_charge"):
        add(Tile("Covers", "Sell entry at the door", "ticket-perforated", reverse("covers")))
    if has("kitchen_display"):
        add(Tile("Kitchen", "Order queue", "fire", reverse("kitchen-view")))
    if not cashier or has("floor_plan"):
        add(Tile("Billing", "POS terminal", "receipt",
                 reverse("billing-view") if has("floor_plan") else reverse("token-dashboard")))
    if has("waiter_call"):
        add(Tile("Calls", "Guests asking for staff", "bell", reverse("waiter-calls")))

    groups = [service]
    if cashier:
        return groups

    office = TileGroup("office", "Back office", "Money, menu, stock and people")
    add = office.tiles.append
    if has("reports"):
        add(Tile("Reports", "Sales and data", "graph-up-arrow", reverse("reports_dashboard"), accent=True))
    # The cash register is a payment prerequisite (fine dining refuses a
    # payment with no open session), so it is never behind shift_management.
    add(Tile("Cash Register", "Open and end of day", "cash-stack", reverse("cash-session-list")))
    add(Tile("History", "All orders", "clock-history", reverse("order-history")))
    add(Tile("Menu", "Dishes and prices", "journal-text", reverse("menu_management")))
    if has("qr_menu"):
        add(Tile("QR Menu", "What guests see", "qr-code-scan", reverse("menu_qr")))
        add(Tile("Table QR Codes", "Print and display board", "qr-code", reverse("setup_qr_codes")))
    if has("inventory"):
        add(Tile("Inventory", "Stock levels", "box-seam", reverse("inventory_board")))
    if has("shift_management"):
        add(Tile("Shifts", "Who is on", "person-badge", reverse("shift-dashboard")))
    if has("reservations"):
        add(Tile("Reservations", "Table bookings", "calendar-check", reverse("reservation-list")))
    if has("crm"):
        add(Tile("Guests", "Guest history", "people", reverse("crm-dashboard")))
    add(Tile("Setup", "Settings and staff", "sliders", reverse("setup_wizard")))
    groups.append(office)

    staff = TileGroup("staff", "Rasova staff", "Only you see these")
    if not manager and (user.is_superuser or user.role == "agent"):
        staff.tiles.append(Tile("Partner", "Resellers and clients", "briefcase", "/sales/"))
    if not manager and user.is_superuser:
        staff.tiles.append(Tile("Features", "Platform flags", "toggles", reverse("feature_flags")))
        staff.tiles.append(Tile("Admin", "Django backend", "shield-lock", reverse("admin:index")))
    if staff.tiles:
        groups.append(staff)
    return groups


def needs_a_look(metric, audit=True):
    """What in today's numbers deserves the owner's attention, in words:
    [(level, words, where)]. Empty means nothing does. `audit`: whether the
    Discount/Void Audit opens for this person; where it does not, voids and
    discounts are words with nowhere to go ("" for where), never a link to a
    403 (as they were for every restaurant without advanced_reports until
    26 Nov 2026)."""
    out = []
    audit_url = reverse("audit_report") if audit else ""
    if metric.get("voids_today"):
        n = metric["voids_today"]
        out.append(("alert", f"{n} void{'s' if n != 1 else ''} today", audit_url))
    if metric.get("discounts_amount"):
        out.append(("warn", f"{rupees(metric['discounts_amount'])} given in discounts", audit_url))
    if metric.get("low_stock"):
        n = metric["low_stock"]
        out.append(("alert", f"{n} ingredient{'s' if n != 1 else ''} low on stock", reverse("inventory_board")))
    return out


def today_blocks(metrics, audit=True):
    """One block per outlet, ready to show: the numbers as words, and what
    needs a look. The page draws it, and the minute's refresh
    (dashboard_metrics_json) sends the same, so the screen never works
    out a rule of its own."""
    return [{
        "outlet": m["outlet"],
        "revenue": rupees(m["revenue"]),
        "orders": m["orders"],
        "avg": rupees(round(m["avg_order_value"] or 0)),
        "tables": m["active_tables"],
        "kitchen": m["kitchen_orders"],
        "alerts": [{"level": level, "words": words, "href": href} for level, words, href in needs_a_look(m, audit)],
    } for m in metrics]
