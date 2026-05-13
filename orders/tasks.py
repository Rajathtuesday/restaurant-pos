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
from django.core.cache import cache
from setup.models import KitchenStation
from orders.services.printing_service import PrintingService

logger = logging.getLogger("pos.printing")

_PRINTER_ERROR_TTL = 180  # seconds before auto-clearing the banner


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
        success = printer.print_kot(order, kot)

        if success:
            logger.info(
                "KOT #%s for Order #%s printed at station '%s'.",
                kot.kot_number, order.id, station.name
            )
            # Clear any stale error for this outlet
            cache.delete(f"printer_err_{order.outlet_id}")
            return True
        else:
            _store_printer_error(order.outlet_id, station.name, kot.kot_number, "Connection refused or timeout")
            return False

    except (KitchenStation.DoesNotExist, Order.DoesNotExist, KOTBatch.DoesNotExist) as e:
        logger.error("KOT print failed — record not found: %s", e)
        return False

    except Exception as e:
        logger.error("KOT #%s print failed: %s", kot_id, e)
        try:
            _store_printer_error(order.outlet_id, station.name, kot_id, str(e))
        except Exception:
            pass
        return False


def _store_printer_error(outlet_id, station_name, kot_number, detail):
    cache.set(
        f"printer_err_{outlet_id}",
        {
            "station": station_name,
            "kot": kot_number,
            "detail": detail,
        },
        _PRINTER_ERROR_TTL,
    )
    logger.warning(
        "Printer error stored for outlet %s — station '%s' KOT #%s: %s",
        outlet_id, station_name, kot_number, detail,
    )
