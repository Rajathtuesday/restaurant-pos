"""
Central Kitchen — production batch management, dispatch, and receiving.

Flow:
  SOURCE OUTLET (central kitchen):
    1. Create a ProductionBatch with items + barcodes
    2. Create BatchTransfers to destination outlets
    3. Dispatch (mark in_transit)

  DESTINATION OUTLET (branch):
    1. See pending incoming transfers
    2. Confirm receipt (or scan barcode) → stock added automatically
"""
import json
import logging
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from core.decorators import tenant_required, feature_required, role_required
from inventory.models import (
    BatchItem, BatchTransfer, InventoryItem, ProductionBatch,
)
from tenants.models import Outlet

logger = logging.getLogger("pos.inventory")

_ALLOWED = ("owner", "manager")


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD  /inventory/central-kitchen/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
def central_kitchen_dashboard(request):
    """
    Shows two panels:
      LEFT  — batches this outlet SENT (source outlet view)
      RIGHT — transfers this outlet is RECEIVING (destination outlet view)
    """
    tenant = request.user.tenant
    outlet = request.user.outlet

    # Batches created by this outlet (central kitchen role)
    sent_batches = (
        ProductionBatch.objects
        .filter(tenant=tenant, source_outlet=outlet)
        .prefetch_related("items", "transfers", "transfers__destination_outlet")
        .order_by("-created_at")[:30]
    )

    # Incoming transfers for this outlet
    incoming = (
        BatchTransfer.objects
        .filter(
            tenant=tenant,
            destination_outlet=outlet,
            status__in=["pending", "in_transit"],
        )
        .select_related("batch", "batch__source_outlet")
        .prefetch_related("batch__items__inventory_item")
        .order_by("-batch__created_at")
    )

    # All outlets in this tenant for the "send to" selector
    all_outlets = Outlet.objects.filter(tenant=tenant).exclude(id=outlet.id)

    # Inventory items for the "add item" form
    inventory_items = InventoryItem.objects.filter(
        tenant=tenant, outlet=outlet
    ).order_by("name")

    return render(request, "inventory/central_kitchen.html", {
        "sent_batches":     sent_batches,
        "incoming":         incoming,
        "all_outlets":      all_outlets,
        "inventory_items":  inventory_items,
        "outlet":           outlet,
    })


# ─────────────────────────────────────────────────────────────────────────────
# CREATE BATCH  POST /inventory/central-kitchen/batch/create/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@role_required("owner", "manager")
@require_POST
def create_batch(request):
    """Create a new ProductionBatch with its initial items."""
    tenant = request.user.tenant
    outlet = request.user.outlet

    notes = request.POST.get("notes", "").strip()

    # Auto-generate batch number: BATCH-YYYYMMDD-NNN
    today_str = timezone.localdate().strftime("%Y%m%d")
    existing = ProductionBatch.objects.filter(
        tenant=tenant, batch_number__startswith=f"BATCH-{today_str}-"
    ).count()
    batch_number = f"BATCH-{today_str}-{existing + 1:03d}"

    with transaction.atomic():
        batch = ProductionBatch.objects.create(
            tenant=tenant,
            batch_number=batch_number,
            source_outlet=outlet,
            created_by=request.user,
            notes=notes,
        )

        # Items passed as JSON array: [{item_id, quantity, unit}]
        items_json = request.POST.get("items", "[]")
        try:
            items_data = json.loads(items_json)
        except json.JSONDecodeError:
            items_data = []

        for idx, row in enumerate(items_data):
            try:
                inv_item = InventoryItem.objects.get(
                    id=row["item_id"], tenant=tenant
                )
                qty = float(row.get("quantity", 0))
                if qty <= 0:
                    continue
                barcode = f"{batch_number}-{idx + 1:03d}"
                BatchItem.objects.create(
                    batch=batch,
                    inventory_item=inv_item,
                    quantity=qty,
                    unit=row.get("unit", inv_item.unit),
                    barcode=barcode,
                )
            except (InventoryItem.DoesNotExist, KeyError, ValueError):
                continue

    logger.info(
        "User %s created batch %s with %d items",
        request.user.username, batch_number, batch.items.count()
    )
    return JsonResponse({
        "success": True,
        "batch_id": batch.id,
        "batch_number": batch_number,
        "message": f"Batch {batch_number} created with {batch.items.count()} items.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# BATCH DETAIL  GET /inventory/central-kitchen/batch/<id>/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
def batch_detail(request, batch_id):
    batch = get_object_or_404(
        ProductionBatch, id=batch_id, tenant=request.user.tenant
    )
    items     = batch.items.select_related("inventory_item").all()
    transfers = batch.transfers.select_related("destination_outlet", "received_by").all()
    all_outlets = Outlet.objects.filter(
        tenant=request.user.tenant
    ).exclude(id=batch.source_outlet.id)

    # Which outlets don't have a transfer yet?
    transferred_outlet_ids = set(t.destination_outlet_id for t in transfers)
    available_outlets = [o for o in all_outlets if o.id not in transferred_outlet_ids]

    return render(request, "inventory/batch_detail.html", {
        "batch":             batch,
        "items":             items,
        "transfers":         transfers,
        "available_outlets": available_outlets,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ADD ITEM TO BATCH  POST /inventory/central-kitchen/batch/<id>/add-item/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@role_required("owner", "manager")
@require_POST
def add_batch_item(request, batch_id):
    batch = get_object_or_404(
        ProductionBatch, id=batch_id, tenant=request.user.tenant,
        source_outlet=request.user.outlet
    )
    if batch.transfers.filter(status__in=["in_transit", "received"]).exists():
        return JsonResponse(
            {"error": "Cannot add items — batch has already been dispatched."},
            status=400
        )

    try:
        data     = json.loads(request.body)
        inv_item = InventoryItem.objects.get(
            id=data["item_id"], tenant=request.user.tenant
        )
        qty = float(data.get("quantity", 0))
        if qty <= 0:
            return JsonResponse({"error": "Quantity must be positive."}, status=400)

        idx     = batch.items.count() + 1
        barcode = f"{batch.batch_number}-{idx:03d}"

        item = BatchItem.objects.create(
            batch=batch,
            inventory_item=inv_item,
            quantity=qty,
            unit=data.get("unit", inv_item.unit),
            barcode=barcode,
        )
        return JsonResponse({
            "success": True,
            "item": {
                "id": item.id, "name": inv_item.name,
                "quantity": qty, "unit": item.unit, "barcode": barcode,
            }
        })
    except (InventoryItem.DoesNotExist, KeyError, ValueError) as e:
        return JsonResponse({"error": str(e)}, status=400)


# ─────────────────────────────────────────────────────────────────────────────
# CREATE TRANSFERS  POST /inventory/central-kitchen/batch/<id>/transfer/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@role_required("owner", "manager")
@require_POST
def create_transfers(request, batch_id):
    """Create BatchTransfer records for selected outlet IDs."""
    batch = get_object_or_404(
        ProductionBatch, id=batch_id, tenant=request.user.tenant
    )
    if not batch.items.exists():
        return JsonResponse(
            {"error": "Add at least one item to the batch before creating transfers."},
            status=400
        )

    try:
        data       = json.loads(request.body)
        outlet_ids = data.get("outlet_ids", [])
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    if not outlet_ids:
        return JsonResponse({"error": "Select at least one outlet."}, status=400)

    created = 0
    skipped = 0
    with transaction.atomic():
        for oid in outlet_ids:
            outlet = Outlet.objects.filter(id=oid, tenant=request.user.tenant).first()
            if not outlet:
                continue
            _, was_created = BatchTransfer.objects.get_or_create(
                tenant=request.user.tenant,
                batch=batch,
                destination_outlet=outlet,
                defaults={"status": "pending"},
            )
            if was_created:
                created += 1
            else:
                skipped += 1

    return JsonResponse({
        "success": True,
        "created": created,
        "skipped": skipped,
        "message": f"{created} transfer(s) created. {skipped} already existed.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# DISPATCH BATCH  POST /inventory/central-kitchen/batch/<id>/dispatch/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@role_required("owner", "manager")
@require_POST
def dispatch_batch(request, batch_id):
    """Mark all pending transfers for this batch as in_transit."""
    batch = get_object_or_404(
        ProductionBatch, id=batch_id, tenant=request.user.tenant
    )
    now      = timezone.now()
    updated  = BatchTransfer.objects.filter(
        batch=batch, status="pending"
    ).update(status="in_transit", dispatched_at=now)

    logger.info(
        "User %s dispatched batch %s to %d outlets",
        request.user.username, batch.batch_number, updated
    )
    return JsonResponse({
        "success": True,
        "dispatched": updated,
        "message": f"Batch dispatched to {updated} outlet(s).",
    })


# ─────────────────────────────────────────────────────────────────────────────
# RECEIVE — list incoming transfers for this outlet
# GET /inventory/central-kitchen/receive/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
def receive_page(request):
    tenant = request.user.tenant
    outlet = request.user.outlet

    incoming = (
        BatchTransfer.objects
        .filter(tenant=tenant, destination_outlet=outlet)
        .select_related("batch", "batch__source_outlet", "received_by")
        .prefetch_related("batch__items__inventory_item")
        .order_by("-batch__created_at")
    )
    return render(request, "inventory/receive_transfers.html", {
        "incoming": incoming,
        "outlet":   outlet,
    })


# ─────────────────────────────────────────────────────────────────────────────
# BARCODE SCAN API  GET /inventory/central-kitchen/scan/?barcode=XXX
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
def scan_barcode(request):
    """AJAX: looks up a barcode → returns the BatchItem + transfer info."""
    barcode = request.GET.get("barcode", "").strip()
    if not barcode:
        return JsonResponse({"error": "No barcode provided."}, status=400)

    item = BatchItem.objects.filter(barcode=barcode).select_related(
        "batch", "batch__source_outlet", "inventory_item"
    ).first()

    if not item:
        return JsonResponse({"error": f"Barcode '{barcode}' not found."}, status=404)

    # Verify this transfer is for this outlet
    transfer = BatchTransfer.objects.filter(
        batch=item.batch,
        destination_outlet=request.user.outlet,
        tenant=request.user.tenant,
    ).first()

    if not transfer:
        return JsonResponse({
            "error": "This item is not part of a transfer to your outlet."
        }, status=403)

    if transfer.status == "received":
        return JsonResponse({
            "error": "This transfer has already been received.",
            "already_received": True,
        }, status=400)

    return JsonResponse({
        "success":        True,
        "transfer_id":    transfer.id,
        "batch_number":   item.batch.batch_number,
        "from_outlet":    item.batch.source_outlet.name,
        "item_name":      item.inventory_item.name,
        "quantity":       float(item.quantity),
        "unit":           item.unit,
        "barcode":        barcode,
        "transfer_status": transfer.status,
    })


# ─────────────────────────────────────────────────────────────────────────────
# CONFIRM RECEIPT  POST /inventory/central-kitchen/receive/<id>/confirm/
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@require_POST
def confirm_receive(request, transfer_id):
    """
    Marks a BatchTransfer as received and adds stock to each BatchItem's
    InventoryItem at the destination outlet.

    Edge cases handled:
    - Already received: idempotent (returns success, no double-add)
    - Inventory item doesn't exist at this outlet: creates it
    - Partial receive: not yet supported (all-or-nothing per transfer)
    """
    tenant   = request.user.tenant
    outlet   = request.user.outlet

    transfer = get_object_or_404(
        BatchTransfer,
        id=transfer_id,
        tenant=tenant,
        destination_outlet=outlet,
    )

    if transfer.status == "received":
        return JsonResponse({
            "success": True,
            "message": "Already received — no changes made.",
            "already_done": True,
        })

    with transaction.atomic():
        items = transfer.batch.items.select_related("inventory_item").all()
        stock_updates = []

        for batch_item in items:
            # Find or create the InventoryItem at THIS outlet
            inv_item, created = InventoryItem.objects.get_or_create(
                tenant=tenant,
                outlet=outlet,
                name=batch_item.inventory_item.name,
                defaults={
                    "unit":               batch_item.unit,
                    "stock":              0,
                    "cost_price":         batch_item.inventory_item.cost_price,
                    "low_stock_threshold": batch_item.inventory_item.low_stock_threshold,
                }
            )
            # Add the received stock
            inv_item.add_stock(
                batch_item.quantity,
                reference=f"Transfer from {transfer.batch.source_outlet.name} — {transfer.batch.batch_number}"
            )
            stock_updates.append({
                "item":     inv_item.name,
                "added":    float(batch_item.quantity),
                "unit":     batch_item.unit,
                "new_stock": float(inv_item.stock),
                "created":  created,
            })

        transfer.status      = "received"
        transfer.received_at = timezone.now()
        transfer.received_by = request.user
        transfer.save(update_fields=["status", "received_at", "received_by"])

    logger.info(
        "User %s received transfer #%s at outlet %s (%d items)",
        request.user.username, transfer_id, outlet.name, len(stock_updates)
    )
    return JsonResponse({
        "success":       True,
        "message":       f"Received {len(stock_updates)} item(s). Stock updated.",
        "stock_updates": stock_updates,
    })
