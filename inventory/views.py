from core.decorators import tenant_required, feature_required
# inventory/views.py
import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem

logger = logging.getLogger("pos.inventory")


def _manager_required(user):
    return user.role in ("owner", "manager") or user.is_superuser


# ============================================================
# INVENTORY BOARD
# ============================================================

@login_required
@tenant_required
@feature_required("inventory")
def inventory_board(request):
    if not _manager_required(request.user):
        return HttpResponseForbidden("Access denied")

    items = InventoryItem.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).select_related("preferred_supplier").order_by("name")

    suppliers = Supplier.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).order_by("name")

    return render(request, "inventory/inventory_board.html", {
        "items": items,
        "suppliers": suppliers,
    })


@login_required
@tenant_required
@feature_required("inventory")
@require_POST
def restock_item(request, item_id):
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        quantity = Decimal(str(data.get("quantity", "0")))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid quantity"}, status=400)

    if quantity <= 0:
        return JsonResponse({"error": "Quantity must be positive"}, status=400)

    try:
        item = InventoryItem.objects.get(
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
    except InventoryItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)

    item.add_stock(quantity)
    logger.info(f"{request.user.username} restocked '{item.name}' +{quantity}. Now: {item.stock}")

    return JsonResponse({"success": True, "new_stock": float(item.stock)})


@login_required
@tenant_required
@feature_required("inventory")
@require_POST
def create_inventory_item(request):
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = data.get("name", "").strip()
    unit = data.get("unit", "").strip()

    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)

    if unit not in ("pcs", "g", "kg", "ml", "l"):
        return JsonResponse({"error": "Invalid unit"}, status=400)

    try:
        stock = Decimal(str(data.get("stock", "0")))
        threshold = Decimal(str(data.get("threshold", "0")))
        cost_price = Decimal(str(data.get("cost_price", "0.00")))
        reorder_qty = Decimal(str(data.get("reorder_quantity", "0")))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid numeric value"}, status=400)

    # Resolve optional supplier
    supplier = None
    supplier_id = data.get("supplier_id")
    if supplier_id:
        try:
            supplier = Supplier.objects.get(
                id=supplier_id, tenant=request.user.tenant, outlet=request.user.outlet
            )
        except Supplier.DoesNotExist:
            return JsonResponse({"error": "Supplier not found"}, status=404)

    item = InventoryItem.objects.create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        name=name,
        unit=unit,
        stock=stock,
        low_stock_threshold=threshold,
        cost_price=cost_price,
        reorder_quantity=reorder_qty,
        preferred_supplier=supplier,
    )
    logger.info(f"{request.user.username} created inventory item '{name}'")
    return JsonResponse({"success": True, "id": item.id})


@login_required
@tenant_required
@feature_required("inventory")
@require_POST
def update_inventory_item(request, item_id):
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        item = InventoryItem.objects.get(
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
    except InventoryItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)

    name = data.get("name", "").strip()
    if name:
        item.name = name

    try:
        if "threshold" in data:
            item.low_stock_threshold = Decimal(str(data["threshold"]))
        if "cost_price" in data:
            item.cost_price = Decimal(str(data["cost_price"]))
        if "reorder_quantity" in data:
            item.reorder_quantity = Decimal(str(data["reorder_quantity"]))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid numeric value"}, status=400)

    if "supplier_id" in data:
        supplier_id = data["supplier_id"]
        if supplier_id:
            try:
                item.preferred_supplier = Supplier.objects.get(
                    id=supplier_id, tenant=request.user.tenant, outlet=request.user.outlet
                )
            except Supplier.DoesNotExist:
                return JsonResponse({"error": "Supplier not found"}, status=404)
        else:
            item.preferred_supplier = None

    item.save(update_fields=["name", "low_stock_threshold", "cost_price", "reorder_quantity", "preferred_supplier", "updated_at"])
    logger.info(f"{request.user.username} updated inventory item '{item.name}'")
    return JsonResponse({"success": True})


# ============================================================
# SUPPLIER MANAGEMENT
# ============================================================

@login_required
@tenant_required
@feature_required("inventory")
def supplier_list(request):
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    suppliers = Supplier.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).order_by("name")

    return render(request, "inventory/suppliers.html", {"suppliers": suppliers})


@login_required
@tenant_required
@feature_required("inventory")
@require_POST
def create_supplier(request):
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = data.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Supplier name is required"}, status=400)

    # Duplicate check (case-insensitive)
    if Supplier.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet, name__iexact=name
    ).exists():
        return JsonResponse({"error": f"Supplier '{name}' already exists"}, status=409)

    supplier = Supplier.objects.create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        name=name,
        contact_person=data.get("contact_person", ""),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        address=data.get("address", ""),
        gst_no=data.get("gst_no", ""),
    )
    return JsonResponse({
        "success": True,
        "id": supplier.id,
        "name": supplier.name,
    })


@login_required
@tenant_required
@feature_required("inventory")
@require_POST
def delete_supplier(request, supplier_id):
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        supplier = Supplier.objects.get(
            id=supplier_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except Supplier.DoesNotExist:
        return JsonResponse({"error": "Supplier not found"}, status=404)

    # Soft-delete — keeps PO history intact
    supplier.is_active = False
    supplier.save(update_fields=["is_active"])
    return JsonResponse({"success": True})


# ============================================================
# PURCHASE ORDERS
# ============================================================

@login_required
@tenant_required
@feature_required("purchase_orders")
def purchase_order_list(request):
    """
    Lists all POs for this outlet.
    Can filter by status via ?status=draft|ordered|received|cancelled
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    status_filter = request.GET.get("status", "")
    qs = PurchaseOrder.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).select_related("supplier").prefetch_related("items__item").order_by("-created_at")

    if status_filter in ("draft", "ordered", "received", "cancelled"):
        qs = qs.filter(status=status_filter)

    suppliers = Supplier.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).order_by("name")

    items = InventoryItem.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).order_by("name")

    return render(request, "inventory/purchase_orders.html", {
        "purchase_orders": qs,
        "suppliers": suppliers,
        "inventory_items": items,
        "status_filter": status_filter,
    })


@login_required
@tenant_required
@feature_required("purchase_orders")
@require_POST
def create_purchase_order(request):
    """
    Creates a new draft PO for a supplier.

    Edge cases:
    - Supplier not found / not in this outlet → 404.
    - Empty items list → reject (a PO must have at least one line).
    - Quantity / price non-positive → reject each line.
    - Duplicate item in same PO → merge quantities.
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    supplier_id = data.get("supplier_id")
    items_data = data.get("items", [])
    notes = data.get("notes", "")

    if not supplier_id:
        return JsonResponse({"error": "supplier_id is required"}, status=400)

    if not items_data:
        return JsonResponse({"error": "At least one item is required"}, status=400)

    try:
        supplier = Supplier.objects.get(
            id=supplier_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_active=True
        )
    except Supplier.DoesNotExist:
        return JsonResponse({"error": "Supplier not found"}, status=404)

    # Validate all items before creating anything
    validated_items = []
    seen_item_ids = set()
    for entry in items_data:
        item_id = entry.get("item_id")
        try:
            qty = Decimal(str(entry.get("quantity", "0")))
            unit_price = Decimal(str(entry.get("unit_price", "0")))
        except InvalidOperation:
            return JsonResponse({"error": "Invalid quantity or price"}, status=400)

        if qty <= 0:
            return JsonResponse({"error": "Quantity must be positive"}, status=400)
        if unit_price < 0:
            return JsonResponse({"error": "Unit price cannot be negative"}, status=400)

        if item_id in seen_item_ids:
            return JsonResponse({"error": "Duplicate item in purchase order"}, status=400)
        seen_item_ids.add(item_id)

        try:
            inv_item = InventoryItem.objects.get(
                id=item_id,
                tenant=request.user.tenant,
                outlet=request.user.outlet
            )
        except InventoryItem.DoesNotExist:
            return JsonResponse({"error": f"Inventory item {item_id} not found"}, status=404)

        validated_items.append((inv_item, qty, unit_price))

    with transaction.atomic():
        total = sum(qty * price for _, qty, price in validated_items)
        po = PurchaseOrder.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            supplier=supplier,
            status="draft",
            total_amount=total,
            notes=notes,
        )
        # Auto-generate PO number using TenantPOCounter
        from .models import TenantPOCounter
        from django.utils import timezone
        
        counter, _ = TenantPOCounter.objects.select_for_update().get_or_create(
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        counter.value += 1
        counter.save(update_fields=["value"])
        po.po_number = f"PO-{timezone.now().year}-{counter.value:04d}"
        po.save(update_fields=["po_number"])

        for inv_item, qty, unit_price in validated_items:
            PurchaseOrderItem.objects.create(
                purchase_order=po,
                item=inv_item,
                quantity=qty,
                unit_price=unit_price,
            )

    logger.info(f"{request.user.username} created PO {po.po_number} from {supplier.name}")
    return JsonResponse({
        "success": True,
        "po_id": po.id,
        "po_number": po.po_number,
        "total": float(total),
    })


@login_required
@tenant_required
@feature_required("purchase_orders")
@require_POST
def mark_po_ordered(request, po_id):
    """Marks a draft PO as 'ordered' (sent to supplier). Sets ordered_at timestamp."""
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        po = PurchaseOrder.objects.get(
            id=po_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except PurchaseOrder.DoesNotExist:
        return JsonResponse({"error": "Purchase order not found"}, status=404)

    if po.status != "draft":
        return JsonResponse({"error": f"Cannot mark a '{po.status}' PO as ordered"}, status=400)

    po.status = "ordered"
    po.ordered_at = timezone.now()
    po.save(update_fields=["status", "ordered_at"])
    return JsonResponse({"success": True, "status": po.status})


@login_required
@tenant_required
@feature_required("purchase_orders")
@require_POST
def receive_purchase_order(request, po_id):
    """
    Receives a PO — updates inventory stock for all line items atomically.

    Edge cases:
    - Already received → reject (idempotent guard).
    - PO must be in 'ordered' state; draft POs are not receivable.
    - Entire receive is atomic; a single stock update failure rolls back all.
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        with transaction.atomic():
            po = PurchaseOrder.objects.select_for_update().get(
                id=po_id, tenant=request.user.tenant, outlet=request.user.outlet
            )

            if po.status == "received":
                return JsonResponse({"error": "This PO has already been received"}, status=400)

            if po.status not in ("draft", "ordered"):
                return JsonResponse({"error": f"Cannot receive a '{po.status}' PO"}, status=400)

            po.receive_order()  # stock updates + status change happen here

    except PurchaseOrder.DoesNotExist:
        return JsonResponse({"error": "Purchase order not found"}, status=404)
    except Exception as e:
        logger.error(f"PO receive failed for PO {po_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)

    logger.info(f"{request.user.username} received PO {po.po_number}")
    return JsonResponse({"success": True, "status": "received"})


@login_required
@tenant_required
@feature_required("purchase_orders")
@require_POST
def cancel_purchase_order(request, po_id):
    """
    Cancels a PO.
    - Cannot cancel a received PO (stock is already in).
    - Cancelling a draft or ordered PO is allowed.
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        po = PurchaseOrder.objects.get(
            id=po_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except PurchaseOrder.DoesNotExist:
        return JsonResponse({"error": "Purchase order not found"}, status=404)

    if po.status == "received":
        return JsonResponse({"error": "Cannot cancel a received PO"}, status=400)

    if po.status == "cancelled":
        return JsonResponse({"error": "PO is already cancelled"}, status=400)

    po.status = "cancelled"
    po.save(update_fields=["status"])
    return JsonResponse({"success": True})


@login_required
@tenant_required
@feature_required("purchase_orders")
def purchase_order_print(request, po_id):
    """Renders a print-friendly view of a purchase order."""
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        po = PurchaseOrder.objects.select_related("supplier", "tenant", "outlet").prefetch_related("items", "items__item").get(
            id=po_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except PurchaseOrder.DoesNotExist:
        return HttpResponseForbidden("Purchase order not found")

    return render(request, "inventory/purchase_order_print.html", {"po": po})


@login_required
@tenant_required
def purchase_order_view(request):
    """Legacy alias for the old URL — redirect to new list view."""
    from django.shortcuts import redirect
    return redirect("purchase_order_list")



@login_required
@tenant_required
def consumption_report(request):
    """
    Daily inventory consumption report.
    Shows how much of each ingredient was used today based on orders × recipes.
    Best practice: calculated from OrderItem quantities × Recipe.quantity_required.
    """
    from django.utils import timezone
    from decimal import Decimal
    from orders.models import OrderItem
    from menu.models import Recipe
    from core.utils import get_business_date

    tenant = request.user.tenant
    outlet = request.user.outlet

    # Date filter — default today, allow ?date=YYYY-MM-DD
    date_str = request.GET.get("date", "")
    try:
        from datetime import date as dt_date
        report_date = dt_date.fromisoformat(date_str) if date_str else get_business_date(
            timezone.now(), outlet
        )
    except ValueError:
        report_date = get_business_date(timezone.now(), outlet)

    # All non-voided items sold today for this outlet
    sold_items = (
        OrderItem.objects
        .filter(
            order__tenant=tenant,
            order__outlet=outlet,
            order__created_at__date=report_date,
            order__status__in=["closed", "paid"],
        )
        .exclude(status="voided")
        .select_related("menu_item")
    )

    # Aggregate consumption per inventory item via recipes
    consumption: dict = {}  # {inventory_item_id: {"item": obj, "qty": Decimal}}
    for order_item in sold_items:
        if not order_item.menu_item:
            continue
        for recipe in order_item.menu_item.recipes.select_related("inventory_item").all():
            inv = recipe.inventory_item
            qty = recipe.quantity_required * Decimal(str(order_item.quantity))
            if inv.id not in consumption:
                inv.refresh_from_db()
                consumption[inv.id] = {"item": inv, "consumed": Decimal("0")}
            consumption[inv.id]["consumed"] += qty

    # Sort by consumed (most used first)
    report_rows = sorted(consumption.values(), key=lambda x: x["consumed"], reverse=True)

    # CSV export
    if request.GET.get("export") == "csv":
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="consumption_{report_date}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Ingredient", "Unit", "Consumed Today", "Remaining Stock"])
        for row in report_rows:
            writer.writerow([
                row["item"].name,
                row["item"].unit,
                f"{row['consumed']:.3f}",
                f"{row['item'].stock:.3f}",
            ])
        return response

    return render(request, "inventory/consumption_report.html", {
        "report_date": report_date,
        "report_rows": report_rows,
        "outlet": outlet,
    })
