import logging

from core.tenant_context import get_current_tenant_id, get_current_outlet_id
from core.request_context import get_current_trace_id


class TenantOutletFilter(logging.Filter):
    """
    Logging filter that injects tenant_id, outlet_id, and trace_id into
    log records. tenant_id/outlet_id read from core.tenant_context, the
    same source of truth TenantManager uses for query scoping; trace_id
    reads from core.request_context, a separate observability-only store
    -- 'NA' here is a display-only fallback for "no context set" (Celery
    tasks with no propagated trace_id, management commands, pre-auth
    requests before the middleware runs), not a value that's ever
    actually stored.
    """
    def filter(self, record):
        record.tenant_id = get_current_tenant_id() or 'NA'
        record.outlet_id = get_current_outlet_id() or 'NA'
        record.trace_id = get_current_trace_id() or 'NA'
        return True
