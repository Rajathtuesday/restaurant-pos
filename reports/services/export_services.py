import csv
import io
import logging
from datetime import timedelta
from decimal import Decimal
from django.db.models import Prefetch, Sum, F
from django.utils import timezone
from orders.models import Order, OrderItem, Payment
from core.utils import get_business_date_range
from orders.services.tax_engine import GST, VAT
from reports.services.documents_issued import NATURE, documents_issued
from reports.services.tax_totals import operator_supplies, rate_totals
import openpyxl
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

logger = logging.getLogger("pos.reports")

# Official GST state/UT codes — the first 2 digits of any GSTIN.
# Reference: https://www.gstn.org.in (public, fixed government table).
GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli",
    "27": "Maharashtra", "28": "Andhra Pradesh (Old)", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}


def _place_of_supply(outlet):
    """Real state name derived from the outlet's own GSTIN prefix, instead of
    a hardcoded placeholder. Falls back to a labeled placeholder (not a value
    that reads as real) if the outlet has no GSTIN on file."""
    if outlet and outlet.gst_no and len(outlet.gst_no) >= 2:
        code = outlet.gst_no[:2]
        if code in GST_STATE_CODES:
            return GST_STATE_CODES[code]
    return "Unknown — set outlet GSTIN"

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
        'Order ID', 'Order No', 'Bill No', 'Date', 'Time', 'Outlet', 'Source',
        'Status', 'Customer Name', 'Subtotal', 'Discount', 'GST',
        'Round Off', 'Grand Total', 'Payment Methods'
    ])
    
    range_start, range_end = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)
    orders = Order.objects.filter(
        tenant=tenant,
        created_at__gte=range_start,
        created_at__lt=range_end
    ).prefetch_related(
        # payments in the order they were taken, so the methods column is stable
        Prefetch('payments', queryset=Payment.objects.order_by('paid_at', 'id')), 'outlet',
    ).order_by('-created_at', '-id')

    if outlet:
        orders = orders.filter(outlet=outlet)

    for order in orders:
        payments = ", ".join([p.method for p in order.payments.all()])
        opened = timezone.localtime(order.created_at)   # stored in UTC; the sheet shows local time
        writer.writerow([
            order.id,
            order.order_number or '-',
            order.bill_number or '-',
            opened.strftime('%Y-%m-%d'),
            opened.strftime('%H:%M:%S'),
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
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)
    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__gte=range_start,
        order__created_at__lt=range_end,
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
    ).order_by('-total_qty', 'menu_item__name')
    
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


def _autosize_columns(ws):
    """Make every column wide enough for its longest value, so no header or
    figure is cut off. A title merged across several columns already has
    their combined width, so it sizes none of them. (Each column used to be
    found from its top cell, which under a merged title is a MergedCell: every
    column but the first was skipped, and the first was sized to the title.)"""
    spanning = {
        (row, col)
        for merged in ws.merged_cells.ranges if merged.max_col > merged.min_col
        for row in range(merged.min_row, merged.max_row + 1)
        for col in range(merged.min_col, merged.max_col + 1)
    }
    for index, column in enumerate(ws.iter_cols(), start=1):
        longest = max(
            (len(line)
             for cell in column
             if cell.value is not None and (cell.row, index) not in spanning
             for line in str(cell.value).splitlines()),
            default=0,
        )
        ws.column_dimensions[get_column_letter(index)].width = max(longest + 2, 10)


def _documents_issued_sheet(wb, tenant, outlet, start_date, end_date):
    """GSTR-1 Table 13, documents issued: one row per bill number series in
    the period (reports/services/documents_issued.py, P23)."""
    ws = wb.create_sheet("GSTR-1 Table 13 (Docs)")
    ws.merge_cells('A1:F1')
    ws['A1'].value = f"GSTR-1 Table 13: Documents Issued - {tenant.name}"
    ws['A1'].font = Font(size=14, bold=True)
    ws['A1'].alignment = Alignment(horizontal='center')
    ws.merge_cells('A2:F2')
    ws['A2'].value = f"Period: {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"
    ws['A2'].font = Font(italic=True)
    ws['A2'].alignment = Alignment(horizontal='center')

    headers = ['Nature of Document', 'Sr. No. From', 'Sr. No. To', 'Total Number', 'Cancelled', 'Net Issued']
    ws.append([])
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        ws.cell(row=4, column=col).font = Font(bold=True)

    rows = documents_issued(tenant, outlet, start_date, end_date)
    for row in rows:
        ws.append([NATURE, row["first"], row["last"], row["total"], row["cancelled"], row["net"]])
    ws.append([])
    ws.append(['TOTAL', '', '',
               sum(r["total"] for r in rows), sum(r["cancelled"] for r in rows), sum(r["net"] for r in rows)])
    for col in range(1, len(headers) + 1):
        ws.cell(row=ws.max_row, column=col).font = Font(bold=True)

    ws.append([])
    ws.append(["One row per bill number series. Total counts the bills in the period. Old-style "
               "numbers (INV-...) were also taken by orders cancelled before their bill, so those "
               "series can skip numbers."])
    ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=6)
    ws.cell(row=ws.max_row, column=1).font = Font(italic=True)
    ws.cell(row=ws.max_row, column=1).alignment = Alignment(wrap_text=True, vertical='top')
    ws.row_dimensions[ws.max_row].height = 45

    _autosize_columns(ws)


TABLE_8_ROWS = (
    "Inter-State supplies to registered persons",
    "Intra-State supplies to registered persons",
    "Inter-State supplies to unregistered persons",
    "Intra-State supplies to unregistered persons",
)


def nil_and_non_gst(totals_by_rate):
    """(nil rated, non-GST) values from rate_totals(): dishes sold at 0% GST,
    and liquor at its value before VAT. Liquor is outside GST altogether
    (Constitution, Article 366(12A)), so it is a non-GST supply."""
    nil_rated = sum((f["taxable"] for (kind, rate), f in totals_by_rate.items()
                     if kind == GST and rate == 0), Decimal("0.00"))
    non_gst = sum((f["taxable"] for (kind, rate), f in totals_by_rate.items() if kind == VAT),
                  Decimal("0.00"))
    return nil_rated, non_gst


def _nil_and_non_gst_sheet(wb, tenant, totals_by_rate, start_date, end_date):
    """GSTR-1 Table 8: nil rated, exempted and non-GST outward supplies.
    Every Rasova bill is to an unregistered guest in the outlet's own state,
    so everything goes on the intra-State, unregistered row."""
    ws = wb.create_sheet("GSTR-1 Table 8 (Nil, non-GST)")
    ws.merge_cells("A1:D1")
    ws["A1"].value = f"GSTR-1 Table 8: Nil rated, exempted and non-GST supplies - {tenant.name}"
    ws["A1"].font = Font(size=14, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A2:D2")
    ws["A2"].value = f"Period: {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"
    ws["A2"].font = Font(italic=True)
    ws["A2"].alignment = Alignment(horizontal="center")

    headers = ["Description", "Nil Rated Supplies",
               "Exempted (other than nil rated/non-GST supply)", "Non-GST Supplies"]
    ws.append([])
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        ws.cell(row=4, column=col).font = Font(bold=True)

    nil_rated, non_gst = nil_and_non_gst(totals_by_rate)
    for description in TABLE_8_ROWS:
        ours = description == TABLE_8_ROWS[-1]
        ws.append([description, round(float(nil_rated), 2) if ours else 0.0, 0.0,
                   round(float(non_gst), 2) if ours else 0.0])

    ws.append([])
    ws.append(["Nil rated: dishes sold at 0% GST. Non-GST: liquor, at its value before VAT."])
    # Merged across the table so the note doesn't size the first column.
    ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=len(headers))
    ws.cell(row=ws.max_row, column=1).font = Font(italic=True)
    _autosize_columns(ws)


_OPERATOR_NAMES = {"zomato": "Zomato", "swiggy": "Swiggy", "uber_eats": "Uber Eats"}


def _operator_supplies_sheet(wb, tenant, orders, start_date, end_date):
    """GSTR-1 Table 14: supplies through e-commerce operators. Orders through
    Zomato, Swiggy or Uber Eats carry no GST of their own: the app pays it
    (CGST Act, section 9(5)), and the restaurant reports their value here,
    against the app's GSTIN, with no tax (and in GSTR-3B 3.1.1(ii))."""
    from setup.models import AggregatorConfig

    ws = wb.create_sheet("GSTR-1 Table 14 (App orders)")
    headers = ["Nature of supply", "GSTIN of e-commerce operator", "E-commerce operator",
               "Net value of supplies", "Integrated Tax (IGST)", "Central Tax (CGST)",
               "State Tax (SGST)", "Cess Amount"]
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    ws["A1"].value = f"GSTR-1 Table 14: Supplies through e-commerce operators - {tenant.name}"
    ws["A1"].font = Font(size=14, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    ws["A2"].value = f"Period: {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"
    ws["A2"].font = Font(italic=True)
    ws["A2"].alignment = Alignment(horizontal="center")
    ws.append([])
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        ws.cell(row=4, column=col).font = Font(bold=True)

    configs = {c.outlet_id: c for c in AggregatorConfig.objects.filter(tenant=tenant)}
    by_gstin = {}
    for (outlet_id, source), value in sorted(operator_supplies(orders).items()):
        config = configs.get(outlet_id)
        gstin = (config.operator_gstin(source) if config else "") or "Not set: add it in Aggregator settings"
        key = (gstin, _OPERATOR_NAMES.get(source, source))
        by_gstin[key] = by_gstin.get(key, Decimal("0.00")) + value
    for (gstin, name), value in by_gstin.items():
        ws.append(["Liable to pay tax u/s 9(5)", gstin, name, round(float(value), 2), 0.0, 0.0, 0.0, 0.0])

    ws.append([])
    ws.append(["The app pays the GST on these orders (CGST Act, section 9(5)): their value is reported "
               "here with no tax, and in GSTR-3B Table 3.1.1(ii)."])
    ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=len(headers))
    ws.cell(row=ws.max_row, column=1).font = Font(italic=True)
    _autosize_columns(ws)


def generate_gstr1_excel(tenant, outlet, start_date, end_date):
    """
    Generates a GSTR-1 compliant Excel report for B2C sales, plus the
    Table 12 HSN/SAC summary — mandatory for every GST filer regardless
    of B2B/B2C mix, unlike the B2CS sheet which only matters once there's
    B2C turnover to report.
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
        
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)
    orders = Order.objects.filter(
        tenant=tenant,
        created_at__gte=range_start,
        created_at__lt=range_end,
        status__in=['paid', 'closed']
    )
    
    if outlet:
        orders = orders.filter(outlet=outlet)
        
    # Taxable value, CGST and SGST per rate, summed from each bill's own tax
    # record: the rate each line was sold at, the parcel charge included, so
    # the return adds up to exactly the bills it covers. Composition bills
    # carry no GST rows.
    totals_by_rate = rate_totals(orders)
    gst_groups = sorted(
        (rate, figures) for (kind, rate), figures in totals_by_rate.items() if kind == GST
    )
    # B2CS is for taxed supplies. Dishes sold at 0% are nil rated and belong in
    # Table 8, with non-GST supplies such as liquor (P20); a 0% row here was
    # the wrong table.
    b2cs_groups = [(rate, figures) for rate, figures in gst_groups if rate > 0]

    total_taxable = Decimal("0.0")
    total_central = Decimal("0.0")
    total_state = Decimal("0.0")

    # Real place of supply, derived from the outlet's own GSTIN — not a
    # hardcoded placeholder. If the report spans multiple outlets that don't
    # share one state, don't guess: this report only computes CGST/SGST
    # (intra-state), never IGST, so mixing states into one "Local" row would
    # be silently wrong for whichever outlet isn't actually local. Proper
    # multi-state GSTR-1 (with real IGST math) is a larger, separate piece of
    # work — this only fixes the misleading placeholder value.
    if outlet:
        pos_state = _place_of_supply(outlet)
    else:
        outlet_states = {o.gst_no[:2] for o in tenant.outlets.all() if o.gst_no and len(o.gst_no) >= 2}
        if len(outlet_states) == 1:
            pos_state = _place_of_supply(tenant.outlets.filter(gst_no__startswith=next(iter(outlet_states))).first())
        elif len(outlet_states) > 1:
            pos_state = "Multiple states — export per outlet"
            logger.warning(
                "GSTR-1 export for tenant %s spans outlets in multiple states (%s); "
                "this report only computes CGST/SGST, not IGST. Export per outlet instead.",
                tenant.id, outlet_states,
            )
        else:
            pos_state = "Unknown — set outlet GSTIN"
    
    for rate, data in b2cs_groups:
        taxable = data['taxable']
        gst = data['tax']
        cgst, sgst = data['cgst'], data['sgst']

        total_taxable += taxable
        total_central += cgst
        total_state += sgst

        ws.append([
            'OE', # Outward Supplies
            pos_state,
            float(rate),
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

    _autosize_columns(ws)

    # ── Table 12 — HSN/SAC Summary ──────────────────────────────────────────
    # Mandatory for every GSTR-1 filer, unconditionally — unlike the B2CS
    # sheet above, this isn't optional just because a period had no B2C sales.
    # A restaurant's whole menu is one GST service classification (SAC 996331,
    # "restaurant/catering services"), so this reuses gst_groups from above,
    # one row per rate actually used.
    # Since the May 2025 returns Table 12 has a B2B tab and a B2C tab. Rasova
    # issues only B2C bills, so this is the B2C tab, and says so (P24).
    ws12 = wb.create_sheet("GSTR-1 Table 12 (HSN, B2C)")

    ws12.merge_cells('A1:K1')
    t12_title = ws12['A1']
    t12_title.value = f"GSTR-1 Table 12: HSN/SAC Summary, B2C tab - {tenant.name}"
    t12_title.font = Font(size=14, bold=True)
    t12_title.alignment = Alignment(horizontal='center')

    ws12.merge_cells('A2:K2')
    t12_date = ws12['A2']
    t12_date.value = f"Period: {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"
    t12_date.font = Font(italic=True)
    t12_date.alignment = Alignment(horizontal='center')

    t12_headers = [
        'HSN/SAC', 'Description', 'UQC', 'Total Quantity', 'Total Value',
        'Rate (%)', 'Taxable Value', 'Integrated Tax (IGST)',
        'Central Tax (CGST)', 'State Tax (SGST)', 'Cess Amount',
    ]
    ws12.append([])
    ws12.append(t12_headers)
    for col in range(1, len(t12_headers) + 1):
        ws12.cell(row=4, column=col).font = Font(bold=True)

    RESTAURANT_SAC = "996331"
    RESTAURANT_SAC_DESC = "Restaurant/catering services"

    t12_total_value = Decimal("0.0")
    t12_total_taxable = Decimal("0.0")
    t12_total_central = Decimal("0.0")
    t12_total_state = Decimal("0.0")

    for rate, data in gst_groups:
        taxable = data['taxable']
        gst = data['tax']
        cgst, sgst = data['cgst'], data['sgst']
        total_value = taxable + gst

        t12_total_value += total_value
        t12_total_taxable += taxable
        t12_total_central += cgst
        t12_total_state += sgst

        ws12.append([
            RESTAURANT_SAC,
            RESTAURANT_SAC_DESC,
            'NA',   # services have no unit of measure
            0,      # Total Quantity — not applicable for services
            round(float(total_value), 2),
            float(rate),
            round(float(taxable), 2),
            0.0,    # IGST — same intra-state-only limitation as the B2CS sheet
            round(float(cgst), 2),
            round(float(sgst), 2),
            0.0,    # Cess
        ])

    ws12.append([])
    ws12.append([
        'TOTAL', '', '', 0,
        round(float(t12_total_value), 2), '',
        round(float(t12_total_taxable), 2),
        0.0,
        round(float(t12_total_central), 2),
        round(float(t12_total_state), 2),
        0.0,
    ])
    for col in range(1, len(t12_headers) + 1):
        ws12.cell(row=ws12.max_row, column=col).font = Font(bold=True)

    _autosize_columns(ws12)

    _nil_and_non_gst_sheet(wb, tenant, totals_by_rate, start_date, end_date)
    _operator_supplies_sheet(wb, tenant, orders, start_date, end_date)
    _documents_issued_sheet(wb, tenant, outlet, start_date, end_date)

    output = io.BytesIO()
    wb.save(output)
    
    logger.info("GSTR-1 Excel generated successfully. Max Row: %s", ws.max_row)
    return output.getvalue()


def generate_waiter_csv(tenant, outlet, start_date, end_date):
    """Generates Staff Performance CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Staff Name', 'Total Orders Handled', 'Total Revenue Handled', 'Average Order Value'])
    
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)
    orders = Order.objects.filter(
        tenant=tenant,
        created_at__gte=range_start,
        created_at__lt=range_end,
        status__in=['paid', 'closed'],
        created_by__isnull=False
    )
    
    if outlet:
        orders = orders.filter(outlet=outlet)
        
    waiter_stats = orders.values('created_by__username').annotate(
        total_orders=Sum(1),
        total_rev=Sum('grand_total')
    ).order_by('-total_rev', 'created_by__username')
    
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
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)
    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__gte=range_start,
        order__created_at__lt=range_end,
        order__status__in=['paid', 'closed'],
        is_complimentary=False,
    ).exclude(status="voided")

    if outlet:
        items = items.filter(order__outlet=outlet)

    category_stats = items.values('menu_item__category__name').annotate(
        qty=Sum('quantity'),
        rev=Sum('total_price')
    ).order_by('-rev', 'menu_item__category__name')
    
    for stat in category_stats:
        writer.writerow([
            stat['menu_item__category__name'] or 'Uncategorized',
            stat['qty'] or 0,
            round(float(stat['rev'] or 0), 2)
        ])
        
    logger.info("Category Sales CSV generated successfully. Categories analyzed: %s", category_stats.count())
    return output.getvalue()


def generate_pl_csv(tenant, outlet, start_date, end_date):
    """Generates the Net Profit / P&L CSV — revenue, COGS, operating
    expenses, net profit, and the expense category breakdown."""
    from reports.services.pl_reports import net_profit_report

    output = io.StringIO()
    writer = csv.writer(output)
    report = net_profit_report(tenant, outlet, start_date, end_date)

    writer.writerow(['NET PROFIT REPORT', f'Period: {start_date} to {end_date}'])
    writer.writerow([])
    writer.writerow(['Gross Revenue', report['gross_revenue']])
    writer.writerow(['GST Collected', report['gst_collected']])
    writer.writerow(['Net Revenue', report['net_revenue']])
    writer.writerow(['Discounts Given', report['discounts']])
    writer.writerow(['COGS', report['cogs']])
    writer.writerow(['Gross Profit', report['gross_profit']])
    writer.writerow(['Gross Margin %', report['gross_margin_pct']])
    writer.writerow(['Operating Expenses', report['operating_expenses']])
    writer.writerow(['NET PROFIT', report['net_profit']])
    writer.writerow(['Net Margin %', report['net_margin_pct']])
    writer.writerow([])
    writer.writerow(['EXPENSE BREAKDOWN BY CATEGORY'])
    writer.writerow(['Category', 'Amount'])
    for row in report['expense_breakdown']:
        writer.writerow([row['category'], float(row['total'])])

    return output.getvalue()


def generate_menu_engineering_csv(tenant, outlet, start_date, end_date):
    """Generates the Menu Engineering (stars/dogs quadrant) CSV."""
    from reports.services.menu_engineering import menu_engineering_report

    output = io.StringIO()
    writer = csv.writer(output)
    rows = menu_engineering_report(tenant, outlet, start_date, end_date)

    writer.writerow(['Item', 'Quantity Sold', 'Revenue', 'COGS', 'Margin %', 'Quadrant', 'Cost Known'])
    for row in rows["items"]:
        writer.writerow([
            row['name'], row['qty'], row['revenue'],
            row['cogs'] if row['cogs_known'] else 'unknown',
            row['margin_pct'] if row['cogs_known'] else '',
            row['quadrant'], 'yes' if row['cogs_known'] else 'no',
        ])

    return output.getvalue()


def generate_labor_csv(tenant, outlet, start_date, end_date):
    """Generates the Labor Cost CSV -- per-staff hours, tips, and cost."""
    from reports.services.labor_reports import labor_cost_report

    output = io.StringIO()
    writer = csv.writer(output)
    report = labor_cost_report(tenant, outlet, start_date, end_date)

    writer.writerow(['LABOR COST REPORT', f'Period: {start_date} to {end_date}'])
    writer.writerow(['Total Labor Cost', report['total_labor_cost']])
    writer.writerow(['Revenue', report.get('revenue', 0)])
    writer.writerow(['Labor Cost %', report['labor_cost_pct']])
    writer.writerow([])
    writer.writerow(['Staff', 'Pay Type', 'Hours', 'Tips', 'Cost'])
    for row in report['rows']:
        writer.writerow([
            row['username'], row['pay_type'] or 'unknown', row['hours'], row['tips'],
            row['cost'] if row['cost_known'] else 'unknown',
        ])

    return output.getvalue()


def generate_audit_csv(tenant, outlet, start_date, end_date):
    """Generates the Discount/Void Staff Audit CSV."""
    from reports.services.audit_reports import discount_void_audit

    output = io.StringIO()
    writer = csv.writer(output)
    report = discount_void_audit(tenant, outlet, start_date, end_date)

    writer.writerow(['DISCOUNT / VOID STAFF AUDIT', f'Period: {start_date} to {end_date}'])
    writer.writerow([])

    writer.writerow(['ORDER-LEVEL DISCOUNTS'])
    writer.writerow(['Staff', 'Count'])
    for row in report['discounts']:
        writer.writerow([row['created_by__username'] or 'Unknown', row['count']])
    writer.writerow([])

    writer.writerow(['ITEM-LEVEL DISCOUNTS (from rollout date onward)'])
    writer.writerow(['Staff', 'Count'])
    for row in report['item_discounts']:
        writer.writerow([row['created_by__username'] or 'Unknown', row['count']])
    writer.writerow([])

    writer.writerow(['COMPLIMENTARY ITEMS (from rollout date onward)'])
    writer.writerow(['Staff', 'Count'])
    for row in report['comps']:
        writer.writerow([row['created_by__username'] or 'Unknown', row['count']])
    writer.writerow([])

    writer.writerow(['ITEM VOIDS'])
    writer.writerow(['Staff', 'Count'])
    for row in report['voids']:
        writer.writerow([row['created_by__username'] or 'Unknown', row['count']])
    writer.writerow([])

    writer.writerow(['VOID REASONS'])
    writer.writerow(['Reason', 'Count'])
    for row in report['void_reasons']:
        writer.writerow([row['metadata__reason'] or 'Unspecified', row['count']])

    return output.getvalue()


def generate_crm_analytics_csv(tenant, outlet, start_date, end_date):
    """Generates the CRM/Loyalty Analytics CSV."""
    from reports.services.crm_reports import crm_analytics_report

    output = io.StringIO()
    writer = csv.writer(output)
    report = crm_analytics_report(tenant, outlet, start_date, end_date)

    writer.writerow(['CRM / LOYALTY ANALYTICS', f'Period: {start_date} to {end_date}'])
    writer.writerow(['Repeat Customer Rate %', report['repeat_rate_pct']])
    writer.writerow(['Repeat Guests', report['repeat_guests']])
    writer.writerow(['Active Guests', report['active_guests']])
    writer.writerow(['Average Rating', report['avg_rating'] if report['avg_rating'] is not None else 'n/a'])
    writer.writerow([])

    writer.writerow(['LOYALTY TREND'])
    writer.writerow(['Date', 'Type', 'Points', 'Count'])
    for row in report['loyalty_trend']:
        writer.writerow([row['day'], row['transaction_type'], row['points'], row['count']])
    writer.writerow([])

    writer.writerow(['FEEDBACK TREND'])
    writer.writerow(['Date', 'Avg Rating', 'Count'])
    for row in report['feedback_trend']:
        writer.writerow([row['day'], round(row['avg_rating'], 2), row['count']])

    return output.getvalue()
