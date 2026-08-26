"""
Stock Requisition — outlet requests items from CK or vendor.

Roles:
  Any outlet staff    → raise a requisition, view their own
  CK Manager/Owner   → approve, convert to batch or PO
  Owner anywhere     → see all requisitions for the tenant
"""
import json
import logging
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required, role_required
from inventory.models import (
    InventoryItem, RequisitionItem, StockRequisition, PurchaseOrder, PurchaseOrderItem
)

logger = logging.getLogger("pos.inventory")


# ──────────────────────────────────────────────────────────────────────────────
# LIST  /inventory/requisitions/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
def requisition_list(request):
    tenant = request.user.tenant
    outlet = request.user.outlet
    role   = request.user.role

    if role in ("owner", "manager"):
        # Owner/manager see: raised by their outlet + incoming to their outlet (if CK)
        qs = StockRequisition.objects.filter(
            tenant=tenant
        ).filter(
            requesting_outlet=outlet
        ) | StockRequisition.objects.filter(
            tenant=tenant,
            fulfilling_outlet=outlet,
        )
        qs = qs.distinct().select_related("requesting_outlet", "fulfilling_outlet")
    else:
        # Other roles see only their outlet's outgoing requests
        qs = StockRequisition.objects.filter(
            tenant=tenant, requesting_outlet=outlet
        ).select_related("requesting_outlet", "fulfilling_outlet")

    qs = qs.prefetch_related("items__inventory_item").order_by("-created_at")

    # Inventory items for the create form
    inventory_items = InventoryItem.objects.filter(
        tenant=tenant, outlet=outlet
    ).order_by("name")

    # Low-stock items (for quick auto-fill)
    low_stock = InventoryItem.objects.filter(
        tenant=tenant, outlet=outlet,
        stock__lte=F("low_stock_threshold"),
    ).exclude(low_stock_threshold=0)

    return render(request, "inventory/requisitions.html", {
        "requisitions":    qs,
        "inventory_items": inventory_items,
        "low_stock":       low_stock,
        "outlet":          outlet,
    })


# ──────────────────────────────────────────────────────────────────────────────
# CREATE  POST /inventory/requisitions/create/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@require_POST
def create_requisition(request):
    """
    Create a requisition from a list of items.
    Optionally auto-routes (CK vs vendor) immediately.
    """
    tenant = request.user.tenant
    outlet = request.user.outlet

    try:
        data  = json.loads(request.body)
        items = data.get("items", [])
        notes = data.get("notes", "")
        route = data.get("route", "auto")   # auto | internal | external
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not items:
        return JsonResponse({"error": "Add at least one item."}, status=400)

    with transaction.atomic():
        req = StockRequisition.objects.create(
            tenant=tenant,
            requesting_outlet=outlet,
            notes=notes,
            route=route,
            status="draft",
            created_by=request.user,
        )
        for row in items:
            try:
                # Scope by outlet too — an item id from another outlet must not
                # be pullable into this outlet's requisition (IDOR).
                inv = InventoryItem.objects.get(id=row["item_id"], tenant=tenant, outlet=outlet)
                qty = float(row.get("quantity", 0))
                if qty <= 0:
                    continue
                RequisitionItem.objects.create(
                    requisition=req,
                    inventory_item=inv,
                    quantity_requested=qty,
                    unit=row.get("unit", inv.unit),
                    notes=row.get("notes", ""),
                )
            except (InventoryItem.DoesNotExist, KeyError, ValueError):
                continue

        if not req.items.exists():
            req.delete()
            return JsonResponse({"error": "No valid items added."}, status=400)

        # Auto-route if requested
        if route == "auto":
            req.auto_route()

        req.status = "pending"
        req.save()

    logger.info(
        "User %s raised requisition #%s for outlet %s (%d items, route: %s)",
        request.user.username, req.id, outlet.name, req.items.count(), req.route
    )
    return JsonResponse({
        "success": True,
        "requisition_id": req.id,
        "route":   req.route,
        "message": f"Requisition #{req.id} raised — routed to {req.get_route_display()}.",
    })


# ──────────────────────────────────────────────────────────────────────────────
# AUTO-GENERATE FROM LOW STOCK  POST /inventory/requisitions/auto-generate/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@require_POST
def auto_generate_requisition(request):
    """
    End-of-day shortcut: creates a requisition for ALL items
    below their low_stock_threshold at this outlet.
    Uses reorder_quantity as the requested amount.
    """
    tenant = request.user.tenant
    outlet = request.user.outlet

    low_items = InventoryItem.objects.filter(
        tenant=tenant, outlet=outlet,
        stock__lte=F("low_stock_threshold"),
    ).exclude(low_stock_threshold=0)

    if not low_items.exists():
        return JsonResponse({
            "success": True,
            "message": "All stock levels are above threshold — no requisition needed.",
            "created": False,
        })

    with transaction.atomic():
        req = StockRequisition.objects.create(
            tenant=tenant,
            requesting_outlet=outlet,
            notes=f"Auto-generated {timezone.localdate().strftime('%d %b %Y')} — end of day",
            route="auto",
            status="draft",
            created_by=request.user,
        )
        for item in low_items:
            qty = item.reorder_quantity if item.reorder_quantity > 0 else item.low_stock_threshold
            RequisitionItem.objects.create(
                requisition=req,
                inventory_item=item,
                quantity_requested=qty,
                unit=item.unit,
                notes=f"Current stock: {item.stock} {item.unit}",
            )
        req.auto_route()
        req.status = "pending"
        req.save()

    logger.info(
        "Auto-generated requisition #%s for outlet %s — %d items, route: %s",
        req.id, outlet.name, req.items.count(), req.route,
    )
    return JsonResponse({
        "success": True,
        "created": True,
        "requisition_id": req.id,
        "item_count": req.items.count(),
        "route":   req.route,
        "message": f"Requisition #{req.id} created with {req.items.count()} low-stock items — routed to {req.get_route_display()}.",
    })


# ──────────────────────────────────────────────────────────────────────────────
# DETAIL  GET /inventory/requisitions/<id>/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
def requisition_detail(request, req_id):
    tenant = request.user.tenant
    outlet = request.user.outlet

    req = get_object_or_404(StockRequisition, id=req_id, tenant=tenant)

    # Access check: must be requesting outlet OR fulfilling outlet OR owner
    if request.user.role not in ("owner",) and \
       req.requesting_outlet != outlet and req.fulfilling_outlet != outlet:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You don't have access to this requisition.")

    items = req.items.select_related("inventory_item").all()
    is_ck = req.fulfilling_outlet == outlet   # True if viewing as central kitchen

    return render(request, "inventory/requisition_detail.html", {
        "req":   req,
        "items": items,
        "is_ck": is_ck,
    })


# ──────────────────────────────────────────────────────────────────────────────
# APPROVE + ADJUST  POST /inventory/requisitions/<id>/approve/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@role_required("owner", "manager")
@require_POST
def approve_requisition(request, req_id):
    """
    Central kitchen approves the requisition, optionally adjusting quantities.
    Body: { "adjustments": { "<item_id>": <approved_qty> } }
    """
    req = get_object_or_404(
        StockRequisition, id=req_id, tenant=request.user.tenant,
        fulfilling_outlet=request.user.outlet,
    )
    if req.status not in ("pending", "draft"):
        return JsonResponse({"error": "Can only approve pending requisitions."}, status=400)

    try:
        data        = json.loads(request.body)
        adjustments = data.get("adjustments", {})
    except json.JSONDecodeError:
        adjustments = {}

    with transaction.atomic():
        for item in req.items.all():
            approved = adjustments.get(str(item.id))
            if approved is not None:
                try:
                    item.quantity_approved = float(approved)
                    item.save(update_fields=["quantity_approved"])
                except (ValueError, TypeError):
                    pass

        req.status      = "approved"
        req.approved_by = request.user
        req.approved_at = timezone.now()
        req.save(update_fields=["status", "approved_by", "approved_at"])

    logger.info("User %s approved requisition #%s", request.user.username, req_id)
    return JsonResponse({"success": True, "message": f"Requisition #{req_id} approved."})


# ──────────────────────────────────────────────────────────────────────────────
# CONVERT TO BATCH (internal route)  POST /inventory/requisitions/<id>/to-batch/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@role_required("owner", "manager")
@require_POST
def convert_to_batch(request, req_id):
    """
    Central kitchen converts an approved requisition into a ProductionBatch.
    Items and quantities come from the requisition.
    """
    from inventory.models import ProductionBatch, BatchItem

    req = get_object_or_404(
        StockRequisition, id=req_id, tenant=request.user.tenant
    )
    if req.status != "approved":
        return JsonResponse({"error": "Requisition must be approved first."}, status=400)
    if req.route not in ("internal", "auto"):
        return JsonResponse({"error": "This requisition is routed to a vendor, not CK."}, status=400)

    today_str = timezone.localdate().strftime("%Y%m%d")
    existing  = ProductionBatch.objects.filter(
        tenant=req.tenant, batch_number__startswith=f"BATCH-{today_str}-"
    ).count()
    batch_number = f"BATCH-{today_str}-{existing + 1:03d}"

    with transaction.atomic():
        batch = ProductionBatch.objects.create(
            tenant=req.tenant,
            batch_number=batch_number,
            source_outlet=request.user.outlet,
            created_by=request.user,
            notes=f"From Requisition #{req.id} — {req.requesting_outlet.name}",
        )
        for idx, req_item in enumerate(req.items.select_related("inventory_item").all()):
            qty = req_item.effective_quantity
            barcode = f"{batch_number}-{idx + 1:03d}"
            BatchItem.objects.create(
                batch=batch,
                inventory_item=req_item.inventory_item,
                quantity=qty,
                unit=req_item.unit,
                barcode=barcode,
            )
            # Auto-create BatchTransfer to requesting outlet
            from inventory.models import BatchTransfer
            BatchTransfer.objects.create(
                tenant=req.tenant,
                batch=batch,
                destination_outlet=req.requesting_outlet,
                status="pending",
            )

        req.production_batch = batch
        req.status           = "in_production"
        req.save(update_fields=["production_batch", "status"])

    logger.info(
        "Requisition #%s converted to batch %s by %s",
        req_id, batch_number, request.user.username,
    )
    return JsonResponse({
        "success":      True,
        "batch_id":     batch.id,
        "batch_number": batch_number,
        "message":      f"Batch {batch_number} created — go to Central Kitchen to dispatch.",
    })


# ──────────────────────────────────────────────────────────────────────────────
# CONVERT TO PO (external route)  POST /inventory/requisitions/<id>/to-po/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@role_required("owner", "manager")
@require_POST
def convert_to_po(request, req_id):
    """
    Converts an approved requisition into a Purchase Order to the vendor.
    Uses preferred_supplier from each InventoryItem.
    Groups items by supplier — one PO per supplier.

    PurchaseOrder enforces at most one draft PO per (tenant, outlet,
    supplier) — see PurchaseOrder.Meta.constraints. If a draft already
    exists for a supplier (e.g. the auto-reorder-on-low-stock path already
    created one), this reuses it and merges the requisition's items in,
    instead of a plain .create() that would raise IntegrityError and crash
    the request. get_or_create + select_for_update mirrors the same pattern
    InventoryItem.trigger_reorder already uses correctly.
    """
    from inventory.models import generate_po_number

    req = get_object_or_404(
        StockRequisition, id=req_id, tenant=request.user.tenant
    )
    if req.status not in ("approved", "pending"):
        return JsonResponse({"error": "Approve the requisition before creating a PO."}, status=400)

    outlet = req.requesting_outlet

    # Group items by supplier
    supplier_groups = {}   # supplier_id → [RequisitionItem]
    skipped_items = []     # names of items with no preferred_supplier
    for item in req.items.select_related("inventory_item__preferred_supplier").all():
        sup = item.inventory_item.preferred_supplier
        if sup is None:
            # A vendor PO requires a supplier. PurchaseOrder.supplier is a
            # non-nullable PROTECT FK, so creating one with supplier=None
            # would raise IntegrityError. These items simply can't be
            # ordered yet — skip them and report exactly which ones,
            # instead of crashing or reporting only a bare count.
            skipped_items.append(item.inventory_item.name)
            continue
        supplier_groups.setdefault(sup.id, {"supplier": sup, "items": []})
        supplier_groups[sup.id]["items"].append(item)

    created_pos = []
    reused_pos = []
    with transaction.atomic():
        for key, group in supplier_groups.items():
            supplier = group["supplier"]

            po, created = PurchaseOrder.objects.select_for_update().get_or_create(
                tenant=req.tenant,
                outlet=outlet,
                supplier=supplier,
                status="draft",
                defaults={"notes": f"From Requisition #{req.id}"},
            )

            if not po.po_number:
                po.po_number = generate_po_number(req.tenant, outlet)
                po.save(update_fields=["po_number"])

            if not created:
                # Reusing an existing draft (e.g. auto-generated from a low
                # stock trigger) — record that this requisition also fed
                # into it, without disturbing whatever notes it already had.
                addition = f"Requisition #{req.id}"
                po.notes = f"{po.notes}; {addition}" if po.notes else addition
                po.save(update_fields=["notes"])

            for req_item in group["items"]:
                line, line_created = PurchaseOrderItem.objects.get_or_create(
                    purchase_order=po,
                    item=req_item.inventory_item,
                    defaults={
                        "quantity": req_item.effective_quantity,
                        "unit_price": req_item.inventory_item.cost_price or 0,
                    },
                )
                if not line_created:
                    # Same item already on this draft PO (from a previous
                    # trigger) — add to the existing quantity rather than
                    # silently ignoring what this requisition also needs.
                    line.quantity = F("quantity") + req_item.effective_quantity
                    line.save(update_fields=["quantity"])

            # Recalculate total from the PO's current items (post-merge).
            total = sum(i.quantity * i.unit_price for i in po.items.all())
            PurchaseOrder.objects.filter(pk=po.pk).update(total_amount=total)

            entry = {"po_id": po.id, "po_number": po.po_number}
            (created_pos if created else reused_pos).append(entry)

        all_pos = created_pos + reused_pos
        if not all_pos:
            # Nothing was ordered (no item had a preferred supplier). No rows
            # were written, so returning here leaves the DB untouched.
            return JsonResponse(
                {"error": "No items have a preferred supplier set — nothing to order. "
                          "Set a preferred supplier on the items first.",
                 "skipped_items": skipped_items},
                status=400,
            )

        # The requisition FK holds exactly one PO. Link the first one
        # touched (created or reused).
        req.purchase_order = PurchaseOrder.objects.get(pk=all_pos[0]["po_id"])
        req.status = "ordered"
        req.save(update_fields=["purchase_order", "status"])

    logger.info(
        "Requisition #%s converted: %d new PO(s), %d merged into existing draft(s), "
        "by %s (%d item(s) skipped, no supplier)",
        req_id, len(created_pos), len(reused_pos), request.user.username, len(skipped_items),
    )
    msg_parts = []
    if created_pos:
        msg_parts.append(f"{len(created_pos)} new PO(s) created")
    if reused_pos:
        msg_parts.append(f"{len(reused_pos)} item(s) added to an existing draft PO")
    msg = ", ".join(msg_parts) + f" from requisition #{req_id}."
    if skipped_items:
        msg += f" Skipped (no preferred supplier): {', '.join(skipped_items)}."
    return JsonResponse({
        "success":      True,
        "pos_created":  len(created_pos),
        "pos_reused":   len(reused_pos),
        "pos":          all_pos,
        "skipped":      len(skipped_items),
        "skipped_items": skipped_items,
        "message":      msg,
    })


# ──────────────────────────────────────────────────────────────────────────────
# CANCEL  POST /inventory/requisitions/<id>/cancel/
# ──────────────────────────────────────────────────────────────────────────────

@login_required
@tenant_required
@feature_required("central_kitchen")
@require_POST
def cancel_requisition(request, req_id):
    req = get_object_or_404(
        StockRequisition, id=req_id, tenant=request.user.tenant,
        requesting_outlet=request.user.outlet,
    )
    if req.status in ("fulfilled", "ordered", "in_production"):
        return JsonResponse({"error": "Cannot cancel — already being processed."}, status=400)
    req.status = "cancelled"
    req.save(update_fields=["status"])
    return JsonResponse({"success": True, "message": "Requisition cancelled."})
