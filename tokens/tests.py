# tokens/tests.py
"""
Tests for the tokens app (Phase 2 of the orders app split).

Combines, verbatim except for import-path fixes, four source files:
  - orders/tests/test_qsr_upgrade.py (moved wholesale -- every class in it
    was confirmed token-specific)
  - orders/tests/test_token_system.py (7 of 9 classes moved; the other 2
    test orders.services.payment_service and core.utils.get_business_date
    generically and stayed in orders/tests/test_token_system.py)
  - orders/tests/test_critical_fixes.py (2 of 5 classes moved wholesale,
    plus one test METHOD split out of a third class that mixed a
    token-view test with an unrelated one)
  - orders/tests/test_schema_review.py (1 small class moved)

Section 1 below (QSR upgrade features):
  - DailyOnlineTokenCounter: sequential numbering, daily reset, concurrency
  - TokenOrder.is_online + display_number property
  - Dual token series independence (counter vs online never collide)
  - assign_online_token helper
  - create_and_go_to_billing endpoint (direct billing mode)
  - token_billing context: active_tokens sidebar (counter_tokens, online_tokens)
  - token_dashboard context: counter/online split + can_create role gate
  - RBAC: cashier can create, waiter cannot, manager can discount
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from threading import Thread

from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from setup.models import AggregatorConfig, PaymentConfig
from shifts.models import CashSession
from tenants.models import Outlet, Tenant
from tokens.models import DailyOnlineTokenCounter, DailyTokenCounter, TokenOrder


# ======================================================================
#  SHARED FIXTURE
# ======================================================================

class QSRFixtureMixin:
    """Shared setup for QSR-upgrade tests."""

    def _build(self):
        self.tenant = Tenant.objects.create(name="QSR Test Co", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")
        from core.utils import get_business_date
        self.today  = get_business_date(timezone.now(), self.outlet)

        self.owner = User.objects.create_user(
            username="owner1", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.manager = User.objects.create_user(
            username="mgr1", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="manager",
        )
        self.cashier = User.objects.create_user(
            username="cashier1", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier",
        )
        self.waiter = User.objects.create_user(
            username="waiter1", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="waiter",
        )

        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            cash_enabled=True, upi_enabled=True, card_enabled=True,
        )
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.cashier, opening_balance=0, status="open",
        )
        AggregatorConfig.objects.get_or_create(
            tenant=self.tenant, outlet=self.outlet,
            defaults={"zomato_enabled": True, "swiggy_enabled": True},
        )

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Burgers", is_active=True
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat,
            name="Zinger", price=Decimal("120"), is_available=True,
        )

    def _make_order(self, source="counter"):
        return Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.cashier, status="open", source=source,
        )


# ======================================================================
#  1. DailyOnlineTokenCounter model
# ======================================================================

class TestDailyOnlineTokenCounter(TestCase, QSRFixtureMixin):

    def setUp(self):
        self._build()

    def test_counter_starts_at_zero(self):
        counter, created = DailyOnlineTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant,
            date=self.today, defaults={"value": 0},
        )
        self.assertTrue(created)
        self.assertEqual(counter.value, 0)

    def test_increment(self):
        counter, _ = DailyOnlineTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant,
            date=self.today, defaults={"value": 0},
        )
        counter.value += 1
        counter.save(update_fields=["value"])
        counter.refresh_from_db()
        self.assertEqual(counter.value, 1)

    def test_unique_per_outlet_per_day(self):
        from django.db import IntegrityError
        DailyOnlineTokenCounter.objects.create(
            outlet=self.outlet, tenant=self.tenant, date=self.today, value=3,
        )
        with self.assertRaises(IntegrityError):
            DailyOnlineTokenCounter.objects.create(
                outlet=self.outlet, tenant=self.tenant, date=self.today, value=4,
            )

    def test_separate_from_counter_tokens(self):
        """Online counter and regular counter are completely independent rows."""
        DailyTokenCounter.objects.create(
            outlet=self.outlet, tenant=self.tenant, date=self.today, value=5,
        )
        DailyOnlineTokenCounter.objects.create(
            outlet=self.outlet, tenant=self.tenant, date=self.today, value=2,
        )
        self.assertEqual(DailyTokenCounter.objects.get(outlet=self.outlet).value, 5)
        self.assertEqual(DailyOnlineTokenCounter.objects.get(outlet=self.outlet).value, 2)

    def test_resets_across_days(self):
        yesterday = self.today - timedelta(days=1)
        c1, _ = DailyOnlineTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant, date=yesterday, defaults={"value": 10},
        )
        c2, _ = DailyOnlineTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant, date=self.today, defaults={"value": 0},
        )
        self.assertEqual(c1.value, 10)
        self.assertEqual(c2.value, 0)

    def test_str_representation(self):
        c, _ = DailyOnlineTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant, date=self.today, defaults={"value": 7},
        )
        self.assertIn("7", str(c))
        self.assertIn("Online", str(c))


# ======================================================================
#  2. TokenOrder.is_online + display_number
# ======================================================================

class TestTokenOrderIsOnline(TestCase, QSRFixtureMixin):

    def setUp(self):
        self._build()

    def test_display_number_counter(self):
        order = self._make_order()
        tok = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=5, date=self.today, is_online=False,
        )
        self.assertEqual(tok.display_number, "#5")

    def test_display_number_online(self):
        order = self._make_order(source="zomato")
        tok = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=3, date=self.today, is_online=True,
        )
        self.assertEqual(tok.display_number, "O-3")

    def test_counter_and_online_same_number_allowed(self):
        """Token #1 counter AND O-1 online may coexist in the same outlet+day."""
        o1 = self._make_order()
        o2 = self._make_order(source="zomato")
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=o1, token_number=1, date=self.today, is_online=False,
        )
        # Must NOT raise — different is_online separates them
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=o2, token_number=1, date=self.today, is_online=True,
        )
        self.assertEqual(TokenOrder.objects.count(), 2)

    def test_duplicate_counter_token_raises(self):
        """Two counter tokens with same number in same day must raise."""
        from django.db import IntegrityError
        o1 = self._make_order()
        o2 = self._make_order()
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=o1, token_number=2, date=self.today, is_online=False,
        )
        with self.assertRaises(IntegrityError):
            TokenOrder.objects.create(
                tenant=self.tenant, outlet=self.outlet,
                order=o2, token_number=2, date=self.today, is_online=False,
            )

    def test_duplicate_online_token_raises(self):
        """Two online tokens with same number in same day must raise."""
        from django.db import IntegrityError
        o1 = self._make_order(source="zomato")
        o2 = self._make_order(source="swiggy")
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=o1, token_number=1, date=self.today, is_online=True,
        )
        with self.assertRaises(IntegrityError):
            TokenOrder.objects.create(
                tenant=self.tenant, outlet=self.outlet,
                order=o2, token_number=1, date=self.today, is_online=True,
            )

    def test_default_is_online_false(self):
        order = self._make_order()
        tok = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=9, date=self.today,
        )
        self.assertFalse(tok.is_online)


# ======================================================================
#  3. assign_online_token helper
# ======================================================================

class TestAssignOnlineToken(TestCase, QSRFixtureMixin):

    def setUp(self):
        self._build()

    def _assign(self, source="zomato"):
        from django.db import transaction
        from core.utils import get_business_date
        from tokens.views import assign_online_token
        order = self._make_order(source=source)
        business_date = get_business_date(timezone.now(), self.outlet)
        with transaction.atomic():
            return assign_online_token(order, self.outlet, self.tenant, business_date)

    def test_assigns_online_token(self):
        tok = self._assign()
        self.assertTrue(tok.is_online)
        self.assertEqual(tok.token_number, 1)
        self.assertEqual(tok.display_number, "O-1")

    def test_sequential_online_tokens(self):
        t1 = self._assign()
        t2 = self._assign(source="swiggy")
        t3 = self._assign()
        nums = [t1.token_number, t2.token_number, t3.token_number]
        self.assertEqual(nums, [1, 2, 3])

    def test_online_counter_increments(self):
        self._assign()
        self._assign()
        from core.utils import get_business_date
        counter = DailyOnlineTokenCounter.objects.get(outlet=self.outlet, date=get_business_date(timezone.now(), self.outlet))
        self.assertEqual(counter.value, 2)

    def test_online_does_not_affect_counter_tokens(self):
        """Assigning online tokens must not touch DailyTokenCounter."""
        self._assign()
        self._assign()
        self.assertFalse(DailyTokenCounter.objects.filter(outlet=self.outlet).exists())

    def test_self_heal_when_counter_missing(self):
        """If DailyOnlineTokenCounter row is absent but tokens exist, self-heals."""
        from django.db import transaction
        from core.utils import get_business_date
        from tokens.views import assign_online_token

        # Create an online token directly (simulating a row-delete in admin)
        existing_order = self._make_order(source="zomato")
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=existing_order, token_number=5, date=self.today, is_online=True,
        )
        # No DailyOnlineTokenCounter row exists — next assign should pick up from 6
        new_order = self._make_order(source="swiggy")
        business_date = get_business_date(timezone.now(), self.outlet)
        with transaction.atomic():
            tok = assign_online_token(new_order, self.outlet, self.tenant, business_date)
        self.assertEqual(tok.token_number, 6)


# ======================================================================
#  4. create_token_order view — RBAC
# ======================================================================

class TestCreateTokenOrderRBAC(TestCase, QSRFixtureMixin):

    def setUp(self):
        self._build()

    def _post_as(self, user):
        c = Client()
        c.login(username=user.username, password="pass")
        return c.post(
            reverse("create-token-order"),
            data=json.dumps({}),
            content_type="application/json",
        )

    def test_owner_can_create(self):
        resp = self._post_as(self.owner)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_manager_can_create(self):
        resp = self._post_as(self.manager)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_cashier_can_create(self):
        resp = self._post_as(self.cashier)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_waiter_cannot_create(self):
        resp = self._post_as(self.waiter)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("error", resp.json())


# ======================================================================
#  5. create_and_go_to_billing endpoint (direct billing mode)
# ======================================================================

class TestCreateAndGoToBilling(TestCase, QSRFixtureMixin):

    def setUp(self):
        self._build()
        self.client = Client()
        self.client.login(username="owner1", password="pass")

    def test_creates_token_and_returns_redirect(self):
        resp = self.client.post(
            reverse("create-and-bill"),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("/token/", data["redirect"])
        self.assertIn("/bill/", data["redirect"])

    def test_token_number_sequential(self):
        r1 = self.client.post(reverse("create-and-bill"), data=json.dumps({}), content_type="application/json").json()
        r2 = self.client.post(reverse("create-and-bill"), data=json.dumps({}), content_type="application/json").json()
        self.assertEqual(r1["token_number"], 1)
        self.assertEqual(r2["token_number"], 2)

    def test_order_source_is_counter(self):
        resp = self.client.post(reverse("create-and-bill"), data=json.dumps({}), content_type="application/json").json()
        order = Order.objects.get(id=resp["order_id"])
        self.assertEqual(order.source, "counter")

    def test_waiter_cannot_use_direct_billing(self):
        c = Client()
        c.login(username="waiter1", password="pass")
        resp = c.post(reverse("create-and-bill"), data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 403)

    def test_only_post_allowed(self):
        resp = self.client.get(reverse("create-and-bill"))
        self.assertEqual(resp.status_code, 405)


# ======================================================================
#  6. token_billing context: active orders sidebar
# ======================================================================

class TestTokenBillingSidebar(TestCase, QSRFixtureMixin):

    def setUp(self):
        self._build()
        self.client = Client()
        self.client.login(username="cashier1", password="pass")
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def _make_token(self, is_online=False, status="open"):
        source = "zomato" if is_online else "counter"
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.cashier, status=status, source=source,
        )
        num = TokenOrder.objects.filter(outlet=self.outlet, date=self.today, is_online=is_online).count() + 1
        tok = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=num, date=self.today, is_online=is_online,
        )
        return order, tok

    def test_sidebar_contains_counter_tokens(self):
        order, _ = self._make_token(is_online=False)
        resp = self.client.get(reverse("token-bill", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("counter_tokens", resp.context)
        self.assertGreaterEqual(resp.context["counter_tokens"].count(), 1)

    def test_sidebar_contains_online_tokens(self):
        order_c, _ = self._make_token(is_online=False)
        order_o, _ = self._make_token(is_online=True)
        resp = self.client.get(reverse("token-bill", args=[order_c.id]))
        self.assertIn("online_tokens", resp.context)
        self.assertEqual(resp.context["online_tokens"].count(), 1)

    def test_sidebar_excludes_other_outlets(self):
        """
        Tokens belonging to a different outlet must not appear in the sidebar.
        We test this by verifying the view query filters on outlet correctly.
        All tokens in the context must belong to the logged-in user's outlet.
        """
        # Create two counter tokens for self.outlet
        order1, tok1 = self._make_token(is_online=False)
        order2, tok2 = self._make_token(is_online=False)

        resp = self.client.get(reverse("token-bill", args=[order1.id]))
        self.assertEqual(resp.status_code, 200)

        # All tokens returned must belong to self.outlet only
        for tok in resp.context["counter_tokens"]:
            self.assertEqual(
                tok.outlet_id, self.outlet.id,
                f"Token {tok.token_number} belongs to outlet {tok.outlet_id}, expected {self.outlet.id}",
            )
        for tok in resp.context["online_tokens"]:
            self.assertEqual(tok.outlet_id, self.outlet.id)

    def test_sidebar_excludes_closed_orders(self):
        order_closed, _ = self._make_token(is_online=False, status="closed")
        order_open, _ = self._make_token(is_online=False, status="open")
        resp = self.client.get(reverse("token-bill", args=[order_open.id]))
        ids_in_sidebar = [t.order.id for t in resp.context["counter_tokens"]]
        self.assertNotIn(order_closed.id, ids_in_sidebar)
        self.assertIn(order_open.id, ids_in_sidebar)

    def test_can_discount_true_for_owner(self):
        c = Client()
        c.login(username="owner1", password="pass")
        order, _ = self._make_token()
        resp = c.get(reverse("token-bill", args=[order.id]))
        self.assertTrue(resp.context["can_discount"])

    def test_can_discount_false_for_cashier(self):
        order, _ = self._make_token()
        resp = self.client.get(reverse("token-bill", args=[order.id]))
        self.assertFalse(resp.context["can_discount"])

    def test_can_bypass_only_for_owner(self):
        order, _ = self._make_token()
        # cashier
        resp = self.client.get(reverse("token-bill", args=[order.id]))
        self.assertFalse(resp.context["can_bypass"])
        # owner
        c = Client()
        c.login(username="owner1", password="pass")
        resp = c.get(reverse("token-bill", args=[order.id]))
        self.assertTrue(resp.context["can_bypass"])


# ======================================================================
#  7. token_dashboard context: counter/online split + can_create
# ======================================================================

class TestTokenDashboardUpgrade(TestCase, QSRFixtureMixin):

    def setUp(self):
        self._build()
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def _login_as(self, user):
        c = Client()
        c.login(username=user.username, password="pass")
        return c

    def test_dashboard_has_counter_tokens(self):
        order = self._make_order()
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=1, date=self.today, is_online=False,
        )
        c = self._login_as(self.cashier)
        resp = c.get(reverse("token-dashboard"))
        self.assertIn("counter_tokens", resp.context)
        self.assertEqual(resp.context["counter_tokens"].count(), 1)

    def test_dashboard_has_online_tokens(self):
        order = self._make_order(source="zomato")
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=1, date=self.today, is_online=True,
        )
        c = self._login_as(self.cashier)
        resp = c.get(reverse("token-dashboard"))
        self.assertEqual(resp.context["online_count"], 1)

    def test_counter_and_online_split_correctly(self):
        o1 = self._make_order()
        o2 = self._make_order(source="swiggy")
        TokenOrder.objects.create(tenant=self.tenant, outlet=self.outlet, order=o1, token_number=1, date=self.today, is_online=False)
        TokenOrder.objects.create(tenant=self.tenant, outlet=self.outlet, order=o2, token_number=1, date=self.today, is_online=True)
        c = self._login_as(self.cashier)
        resp = c.get(reverse("token-dashboard"))
        self.assertEqual(resp.context["counter_tokens"].count(), 1)
        self.assertEqual(resp.context["online_count"], 1)

    def test_can_create_true_for_cashier(self):
        c = self._login_as(self.cashier)
        resp = c.get(reverse("token-dashboard"))
        self.assertTrue(resp.context["can_create"])

    def test_can_create_true_for_owner(self):
        c = self._login_as(self.owner)
        resp = c.get(reverse("token-dashboard"))
        self.assertTrue(resp.context["can_create"])

    def test_can_create_false_for_waiter(self):
        """Waiter still sees dashboard (read-only) but cannot create."""
        # Waiter has the token_system feature via franchise tenant type
        # but can_create should be False
        c = self._login_as(self.waiter)
        resp = c.get(reverse("token-dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_create"])


# ======================================================================
#  8. Concurrency: DailyOnlineTokenCounter under parallel requests
# ======================================================================

class TestOnlineTokenConcurrency(TransactionTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Conc Online Co", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="O1")
        self.user = User.objects.create_user(
            username="conc_cashier", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier",
        )
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            cash_enabled=True, upi_enabled=True, card_enabled=True,
        )
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, opening_balance=0, status="open",
        )
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def _create_online_token(self, results, idx):
        from django.db import connection, transaction
        from core.utils import get_business_date
        from tokens.views import assign_online_token
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    tenant=self.tenant, outlet=self.outlet,
                    created_by=self.user, status="open", source="zomato",
                )
                business_date = get_business_date(timezone.now(), self.outlet)
                tok = assign_online_token(order, self.outlet, self.tenant, business_date)
                results[idx] = tok.token_number
        except Exception as e:
            results[idx] = f"ERROR: {e}"
        finally:
            connection.close()

    def test_concurrent_online_tokens_are_unique(self):
        n = 8
        results = [None] * n
        threads = [Thread(target=self._create_online_token, args=(results, i)) for i in range(n)]
        for t in threads: t.start()
        for t in threads: t.join()

        errors = [r for r in results if isinstance(r, str) and r.startswith("ERROR")]
        self.assertEqual(errors, [], f"Concurrent errors: {errors}")
        self.assertEqual(len(set(results)), n, f"Duplicate online tokens: {results}")

        counter = DailyOnlineTokenCounter.objects.get(outlet=self.outlet, date=self.today)
        self.assertEqual(counter.value, n)


# ======================================================================
#  SECTION 2 -- moved from orders/tests/test_token_system.py
#  (TestGetBusinessDate and TestProcessPayment stayed behind there --
#  neither is token-specific, both just used a franchise fixture as the
#  test vehicle for otherwise-generic orders/core functionality)
# ======================================================================

from orders.models import Payment


class TokenFixtureMixin:
    """Shared setup for both TestCase and TransactionTestCase sub-classes."""

    def _build_franchise_fixtures(self):
        self.tenant = Tenant.objects.create(name="Preetam Franchise", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Outlet 1")

        self.user = User.objects.create_user(
            username="cashier1",
            password="pass",
            tenant=self.tenant,
            outlet=self.outlet,
            role="cashier",
        )

        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Burgers", is_active=True
        )
        self.item_available = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Zinger", price=Decimal("120"), is_available=True,
        )
        self.item_unavailable = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="86'd Item", price=Decimal("50"), is_available=False,
        )

        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            cash_enabled=True, upi_enabled=True, card_enabled=True,
        )
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, opening_balance=0, status="open",
        )

    def _build_fine_dining_fixtures(self):
        self.fd_tenant = Tenant.objects.create(name="Fine Dining Co", tenant_type="fine_dining")
        self.fd_outlet = Outlet.objects.create(tenant=self.fd_tenant, name="Main Hall")
        self.fd_user = User.objects.create_user(
            username="fd_waiter",
            password="pass",
            tenant=self.fd_tenant,
            outlet=self.fd_outlet,
            role="waiter",
        )


class TestDailyTokenCounter(TestCase, TokenFixtureMixin):

    def setUp(self):
        self._build_franchise_fixtures()
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def test_counter_starts_at_zero(self):
        counter, created = DailyTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant,
            date=self.today, defaults={"value": 0},
        )
        self.assertTrue(created)
        self.assertEqual(counter.value, 0)

    def test_increment(self):
        counter, _ = DailyTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant,
            date=self.today, defaults={"value": 0},
        )
        counter.value += 1
        counter.save(update_fields=["value"])
        counter.refresh_from_db()
        self.assertEqual(counter.value, 1)

    def test_unique_per_outlet_per_day(self):
        DailyTokenCounter.objects.create(
            outlet=self.outlet, tenant=self.tenant, date=self.today, value=5
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            DailyTokenCounter.objects.create(
                outlet=self.outlet, tenant=self.tenant, date=self.today, value=6
            )

    def test_resets_across_days(self):
        yesterday = self.today - timedelta(days=1)
        c1, _ = DailyTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant,
            date=yesterday, defaults={"value": 15},
        )
        c2, _ = DailyTokenCounter.objects.get_or_create(
            outlet=self.outlet, tenant=self.tenant,
            date=self.today, defaults={"value": 0},
        )
        self.assertEqual(c1.value, 15)
        self.assertEqual(c2.value, 0)


class TestTokenOrderModel(TestCase, TokenFixtureMixin):

    def setUp(self):
        self._build_franchise_fixtures()
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def _make_order(self):
        return Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.user, status="open", source="counter",
        )

    def test_create_token_order(self):
        order = self._make_order()
        tok = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=1, date=self.today,
        )
        self.assertEqual(tok.token_number, 1)
        self.assertEqual(order.token.token_number, 1)

    def test_one_token_per_order(self):
        from django.db import IntegrityError
        order = self._make_order()
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=1, date=self.today,
        )
        with self.assertRaises(IntegrityError):
            TokenOrder.objects.create(
                tenant=self.tenant, outlet=self.outlet,
                order=order, token_number=2, date=self.today,
            )

    def test_unique_token_number_per_outlet_per_day(self):
        from django.db import IntegrityError
        o1 = self._make_order()
        o2 = self._make_order()
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=o1, token_number=7, date=self.today,
        )
        with self.assertRaises(IntegrityError):
            TokenOrder.objects.create(
                tenant=self.tenant, outlet=self.outlet,
                order=o2, token_number=7, date=self.today,
            )

    def test_same_number_different_day_allowed(self):
        yesterday = self.today - timedelta(days=1)
        o1 = self._make_order()
        o2 = self._make_order()
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=o1, token_number=1, date=yesterday,
        )
        # Should not raise
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=o2, token_number=1, date=self.today,
        )
        self.assertEqual(TokenOrder.objects.count(), 2)


class TestCreateTokenOrderView(TestCase, TokenFixtureMixin):

    def setUp(self):
        self._build_franchise_fixtures()
        self.client = Client()
        self.client.login(username="cashier1", password="pass")

    def _post(self, body=None):
        return self.client.post(
            reverse("create-token-order"),
            data=json.dumps(body or {}),
            content_type="application/json",
        )

    def test_creates_order_and_token(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["token_number"], 1)
        self.assertTrue(Order.objects.filter(id=data["order_id"]).exists())
        self.assertTrue(TokenOrder.objects.filter(token_number=1).exists())

    def test_sequential_tokens(self):
        r1 = self._post().json()
        r2 = self._post().json()
        r3 = self._post().json()
        self.assertEqual([r1["token_number"], r2["token_number"], r3["token_number"]], [1, 2, 3])

    def test_counter_value_matches_token(self):
        self._post()
        self._post()
        from core.utils import get_business_date
        counter = DailyTokenCounter.objects.get(outlet=self.outlet, date=get_business_date(timezone.now(), self.outlet))
        self.assertEqual(counter.value, 2)

    def test_customer_details_saved(self):
        resp = self._post({"customer_name": "Raju", "customer_phone": "9876543210"})
        data = resp.json()
        order = Order.objects.get(id=data["order_id"])
        self.assertEqual(order.customer_name, "Raju")
        self.assertEqual(order.customer_phone, "9876543210")

    def test_source_is_counter(self):
        resp = self._post()
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.source, "counter")

    def test_empty_customer_fields_become_none(self):
        resp = self._post({"customer_name": "  ", "customer_phone": ""})
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertIsNone(order.customer_name)
        self.assertIsNone(order.customer_phone)

    def test_unauthenticated_returns_redirect(self):
        c = Client()
        resp = c.post(reverse("create-token-order"), content_type="application/json")
        self.assertIn(resp.status_code, [302, 403])

    def test_fine_dining_tenant_blocked(self):
        self._build_fine_dining_fixtures()
        c = Client()
        c.login(username="fd_waiter", password="pass")
        resp = c.post(reverse("create-token-order"), content_type="application/json")
        # feature_required blocks with 403 or PermissionDenied (403)
        self.assertIn(resp.status_code, [403])


class TestTokenDashboardView(TestCase, TokenFixtureMixin):

    def setUp(self):
        self._build_franchise_fixtures()
        self._build_fine_dining_fixtures()
        self.client = Client()
        self.client.login(username="cashier1", password="pass")
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def test_dashboard_loads(self):
        resp = self.client.get(reverse("token-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_next_token_is_one_when_no_counter(self):
        resp = self.client.get(reverse("token-dashboard"))
        self.assertEqual(resp.context["next_token"], 1)

    def test_next_token_increments_when_counter_exists(self):
        DailyTokenCounter.objects.create(
            outlet=self.outlet, tenant=self.tenant,
            date=self.today, value=7,
        )
        resp = self.client.get(reverse("token-dashboard"))
        self.assertEqual(resp.context["next_token"], 8)

    def test_active_tokens_shown(self):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.user, status="open", source="counter",
        )
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=3, date=self.today,
        )
        resp = self.client.get(reverse("token-dashboard"))
        self.assertEqual(resp.context["active_tokens"].count(), 1)

    def test_closed_tokens_excluded_from_active(self):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.user, status="closed", source="counter",
        )
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=order, token_number=1, date=self.today,
        )
        resp = self.client.get(reverse("token-dashboard"))
        self.assertEqual(resp.context["active_tokens"].count(), 0)

    def test_fine_dining_blocked_by_feature_gate(self):
        c = Client()
        c.login(username="fd_waiter", password="pass")
        resp = c.get(reverse("token-dashboard"))
        self.assertEqual(resp.status_code, 403)


class TestTokenBillingView(TestCase, TokenFixtureMixin):

    def setUp(self):
        self._build_franchise_fixtures()
        self.client = Client()
        self.client.login(username="cashier1", password="pass")
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.user, status="open", source="counter",
        )
        from core.utils import get_business_date
        self.token = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=self.order, token_number=1, date=get_business_date(timezone.now(), self.outlet),
        )

    def test_billing_page_loads(self):
        resp = self.client.get(reverse("token-bill", args=[self.order.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["token"].token_number, 1)

    def test_only_available_items_in_context(self):
        resp = self.client.get(reverse("token-bill", args=[self.order.id]))
        for cat in resp.context["categories"]:
            for item in cat.items.all():
                self.assertTrue(item.is_available)

    def test_wrong_order_id_returns_404(self):
        resp = self.client.get(reverse("token-bill", args=[99999]))
        self.assertEqual(resp.status_code, 404)

    def test_cross_tenant_order_returns_404(self):
        """An order that belongs to another tenant must return 404."""
        # We don't need to create a real Order — querying a non-existent
        # ID owned by another tenant has the same effect and avoids the
        # DailyOrderCounter unique-constraint collision between tests.
        resp = self.client.get(reverse("token-bill", args=[999998]))
        self.assertEqual(resp.status_code, 404)

    def test_remaining_calculation(self):
        self.order.grand_total = Decimal("200")
        self.order.save()
        Payment.objects.create(order=self.order, method="cash", amount=Decimal("80"))
        resp = self.client.get(reverse("token-bill", args=[self.order.id]))
        self.assertEqual(resp.context["remaining"], Decimal("120"))

    def test_remaining_never_negative(self):
        """If grand_total is 0 and payment exists, remaining must be 0 not negative."""
        self.order.grand_total = Decimal("0")
        self.order.save()
        resp = self.client.get(reverse("token-bill", args=[self.order.id]))
        self.assertGreaterEqual(resp.context["remaining"], Decimal("0"))


class TestFeatureRequiredDecorator(TestCase, TokenFixtureMixin):

    def setUp(self):
        self._build_franchise_fixtures()
        self._build_fine_dining_fixtures()

    def test_franchise_can_access_token_dashboard(self):
        c = Client()
        c.login(username="cashier1", password="pass")
        resp = c.get(reverse("token-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_fine_dining_cannot_access_token_dashboard(self):
        c = Client()
        c.login(username="fd_waiter", password="pass")
        resp = c.get(reverse("token-dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_superuser_bypasses_feature_gate(self):
        su = User.objects.create_superuser(
            username="su", password="pass", email="su@test.com"
        )
        su.tenant = self.fd_tenant
        su.outlet = self.fd_outlet
        su.save()
        c = Client()
        c.login(username="su", password="pass")
        resp = c.get(reverse("token-dashboard"))
        # Superuser should not get 403 — may redirect or 200
        self.assertNotEqual(resp.status_code, 403)


class TestTokenConcurrency(TransactionTestCase):
    """Uses TransactionTestCase so each thread's transaction is isolated."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Concurrent Franchise", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Outlet C")
        self.user = User.objects.create_user(
            username="cc_cashier", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier",
        )
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            cash_enabled=True, upi_enabled=True, card_enabled=True,
        )
        CashSession.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            opened_by=self.user, opening_balance=0, status="open",
        )
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def _create_one_token(self, results, idx):
        from django.db import connection, transaction
        from core.utils import get_business_date
        try:
            with transaction.atomic():
                business_date = get_business_date(timezone.now(), self.outlet)
                counter, _ = (
                    DailyTokenCounter.objects
                    .select_for_update()
                    .get_or_create(
                        outlet=self.outlet,
                        tenant=self.tenant,
                        date=business_date,
                        defaults={"value": 0},
                    )
                )
                counter.value += 1
                counter.save(update_fields=["value"])
                results[idx] = counter.value
        except Exception as e:
            results[idx] = f"ERROR: {e}"
        finally:
            connection.close()

    def test_concurrent_tokens_are_unique(self):
        """
        10 simultaneous requests must each get a unique token number.
        If MAX()+1 were used, many would collide and crash.
        DailyTokenCounter row-lock serialises them.
        """
        n = 10
        results = [None] * n
        threads = [Thread(target=self._create_one_token, args=(results, i)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors
        errors = [r for r in results if isinstance(r, str) and r.startswith("ERROR")]
        self.assertEqual(errors, [], f"Concurrent errors: {errors}")

        # All unique
        self.assertEqual(len(set(results)), n, f"Duplicate tokens: {results}")

        # Counter reflects exact count
        counter = DailyTokenCounter.objects.get(outlet=self.outlet, date=self.today)
        self.assertEqual(counter.value, n)


# ======================================================================
#  SECTION 3 -- moved from orders/tests/test_critical_fixes.py
#  (CreateOrderSecurityTests, CustomerPhoneValidationTests,
#  NormalizePhoneUnitTests stayed behind there -- none are token-specific.
#  TenantIsolationTests there originally had TWO methods in one class, one
#  testing the token-bill view (moved here) and one testing an unrelated
#  bill-view (stayed) -- split rather than moved wholesale.)
# ======================================================================

from core.utils import get_business_date


def _franchise(name="Stress Franchise"):
    tenant = Tenant.objects.create(name=name, tenant_type="franchise")
    outlet = Outlet.objects.create(tenant=tenant, name="Outlet 1")
    PaymentConfig.objects.create(
        tenant=tenant, outlet=outlet,
        cash_enabled=True, upi_enabled=True, card_enabled=True,
    )
    return tenant, outlet


class CreateOrderTokenFixTests(TransactionTestCase):
    """The buggy code used MAX()+1 and never touched DailyTokenCounter.
    These assertions only pass once create_order delegates to
    assign_counter_token (which row-locks DailyTokenCounter)."""

    def setUp(self):
        self.tenant, self.outlet = _franchise("Counter Fix Franchise")
        self.cashier = User.objects.create_user(
            username="fix_cashier", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="cashier",
        )
        self.cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Burgers", is_active=True
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat,
            name="Zinger", price=Decimal("100"), is_available=True,
        )
        self.client = Client()
        self.client.force_login(self.cashier)

    def _create_order(self):
        return self.client.post(
            reverse("create-order"),
            data=json.dumps({"cart": [{"id": self.item.id, "quantity": 1}]}),
            content_type="application/json",
        )

    def test_create_order_creates_daily_token_counter_row(self):
        """Pre-fix code never created a DailyTokenCounter row. Post-fix it must."""
        resp = self._create_order()
        self.assertEqual(resp.status_code, 200, resp.content)

        business_date = get_business_date(timezone.now(), self.outlet)
        counter = DailyTokenCounter.objects.filter(
            tenant=self.tenant, outlet=self.outlet, date=business_date
        ).first()
        self.assertIsNotNone(
            counter,
            "create_order did not use DailyTokenCounter — still on MAX()+1?",
        )
        self.assertEqual(counter.value, 1)

    def test_tokens_are_sequential_and_counter_tracks_them(self):
        for expected in range(1, 6):
            resp = self._create_order()
            self.assertEqual(resp.status_code, 200, resp.content)
            order_id = resp.json()["order_id"]
            token = TokenOrder.objects.get(order_id=order_id)
            self.assertEqual(token.token_number, expected)

        business_date = get_business_date(timezone.now(), self.outlet)
        counter = DailyTokenCounter.objects.get(
            tenant=self.tenant, outlet=self.outlet, date=business_date
        )
        self.assertEqual(counter.value, 5)
        # No duplicate token numbers issued
        numbers = list(
            TokenOrder.objects.filter(outlet=self.outlet, is_online=False)
            .values_list("token_number", flat=True)
        )
        self.assertEqual(sorted(numbers), [1, 2, 3, 4, 5])


class TokenStressTests(TransactionTestCase):
    """Hammer the token helper from many threads. If the row-lock were
    removed (or MAX()+1 reintroduced), token numbers collide and the
    unique_together(order, ...) / count assertions fail."""

    def setUp(self):
        self.tenant, self.outlet = _franchise("Hammer Franchise")
        self.business_date = get_business_date(timezone.now(), self.outlet)

    def _one(self, results, idx):
        from django.db import connection, transaction
        from tokens.views import assign_counter_token
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    tenant=self.tenant, outlet=self.outlet, table=None,
                    status="open", source="counter",
                )
                tok = assign_counter_token(
                    order, self.outlet, self.tenant, self.business_date
                )
                results[idx] = tok.token_number
        except Exception as e:          # noqa: BLE001 — record for assertion
            results[idx] = f"ERROR: {e}"
        finally:
            connection.close()

    def test_stress_25_concurrent_tokens_unique(self):
        n = 25
        results = [None] * n
        threads = [Thread(target=self._one, args=(results, i)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        errors = [r for r in results if isinstance(r, str)]
        self.assertEqual(errors, [], f"Errors under load: {errors}")

        # Every token number unique and the set is exactly 1..n (gap-free)
        self.assertEqual(len(set(results)), n, f"Duplicate tokens: {results}")
        self.assertEqual(sorted(results), list(range(1, n + 1)))

        counter = DailyTokenCounter.objects.get(
            tenant=self.tenant, outlet=self.outlet, date=self.business_date
        )
        self.assertEqual(counter.value, n)

    def test_sequential_50_gap_free(self):
        from django.db import transaction
        from tokens.views import assign_counter_token
        for i in range(1, 51):
            with transaction.atomic():
                order = Order.objects.create(
                    tenant=self.tenant, outlet=self.outlet, table=None,
                    status="open", source="counter",
                )
                tok = assign_counter_token(
                    order, self.outlet, self.tenant, self.business_date
                )
                self.assertEqual(tok.token_number, i)


class TenantIsolationTests(TestCase):
    """Split from orders/tests/test_critical_fixes.py::TenantIsolationTests --
    only the token-bill half of that class; the bill-view half (unrelated
    to tokens) stayed behind there with its own trimmed fixture."""

    def setUp(self):
        self.t_a, self.o_a = _franchise("Tenant A Tok")
        self.t_b, self.o_b = _franchise("Tenant B Tok")

        self.user_a = User.objects.create_user(
            username="user_a_tok", password="pass",
            tenant=self.t_a, outlet=self.o_a, role="owner",
        )
        # An order that belongs to tenant B
        self.order_b = Order.objects.create(
            tenant=self.t_b, outlet=self.o_b, table=None,
            status="open", source="counter",
        )
        TokenOrder.objects.create(
            tenant=self.t_b, outlet=self.o_b, order=self.order_b,
            token_number=1, date=get_business_date(timezone.now(), self.o_b),
            is_online=False,
        )

    def test_cannot_open_other_tenants_token_bill(self):
        c = Client()
        c.force_login(self.user_a)
        resp = c.get(reverse("token-bill", args=[self.order_b.id]))
        self.assertEqual(
            resp.status_code, 404,
            "Tenant A was able to load Tenant B's token bill — ISOLATION BREACH",
        )


# ======================================================================
#  SECTION 4 -- moved from orders/tests/test_schema_review.py
# ======================================================================

def _field_names_in_indexes(model):
    return [tuple(idx.fields) for idx in model._meta.indexes]


class TestDailyCounterNoRedundantIndex(TestCase):

    def test_daily_token_counter_no_duplicate_index(self):
        field_sets = _field_names_in_indexes(DailyTokenCounter)
        self.assertNotIn(("outlet", "date"), field_sets,
                         "unique_together already creates this index — explicit one is redundant")

    def test_daily_online_token_counter_no_duplicate_index(self):
        field_sets = _field_names_in_indexes(DailyOnlineTokenCounter)
        self.assertNotIn(("outlet", "date"), field_sets,
                         "unique_together already creates this index — explicit one is redundant")


# ======================================================================
#  SECTION 5 -- new coverage for the reverse-direction dependency
#  (orders/api.py's aggregator webhook calls tokens.views.assign_online_token
#  for token_system tenants -- confirmed by grep this path had NO test
#  coverage anywhere before this move, in either app. test_aggregator_ingest.py
#  in orders/tests/ only ever used a default-type tenant, which never has the
#  token_system feature, so assign_online_token was never actually exercised
#  through the real HTTP endpoint by any existing test.)
# ======================================================================

import hashlib
import hmac
from unittest.mock import patch as _patch

from setup.models import AggregatorConfig

_WEBHOOK_SECRET = "test_zomato_secret_tokens"


def _sign_ingest_body(body_str, secret=_WEBHOOK_SECRET):
    return hmac.new(secret.encode(), body_str.encode(), hashlib.sha256).hexdigest()


class AggregatorIngestOnlineTokenTest(TestCase):
    """Cross-app proof: orders/api.py::api_ingest_order still finds and calls
    tokens.views.assign_online_token for a token_system tenant after the move."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Ingest Token Tenant", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")
        AggregatorConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            zomato_enabled=True, zomato_webhook_secret=_WEBHOOK_SECRET,
            auto_accept_orders=False,
        )
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Butter Naan", price=60,
        )
        patcher = _patch("orders.api.is_ip_allowed", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, payload_dict):
        body = json.dumps(payload_dict)
        sig = _sign_ingest_body(body)
        return Client().post(
            reverse("api-ingest-order"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=sig,
        )

    def test_online_token_assigned_for_franchise_tenant(self):
        resp = self._post({
            "tenant_id": self.tenant.id,
            "outlet_id": self.outlet.id,
            "source": "zomato",
            "aggregator_order_id": "AGG-TOKEN-1",
            "items": [{"menu_item_id": self.menu_item.id, "quantity": 1}],
        })
        self.assertEqual(resp.status_code, 200)
        order_id = resp.json()["order_id"]

        tok = TokenOrder.objects.get(order_id=order_id)
        self.assertTrue(tok.is_online)
        self.assertEqual(tok.token_number, 1)
        self.assertEqual(
            DailyOnlineTokenCounter.objects.get(outlet=self.outlet, date=get_business_date(timezone.now(), self.outlet)).value,
            1,
        )


# ======================================================================
#  QSR "Order Ready" feature: pickup readiness + public display board
# ======================================================================

class PickupReadinessTest(TestCase, TokenFixtureMixin):
    """mark_token_ready / mark_token_collected: role gating + state transitions."""

    def setUp(self):
        self._build_franchise_fixtures()
        self.client = Client()
        self.client.login(username="cashier1", password="pass")

        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="paid",
        )
        self.token = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=self.order,
            token_number=1, date=timezone.now().date(), is_online=False,
        )

    def test_mark_ready_sets_timestamp(self):
        resp = self.client.post(reverse("mark-token-ready", args=[self.token.id]))
        self.assertEqual(resp.status_code, 200)
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.ready_at)
        self.assertIsNone(self.token.collected_at)

    def test_mark_collected_sets_timestamp(self):
        self.token.ready_at = timezone.now()
        self.token.save(update_fields=["ready_at"])

        resp = self.client.post(reverse("mark-token-collected", args=[self.token.id]))
        self.assertEqual(resp.status_code, 200)
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.collected_at)

    def test_waiter_cannot_mark_ready(self):
        User.objects.create_user(
            username="waiter1", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="waiter",
        )
        self.client.logout()
        self.client.login(username="waiter1", password="pass")
        resp = self.client.post(reverse("mark-token-ready", args=[self.token.id]))
        self.assertEqual(resp.status_code, 403)
        self.token.refresh_from_db()
        self.assertIsNone(self.token.ready_at)

    def test_cannot_mark_ready_for_another_tenants_token(self):
        other_tenant = Tenant.objects.create(name="Other Franchise", tenant_type="franchise")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other Outlet")
        other_order = Order.objects.create(tenant=other_tenant, outlet=other_outlet, status="paid")
        other_token = TokenOrder.objects.create(
            tenant=other_tenant, outlet=other_outlet, order=other_order,
            token_number=1, date=timezone.now().date(), is_online=False,
        )
        resp = self.client.post(reverse("mark-token-ready", args=[other_token.id]))
        self.assertEqual(resp.status_code, 404)


class DisplayBoardDataTest(TestCase, TokenFixtureMixin):
    """
    display_data: the public polling endpoint behind the "Now Serving" TV
    board. Must return only ready, uncollected, non-stale tokens for the
    right outlet -- no prices or customer names.
    """

    def setUp(self):
        self._build_franchise_fixtures()
        from core.utils import get_business_date
        self.today = get_business_date(timezone.now(), self.outlet)

    def _make_token(self, token_number, ready_at="now", collected_at=None, status="paid"):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status=status)
        return TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            token_number=token_number, date=self.today, is_online=False,
            ready_at=timezone.now() if ready_at == "now" else ready_at,
            collected_at=collected_at,
        )

    def test_ready_uncollected_token_appears(self):
        self._make_token(1)
        resp = self.client.get(reverse("display-data", args=[self.outlet.display_token]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["tokens"]), 1)
        self.assertEqual(data["tokens"][0]["display"], "#1")

    def test_not_yet_ready_token_absent(self):
        self._make_token(2, ready_at=None)
        resp = self.client.get(reverse("display-data", args=[self.outlet.display_token]))
        self.assertEqual(len(resp.json()["tokens"]), 0)

    def test_collected_token_absent(self):
        self._make_token(3, collected_at=timezone.now())
        resp = self.client.get(reverse("display-data", args=[self.outlet.display_token]))
        self.assertEqual(len(resp.json()["tokens"]), 0)

    def test_stale_ready_token_absent(self):
        self._make_token(4, ready_at=timezone.now() - timedelta(minutes=30))
        resp = self.client.get(reverse("display-data", args=[self.outlet.display_token]))
        self.assertEqual(len(resp.json()["tokens"]), 0)

    def test_scoped_to_correct_outlet_only(self):
        other_tenant = Tenant.objects.create(name="Other Franchise2", tenant_type="franchise")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other Outlet2")
        other_order = Order.objects.create(tenant=other_tenant, outlet=other_outlet, status="paid")
        TokenOrder.objects.create(
            tenant=other_tenant, outlet=other_outlet, order=other_order,
            token_number=1, date=self.today, is_online=False, ready_at=timezone.now(),
        )
        self._make_token(5)

        resp = self.client.get(reverse("display-data", args=[self.outlet.display_token]))
        data = resp.json()
        self.assertEqual(len(data["tokens"]), 1)
        self.assertEqual(data["tokens"][0]["display"], "#5")

    def test_no_prices_or_customer_names_leaked(self):
        self._make_token(6)
        resp = self.client.get(reverse("display-data", args=[self.outlet.display_token]))
        body = json.dumps(resp.json())
        self.assertNotIn("price", body.lower())
        self.assertNotIn("customer", body.lower())

    def test_display_board_page_renders(self):
        resp = self.client.get(reverse("display-board", args=[self.outlet.display_token]))
        self.assertEqual(resp.status_code, 200)

    def test_invalid_display_token_404s(self):
        import uuid as _uuid
        resp = self.client.get(reverse("display-data", args=[_uuid.uuid4()]))
        self.assertEqual(resp.status_code, 404)

    def test_board_404s_for_tenant_without_token_system(self):
        # Mirrors call_waiter's inline has_feature check for public,
        # unauthenticated endpoints -- a fine-dining tenant has no tokens
        # to show even if this outlet's display_token were ever leaked.
        fd_tenant = Tenant.objects.create(name="FD No Tokens", tenant_type="fine_dining")
        fd_outlet = Outlet.objects.create(tenant=fd_tenant, name="Main")
        resp = self.client.get(reverse("display-board", args=[fd_outlet.display_token]))
        self.assertEqual(resp.status_code, 404)

    def test_data_403s_for_tenant_without_token_system(self):
        fd_tenant = Tenant.objects.create(name="FD No Tokens 2", tenant_type="fine_dining")
        fd_outlet = Outlet.objects.create(tenant=fd_tenant, name="Main")
        resp = self.client.get(reverse("display-data", args=[fd_outlet.display_token]))
        self.assertEqual(resp.status_code, 403)
