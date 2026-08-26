# inventory/services.py
"""
Vendor-email delivery for purchase orders.

Mirrors billing/services.py's render_invoice_pdf (same weasyprint +
render_to_string shape, same explicit presentational_hints=False — the
real fix for GHSA-jhhc-3hcp-qhm5 / CVE-2026-49452, a WeasyPrint CSS-
injection bug. A PO's notes field is tenant-influenced content the same
way an invoice's tenant name is — same risk, same fix) and
billing/tasks.py's EmailMessage pattern for the send itself.
"""
import logging

import weasyprint
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger("pos.inventory")


def render_purchase_order_pdf(po, base_url="https://rasova.net/"):
    html_string = render_to_string("inventory/purchase_order_print.html", {"po": po})
    return weasyprint.HTML(string=html_string, base_url=base_url).write_pdf(
        presentational_hints=False
    )


def send_purchase_order_email(po):
    """
    Emails a PDF of this PO to its supplier, if the outlet has opted in
    and the supplier has an email on file. Never raises — this is meant
    to run from transaction.on_commit, where an uncaught exception would
    otherwise surface as a 500 on the "mark as ordered" request even
    though the status change itself already committed successfully.
    Same "never raises" philosophy as notifications/services/whatsapp_service.py.

    Returns True only if the email actually sent (mirrors emailed_at,
    which is set only on a real send, not a mere attempt).
    """
    if not po.outlet.po_vendor_email_enabled:
        return False
    if not po.supplier.email:
        logger.info("PO %s: vendor email enabled but supplier %s has no email on file", po.id, po.supplier.name)
        return False

    try:
        pdf_bytes = render_purchase_order_pdf(po)
        email = EmailMessage(
            subject=f"Purchase Order {po.po_number or po.id} from {po.tenant.name}",
            body=(
                f"Please find attached Purchase Order {po.po_number or po.id} "
                f"from {po.tenant.name} ({po.outlet.name})."
            ),
            to=[po.supplier.email],
        )
        email.attach(f"PO_{po.po_number or po.id}.pdf", pdf_bytes, "application/pdf")
        sent_count = email.send(fail_silently=True)
    except Exception as exc:
        logger.error("send_purchase_order_email failed for PO %s: %s", po.id, exc)
        return False

    if sent_count:
        from .models import PurchaseOrder
        PurchaseOrder.objects.filter(pk=po.pk).update(emailed_at=timezone.now())
        return True

    logger.warning("send_purchase_order_email: PO %s email.send() reported 0 sent", po.id)
    return False
