"""
Celery tasks that belong to no one app.
"""
from celery import shared_task
from django.utils import timezone


@shared_task(name="core.tasks.heartbeat")
def heartbeat():
    """Beat queues this every minute and the worker runs it. Its result row
    (django_celery_results) is how /health/ knows both are alive
    (core/health.py, check_heartbeat)."""
    return timezone.now().isoformat()
