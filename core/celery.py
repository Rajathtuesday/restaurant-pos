import os
from celery import Celery
from celery.signals import task_prerun, task_postrun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

app = Celery("rasova")

# Read config from Django settings, all keys prefixed with CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in every installed app's tasks.py
app.autodiscover_tasks()


# Registered once, here, so that any task dispatched via
# core.celery_utils.dispatch() automatically gets the trace_id from its
# triggering request set for the duration of its run, and cleared after --
# no individual task has to know this exists or do anything itself.
@task_prerun.connect
def _set_trace_id_for_task(task_id=None, task=None, **kwargs):
    from core.request_context import set_current_trace_id
    headers = getattr(task.request, "headers", None) or {}
    set_current_trace_id(headers.get("trace_id"))


@task_postrun.connect
def _clear_trace_id_after_task(**kwargs):
    from core.request_context import clear_current_trace_id
    clear_current_trace_id()
