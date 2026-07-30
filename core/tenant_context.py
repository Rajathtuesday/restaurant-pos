"""
Per-request tenant/outlet context, the single source of truth that
TenantManager reads from to auto-scope queries (see core/models.py).

Backed by asgiref.local.Local() rather than a raw threading.local() --
already a transitive Django dependency (no new install), and it's what
Django itself uses for equivalent per-request state, so this stays
correct if the app ever moves toward ASGI/async views later.

Stores real values only: tenant_id is an int or None, never a sentinel
string. The 'NA' placeholder some log lines show is a display-only
concern, handled in log_filters.py, not stored here -- storing a
non-None sentinel here would make "no tenant" indistinguishable from
"tenant id happens to be falsy", which is exactly the kind of ambiguity
a query-filtering source of truth can't afford.

Set and cleared in exactly one place: ContextLoggingMiddleware, wrapped
in try/finally, so a value can never survive past the request that set
it even if the view raises. See core/middleware.py.
"""
from asgiref.local import Local

_local = Local()


def set_current_tenant_outlet(tenant_id, outlet_id):
    _local.tenant_id = tenant_id
    _local.outlet_id = outlet_id


def clear_current_tenant_outlet():
    _local.tenant_id = None
    _local.outlet_id = None


def get_current_tenant_id():
    return getattr(_local, "tenant_id", None)


def get_current_outlet_id():
    return getattr(_local, "outlet_id", None)
