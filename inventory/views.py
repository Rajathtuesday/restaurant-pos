from core.decorators import tenant_required, feature_required
# inventory/views.py
import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem, generate_po_number

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

    categories = sorted({i.category for i in items if i.category})

    # Visiting the board is treated as having seen whatever low-stock/system
    # alerts brought a manager here in the first place -- same "click through
    # to go handle it" acknowledgment pattern as the other header badges,
    # none of which have a separate dropdown/dismiss UI either.
    from notifications.models import Notification
    Notification.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet,
        type__in=["low_stock", "system"], is_read=False,
    ).update(is_read=True)

    return render(request, "inventory/inventory_board.html", {
        "items": items,
        "suppliers": suppliers,
        "categories": categories,
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
    logger.info("%s restocked '%s' +%s. Now: %s", request.user.username, item.name, quantity, item.stock)

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
    category = data.get("category", "").strip()[:100]
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
        category=category,
        unit=unit,
        stock=stock,
        low_stock_threshold=threshold,
        cost_price=cost_price,
        reorder_quantity=reorder_qty,
        preferred_supplier=supplier,
    )
    logger.info("%s created inventory item '%s'", request.user.username, name)
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

    if "category" in data:
        item.category = (data.get("category") or "").strip()[:100]

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

    item.save(update_fields=["name", "category", "low_stock_threshold", "cost_price", "reorder_quantity", "preferred_supplier", "updated_at"])
    logger.info("%s updated inventory item '%s'", request.user.username, item.name)
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
    Can filter by status via ?status=draft|ordered|partially_received|received|cancelled
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    status_filter = request.GET.get("status", "")
    qs = PurchaseOrder.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).select_related("supplier").prefetch_related("items__item").order_by("-created_at")

    if status_filter in ("draft", "ordered", "partially_received", "received", "cancelled"):
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
        po.po_number = generate_po_number(request.user.tenant, request.user.outlet)
        po.save(update_fields=["po_number"])

        for inv_item, qty, unit_price in validated_items:
            PurchaseOrderItem.objects.create(
                purchase_order=po,
                item=inv_item,
                quantity=qty,
                unit_price=unit_price,
            )

    logger.info("%s created PO %s from %s", request.user.username, po.po_number, supplier.name)
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
def edit_purchase_order(request, po_id):
    """
    Replaces a draft PO's line items and notes. Only allowed while the PO
    is still 'draft' -- once it's ordered the vendor may already be
    acting on it, and once anything's been received or it's cancelled,
    editing it retroactively would misrepresent what actually happened.
    Same validation as create_purchase_order. Supplier is not editable --
    changing it would have to navigate the one-draft-per-supplier
    uniqueness constraint; cancel and start a new PO instead.
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        po = PurchaseOrder.objects.get(
            id=po_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except PurchaseOrder.DoesNotExist:
        return JsonResponse({"error": "Purchase order not found"}, status=404)

    if po.status != "draft":
        return JsonResponse({"error": f"Cannot edit a '{po.status}' purchase order"}, status=400)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    items_data = data.get("items", [])
    notes = data.get("notes", "")

    if not items_data:
        return JsonResponse({"error": "At least one item is required"}, status=400)

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
                id=item_id, tenant=request.user.tenant, outlet=request.user.outlet
            )
        except InventoryItem.DoesNotExist:
            return JsonResponse({"error": f"Inventory item {item_id} not found"}, status=404)

        validated_items.append((inv_item, qty, unit_price))

    with transaction.atomic():
        po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
        if po.status != "draft":
            return JsonResponse({"error": f"Cannot edit a '{po.status}' purchase order"}, status=400)

        kept_item_ids = [inv_item.id for inv_item, _, _ in validated_items]
        po.items.exclude(item_id__in=kept_item_ids).delete()

        for inv_item, qty, unit_price in validated_items:
            PurchaseOrderItem.objects.update_or_create(
                purchase_order=po, item=inv_item,
                defaults={"quantity": qty, "unit_price": unit_price},
            )

        total = sum(qty * price for _, qty, price in validated_items)
        po.notes = notes
        po.total_amount = total
        po.save(update_fields=["notes", "total_amount"])

    logger.info("%s edited PO %s", request.user.username, po.po_number or po.id)
    return JsonResponse({"success": True, "po_id": po.id, "total": float(total)})


@login_required
@tenant_required
@feature_required("purchase_orders")
@require_POST
def mark_po_ordered(request, po_id):
    """
    Marks a draft PO as 'ordered' (sent to supplier). Sets ordered_at
    timestamp. If the outlet has opted in to vendor emails (Outlet.
    po_vendor_email_enabled) and the supplier has an email on file, also
    emails a PDF copy of the PO after the status change actually commits.
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        po = PurchaseOrder.objects.select_related("supplier", "tenant", "outlet").get(
            id=po_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except PurchaseOrder.DoesNotExist:
        return JsonResponse({"error": "Purchase order not found"}, status=404)

    if po.status != "draft":
        return JsonResponse({"error": f"Cannot mark a '{po.status}' PO as ordered"}, status=400)

    with transaction.atomic():
        po.status = "ordered"
        po.ordered_at = timezone.now()
        po.save(update_fields=["status", "ordered_at"])

        # Only touch inventory.services (which imports weasyprint at module
        # load) when there's actually a chance of sending -- most outlets
        # have po_vendor_email_enabled off (the default), and importing a
        # module that can fail on a server missing WeasyPrint's native
        # GTK/Pango libs must never risk the status update itself, which is
        # the whole point of running this after commit in the first place.
        if po.outlet.po_vendor_email_enabled:
            def _send_email():
                try:
                    from .services import send_purchase_order_email
                    send_purchase_order_email(po)
                except Exception as exc:
                    logger.error("PO %s: vendor email trigger failed: %s", po.id, exc)
            transaction.on_commit(_send_email)

    return JsonResponse({"success": True, "status": po.status})


@login_required
@tenant_required
@feature_required("purchase_orders")
@require_POST
def receive_purchase_order(request, po_id):
    """
    Records a delivery against a PO — updates inventory stock for the
    received line items atomically. Supports partial receiving: a real
    delivery is often short or split across trips.

    Body (optional): { "items": { "<item_id>": {"quantity_received": n,
    "invoiced_price": n or null}, ... } }. Any item omitted, or an empty/
    missing body entirely, defaults to receiving that line's full
    remaining ordered quantity at its ordered price — the original
    one-click "Receive" behavior.

    Edge cases:
    - Already fully received → reject (idempotent guard).
    - PO must be in 'draft', 'ordered', or 'partially_received' state.
    - Entire receive is atomic; a single stock update failure rolls back all.
    """
    if not _manager_required(request.user):
        return HttpResponseForbidden()

    try:
        payload = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        payload = {}
    receipts = payload.get("items") or {}

    try:
        with transaction.atomic():
            po = PurchaseOrder.objects.select_for_update().get(
                id=po_id, tenant=request.user.tenant, outlet=request.user.outlet
            )

            if po.status == "received":
                return JsonResponse({"error": "This PO has already been received"}, status=400)

            if po.status not in ("draft", "ordered", "partially_received"):
                return JsonResponse({"error": f"Cannot receive a '{po.status}' PO"}, status=400)

            po.receive_order(receipts=receipts)  # stock updates + status change happen here
            po.refresh_from_db(fields=["status"])

    except PurchaseOrder.DoesNotExist:
        return JsonResponse({"error": "Purchase order not found"}, status=404)
    except (InvalidOperation, TypeError, KeyError, AttributeError) as e:
        return JsonResponse({"error": f"Invalid receiving quantities: {e}"}, status=400)
    except Exception:
        logger.exception("PO receive failed for PO %s", po_id)
        return JsonResponse({"error": "Could not receive the PO. Please try again."}, status=500)

    logger.info("%s received PO %s (status now %s)", request.user.username, po.po_number, po.status)
    return JsonResponse({"success": True, "status": po.status})


@login_required
@tenant_required
@feature_required("purchase_orders")
@require_POST
def cancel_purchase_order(request, po_id):
    """
    Cancels a PO.
    - Cannot cancel a received or partially received PO (some stock is
      already in — cancelling would hide that a delivery happened).
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

    if po.status in ("received", "partially_received"):
        return JsonResponse({"error": f"Cannot cancel a '{po.status}' PO — stock has already been received against it"}, status=400)

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
    from inventory.models import Recipe
    from inventory.unit_conversion import recipe_expected_quantity
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
        .prefetch_related("modifiers__modifier__inventory_links__inventory_item")
    )

    # Aggregate consumption per inventory item via recipes AND modifiers —
    # this used to only look at base recipes, silently missing anything
    # deducted through a modifier link (e.g. "Extra Cheese").
    consumption: dict = {}  # {inventory_item_id: {"item": obj, "consumed": Decimal}}

    def _add(inv, qty):
        if inv.id not in consumption:
            inv.refresh_from_db()
            consumption[inv.id] = {"item": inv, "consumed": Decimal("0")}
        consumption[inv.id]["consumed"] += qty

    for order_item in sold_items:
        if not order_item.menu_item:
            continue
        for recipe in order_item.menu_item.recipes.select_related("inventory_item").all():
            # A recipe's unit doesn't have to match the inventory item's own
            # unit (e.g. a recipe in grams against a kg-tracked item) — must
            # convert before multiplying, same as the actual KOT deduction
            # does, or this silently shows a number 1000x off.
            qty = recipe_expected_quantity(
                recipe.quantity_required, recipe.unit, recipe.inventory_item,
                logger=logger, context=f"Recipe {recipe.id} (menu item '{order_item.menu_item.name}')",
            )
            if qty is None:
                continue
            _add(recipe.inventory_item, qty * Decimal(str(order_item.quantity)))

        for oim in order_item.modifiers.all():
            if not oim.modifier:
                continue
            for mod_recipe in oim.modifier.inventory_links.all():
                qty = recipe_expected_quantity(
                    mod_recipe.quantity_required, mod_recipe.unit, mod_recipe.inventory_item,
                    logger=logger, context=f"ModifierRecipe {mod_recipe.id} (modifier '{oim.modifier.name}')",
                )
                if qty is None:
                    continue
                _add(mod_recipe.inventory_item, qty * Decimal(str(order_item.quantity)))

    # Sort by consumed (most used first)
    report_rows = sorted(consumption.values(), key=lambda x: x["consumed"], reverse=True)
    categories = sorted({r["item"].category for r in report_rows if r["item"].category})

    # CSV export
    if request.GET.get("export") == "csv":
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="consumption_{report_date}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Ingredient", "Category", "Unit", "Consumed Today", "Remaining Stock"])
        for row in report_rows:
            writer.writerow([
                row["item"].name,
                row["item"].category,
                row["item"].unit,
                f"{row['consumed']:.3f}",
                f"{row['item'].stock:.3f}",
            ])
        return response

    return render(request, "inventory/consumption_report.html", {
        "report_date": report_date,
        "report_rows": report_rows,
        "categories": categories,
        "outlet": outlet,
    })


# ============================================================
# WASTAGE LOGGING
# ============================================================

@login_required
@tenant_required
@feature_required("inventory")
@require_POST
def log_wastage(request, item_id):
    if not _manager_required(request.user):
        return JsonResponse({"error": "Permission denied"}, status=403)

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

    reason = data.get("reason", "").strip()[:100]
    notes  = data.get("notes", "").strip()[:200]
    reference = reason if not notes else f"{reason} — {notes}"

    try:
        item = InventoryItem.objects.get(
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
        )
    except InventoryItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)

    try:
        item.record_wastage(quantity, reference=reference or "Manual wastage")
    except Exception:
        logger.exception("Error logging wastage for item %s", item_id)
        return JsonResponse({"error": "Could not log the wastage. Please try again."}, status=400)

    item.refresh_from_db()  # F() expression; object is stale until refreshed
    logger.info(
        "%s logged wastage: %s ×%.3f %s (%s)",
        request.user.username, item.name, quantity, item.unit, reference
    )
    return JsonResponse({"success": True, "new_stock": float(item.stock)})


@login_required
@tenant_required
@feature_required("inventory")
@require_POST
def adjust_stock(request, item_id):
    """
    Manually corrects an item's stock — e.g. reconciling a physical count
    against what the system shows. Body: either {"new_count": n} (the
    counted total) or {"delta": n} (a signed +/- change), plus a required
    "reason". Exactly one of new_count/delta must be given.
    """
    if not _manager_required(request.user):
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    reason = data.get("reason", "").strip()[:200]
    if not reason:
        return JsonResponse({"error": "A reason is required for a manual adjustment"}, status=400)

    try:
        item = InventoryItem.objects.get(
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
        )
    except InventoryItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)

    has_new_count = "new_count" in data and data.get("new_count") not in (None, "")
    has_delta = "delta" in data and data.get("delta") not in (None, "")
    if has_new_count == has_delta:  # both given or neither given
        return JsonResponse({"error": "Provide exactly one of new_count or delta"}, status=400)

    try:
        if has_new_count:
            new_count = Decimal(str(data["new_count"]))
            delta = new_count - item.stock
        else:
            delta = Decimal(str(data["delta"]))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid quantity"}, status=400)

    try:
        item.adjust_stock(delta, reference=reason)
    except ValidationError as e:
        return JsonResponse({"error": str(e.message if hasattr(e, "message") else e)}, status=400)
    except Exception:
        logger.exception("Error adjusting stock for item %s", item_id)
        return JsonResponse({"error": "Could not apply the adjustment. Please try again."}, status=400)

    item.refresh_from_db()
    logger.info(
        "%s manually adjusted stock: %s %+.3f %s -> %.3f (%s)",
        request.user.username, item.name, delta, item.unit, item.stock, reason
    )
    return JsonResponse({"success": True, "new_stock": float(item.stock)})


# ============================================================
# VARIANCE REPORT
# ============================================================

@login_required
@tenant_required
@feature_required("inventory")
def variance_report(request):
    """
    Daily variance report.
    Variance = transaction-recorded consumption minus recipe-expected consumption.
      0   → KOT deductions matched orders exactly (healthy)
      +ve → more was deducted than orders explain (untracked consumption, recipe drift)
      -ve → orders placed but inventory wasn't fully deducted (tracking gap)
    """
    import csv
    from datetime import date as dt_date
    from core.utils import get_business_date
    from orders.models import OrderItem
    from .models import InventoryTransaction
    from .unit_conversion import recipe_expected_quantity

    tenant = request.user.tenant
    outlet = request.user.outlet

    date_str = request.GET.get("date", "")
    try:
        report_date = dt_date.fromisoformat(date_str) if date_str else get_business_date(
            timezone.now(), outlet
        )
    except ValueError:
        report_date = get_business_date(timezone.now(), outlet)

    # Step 1 — recipe-expected consumption per inventory item (from actual orders sold)
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
        .prefetch_related(
            "menu_item__recipes__inventory_item",
            "modifiers__modifier__inventory_links__inventory_item",
        )
    )
    recipe_map: dict = {}  # {inventory_item_id: Decimal}
    for oi in sold_items:
        if not oi.menu_item:
            continue
        for recipe in oi.menu_item.recipes.all():
            # Convert into the inventory item's own unit before multiplying —
            # a recipe entered in grams against a kg-tracked item would
            # otherwise inflate "expected" by 1000x and show a fake variance.
            qty = recipe_expected_quantity(
                recipe.quantity_required, recipe.unit, recipe.inventory_item,
                logger=logger, context=f"Recipe {recipe.id} (menu item '{oi.menu_item.name}')",
            )
            if qty is None:
                continue
            recipe_map[recipe.inventory_item_id] = (
                recipe_map.get(recipe.inventory_item_id, Decimal("0")) + qty * Decimal(str(oi.quantity))
            )
        for oim in oi.modifiers.all():
            if not oim.modifier:
                continue
            for mod_recipe in oim.modifier.inventory_links.all():
                qty = recipe_expected_quantity(
                    mod_recipe.quantity_required, mod_recipe.unit, mod_recipe.inventory_item,
                    logger=logger, context=f"ModifierRecipe {mod_recipe.id} (modifier '{oim.modifier.name}')",
                )
                if qty is None:
                    continue
                recipe_map[mod_recipe.inventory_item_id] = (
                    recipe_map.get(mod_recipe.inventory_item_id, Decimal("0")) + qty * Decimal(str(oi.quantity))
                )

    # Step 2 — per item: compare recipe vs transaction
    items = InventoryItem.objects.filter(tenant=tenant, outlet=outlet).order_by("name")
    rows = []
    for item in items:
        txns = InventoryTransaction.objects.filter(
            item=item, tenant=tenant, outlet=outlet,
            created_at__date=report_date,
        )
        restocked        = sum(t.quantity for t in txns if t.transaction_type == "restock")
        txn_consumed     = abs(sum(t.quantity for t in txns if t.transaction_type == "consume"))
        wastage          = abs(sum(t.quantity for t in txns if t.transaction_type == "wastage"))
        recipe_expected  = recipe_map.get(item.id, Decimal("0"))
        variance         = txn_consumed - recipe_expected
        variance_pct     = (
            round(float(variance / recipe_expected * 100), 1)
            if recipe_expected > 0 else None
        )
        rows.append({
            "item":             item,
            "recipe_expected":  recipe_expected,
            "txn_consumed":     txn_consumed,
            "wastage":          wastage,
            "restocked":        restocked,
            "variance":         variance,
            "variance_pct":     variance_pct,
        })

    rows.sort(key=lambda r: abs(r["variance"]), reverse=True)

    ok_count       = sum(1 for r in rows if r["variance_pct"] is not None and abs(r["variance_pct"]) <= 3)
    warn_count     = sum(1 for r in rows if r["variance_pct"] is not None and 3 < abs(r["variance_pct"]) <= 8)
    critical_count = sum(1 for r in rows if r["variance_pct"] is not None and abs(r["variance_pct"]) > 8)
    categories     = sorted({r["item"].category for r in rows if r["item"].category})

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="variance_{report_date}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Item", "Category", "Unit", "Recipe Expected", "Txn Consumed",
                         "Variance", "Variance %", "Wastage", "Restocked", "Current Stock"])
        for r in rows:
            writer.writerow([
                r["item"].name, r["item"].category, r["item"].unit,
                f"{r['recipe_expected']:.3f}", f"{r['txn_consumed']:.3f}",
                f"{r['variance']:.3f}",
                f"{r['variance_pct']:.1f}" if r["variance_pct"] is not None else "—",
                f"{r['wastage']:.3f}", f"{r['restocked']:.3f}",
                f"{r['item'].stock:.3f}",
            ])
        return response

    return render(request, "inventory/variance_report.html", {
        "report_date":    report_date,
        "rows":           rows,
        "outlet":         outlet,
        "ok_count":       ok_count,
        "warn_count":     warn_count,
        "critical_count": critical_count,
        "categories":     categories,
    })
