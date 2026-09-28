# orders/services/tax_service.py
from decimal import Decimal

from orders.services.tax_engine import split_gst

# GST rates a restaurant bill can carry since GST 2.0 (22 September 2025).
# Restaurant service is 5%, or 18% in a hotel whose rooms cost over ₹7,500 a
# night; 0% is for exempt or nil-rated lines. The 12% and 28% slabs no longer
# exist: dishes still set to them keep working and are flagged on the GST
# Rates page until someone picks a current rate.
GST_RATES = (Decimal("0.00"), Decimal("5.00"), Decimal("18.00"))

GST_RATE_CHOICES = [
    {"value": "0.00", "label": "0%, exempt or nil-rated"},
    {"value": "5.00", "label": "5%, restaurant (most restaurants and cafes)"},
    {"value": "18.00", "label": "18%, restaurant in a hotel with rooms over ₹7,500 a night"},
]

RETIRED_GST_RATES = (Decimal("12.00"), Decimal("28.00"))


def sale_tax(menu_item, liquor_vat):
    """The tax a dish is sold with, as (kind, rate, VAT class name): liquor
    ("vat") when the tenant has the liquor_vat feature and the dish has a
    VAT class, otherwise GST at the dish's GST rate. The one place that
    decides; the bill line's snapshot and the carts both come from here."""
    if liquor_vat and menu_item.vat_class_id:
        vat_class = menu_item.vat_class
        return "vat", vat_class.rate, vat_class.name
    return "gst", menu_item.gst_percentage, ""


def tax_snapshot_for(menu_item, tenant):
    """The tax fields for a new bill line of this dish
    (OrderItem.TAX_SNAPSHOT_FIELDS), copied at the moment it is ordered."""
    from core.features import has_feature

    kind, rate, class_name = sale_tax(menu_item, has_feature(tenant, "liquor_vat"))
    if kind == "vat":
        return {"gst_percentage": Decimal("0.00"), "tax_kind": "vat",
                "vat_rate": rate, "vat_class_name": class_name}
    return {"gst_percentage": rate, "tax_kind": "gst",
            "vat_rate": Decimal("0.00"), "vat_class_name": ""}


def sale_tax_map(items, tenant):
    """{dish id: {"kind": "gst" | "vat", "rate": "5.00"}} for the dishes on a
    page, so its cart shows the same tax the bill will charge (the page puts
    it in a json_script tag for static/js/cart_tax.js). One feature check and
    at most one query, however many dishes."""
    from core.features import has_feature
    from menu.models import VatClass

    items = list(items)
    liquor_vat = has_feature(tenant, "liquor_vat")
    class_ids = {item.vat_class_id for item in items if item.vat_class_id}
    classes = {}
    if liquor_vat and class_ids:
        classes = {c.id: c for c in VatClass.objects.for_tenant(tenant).filter(id__in=class_ids)}
    taxes = {}
    for item in items:
        if liquor_vat and item.vat_class_id in classes:
            item.vat_class = classes[item.vat_class_id]
        kind, rate, _ = sale_tax(item, liquor_vat and item.vat_class_id in classes)
        taxes[str(item.id)] = {"kind": kind, "rate": f"{Decimal(rate):.2f}"}
    return taxes


def split_cgst_sgst(gst_amount: Decimal) -> tuple[Decimal, Decimal]:
    """Split a GST amount into equal CGST/SGST halves: CGST is half rounded
    half up, SGST the rest, so the two always add up exactly. The tax engine
    owns the rule (tax_engine.split_gst); this name stays for its callers."""
    return split_gst(Decimal(gst_amount))
