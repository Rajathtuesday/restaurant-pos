import threading
import logging

# Thread-local storage for tenant/outlet context
_thread_locals = threading.local()

def set_current_tenant_outlet(tenant_id, outlet_id):
    _thread_locals.tenant_id = tenant_id
    _thread_locals.outlet_id = outlet_id

def clear_current_tenant_outlet():
    if hasattr(_thread_locals, 'tenant_id'):
        del _thread_locals.tenant_id
    if hasattr(_thread_locals, 'outlet_id'):
        del _thread_locals.outlet_id

class TenantOutletFilter(logging.Filter):
    """
    Logging filter that injects tenant_id and outlet_id into log records.
    """
    def filter(self, record):
        record.tenant_id = getattr(_thread_locals, 'tenant_id', 'NA')
        record.outlet_id = getattr(_thread_locals, 'outlet_id', 'NA')
        return True
