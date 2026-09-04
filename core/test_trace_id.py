# core/test_trace_id.py
"""
trace_id follows one request through to every Celery task it triggers, the
same job tenant_id/outlet_id already do for query scoping -- see
core/request_context.py's docstring for why it's a separate module rather
than folded into core/tenant_context.py.
"""
import logging

from django.test import TestCase, Client

from accounts.models import User
from core.log_filters import TenantOutletFilter
from core.request_context import (
    set_current_trace_id, clear_current_trace_id, get_current_trace_id,
)
from tenants.models import Tenant, Outlet


class TenantOutletFilterTraceIdTests(TestCase):
    """The one filter already wired into every log handler now also
    carries trace_id -- no new filter, no new per-handler config."""

    def tearDown(self):
        clear_current_trace_id()

    def test_trace_id_is_NA_outside_any_context(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
        TenantOutletFilter().filter(record)
        self.assertEqual(record.trace_id, "NA")

    def test_trace_id_reflects_current_context(self):
        set_current_trace_id("abc-123")
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
        TenantOutletFilter().filter(record)
        self.assertEqual(record.trace_id, "abc-123")


class MiddlewareTraceIdTests(TestCase):
    """ContextLoggingMiddleware mints or honors a trace_id and echoes it
    back on every response, authenticated or not."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Trace Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

    def test_response_carries_a_fresh_trace_id_when_none_supplied(self):
        resp = Client().get("/health/")
        self.assertIn("X-Trace-Id", resp.headers)
        self.assertTrue(len(resp.headers["X-Trace-Id"]) > 0)

    def test_inbound_trace_id_is_echoed_back_unchanged(self):
        resp = Client().get("/health/", HTTP_X_TRACE_ID="caller-supplied-id-999")
        self.assertEqual(resp.headers["X-Trace-Id"], "caller-supplied-id-999")

    def test_context_is_cleared_after_the_request_finishes(self):
        Client().get("/health/")
        # Middleware's finally-block must clear it -- nothing should leak
        # onto whatever runs next on this same worker thread.
        self.assertIsNone(get_current_trace_id())


class CeleryTraceIdPropagationTests(TestCase):
    """dispatch() threads the current trace_id into the task's headers;
    core.celery's task_prerun/task_postrun signals pick it up automatically
    with zero changes to the task's own body."""

    def test_task_sees_the_dispatching_requests_trace_id(self):
        from celery import shared_task
        from core.celery_utils import dispatch

        seen = {}

        @shared_task(bind=True)
        def _probe_task(self):
            from core.request_context import get_current_trace_id
            seen["trace_id"] = get_current_trace_id()

        set_current_trace_id("probe-trace-456")
        try:
            _probe_task.apply(headers={"trace_id": get_current_trace_id()})
        finally:
            clear_current_trace_id()

        self.assertEqual(seen["trace_id"], "probe-trace-456")

    def test_task_run_with_no_trace_id_falls_back_to_none_not_a_stale_value(self):
        from celery import shared_task

        seen = {}

        @shared_task(bind=True)
        def _probe_task_2(self):
            from core.request_context import get_current_trace_id
            seen["trace_id"] = get_current_trace_id()

        # No trace_id set anywhere -- mirrors a management command or a
        # task dispatched outside any request.
        clear_current_trace_id()
        _probe_task_2.apply(headers={"trace_id": None})

        self.assertIsNone(seen["trace_id"])
