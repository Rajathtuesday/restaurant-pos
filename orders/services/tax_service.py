# orders/services/tax_service.py
from decimal import Decimal, ROUND_HALF_UP


def split_cgst_sgst(gst_amount: Decimal) -> tuple[Decimal, Decimal]:
    """Split a GST amount into equal CGST/SGST halves.

    CGST is rounded independently; SGST is whatever's left over, not
    independently rounded — that's what guarantees CGST + SGST always
    equals the original amount exactly, even when the amount doesn't
    split evenly to the paisa.
    """
    cgst = (gst_amount / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sgst = gst_amount - cgst
    return cgst, sgst
