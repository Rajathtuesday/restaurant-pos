from escpos.printer import Network, Usb
from django.conf import settings
import logging

logger = logging.getLogger("pos.printing")

class PrintingService:
    def __init__(self, printer_type="network", host=None, port=9100, vendor_id=None, product_id=None):
        self.printer_type = printer_type
        self.host = host
        self.port = port
        self.vendor_id = vendor_id
        self.product_id = product_id

    def get_printer(self, host=None, port=None):
        try:
            target_host = host or self.host
            target_port = port or self.port
            if self.printer_type == "network" and target_host:
                return Network(target_host, port=target_port)
            elif self.printer_type == "usb" and self.vendor_id:
                return Usb(self.vendor_id, self.product_id)
            return None
        except Exception as e:
            logger.error(f"Printer connection failed: {e}")
            return None

    def print_kot(self, order, kot_batch):
        """Prints a Kitchen Order Ticket."""
        p = self.get_printer()
        if not p: return False
        
        try:
            p.set(align='center', text_type='B', width=2, height=2)
            p.text(f"KOT #{kot_batch.kot_number}\n")
            p.set(align='center', text_type='B', width=1, height=1)
            p.text(f"Table: {order.table.name if order.table else 'Walk-in'}\n")
            p.text("-" * 32 + "\n")
            
            p.set(align='left')
            for item in kot_batch.items.all():
                p.text(f"{item.quantity} x {item.menu_item.name}\n")
                if item.notes:
                    p.text(f"  * {item.notes}\n")
                # Print modifiers
                for mod in item.modifiers.all():
                    p.text(f"    + {mod.name}\n")

            p.text("-" * 32 + "\n")
            p.set(align='right')
            p.text(f"{order.created_at.strftime('%d/%m %H:%M')}\n")
            p.cut()
            return True
        except Exception as e:
            logger.error(f"Printing failed: {e}")
            return False

    def print_bill(self, order):
        """Prints the final customer bill."""
        p = self.get_printer()
        if not p: return False

        try:
            p.set(align='center', text_type='B', width=2, height=2)
            p.text(f"{order.tenant.name}\n")
            p.set(align='center', text_type='NORMAL')
            p.text(f"{order.outlet.name}\n")
            p.text("-" * 32 + "\n")
            
            p.set(align='left')
            p.text(f"Bill: {order.order_number}\n")
            p.text(f"Date: {order.created_at.strftime('%d/%m/%Y %H:%M')}\n")
            p.text("-" * 32 + "\n")
            
            # Header
            p.text(f"{'Item':<20} {'Qty':>3} {'Amt':>7}\n")
            p.text("-" * 32 + "\n")
            
            for item in order.items.exclude(status="voided"):
                name = item.menu_item.name[:18]
                p.text(f"{name:<20} {item.quantity:>3} {float(item.total_price):>7.2f}\n")
                
            p.text("-" * 32 + "\n")
            p.set(align='right')
            p.text(f"Subtotal: {float(order.subtotal):>7.2f}\n")
            p.text(f"Tax (GST): {float(order.gst_total):>7.2f}\n")
            if order.discount_total > 0:
                p.text(f"Discount: -{float(order.discount_total):>7.2f}\n")
            
            p.set(text_type='B', height=2)
            p.text(f"GRAND TOTAL: {float(order.grand_total):>7.2f}\n")
            
            p.set(align='center', text_type='NORMAL', height=1)
            p.text("\nThank you for visiting!\n")
            p.cut()
            return True
        except Exception as e:
            logger.error(f"Bill printing failed: {e}")
            return False
