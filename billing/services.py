# billing/services.py
"""
Invoice PDF generation for Rasova's own subscription billing.

Follows the exact same pattern as orders/views/print_views.py's
download_pdf_bill -- including the same explicit presentational_hints=False,
which is the real fix for GHSA-jhhc-3hcp-qhm5 / CVE-2026-49452 (a WeasyPrint
CSS-injection bug that only fires when that flag is True). This invoice
renders a tenant's name, which counts as guest-influenced content the same
way bill.html's item notes do -- same risk, same fix, applied here too
rather than assumed unnecessary because "it's just an invoice."
"""
import weasyprint
from django.template.loader import render_to_string


def render_invoice_pdf(invoice, payment_link_url="", base_url="https://rasova.net/"):
    html_string = render_to_string(
        "billing/invoice.html",
        {"invoice": invoice, "payment_link_url": payment_link_url},
    )
    return weasyprint.HTML(string=html_string, base_url=base_url).write_pdf(
        presentational_hints=False
    )
