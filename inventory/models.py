# inventory/models.py
from django.db import models, transaction
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

class InventoryItem(models.Model):

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE
    )

    outlet = models.ForeignKey(
        "tenants.Outlet",
        on_delete=models.CASCADE
    )

    name = models.CharField(max_length=255)

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
                counter, _ = TenantPOCounter.objects.select_for_update().get_or_create(
                    tenant=self.tenant, outlet=self.outlet
                )
                counter.value += 1
                counter.save(update_fields=["value"])
                po.po_number = f"PO-{self.outlet_id}-{timezone.now().year}-{counter.value:04d}"
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

class Supplier(models.Model):
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

class TenantPOCounter(models.Model):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    value = models.IntegerField(default=0)

    class Meta:
        unique_together = ("tenant", "outlet")

class PurchaseOrder(models.Model):
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

class InventoryTransaction(models.Model):

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

# -------------------------------------------------------
# CENTRAL KITCHEN PRODUCTION (FRANCHISE MODE)
# -------------------------------------------------------

class ProductionBatch(models.Model):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=50)
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


class BatchItem(models.Model):
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


class BatchTransfer(models.Model):
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

    def __str__(self):
        return f"Transfer {self.batch.batch_number} to {self.destination_outlet.name}"


# -------------------------------------------------------
# STOCK REQUISITION
# Outlet requests items from central kitchen or vendor.
# System auto-routes based on whether a CK outlet exists.
# -------------------------------------------------------

class StockRequisition(models.Model):

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

        # Find central kitchen outlet for this tenant
        # Convention: the outlet that is the source of any ProductionBatch
        ck = (
            ProductionBatch.objects
            .filter(tenant=self.tenant)
            .values_list("source_outlet", flat=True)
            .first()
        )
        if ck:
            ck_outlet = _Outlet.objects.filter(id=ck).first()
        else:
            ck_outlet = None

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