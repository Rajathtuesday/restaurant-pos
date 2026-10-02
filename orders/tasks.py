from celery import shared_task

# ── Public demo tenant upkeep ───────────────────────────────────────────────
# Lives in a tasks.py because Celery's autodiscover_tasks() only scans each
# app's tasks.py, not orders/services/demo_seed.py directly. (The printing
# tasks that used to share this file are in printing/tasks.py.)
@shared_task(name="orders.tasks.reset_demo_tenant_task")
def reset_demo_tenant_task():
    """Re-seeds the public /live-demo/ tenant on a schedule, so whatever a
    visitor changed (new orders, bills) never accumulates for the next one.
    Menu/tables/owner are get_or_create and untouched; see demo_seed.py."""
    from orders.services.demo_seed import create_or_reset_demo_tenant
    create_or_reset_demo_tenant()
