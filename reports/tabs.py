"""
The report tabs (reports/_report_tabs.html): one per report page, each
shown only where its page opens for this person, by the checks the page
itself makes (core.decorators.may). Until 26 Nov 2026 every tab showed for
everyone, and four of them answered an owner without advanced_reports with
a 403.
"""
from django.urls import reverse

from core.decorators import may


def report_tabs(request):
    from reports import views
    tabs = (
        ("Sales", "reports_dashboard", views.dashboard),
        ("Kitchen KPIs", "kitchen_dashboard", views.kitchen_dashboard),
        ("Inventory", "inventory_report", views.inventory_report),
        ("Menu Engineering", "menu_engineering", views.menu_engineering_report_view),
        ("Labor Cost", "labor_report", views.labor_report_view),
        ("Discount/Void Audit", "audit_report", views.audit_report_view),
        ("CRM Analytics", "crm_analytics_report", views.crm_analytics_report_view),
    )
    shown = []
    for label, name, view in tabs:
        if may(request.user, view):
            url = reverse(name)
            shown.append({"label": label, "url": url, "active": request.path.startswith(url)})
    return shown
