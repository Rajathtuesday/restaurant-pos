"""
dispatch() is the one thing every Celery call site should use instead of
.delay() directly -- it threads the current trace_id through as a task
header, which core.celery's task_prerun/task_postrun signal handlers pick
up automatically. Using .delay() instead of dispatch() still works, the
task just runs with no trace_id (falls back to 'NA' in logs), same as any
task triggered outside a request (a management command, a scheduled job).
"""
from core.request_context import get_current_trace_id


def dispatch(task, *args, **kwargs):
    return task.apply_async(
        args=args, kwargs=kwargs,
        headers={"trace_id": get_current_trace_id()},
    )
