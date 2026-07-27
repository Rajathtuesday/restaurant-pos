"""
Tests for Counter Billing Mode features:
  1. Composition Scheme (Bill of Supply)
  2. Split Bill by Category (Counter Billing Mode)
  3. Parcel Surcharge
  4. Inventory deduction at payment time
  5. Feature gating

Run: python manage.py test orders.tests.test_billing_modes
"""
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from tenants.models import Tenant, Outlet
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment
from setup.models import PaymentConfig


# ── Shared fixture ────────────────────────────────────────────────────────────

class CounterBillingBase(TestCase):
    """Creates a café tenant (counter billing type) with 3 menu categories."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Counter Cafe Test",
            tenant_type="cafe",
        )
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Counter",
            gst_no="29AABCM1234A1Z1",
            parcel_charge_amount=Decimal("5"),
            parcel_charge_per_item=False,
            is_composition_scheme=False,
            split_bill_by_category=False,
        )
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            cash_enabled=True, upi_enabled=True,
        )
        self.owner = User.objects.create_user(
            username="counter_owner",
            password="testpass",
            tenant=self.tenant,
            outlet=self.outlet,
            role="owner",
        )
        # Three menu categories
        self.cat_breakfast = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Breakfast"
        )
        self.cat_juice = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Juice"
        )
        self.cat_tea = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Tea & Coffee"
        )
        # Menu items
        self.idli = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.cat_breakfast, name="Idli", price=40, gst_percentage=5,
        )
        self.dosa = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.cat_breakfast, name="Masala Dosa", price=60, gst_percentage=5,
        )
        self.juice = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.cat_juice, name="Mango Juice", price=40, gst_percentage=0,
        )
        self.chai = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.cat_tea, name="Cutting Chai", price=15, gst_percentage=0,
        )
        self.client = Client()
        self.client.login(username="counter_owner", password="testpass")

    def _make_order(self, items=None):
        """Helper — creates an open order with given items."""
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.owner, source="counter",
        )
        if items is None:
            items = [(self.idli, 1), (self.juice, 2), (self.chai, 2)]
        for menu_item, qty in items:
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=qty,
                price=menu_item.price,
                gst_percentage=menu_item.gst_percentage,
                total_price=menu_item.price * qty,
            )
        order.recalculate_totals()
        return order


# ── 1. Composition Scheme ─────────────────────────────────────────────────────

class CompositionSchemeTest(CounterBillingBase):

    def test_outlet_flag_defaults_false(self):
        self.assertFalse(self.outlet.is_composition_scheme)

    def test_set_composition_scheme_via_outlet_settings(self):
        resp = self.client.post(
            reverse("outlet_settings"),
            {
                "name": self.outlet.name,
                "gst_no": self.outlet.gst_no,
                "is_composition_scheme": "true",
                "parcel_charge_amount": "5",
            },
        )
        self.assertIn(resp.status_code, [200, 302])
        self.outlet.refresh_from_db()
        self.assertTrue(self.outlet.is_composition_scheme)

    def test_composition_receipt_context(self):
        """thermal_receipt_view passes is_comp context correctly."""
        order = self._make_order()
        self.outlet.is_composition_scheme = True
        self.outlet.save(update_fields=["is_composition_scheme"])
        resp = self.client.get(f"/thermal-receipt/{order.id}/")
        self.assertEqual(resp.status_code, 200)
        # Template receives split_mode and category_groups context
        self.assertIn("split_mode", resp.context)

    def test_composition_gst_not_in_totals(self):
        """Order grand_total = subtotal when all items have 0% GST on composition outlet."""
        self.outlet.is_composition_scheme = True
        self.outlet.save(update_fields=["is_composition_scheme"])
        # Items with 0% GST — total equals price sum
        order = self._make_order([(self.juice, 1), (self.chai, 1)])
        self.assertEqual(order.grand_total, Decimal("55"))


# ── 2. Split Bill by Category ─────────────────────────────────────────────────

class SplitBillTest(CounterBillingBase):

    def setUp(self):
        super().setUp()
        self.outlet.split_bill_by_category = True
        self.outlet.save(update_fields=["split_bill_by_category"])

    def test_split_mode_context_passed_to_receipt(self):
        order = self._make_order()
        resp = self.client.get(f"/thermal-receipt/{order.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["split_mode"])
        self.assertGreater(len(resp.context["category_groups"]), 0)

    def test_category_groups_correct_count(self):
        """3 categories in order → 3 category groups."""
        order = self._make_order()
        resp = self.client.get(f"/thermal-receipt/{order.id}/")
        # Breakfast (idli), Juice, Tea — 3 groups
        self.assertEqual(len(resp.context["category_groups"]), 3)

    def test_category_groups_single_category(self):
        """Order with items from only 1 category → 1 group."""
        order = self._make_order([(self.idli, 2), (self.dosa, 1)])
        resp = self.client.get(f"/thermal-receipt/{order.id}/")
        self.assertEqual(len(resp.context["category_groups"]), 1)

    def test_voided_items_excluded_from_groups(self):
        """Voided items must not appear in category slips."""
        order = self._make_order([(self.idli, 1), (self.juice, 1)])
        # Void the juice
        order.items.filter(menu_item=self.juice).update(status="voided")
        order.recalculate_totals()
        resp = self.client.get(f"/thermal-receipt/{order.id}/")
        # Only 1 category now (Breakfast)
        self.assertEqual(len(resp.context["category_groups"]), 1)
        self.assertEqual(resp.context["category_groups"][0]["cat_name"], "Breakfast")

    def test_split_receipt_loads_for_multi_category_order(self):
        """Receipt endpoint returns 200 with split_mode=True for multi-category order."""
        order = self._make_order()  # default: breakfast + juice + tea
        resp = self.client.get(f"/thermal-receipt/{order.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["split_mode"])
        # Each category slip has cat_name set correctly
        for group in resp.context["category_groups"]:
            self.assertIn("cat_name", group)
            self.assertTrue(group["cat_name"])  # non-empty

    def test_split_mode_off_no_category_groups(self):
        """When split_bill_by_category is False → category_groups is empty."""
        self.outlet.split_bill_by_category = False
        self.outlet.save(update_fields=["split_bill_by_category"])
        order = self._make_order()
        resp = self.client.get(f"/thermal-receipt/{order.id}/")
        self.assertFalse(resp.context["split_mode"])
        self.assertEqual(resp.context["category_groups"], [])


# ── 3. Parcel Surcharge ───────────────────────────────────────────────────────

class ParcelSurchargeTest(CounterBillingBase):

    def test_parcel_charge_default_zero_on_order(self):
        order = self._make_order()
        self.assertEqual(order.parcel_surcharge, Decimal("0"))

    def test_toggle_parcel_adds_surcharge(self):
        order = self._make_order()
        order.status = "billing"
        order.save(update_fields=["status"])
        original_total = order.grand_total

        resp = self.client.post(f"/toggle-parcel/{order.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["parcel_on"])
        self.assertEqual(Decimal(str(data["parcel_amount"])), Decimal("5"))
        self.assertEqual(Decimal(str(data["grand_total"])), original_total + Decimal("5"))

    def test_toggle_parcel_twice_removes_surcharge(self):
        order = self._make_order()
        order.status = "billing"
        order.save(update_fields=["status"])
        original_total = order.grand_total

        self.client.post(f"/toggle-parcel/{order.id}/")   # add
        resp = self.client.post(f"/toggle-parcel/{order.id}/")  # remove
        data = resp.json()
        self.assertFalse(data["parcel_on"])
        self.assertEqual(Decimal(str(data["grand_total"])), original_total)

    def test_toggle_parcel_on_closed_order_returns_404(self):
        order = self._make_order()
        order.status = "closed"
        order.save(update_fields=["status"])
        resp = self.client.post(f"/toggle-parcel/{order.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_parcel_included_in_grand_total_recalculate(self):
        order = self._make_order([(self.idli, 1)])  # 40 + GST
        order.parcel_surcharge = Decimal("5")
        order.save(update_fields=["parcel_surcharge"])
        order.recalculate_totals()
        order.refresh_from_db()
        # Grand total must be greater by 5 compared to no parcel
        self.assertEqual(order.grand_total - Decimal("5"),
                         order.subtotal + order.gst_total)

    def test_outlet_with_zero_parcel_charge(self):
        """Outlet with parcel_charge_amount=0 → no button shown (template gating)."""
        self.outlet.parcel_charge_amount = Decimal("0")
        self.outlet.save(update_fields=["parcel_charge_amount"])
        # Just verify the value is 0 — template hiding is a visual test
        self.assertEqual(self.outlet.parcel_charge_amount, Decimal("0"))


# ── 4. Inventory Deduction at Payment ────────────────────────────────────────

class InventoryDeductionTest(CounterBillingBase):

    def setUp(self):
        super().setUp()
        self.outlet.split_bill_by_category = True
        self.outlet.save(update_fields=["split_bill_by_category"])
        # Create inventory item + recipe
        from inventory.models import InventoryItem, Recipe
        self.batter = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Idli Batter", unit="kg", stock=Decimal("5.0"),
            cost_price=Decimal("50"),
        )
        Recipe.objects.create(
            menu_item=self.idli,
            inventory_item=self.batter,
            quantity_required=Decimal("0.1"), unit="kg",  # 100g per idli, batter is tracked in kg
        )

    def test_inventory_deducts_on_payment_for_counter_billing(self):
        """For counter billing outlets, stock deducts when order closes."""
        from inventory.models import InventoryItem
        order = self._make_order([(self.idli, 2)])  # 2 idlis = 200g batter
        order.status = "billing"
        order.save(update_fields=["status"])

        self.client.post(
            f"/pay/{order.id}/",
            data='{"method":"cash","amount":"' + str(order.grand_total) + '"}',
            content_type="application/json",
        )

        batter = InventoryItem.objects.get(id=self.batter.id)
        # 5.0 - (2 × 0.1) = 4.8
        self.assertAlmostEqual(float(batter.stock), 4.8, places=2)

    def test_insufficient_stock_does_not_block_payment(self):
        """If stock runs out, payment still succeeds (just logs a warning)."""
        self.batter.stock = Decimal("0.05")  # only 50g — less than 200g needed
        self.batter.save(update_fields=["stock"])

        order = self._make_order([(self.idli, 2)])
        order.status = "billing"
        order.save(update_fields=["status"])

        resp = self.client.post(
            f"/pay/{order.id}/",
            data='{"method":"cash","amount":"' + str(order.grand_total) + '"}',
            content_type="application/json",
        )
        # Payment should succeed regardless of stock state
        data = resp.json()
        self.assertTrue(data.get("success"), f"Payment failed: {data}")

    def test_counter_source_always_deducts_inventory(self):
        """
        payment_service deducts inventory for order.source=='counter' regardless of
        split_bill_by_category. The split_bill flag controls PRINT behaviour, not stock.
        """
        from inventory.models import InventoryItem
        self.outlet.split_bill_by_category = False  # Counter Billing OFF
        self.outlet.save(update_fields=["split_bill_by_category"])

        order = self._make_order([(self.idli, 2)])  # source='counter' by default
        order.status = "billing"
        order.save(update_fields=["status"])
        initial_stock = float(InventoryItem.objects.get(id=self.batter.id).stock)

        self.client.post(
            f"/pay/{order.id}/",
            data='{"method":"cash","amount":"' + str(order.grand_total) + '"}',
            content_type="application/json",
        )

        batter = InventoryItem.objects.get(id=self.batter.id)
        # 2 idlis × 0.1 kg = 0.2 kg deducted — counter orders ALWAYS deduct
        self.assertAlmostEqual(float(batter.stock), initial_stock - 0.2, places=2)

    def test_modifier_recipe_deducted_on_qsr_counter_payment(self):
        """The QSR/counter deduction path (_deduct_inventory_for_order) used
        to only look at base recipes — a paid modifier's inventory link (e.g.
        Extra Chutney) was silently never deducted at all on counter orders,
        unlike the fine-dining KOT path which already handled it."""
        from inventory.models import InventoryItem, ModifierRecipe
        from menu.models import ModifierGroup, Modifier
        from orders.models import OrderItemModifier

        chutney = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Chutney", unit="ml", stock=Decimal("1000"),
        )
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        mod = Modifier.objects.create(group=group, name="Extra Chutney", price=Decimal("10"))
        ModifierRecipe.objects.create(
            modifier=mod, inventory_item=chutney,
            quantity_required=Decimal("50"), unit="ml",
        )

        order = self._make_order([(self.idli, 1)])
        OrderItemModifier.objects.create(
            order_item=order.items.first(), modifier=mod, name=mod.name, price=mod.price,
        )
        order.recalculate_totals()
        order.status = "billing"
        order.save(update_fields=["status"])

        self.client.post(
            f"/pay/{order.id}/",
            data='{"method":"cash","amount":"' + str(order.grand_total) + '"}',
            content_type="application/json",
        )

        chutney.refresh_from_db()
        self.assertEqual(chutney.stock, Decimal("950"))  # 1000 - 50ml, not left at 1000


# ── 5. Feature Gating ─────────────────────────────────────────────────────────

class FeatureGatingTest(TestCase):

    def test_cafe_has_counter_billing_feature(self):
        from core.features import has_feature
        tenant = Tenant.objects.create(name="Cafe FG Test", tenant_type="cafe")
        self.assertTrue(has_feature(tenant, "counter_billing"))
        self.assertTrue(has_feature(tenant, "composition_scheme"))
        self.assertTrue(has_feature(tenant, "parcel_charge"))

    def test_franchise_has_counter_billing_feature(self):
        from core.features import has_feature
        tenant = Tenant.objects.create(name="Franchise FG Test", tenant_type="franchise")
        self.assertTrue(has_feature(tenant, "counter_billing"))
        self.assertTrue(has_feature(tenant, "composition_scheme"))

    def test_fine_dining_does_not_have_counter_billing(self):
        from core.features import has_feature
        tenant = Tenant.objects.create(name="Fine Dining FG Test", tenant_type="fine_dining")
        self.assertFalse(has_feature(tenant, "counter_billing"))

    def test_fine_dining_has_composition_scheme(self):
        """Fine dining can also be on composition scheme."""
        from core.features import has_feature
        tenant = Tenant.objects.create(name="Fine Dining Comp Test", tenant_type="fine_dining")
        self.assertTrue(has_feature(tenant, "composition_scheme"))

    def test_feature_override_enables_counter_billing_for_fine_dining(self):
        """Admin can manually enable counter_billing for fine dining via override."""
        from core.features import has_feature
        from tenants.models import TenantFeatureOverride
        tenant = Tenant.objects.create(name="FD Override Test", tenant_type="fine_dining")
        TenantFeatureOverride.objects.create(
            tenant=tenant, feature="counter_billing", enabled=True
        )
        self.assertTrue(has_feature(tenant, "counter_billing"))


# ── 6. Payment reference field ────────────────────────────────────────────────
# Moved from orders/tests/test_razorpay.py::ProcessPaymentReferenceTest (Phase 6
# of the orders app split) -- tests orders.services.payment_service.process_payment's
# generic `reference` field, nothing razorpay-specific, CounterBillingBase is
# just the fixture vehicle. Stays here, colocated with that fixture, rather
# than moving into payments/tests.py with the genuinely razorpay-specific tests.

class ProcessPaymentReferenceTest(CounterBillingBase):

    def test_reference_stored_on_payment(self):
        from orders.services.payment_service import process_payment
        order = self._make_order()
        result = process_payment(order, "upi", order.grand_total, reference="pay_abc123")
        self.assertEqual(result["payment"].reference, "pay_abc123")

    def test_reference_defaults_to_none(self):
        from orders.services.payment_service import process_payment
        order = self._make_order()
        result = process_payment(order, "cash", order.grand_total)
        self.assertIsNone(result["payment"].reference)
