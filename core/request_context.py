"""
Per-request trace_id, kept deliberately separate from tenant_context.py.

tenant_context.py is a correctness-critical query-scoping concern --
TenantManager reads it to auto-scope every TenantScopedModel query, and
that responsibility shouldn't grow to carry unrelated fields. trace_id is
purely an observability concern (which log lines belong to the same
request/task chain), so it lives here instead -- future logging-only
fields (e.g. a captured request path) belong in this file too, not in
tenant_context.py.

Backed by asgiref.local.Local() for the same reason tenant_context.py is:
already a transitive Django dependency, and it's what Django itself uses
for equivalent per-request state, so this stays correct under ASGI/async
views too.

Set and cleared in exactly one place for HTTP requests -- ContextLogging-
Middleware, wrapped in try/finally -- and set/cleared per task by the
task_prerun/task_postrun Celery signal handlers in core/celery.py, so a
value can never survive past the request or task that set it.
"""
from asgiref.local import Local

_local = Local()


def set_current_trace_id(trace_id):
    _local.trace_id = trace_id


def clear_current_trace_id():
    _local.trace_id = None


def get_current_trace_id():
    return getattr(_local, "trace_id", None)
