"""
Proves the actual tenant-isolation guarantee: with a tenant set in
core.tenant_context (the way ContextLoggingMiddleware sets it from
request.user.tenant for every real request), a query against a
TenantScopedModel must return only that tenant's rows -- never another
tenant's, even when nothing in the query itself mentions tenant.

These run against TODAY's TenantManager, which does NOT auto-filter yet
(core/models.py:13, get_queryset() returns everything unfiltered --
filtering is opt-in via .for_tenant()/.for_outlet(), which most call
sites don't use). test_cross_tenant_rows_are_not_visible is therefore
EXPECTED TO FAIL right now -- that failure is the actual proof this
codebase has the gap the tenant-isolation plan exists to close. It must
start passing, unmodified, the moment core/models.py's get_queryset()
is rewritten to auto-scope from core.tenant_context (plan step 7). Do
not "fix" this test to make it pass before that change lands.

Representative models, one per app that owns TenantScopedModel rows in
a meaningfully different shape (tenant+outlet vs tenant-only, a
OneToOne, a FK chain): Table, Order, MenuItem, InventoryItem, Guest,
TokenOrder, PaymentConfig.
"""
from datetime import date
from decimal import Decimal

from crm.models import Guest
from inventory.models import InventoryItem
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from setup.models import PaymentConfig
from tenants.models import Outlet, Tenant
from tokens.models import TokenOrder

from core.test_utils import TenantScopedTestCase, as_tenant


class CrossTenantLeakTest(TenantScopedTestCase):
    """
    tenant/outlet A is "the current request's tenant" for every test in
    this class (set up by TenantScopedTestCase.setUp). tenant/outlet B
    exists only as the other tenant whose rows must stay invisible.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Isolation Test Tenant A", slug="iso-test-a")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

        self.tenant_b = Tenant.objects.create(name="Isolation Test Tenant B", slug="iso-test-b")
        self.outlet_b = Outlet.objects.create(tenant=self.tenant_b, name="Main")

        super().setUp()  # sets current context to self.tenant/self.outlet

        self.table_a = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.table_b = Table.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, name="T1")

        self.order_a = Order.objects.create(tenant=self.tenant, outlet=self.outlet, table=self.table_a)
        self.order_b = Order.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, table=self.table_b)

        cat_a = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Starters")
        cat_b = MenuCategory.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, name="Starters")
        self.item_a = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=cat_a, name="Paneer Tikka", price=Decimal("199.00"))
        self.item_b = MenuItem.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, category=cat_b, name="Paneer Tikka", price=Decimal("199.00"))

        self.inv_a = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name="Rice", unit="kg")
        self.inv_b = InventoryItem.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, name="Rice", unit="kg")

        self.guest_a = Guest.objects.create(tenant=self.tenant, phone="9999900001")
        self.guest_b = Guest.objects.create(tenant=self.tenant_b, phone="9999900001")

        self.token_a = TokenOrder.objects.create(tenant=self.tenant, outlet=self.outlet, order=self.order_a, token_number=1, date=date.today())
        self.token_b = TokenOrder.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, order=self.order_b, token_number=1, date=date.today())

        self.paycfg_a = PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet)
        self.paycfg_b = PaymentConfig.objects.create(tenant=self.tenant_b, outlet=self.outlet_b)

    def test_cross_tenant_rows_are_not_visible(self):
        """
        The core guarantee. With tenant A as the current context, a bare
        .objects.all() must return ONLY tenant A's row for every
        representative model -- tenant B's row must never appear, even
        though nothing here filters by tenant explicitly.

        FAILS against today's manager (returns both A and B's rows).
        Must pass, unmodified, once auto-scoping lands.
        """
        cases = [
            (Table, self.table_a, self.table_b),
            (Order, self.order_a, self.order_b),
            (MenuItem, self.item_a, self.item_b),
            (InventoryItem, self.inv_a, self.inv_b),
            (Guest, self.guest_a, self.guest_b),
            (TokenOrder, self.token_a, self.token_b),
            (PaymentConfig, self.paycfg_a, self.paycfg_b),
        ]
        for model, row_a, row_b in cases:
            with self.subTest(model=model.__name__):
                visible_ids = set(model.objects.all().values_list("pk", flat=True))
                self.assertIn(row_a.pk, visible_ids, f"{model.__name__}: own tenant's row missing")
                self.assertNotIn(row_b.pk, visible_ids, f"{model.__name__}: other tenant's row leaked")

    def test_explicit_filter_for_other_tenant_returns_empty_not_error(self):
        # Deliberately crossing tenants via an explicit filter (the
        # .for_tenant()/.for_outlet() escape hatch, or a manual
        # tenant=... kwarg) must behave like a normal empty queryset,
        # not raise -- this must hold both before and after the flip.
        self.assertFalse(Table.objects.filter(tenant=self.tenant_b, pk=self.table_b.pk).filter(tenant=self.tenant).exists())
        self.assertTrue(Table.objects.for_tenant(self.tenant_b).filter(pk=self.table_b.pk).exists())

    def test_unset_context_sees_everything_unfiltered(self):
        # A Celery task or management command (no request, no
        # middleware, context never set) must keep seeing every
        # tenant's rows -- unrelated to and unaffected by the flip.
        from core.tenant_context import clear_current_tenant_outlet
        clear_current_tenant_outlet()
        try:
            visible_ids = set(Table.objects.all().values_list("pk", flat=True))
            self.assertIn(self.table_a.pk, visible_ids)
            self.assertIn(self.table_b.pk, visible_ids)
        finally:
            # restore so tearDown's own clear_current_tenant_outlet() call
            # isn't the only thing standing between this test and a leak
            from core.tenant_context import set_current_tenant_outlet
            set_current_tenant_outlet(self.tenant.id, self.outlet.id)

    def test_as_tenant_context_manager_switches_correctly(self):
        # Proves the as_tenant() helper itself: same query, different
        # current tenant, different visible row -- this is the primitive
        # every other isolation test in the suite will build on.
        with as_tenant(self.tenant_b, self.outlet_b):
            visible_ids = set(Table.objects.all().values_list("pk", flat=True))
        self.assertIn(self.table_b.pk, visible_ids)
