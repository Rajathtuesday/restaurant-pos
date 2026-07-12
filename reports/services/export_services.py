import csv
import io
import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Sum, F
from django.utils import timezone
from orders.models import Order, OrderItem
import openpyxl
from openpyxl.styles import Font, Alignment

logger = logging.getLogger("pos.reports")

def generate_orders_csv(tenant, outlet, start_date, end_date):
    """Generates a detailed CSV of all orders in the given date range."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Composition Scheme note for CA — only shown when applicable
    if outlet and getattr(outlet, 'is_composition_scheme', False):
        writer.writerow([
            'NOTE: Composition Taxable Person — not eligible to collect tax on supplies.',
            f'GSTIN: {outlet.gst_no or "N/A"}',
            f'Period: {start_date} to {end_date}'
        ])
        writer.writerow([])  # blank separator row

    writer.writerow([
        'Order ID', 'Order No', 'Date', 'Time', 'Outlet', 'Source',
        'Status', 'Customer Name', 'Subtotal', 'Discount', 'GST',
        'Round Off', 'Grand Total', 'Payment Methods'
    ])
    
    orders = Order.objects.filter(
        tenant=tenant,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    ).prefetch_related('payments', 'outlet').order_by('-created_at')
    
    if outlet:
        orders = orders.filter(outlet=outlet)
        
    for order in orders:
        payments = ", ".join([p.method for p in order.payments.all()])
        writer.writerow([
            order.id,
            order.order_number or '-',
            order.created_at.strftime('%Y-%m-%d'),
            order.created_at.strftime('%H:%M:%S'),
            order.outlet.name if order.outlet else 'Unknown',
            order.get_source_display(),
            order.get_status_display(),
            order.customer_name or 'Walk-in',
            order.subtotal,
            order.discount_total,
            order.gst_total,
            order.round_off,
            order.grand_total,
            payments
        ])
        
    logger.info("Orders CSV generated successfully. Rows: %s", orders.count())
    return output.getvalue()


def generate_items_csv(tenant, outlet, start_date, end_date):
    """Generates a detailed CSV of all items sold in the given date range."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        'Item Name', 'Category', 'Quantity Sold', 'Gross Revenue', 'Average Rate'
    ])
    
    # Match the canonical "sold item" definition used by the dashboard
    # (Order.recalculate_totals / item_reports.top_items): only paid/closed
    # orders, exclude voided and complimentary items. The old filter counted
    # items from any order status and included complimentary items, so this
    # export never matched the dashboard's numbers for the same period.
    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__date__gte=start_date,
        order__created_at__date__lte=end_date,
        order__status__in=['paid', 'closed'],
        is_complimentary=False,
    ).exclude(status="voided")

    if outlet:
        items = items.filter(order__outlet=outlet)

    item_stats = items.values(
        'menu_item__name', 'menu_item__category__name'
    ).annotate(
        total_qty=Sum('quantity'),
        total_rev=Sum('total_price')
    ).order_by('-total_qty')
    
    for stat in item_stats:
        qty = stat['total_qty'] or 0
        rev = stat['total_rev'] or 0
        avg_rate = (rev / qty) if qty > 0 else 0
        writer.writerow([
            stat['menu_item__name'] or 'Unknown Item',
            stat['menu_item__category__name'] or 'Uncategorized',
            qty,
            rev,
            round(avg_rate, 2)
        ])
        
    logger.info("Items CSV generated successfully. Unique Items: %s", item_stats.count())
    return output.getvalue()


def generate_gstr1_excel(tenant, outlet, start_date, end_date):
    """
    Generates a GSTR-1 compliant Excel report for B2C sales.
    Splits GST into CGST and SGST (assuming intra-state for standard restaurant POS).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "GSTR-1 B2CS"
    
    # Title
    ws.merge_cells('A1:J1')
    title_cell = ws['A1']
    title_cell.value = f"GSTR-1 B2CS (Business to Consumer Small) Report - {tenant.name}"
    title_cell.font = Font(size=14, bold=True)
    title_cell.alignment = Alignment(horizontal='center')
    
    ws.merge_cells('A2:J2')
    date_cell = ws['A2']
    date_cell.value = f"Period: {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"
    date_cell.font = Font(italic=True)
    date_cell.alignment = Alignment(horizontal='center')
    
    # Headers
    headers = [
        'Type', 'Place of Supply', 'Rate (%)', 'Taxable Value', 
        'Central Tax (CGST)', 'State Tax (SGST)', 'Integrated Tax (IGST)', 
        'Cess Amount', 'E-Commerce GSTIN', 'Total Tax'
    ]
    
    ws.append([]) # Empty row
    ws.append(headers)
    
    # Style headers
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=4, column=col)
        cell.font = Font(bold=True)
        
    # Get order items and calculate taxes grouped by GST rate
    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__date__gte=start_date,
        order__created_at__date__lte=end_date,
        order__status__in=['paid', 'closed']
    )
    
    if outlet:
        items = items.filter(order__outlet=outlet)
        
    # We group by the menu item's GST percentage
    # To handle order-level discounts, we calculate an approximation:
    # Taxable Value = Sum(total_price)
    # GST = Sum(GST calculated on item base considering order discounts)
    # For a perfect GSTR-1, we will re-calculate item-level taxable amounts accounting for order discounts
    
    # Let's fetch the actual orders to iterate through their items to be perfectly accurate with order discounts
    orders = Order.objects.filter(
        tenant=tenant,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        status__in=['paid', 'closed']
    ).prefetch_related('items', 'items__menu_item')
    
    if outlet:
        orders = orders.filter(outlet=outlet)
        
    # Group data by GST Rate
    gst_groups = {} # rate: {'taxable': 0, 'gst': 0}
    
    for order in orders:
        # Composition scheme outlets issue Bill of Supply — no GST, skip from GSTR-1
        if getattr(order.outlet, "is_composition_scheme", False):
            continue

        gst_inclusive = getattr(order.outlet, "gst_inclusive", False)

        # Reconstruct exactly how Order.recalculate_totals works
        items_valid = [item for item in order.items.all() if item.status != "voided" and not item.is_complimentary]
        
        raw_subtotal = sum((item.total_price for item in items_valid), Decimal("0.0"))
        
        item_discount_total = Decimal("0.00")
        subtotal_after_item_discounts = Decimal("0.00")
        for item in items_valid:
            item_base = item.total_price
            if getattr(item, 'item_discount_pct', Decimal("0.00")) > 0:
                item_discount = item_base * (item.item_discount_pct / Decimal("100"))
                item_base = item_base - item_discount
            subtotal_after_item_discounts += item_base
            
        order_discount_total = Decimal("0.00")
        if order.discount_type == "percentage" and (order.discount_value or 0) > 0:
            order_discount_total = subtotal_after_item_discounts * (Decimal(order.discount_value) / Decimal("100"))
        elif order.discount_type == "amount" and (order.discount_value or 0) > 0:
            order_discount_total = Decimal(str(order.discount_value))
            
        if subtotal_after_item_discounts > 0:
            order_discount_factor = max(Decimal("0.0"), (subtotal_after_item_discounts - order_discount_total) / subtotal_after_item_discounts)
        else:
            order_discount_factor = Decimal("1.0")
            
        for item in items_valid:
            item_base = item.total_price
            if getattr(item, 'item_discount_pct', Decimal("0.00")) > 0:
                item_base = item_base * (1 - item.item_discount_pct / Decimal("100"))
            
            item_discounted = item_base * order_discount_factor
            rate = item.menu_item.gst_percentage if item.menu_item else Decimal("5.00")

            rate_key = float(rate)
            if rate_key not in gst_groups:
                gst_groups[rate_key] = {'taxable': Decimal("0.0"), 'gst': Decimal("0.0")}

            if gst_inclusive:
                # item_discounted is the inclusive customer price — back-calculate
                item_gst     = item_discounted * rate / (Decimal("100") + rate)
                item_taxable = item_discounted - item_gst
            else:
                # item_discounted is already the pre-GST base
                item_taxable = item_discounted
                item_gst     = item_taxable * rate / Decimal("100")

            gst_groups[rate_key]['taxable'] += item_taxable
            gst_groups[rate_key]['gst']     += item_gst

    total_taxable = Decimal("0.0")
    total_central = Decimal("0.0")
    total_state = Decimal("0.0")

    # Assuming place of supply is local state. You would normally read from tenant's state.
    pos_state = "Local"
    
    for rate, data in sorted(gst_groups.items()):
        taxable = data['taxable']
        gst = data['gst']
        
        cgst = (gst / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sgst = gst - cgst   # ensures CGST + SGST == total GST exactly
        
        total_taxable += taxable
        total_central += cgst
        total_state += sgst
        
        ws.append([
            'OE', # Outward Supplies
            pos_state,
            rate,
            round(float(taxable), 2),
            round(float(cgst), 2),
            round(float(sgst), 2),
            0.0, # IGST
            0.0, # Cess
            '',  # E-Comm GSTIN
            round(float(gst), 2)
        ])
        
    # Totals Row
    ws.append([])
    ws.append([
        'TOTAL', '', '', 
        round(float(total_taxable), 2),
        round(float(total_central), 2),
        round(float(total_state), 2),
        0.0, 0.0, '', 
        round(float(total_central + total_state), 2)
    ])
    
    # Bold the totals row
    for col in range(1, 11):
        ws.cell(row=ws.max_row, column=col).font = Font(bold=True)
        
    # Auto-adjust column widths (skip MergedCells which lack column_letter)
    for col in ws.columns:
        max_length = 0
        first_cell = col[0]
        if not hasattr(first_cell, "column_letter"):
            continue
        column = first_cell.column_letter
        for cell in col:
            try:
                if cell.value and len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception:
                pass
        ws.column_dimensions[column].width = max_length + 2

    output = io.BytesIO()
    wb.save(output)
    
    logger.info("GSTR-1 Excel generated successfully. Max Row: %s", ws.max_row)
    return output.getvalue()


def generate_waiter_csv(tenant, outlet, start_date, end_date):
    """Generates Staff Performance CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Staff Name', 'Total Orders Handled', 'Total Revenue Handled', 'Average Order Value'])
    
    orders = Order.objects.filter(
        tenant=tenant,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        status__in=['paid', 'closed'],
        created_by__isnull=False
    )
    
    if outlet:
        orders = orders.filter(outlet=outlet)
        
    waiter_stats = orders.values('created_by__username').annotate(
        total_orders=Sum(1),
        total_rev=Sum('grand_total')
    ).order_by('-total_rev')
    
    for stat in waiter_stats:
        orders_count = stat['total_orders'] or 0
        rev = stat['total_rev'] or 0
        aov = (rev / orders_count) if orders_count > 0 else 0
        writer.writerow([
            stat['created_by__username'],
            orders_count,
            round(float(rev), 2),
            round(float(aov), 2)
        ])
        
    logger.info("Waiter Performance CSV generated successfully. Waiters analyzed: %s", waiter_stats.count())
    return output.getvalue()


def generate_category_csv(tenant, outlet, start_date, end_date):
    """Generates Category Sales CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Category Name', 'Items Sold', 'Total Revenue'])
    
    # Same canonical "sold item" definition as the dashboard — exclude voided
    # and complimentary items so this export agrees with the on-screen numbers.
    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__date__gte=start_date,
        order__created_at__date__lte=end_date,
        order__status__in=['paid', 'closed'],
        is_complimentary=False,
    ).exclude(status="voided")

    if outlet:
        items = items.filter(order__outlet=outlet)

    category_stats = items.values('menu_item__category__name').annotate(
        qty=Sum('quantity'),
        rev=Sum('total_price')
    ).order_by('-rev')
    
    for stat in category_stats:
        writer.writerow([
            stat['menu_item__category__name'] or 'Uncategorized',
            stat['qty'] or 0,
            round(float(stat['rev'] or 0), 2)
        ])
        
    logger.info("Category Sales CSV generated successfully. Categories analyzed: %s", category_stats.count())
    return output.getvalue()
