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


def split_cgst_sgst(gst_amount: Decimal) -> tuple[Decimal, Decimal]:
    """Split a GST amount into equal CGST/SGST halves: CGST is half rounded
    half up, SGST the rest, so the two always add up exactly. The tax engine
    owns the rule (tax_engine.split_gst); this name stays for its callers."""
    return split_gst(Decimal(gst_amount))
