import logging
from celery import shared_task

logger = logging.getLogger("pos.printing")

@shared_task(bind=True, max_retries=3)
def print_kot_task(self, station_id, order_id, kot_id):
    """
    Background task to print a KOT to a network thermal printer.
    Retries up to 3 times on failure.
    """
    from orders.models import Order, KOTBatch
    from setup.models import Station
    from orders.services.printing_service import PrintingService

    try:
        station = Station.objects.get(id=station_id)
        order = Order.objects.get(id=order_id)
        kot = KOTBatch.objects.get(id=kot_id)

        if not station.printer_ip:
            logger.warning(f"Station {station.name} has no printer IP. Skipping print task.")
            return False

        printer = PrintingService(
            printer_type="network", 
            host=station.printer_ip, 
            port=station.printer_port
        )
        printer.print_kot(order, kot)
        
        logger.info(f"Successfully printed KOT #{kot.kot_number} for Order #{order.id} at {station.name}")
        return True

    except (Station.DoesNotExist, Order.DoesNotExist, KOTBatch.DoesNotExist) as e:
        logger.error(f"Failed to find required records for printing KOT: {e}")
        return False
        
    except Exception as e:
        logger.error(f"Auto-printing KOT #{kot_id} failed: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
