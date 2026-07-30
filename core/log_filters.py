import logging

from core.tenant_context import get_current_tenant_id, get_current_outlet_id


class TenantOutletFilter(logging.Filter):
    """
    Logging filter that injects tenant_id and outlet_id into log records.
    Reads from core.tenant_context, the same source of truth TenantManager
    uses for query scoping -- 'NA' here is a display-only fallback for
    "no tenant in this context" (Celery tasks, management commands,
    pre-auth requests), not a value that's ever actually stored.
    """
    def filter(self, record):
        record.tenant_id = get_current_tenant_id() or 'NA'
        record.outlet_id = get_current_outlet_id() or 'NA'
        return True
