import logging
from celery import shared_task
from django.core.cache import cache
from setup.models import KitchenStation
from orders.services.printing_service import PrintingService

logger = logging.getLogger("pos.printing")

_PRINTER_ERROR_TTL = 180  # seconds before auto-clearing the banner


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    queue="printing",
    name="orders.tasks.print_kot_task",
)
def print_kot_task(self, station_id, order_id, kot_id):
    """
    Print a KOT to a network thermal printer.
    Retries twice (5-second gap) before giving up and storing a banner error.
    """
    from orders.models import Order, KOTBatch

    try:
        station = KitchenStation.objects.get(id=station_id)
        order   = Order.objects.get(id=order_id)
        kot     = KOTBatch.objects.get(id=kot_id)

        if not station.printer_ip:
            logger.warning(
                "Station '%s' has no printer IP — KOT #%s skipped.",
                station.name, kot.kot_number,
            )
            return False

        printer = PrintingService(
            printer_type="network",
            host=station.printer_ip,
            port=station.printer_port,
            chars_per_line=station.chars_per_line,
            cut_type=station.cut_type,
            encoding=station.printer_encoding,
        )
        success = printer.print_kot(order, kot)

        if success:
            logger.info(
                "KOT #%s for Order #%s printed at station '%s'.",
                kot.kot_number, order.id, station.name,
            )
            cache.delete(f"printer_err_{order.outlet_id}")
            return True

        _store_printer_error(
            order.outlet_id, station.name, kot.kot_number, "Connection refused or timeout"
        )
        raise self.retry(exc=Exception("Print failed"))

    except (KitchenStation.DoesNotExist, Order.DoesNotExist, KOTBatch.DoesNotExist) as exc:
        logger.error("KOT print failed — record not found: %s", exc)
        return False

    except self.MaxRetriesExceededError:
        logger.error("KOT #%s gave up after %s retries.", kot_id, self.max_retries)
        return False

    except Exception as exc:
        logger.error("KOT #%s print error: %s", kot_id, exc)
        try:
            from orders.models import Order as O
            outlet_id = O.objects.filter(id=order_id).values_list("outlet_id", flat=True).first()
            if outlet_id:
                _store_printer_error(outlet_id, str(station_id), kot_id, str(exc))
        except Exception:
            pass
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    queue="printing",
    name="orders.tasks.print_bill_task",
)
def print_bill_task(self, order_id, station_id):
    """
    Print bill + all KOTs after payment.

    strip_mode is auto-detected:
      - no kitchen_display feature → QSR strip (token receipt + KOTs as one connected strip,
        partial cuts between sections, single full cut at the very end)
      - kitchen_display present   → fine-dining (bill full cut, KOTs partial cuts)
    """
    from orders.models import Order, KOTBatch
    from core.features import has_feature

    try:
        station = KitchenStation.objects.get(id=station_id)
        order   = Order.objects.prefetch_related("items", "payments").get(id=order_id)
        kots    = list(
            KOTBatch.objects
            .filter(order=order)
            .prefetch_related("items__menu_item", "items__modifiers")
            .select_related("station")
            .order_by("kot_number")
        )

        if not station.printer_ip:
            logger.warning("No printer IP on station '%s' — bill print skipped.", station.name)
            return False

        # QSR strip mode: no KDS → receipt + KOTs are one connected strip
        strip_mode = not has_feature(order.tenant, "kitchen_display")

        printer = PrintingService(
            printer_type="network",
            host=station.printer_ip,
            port=station.printer_port,
            chars_per_line=station.chars_per_line,
            cut_type=station.cut_type,
            encoding=station.printer_encoding,
        )
        success = printer.print_bill_with_kots(order, kots, strip_mode=strip_mode)

        if success:
            logger.info("Bill + %d KOT(s) printed for Order #%s.", len(kots), order_id)
            cache.delete(f"printer_err_{order.outlet_id}")
            return True

        _store_printer_error(order.outlet_id, station.name, "bill", "Connection refused")
        raise self.retry(exc=Exception("Bill print failed"))

    except (KitchenStation.DoesNotExist, Order.DoesNotExist) as exc:
        logger.error("Bill print failed — record not found: %s", exc)
        return False

    except self.MaxRetriesExceededError:
        logger.error("Bill print for Order #%s gave up after retries.", order_id)
        return False

    except Exception as exc:
        logger.error("Bill print for Order #%s error: %s", order_id, exc)
        raise self.retry(exc=exc)


def _store_printer_error(outlet_id, station_name, kot_number, detail):
    cache.set(
        f"printer_err_{outlet_id}",
        {"station": station_name, "kot": kot_number, "detail": detail},
        _PRINTER_ERROR_TTL,
    )
    logger.warning(
        "Printer error stored — outlet %s, station '%s', KOT #%s: %s",
        outlet_id, station_name, kot_number, detail,
    )
