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
from django.template.loader import render_to_string
from core.pdf import render_pdf


def render_invoice_pdf(invoice, payment_link_url="", base_url="https://rasova.net/"):
    html_string = render_to_string(
        "billing/invoice.html",
        {"invoice": invoice, "payment_link_url": payment_link_url},
    )
    # render_pdf reads the page's stylesheet from disk (and nothing outside
    # Rasova's own files), with presentational_hints=False.
    return render_pdf(html_string, base_url)
