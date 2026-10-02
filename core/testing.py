"""
Helpers for tests across the apps.

Tenant scoping: TenantManager (core/models.py) auto-scopes every query to
whatever tenant core.tenant_context currently holds, normally populated by
ContextLoggingMiddleware from request.user.tenant for the duration of one
real HTTP request. A test that builds fixtures and asserts on querysets
directly (no HTTP request) never goes through that middleware, so the
context is unset and every query returns unfiltered, as in a Celery task or
management command. That is right for most tests, but wrong for anything
testing the tenant-scoping guarantee itself: as_tenant and
TenantScopedTestCase set the context for them.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from core.tenant_context import clear_current_tenant_outlet, set_current_tenant_outlet


def freeze_ratelimit_clock(test):
    """django_ratelimit counts in fixed one-minute windows. Twenty real
    requests on a busy machine can take long enough to cross into the next
    window, which starts the count again and lets the next request through
    (seen in a full-suite run on 1 Oct 2026). Its clock, and only its clock,
    is held still for the test."""
    patcher = patch("django_ratelimit.core.time", SimpleNamespace(time=lambda: 1_800_000_000))
    patcher.start()
    test.addCleanup(patcher.stop)


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
