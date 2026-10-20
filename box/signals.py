"""
Two things a box does at the moment a row is saved.

New orders and the licence: on a box whose licence has run out (past the
grace days), a NEW order can't start. Done at the one place every order is born (Order's pre_save
for a new row), so it holds for the billing screen, tokens, QSR and any
future path alike. An order already open is never touched: it can still
get dishes, be billed and be paid.

The print queue: a new print job is printed by the box's own worker
(box/print_queue.py); in the cloud it waits for a device in the restaurant.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from orders.exceptions import OrderError
from orders.models import Order
from printing.models import PrintJob

logger = logging.getLogger("pos.printing")


class LicenceEnded(OrderError):
    """The box's licence has ended; says what to do."""


@receiver(pre_save, sender=Order, dispatch_uid="box_licence_new_orders")
def refuse_new_orders_without_licence(sender, instance, **kwargs):
    from box.edition import is_box
    if not is_box() or not instance._state.adding:
        return
    from box import license
    state = license.current()
    if state.blocks_new_orders:
        raise LicenceEnded(state.message)


@receiver(post_save, sender=PrintJob, dispatch_uid="box_prints_its_own_queue")
def print_new_jobs_on_the_box(sender, instance, created, **kwargs):
    from box.edition import is_box
    if not created or not is_box() or instance.status != PrintJob.PENDING:
        return
    job_id = instance.pk

    def send():
        from box.print_queue import print_job
        from box.tasks import print_queued_job
        from core.celery_utils import dispatch
        try:
            dispatch(print_queued_job, job_id)
        except Exception as exc:  # no worker queue reachable: print from here, as KOTs do
            logger.warning("Box print job %s: queue unavailable, printing now (%s)", job_id, exc)
            print_job(job_id)

    transaction.on_commit(send)
