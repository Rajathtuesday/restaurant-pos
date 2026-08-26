# inventory/models.py
from django.db import models, transaction
from core.models import TenantScopedModel
from django.db.models import F, Q, CheckConstraint
from decimal import Decimal
from django.core.exceptions import ValidationError
from notifications.services.notification_service import create_notification


UNIT_CHOICES = [
    ("pcs", "Pieces"),
    ("g", "Grams"),
    ("kg", "Kilograms"),
    ("ml", "Milliliters"),
    ("l", "Liters"),
]


TRANSACTION_TYPES = [
    ("restock", "Restock"),
    ("consume", "Consumption"),
    ("wastage", "Wastage"),
    ("adjustment", "Manual Adjustment"),
]


# -------------------------------------------------------
# INVENTORY ITEM
# -------------------------------------------------------

class InventoryItem(TenantScopedModel):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=255)

    # Free-text, not a separate model — restaurants group ingredients very
    # differently (Veggies/Dairy/Spices vs Bar/Kitchen/Dry Store), so a
    # rigid choices list or a full CRUD-able category model would fight
    # whatever convention each tenant already uses. Blank means "unfiled".
    category = models.CharField(max_length=100, blank=True, default="")

    unit = models.CharField(
        max_length=10,
        choices=UNIT_CHOICES
    )

    stock = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0
    )

    low_stock_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0
    )
    
    reorder_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        help_text="Standard quantity to order when stock is low"
    )

    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Current cost price per unit"
    )

    last_purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Price from the last purchase order"
    )
    
    preferred_supplier = models.ForeignKey(
        "Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)


    class Meta:

        constraints = [

            CheckConstraint(
                condition=Q(stock__gte=0),
                name="inventory_stock_non_negative"
            )

        ]

        indexes = [
            models.Index(fields=["tenant", "outlet"]),
        ]


    # -------------------------------------------------------
    # REDUCE STOCK (USED BY KITCHEN)
    # -------------------------------------------------------

    def reduce_stock(self, quantity, reference=None):

        quantity = Decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be positive")

        with transaction.atomic():

            item = InventoryItem.objects.select_for_update().get(id=self.id)

            if item.stock < quantity:
                raise ValidationError(
                    f"Insufficient stock for {item.name}"
                )

            new_stock = item.stock - quantity

            item.stock = F("stock") - quantity
            item.save(update_fields=["stock"])

            InventoryTransaction.objects.create(
                item=item,
                tenant=item.tenant,
                outlet=item.outlet,
                quantity=-quantity,
                transaction_type="consume",
                reference=reference
            )

            if new_stock <= item.low_stock_threshold:
                # trigger_reorder is a DB write — keep it in-transaction so
                # TestCase tests and rollbacks behave correctly.
                if item.preferred_supplier and item.reorder_quantity > 0:
                    try:
                        item.trigger_reorder()
                    except Exception as e:
                        import logging
                        logging.getLogger("pos.inventory").error(
                            "Auto-reorder failed for %s: %s", item.name, e
                        )

                # Notification is a side-effect; defer to after commit so it
                # doesn't fire for transactions that are later rolled back.
                _tenant = item.tenant
                _outlet = item.outlet
                _name = item.name
                _unit = item.unit
                _new_stock = new_stock

                def _notify():
                    create_notification(
                        _tenant, _outlet, "low_stock",
                        f"{_name} low stock ({_new_stock} {_unit})"
                    )

                transaction.on_commit(_notify)


    def trigger_reorder(self):
        """
        Creates a draft Purchase Order for the preferred supplier.
        """
        from django.utils import timezone

        with transaction.atomic():
            po, created = PurchaseOrder.objects.select_for_update().get_or_create(
                tenant=self.tenant,
                outlet=self.outlet,
                supplier=self.preferred_supplier,
                status="draft",
                defaults={"notes": f"Auto-generated due to low stock on {timezone.now().date()}"}
            )

            if not po.po_number:
                po.po_number = generate_po_number(self.tenant, self.outlet)
                po.save(update_fields=["po_number"])

            # Add item to PO if not already there
            PurchaseOrderItem.objects.get_or_create(
                purchase_order=po,
                item=self,
                defaults={"quantity": self.reorder_quantity, "unit_price": self.cost_price}
            )

            # Recalculate total
            total = sum(i.quantity * i.unit_price for i in po.items.all())
            PurchaseOrder.objects.filter(pk=po.pk).update(total_amount=total)


    # -------------------------------------------------------
    # ADD STOCK
    # -------------------------------------------------------

    def add_stock(self, quantity, reference=None):

        quantity = Decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be positive")

        with transaction.atomic():

            item = InventoryItem.objects.select_for_update().get(id=self.id)

            item.stock = F("stock") + quantity
            item.save(update_fields=["stock"])

            InventoryTransaction.objects.create(
                item=item,
                tenant=item.tenant,
                outlet=item.outlet,
                quantity=quantity,
                transaction_type="restock",
                reference=reference
            )


    # -------------------------------------------------------
    # WASTAGE
    # -------------------------------------------------------

    def record_wastage(self, quantity, reference=None):

        quantity = Decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be positive")

        with transaction.atomic():

            item = InventoryItem.objects.select_for_update().get(id=self.id)

            if item.stock < quantity:
                raise ValidationError("Not enough stock")

            item.stock = F("stock") - quantity
            item.save(update_fields=["stock"])

            InventoryTransaction.objects.create(
                item=item,
                tenant=item.tenant,
                outlet=item.outlet,
                quantity=-quantity,
                transaction_type="wastage",
                reference=reference
            )


    # -------------------------------------------------------
    # LOW STOCK CHECK
    # -------------------------------------------------------

    @property
    def is_low_stock(self):

        return self.stock <= self.low_stock_threshold


    def __str__(self):

        return f"{self.name} ({self.stock} {self.unit})"



# -------------------------------------------------------
# SUPPLIER
# -------------------------------------------------------

class Supplier(TenantScopedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    
    name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    gst_no = models.CharField(max_length=15, blank=True)
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


# -------------------------------------------------------
# PURCHASE ORDER
# -------------------------------------------------------

class TenantPOCounter(TenantScopedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    value = models.IntegerField(default=0)

    class Meta:
        unique_together = ("tenant", "outlet")


def generate_po_number(tenant, outlet):
    """
    Atomically generates the next PO number for a tenant+outlet, using the
    same counter row every PO-creation path shares. Previously convert_to_po
    (inventory/requisition_views.py) generated numbers via
    PurchaseOrder.objects.filter(...).count(), a real race condition under
    concurrent requests, in a completely different format from this one.
    Both paths now call this single function instead.
    """
    from django.utils import timezone

    with transaction.atomic():
        counter, _ = TenantPOCounter.objects.select_for_update().get_or_create(
            tenant=tenant, outlet=outlet
        )
        counter.value += 1
        counter.save(update_fields=["value"])
        return f"PO-{outlet.id}-{timezone.now().year}-{counter.value:04d}"


class PurchaseOrder(TenantScopedModel):
    STATUS_CHOICES = (
        ("draft", "Draft"),
        ("ordered", "Ordered"),
        ("received", "Received"),
        ("cancelled", "Cancelled"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    
    po_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    ordered_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "outlet", "supplier"],
                condition=Q(status="draft"),
                name="unique_draft_po_per_supplier"
            )
        ]

    def __str__(self):
        return f"PO-{self.id} | {self.supplier.name} | {self.status}"

    @transaction.atomic
    def receive_order(self):
        """
        Marks PO as received and updates inventory stock atomically.

        Deliberately avoids calling add_stock() to prevent nested
        transaction.atomic + select_for_update deadlocks: we already hold
        row-locks on PurchaseOrder (from the view) and PurchaseOrderItem
        (via select_for_update below), so we write stock directly using
        F() expressions and create the ledger entries inline.
        """
        if self.status == "received":
            return

        from django.utils import timezone

        # Lock all inventory items referenced by this PO in a single query
        # to prevent deadlocks (consistent lock-ordering).
        item_ids = list(self.items.values_list("item_id", flat=True))
        locked_items = {
            item.id: item
            for item in InventoryItem.objects.select_for_update().filter(id__in=item_ids)
        }

        reference = f"PO #{self.po_number or self.id}"
        transactions_to_create = []

        for item_link in self.items.select_for_update().select_related("item"):
            inv_item = locked_items[item_link.item_id]
            qty = Decimal(item_link.quantity)

            # Update stock with F() to avoid stale-read races
            InventoryItem.objects.filter(pk=inv_item.pk).update(
                stock=F("stock") + qty,
                last_purchase_price=item_link.unit_price,
            )

            transactions_to_create.append(
                InventoryTransaction(
                    item=inv_item,
                    tenant=inv_item.tenant,
                    outlet=inv_item.outlet,
                    quantity=qty,
                    transaction_type="restock",
                    reference=reference,
                )
            )

        InventoryTransaction.objects.bulk_create(transactions_to_create)

        self.status = "received"
        self.received_at = timezone.now()
        self.save(update_fields=["status", "received_at"])


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.item.name} x {self.quantity}"



# -------------------------------------------------------
# INVENTORY TRANSACTION LEDGER
# -------------------------------------------------------

class InventoryTransaction(TenantScopedModel):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="transactions"
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES
    )

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3
    )

    reference = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        indexes = [
            models.Index(fields=["tenant", "outlet"]),
            models.Index(fields=["item"]),
            models.Index(fields=["item", "created_at"],              name="invtxn_item_date"),
            models.Index(fields=["tenant", "outlet", "created_at"],  name="invtxn_outlet_date"),
        ]

    def __str__(self):
        return f"{self.transaction_type} {self.quantity} {self.item.name}"



# -------------------------------------------------------
# RECIPE
# -------------------------------------------------------

class Recipe(models.Model):

    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.CASCADE,
        related_name="recipes"
    )

    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE
    )

    quantity_required = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    unit = models.CharField(
        max_length=10,
        choices=UNIT_CHOICES,
        default="g"
    )

    class Meta:
        unique_together = ("menu_item", "inventory_item")

    def clean(self):
        if (
            self.menu_item_id
            and self.inventory_item_id
            and self.menu_item.tenant_id != self.inventory_item.tenant_id
        ):
            raise ValidationError("Recipe cannot link items across tenants.")

    def __str__(self):
        return f"{self.menu_item.name} → {self.quantity_required} {self.unit}"


class ModifierRecipe(models.Model):
    """
    Links a Modifier to an InventoryItem.
    When an OrderItemModifier is selected, inventory_item is deducted
    by quantity_required × order_item.quantity on KOT creation.
    Example: modifier "Extra Shot" → InventoryItem "Rum" 60ml
    """
    modifier = models.ForeignKey(
        "menu.Modifier",
        on_delete=models.CASCADE,
        related_name="inventory_links"
    )
    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="modifier_recipes"
    )
    quantity_required = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default="ml")

    class Meta:
        unique_together = ("modifier", "inventory_item")

    def __str__(self):
        return f"{self.modifier.name} → {self.inventory_item.name} ×{self.quantity_required}{self.unit}"


# -------------------------------------------------------
# CENTRAL KITCHEN PRODUCTION (FRANCHISE MODE)
# -------------------------------------------------------

class ProductionBatch(TenantScopedModel):
    BATCH_TYPE = (
        # New goods cooked/assembled from ingredients (e.g. "Gravy Base").
        # The finished good is created fresh; nothing is debited on dispatch.
        ("produce", "Produced — new goods from ingredients"),
        # Existing stock the central kitchen already holds is physically moved
        # to a branch. Dispatch DEBITS the kitchen's own stock so total
        # tenant-wide inventory stays constant across the transfer.
        ("move", "Moved — existing kitchen stock leaves"),
    )

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=50)
    batch_type = models.CharField(
        max_length=10, choices=BATCH_TYPE, default="produce",
        help_text=(
            "'move' debits the central kitchen's own stock when the batch is "
            "dispatched; 'produce' does not (the goods are newly made)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    source_outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE,
        related_name='batches_sent'
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ('tenant', 'batch_number')
        indexes = [
            models.Index(fields=["tenant", "source_outlet"]),
        ]

    def __str__(self):
        return f"Batch {self.batch_number}"


class BatchItem(TenantScopedModel):
    batch = models.ForeignKey(ProductionBatch, on_delete=models.CASCADE, related_name='items')
    # tenant denormalised here so barcode uniqueness can be enforced per-tenant at the DB level.
    # A globally-unique barcode (the old approach) collides when two franchise outlets use
    # sequential numbering starting from 1.
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, null=True, blank=True)
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES)
    barcode = models.CharField(max_length=100)

    class Meta:
        unique_together = (("tenant", "barcode"),)

    def __str__(self):
        return f"{self.inventory_item.name} ({self.barcode})"


class BatchTransfer(TenantScopedModel):
    STATUS = (
        ('pending', 'Pending'),
        ('in_transit', 'In Transit'),
        ('received', 'Received'),
        ('partial', 'Partially Received')
    )
    
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    batch = models.ForeignKey(ProductionBatch, on_delete=models.CASCADE, related_name='transfers')
    destination_outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE,
        related_name='transfers_received'
    )
    status = models.CharField(max_length=20, choices=STATUS, default='pending')
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="received_transfers")

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "destination_outlet", "status"]),
        ]
        constraints = [
            # create_transfers relies on get_or_create to avoid duplicate
            # transfers for the same batch+destination; back it with a real DB
            # constraint so a concurrent double-submit can't create two.
            models.UniqueConstraint(
                fields=["tenant", "batch", "destination_outlet"],
                name="unique_transfer_per_batch_destination",
            )
        ]

    def __str__(self):
        return f"Transfer {self.batch.batch_number} to {self.destination_outlet.name}"


# -------------------------------------------------------
# STOCK REQUISITION
# Outlet requests items from central kitchen or vendor.
# System auto-routes based on whether a CK outlet exists.
# -------------------------------------------------------

class StockRequisition(TenantScopedModel):

    STATUS = [
        ("draft",         "Draft"),
        ("pending",       "Pending"),
        ("approved",      "Approved — Ready to Fulfill"),
        ("in_production", "In Production (CK Processing)"),
        ("ordered",       "Ordered from Vendor"),
        ("fulfilled",     "Fulfilled"),
        ("cancelled",     "Cancelled"),
    ]

    ROUTE = [
        ("auto",     "Auto-route"),           # system decides
        ("internal", "Central Kitchen"),      # internal transfer
        ("external", "Vendor PO"),            # external purchase
        ("split",    "Split (CK + Vendor)"),  # some from CK, rest from vendor
    ]

    tenant            = models.ForeignKey("tenants.Tenant",  on_delete=models.CASCADE)
    requesting_outlet = models.ForeignKey(
        "tenants.Outlet", on_delete=models.CASCADE, related_name="requisitions_raised"
    )

    status = models.CharField(max_length=20, choices=STATUS, default="draft")
    route  = models.CharField(max_length=20, choices=ROUTE,  default="auto")

    notes  = models.TextField(blank=True)

    # Set when routed
    fulfilling_outlet = models.ForeignKey(
        "tenants.Outlet", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="requisitions_to_fulfill",
        help_text="Central kitchen outlet that will fulfill this (if internal)"
    )

    # Links to downstream fulfillment records
    production_batch = models.ForeignKey(
        ProductionBatch, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Set when CK converts this requisition to a batch"
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Set when converted to a vendor PO"
    )

    created_by  = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, related_name="requisitions_created")
    created_at  = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="requisitions_approved")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes  = [
            models.Index(fields=["tenant", "requesting_outlet", "status"]),
            models.Index(fields=["tenant", "fulfilling_outlet", "status"]),
        ]

    def __str__(self):
        return f"Req #{self.id} — {self.requesting_outlet.name} [{self.status}]"

    def is_editable(self):
        return self.status in ("draft", "pending")

    def auto_route(self):
        """
        Decide whether to fulfill from central kitchen or vendor.
        Sets self.route and self.fulfilling_outlet.
        Does NOT save — caller must save.
        """
        from tenants.models import Outlet as _Outlet

        # Prefer the explicit flag. Fall back to the old history-based guess
        # only for tenants that haven't set it yet, so existing tenants
        # aren't silently misrouted the moment this field shipped.
        ck_outlet = _Outlet.objects.filter(
            tenant=self.tenant, is_central_kitchen=True
        ).first()

        if not ck_outlet:
            # Convention: the outlet that is the source of any ProductionBatch
            ck = (
                ProductionBatch.objects
                .filter(tenant=self.tenant)
                .values_list("source_outlet", flat=True)
                .first()
            )
            if ck:
                ck_outlet = _Outlet.objects.filter(id=ck).first()

        if not ck_outlet or ck_outlet == self.requesting_outlet:
            # No central kitchen or CK is us — go to vendor
            self.route = "external"
            self.fulfilling_outlet = None
            return

        # Check if CK has sufficient stock for all items
        items     = list(self.items.select_related("inventory_item").all())
        can_fill  = True
        for req_item in items:
            ck_stock = InventoryItem.objects.filter(
                tenant=self.tenant, outlet=ck_outlet,
                name=req_item.inventory_item.name,
            ).values_list("stock", flat=True).first() or 0
            if ck_stock < req_item.quantity_requested:
                can_fill = False
                break

        if can_fill:
            self.route            = "internal"
            self.fulfilling_outlet = ck_outlet
        else:
            self.route            = "external"
            self.fulfilling_outlet = None


class RequisitionItem(models.Model):
    """A single item line in a StockRequisition."""

    requisition        = models.ForeignKey(StockRequisition, on_delete=models.CASCADE, related_name="items")
    inventory_item     = models.ForeignKey(InventoryItem,    on_delete=models.CASCADE)
    quantity_requested = models.DecimalField(max_digits=12, decimal_places=3)
    quantity_approved  = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True,
                                             help_text="CK can adjust this before approving")
    unit               = models.CharField(max_length=10, choices=UNIT_CHOICES)
    notes              = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("requisition", "inventory_item")

    def __str__(self):
        return f"{self.inventory_item.name} × {self.quantity_requested} {self.unit}"

    @property
    def effective_quantity(self):
        """Approved quantity if set, otherwise requested."""
        return self.quantity_approved if self.quantity_approved is not None else self.quantity_requested


# -------------------------------------------------------
# AI RECIPE IMPORT
#
# Staging tables for AI-extracted recipes. Nothing here ever gets read by the
# order/KOT/COGS pipeline — those only ever read the real Recipe table.
# A RecipeImportJob's lines are proposals; Recipe rows are only created when a
# human explicitly confirms via recipe_service.upsert_recipe. See
# inventory/recipe_import_views.py and inventory/tasks.py:ai_import_recipe.
# -------------------------------------------------------

RECIPE_IMPORT_STATUS = [
    ("processing",       "Processing"),
    ("ready_for_review", "Ready for Review"),
    ("confirmed",        "Confirmed"),
    ("discarded",        "Discarded"),
    ("failed",           "Failed"),
]

MATCH_METHOD = [
    ("exact",  "Exact name match"),
    ("fuzzy",  "Fuzzy match"),
    ("ai",     "AI-resolved"),
    ("new",    "No match — new ingredient"),
    ("manual", "Manually added by reviewer"),
]


class RecipeImportJob(TenantScopedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    menu_item = models.ForeignKey(
        "menu.MenuItem", on_delete=models.CASCADE, related_name="recipe_import_jobs"
    )
    status = models.CharField(max_length=20, choices=RECIPE_IMPORT_STATUS, default="processing")
    source_filename = models.CharField(max_length=255, blank=True)
    # Gemini's raw structured output, kept for debugging a bad extraction —
    # never read by anything that writes to Recipe.
    raw_extraction = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "outlet", "status"])]

    def __str__(self):
        return f"RecipeImportJob({self.menu_item.name}, {self.status})"


class RecipeImportLine(models.Model):
    job = models.ForeignKey(RecipeImportJob, on_delete=models.CASCADE, related_name="lines")
    # Preserves source-document order in the review UI. Also used to mark a
    # manually-added row (see the review UI's "+ Add ingredient"): those get
    # order values appended after every extracted line.
    order = models.PositiveIntegerField(default=0)

    # --- as extracted — immutable audit trail of what Gemini/the reviewer said.
    # Blank for a manually-added row (match_method="manual").
    raw_ingredient_name = models.CharField(max_length=255, blank=True)
    raw_quantity_text = models.CharField(max_length=100, blank=True)  # e.g. "1 cup", "a pinch"

    # --- deterministic/AI-resolved quantity. Null if genuinely ambiguous —
    # never a guess (see inventory/recipe_unit_table.py).
    extracted_quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    extracted_unit = models.CharField(max_length=10, choices=UNIT_CHOICES, null=True, blank=True)
    needs_manual_quantity = models.BooleanField(default=False)

    # --- matching ---
    match_method = models.CharField(max_length=10, choices=MATCH_METHOD)
    match_confidence = models.FloatField(null=True, blank=True)  # 0-1, fuzzy layer only
    # What the AI/fuzzy layer proposed. Deliberately never the write target —
    # confirm() only ever reads resolved_inventory_item below, so a bug that
    # fails to populate the human-owned fields can't fall back to writing an
    # unreviewed suggestion into Recipe.
    suggested_inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    is_new_ingredient = models.BooleanField(default=False)

    # --- human-owned final state, edited during review. confirm() reads ONLY
    # these fields. ---
    resolved_inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    final_quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    final_unit = models.CharField(max_length=10, choices=UNIT_CHOICES, null=True, blank=True)
    include_in_recipe = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.raw_ingredient_name or '(manual)'} (job {self.job_id})"