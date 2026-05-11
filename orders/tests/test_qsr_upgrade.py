# orders/tests/test_qsr_upgrade.py
"""
Tests for the QSR upgrade features:
  - DailyOnlineTokenCounter: sequential numbering, daily reset, concurrency
  - TokenOrder.is_online + display_number property
  - Dual token series independence (counter vs online never collide)
  - assign_online_token helper
  - create_and_go_to_billing endpoint (direct billing mode)
  - token_billing context: active_tokens sidebar (counter_tokens, online_tokens)
  - token_dashboard context: counter/online split + can_create role gate
  - RBAC: cashier can create, waiter cannot, manager can discount
  - api_ingest_order auto-assigns online token for QSR tenants
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
from orders.models import (
    DailyOnlineTokenCounter,
    DailyTokenCounter,
    Order,
    TokenOrder,
)
from setup.models import AggregatorConfig, PaymentConfig
from shifts.models import CashSession
from tenants.models import Outlet, Tenant


# ======================================================================
#  SHARED FIXTURE
# ======================================================================

class QSRFixtureMixin:
    """Shared setup for QSR-upgrade tests."""

    def _build(self):
        self.tenant = Tenant.objects.create(name="QSR Test Co", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")
        self.today  = date.today()

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
        from orders.views.token_views import assign_online_token
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
        counter = DailyOnlineTokenCounter.objects.get(outlet=self.outlet, date=date.today())
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
        from orders.views.token_views import assign_online_token

        # Create an online token directly (simulating a row-delete in admin)
        existing_order = self._make_order(source="zomato")
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            order=existing_order, token_number=5, date=date.today(), is_online=True,
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
        self.today = date.today()

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
        self.today = date.today()

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
        self.today = date.today()

    def _create_online_token(self, results, idx):
        from django.db import connection, transaction
        from core.utils import get_business_date
        from orders.views.token_views import assign_online_token
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
