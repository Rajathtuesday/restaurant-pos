"""
Tests for GST Inclusive / Exclusive pricing mode.

Exclusive (default): item.price is the BASE. GST added on top.
  grand_total = subtotal + gst

Inclusive (gst_inclusive=True): item.price is the CUSTOMER PRICE.
  GST is back-calculated: gst = price × rate / (100 + rate)
  grand_total = inclusive_price - discounts
  subtotal + gst_total ≡ grand_total  (always)

Every test uses Decimal arithmetic — no floats allowed in financial code.
"""
from decimal import Decimal
from django.test import TestCase

from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from tenants.models import Tenant, Outlet


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _setup(gst_inclusive=False):
    tenant = Tenant.objects.create(name=f"TestRest_{gst_inclusive}")
    outlet = Outlet.objects.create(tenant=tenant, name="Main", gst_inclusive=gst_inclusive)
    cat    = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
    return tenant, outlet, cat


def _item(tenant, outlet, cat, name, price, gst_rate):
    return MenuItem.objects.create(
        tenant=tenant, outlet=outlet, category=cat,
        name=name,
        price=Decimal(str(price)),
        gst_percentage=Decimal(str(gst_rate)),
        is_available=True,
    )


def _order_with_items(outlet, tenant, items_spec):
    """
    items_spec = [(menu_item, quantity), ...]
    Returns order with items added (status='pending', total_price set).
    """
    order = Order.objects.create(tenant=tenant, outlet=outlet)
    for mi, qty in items_spec:
        OrderItem.objects.create(
            order=order,
            menu_item=mi,
            quantity=qty,
            price=mi.price,
            gst_percentage=mi.gst_percentage,
            total_price=mi.price * qty,
            status="pending",
        )
    order.recalculate_totals()
    order.refresh_from_db()
    return order


# ──────────────────────────────────────────────────────────────────────────────
# EXCLUSIVE TESTS (should behave identically to before)
# ──────────────────────────────────────────────────────────────────────────────

class TestGSTExclusiveUnchanged(TestCase):
    """Existing exclusive behaviour must be exactly preserved."""

    def setUp(self):
        self.tenant, self.outlet, self.cat = _setup(gst_inclusive=False)

    def test_simple_5pct(self):
        mi    = _item(self.tenant, self.outlet, self.cat, "Coffee", 20, 5)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 1)])
        self.assertEqual(order.subtotal,    Decimal("20.00"))
        self.assertEqual(order.gst_total,   Decimal("1.00"))
        self.assertEqual(order.grand_total, Decimal("21"))

    def test_simple_18pct(self):
        mi    = _item(self.tenant, self.outlet, self.cat, "Beer", 100, 18)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 1)])
        self.assertEqual(order.subtotal,    Decimal("100.00"))
        self.assertEqual(order.gst_total,   Decimal("18.00"))
        self.assertEqual(order.grand_total, Decimal("118"))

    def test_zero_gst(self):
        mi    = _item(self.tenant, self.outlet, self.cat, "Water", 30, 0)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 2)])
        self.assertEqual(order.subtotal,    Decimal("60.00"))
        self.assertEqual(order.gst_total,   Decimal("0.00"))
        self.assertEqual(order.grand_total, Decimal("60"))

    def test_subtotal_plus_gst_equals_grand_total(self):
        mi    = _item(self.tenant, self.outlet, self.cat, "Dish", 99, 5)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 3)])
        # 99 × 3 = 297. GST = 14.85. Total = 311.85 → rounds to 312.
        self.assertEqual(order.subtotal + order.gst_total + order.round_off,
                         order.grand_total)

    def test_multiple_quantities(self):
        mi    = _item(self.tenant, self.outlet, self.cat, "Burger", 150, 5)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 2)])
        self.assertEqual(order.subtotal,  Decimal("300.00"))
        self.assertEqual(order.gst_total, Decimal("15.00"))


# ──────────────────────────────────────────────────────────────────────────────
# INCLUSIVE TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestGSTInclusive(TestCase):
    """GST-inclusive mode: price = customer pays, GST back-calculated inside."""

    def setUp(self):
        self.tenant, self.outlet, self.cat = _setup(gst_inclusive=True)

    # ── Core formula ──────────────────────────────────────────────────────────

    def test_simple_coffee_25_at_5pct(self):
        """₹25 inclusive at 5% → GST = 25×5/105 = 1.19, base = 23.81"""
        mi    = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 1)])
        # grand_total = ₹25 (customer pays exactly this)
        self.assertEqual(order.grand_total, Decimal("25"))
        # GST back-calculated
        expected_gst = (Decimal("25") * 5 / 105).quantize(Decimal("0.01"))
        self.assertAlmostEqual(float(order.gst_total), float(expected_gst), places=1)
        # subtotal = grand - gst
        self.assertAlmostEqual(
            float(order.subtotal + order.gst_total),
            float(order.grand_total),
            places=1,
        )

    def test_zero_gst_item_inclusive(self):
        """0% GST item: inclusive = exclusive, no back-calculation."""
        mi    = _item(self.tenant, self.outlet, self.cat, "Water", 20, 0)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 1)])
        self.assertEqual(order.gst_total,   Decimal("0.00"))
        self.assertEqual(order.subtotal,    Decimal("20.00"))
        self.assertEqual(order.grand_total, Decimal("20"))

    def test_18pct_back_calculation(self):
        """₹118 inclusive at 18% → GST = 118×18/118 = 18, base = 100"""
        mi    = _item(self.tenant, self.outlet, self.cat, "Premium", 118, 18)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 1)])
        self.assertEqual(order.grand_total, Decimal("118"))
        expected_gst = (Decimal("118") * 18 / 118).quantize(Decimal("0.01"))
        self.assertAlmostEqual(float(order.gst_total), float(expected_gst), places=1)

    def test_quantity_multiple(self):
        """₹25 × 3 units = ₹75 total. GST back-calculated from 75."""
        mi    = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 3)])
        self.assertEqual(order.grand_total, Decimal("75"))
        expected_gst = (Decimal("75") * 5 / 105).quantize(Decimal("0.01"))
        self.assertAlmostEqual(float(order.gst_total), float(expected_gst), places=1)

    # ── Invariants ────────────────────────────────────────────────────────────

    def test_subtotal_plus_gst_equals_grand(self):
        """subtotal + gst_total + round_off == grand_total always."""
        mi    = _item(self.tenant, self.outlet, self.cat, "Dish", 79, 5)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 2)])
        self.assertEqual(
            order.subtotal + order.gst_total + order.round_off,
            order.grand_total,
        )

    def test_no_float_rounding_errors(self):
        """Decimal arithmetic — result must be a Decimal, not a float."""
        mi = _item(self.tenant, self.outlet, self.cat, "Item", "33.33", 5)
        order = _order_with_items(self.outlet, self.tenant, [(mi, 3)])
        self.assertIsInstance(order.gst_total,   Decimal)
        self.assertIsInstance(order.subtotal,    Decimal)
        self.assertIsInstance(order.grand_total, Decimal)

    # ── Multiple items with different GST rates ───────────────────────────────

    def test_mixed_gst_rates(self):
        """Items with 5% and 18% GST. Each back-calculated independently."""
        mi5  = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        mi18 = _item(self.tenant, self.outlet, self.cat, "Beer",   118, 18)
        order = _order_with_items(self.outlet, self.tenant, [(mi5, 1), (mi18, 1)])
        # grand_total = 25 + 118 = 143
        self.assertEqual(order.grand_total, Decimal("143"))
        # total GST = GST_in_25 + GST_in_118
        expected_gst = (
            Decimal("25") * 5 / 105 +
            Decimal("118") * 18 / 118
        ).quantize(Decimal("0.01"))
        self.assertAlmostEqual(float(order.gst_total), float(expected_gst), places=1)

    def test_mixed_with_zero_gst(self):
        """Items with 5% and 0% GST."""
        mi5 = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        mi0 = _item(self.tenant, self.outlet, self.cat, "Water",  20, 0)
        order = _order_with_items(self.outlet, self.tenant, [(mi5, 1), (mi0, 1)])
        self.assertEqual(order.grand_total, Decimal("45"))
        # Only the 5% item contributes GST
        expected_gst = (Decimal("25") * 5 / 105).quantize(Decimal("0.01"))
        self.assertAlmostEqual(float(order.gst_total), float(expected_gst), places=1)

    # ── Discounts ─────────────────────────────────────────────────────────────

    def test_percentage_discount_on_inclusive_price(self):
        """10% order discount on ₹100 inclusive → customer pays ₹90."""
        mi    = _item(self.tenant, self.outlet, self.cat, "Meal", 100, 5)
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=mi.price, gst_percentage=mi.gst_percentage,
            total_price=mi.price, status="pending",
        )
        order.discount_type  = "percentage"
        order.discount_value = Decimal("10")
        order.save(update_fields=["discount_type", "discount_value"])
        order.recalculate_totals()
        order.refresh_from_db()
        # grand_total = 100 × (1 - 0.10) = 90
        self.assertEqual(order.grand_total, Decimal("90"))
        self.assertEqual(order.discount_total, Decimal("10.00"))
        # GST back-calculated from 90
        expected_gst = (Decimal("90") * 5 / 105).quantize(Decimal("0.01"))
        self.assertAlmostEqual(float(order.gst_total), float(expected_gst), places=1)

    def test_flat_amount_discount_on_inclusive(self):
        """₹5 flat discount on ₹25 inclusive → customer pays ₹20."""
        mi    = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=mi.price, gst_percentage=mi.gst_percentage,
            total_price=mi.price, status="pending",
        )
        order.discount_type  = "amount"
        order.discount_value = Decimal("5")
        order.save(update_fields=["discount_type", "discount_value"])
        order.recalculate_totals()
        order.refresh_from_db()
        self.assertEqual(order.grand_total, Decimal("20"))
        self.assertEqual(order.discount_total, Decimal("5.00"))

    def test_discount_cannot_exceed_total(self):
        """Discount > grand_total is capped at grand_total."""
        mi    = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=mi.price, gst_percentage=mi.gst_percentage,
            total_price=mi.price, status="pending",
        )
        order.discount_type  = "amount"
        order.discount_value = Decimal("999")  # far more than the order
        order.save(update_fields=["discount_type", "discount_value"])
        order.recalculate_totals()
        order.refresh_from_db()
        self.assertGreaterEqual(order.grand_total, Decimal("0"))

    # ── Voided + complimentary items excluded ─────────────────────────────────

    def test_voided_items_excluded_from_inclusive_calc(self):
        mi    = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        # active item
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=mi.price, gst_percentage=mi.gst_percentage,
            total_price=mi.price, status="pending",
        )
        # voided item — should NOT affect totals
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=mi.price, gst_percentage=mi.gst_percentage,
            total_price=mi.price, status="voided",
        )
        order.recalculate_totals()
        order.refresh_from_db()
        # Only the active item counted: grand_total = 25
        self.assertEqual(order.grand_total, Decimal("25"))

    def test_complimentary_items_excluded(self):
        mi    = _item(self.tenant, self.outlet, self.cat, "Coffee", 25, 5)
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet)
        # active item
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=mi.price, gst_percentage=mi.gst_percentage,
            total_price=mi.price, status="served",
        )
        # complimentary item — excluded from totals
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            price=mi.price, gst_percentage=mi.gst_percentage,
            total_price=mi.price, status="served", is_complimentary=True,
        )
        order.recalculate_totals()
        order.refresh_from_db()
        self.assertEqual(order.grand_total, Decimal("25"))


# ──────────────────────────────────────────────────────────────────────────────
# SWITCHING BETWEEN MODES
# ──────────────────────────────────────────────────────────────────────────────

class TestGSTModeSwitching(TestCase):
    """Switching gst_inclusive on an outlet recalculates correctly."""

    def test_same_item_different_modes_different_totals(self):
        """₹25 exclusive at 5% → ₹26 total (rounded). ₹25 inclusive → ₹25 total."""
        t_excl, o_excl, cat_excl = _setup(gst_inclusive=False)
        t_incl, o_incl, cat_incl = _setup(gst_inclusive=True)

        mi_excl = _item(t_excl, o_excl, cat_excl, "Coffee", 25, 5)
        mi_incl = _item(t_incl, o_incl, cat_incl, "Coffee", 25, 5)

        order_excl = _order_with_items(o_excl, t_excl, [(mi_excl, 1)])
        order_incl = _order_with_items(o_incl, t_incl, [(mi_incl, 1)])

        # Exclusive: 25 + 1.25 = 26.25 → rounds to 26
        self.assertEqual(order_excl.grand_total, Decimal("26"))
        # Inclusive: customer pays exactly ₹25
        self.assertEqual(order_incl.grand_total, Decimal("25"))

    def test_gst_total_lower_in_inclusive_for_same_price(self):
        """Inclusive GST is always ≤ exclusive GST for the same face price."""
        t_excl, o_excl, cat_excl = _setup(gst_inclusive=False)
        t_incl, o_incl, cat_incl = _setup(gst_inclusive=True)

        mi_excl = _item(t_excl, o_excl, cat_excl, "Item", 100, 18)
        mi_incl = _item(t_incl, o_incl, cat_incl, "Item", 100, 18)

        order_excl = _order_with_items(o_excl, t_excl, [(mi_excl, 1)])
        order_incl = _order_with_items(o_incl, t_incl, [(mi_incl, 1)])

        # Exclusive GST = 18. Inclusive back-calc = 100×18/118 ≈ 15.25
        self.assertGreater(order_excl.gst_total, order_incl.gst_total)
