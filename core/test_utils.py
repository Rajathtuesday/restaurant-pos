"""
Shared test infrastructure for tenant-isolation-aware tests.

TenantManager (core/models.py) auto-scopes every query to whatever
tenant core.tenant_context currently holds -- normally populated by
ContextLoggingMiddleware from request.user.tenant, for the duration of
one real HTTP request. A test that builds fixtures and asserts on
queryset results directly (no HTTP request involved) never goes through
that middleware, so without help, the context is simply unset -- every
query returns unfiltered, same as a Celery task or management command.
That's correct for tests that WANT that (most existing tests do), but
wrong for anything specifically testing the tenant-scoping guarantee
itself.
"""
from contextlib import contextmanager

from django.test import TestCase

from core.tenant_context import set_current_tenant_outlet, clear_current_tenant_outlet


@contextmanager
def as_tenant(tenant, outlet=None):
    """
    Runs the wrapped block as if it were a request authenticated as a
    user of `tenant` (and `outlet`, if given) -- the same context
    ContextLoggingMiddleware sets for a real request. Cleared on exit
    even if the block raises.

    Use this directly in a test method when you need to check behavior
    under more than one tenant's context in the same test (e.g. "as
    tenant A, see only A's row; as tenant B, see only B's row").
    """
    set_current_tenant_outlet(tenant.id, outlet.id if outlet else None)
    try:
        yield
    finally:
        clear_current_tenant_outlet()


class TenantScopedTestCase(TestCase):
    """
    Base TestCase that keeps the tenant-scoping context set for the
    whole test method, the way a real authenticated request would see
    it for its whole lifetime, not just a single block.

    Subclasses must set self.tenant (and may set self.outlet) in their
    own setUp() BEFORE calling super().setUp():

        class MyTest(TenantScopedTestCase):
            def setUp(self):
                self.tenant = Tenant.objects.create(...)
                super().setUp()
    """
    tenant = None
    outlet = None

    def setUp(self):
        super().setUp()
        if self.tenant is not None:
            set_current_tenant_outlet(
                self.tenant.id,
                self.outlet.id if self.outlet else None,
            )

    def tearDown(self):
        clear_current_tenant_outlet()
        super().tearDown()
