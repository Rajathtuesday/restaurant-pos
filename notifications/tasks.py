import logging
from celery import shared_task

logger = logging.getLogger("pos.notifications")


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    name="notifications.tasks.send_whatsapp_receipt_task",
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_whatsapp_receipt_task(self, order_id, bill_url):
    """
    Sends a WhatsApp bill receipt off the request/response cycle.

    Previously this ran synchronously inside payment_views.py's
    transaction.on_commit() — the Meta/Twilio HTTP call (up to an 8s timeout)
    was blocking the cashier's "payment complete" response. No new Celery
    queue is introduced; this lands on the default queue like everything
    else not explicitly routed to "printing".
    """
    from orders.models import Order
    from notifications.services.whatsapp_service import send_bill_receipt

    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist:
        logger.warning("send_whatsapp_receipt_task: order %s no longer exists", order_id)
        return

    send_bill_receipt(order, bill_url)
