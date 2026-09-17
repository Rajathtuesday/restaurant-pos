# orders/scripts/demo_seed.py
"""
Creates (or resets) the public-facing "Demo Bistro" tenant -- the one a
prospect lands in via the /live-demo/ magic link, not the DEBUG-only
/demo/ tenant-switcher in core/views.py (a completely separate, existing
tool for local development, left untouched).

Idempotent by design: safe to run once by hand to set the tenant up, and
safe to run again on a schedule to wipe out whatever a visitor did and
hand the next one a fresh-looking floor plan. Never deletes and recreates
the tenant/outlet/owner/tables/menu themselves (those are stable, get_or_create
only) -- only the transactional data (orders/items/payments) actually gets
wiped and reseeded every run, since that's the only part a demo visitor can
mess up.
"""
import random
from decimal import Decimal

from django.utils import timezone
from datetime import timedelta

from tenants.models import Tenant, TenantFeatureOverride
from accounts.models import User
from orders.models import Table, Order, OrderItem, Payment
from menu.models import MenuCategory, MenuItem
from setup.models import PaymentConfig
from inventory.models import InventoryItem

DEMO_TENANT_NAME = "Demo Bistro"
DEMO_TENANT_SLUG = "demo-bistro"
DEMO_OWNER_USERNAME = "demo_owner"

# Generic, deliberately not a real client's actual menu or branding -- this
# is shown to strangers on the open internet.
_MENU = {
    "Starters": [
        ("Paneer Tikka", "220", True),
        ("Chicken 65", "260", False),
        ("Veg Spring Rolls", "180", True),
        ("Chilli Garlic Mushroom", "210", True),
    ],
    "Mains": [
        ("Butter Chicken", "340", False),
        ("Dal Makhani", "240", True),
        ("Paneer Butter Masala", "280", True),
        ("Mutton Rogan Josh", "420", False),
        ("Veg Biryani", "260", True),
    ],
    "Breads": [
        ("Butter Naan", "60", True),
        ("Garlic Naan", "70", True),
        ("Tandoori Roti", "40", True),
    ],
    "Desserts": [
        ("Gulab Jamun", "110", True),
        ("Rasmalai", "130", True),
    ],
    "Beverages": [
        ("Masala Chai", "50", True),
        ("Fresh Lime Soda", "90", True),
        ("Mango Lassi", "120", True),
    ],
}

_TABLE_NAMES = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]

# (name, category, unit, stock, low_stock_threshold, reorder_quantity, cost_price)
# A few items are deliberately seeded BELOW their threshold so the low-stock
# alert on the dashboard has something real to show a visitor, not an
# always-empty list.
_INVENTORY = [
    ("Paneer",                "Dairy",      "kg", Decimal("2.5"), Decimal("5"),  Decimal("10"), Decimal("320")),  # low
    ("Chicken",                "Meat",       "kg", Decimal("18"),  Decimal("8"),  Decimal("15"), Decimal("220")),
    ("Basmati Rice",           "Dry Store",  "kg", Decimal("40"),  Decimal("15"), Decimal("25"), Decimal("90")),
    ("Onions",                 "Veggies",    "kg", Decimal("12"),  Decimal("10"), Decimal("20"), Decimal("35")),
    ("Tomatoes",               "Veggies",    "kg", Decimal("3"),   Decimal("8"),  Decimal("15"), Decimal("40")),   # low
    ("Milk",                   "Dairy",      "l",  Decimal("22"),  Decimal("10"), Decimal("20"), Decimal("60")),
    ("Refined Flour (Maida)",  "Dry Store",  "kg", Decimal("25"),  Decimal("10"), Decimal("20"), Decimal("45")),
    ("Cooking Oil",            "Dry Store",  "l",  Decimal("15"),  Decimal("8"),  Decimal("15"), Decimal("140")),
    ("Sugar",                  "Dry Store",  "kg", Decimal("18"),  Decimal("6"),  Decimal("12"), Decimal("48")),
    ("Garam Masala",           "Spices",     "kg", Decimal("0.6"), Decimal("1"),  Decimal("2"),  Decimal("650")),  # low
]

_HISTORY_DAYS = 14
_PEAK_HOURS = [12, 13, 14, 19, 20, 21]
_PAYMENT_METHODS = ["cash", "upi", "card"]


def _ensure_tenant_and_owner():
    tenant, _ = Tenant.objects.get_or_create(
        name=DEMO_TENANT_NAME,
        defaults={"tenant_type": "fine_dining", "slug": DEMO_TENANT_SLUG},
    )
    # slug is set at creation via defaults above; if the tenant already
    # existed without one (e.g. created before this field mattered here),
    # backfill it so subdomain redirects after demo login work correctly.
    if not tenant.slug:
        tenant.slug = DEMO_TENANT_SLUG
        tenant.save(update_fields=["slug"])

    outlet = tenant.outlets.first()
    if outlet is None:
        outlet = tenant.outlets.create(name="Demo Bistro - Main")

    owner, created = User.objects.get_or_create(
        username=DEMO_OWNER_USERNAME,
        defaults={"role": "owner", "tenant": tenant, "outlet": outlet},
    )
    if created:
        # Never reachable via the normal password login form -- this
        # account only ever gets a session via the /live-demo/ magic link,
        # which logs it in directly without a password at all.
        owner.set_unusable_password()
        owner.save(update_fields=["password"])
    elif owner.tenant_id != tenant.id or owner.outlet_id != outlet.id:
        owner.tenant = tenant
        owner.outlet = outlet
        owner.save(update_fields=["tenant", "outlet"])

    return tenant, outlet, owner


def _ensure_feature_overrides(tenant):
    # ai_menu_import is ON by default for fine_dining -- explicitly off
    # here so a curious visitor can't spend the shared Gemini free-tier
    # quota (20 requests/day total, shared across every real tenant) on a
    # demo account. razorpay_gateway is already off-by-default for every
    # tenant unless explicitly turned on, so no override needed there --
    # this demo tenant simply never gets one.
    TenantFeatureOverride.objects.update_or_create(
        tenant=tenant, feature="ai_menu_import",
        defaults={"enabled": False, "notes": "Public demo tenant -- shared AI quota protection"},
    )


def _ensure_payment_config(tenant, outlet):
    config, _ = PaymentConfig.for_outlet(outlet, tenant)
    config.cash_enabled = True
    config.upi_enabled = True
    config.card_enabled = True
    # Deliberately no upi_id set -- a demo tenant showing a real UPI QR
    # would be showing a payment address to strangers on the open internet
    # for money that was never actually meant to be collected.
    config.upi_id = ""
    config.save(update_fields=["cash_enabled", "upi_enabled", "card_enabled", "upi_id"])


def _ensure_tables(tenant, outlet):
    tables = []
    for name in _TABLE_NAMES:
        table, _ = Table.objects.get_or_create(tenant=tenant, outlet=outlet, name=name)
        tables.append(table)
    return tables


def _ensure_menu(tenant, outlet):
    items_by_name = {}
    for cat_name, items in _MENU.items():
        category, _ = MenuCategory.objects.get_or_create(tenant=tenant, outlet=outlet, name=cat_name)
        for name, price, is_veg in items:
            item, _ = MenuItem.objects.get_or_create(
                tenant=tenant, outlet=outlet, category=category, name=name,
                defaults={"price": Decimal(price), "gst_percentage": Decimal("5"), "is_veg": is_veg},
            )
            items_by_name[name] = item
    return items_by_name


def _ensure_inventory(tenant, outlet):
    """
    Force-resets stock levels to the baseline above on every run, same
    philosophy as orders: whatever a visitor changed (a manual stock edit,
    a wastage entry) shouldn't linger for the next one.
    """
    for name, category, unit, stock, threshold, reorder_qty, cost in _INVENTORY:
        InventoryItem.objects.update_or_create(
            tenant=tenant, outlet=outlet, name=name,
            defaults={
                "category": category,
                "unit": unit,
                "stock": stock,
                "low_stock_threshold": threshold,
                "reorder_quantity": reorder_qty,
                "cost_price": cost,
            },
        )


def _seed_order_history(tenant, outlet, items_by_name, owner):
    """
    Backdated, already-paid orders across the last two weeks, so Reports/
    Dashboard have a real trend to plot instead of a single flat day.

    daily_sales() derives revenue from Payment.paid_at, not Order.created_at
    (see reports/services/sales_reports.py), so both get backdated together
    to the same historical moment after creation -- created_at/paid_at are
    auto_now_add, which only takes effect at INSERT time, so this has to be
    a separate .update() call rather than passed into .create().

    A fixed random seed keeps the generated history looking the same shape
    on every reset, rather than reshuffling into a different-looking demo
    every 2 hours for no reason.
    """
    rng = random.Random(42)
    all_items = list(items_by_name.values())
    now = timezone.now()

    for day_offset in range(_HISTORY_DAYS, -1, -1):
        day = now - timedelta(days=day_offset)
        for _ in range(rng.randint(4, 9)):
            order_time = day.replace(
                hour=rng.choice(_PEAK_HOURS), minute=rng.randint(0, 59),
                second=0, microsecond=0,
            )
            if order_time > now:
                order_time = now

            order = Order.objects.create(
                tenant=tenant, outlet=outlet, created_by=owner,
                status="closed",
                source=rng.choice(["dine_in", "dine_in", "dine_in", "takeaway"]),
            )

            for menu_item in rng.sample(all_items, k=rng.randint(2, 4)):
                qty = rng.randint(1, 3)
                OrderItem.objects.create(
                    order=order, menu_item=menu_item, quantity=qty,
                    price=menu_item.price, gst_percentage=Decimal("5"),
                    total_price=menu_item.price * qty, status="served",
                )
            order.recalculate_totals()

            payment = Payment.objects.create(
                order=order, method=rng.choice(_PAYMENT_METHODS),
                amount=order.grand_total, created_by=owner,
            )

            Order.objects.filter(pk=order.pk).update(created_at=order_time, closed_at=order_time)
            Payment.objects.filter(pk=payment.pk).update(paid_at=order_time)


def _clear_transactional_data(tenant):
    # Cascades to OrderItem/OrderEvent through their own FKs, but NOT to
    # Payment -- Payment.order is on_delete=PROTECT (a real production
    # safeguard against ever losing a financial record via an unrelated
    # cascade), so any order a demo visitor actually paid raised
    # ProtectedError here and silently broke every reset after it,
    # including the scheduled one every 2 hours. Payments must go first.
    Payment.objects.filter(order__tenant=tenant).delete()
    Order.objects.filter(tenant=tenant).delete()


def _seed_sample_orders(tenant, outlet, tables, items_by_name, owner):
    # Table 1: an order already open mid-service, so the floor plan never
    # looks like an empty shell the moment someone lands on it.
    order1 = Order.objects.create(
        tenant=tenant, outlet=outlet, table=tables[0], created_by=owner,
        status="open", source="dine_in",
    )
    OrderItem.objects.create(
        order=order1, menu_item=items_by_name["Paneer Tikka"], quantity=1,
        price=items_by_name["Paneer Tikka"].price, gst_percentage=Decimal("5"),
        total_price=items_by_name["Paneer Tikka"].price, status="served",
    )
    OrderItem.objects.create(
        order=order1, menu_item=items_by_name["Butter Naan"], quantity=2,
        price=items_by_name["Butter Naan"].price, gst_percentage=Decimal("5"),
        total_price=items_by_name["Butter Naan"].price * 2, status="preparing",
    )
    order1.recalculate_totals()

    # Table 3: a second table, further along, so the demo shows more than
    # one state at a glance.
    order2 = Order.objects.create(
        tenant=tenant, outlet=outlet, table=tables[2], created_by=owner,
        status="open", source="dine_in",
    )
    OrderItem.objects.create(
        order=order2, menu_item=items_by_name["Butter Chicken"], quantity=1,
        price=items_by_name["Butter Chicken"].price, gst_percentage=Decimal("5"),
        total_price=items_by_name["Butter Chicken"].price, status="served",
    )
    OrderItem.objects.create(
        order=order2, menu_item=items_by_name["Mango Lassi"], quantity=2,
        price=items_by_name["Mango Lassi"].price, gst_percentage=Decimal("5"),
        total_price=items_by_name["Mango Lassi"].price * 2, status="served",
    )
    order2.recalculate_totals()


def create_or_reset_demo_tenant():
    """
    Safe to call any number of times: sets the demo tenant up if it doesn't
    exist yet, and always wipes + reseeds the transactional (order) data so
    a visitor never inherits a mess left by an earlier one.

    Returns the Tenant, for callers that want it (e.g. a management command
    printing a confirmation).
    """
    tenant, outlet, owner = _ensure_tenant_and_owner()
    _ensure_feature_overrides(tenant)
    _ensure_payment_config(tenant, outlet)
    tables = _ensure_tables(tenant, outlet)
    items_by_name = _ensure_menu(tenant, outlet)
    _ensure_inventory(tenant, outlet)

    _clear_transactional_data(tenant)
    _seed_sample_orders(tenant, outlet, tables, items_by_name, owner)
    _seed_order_history(tenant, outlet, items_by_name, owner)

    return tenant
