# Rasova POS — Testing Strategy
**What to test, why, how, with real code examples from this codebase.**

---

## The Testing Pyramid

```
                        ▲
                       /|\
                      / | \
                     /  |  \
                    / E2E \       ← 5%   (manual, from TESTING_GUIDE.md)
                   /────────\
                  / Integration\  ← 25%  (HTTP client, full DB, real services)
                 /──────────────\
                /   Unit Tests   \ ← 70%  (isolated, fast, no DB where possible)
               /──────────────────\

Don't invert this pyramid. Unit tests are cheap, E2E tests are expensive.
```

**What Rasova already has:** 17 unit/integration tests (`orders/tests/`), 0 coverage tooling, 0 pytest.  
**This document:** what to add and how.

---

## Part 1 — Tools to Install

```bash
pip install pytest pytest-django factory-boy faker coverage[toml]
```

Add to `requirements.txt`:
```
# Testing (dev only — do not install on production server)
pytest==8.3.5
pytest-django==4.9.0
factory-boy==3.3.1
faker==28.4.1
coverage[toml]==7.6.10
```

Create `pytest.ini` in the project root:
```ini
[pytest]
DJANGO_SETTINGS_MODULE = core.settings
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = --tb=short -q
```

Create `pyproject.toml` (or add to existing):
```toml
[tool.coverage.run]
source = ["accounts", "menu", "orders", "setup", "reports", "inventory", "tenants"]
omit = ["*/migrations/*", "*/tests/*", "*/__pycache__/*", "*/management/commands/*"]

[tool.coverage.report]
show_missing = true
skip_covered = false
fail_under = 70
```

---

## Part 2 — Test Categories for Rasova

| Category | What it tests | Speed | Needs DB |
|---|---|---|---|
| **Unit** | Individual functions, model methods, decorators | Fast (< 1s) | Sometimes |
| **Integration** | Full HTTP request → DB → response | Medium (1-5s) | Yes |
| **Security** | Tenant isolation, RBAC, feature gates | Fast | Yes |
| **Financial** | Decimal math, totals, GST, no float bugs | Fast | Yes |
| **Concurrency** | Race conditions in payment + KOT | Slow | Yes (TransactionTestCase) |
| **Celery/Task** | Print tasks, idempotency, retry | Fast | Yes (fakeredis) |
| **API** | JSON endpoints, status codes, payloads | Medium | Yes |

---

## Part 3 — Unit Tests

### 3.1 — What to unit-test

Unit tests check ONE thing in isolation. Mock everything else.

**High priority — test these first:**

```
orders/services/order_service.py
  recalculate_totals()       ← money calculation, must be exact
  get_or_create_open_order() ← must not create duplicate open orders per table

orders/services/kot_service.py
  create_kot()               ← KOT number must be unique per outlet per day
                             ← items grouped correctly by station

orders/services/payment_service.py
  process_payment()          ← select_for_update must prevent double payment

core/features.py
  has_feature()              ← default + override logic
  TENANT_FEATURES            ← each type has correct defaults

core/decorators.py
  feature_required           ← returns JSON 403 for API, raises PermissionDenied for pages
  tenant_required            ← blocks users with no tenant

orders/tasks.py
  print_kot_task             ← idempotency key prevents double print
  print_bill_task            ← outlet_id isolation skips wrong outlet
```

### 3.2 — Example: Financial accuracy

```python
# orders/tests/test_financial.py
from decimal import Decimal
from django.test import TestCase
from orders.models import Order, OrderItem
from tenants.models import Tenant, Outlet


class TestRecalculateTotals(TestCase):
    def setUp(self):
        tenant = Tenant.objects.create(name="TestRest")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        self.order = Order.objects.create(tenant=tenant, outlet=outlet)

    def _add(self, price, qty, gst):
        from menu.models import MenuCategory, MenuItem
        cat, _ = MenuCategory.objects.get_or_create(
            tenant=self.order.tenant, outlet=self.order.outlet, name="X"
        )
        mi = MenuItem.objects.create(
            tenant=self.order.tenant, outlet=self.order.outlet, category=cat,
            name=f"Item {price}", price=Decimal(str(price)),
            gst_percentage=Decimal(str(gst))
        )
        OrderItem.objects.create(
            order=self.order, menu_item=mi, quantity=qty,
            price=Decimal(str(price)), gst_percentage=Decimal(str(gst)),
            total_price=Decimal(str(price)) * qty,
        )

    def test_simple_total(self):
        self._add(100, 2, 5)
        self.order.recalculate_totals()
        self.assertEqual(self.order.subtotal, Decimal("200.00"))
        self.assertEqual(self.order.gst_total, Decimal("10.00"))
        self.assertEqual(self.order.grand_total, Decimal("210.00"))

    def test_no_float_rounding_error(self):
        # Classic float trap: 0.1 + 0.2 != 0.3
        # Decimal must handle this correctly
        self._add("99.99", 3, 5)  # 299.97 * 1.05 = 314.9685 → 314.97
        self.order.recalculate_totals()
        # Must be EXACT Decimal, not 314.9700000000001
        self.assertIsInstance(self.order.grand_total, Decimal)
        self.assertEqual(str(self.order.grand_total), str(self.order.grand_total))

    def test_voided_items_excluded(self):
        self._add(200, 1, 5)
        # Void all items
        self.order.items.update(status="voided")
        self.order.recalculate_totals()
        self.assertEqual(self.order.grand_total, Decimal("0.00"))

    def test_discount_reduces_total(self):
        self._add(200, 1, 0)   # No GST for simplicity
        self.order.discount_total = Decimal("20.00")
        self.order.recalculate_totals()
        self.assertEqual(self.order.grand_total, Decimal("180.00"))

    def test_gst_rounding_half_up(self):
        # 100 * 5% = 5.00 — simple case
        # 100 * 18% = 18.00 — simple case
        # 67 * 5% = 3.35 — should be 3.35, not 3.4 or 3.3
        self._add(67, 1, 5)
        self.order.recalculate_totals()
        self.assertEqual(self.order.gst_total, Decimal("3.35"))
```

### 3.3 — Example: Feature flag logic

```python
# core/tests/test_features.py
from django.test import TestCase
from core.features import has_feature, TENANT_FEATURES
from tenants.models import Tenant, TenantFeatureOverride


class TestHasFeature(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="TestRest", tenant_type="fine_dining")

    def test_default_on_feature(self):
        # floor_plan is ON by default for fine_dining
        self.assertTrue(has_feature(self.tenant, "floor_plan"))

    def test_default_off_feature(self):
        # token_system is OFF by default for fine_dining
        self.assertFalse(has_feature(self.tenant, "token_system"))

    def test_override_enables(self):
        # Force-enable token_system for fine_dining tenant
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="token_system", enabled=True
        )
        self.assertTrue(has_feature(self.tenant, "token_system"))

    def test_override_disables(self):
        # Force-disable floor_plan (normally ON for fine_dining)
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="floor_plan", enabled=False
        )
        self.assertFalse(has_feature(self.tenant, "floor_plan"))

    def test_franchise_defaults(self):
        t = Tenant.objects.create(name="QSR", tenant_type="franchise")
        self.assertTrue(has_feature(t, "token_system"))
        self.assertFalse(has_feature(t, "floor_plan"))

    def test_cache_invalidation_after_override(self):
        # First call caches the result
        result_before = has_feature(self.tenant, "token_system")
        self.assertFalse(result_before)
        # Create override
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="token_system", enabled=True
        )
        # Invalidate cache if your implementation caches (check _feature_overrides attr)
        if hasattr(self.tenant, "_feature_overrides"):
            del self.tenant._feature_overrides
        self.assertTrue(has_feature(self.tenant, "token_system"))
```

### 3.4 — Example: Decorator tests

```python
# core/tests/test_decorators.py
from django.test import TestCase, RequestFactory
from django.http import JsonResponse
from core.decorators import feature_required, tenant_required
from accounts.models import User
from tenants.models import Tenant, Outlet, TenantFeatureOverride


class TestFeatureRequired(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Rest", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.user   = User.objects.create_user(
            username="u1", password="pass", tenant=self.tenant, outlet=self.outlet
        )

    def test_api_request_gets_json_403(self):
        """API paths get JSON 403, not HTML PermissionDenied."""
        from django.test import Client
        c = Client()
        c.force_login(self.user)
        # kitchen_display is OFF for franchise by default
        response = c.post("/send-kitchen-message/999/",
                         content_type="application/json",
                         data=b'{"message":"test"}')
        # Should be 403 JSON (not HTML) because Content-Type: application/json
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertIn("error", data)

    def test_page_request_raises_permission_denied(self):
        """Non-API pages raise PermissionDenied (renders 403.html)."""
        from django.test import Client
        c = Client()
        c.force_login(self.user)
        # kitchen/ requires kitchen_display — not enabled for franchise
        response = c.get("/kitchen/")
        self.assertEqual(response.status_code, 403)
```

---

## Part 4 — Integration Tests (HTTP → DB → Response)

Integration tests hit the full Django stack: URL routing, views, DB, response.

### 4.1 — What to integration-test

```
Every URL that:
  - Writes to DB (POST, creates records)
  - Is protected by decorators
  - Returns JSON that JS depends on
  - Has complex business logic in the view
```

### 4.2 — Example: Order creation flow

```python
# orders/tests/test_integration.py
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from tenants.models import Tenant, Outlet


class TestCreateOrderFlow(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Rest")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.cashier = User.objects.create_user(
            username="cash", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier"
        )
        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Food"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat,
            name="Burger", price=Decimal("150"), gst_percentage=Decimal("5"),
            is_available=True
        )
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="T1", state="free"
        )
        self.c = Client()
        self.c.force_login(self.cashier)

    def test_create_order_creates_record(self):
        payload = {"cart": [{"id": self.item.id, "quantity": 2}],
                   "table_id": self.table.id, "source": "dine_in"}
        response = self.c.post("/create-order/",
                               data=json.dumps(payload),
                               content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("order_id", data)

        # Verify DB state
        order = Order.objects.get(id=data["order_id"])
        self.assertEqual(order.tenant, self.tenant)
        self.assertEqual(order.outlet, self.outlet)
        self.assertEqual(order.items.count(), 1)
        item = order.items.first()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.price, Decimal("150"))

    def test_empty_cart_rejected(self):
        payload = {"cart": [], "table_id": self.table.id, "source": "dine_in"}
        response = self.c.post("/create-order/",
                               data=json.dumps(payload),
                               content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_table_state_changes_to_ordering(self):
        payload = {"cart": [{"id": self.item.id, "quantity": 1}],
                   "table_id": self.table.id, "source": "dine_in"}
        self.c.post("/create-order/", data=json.dumps(payload),
                    content_type="application/json")
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "ordering")

    def test_second_order_same_table_reuses_existing(self):
        payload = {"cart": [{"id": self.item.id, "quantity": 1}],
                   "table_id": self.table.id, "source": "dine_in"}
        r1 = self.c.post("/create-order/", data=json.dumps(payload),
                         content_type="application/json")
        r2 = self.c.post("/create-order/", data=json.dumps(payload),
                         content_type="application/json")
        d1 = r1.json()
        d2 = r2.json()
        # Same table → same order (items added to existing order)
        self.assertEqual(d1["order_id"], d2["order_id"])
        order = Order.objects.get(id=d1["order_id"])
        # 2 items total (1 from each request), NOT 2 separate orders
        self.assertEqual(Order.objects.filter(table=self.table, status="open").count(), 1)
```

### 4.3 — Example: Generate bill + pay order

```python
class TestPaymentFlow(TestCase):
    # ... (same setUp as above)

    def _create_and_send_order(self):
        """Helper: create an order with 1 item, send to kitchen."""
        from orders.services.order_service import get_or_create_open_order, add_items_to_order
        order = get_or_create_open_order(self.cashier, self.table)
        add_items_to_order(order, [{"id": self.item.id, "quantity": 1}],
                           user=self.cashier)
        # Mark items as sent (bypass KOT for speed)
        order.items.update(status="sent")
        return order

    def test_generate_bill_transitions_to_billing(self):
        order = self._create_and_send_order()
        response = self.c.post(f"/generate-bill/{order.id}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("grand_total", data)
        order.refresh_from_db()
        self.assertEqual(order.status, "billing")

    def test_pay_order_closes_order(self):
        order = self._create_and_send_order()
        order.status = "billing"
        order.recalculate_totals()
        order.save()
        # Setup payment config
        from setup.models import PaymentConfig
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet, cash_enabled=True
        )
        payload = {"method": "cash", "amount": str(order.grand_total)}
        response = self.c.post(f"/pay/{order.id}/",
                               data=json.dumps(payload),
                               content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        order.refresh_from_db()
        self.assertEqual(order.status, "closed")

    def test_cannot_pay_already_closed_order(self):
        order = self._create_and_send_order()
        order.status = "closed"
        order.save()
        payload = {"method": "cash", "amount": "200"}
        response = self.c.post(f"/pay/{order.id}/",
                               data=json.dumps(payload),
                               content_type="application/json")
        data = response.json()
        self.assertFalse(data.get("success", False))
        self.assertIn("error", data)
```

---

## Part 5 — Security Tests (Tenant Isolation + RBAC)

These are the most important tests in a multi-tenant SaaS.
One failure here means Restaurant A can see Restaurant B's data.

### 5.1 — Tenant isolation tests (MUST PASS 100%)

```python
# orders/tests/test_security.py
from django.test import TestCase, Client
from accounts.models import User
from orders.models import Order, Table
from tenants.models import Tenant, Outlet


def _setup_tenant(name):
    t = Tenant.objects.create(name=name)
    o = Outlet.objects.create(tenant=t, name=f"{name} Main")
    u = User.objects.create_user(
        username=f"owner_{name.lower()}", password="pass",
        tenant=t, outlet=o, role="owner"
    )
    return t, o, u


class TestTenantIsolation(TestCase):
    def setUp(self):
        self.t_a, self.o_a, self.u_a = _setup_tenant("RestaurantA")
        self.t_b, self.o_b, self.u_b = _setup_tenant("RestaurantB")

        # Create an order belonging to tenant B
        self.table_b = Table.objects.create(
            tenant=self.t_b, outlet=self.o_b, name="T1", state="free"
        )
        self.order_b = Order.objects.create(
            tenant=self.t_b, outlet=self.o_b,
            table=self.table_b, status="open"
        )

    def test_cannot_access_other_tenant_bill(self):
        """Tenant A user cannot open Tenant B's bill page."""
        c = Client()
        c.force_login(self.u_a)
        response = c.get(f"/bill/{self.order_b.id}/")
        # Must be 404 (the order doesn't exist in tenant A's scope)
        # NOT 200 (would expose B's data) and NOT 500 (error)
        self.assertEqual(response.status_code, 404)

    def test_cannot_pay_other_tenant_order(self):
        """POST to /pay/<id>/ for another tenant's order returns 404."""
        import json
        c = Client()
        c.force_login(self.u_a)
        response = c.post(
            f"/pay/{self.order_b.id}/",
            data=json.dumps({"method": "cash", "amount": "100"}),
            content_type="application/json"
        )
        # Either 404 (order not found for this tenant) or 400
        self.assertIn(response.status_code, [404, 400])

    def test_tables_data_only_shows_own_outlet(self):
        """GET /tables-data/ must not leak other outlets' tables."""
        Table.objects.create(
            tenant=self.t_a, outlet=self.o_a, name="T1", state="free"
        )
        c = Client()
        c.force_login(self.u_a)
        response = c.get("/tables-data/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for table in data["tables"]:
            # All tables must belong to tenant A's outlet
            t = Table.objects.get(id=table["id"])
            self.assertEqual(t.tenant_id, self.t_a.id)

    def test_notifications_api_scoped_to_outlet(self):
        """GET /api/notifications/ must only return own outlet's data."""
        from orders.models import WaiterCall
        # Create a waiter call for tenant B
        WaiterCall.objects.create(
            tenant=self.t_b, outlet=self.o_b, table=self.table_b
        )
        c = Client()
        c.force_login(self.u_a)
        response = c.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # Tenant A should see 0 waiter calls (the one we made was for B)
        self.assertEqual(data["waiter_calls"]["count"], 0)

    def test_kitchen_messages_waiter_scoped(self):
        """Waiter only sees kitchen messages for orders they created."""
        from orders.models import KitchenMessage
        # Order created by waiter A
        waiter_a = User.objects.create_user(
            username="waiter_a", password="pass",
            tenant=self.t_a, outlet=self.o_a, role="waiter"
        )
        waiter_b_same_outlet = User.objects.create_user(
            username="waiter_b2", password="pass",
            tenant=self.t_a, outlet=self.o_a, role="waiter"
        )
        order_by_a = Order.objects.create(
            tenant=self.t_a, outlet=self.o_a, created_by=waiter_a
        )
        # Kitchen sends message about waiter A's order
        KitchenMessage.objects.create(
            tenant=self.t_a, outlet=self.o_a,
            order=order_by_a, message="Delayed 10 mins"
        )
        # Waiter B (different person, same outlet) should NOT see this
        c = Client()
        c.force_login(waiter_b_same_outlet)
        response = c.get("/api/notifications/")
        data = response.json()
        self.assertEqual(data["kitchen_messages"]["count"], 0)

        # Waiter A SHOULD see it
        c2 = Client()
        c2.force_login(waiter_a)
        response2 = c2.get("/api/notifications/")
        data2 = response2.json()
        self.assertEqual(data2["kitchen_messages"]["count"], 1)
```

### 5.2 — Role-based access control tests

```python
class TestRolePermissions(TestCase):
    def setUp(self):
        t = Tenant.objects.create(name="Rest")
        o = Outlet.objects.create(tenant=t, name="Main")
        self.owner   = User.objects.create_user("owner",   "pass", tenant=t, outlet=o, role="owner")
        self.manager = User.objects.create_user("manager", "pass", tenant=t, outlet=o, role="manager")
        self.cashier = User.objects.create_user("cashier", "pass", tenant=t, outlet=o, role="cashier")
        self.waiter  = User.objects.create_user("waiter",  "pass", tenant=t, outlet=o, role="waiter")
        self.kitchen = User.objects.create_user("kitchen", "pass", tenant=t, outlet=o, role="chef")

    def _get(self, url, user):
        c = Client()
        c.force_login(user)
        return c.get(url)

    def test_waiter_cannot_access_reports(self):
        response = self._get("/reports/dashboard/", self.waiter)
        self.assertIn(response.status_code, [302, 403])

    def test_kitchen_cannot_access_billing(self):
        response = self._get("/billing/", self.kitchen)
        # Kitchen gets redirected to /kitchen/ on login, but direct URL should block
        self.assertIn(response.status_code, [302, 403, 200])
        # If 200, verify it's not the billing page (kitchen user shouldn't see it)

    def test_cashier_cannot_access_setup(self):
        response = self._get("/setup/", self.cashier)
        self.assertIn(response.status_code, [302, 403])

    def test_superuser_bypasses_feature_required(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        su = User.objects.create_superuser("admin2", "admin@test.com", "pass")
        # Superuser can access kitchen even if kitchen_display is not enabled
        response = self._get("/kitchen/", su)
        # Should not be 403 for superuser
        self.assertNotEqual(response.status_code, 403)
```

---

## Part 6 — Financial Accuracy Tests (Critical)

```python
# orders/tests/test_financial_accuracy.py
"""
No float arithmetic. Every paise must be exact.
These tests must NEVER be deleted.
"""
from decimal import Decimal
from django.test import TestCase
from orders.models import Order, OrderItem
from tenants.models import Tenant, Outlet


class TestGSTCalculation(TestCase):
    def _order(self):
        t = Tenant.objects.create(name="X")
        o = Outlet.objects.create(tenant=t, name="Y")
        return Order.objects.create(tenant=t, outlet=o)

    def _add_item(self, order, price, qty, gst):
        from menu.models import MenuCategory, MenuItem
        cat, _ = MenuCategory.objects.get_or_create(
            tenant=order.tenant, outlet=order.outlet, name="C"
        )
        mi = MenuItem.objects.create(
            tenant=order.tenant, outlet=order.outlet, category=cat,
            name="I", price=Decimal(str(price)),
            gst_percentage=Decimal(str(gst))
        )
        return OrderItem.objects.create(
            order=order, menu_item=mi, quantity=qty,
            price=Decimal(str(price)), gst_percentage=Decimal(str(gst)),
            total_price=Decimal(str(price)) * qty
        )

    def test_5_percent_gst(self):
        o = self._order()
        self._add_item(o, 200, 1, 5)
        o.recalculate_totals()
        self.assertEqual(o.subtotal, Decimal("200.00"))
        self.assertEqual(o.gst_total, Decimal("10.00"))
        self.assertEqual(o.grand_total, Decimal("210.00"))

    def test_18_percent_gst(self):
        o = self._order()
        self._add_item(o, 100, 1, 18)
        o.recalculate_totals()
        self.assertEqual(o.gst_total, Decimal("18.00"))
        self.assertEqual(o.grand_total, Decimal("118.00"))

    def test_zero_gst(self):
        o = self._order()
        self._add_item(o, 50, 2, 0)
        o.recalculate_totals()
        self.assertEqual(o.gst_total, Decimal("0.00"))
        self.assertEqual(o.grand_total, Decimal("100.00"))

    def test_multiple_items_different_gst(self):
        o = self._order()
        self._add_item(o, 100, 1, 5)    # subtotal 100, gst 5
        self._add_item(o, 200, 1, 18)   # subtotal 200, gst 36
        o.recalculate_totals()
        self.assertEqual(o.subtotal, Decimal("300.00"))
        self.assertEqual(o.gst_total, Decimal("41.00"))
        self.assertEqual(o.grand_total, Decimal("341.00"))

    def test_quantity_multiplication_exact(self):
        o = self._order()
        self._add_item(o, "33.33", 3, 0)  # 33.33 * 3 = 99.99
        o.recalculate_totals()
        self.assertEqual(o.subtotal, Decimal("99.99"))

    def test_grand_total_equals_payment_required(self):
        """The amount you must collect equals grand_total. No discrepancy."""
        o = self._order()
        self._add_item(o, 175, 2, 5)    # 350 + 17.5 = 367.5
        o.recalculate_totals()
        # grand_total should be Decimal("367.50"), not 367.4999... or 367.5001...
        self.assertEqual(o.grand_total, Decimal("367.50"))
        self.assertTrue(o.grand_total > 0)
        self.assertIsInstance(o.grand_total, Decimal)
```

---

## Part 7 — Concurrency Tests

These are the hardest and slowest to write. Use `TransactionTestCase`.  
`TestCase` wraps everything in one transaction, which hides deadlocks.  
`TransactionTestCase` commits to the real DB, which reveals them.

**Warning:** These tests are 10-30x slower. Run them only in CI, not on every save.

```python
# orders/tests/test_concurrency.py
"""
Concurrency tests for race-condition-prone code.
Uses TransactionTestCase so that select_for_update() actually locks rows.
"""
import threading
from decimal import Decimal
from django.test import TransactionTestCase
from accounts.models import User
from orders.models import Order, Table, DailyKOTCounter
from tenants.models import Tenant, Outlet
from setup.models import KitchenStation, PaymentConfig


class TestPaymentRaceCondition(TransactionTestCase):
    """
    Two cashiers try to pay the same order simultaneously.
    One should succeed. One should get "Order already completed".
    """
    def setUp(self):
        t = Tenant.objects.create(name="RaceRest")
        o = Outlet.objects.create(tenant=t, name="Main")
        self.cashier = User.objects.create_user(
            "cashier_race", "pass", tenant=t, outlet=o, role="cashier"
        )
        table = Table.objects.create(tenant=t, outlet=o, name="T1", state="free")
        self.order = Order.objects.create(
            tenant=t, outlet=o, table=table, status="billing",
            subtotal=Decimal("200"), gst_total=Decimal("10"),
            grand_total=Decimal("210")
        )
        PaymentConfig.objects.create(
            tenant=t, outlet=o, cash_enabled=True
        )

    def test_double_payment_prevented(self):
        """Two concurrent POST /pay/<id>/ calls — exactly one succeeds."""
        from django.test import Client
        results = []
        errors  = []

        def pay():
            try:
                c = Client()
                c.force_login(self.cashier)
                import json
                r = c.post(f"/pay/{self.order.id}/",
                           data=json.dumps({"method": "cash", "amount": "210"}),
                           content_type="application/json")
                results.append(r.json())
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=pay)
        t2 = threading.Thread(target=pay)
        t1.start(); t2.start()
        t1.join();  t2.join()

        self.assertEqual(len(errors), 0, f"Exceptions: {errors}")
        successes = [r for r in results if r.get("success")]
        failures  = [r for r in results if not r.get("success")]
        # Exactly one payment accepted
        self.assertEqual(len(successes), 1, "Expected exactly 1 success")
        self.assertEqual(len(failures),  1, "Expected exactly 1 failure")
        # Order in DB has exactly one payment
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "closed")
        self.assertEqual(self.order.payments.exclude(method="refund").count(), 1)


class TestKOTNumberUnique(TransactionTestCase):
    """
    10 concurrent KOT creations for the same outlet/day.
    Each must get a UNIQUE KOT number.
    """
    def setUp(self):
        t = Tenant.objects.create(name="KOTRace")
        o = Outlet.objects.create(tenant=t, name="Main")
        self.user = User.objects.create_user(
            "user_kot", "pass", tenant=t, outlet=o, role="cashier"
        )
        self.station = KitchenStation.objects.create(
            tenant=t, outlet=o, name="Grill", is_default=True
        )
        self.t, self.o = t, o

    def test_concurrent_kot_numbers_unique(self):
        from orders.services.kot_service import create_kot
        from menu.models import MenuCategory, MenuItem
        from orders.models import OrderItem, KOTBatch

        cat = MenuCategory.objects.create(tenant=self.t, outlet=self.o, name="X")
        mi = MenuItem.objects.create(
            tenant=self.t, outlet=self.o, category=cat,
            name="Item", price=Decimal("100"),
            gst_percentage=Decimal("5"), station=self.station
        )

        kot_numbers = []
        errors = []

        def make_kot():
            try:
                order = Order.objects.create(
                    tenant=self.t, outlet=self.o, status="open"
                )
                OrderItem.objects.create(
                    order=order, menu_item=mi, quantity=1,
                    price=mi.price, gst_percentage=mi.gst_percentage,
                    total_price=mi.price, status="pending"
                )
                kots = create_kot(self.user, order)
                for k in kots:
                    kot_numbers.append(k.kot_number)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=make_kot) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(kot_numbers), 10)
        # All 10 KOT numbers must be unique
        self.assertEqual(len(set(kot_numbers)), 10,
                         f"Duplicate KOT numbers: {kot_numbers}")
```

---

## Part 8 — Celery Task Tests (with fakeredis)

```python
# orders/tests/test_tasks.py
from unittest.mock import patch, MagicMock
from decimal import Decimal
from django.test import TestCase, override_settings
from django.core.cache import cache
from accounts.models import User
from orders.models import Order, KOTBatch, Table
from setup.models import KitchenStation
from tenants.models import Tenant, Outlet


@override_settings(CACHES={
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache"
    }
})
class TestPrintBillTask(TestCase):
    def setUp(self):
        t = Tenant.objects.create(name="PrintRest")
        o = Outlet.objects.create(tenant=t, name="Main")
        self.station = KitchenStation.objects.create(
            tenant=t, outlet=o, name="Counter",
            printer_ip="192.168.1.100", printer_port=9100, is_default=True
        )
        self.order = Order.objects.create(
            tenant=t, outlet=o, status="closed",
            subtotal=Decimal("200"), gst_total=Decimal("10"),
            grand_total=Decimal("210")
        )

    def test_idempotency_prevents_double_print(self):
        """If idempotency key exists, task returns True without printing."""
        cache.set(f"bill_printed_{self.order.id}", True)

        with patch("orders.tasks.PrintingService") as mock_svc:
            from orders.tasks import print_bill_task
            result = print_bill_task(self.order.id, self.station.id)

        self.assertTrue(result)
        mock_svc.assert_not_called()  # printer was never instantiated

    def test_wrong_outlet_skips(self):
        """If RASOVA_OUTLET_ID is set and doesn't match, task skips."""
        import os
        # Temporarily set the outlet guard to a different outlet
        other_outlet = Outlet.objects.create(
            tenant=self.order.tenant, name="Other"
        )
        with patch.dict(os.environ, {"RASOVA_OUTLET_ID": str(other_outlet.id)}):
            # Reload the module-level variable
            import importlib
            import orders.tasks as tasks_module
            original = tasks_module._LOCAL_OUTLET_ID
            tasks_module._LOCAL_OUTLET_ID = other_outlet.id

            with patch("orders.tasks.PrintingService") as mock_svc:
                from orders.tasks import print_bill_task
                result = print_bill_task(self.order.id, self.station.id)

            tasks_module._LOCAL_OUTLET_ID = original  # restore

        self.assertFalse(result)
        mock_svc.assert_not_called()

    def test_successful_print_writes_idempotency_key(self):
        """After successful print, Redis key must be set."""
        with patch("orders.tasks.PrintingService") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.print_bill_with_kots.return_value = True
            mock_cls.return_value = mock_instance

            from orders.tasks import print_bill_task
            result = print_bill_task(self.order.id, self.station.id)

        self.assertTrue(result)
        # Idempotency key must now exist in cache
        self.assertTrue(cache.get(f"bill_printed_{self.order.id}"))

    def test_failed_print_does_not_write_idempotency_key(self):
        """If print fails, key must NOT be set (so retry can try again)."""
        with patch("orders.tasks.PrintingService") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.print_bill_with_kots.return_value = False
            mock_cls.return_value = mock_instance

            from orders.tasks import print_bill_task
            try:
                print_bill_task(self.order.id, self.station.id)
            except Exception:
                pass  # Will raise retry exception

        self.assertIsNone(cache.get(f"bill_printed_{self.order.id}"))
```

---

## Part 9 — API Tests

```python
# orders/tests/test_api.py
import json
from django.test import TestCase, Client
from accounts.models import User
from orders.models import WaiterCall, KitchenMessage, Order, Table
from tenants.models import Tenant, Outlet


class TestNotificationAPI(TestCase):
    def setUp(self):
        t = Tenant.objects.create(name="NotifRest")
        o = Outlet.objects.create(tenant=t, name="Main")
        table = Table.objects.create(tenant=t, outlet=o, name="T1", state="free")
        self.waiter  = User.objects.create_user("waiter1", "pass", tenant=t, outlet=o, role="waiter")
        self.waiter2 = User.objects.create_user("waiter2", "pass", tenant=t, outlet=o, role="waiter")
        self.manager = User.objects.create_user("mgr",     "pass", tenant=t, outlet=o, role="manager")
        self.order = Order.objects.create(
            tenant=t, outlet=o, created_by=self.waiter, status="open"
        )
        WaiterCall.objects.create(tenant=t, outlet=o, table=table)
        KitchenMessage.objects.create(
            tenant=t, outlet=o, order=self.order, message="Delayed"
        )
        self.t, self.o = t, o

    def test_returns_correct_structure(self):
        c = Client()
        c.force_login(self.manager)
        r = c.get("/api/notifications/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("waiter_calls", data)
        self.assertIn("kitchen_messages", data)
        self.assertIn("notifications", data)
        self.assertIn("count", data["waiter_calls"])
        self.assertIn("items", data["waiter_calls"])

    def test_manager_sees_all_kitchen_messages(self):
        c = Client()
        c.force_login(self.manager)
        data = c.get("/api/notifications/").json()
        self.assertEqual(data["kitchen_messages"]["count"], 1)

    def test_waiter_sees_only_own_kitchen_messages(self):
        # waiter2 should see 0 (the message is for waiter1's order)
        c = Client()
        c.force_login(self.waiter2)
        data = c.get("/api/notifications/").json()
        self.assertEqual(data["kitchen_messages"]["count"], 0)

    def test_unauthenticated_gets_redirect(self):
        r = Client().get("/api/notifications/")
        self.assertIn(r.status_code, [302, 403])

    def test_resolved_calls_not_counted(self):
        from orders.models import WaiterCall
        WaiterCall.objects.all().update(is_resolved=True)
        c = Client()
        c.force_login(self.manager)
        data = c.get("/api/notifications/").json()
        self.assertEqual(data["waiter_calls"]["count"], 0)


class TestKitchenDataAPI(TestCase):
    def setUp(self):
        t = Tenant.objects.create(name="KitchenRest")
        o = Outlet.objects.create(tenant=t, name="Main")
        self.kitchen_user = User.objects.create_user(
            "chef1", "pass", tenant=t, outlet=o, role="chef"
        )

    def test_kitchen_data_returns_json(self):
        c = Client()
        c.force_login(self.kitchen_user)
        r = c.get("/kitchen-data/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("orders", data)
```

---

## Part 10 — What NOT to Test

Avoid testing these — they waste time and break on every refactor:

```
❌ Django's own ORM queries (filter, get, create) — Django tests those
❌ HTML content of templates character by character
❌ Third-party library behaviour (escpos, fakeredis internals)
❌ Every possible HTTP status code for non-critical paths
❌ Generated migration files
❌ Management commands that wrap other tested functions
❌ Settings values themselves
❌ Logger output format
```

---

## Part 11 — Test Organisation

```
orders/
  tests/
    __init__.py
    test_critical.py          ← existing (17 tests, all pass)
    test_financial.py         ← decimal accuracy, GST, totals ← ADD
    test_security.py          ← tenant isolation, RBAC        ← ADD
    test_api.py               ← JSON endpoints                ← ADD
    test_tasks.py             ← Celery idempotency            ← ADD
    test_concurrency.py       ← race conditions (slow)        ← ADD

core/
  tests/
    __init__.py
    test_features.py          ← has_feature, overrides        ← ADD
    test_decorators.py        ← feature_required, tenant_req  ← ADD
```

Each file tests ONE concern. Never mix unit tests with integration tests in the same file.

---

## Part 12 — Test Data: Factories

Once you install `factory-boy`, replace repeated setup helpers with factories:

```python
# orders/tests/factories.py
import factory
from decimal import Decimal
from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Tenant, Outlet


class TenantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tenant
    name = factory.Sequence(lambda n: f"Restaurant {n}")
    tenant_type = "fine_dining"


class OutletFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Outlet
    tenant = factory.SubFactory(TenantFactory)
    name   = "Main Branch"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
    username = factory.Sequence(lambda n: f"user{n}")
    password = factory.PostGenerationMethodCall("set_password", "testpass")
    tenant   = factory.SubFactory(TenantFactory)
    outlet   = factory.LazyAttribute(lambda o: OutletFactory(tenant=o.tenant))
    role     = "cashier"


class MenuItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MenuItem
    tenant          = factory.SubFactory(TenantFactory)
    outlet          = factory.LazyAttribute(lambda o: OutletFactory(tenant=o.tenant))
    category        = factory.LazyAttribute(lambda o: MenuCategory.objects.create(
                          tenant=o.tenant, outlet=o.outlet, name="Food"))
    name            = factory.Sequence(lambda n: f"Item {n}")
    price           = Decimal("100.00")
    gst_percentage  = Decimal("5.00")
    is_available    = True


class OrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Order
    tenant = factory.SubFactory(TenantFactory)
    outlet = factory.LazyAttribute(lambda o: OutletFactory(tenant=o.tenant))
    status = "open"
```

Usage:
```python
# Instead of 10 lines of setUp:
tenant   = TenantFactory()
cashier  = UserFactory(tenant=tenant, role="cashier")
item     = MenuItemFactory(tenant=tenant)
order    = OrderFactory(tenant=tenant, outlet=cashier.outlet)
```

---

## Part 13 — Running Tests

```bash
# Run all tests (uses Django's test runner)
python manage.py test --keepdb

# Run specific file
python manage.py test orders.tests.test_critical --keepdb

# Run specific test class
python manage.py test orders.tests.test_security.TestTenantIsolation --keepdb

# Run specific test method
python manage.py test orders.tests.test_security.TestTenantIsolation.test_cannot_access_other_tenant_bill --keepdb

# With pytest (after pip install pytest pytest-django)
pytest

# With coverage
coverage run manage.py test --keepdb
coverage report
coverage html   # generates htmlcov/index.html — open in browser

# Skip slow concurrency tests during development
pytest -k "not Concurrency"
```

---

## Part 14 — Coverage Targets

| Module | Target | Why |
|---|---|---|
| `orders/services/` | 90% | Payment + KOT logic — financial risk |
| `core/features.py` | 95% | Every tenant type + override combination |
| `core/decorators.py` | 90% | Blocks all API vs page requests correctly |
| `orders/models.py` | 80% | `recalculate_totals` must be 100% |
| `orders/views/` | 70% | Integration tests cover the critical paths |
| `setup/views/` | 50% | Config views, lower risk |
| `accounts/views/` | 60% | Login, dashboard |
| `menu/views/` | 50% | CRUD, lower risk |

**Current state:** 17 tests, unknown coverage (coverage not installed).  
**Target:** 80+ tests, 75% overall coverage before first paid customer.

---

## Part 15 — CI/CD: Auto-run on Every Push

Add `.github/workflows/test.yml`:
```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: test_db
          POSTGRES_PASSWORD: testpass
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:alpine
        options: --health-cmd "redis-cli ping" --health-interval 10s

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests with coverage
        env:
          DATABASE_URL: postgres://postgres:testpass@localhost/test_db
          SECRET_KEY: ci-test-secret-key-not-real
          REDIS_URL: redis://localhost:6379/0
          DEBUG: "True"
          ALLOWED_HOSTS: "localhost"
        run: |
          coverage run manage.py test --keepdb --verbosity=0
          coverage report --fail-under=70
```

This runs on every `git push`. If tests fail, GitHub shows a red ✗ on the commit.  
Never merge to `main` if tests are red.

---

## Summary: Priority Order

Build tests in this order. Stop after each batch if you're short on time.

```
Batch 1 — Must have before first paying customer (2-3 hours)
  ✅ test_critical.py           (already done, 17 tests)
  📝 test_financial.py          (decimal accuracy — ~8 tests)
  📝 test_security.py           (tenant isolation — ~6 tests)

Batch 2 — Should have before scaling to 5+ restaurants (half a day)
  📝 test_api.py                (notification, kitchen, tables APIs — ~10 tests)
  📝 core/test_features.py      (feature flag logic — ~6 tests)
  📝 core/test_decorators.py    (JSON 403 vs HTML 403 — ~4 tests)

Batch 3 — Nice to have (a full day)
  📝 test_tasks.py              (Celery idempotency — ~5 tests)
  📝 test_concurrency.py        (race conditions — ~3 tests, slow)
  📝 Install factories for test_* maintainability

Batch 4 — Coverage polish
  📝 Install coverage, run coverage html, fix the red lines
  📝 Reach 75% overall coverage
  📝 Wire up GitHub Actions CI
```
