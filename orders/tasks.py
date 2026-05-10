# orders/tasks.py
# ============================================================
# Celery removed (BUG-002):
#   There is no celery.py, no broker, and no worker process.
#   @shared_task queues silently — nothing fires — KOTs never print.
#   Changed to a plain function. Add Celery back after first live day.
#
# Station → KitchenStation (BUG-001):
#   setup.models has KitchenStation, not Station.
#   The old import would crash with ImportError the first time
#   any KOT print was attempted.
# ============================================================

import logging
from setup.models import KitchenStation
from orders.services.printing_service import PrintingService

logger = logging.getLogger("pos.printing")


def print_kot_task(station_id, order_id, kot_id):
    """
    Synchronously prints a KOT to a network thermal printer.
    Called directly from kot_service after transaction commit.
    Returns True on success, False on failure — never raises.
    """
    from orders.models import Order, KOTBatch

    try:
        station = KitchenStation.objects.get(id=station_id)
        order   = Order.objects.get(id=order_id)
        kot     = KOTBatch.objects.get(id=kot_id)

        if not station.printer_ip:
            logger.warning(
                "Station '%s' has no printer IP configured. KOT #%s skipped.",
                station.name, kot.kot_number
            )
            return False

        printer = PrintingService(
            printer_type="network",
            host=station.printer_ip,
            port=getattr(station, "printer_port", 9100),
        )
        printer.print_kot(order, kot)

        logger.info(
            "KOT #%s for Order #%s printed at station '%s'.",
            kot.kot_number, order.id, station.name
        )
        return True

    except (KitchenStation.DoesNotExist, Order.DoesNotExist, KOTBatch.DoesNotExist) as e:
        logger.error("KOT print failed — record not found: %s", e)
        return False

    except Exception as e:
        logger.error("KOT #%s print failed: %s", kot_id, e)
        return False
